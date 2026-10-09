from __future__ import annotations

import argparse
import base64
import contextlib
import json
import sys
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 12 * 1024 * 1024
MAX_AUDIO_BYTES = 8 * 1024 * 1024


def emit(payload: dict[str, Any]) -> None:
    line = json.dumps({"version": PROTOCOL_VERSION, **payload}, ensure_ascii=False, separators=(",", ":")) + "\n"
    output = getattr(sys.stdout, "buffer", sys.stdout)
    output.write(line.encode("utf-8"))
    output.flush()


def duration_ms(audio: bytes) -> int:
    try:
        import av

        with av.open(BytesIO(audio)) as container:
            stream = next((item for item in container.streams.audio), None)
            if stream is not None and stream.duration is not None and stream.time_base is not None:
                return max(0, round(float(stream.duration * stream.time_base) * 1000))
    except Exception:
        pass
    return 0


def load_model(model_dir: Path) -> Any:
    with contextlib.redirect_stdout(sys.stderr):
        from funasr import AutoModel

        return AutoModel(
            model=str(model_dir),
            device="cpu",
            disable_update=True,
            disable_log=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    args = parser.parse_args()
    model_dir = Path(args.model_dir).resolve()
    try:
        model = load_model(model_dir)
    except Exception:
        emit({"event": "error", "code": "MODEL_LOAD_FAILED"})
        return 2
    emit({"event": "ready", "engine": "sensevoice", "device": "cpu"})

    for raw_line in sys.stdin.buffer:
        if len(raw_line) > MAX_LINE_BYTES:
            emit({"event": "error", "code": "LINE_TOO_LARGE"})
            return 3
        try:
            payload = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            emit({"event": "error", "code": "INVALID_JSON"})
            continue
        if not isinstance(payload, dict) or payload.get("version") != PROTOCOL_VERSION:
            emit({"event": "error", "code": "PROTOCOL_VERSION"})
            continue
        command = payload.get("command")
        if command == "shutdown":
            emit({"event": "shutdown"})
            return 0
        if command != "transcribe":
            emit({"event": "error", "request_id": payload.get("request_id"), "code": "UNKNOWN_COMMAND"})
            continue
        request_id = str(payload.get("request_id") or uuid.uuid4().hex)
        try:
            audio = base64.b64decode(payload.get("audio_base64", ""), validate=True)
            if not audio or len(audio) > MAX_AUDIO_BYTES:
                raise ValueError("audio limit")
            # FunASR may print non-UTF-8 progress/warnings while decoding. Keep
            # stdout reserved for the NDJSON protocol consumed by the parent.
            with contextlib.redirect_stdout(sys.stderr):
                result = model.generate(
                    input=audio,
                    language="zh",
                    use_itn=True,
                    batch_size=1,
                )
            raw_text = str(result[0].get("text", "") if result else "")
            try:
                from funasr.utils.postprocess_utils import rich_transcription_postprocess

                text = rich_transcription_postprocess(raw_text).strip()
            except Exception:
                text = raw_text.strip()
            emit({
                "event": "transcribed",
                "request_id": request_id,
                "text": text,
                "language": "zh",
                "duration_ms": duration_ms(audio),
            })
        except Exception:
            emit({"event": "error", "request_id": request_id, "code": "TRANSCRIPTION_FAILED"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
