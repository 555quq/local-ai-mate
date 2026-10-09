#!/usr/bin/env python3
"""Generate deterministic local Kokoro voice auditions for Luna."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
import wave
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


AUDITION_TEXT = "晚上好，我是露娜。今天过得怎么样？如果有点累，就慢慢告诉我吧，我会认真听着。"
SAMPLE_RATE = 24_000
_VOICE_PATTERN = re.compile(r"^zf_(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class WavMetrics:
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float
    peak: float
    sha256: str


@dataclass(frozen=True)
class SampleResult:
    audition_id: str
    voice_id: str
    filename: str
    generation_seconds: float
    metrics: WavMetrics


def _voice_sort_key(voice_id: str) -> tuple[int, str]:
    match = _VOICE_PATTERN.fullmatch(voice_id)
    if not match:
        return (2**31 - 1, voice_id.casefold())
    return (int(match.group(1)), voice_id.casefold())


def discover_mandarin_female_voices(voice_dir: Path) -> list[str]:
    """Return unique zf_* voice IDs from .pt assets in natural order."""
    if not voice_dir.is_dir():
        raise FileNotFoundError(f"Voice directory does not exist: {voice_dir}")

    voices = {
        path.stem
        for path in voice_dir.rglob("*.pt")
        if path.is_file() and _VOICE_PATTERN.fullmatch(path.stem)
    }
    return sorted(voices, key=_voice_sort_key)


def select_four_voice_ids(voice_ids: Sequence[str]) -> list[str]:
    """Choose four voices evenly across an already ordered candidate list."""
    unique = list(dict.fromkeys(voice_ids))
    if len(unique) < 4:
        raise ValueError(f"At least four unique Mandarin female voices are required; found {len(unique)}")
    indexes = [round(i * (len(unique) - 1) / 3) for i in range(4)]
    selected = [unique[index] for index in indexes]
    if len(set(selected)) != 4:
        raise ValueError("Voice selection did not produce four unique candidates")
    return selected


def validate_wav(path: Path) -> WavMetrics:
    """Validate a PCM16 mono 24 kHz WAV and return reproducibility metrics."""
    if not path.is_file() or path.stat().st_size <= 44:
        raise ValueError(f"WAV is missing or empty: {path}")

    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frames = handle.getnframes()
            payload = handle.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"WAV cannot be decoded: {path}: {exc}") from exc

    if channels != 1:
        raise ValueError(f"WAV must be mono, got {channels} channels: {path}")
    if sample_width != 2:
        raise ValueError(f"WAV must be PCM16, got {sample_width * 8}-bit samples: {path}")
    if sample_rate != SAMPLE_RATE:
        raise ValueError(f"WAV must be {SAMPLE_RATE} Hz, got {sample_rate} Hz: {path}")
    if frames <= 0 or not payload:
        raise ValueError(f"WAV contains no audio frames: {path}")

    samples = array("h")
    samples.frombytes(payload)
    if os.sys.byteorder != "little":
        samples.byteswap()
    absolute_peak = max(abs(value) for value in samples)
    if absolute_peak >= 32_767:
        raise ValueError(f"WAV contains clipped samples: {path}")

    return WavMetrics(
        sample_rate=sample_rate,
        channels=channels,
        frames=frames,
        duration_seconds=round(frames / sample_rate, 3),
        peak=round(absolute_peak / 32_768, 6),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def build_manifest(samples: Sequence[SampleResult]) -> dict[str, object]:
    if len(samples) != 4:
        raise ValueError(f"Manifest requires exactly four samples; got {len(samples)}")
    if len({sample.voice_id for sample in samples}) != 4:
        raise ValueError("Manifest voice IDs must be unique")
    return {
        "schema_version": 1,
        "design_version": "v1.8.0",
        "engine": "Kokoro-82M-v1.1-zh",
        "text": AUDITION_TEXT,
        "sample_rate": SAMPLE_RATE,
        "samples": [
            {
                "audition_id": sample.audition_id,
                "voice_id": sample.voice_id,
                "filename": sample.filename,
                "generation_seconds": round(sample.generation_seconds, 3),
                **asdict(sample.metrics),
            }
            for sample in samples
        ],
    }


def _write_pcm16(path: Path, audio: Any) -> None:
    import numpy as np

    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if samples.size == 0 or not np.all(np.isfinite(samples)):
        raise ValueError(f"Synthesizer returned invalid audio for {path.name}")
    peak = float(np.max(np.abs(samples)))
    if peak > 0.95:
        samples = samples * (0.95 / peak)
    pcm = np.rint(samples * 32_767).astype("<i2", copy=False)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())


def _default_synthesizer(model_dir: Path, device: str) -> Callable[[str, str], Any]:
    import numpy as np
    from kokoro import KModel, KPipeline

    model_path = model_dir / "kokoro-v1_1-zh.pth"
    config_path = model_dir / "config.json"
    if not model_path.is_file() or not config_path.is_file():
        raise FileNotFoundError(f"Kokoro snapshot is incomplete: {model_dir}")
    model = KModel(
        repo_id="hexgrad/Kokoro-82M-v1.1-zh",
        config=str(config_path),
        model=str(model_path),
    ).to(device).eval()
    pipeline = KPipeline(
        lang_code="z",
        repo_id="hexgrad/Kokoro-82M-v1.1-zh",
        model=model,
        device=device,
    )

    def synthesize(text: str, voice_id: str) -> Any:
        voice_path = model_dir / "voices" / f"{voice_id}.pt"
        chunks = [np.asarray(audio, dtype=np.float32) for _, _, audio in pipeline(text, voice=str(voice_path))]
        if not chunks:
            raise RuntimeError(f"Kokoro returned no audio for voice {voice_id}")
        return np.concatenate(chunks)

    return synthesize


def generate_auditions(
    model_dir: Path,
    output_dir: Path,
    device: str = "cpu",
    synthesizer: Callable[[str, str], Any] | None = None,
) -> Path:
    voice_dir = model_dir / "voices"
    voice_ids = select_four_voice_ids(discover_mandarin_female_voices(voice_dir))
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = output_dir / "manifest.json"
    temporary_manifest = output_dir / "manifest.json.tmp"
    manifest_path.unlink(missing_ok=True)
    temporary_manifest.unlink(missing_ok=True)
    for audition_id in ("K1", "K2", "K3", "K4"):
        (output_dir / f"{audition_id}.wav").unlink(missing_ok=True)

    synthesize = synthesizer or _default_synthesizer(model_dir, device)
    results: list[SampleResult] = []
    for index, voice_id in enumerate(voice_ids, start=1):
        audition_id = f"K{index}"
        output_path = output_dir / f"{audition_id}.wav"
        started = time.perf_counter()
        audio = synthesize(AUDITION_TEXT, voice_id)
        generation_seconds = time.perf_counter() - started
        _write_pcm16(output_path, audio)
        metrics = validate_wav(output_path)
        results.append(
            SampleResult(
                audition_id=audition_id,
                voice_id=voice_id,
                filename=output_path.name,
                generation_seconds=generation_seconds,
                metrics=metrics,
            )
        )

    manifest = build_manifest(results)
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    return manifest_path


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest_path = generate_auditions(args.model_dir.resolve(), args.output_dir.resolve(), args.device)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for sample in manifest["samples"]:
        print(f"{sample['audition_id']}: {sample['voice_id']} -> {manifest_path.parent / sample['filename']}")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
