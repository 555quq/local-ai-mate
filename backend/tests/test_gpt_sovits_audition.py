from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gpt_sovits_audition import (  # noqa: E402
    BASELINE_PROFILE,
    COOL_RESTRAINT_PROFILE,
    generate_clones,
    get_profile,
    validate_clone,
)


def _write_pcm16(path: Path, *, channels: int = 1, rate: int = 48_000, value: int = 1000) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(int(value).to_bytes(2, "little", signed=True) * channels * 480)


def test_validate_clone_accepts_valid_pcm16(tmp_path: Path) -> None:
    path = tmp_path / "valid.wav"
    _write_pcm16(path)
    metrics = validate_clone(path)
    assert metrics.channels == 1
    assert metrics.sample_rate == 48_000
    assert metrics.duration_seconds == 0.01


@pytest.mark.parametrize(("kwargs", "message"), [({"channels": 2}, "mono"), ({"value": 32_767}, "clipped")])
def test_validate_clone_rejects_invalid_audio(tmp_path: Path, kwargs: dict[str, int], message: str) -> None:
    path = tmp_path / "invalid.wav"
    _write_pcm16(path, **kwargs)
    with pytest.raises(ValueError, match=message):
        validate_clone(path)


def test_generate_clones_requires_reference_text(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    metadata = tmp_path / "reference.json"
    metadata.write_text('{"reference": {"text": ""}}', encoding="utf-8")
    with pytest.raises(ValueError, match="prompt text"):
        generate_clones(reference, metadata, tmp_path / "out", runner=lambda *_: (48_000, np.zeros(100)))


def test_baseline_profile_preserves_v190_defaults() -> None:
    profile = get_profile("v1.9.0")
    assert profile is BASELINE_PROFILE
    assert profile.style_id == "baseline-zero-shot"
    assert [sample.filename for sample in profile.samples] == ["clone-1.wav", "clone-2.wav"]
    assert [sample.text for sample in profile.samples] == [
        "晚上好，我是露娜。今天过得怎么样？我一直在这里等你。",
        "如果今天有点累，就慢慢说给我听吧，我会认真陪着你。",
    ]
    assert profile.parameters.speed_factor == 1.0
    assert profile.parameters.fragment_interval == 0.3
    assert profile.parameters.temperature == 0.6
    assert profile.parameters.top_p == 0.9


def test_cool_restraint_profile_has_exact_c1_parameters() -> None:
    profile = get_profile("v1.9.1")
    assert profile is COOL_RESTRAINT_PROFILE
    assert profile.style_id == "natural-restraint-c1"
    assert [sample.filename for sample in profile.samples] == [
        "cool-1.wav",
        "cool-2.wav",
        "cool-3.wav",
    ]
    assert [sample.text for sample in profile.samples] == [
        "你回来了。今天还顺利吗？",
        "累了就先休息。其他事情，明天再处理。",
        "嗯，我在。你慢慢说。",
    ]
    assert profile.parameters.speed_factor == 0.96
    assert profile.parameters.fragment_interval == 0.22
    assert profile.parameters.temperature == 0.5
    assert profile.parameters.top_p == 0.85
    assert profile.parameters.top_k == 5
    assert profile.parameters.repetition_penalty == 1.35
    assert profile.parameters.sample_steps == 4
    assert profile.parameters.seed == 20261008
    assert profile.parameters.text_split_method == "cut5"


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown audition profile"):
        get_profile("v9")


@pytest.mark.parametrize(
    "metadata_content",
    [
        '{"reference": {"text": ""}}',
        "not json",
        '{"reference": {"text": "plain English"}}',
    ],
)
def test_invalid_reference_metadata_never_invokes_runner(tmp_path: Path, metadata_content: str) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    metadata = tmp_path / "reference.json"
    metadata.write_text(metadata_content, encoding="utf-8")
    calls = 0

    def runner(*_args: object) -> tuple[int, np.ndarray]:
        nonlocal calls
        calls += 1
        return (48_000, np.zeros(100))

    with pytest.raises(ValueError):
        generate_clones(reference, metadata, tmp_path / "out", runner=runner)
    assert calls == 0


def test_generation_is_atomic_and_preserves_unrelated_files(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    metadata = tmp_path / "reference.json"
    metadata.write_text('{"reference": {"text": "参考文本", "start": 1, "end": 7}}', encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    unrelated = output / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")
    calls = 0

    def flaky(*_args: object) -> tuple[int, np.ndarray]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("inference failed")
        return (48_000, np.full(4_800, 1000, dtype=np.int16))

    with pytest.raises(RuntimeError, match="inference failed"):
        generate_clones(reference, metadata, output, runner=flaky)
    assert not (output / "manifest.json").exists()
    assert not list(output.glob("*.tmp.wav"))
    assert unrelated.read_text(encoding="utf-8") == "keep"

    manifest_path = generate_clones(
        reference,
        metadata,
        output,
        runner=lambda *_: (48_000, np.full(4_800, 1000, dtype=np.int16)),
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [item["audition_id"] for item in manifest["samples"]] == ["C1", "C2"]
    assert manifest["design_version"] == "v1.9.0"
    assert manifest["style_id"] == "baseline-zero-shot"
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_cool_restraint_generation_uses_profile_and_records_parameters(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    metadata = tmp_path / "reference.json"
    metadata.write_text(
        '{"reference": {"text": "这是参考文本。", "start": 1, "end": 7}}',
        encoding="utf-8",
    )
    calls: list[str] = []

    def runner(text: str, *_args: object) -> tuple[int, np.ndarray]:
        calls.append(text)
        return (48_000, np.full(4_800, 1000, dtype=np.int16))

    output = tmp_path / "out"
    manifest_path = generate_clones(
        reference,
        metadata,
        output,
        profile=COOL_RESTRAINT_PROFILE,
        runner=runner,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert calls == [sample.text for sample in COOL_RESTRAINT_PROFILE.samples]
    assert [item["filename"] for item in manifest["samples"]] == [
        "cool-1.wav",
        "cool-2.wav",
        "cool-3.wav",
    ]
    assert manifest["design_version"] == "v1.9.1"
    assert manifest["style_id"] == "natural-restraint-c1"
    assert manifest["parameters"] == {
        "top_k": 5,
        "top_p": 0.85,
        "temperature": 0.5,
        "text_split_method": "cut5",
        "speed_factor": 0.96,
        "fragment_interval": 0.22,
        "seed": 20261008,
        "repetition_penalty": 1.35,
        "sample_steps": 4,
    }
    assert all((output / item["filename"]).is_file() for item in manifest["samples"])
    assert not list(output.glob("*.tmp.wav"))
