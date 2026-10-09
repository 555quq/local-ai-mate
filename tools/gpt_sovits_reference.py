#!/usr/bin/env python3
"""Select a clean local reference utterance for GPT-SoVITS zero-shot inference."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np


SAMPLE_RATE = 32_000
MIN_SECONDS = 4.0
MAX_SECONDS = 12.0
_SILENCE_EVENT = re.compile(r"silence_(start|end):\s*(-?[0-9.]+)")


@dataclass(frozen=True)
class TimeRange:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Candidate:
    start: float
    end: float
    text: str
    avg_logprob: float
    no_speech_prob: float
    rms_dbfs: float
    peak: float
    silence_fraction: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def score(self) -> float:
        duration_preference = -abs(self.duration - 7.0) * 0.04
        signal_margin = min(max((self.rms_dbfs + 36.0) / 24.0, 0.0), 1.0) * 0.15
        return (
            self.avg_logprob
            - self.no_speech_prob * 1.5
            - self.silence_fraction * 0.8
            + duration_preference
            + signal_margin
        )


def parse_silence_intervals(text: str, duration: float) -> list[TimeRange]:
    """Parse FFmpeg silencedetect events into clamped, non-overlapping ranges."""
    pending_start: float | None = None
    result: list[TimeRange] = []
    for kind, raw_value in _SILENCE_EVENT.findall(text):
        value = min(max(float(raw_value), 0.0), duration)
        if kind == "start":
            pending_start = value
        elif pending_start is not None and value > pending_start:
            result.append(TimeRange(pending_start, value))
            pending_start = None
    if pending_start is not None and pending_start < duration:
        result.append(TimeRange(pending_start, duration))

    merged: list[TimeRange] = []
    for current in sorted(result, key=lambda item: item.start):
        if not merged or current.start > merged[-1].end:
            merged.append(current)
        else:
            merged[-1] = TimeRange(merged[-1].start, max(merged[-1].end, current.end))
    return merged


def rank_candidates(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Filter unsafe references and rank the remaining candidates deterministically."""
    accepted = [
        candidate
        for candidate in candidates
        if MIN_SECONDS <= candidate.duration <= MAX_SECONDS
        and bool(candidate.text.strip())
        and candidate.avg_logprob >= -1.2
        and candidate.no_speech_prob <= 0.5
        and candidate.rms_dbfs >= -40.0
        and 0.01 <= candidate.peak < 0.999
        and candidate.silence_fraction <= 0.45
    ]
    return sorted(accepted, key=lambda item: (-item.score, item.start, item.end))


def _run(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(arguments), check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _probe_duration(source: Path, ffprobe: str) -> float:
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ]
    )
    return float(result.stdout.strip())


def _decode_source(source: Path, decoded_path: Path, ffmpeg: str) -> None:
    decoded_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            str(decoded_path),
        ]
    )


def _read_audio_range(decoded_path: Path, start: float, end: float) -> np.ndarray:
    with wave.open(str(decoded_path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getframerate() != SAMPLE_RATE or handle.getsampwidth() != 2:
            raise ValueError(f"Unexpected decoded WAV format: {decoded_path}")
        start_frame = max(0, round(start * SAMPLE_RATE))
        end_frame = min(handle.getnframes(), round(end * SAMPLE_RATE))
        handle.setpos(start_frame)
        payload = handle.readframes(max(0, end_frame - start_frame))
    return np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32_768.0


def _signal_metrics(samples: np.ndarray) -> tuple[float, float, float]:
    if samples.size == 0:
        return (-math.inf, 0.0, 1.0)
    rms = float(np.sqrt(np.mean(np.square(samples))))
    rms_dbfs = 20.0 * math.log10(max(rms, 1e-9))
    peak = float(np.max(np.abs(samples)))
    frame_size = max(1, int(SAMPLE_RATE * 0.02))
    frame_count = samples.size // frame_size
    if frame_count == 0:
        silence_fraction = 1.0
    else:
        framed = samples[: frame_count * frame_size].reshape(frame_count, frame_size)
        frame_rms = np.sqrt(np.mean(np.square(framed), axis=1))
        silence_fraction = float(np.mean(frame_rms < 0.006))
    return (round(rms_dbfs, 3), round(peak, 6), round(silence_fraction, 6))


def _merge_whisper_segments(segments: Sequence[object]) -> list[tuple[float, float, str, float, float]]:
    """Merge adjacent short Whisper segments into valid reference-length utterances."""
    merged: list[tuple[float, float, str, float, float]] = []
    index = 0
    while index < len(segments):
        current = segments[index]
        start = float(current.start)
        end = float(current.end)
        texts = [str(current.text).strip()]
        logprobs = [float(current.avg_logprob)]
        no_speech = [float(current.no_speech_prob)]
        cursor = index + 1
        while end - start < MIN_SECONDS and cursor < len(segments):
            following = segments[cursor]
            gap = float(following.start) - end
            proposed_end = float(following.end)
            if gap > 1.0 or proposed_end - start > MAX_SECONDS:
                break
            end = proposed_end
            texts.append(str(following.text).strip())
            logprobs.append(float(following.avg_logprob))
            no_speech.append(float(following.no_speech_prob))
            cursor += 1
        if MIN_SECONDS <= end - start <= MAX_SECONDS:
            merged.append((start, end, "".join(texts), sum(logprobs) / len(logprobs), max(no_speech)))
        index = max(index + 1, cursor)
    return merged


def select_reference(
    source: Path,
    output_dir: Path,
    whisper_model_dir: Path,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> Path:
    from faster_whisper import WhisperModel

    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    decoded_path = output_dir / "source-decoded.wav"
    _decode_source(source, decoded_path, ffmpeg)

    model = WhisperModel(str(whisper_model_dir.resolve()), device="cpu", compute_type="int8", local_files_only=True)
    segment_iterator, info = model.transcribe(
        str(decoded_path),
        language="zh",
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    raw_segments = list(segment_iterator)

    candidates: list[Candidate] = []
    for start, end, text, avg_logprob, no_speech_prob in _merge_whisper_segments(raw_segments):
        padded_start = max(0.0, start - 0.10)
        padded_end = min(float(info.duration), end + 0.10)
        samples = _read_audio_range(decoded_path, padded_start, padded_end)
        rms_dbfs, peak, silence_fraction = _signal_metrics(samples)
        candidates.append(
            Candidate(
                start=round(padded_start, 3),
                end=round(padded_end, 3),
                text=text,
                avg_logprob=round(avg_logprob, 6),
                no_speech_prob=round(no_speech_prob, 6),
                rms_dbfs=rms_dbfs,
                peak=peak,
                silence_fraction=silence_fraction,
            )
        )

    ranked = rank_candidates(candidates)
    if not ranked:
        candidate_path = output_dir / "candidates.json"
        candidate_path.write_text(
            json.dumps([asdict(item) | {"score": item.score} for item in candidates[:3]], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise RuntimeError(f"No clean reference candidate passed validation; inspect {candidate_path}")

    selected = ranked[0]
    temporary_reference = output_dir / "reference.wav.tmp.wav"
    reference_path = output_dir / "reference.wav"
    temporary_metadata = output_dir / "reference.json.tmp"
    metadata_path = output_dir / "reference.json"
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{selected.start:.3f}",
            "-t",
            f"{selected.duration:.3f}",
            "-i",
            str(decoded_path),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            str(temporary_reference),
        ]
    )
    temporary_reference.replace(reference_path)
    metadata = {
        "schema_version": 1,
        "design_version": "v1.9.0",
        "source": str(source),
        "source_duration_seconds": round(_probe_duration(source, ffprobe), 3),
        "reference": asdict(selected) | {"duration": round(selected.duration, 3), "score": round(selected.score, 6)},
        "whisper_language": info.language,
        "whisper_language_probability": round(float(info.language_probability), 6),
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
    }
    temporary_metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_metadata.replace(metadata_path)
    return metadata_path


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--whisper-model-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe") or "ffprobe")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    metadata_path = select_reference(
        args.source,
        args.output_dir,
        args.whisper_model_dir,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
    )
    print(metadata_path)
    print(metadata_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
