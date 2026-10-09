from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.config import Settings
from app.gpt_sovits import AudioFragment, FallbackStreamingTTSService, GPTSoVITSWorkerService
from app.tts import TTSBusy, TTSSynthesisFailed, TTSUnavailable


class FakeProvider:
    def __init__(self, device: str, *, error_before: Exception | None = None, error_after: Exception | None = None) -> None:
        self.device = device
        self.error_before = error_before
        self.error_after = error_after
        self.calls = 0
        self.closed = False
        self.cancelled = False
        self.warmup_error: Exception | None = None

    def warmup(self) -> None:
        if self.warmup_error:
            raise self.warmup_error

    def stream(self, _text: str):
        self.calls += 1
        if self.error_before:
            raise self.error_before
        yield AudioFragment(0, f"{self.device}-0".encode(), "gpt-sovits", self.device)
        if self.error_after:
            raise self.error_after
        yield AudioFragment(1, f"{self.device}-1".encode(), "gpt-sovits", self.device)

    def cancel(self, _request_id=None) -> None:
        self.cancelled = True

    def close(self) -> None:
        self.closed = True

    def status(self):
        return {"enabled": True, "status": "ready", "engine": "gpt-sovits", "device": self.device, "last_error": None}


class FakePiper:
    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, _text: str) -> bytes:
        self.calls += 1
        return b"RIFFpiperWAVE"

    def status(self):
        return {"enabled": True, "status": "ready"}


def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, gsv_cache_items=8, gsv_cache_bytes=1024)


def test_cuda_is_preferred_and_completed_audio_is_cached(tmp_path: Path) -> None:
    cuda = FakeProvider("cuda")
    cpu = FakeProvider("cpu")
    piper = FakePiper()
    service = FallbackStreamingTTSService(settings(tmp_path), cuda=cuda, cpu=cpu, piper=piper)  # type: ignore[arg-type]
    first = list(service.stream("你好"))
    second = list(service.stream("你好"))
    assert [item.audio for item in first] == [b"cuda-0", b"cuda-1"]
    assert [item.audio for item in second] == [b"cuda-0", b"cuda-1"]
    assert cuda.calls == 1
    assert cpu.calls == 0
    assert piper.calls == 0


def test_failure_before_first_fragment_falls_back_to_cpu(tmp_path: Path) -> None:
    cuda = FakeProvider("cuda", error_before=TTSSynthesisFailed("oom"))
    cpu = FakeProvider("cpu")
    service = FallbackStreamingTTSService(settings(tmp_path), cuda=cuda, cpu=cpu, piper=FakePiper())  # type: ignore[arg-type]
    fragments = list(service.stream("你好"))
    assert [item.device for item in fragments] == ["cpu", "cpu"]


def test_failure_after_first_fragment_restarts_from_cpu(tmp_path: Path) -> None:
    cuda = FakeProvider("cuda", error_after=TTSSynthesisFailed("late"))
    cpu = FakeProvider("cpu")
    service = FallbackStreamingTTSService(settings(tmp_path), cuda=cuda, cpu=cpu, piper=FakePiper())  # type: ignore[arg-type]
    fragments = list(service.stream("你好"))
    assert [(item.device, item.restart) for item in fragments] == [
        ("cuda", False),
        ("cpu", True),
        ("cpu", False),
    ]
    assert cpu.calls == 1
    assert service.status()["last_error"] is None


def test_all_gpt_workers_fail_then_piper_is_used(tmp_path: Path) -> None:
    error = TTSUnavailable("missing")
    cuda = FakeProvider("cuda", error_before=error)
    cpu = FakeProvider("cpu", error_before=error)
    piper = FakePiper()
    service = FallbackStreamingTTSService(settings(tmp_path), cuda=cuda, cpu=cpu, piper=piper)  # type: ignore[arg-type]
    fragments = list(service.stream("你好"))
    assert [(item.engine, item.device) for item in fragments] == [("piper", "cpu")]
    assert piper.calls == 1


def test_partial_stream_caches_only_clean_replacement(tmp_path: Path) -> None:
    cuda = FakeProvider("cuda", error_after=TTSSynthesisFailed("late"))
    piper = FakePiper()
    service = FallbackStreamingTTSService(settings(tmp_path), cuda=cuda, cpu=None, piper=piper)  # type: ignore[arg-type]
    first = list(service.stream("你好"))
    second = list(service.stream("你好"))
    assert [(item.engine, item.restart) for item in first] == [("gpt-sovits", False), ("piper", True)]
    assert [(item.engine, item.restart) for item in second] == [("piper", False)]
    assert piper.calls == 1
    assert cuda.calls == 1


def test_busy_lock_rejects_second_request(tmp_path: Path) -> None:
    service = FallbackStreamingTTSService(settings(tmp_path), cuda=FakeProvider("cuda"), cpu=None, piper=FakePiper())  # type: ignore[arg-type]
    first = service.stream("第一句")
    assert next(first).index == 0
    with pytest.raises(TTSBusy):
        next(service.stream("第二句"))
    first.close()


def test_close_clears_cache_and_closes_workers(tmp_path: Path) -> None:
    cuda = FakeProvider("cuda")
    cpu = FakeProvider("cpu")
    service = FallbackStreamingTTSService(settings(tmp_path), cuda=cuda, cpu=cpu, piper=FakePiper())  # type: ignore[arg-type]
    list(service.stream("你好"))
    service.close()
    assert cuda.closed and cpu.closed
    assert not service._cache


@pytest.mark.parametrize(("device", "expected_timeout"), [("cuda", 7.0), ("cpu", 91.0)])
def test_worker_uses_device_specific_fragment_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    device: str,
    expected_timeout: float,
) -> None:
    configured = Settings(
        data_dir=tmp_path,
        gsv_fragment_timeout_seconds=7,
        gsv_cpu_fragment_timeout_seconds=91,
    )
    service = GPTSoVITSWorkerService(configured, python_path=tmp_path / "python.exe", device=device)
    timeouts: list[float] = []
    events = iter(
        [
            {"event": "started"},
            {
                "event": "fragment",
                "index": 0,
                "media_type": "audio/wav",
                "audio_base64": base64.b64encode(b"RIFFtestWAVE").decode("ascii"),
            },
            {"event": "completed", "fragment_count": 1},
        ]
    )

    monkeypatch.setattr(service, "warmup", lambda: None)
    monkeypatch.setattr(service, "_send", lambda _payload: None)

    def next_event(_request_id: str, timeout: float):
        timeouts.append(timeout)
        return next(events)

    monkeypatch.setattr(service, "_next_request_event", next_event)

    assert [fragment.audio for fragment in service.stream("你好")] == [b"RIFFtestWAVE"]
    assert timeouts == [expected_timeout, expected_timeout, expected_timeout]
