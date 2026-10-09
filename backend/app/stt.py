from __future__ import annotations

import importlib.util
import base64
import json
import os
import queue
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

from .config import Settings


class STTUnavailable(RuntimeError):
    """The local speech recognition runtime or model cannot be used."""


class STTBusy(RuntimeError):
    """A transcription is already running."""


class STTTranscriptionFailed(RuntimeError):
    """The local speech recognition model failed to transcribe audio."""


@dataclass(frozen=True, slots=True)
class STTResult:
    text: str
    language: str
    duration_ms: int


class STTService(Protocol):
    def status(self) -> dict[str, Any]: ...

    def transcribe(self, audio: bytes, content_type: str) -> STTResult: ...


class FasterWhisperSTTService:
    """Lazy, single-task faster-whisper wrapper fixed to CPU int8."""

    def __init__(
        self,
        settings: Settings,
        *,
        model_loader: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self._model_loader = model_loader
        self._model: Any | None = None
        self._lock = threading.Lock()
        self._failed = False

    def status(self) -> dict[str, Any]:
        if not self.settings.stt_enabled:
            return {"enabled": False, "status": "unavailable"}
        if self._failed:
            return {"enabled": True, "status": "failed"}
        if not self.settings.stt_model_path.is_dir():
            return {"enabled": True, "status": "unavailable"}
        if self._model_loader is None and importlib.util.find_spec("faster_whisper") is None:
            return {"enabled": True, "status": "unavailable"}
        return {"enabled": True, "status": "ready" if self._model is not None else "configured"}

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        if not self.settings.stt_enabled:
            raise STTUnavailable("stt disabled")
        if not self.settings.stt_model_path.is_dir():
            raise STTUnavailable("speech recognition model unavailable")
        try:
            if self._model_loader is not None:
                loader = self._model_loader
            else:
                from faster_whisper import WhisperModel

                loader = WhisperModel
            self._model = loader(
                str(self.settings.stt_model_path),
                device="cpu",
                compute_type="int8",
                local_files_only=True,
            )
        except (ImportError, ModuleNotFoundError) as exc:
            raise STTUnavailable("speech recognition runtime unavailable") from exc
        except STTUnavailable:
            raise
        except Exception as exc:
            self._failed = True
            raise STTTranscriptionFailed("speech recognition model load failed") from exc
        return self._model

    def transcribe(self, audio: bytes, content_type: str) -> STTResult:
        if not self._lock.acquire(blocking=False):
            raise STTBusy("transcription already running")
        try:
            model = self._load_model()
            audio_buffer = BytesIO(audio)
            try:
                segments, info = model.transcribe(
                    audio_buffer,
                    language="zh",
                    beam_size=1,
                    vad_filter=True,
                    condition_on_previous_text=False,
                )
                text = "".join(segment.text for segment in segments).strip()
                language = str(getattr(info, "language", "zh") or "zh")
                duration_ms = max(0, round(float(getattr(info, "duration", 0.0)) * 1000))
                return STTResult(text=text, language=language, duration_ms=duration_ms)
            except Exception as exc:
                raise STTTranscriptionFailed("speech transcription failed") from exc
            finally:
                audio_buffer.close()
        finally:
            self._lock.release()


class SenseVoiceSTTService:
    """Run SenseVoiceSmall in an isolated local worker with Whisper fallback."""

    protocol_version = 1
    max_line_bytes = 12 * 1024 * 1024

    def __init__(
        self,
        settings: Settings,
        *,
        fallback: STTService | None = None,
        popen_factory: Any = subprocess.Popen,
    ) -> None:
        self.settings = settings
        self.fallback = fallback
        self._popen_factory = popen_factory
        self._process: subprocess.Popen[str] | None = None
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._state = "configured"
        self._last_error: str | None = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _trusted_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.project_root):
            raise STTUnavailable("worker path outside project")
        return resolved

    def _command(self) -> list[str]:
        return [
            str(self._trusted_path(self.settings.sensevoice_python)),
            str(self._trusted_path(self.settings.sensevoice_worker_path)),
            "--model-dir",
            str(self._trusted_path(self.settings.sensevoice_model_path)),
        ]

    def _read_stdout(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            if len(line.encode("utf-8")) > self.max_line_bytes:
                self._events.put({"event": "protocol_error", "code": "LINE_TOO_LARGE"})
                return
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self._events.put({"event": "protocol_error", "code": "INVALID_JSON"})
                return
            if isinstance(event, dict):
                self._events.put(event)
            else:
                self._events.put({"event": "protocol_error", "code": "INVALID_EVENT"})

    @staticmethod
    def _drain_stderr(process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for _line in process.stderr:
            pass

    def _next_event(self, timeout: float) -> dict[str, Any]:
        try:
            event = self._events.get(timeout=timeout)
        except queue.Empty as exc:
            raise STTTranscriptionFailed("worker timeout") from exc
        if event.get("version") != self.protocol_version:
            raise STTTranscriptionFailed("worker protocol version mismatch")
        if event.get("event") == "protocol_error":
            raise STTTranscriptionFailed("worker protocol error")
        return event

    def _send(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise STTUnavailable("worker unavailable")
        line = json.dumps({"version": self.protocol_version, **payload}, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            try:
                process.stdin.write(line + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise STTUnavailable("worker pipe unavailable") from exc

    def _terminate(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

    def _start(self) -> None:
        if self._process is not None and self._process.poll() is None and self._state == "ready":
            return
        if not self.settings.sensevoice_python.is_file() or not self.settings.sensevoice_worker_path.is_file():
            self._state = "unavailable"
            self._last_error = "RUNTIME_MISSING"
            raise STTUnavailable("sensevoice runtime unavailable")
        if not self.settings.sensevoice_model_path.is_dir():
            self._state = "unavailable"
            self._last_error = "MODEL_MISSING"
            raise STTUnavailable("sensevoice model unavailable")
        self._state = "warming"
        while not self._events.empty():
            try:
                self._events.get_nowait()
            except queue.Empty:
                break
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = self._popen_factory(
                self._command(), cwd=str(self.project_root), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=creationflags,
            )
        except (OSError, ValueError) as exc:
            self._state = "unavailable"
            self._last_error = "PROCESS_START_FAILED"
            raise STTUnavailable("sensevoice worker failed to start") from exc
        self._process = process
        threading.Thread(target=self._read_stdout, args=(process,), daemon=True).start()
        threading.Thread(target=self._drain_stderr, args=(process,), daemon=True).start()
        try:
            event = self._next_event(90)
            if event.get("event") != "ready" or event.get("engine") != "sensevoice":
                raise STTUnavailable("sensevoice worker failed to become ready")
        except (STTUnavailable, STTTranscriptionFailed):
            self._state = "failed"
            self._last_error = "MODEL_LOAD_FAILED"
            self._terminate()
            raise
        self._state = "ready"
        self._last_error = None

    def _fallback_status(self) -> dict[str, Any] | None:
        if self.fallback is None:
            return None
        try:
            return self.fallback.status()
        except Exception:
            return {"enabled": True, "status": "unavailable"}

    def status(self) -> dict[str, Any]:
        if not self.settings.stt_enabled:
            return {"enabled": False, "status": "unavailable"}
        fallback_status = self._fallback_status()
        runtime_ready = (
            self.settings.sensevoice_python.is_file()
            and self.settings.sensevoice_worker_path.is_file()
            and self.settings.sensevoice_model_path.is_dir()
        )
        if self._state in {"failed", "unavailable"} or not runtime_ready:
            if fallback_status and fallback_status.get("status") in {"configured", "ready"}:
                return {"enabled": True, "status": "degraded", "engine": "faster-whisper", "fallback_ready": True}
            return {"enabled": True, "status": "unavailable", "engine": "sensevoice", "fallback_ready": False}
        return {
            "enabled": True,
            "status": "ready" if self._state == "ready" else "configured",
            "engine": "sensevoice",
            "device": "cpu",
            "fallback_ready": bool(fallback_status and fallback_status.get("status") in {"configured", "ready"}),
        }

    def warmup(self) -> None:
        """Load the isolated worker before the first browser recording."""
        if not self.settings.stt_enabled or not self._lock.acquire(blocking=False):
            return
        try:
            self._start()
        except (STTUnavailable, STTTranscriptionFailed):
            # The request path will continue to use the faster-whisper fallback.
            return
        finally:
            self._lock.release()

    def transcribe(self, audio: bytes, content_type: str) -> STTResult:
        if not self._lock.acquire(blocking=False):
            raise STTBusy("transcription already running")
        try:
            request_id = uuid.uuid4().hex
            try:
                self._start()
                self._send({
                    "command": "transcribe",
                    "request_id": request_id,
                    "audio_base64": base64.b64encode(audio).decode("ascii"),
                })
                deadline = time.monotonic() + max(30.0, self.settings.stt_max_duration_ms / 1000 * 3)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise STTTranscriptionFailed("worker timeout")
                    event = self._next_event(remaining)
                    if event.get("request_id") != request_id:
                        continue
                    if event.get("event") == "error":
                        raise STTTranscriptionFailed("sensevoice worker failed")
                    if event.get("event") != "transcribed":
                        raise STTTranscriptionFailed("worker event unsupported")
                    return STTResult(
                        text=str(event.get("text") or "").strip(),
                        language=str(event.get("language") or "zh"),
                        duration_ms=max(0, int(event.get("duration_ms") or 0)),
                    )
            except (STTUnavailable, STTTranscriptionFailed):
                self._last_error = "SENSEVOICE_FAILED"
                self._state = "failed"
                self._terminate()
                if self.fallback is not None:
                    return self.fallback.transcribe(audio, content_type)
                raise
        finally:
            self._lock.release()

    def close(self) -> None:
        if self._process is not None and self._process.poll() is None:
            try:
                self._send({"command": "shutdown", "request_id": "shutdown"})
                self._process.wait(timeout=3)
            except (STTUnavailable, OSError, subprocess.TimeoutExpired):
                self._terminate()
        self._process = None
        self._state = "unavailable"
        if self.fallback is not None:
            close = getattr(self.fallback, "close", None)
            if close is not None:
                close()
