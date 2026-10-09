from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .config import Settings
from .tts import PiperTTSService, TTSBusy, TTSSynthesisFailed, TTSUnavailable


PROTOCOL_VERSION = 1
MAX_PROTOCOL_LINE_BYTES = 6 * 1024 * 1024
MAX_FRAGMENT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class AudioFragment:
    index: int
    audio: bytes
    engine: str
    device: str
    restart: bool = False


class StreamingTTSService(Protocol):
    def warmup(self) -> None: ...

    def stream(self, text: str) -> Iterator[AudioFragment]: ...

    def cancel(self, request_id: str | None = None) -> None: ...

    def close(self) -> None: ...

    def status(self) -> dict[str, Any]: ...


class GPTSoVITSWorkerService:
    """Supervise one isolated GPT-SoVITS worker process."""

    def __init__(
        self,
        settings: Settings,
        *,
        python_path: Path,
        device: str,
        popen_factory: Any = subprocess.Popen,
    ) -> None:
        self.settings = settings
        self.python_path = python_path.resolve()
        self.device = device
        self._popen_factory = popen_factory
        self._process: subprocess.Popen[str] | None = None
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._process_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._state = "configured"
        self._last_error: str | None = None
        self._current_request_id: str | None = None

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _trusted_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.project_root):
            raise TTSUnavailable("worker path outside project")
        return resolved

    def _command(self) -> list[str]:
        root = self.project_root
        return [
            str(self._trusted_path(self.python_path)),
            str(self._trusted_path(self.settings.gsv_worker_path)),
            "--repo-dir",
            str(root / "runtime" / "gpt-sovits" / "repo"),
            "--model-dir",
            str(root / "models" / "tts" / "gpt-sovits"),
            "--reference",
            str(root / "auditions" / "gpt-sovits-v1.9.0" / "reference.wav"),
            "--reference-metadata",
            str(root / "auditions" / "gpt-sovits-v1.9.0" / "reference.json"),
            "--profile",
            self.settings.gsv_profile,
            "--device",
            self.device,
        ]

    def _read_stdout(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            if len(line.encode("utf-8")) > MAX_PROTOCOL_LINE_BYTES:
                self._events.put({"event": "protocol_error", "code": "LINE_TOO_LARGE"})
                return
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self._events.put({"event": "protocol_error", "code": "INVALID_JSON"})
                return
            if not isinstance(event, dict):
                self._events.put({"event": "protocol_error", "code": "INVALID_EVENT"})
                return
            self._events.put(event)

    @staticmethod
    def _drain_stderr(process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for _line in process.stderr:
            pass

    def _send(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise TTSUnavailable("worker unavailable")
        line = json.dumps({"version": PROTOCOL_VERSION, **payload}, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            try:
                process.stdin.write(line + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise TTSUnavailable("worker pipe unavailable") from exc

    def _next_event(self, timeout: float) -> dict[str, Any]:
        try:
            event = self._events.get(timeout=timeout)
        except queue.Empty as exc:
            raise TTSSynthesisFailed("worker timeout") from exc
        if event.get("version") != PROTOCOL_VERSION:
            raise TTSSynthesisFailed("worker protocol version mismatch")
        if event.get("event") == "protocol_error":
            raise TTSSynthesisFailed("worker protocol error")
        return event

    def _next_request_event(self, request_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TTSSynthesisFailed("worker timeout")
            event = self._next_event(remaining)
            if event.get("request_id") == request_id:
                return event
            # A cancelled HTTP stream can leave its final worker event queued.
            # Ignore only well-formed stale request events; protocol errors still
            # fail in _next_event before reaching this branch.

    def warmup(self) -> None:
        with self._process_lock:
            if self._process is not None and self._process.poll() is None and self._state == "ready":
                return
            if self._state in {"failed", "unavailable"}:
                raise TTSUnavailable("worker disabled after startup failure")
            if not self.python_path.is_file() or not self.settings.gsv_worker_path.is_file():
                self._state = "unavailable"
                self._last_error = "RUNTIME_MISSING"
                raise TTSUnavailable("worker runtime unavailable")
            self._state = "warming"
            while not self._events.empty():
                try:
                    self._events.get_nowait()
                except queue.Empty:
                    break
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                process = self._popen_factory(
                    self._command(),
                    cwd=str(self.project_root),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                )
            except (OSError, ValueError) as exc:
                self._state = "unavailable"
                self._last_error = "PROCESS_START_FAILED"
                raise TTSUnavailable("worker process failed to start") from exc
            self._process = process
            threading.Thread(target=self._read_stdout, args=(process,), daemon=True).start()
            threading.Thread(target=self._drain_stderr, args=(process,), daemon=True).start()
            try:
                event = self._next_event(self.settings.gsv_warmup_timeout_seconds)
                if event.get("event") != "ready" or event.get("device") != self.device:
                    raise TTSUnavailable("worker failed to become ready")
            except (TTSUnavailable, TTSSynthesisFailed):
                self._state = "failed"
                self._last_error = "MODEL_LOAD_FAILED"
                self._terminate_process()
                raise
            self._state = "ready"
            self._last_error = None

    def stream(self, text: str) -> Iterator[AudioFragment]:
        if not self._request_lock.acquire(blocking=False):
            raise TTSBusy("synthesis already running")
        request_id = uuid.uuid4().hex
        self._current_request_id = request_id
        expected_index = 0
        try:
            self.warmup()
            self._send({"command": "synthesize", "request_id": request_id, "text": text})
            fragment_timeout = (
                self.settings.gsv_cpu_fragment_timeout_seconds
                if self.device == "cpu"
                else self.settings.gsv_fragment_timeout_seconds
            )
            while True:
                event = self._next_request_event(request_id, fragment_timeout)
                event_name = event.get("event")
                if event_name == "started":
                    continue
                if event_name == "fragment":
                    if event.get("index") != expected_index or event.get("media_type") != "audio/wav":
                        raise TTSSynthesisFailed("worker fragment order invalid")
                    try:
                        audio = base64.b64decode(event.get("audio_base64", ""), validate=True)
                    except (ValueError, TypeError) as exc:
                        raise TTSSynthesisFailed("worker audio encoding invalid") from exc
                    if not audio.startswith(b"RIFF") or len(audio) > MAX_FRAGMENT_BYTES:
                        raise TTSSynthesisFailed("worker audio invalid")
                    yield AudioFragment(expected_index, audio, "gpt-sovits", self.device)
                    expected_index += 1
                    continue
                if event_name == "completed":
                    if event.get("fragment_count") != expected_index or expected_index == 0:
                        raise TTSSynthesisFailed("worker completion invalid")
                    return
                if event_name == "cancelled":
                    raise TTSSynthesisFailed("worker request cancelled")
                if event_name == "error":
                    self._last_error = str(event.get("code") or "SYNTHESIS_FAILED")
                    raise TTSSynthesisFailed("worker synthesis failed")
                raise TTSSynthesisFailed("worker event unsupported")
        finally:
            self._current_request_id = None
            self._request_lock.release()

    def cancel(self, request_id: str | None = None) -> None:
        active = request_id or self._current_request_id
        if active and self._process is not None:
            try:
                self._send({"command": "cancel", "request_id": active})
            except TTSUnavailable:
                pass

    def _terminate_process(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()

    def close(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            try:
                self._send({"command": "shutdown", "request_id": "shutdown"})
                process.wait(timeout=3)
            except (TTSUnavailable, OSError, subprocess.TimeoutExpired):
                self._terminate_process()
        self._process = None
        self._state = "unavailable"

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "status": self._state,
            "engine": "gpt-sovits",
            "device": self.device,
            "last_error": self._last_error,
        }


@dataclass
class _CacheEntry:
    fragments: tuple[AudioFragment, ...]
    bytes_total: int


class FallbackStreamingTTSService:
    def __init__(
        self,
        settings: Settings,
        *,
        cuda: StreamingTTSService | None,
        cpu: StreamingTTSService | None,
        piper: PiperTTSService,
    ) -> None:
        self.settings = settings
        self.cuda = cuda
        self.cpu = cpu
        self.piper = piper
        self._lock = threading.Lock()
        self._active: StreamingTTSService | None = None
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._cache_bytes = 0
        self._preferred_device: str | None = None
        self._last_error: str | None = None

    def _providers(self) -> list[tuple[str, StreamingTTSService]]:
        values: list[tuple[str, StreamingTTSService]] = []
        if self.cuda is not None:
            values.append(("cuda", self.cuda))
        if self.cpu is not None:
            values.append(("cpu", self.cpu))
        return values

    def warmup(self) -> None:
        for device, provider in self._providers():
            try:
                provider.warmup()
            except (TTSUnavailable, TTSSynthesisFailed):
                self._last_error = f"{device.upper()}_WARMUP_FAILED"
                continue
            self._preferred_device = device
            self._last_error = None
            return

    def _cache_key(self, text: str, engine: str, device: str) -> str:
        source = f"{text.strip()}\0{self.settings.gsv_profile}\0{engine}\0{device}".encode("utf-8")
        return hashlib.sha256(source).hexdigest()

    def _cached(self, text: str) -> tuple[AudioFragment, ...] | None:
        for engine, device in (("gpt-sovits", "cuda"), ("gpt-sovits", "cpu"), ("piper", "cpu")):
            key = self._cache_key(text, engine, device)
            entry = self._cache.get(key)
            if entry is not None:
                self._cache.move_to_end(key)
                return entry.fragments
        return None

    def _put_cache(self, text: str, fragments: list[AudioFragment]) -> None:
        if not fragments:
            return
        total = sum(len(item.audio) for item in fragments)
        if total > self.settings.gsv_cache_bytes:
            return
        key = self._cache_key(text, fragments[0].engine, fragments[0].device)
        previous = self._cache.pop(key, None)
        if previous is not None:
            self._cache_bytes -= previous.bytes_total
        self._cache[key] = _CacheEntry(tuple(fragments), total)
        self._cache_bytes += total
        while len(self._cache) > self.settings.gsv_cache_items or self._cache_bytes > self.settings.gsv_cache_bytes:
            _, removed = self._cache.popitem(last=False)
            self._cache_bytes -= removed.bytes_total

    def _collect_recovery(self, text: str, failed_device: str) -> list[AudioFragment] | None:
        """Synthesize a clean replacement stream without exposing partial audio."""
        candidates: list[tuple[str, StreamingTTSService]] = []
        if failed_device == "cuda" and self.cpu is not None:
            candidates.append(("cpu", self.cpu))
        for device, provider in candidates:
            self._active = provider
            try:
                replacement = list(provider.stream(text))
                if replacement:
                    return [AudioFragment(index, item.audio, item.engine, item.device) for index, item in enumerate(replacement)]
            except (TTSUnavailable, TTSSynthesisFailed):
                self._last_error = f"{device.upper()}_RECOVERY_FAILED"
            finally:
                self._active = None
        try:
            replacement_audio = self.piper.synthesize(text)
        except (TTSUnavailable, TTSSynthesisFailed):
            self._last_error = "PIPER_SYNTHESIS_FAILED"
            return None
        return [AudioFragment(0, replacement_audio, "piper", "cpu")]

    def stream(self, text: str) -> Iterator[AudioFragment]:
        if not self._lock.acquire(blocking=False):
            raise TTSBusy("synthesis already running")
        try:
            cached = self._cached(text)
            if cached is not None:
                self._last_error = None
                yield from cached
                return
            for device, provider in self._providers():
                produced: list[AudioFragment] = []
                self._active = provider
                try:
                    for fragment in provider.stream(text):
                        produced.append(fragment)
                        yield fragment
                    self._preferred_device = device
                    self._last_error = None
                    self._put_cache(text, produced)
                    return
                except (TTSUnavailable, TTSSynthesisFailed):
                    self._last_error = f"{device.upper()}_SYNTHESIS_FAILED"
                    if produced:
                        replacement = self._collect_recovery(text, device)
                        if replacement is None:
                            raise
                        self._preferred_device = replacement[0].device
                        self._last_error = None
                        self._put_cache(text, replacement)
                        for index, fragment in enumerate(replacement):
                            yield AudioFragment(index, fragment.audio, fragment.engine, fragment.device, restart=index == 0)
                        return
                    continue
                finally:
                    self._active = None
            try:
                audio = self.piper.synthesize(text)
            except (TTSUnavailable, TTSSynthesisFailed):
                self._last_error = "PIPER_SYNTHESIS_FAILED"
                raise TTSUnavailable("all local tts engines unavailable")
            fragment = AudioFragment(0, audio, "piper", "cpu")
            self._last_error = None
            self._put_cache(text, [fragment])
            yield fragment
        finally:
            self._active = None
            self._lock.release()

    def cancel(self, request_id: str | None = None) -> None:
        if self._active is not None:
            self._active.cancel(request_id)

    def close(self) -> None:
        for _, provider in self._providers():
            provider.close()
        self._cache.clear()
        self._cache_bytes = 0

    def status(self) -> dict[str, Any]:
        provider_statuses = [provider.status() for _, provider in self._providers()]
        ready = next((item for item in provider_statuses if item.get("status") == "ready"), None)
        warming = any(item.get("status") == "warming" for item in provider_statuses)
        piper_status = self.piper.status()
        return {
            "enabled": True,
            "status": "ready" if ready else "warming" if warming else "degraded" if piper_status.get("status") in {"ready", "configured"} else "unavailable",
            "primary_engine": "gpt-sovits",
            "device": ready.get("device") if ready else self._preferred_device,
            "fallback_ready": piper_status.get("status") in {"ready", "configured"},
            "last_error": self._last_error,
        }
