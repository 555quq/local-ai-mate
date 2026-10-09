from __future__ import annotations

import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.stt import (
    FasterWhisperSTTService,
    SenseVoiceSTTService,
    STTBusy,
    STTResult,
    STTTranscriptionFailed,
    STTUnavailable,
)


class FakeWhisperModel:
    def __init__(self) -> None:
        self.calls: list[tuple[type[object], bytes, dict[str, object]]] = []

    def transcribe(self, audio: object, **kwargs: object):
        self.calls.append((type(audio), audio.getvalue(), kwargs))
        return iter([SimpleNamespace(text=" 你好 "), SimpleNamespace(text="世界 ")]), SimpleNamespace(
            language="zh", duration=1.234
        )


class FakeSTTService:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls: list[tuple[bytes, str]] = []
        self.result = STTResult(text="你好，露娜", language="zh", duration_ms=1_240)

    def status(self) -> dict[str, object]:
        return {"enabled": True, "status": "ready"}

    def transcribe(self, audio: bytes, content_type: str) -> STTResult:
        self.calls.append((audio, content_type))
        if self.error:
            raise self.error
        return self.result


class FasterWhisperSTTServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.model_path = Path(self.temp_dir.name) / "faster-whisper-base"
        self.model_path.mkdir()
        self.settings = Settings(
            data_dir=Path(self.temp_dir.name) / "data",
            stt_enabled=True,
            stt_model_path=self.model_path,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_transcribes_from_memory_with_fixed_cpu_options(self) -> None:
        model = FakeWhisperModel()
        loader_calls: list[tuple[str, dict[str, object]]] = []

        def load_model(model_path: str, **kwargs: object) -> FakeWhisperModel:
            loader_calls.append((model_path, kwargs))
            return model

        service = FasterWhisperSTTService(self.settings, model_loader=load_model)
        self.assertEqual(service.status(), {"enabled": True, "status": "configured"})

        result = service.transcribe(b"browser-audio", "audio/webm")

        self.assertEqual(result, STTResult(text="你好 世界", language="zh", duration_ms=1_234))
        self.assertEqual(
            loader_calls,
            [(str(self.model_path), {"device": "cpu", "compute_type": "int8", "local_files_only": True})],
        )
        audio_type, audio, options = model.calls[0]
        self.assertIs(audio_type, BytesIO)
        self.assertEqual(audio, b"browser-audio")
        self.assertEqual(
            options,
            {
                "language": "zh",
                "beam_size": 1,
                "vad_filter": True,
                "condition_on_previous_text": False,
            },
        )
        self.assertEqual(service.status(), {"enabled": True, "status": "ready"})

    def test_missing_model_is_unavailable_without_exposing_path(self) -> None:
        self.model_path.rmdir()
        service = FasterWhisperSTTService(self.settings, model_loader=lambda *_args, **_kwargs: FakeWhisperModel())
        self.assertEqual(service.status(), {"enabled": True, "status": "unavailable"})
        self.assertNotIn(str(self.model_path), repr(service.status()))
        with self.assertRaises(STTUnavailable):
            service.transcribe(b"audio", "audio/wav")

    def test_concurrent_transcription_is_rejected(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class BlockingModel(FakeWhisperModel):
            def transcribe(self, audio: object, **kwargs: object):
                started.set()
                release.wait(timeout=2)
                return super().transcribe(audio, **kwargs)

        service = FasterWhisperSTTService(
            self.settings, model_loader=lambda *_args, **_kwargs: BlockingModel()
        )
        worker = threading.Thread(target=lambda: service.transcribe(b"first", "audio/ogg"))
        worker.start()
        self.assertTrue(started.wait(timeout=1))
        with self.assertRaises(STTBusy):
            service.transcribe(b"second", "audio/ogg")
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())


class SenseVoiceSTTServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = Settings(
            data_dir=self.root / "data",
            stt_enabled=True,
            stt_engine="sensevoice",
            sensevoice_model_path=self.root / "sensevoice-model",
            sensevoice_python=self.root / "python.exe",
            sensevoice_worker_path=self.root / "worker.py",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_transcribes_through_worker_protocol(self) -> None:
        fallback = FakeSTTService()
        service = SenseVoiceSTTService(self.settings, fallback=fallback)
        service._start = lambda: None  # type: ignore[method-assign]

        def send(payload: dict[str, object]) -> None:
            service._events.put(
                {
                    "version": 1,
                    "event": "transcribed",
                    "request_id": payload["request_id"],
                    "text": "你好，露娜",
                    "language": "zh",
                    "duration_ms": 860,
                }
            )

        service._send = send  # type: ignore[method-assign]
        result = service.transcribe(b"audio", "audio/webm")
        self.assertEqual(result, STTResult(text="你好，露娜", language="zh", duration_ms=860))

    def test_falls_back_and_preserves_content_type(self) -> None:
        fallback = FakeSTTService()
        service = SenseVoiceSTTService(self.settings, fallback=fallback)

        def unavailable() -> None:
            raise STTUnavailable("not ready")

        service._start = unavailable  # type: ignore[method-assign]
        result = service.transcribe(b"audio", "audio/ogg")
        self.assertEqual(result, fallback.result)
        self.assertEqual(fallback.calls, [(b"audio", "audio/ogg")])

    def test_status_degrades_to_fallback_without_exposing_paths(self) -> None:
        service = SenseVoiceSTTService(self.settings, fallback=FakeSTTService())
        status = service.status()
        self.assertEqual(status["status"], "degraded")
        self.assertTrue(status["fallback_ready"])
        self.assertNotIn(str(self.root), repr(status))


class STTApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token = "stt-test-token"
        self.origin = "http://testserver"
        self.settings = Settings(
            data_dir=Path(self.temp_dir.name),
            session_token=self.token,
            allowed_origin=self.origin,
            stt_enabled=True,
            stt_model_path=Path(self.temp_dir.name) / "private" / "model",
        )
        self.stt = FakeSTTService()
        self.client = TestClient(create_app(self.settings, stt_service=self.stt))
        self.headers = {"X-Session-Token": self.token, "Origin": self.origin}

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def transcribe(self, audio: bytes, content_type: str = "audio/webm"):
        return self.client.post(
            "/api/v1/stt/transcribe",
            headers={**self.headers, "Content-Type": content_type},
            content=audio,
        )

    def test_returns_enveloped_transcript_without_caching(self) -> None:
        response = self.transcribe(b"webm-data", "audio/webm; codecs=opus")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            response.json()["data"],
            {"text": "你好，露娜", "language": "zh", "duration_ms": 1_240},
        )
        self.assertEqual(self.stt.calls, [(b"webm-data", "audio/webm")])

    def test_requires_session_and_origin(self) -> None:
        missing = self.client.post(
            "/api/v1/stt/transcribe", headers={"Content-Type": "audio/wav"}, content=b"wav"
        )
        self.assertEqual(missing.status_code, 401)
        wrong = {
            "X-Session-Token": self.token,
            "Origin": "https://example.invalid",
            "Content-Type": "audio/wav",
        }
        self.assertEqual(
            self.client.post("/api/v1/stt/transcribe", headers=wrong, content=b"wav").status_code,
            401,
        )
        self.assertEqual(self.stt.calls, [])

    def test_rejects_empty_oversized_and_unsupported_audio(self) -> None:
        empty = self.transcribe(b"", "audio/wav")
        self.assertEqual(empty.status_code, 422)
        self.assertEqual(empty.json()["error"]["code"], "STT_EMPTY_AUDIO")

        oversized = self.transcribe(b"x" * (8 * 1024 * 1024 + 1), "audio/ogg")
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(oversized.json()["error"]["code"], "STT_AUDIO_TOO_LARGE")

        unsupported = self.transcribe(b"mp4", "audio/mp4")
        self.assertEqual(unsupported.status_code, 415)
        self.assertEqual(unsupported.json()["error"]["code"], "STT_UNSUPPORTED_MEDIA")
        self.assertEqual(self.stt.calls, [])

    def test_rejects_audio_longer_than_fifteen_seconds(self) -> None:
        self.stt.result = STTResult(text="过长", language="zh", duration_ms=15_001)
        response = self.transcribe(b"audio", "audio/wav")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "STT_AUDIO_TOO_LARGE")

    def test_maps_stt_failures_to_fixed_codes(self) -> None:
        cases = [
            (STTBusy("busy"), 429, "STT_BUSY"),
            (STTUnavailable("unavailable"), 503, "STT_UNAVAILABLE"),
            (STTTranscriptionFailed("failed"), 503, "STT_TRANSCRIPTION_FAILED"),
        ]
        for error, status, code in cases:
            with self.subTest(code=code):
                self.stt.error = error
                response = self.transcribe(b"audio", "audio/wav")
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["error"]["code"], code)
                self.assertTrue(response.json()["error"]["retryable"])

    def test_health_contains_only_safe_stt_status(self) -> None:
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["stt"], {"enabled": True, "status": "ready"})
        self.assertNotIn(str(self.settings.stt_model_path), response.text)


if __name__ == "__main__":
    unittest.main()
