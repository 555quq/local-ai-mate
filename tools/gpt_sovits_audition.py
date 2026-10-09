#!/usr/bin/env python3
"""Generate two local GPT-SoVITS V5 Turbo zero-shot auditions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import wave
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


@dataclass(frozen=True)
class AudioMetrics:
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float
    peak: float
    sha256: str


@dataclass(frozen=True)
class CloneResult:
    audition_id: str
    text: str
    filename: str
    generation_seconds: float
    metrics: AudioMetrics


@dataclass(frozen=True)
class InferenceParameters:
    top_k: int
    top_p: float
    temperature: float
    text_split_method: str
    speed_factor: float
    fragment_interval: float
    seed: int
    repetition_penalty: float
    sample_steps: int


@dataclass(frozen=True)
class AuditionSample:
    audition_id: str
    text: str
    filename: str


@dataclass(frozen=True)
class AuditionProfile:
    design_version: str
    style_id: str
    samples: tuple[AuditionSample, ...]
    parameters: InferenceParameters


BASELINE_PROFILE = AuditionProfile(
    design_version="v1.9.0",
    style_id="baseline-zero-shot",
    samples=(
        AuditionSample("C1", "晚上好，我是露娜。今天过得怎么样？我一直在这里等你。", "clone-1.wav"),
        AuditionSample("C2", "如果今天有点累，就慢慢说给我听吧，我会认真陪着你。", "clone-2.wav"),
    ),
    parameters=InferenceParameters(
        top_k=5,
        top_p=0.9,
        temperature=0.6,
        text_split_method="cut5",
        speed_factor=1.0,
        fragment_interval=0.3,
        seed=20261008,
        repetition_penalty=1.35,
        sample_steps=4,
    ),
)

COOL_RESTRAINT_PROFILE = AuditionProfile(
    design_version="v1.9.1",
    style_id="natural-restraint-c1",
    samples=(
        AuditionSample("C1", "你回来了。今天还顺利吗？", "cool-1.wav"),
        AuditionSample("C2", "累了就先休息。其他事情，明天再处理。", "cool-2.wav"),
        AuditionSample("C3", "嗯，我在。你慢慢说。", "cool-3.wav"),
    ),
    parameters=InferenceParameters(
        top_k=5,
        top_p=0.85,
        temperature=0.5,
        text_split_method="cut5",
        speed_factor=0.96,
        fragment_interval=0.22,
        seed=20261008,
        repetition_penalty=1.35,
        sample_steps=4,
    ),
)

AUDITION_PROFILES = {
    BASELINE_PROFILE.design_version: BASELINE_PROFILE,
    COOL_RESTRAINT_PROFILE.design_version: COOL_RESTRAINT_PROFILE,
}

# Backward-compatible public constant used by earlier callers and notes.
AUDITION_TEXTS = tuple(sample.text for sample in BASELINE_PROFILE.samples)


def get_profile(profile_id: str) -> AuditionProfile:
    try:
        return AUDITION_PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"Unknown audition profile: {profile_id}") from exc


def validate_clone(path: Path) -> AudioMetrics:
    if not path.is_file() or path.stat().st_size <= 44:
        raise ValueError(f"Clone WAV is missing or empty: {path}")
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.getnframes()
            payload = handle.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"Clone WAV cannot be decoded: {path}: {exc}") from exc
    if channels != 1:
        raise ValueError(f"Clone WAV must be mono: {path}")
    if width != 2:
        raise ValueError(f"Clone WAV must be PCM16: {path}")
    if rate < 16_000 or rate > 96_000:
        raise ValueError(f"Clone WAV has invalid sample rate {rate}: {path}")
    if frames <= 0 or not payload:
        raise ValueError(f"Clone WAV has no samples: {path}")
    samples = array("h")
    samples.frombytes(payload)
    if sys.byteorder != "little":
        samples.byteswap()
    absolute_peak = max(abs(value) for value in samples)
    if absolute_peak >= 32_767:
        raise ValueError(f"Clone WAV contains clipped samples: {path}")
    return AudioMetrics(
        sample_rate=rate,
        channels=channels,
        frames=frames,
        duration_seconds=round(frames / rate, 3),
        peak=round(absolute_peak / 32_768, 6),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _write_pcm16(path: Path, sample_rate: int, audio: Any) -> None:
    import numpy as np

    samples = np.asarray(audio).reshape(-1)
    if samples.size == 0 or not np.all(np.isfinite(samples)):
        raise ValueError(f"Inference returned invalid audio for {path.name}")
    if np.issubdtype(samples.dtype, np.floating):
        peak = float(np.max(np.abs(samples)))
        if peak > 0.95:
            samples = samples * (0.95 / peak)
        samples = np.rint(samples * 32_767).astype("<i2")
    else:
        samples = samples.astype(np.float32)
        peak = float(np.max(np.abs(samples)))
        if peak >= 32_767:
            samples = samples * (31_000.0 / peak)
        samples = np.rint(samples).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(samples.tobytes())


def _load_runner(
    repo_dir: Path,
    model_dir: Path,
    parameters: InferenceParameters,
) -> Callable[[str, Path, str], tuple[int, Any]]:
    repo_dir = repo_dir.resolve()
    model_dir = model_dir.resolve()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    # chinese2.py reads this environment variable lazily when G2PW is first
    # imported. Keep it pointed at the centralized local BERT checkout instead
    # of the repository-relative pretrained_models default.
    os.environ["bert_path"] = str(model_dir / "chinese-roberta-wwm-ext-large")
    os.chdir(repo_dir)
    sys.path.insert(0, str(repo_dir))
    sys.path.insert(0, str(repo_dir / "GPT_SoVITS"))

    from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

    config = TTS_Config(
        {
            "custom": {
                "device": "cpu",
                "is_half": False,
                "version": "v5turbo",
                "t2s_weights_path": str(model_dir / "s1v3.ckpt"),
                "vits_weights_path": str(model_dir / "gsv-v5-pretrained" / "s2Gv5turbo.pth"),
                "cnhuhbert_base_path": str(model_dir / "chinese-hubert-base"),
                "bert_base_path": str(model_dir / "chinese-roberta-wwm-ext-large"),
            }
        }
    )
    pipeline = TTS(config)

    def run(text: str, reference_path: Path, prompt_text: str) -> tuple[int, Any]:
        inputs = {
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": str(reference_path.resolve()),
            "aux_ref_audio_paths": [],
            "prompt_text": prompt_text,
            "prompt_lang": "zh",
            "top_k": parameters.top_k,
            "top_p": parameters.top_p,
            "temperature": parameters.temperature,
            "text_split_method": parameters.text_split_method,
            "batch_size": 1,
            "split_bucket": False,
            "speed_factor": parameters.speed_factor,
            "fragment_interval": parameters.fragment_interval,
            "seed": parameters.seed,
            "parallel_infer": False,
            "use_cuda_graph": False,
            "use_flash_attention": False,
            "repetition_penalty": parameters.repetition_penalty,
            "sample_steps": parameters.sample_steps,
            "cfg_rate": 0.0,
            "return_fragment": False,
            "streaming_mode": False,
        }
        return next(pipeline.run(inputs))

    return run


def generate_clones(
    reference_path: Path,
    reference_metadata_path: Path,
    output_dir: Path,
    repo_dir: Path | None = None,
    model_dir: Path | None = None,
    profile: AuditionProfile = BASELINE_PROFILE,
    runner: Callable[[str, Path, str], tuple[int, Any]] | None = None,
) -> Path:
    metadata = json.loads(reference_metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or not isinstance(metadata.get("reference"), dict):
        raise ValueError("Reference metadata must contain a reference object")
    reference_metadata = metadata["reference"]
    prompt_text = str(reference_metadata.get("text", "")).strip()
    if not prompt_text:
        raise ValueError("Reference metadata is missing prompt text")
    if re.search(r"[\u3400-\u9fff]", prompt_text) is None:
        raise ValueError("Reference prompt text must contain Chinese characters")
    if not reference_path.is_file():
        raise FileNotFoundError(reference_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    temporary_manifest = output_dir / "manifest.json.tmp"
    manifest_path.unlink(missing_ok=True)
    temporary_manifest.unlink(missing_ok=True)
    for sample in profile.samples:
        path = output_dir / sample.filename
        path.unlink(missing_ok=True)
        path.with_suffix(".tmp.wav").unlink(missing_ok=True)

    if runner is None:
        if repo_dir is None or model_dir is None:
            raise ValueError("repo_dir and model_dir are required for real inference")
        runner = _load_runner(repo_dir, model_dir, profile.parameters)

    results: list[CloneResult] = []
    for sample in profile.samples:
        path = output_dir / sample.filename
        temporary_path = path.with_suffix(".tmp.wav")
        started = time.perf_counter()
        try:
            sample_rate, audio = runner(sample.text, reference_path, prompt_text)
            elapsed = time.perf_counter() - started
            _write_pcm16(temporary_path, int(sample_rate), audio)
            metrics = validate_clone(temporary_path)
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
        results.append(CloneResult(sample.audition_id, sample.text, sample.filename, elapsed, metrics))

    manifest = {
        "schema_version": 1,
        "design_version": profile.design_version,
        "engine": "GPT-SoVITS v5turbo",
        "mode": "zero-shot",
        "style_id": profile.style_id,
        "parameters": asdict(profile.parameters),
        "reference": {
            "filename": reference_path.name,
            "prompt_text": prompt_text,
            "source_start": reference_metadata.get("start"),
            "source_end": reference_metadata.get("end"),
        },
        "samples": [
            {
                "audition_id": item.audition_id,
                "text": item.text,
                "filename": item.filename,
                "generation_seconds": round(item.generation_seconds, 3),
                **asdict(item.metrics),
            }
            for item in results
        ],
    }
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    return manifest_path


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reference-metadata", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repo-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--profile", choices=tuple(AUDITION_PROFILES), default="v1.9.0")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest_path = generate_clones(
        args.reference.resolve(),
        args.reference_metadata.resolve(),
        args.output_dir.resolve(),
        args.repo_dir.resolve(),
        args.model_dir.resolve(),
        profile=get_profile(args.profile),
    )
    print(manifest_path)
    print(manifest_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
