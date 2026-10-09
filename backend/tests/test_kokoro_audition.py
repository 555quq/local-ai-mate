from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.kokoro_audition import (  # noqa: E402
    SAMPLE_RATE,
    SampleResult,
    WavMetrics,
    build_manifest,
    discover_mandarin_female_voices,
    generate_auditions,
    select_four_voice_ids,
    validate_wav,
)


def _write_wav(path: Path, *, rate: int = SAMPLE_RATE, channels: int = 1, value: int = 1000) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frame = int(value).to_bytes(2, byteorder="little", signed=True) * channels
        handle.writeframes(frame * 240)


def test_discovers_only_unique_mandarin_female_voices_in_natural_order(tmp_path: Path) -> None:
    for relative in ("zf_10.pt", "zf_2.pt", "zf_1.pt", "nested/zf_2.pt", "zm_1.pt", "zf_bad.pt"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    assert discover_mandarin_female_voices(tmp_path) == ["zf_1", "zf_2", "zf_10"]


def test_selects_four_evenly_spaced_voices() -> None:
    voices = [f"zf_{index:03}" for index in range(1, 11)]
    assert select_four_voice_ids(voices) == ["zf_001", "zf_004", "zf_007", "zf_010"]


def test_rejects_fewer_than_four_unique_voices() -> None:
    with pytest.raises(ValueError, match="At least four"):
        select_four_voice_ids(["zf_1", "zf_1", "zf_2", "zf_3"])


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"channels": 2}, "must be mono"),
        ({"rate": 22_050}, "must be 24000 Hz"),
        ({"value": 32_767}, "clipped"),
    ],
)
def test_validate_wav_rejects_invalid_audio(tmp_path: Path, kwargs: dict[str, int], message: str) -> None:
    path = tmp_path / "invalid.wav"
    _write_wav(path, **kwargs)
    with pytest.raises(ValueError, match=message):
        validate_wav(path)


def test_validate_wav_returns_metrics(tmp_path: Path) -> None:
    path = tmp_path / "valid.wav"
    _write_wav(path)
    metrics = validate_wav(path)
    assert metrics.sample_rate == SAMPLE_RATE
    assert metrics.channels == 1
    assert metrics.frames == 240
    assert metrics.duration_seconds == 0.01
    assert len(metrics.sha256) == 64


def test_manifest_requires_four_unique_voices() -> None:
    metrics = WavMetrics(SAMPLE_RATE, 1, 240, 0.01, 0.1, "a" * 64)
    samples = [SampleResult(f"K{i}", "zf_001", f"K{i}.wav", 0.1, metrics) for i in range(1, 5)]
    with pytest.raises(ValueError, match="unique"):
        build_manifest(samples)


def test_generation_is_atomic_and_preserves_unrelated_files(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    voice_dir = model_dir / "voices"
    voice_dir.mkdir(parents=True)
    for index in range(1, 5):
        (voice_dir / f"zf_{index:03}.pt").touch()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    unrelated = output_dir / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")
    (output_dir / "manifest.json").write_text("old", encoding="utf-8")

    calls = 0

    def flaky_synthesizer(_text: str, _voice_id: str) -> np.ndarray:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated failure")
        return np.full(SAMPLE_RATE // 10, 0.1, dtype=np.float32)

    with pytest.raises(RuntimeError, match="simulated failure"):
        generate_auditions(model_dir, output_dir, synthesizer=flaky_synthesizer)
    assert not (output_dir / "manifest.json").exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"

    manifest_path = generate_auditions(
        model_dir,
        output_dir,
        synthesizer=lambda _text, _voice: np.full(SAMPLE_RATE // 10, 0.1, dtype=np.float32),
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [sample["audition_id"] for sample in manifest["samples"]] == ["K1", "K2", "K3", "K4"]
    assert len({sample["voice_id"] for sample in manifest["samples"]}) == 4
    assert unrelated.read_text(encoding="utf-8") == "keep"
