#!/usr/bin/env python3
"""Isolated GPT-SoVITS worker using versioned NDJSON over stdio."""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import queue
import re
import sys
import threading
import traceback
import wave
from array import array
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, TextIO


PROTOCOL_VERSION = 1
MAX_COMMAND_BYTES = 16 * 1024
MAX_TEXT_CHARS = 500
MAX_FRAGMENT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class WorkerCommand:
    command: str
    request_id: str
    text: str | None = None


@dataclass(frozen=True)
class WorkerConfig:
    repo_dir: Path
    model_dir: Path
    reference_path: Path
    reference_metadata_path: Path
    profile_id: str
    device: str


def parse_command(line: str) -> WorkerCommand:
    if len(line.encode("utf-8")) > MAX_COMMAND_BYTES:
        raise ValueError("COMMAND_TOO_LARGE")
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError("INVALID_JSON") from exc
    if not isinstance(payload, dict) or payload.get("version") != PROTOCOL_VERSION:
        raise ValueError("PROTOCOL_VERSION_UNSUPPORTED")
    command = payload.get("command")
    request_id = payload.get("request_id")
    if command not in {"ping", "synthesize", "cancel", "shutdown"}:
        raise ValueError("COMMAND_UNSUPPORTED")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise ValueError("REQUEST_ID_INVALID")
    text = payload.get("text")
    if command == "synthesize":
        if not isinstance(text, str) or not text.strip():
            raise ValueError("TEXT_EMPTY")
        text = text.strip()
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError("TEXT_TOO_LONG")
    else:
        text = None
    return WorkerCommand(command=command, request_id=request_id, text=text)


def encode_event(event: dict[str, Any]) -> str:
    payload = {"version": PROTOCOL_VERSION, **event}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def _pcm16_wav(sample_rate: int, audio: Any) -> bytes:
    import numpy as np

    samples = np.asarray(audio).reshape(-1)
    if samples.size == 0 or not np.all(np.isfinite(samples)):
        raise ValueError("INVALID_AUDIO")
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
    output = BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(samples.tobytes())
    payload = output.getvalue()
    if len(payload) > MAX_FRAGMENT_BYTES:
        raise ValueError("FRAGMENT_TOO_LARGE")
    return payload


def stream_events(
    model: Any,
    command: WorkerCommand,
    *,
    device: str,
    cancelled: Callable[[], bool] = lambda: False,
) -> Iterator[dict[str, Any]]:
    if command.command != "synthesize" or command.text is None:
        raise ValueError("SYNTHESIZE_COMMAND_REQUIRED")
    yield {"event": "started", "request_id": command.request_id, "device": device}
    count = 0
    for sample_rate, audio in model.stream(command.text):
        if cancelled():
            yield {"event": "cancelled", "request_id": command.request_id}
            return
        wav = _pcm16_wav(int(sample_rate), audio)
        yield {
            "event": "fragment",
            "request_id": command.request_id,
            "index": count,
            "media_type": "audio/wav",
            "audio_base64": base64.b64encode(wav).decode("ascii"),
        }
        count += 1
    if count == 0:
        raise ValueError("EMPTY_STREAM")
    yield {"event": "completed", "request_id": command.request_id, "fragment_count": count}


class GPTSoVITSModel:
    def __init__(self, config: WorkerConfig) -> None:
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / "tools"))
        from gpt_sovits_audition import get_profile

        self.profile = get_profile(config.profile_id)
        metadata = json.loads(config.reference_metadata_path.read_text(encoding="utf-8"))
        reference = metadata.get("reference") if isinstance(metadata, dict) else None
        if not isinstance(reference, dict):
            raise ValueError("REFERENCE_METADATA_INVALID")
        self.prompt_text = str(reference.get("text", "")).strip()
        if not self.prompt_text or re.search(r"[\u3400-\u9fff]", self.prompt_text) is None:
            raise ValueError("REFERENCE_TEXT_INVALID")
        self.reference_path = config.reference_path.resolve()
        if not self.reference_path.is_file():
            raise FileNotFoundError("REFERENCE_AUDIO_MISSING")

        repo_dir = config.repo_dir.resolve()
        model_dir = config.model_dir.resolve()
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ["bert_path"] = str(model_dir / "chinese-roberta-wwm-ext-large")
        os.chdir(repo_dir)
        sys.path.insert(0, str(repo_dir))
        sys.path.insert(0, str(repo_dir / "GPT_SoVITS"))

        with contextlib.redirect_stdout(sys.stderr):
            from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

            self.pipeline = TTS(
                TTS_Config(
                    {
                        "custom": {
                            "device": config.device,
                            "is_half": config.device == "cuda",
                            "version": "v5turbo",
                            "t2s_weights_path": str(model_dir / "s1v3.ckpt"),
                            "vits_weights_path": str(model_dir / "gsv-v5-pretrained" / "s2Gv5turbo.pth"),
                            "cnhuhbert_base_path": str(model_dir / "chinese-hubert-base"),
                            "bert_base_path": str(model_dir / "chinese-roberta-wwm-ext-large"),
                        }
                    }
                )
            )
            if config.device == "cuda":
                # The language model is used only to build text features. Keep
                # semantic generation and the vocoder on CUDA, but move BERT to
                # CPU so a 4 GiB laptop GPU retains a safe playback/display
                # margin. The resulting features are moved back by TTS.run.
                import torch

                self.pipeline.bert_model = self.pipeline.bert_model.float().to("cpu")
                self.pipeline.text_preprocessor.bert_model = self.pipeline.bert_model
                self.pipeline.text_preprocessor.device = torch.device("cpu")
                torch.cuda.empty_cache()

    def stream(self, text: str) -> Iterable[tuple[int, Any]]:
        parameters = self.profile.parameters
        inputs = {
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": str(self.reference_path),
            "aux_ref_audio_paths": [],
            "prompt_text": self.prompt_text,
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
            "return_fragment": True,
            "streaming_mode": True,
        }
        with contextlib.redirect_stdout(sys.stderr):
            yield from self.pipeline.run(inputs)


def run_worker(
    config: WorkerConfig,
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    model_factory: Callable[[WorkerConfig], Any] = GPTSoVITSModel,
) -> int:
    write_lock = threading.Lock()
    commands: queue.Queue[WorkerCommand | None] = queue.Queue()
    cancelled_ids: set[str] = set()
    shutdown = threading.Event()

    def emit(event: dict[str, Any]) -> None:
        with write_lock:
            stdout.write(encode_event(event))
            stdout.flush()

    try:
        model = model_factory(config)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        emit({"event": "error", "request_id": "startup", "code": "MODEL_LOAD_FAILED", "retryable": False})
        return 2
    emit({"event": "ready", "request_id": "startup", "device": config.device})

    def read_commands() -> None:
        for line in stdin:
            try:
                command = parse_command(line)
            except ValueError as exc:
                emit({"event": "error", "request_id": "invalid", "code": str(exc), "retryable": False})
                continue
            if command.command == "cancel":
                cancelled_ids.add(command.request_id)
            elif command.command == "shutdown":
                shutdown.set()
                commands.put(None)
                return
            else:
                commands.put(command)
        shutdown.set()
        commands.put(None)

    reader = threading.Thread(target=read_commands, name="gsv-worker-stdin", daemon=True)
    reader.start()
    while not shutdown.is_set():
        command = commands.get()
        if command is None:
            break
        if command.command == "ping":
            emit({"event": "ready", "request_id": command.request_id, "device": config.device})
            continue
        try:
            for event in stream_events(
                model,
                command,
                device=config.device,
                cancelled=lambda request_id=command.request_id: shutdown.is_set() or request_id in cancelled_ids,
            ):
                emit(event)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            emit({"event": "error", "request_id": command.request_id, "code": "SYNTHESIS_FAILED", "retryable": True})
        finally:
            cancelled_ids.discard(command.request_id)
    return 0


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reference-metadata", type=Path, required=True)
    parser.add_argument("--profile", default="v1.9.1")
    parser.add_argument("--device", choices=("cpu", "cuda"), required=True)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    # Windows pipe streams otherwise inherit the active ANSI code page (often
    # GBK), while the API supervisor writes UTF-8 NDJSON. Pin all protocol and
    # diagnostic streams so Chinese request text cannot corrupt the command.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = _parse_args(argv)
    return run_worker(
        WorkerConfig(
            repo_dir=args.repo_dir,
            model_dir=args.model_dir,
            reference_path=args.reference,
            reference_metadata_path=args.reference_metadata,
            profile_id=args.profile,
            device=args.device,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
