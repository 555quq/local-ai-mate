#!/usr/bin/env python3
"""Benchmark a GPT-SoVITS stdio worker without retaining generated audio."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import queue
import subprocess
import threading
import time
import wave
from collections import deque
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from gpt_sovits_audition import get_profile


def _reader(stream: Any, events: queue.Queue[dict[str, Any]]) -> None:
    for line in stream:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            events.put({"event": "invalid", "raw_prefix": line[:160]})
            return
        if isinstance(payload, dict):
            events.put(payload)


def _drain(stream: Any, lines: deque[str]) -> None:
    for line in stream:
        lines.append(line.rstrip())


def _gpu_memory_mib() -> int | None:
    """Return total device memory in use.

    On Windows a venv launcher can hand execution to a child python.exe, so the
    PID returned by Popen is not guaranteed to own the CUDA context. Measuring
    the whole GPU is deliberately conservative and preserves the 512 MiB
    safety margin even when the desktop or another local service uses VRAM.
    """
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first_line = result.stdout.splitlines()[0].strip() if result.stdout.splitlines() else ""
    try:
        return int(first_line)
    except ValueError:
        return None


def _validate_wav(encoded: str) -> dict[str, Any]:
    audio = base64.b64decode(encoded, validate=True)
    with wave.open(BytesIO(audio), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2 or handle.getnframes() <= 0:
            raise ValueError("invalid wav fragment")
        return {
            "bytes": len(audio),
            "sample_rate": handle.getframerate(),
            "duration_seconds": round(handle.getnframes() / handle.getframerate(), 3),
            "sha256": hashlib.sha256(audio).hexdigest(),
        }


def benchmark(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        str(args.python),
        str(args.worker),
        "--repo-dir",
        str(args.repo_dir),
        "--model-dir",
        str(args.model_dir),
        "--reference",
        str(args.reference),
        "--reference-metadata",
        str(args.reference_metadata),
        "--profile",
        args.profile,
        "--device",
        args.device,
    ]
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        cwd=str(args.project_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    if process.stdout is None or process.stdin is None or process.stderr is None:
        raise RuntimeError("worker pipes unavailable")
    events: queue.Queue[dict[str, Any]] = queue.Queue()
    stderr_tail: deque[str] = deque(maxlen=40)
    threading.Thread(target=_reader, args=(process.stdout, events), daemon=True).start()
    threading.Thread(target=_drain, args=(process.stderr, stderr_tail), daemon=True).start()
    try:
        ready = events.get(timeout=args.warmup_timeout)
        startup_seconds = round(time.perf_counter() - started, 3)
        if ready.get("event") != "ready":
            return {"device": args.device, "accepted": False, "startup_seconds": startup_seconds, "error": "MODEL_LOAD_FAILED", "runs": []}
        runs: list[dict[str, Any]] = []
        peak_memory = _gpu_memory_mib() if args.device == "cuda" else None
        profile = get_profile(args.profile)
        for repeat in range(args.repeats):
            for sample in profile.samples:
                request_id = f"benchmark-{repeat + 1}-{sample.audition_id}"
                request_started = time.perf_counter()
                process.stdin.write(json.dumps({"version": 1, "command": "synthesize", "request_id": request_id, "text": sample.text}, ensure_ascii=False) + "\n")
                process.stdin.flush()
                first_fragment: float | None = None
                fragments: list[dict[str, Any]] = []
                while True:
                    event = events.get(timeout=args.fragment_timeout)
                    if event.get("request_id") != request_id:
                        raise RuntimeError(
                            f"worker request mismatch: event={event.get('event')!r}, "
                            f"request_id={event.get('request_id')!r}, raw_prefix={event.get('raw_prefix')!r}"
                        )
                    if event.get("event") == "fragment":
                        if first_fragment is None:
                            first_fragment = time.perf_counter()
                        fragments.append(_validate_wav(str(event.get("audio_base64", ""))))
                        if args.device == "cuda":
                            memory = _gpu_memory_mib()
                            if memory is not None:
                                peak_memory = max(peak_memory or 0, memory)
                    elif event.get("event") == "completed":
                        total_seconds = time.perf_counter() - request_started
                        audio_seconds = sum(item["duration_seconds"] for item in fragments)
                        runs.append(
                            {
                                "repeat": repeat + 1,
                                "audition_id": sample.audition_id,
                                "text": sample.text,
                                "ttff_seconds": round((first_fragment or time.perf_counter()) - request_started, 3),
                                "total_seconds": round(total_seconds, 3),
                                "audio_seconds": round(audio_seconds, 3),
                                "real_time_factor": round(total_seconds / audio_seconds, 3) if audio_seconds else None,
                                "fragments": fragments,
                            }
                        )
                        break
                    elif event.get("event") == "error":
                        raise RuntimeError(str(event.get("code") or "SYNTHESIS_FAILED"))
        expected_runs = args.repeats * len(profile.samples)
        safety_ok = args.device != "cuda" or (peak_memory is not None and peak_memory <= 3584)
        return {
            "device": args.device,
            "accepted": len(runs) == expected_runs and safety_ok,
            "startup_seconds": startup_seconds,
            "peak_gpu_memory_mib": peak_memory,
            "gpu_safety_margin_mib": 4096 - peak_memory if peak_memory is not None else None,
            "runs": runs,
        }
    except (queue.Empty, RuntimeError, ValueError) as exc:
        return {
            "device": args.device,
            "accepted": False,
            "error": type(exc).__name__,
            "error_message": str(exc),
            "stderr_tail": list(stderr_tail),
            "runs": [],
        }
    finally:
        if process.poll() is None:
            try:
                process.stdin.write(json.dumps({"version": 1, "command": "shutdown", "request_id": "shutdown"}) + "\n")
                process.stdin.flush()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--repo-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reference-metadata", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", default="v1.9.1")
    parser.add_argument("--device", choices=("cpu", "cuda"), required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup-timeout", type=float, default=90)
    parser.add_argument("--fragment-timeout", type=float, default=30)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    report = benchmark(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(args.output)
    console_summary = {key: value for key, value in report.items() if key not in {"runs", "stderr_tail"}}
    print(json.dumps(console_summary, ensure_ascii=True))
    return 0 if report.get("accepted") else 2


if __name__ == "__main__":
    raise SystemExit(main())
