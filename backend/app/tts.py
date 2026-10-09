from __future__ import annotations

import importlib.util
import threading
import wave
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

from .config import Settings


class TTSUnavailable(RuntimeError):
    """The local runtime or configured voice cannot be used."""


class TTSBusy(RuntimeError):
    """A synthesis is already running."""


class TTSSynthesisFailed(RuntimeError):
    """Piper failed while producing audio."""


class TTSService(Protocol):
    def status(self) -> dict[str, Any]: ...

    def synthesize(self, text: str) -> bytes: ...


class PiperTTSService:
    """Lazy, CPU-only Piper wrapper that never writes synthesized audio to disk."""

    def __init__(
        self,
        settings: Settings,
        *,
        voice_loader: Callable[[Path], Any] | None = None,
    ) -> None:
        self.settings = settings
        self._voice_loader = voice_loader
        self._voice: Any | None = None
        self._lock = threading.Lock()
        self._failed = False

    @property
    def _config_path(self) -> Path:
        return Path(f"{self.settings.tts_model_path}.json")

    def status(self) -> dict[str, Any]:
        if not self.settings.tts_enabled:
            return {"enabled": False, "status": "unavailable"}
        if self._failed:
            return {"enabled": True, "status": "failed"}
        if not self.settings.tts_model_path.is_file() or not self._config_path.is_file():
            return {"enabled": True, "status": "unavailable"}
        if self._voice_loader is None and importlib.util.find_spec("piper") is None:
            return {"enabled": True, "status": "unavailable"}
        return {"enabled": True, "status": "ready" if self._voice is not None else "configured"}

    def _load_voice(self) -> Any:
        if self._voice is not None:
            return self._voice
        if not self.settings.tts_enabled:
            raise TTSUnavailable("tts disabled")
        if not self.settings.tts_model_path.is_file() or not self._config_path.is_file():
            raise TTSUnavailable("voice model unavailable")
        try:
            if self._voice_loader is not None:
                self._voice = self._voice_loader(self.settings.tts_model_path)
            else:
                from piper import PiperVoice

                self._voice = PiperVoice.load(str(self.settings.tts_model_path), use_cuda=False)
        except (ImportError, ModuleNotFoundError) as exc:
            raise TTSUnavailable("piper runtime unavailable") from exc
        except TTSUnavailable:
            raise
        except Exception as exc:
            self._failed = True
            raise TTSSynthesisFailed("voice load failed") from exc
        return self._voice

    def synthesize(self, text: str) -> bytes:
        if not self._lock.acquire(blocking=False):
            raise TTSBusy("synthesis already running")
        try:
            voice = self._load_voice()
            output = BytesIO()
            try:
                with wave.open(output, "wb") as wav_file:
                    voice.synthesize_wav(text, wav_file)
            except Exception as exc:
                self._failed = True
                raise TTSSynthesisFailed("synthesis failed") from exc
            audio = output.getvalue()
            if len(audio) < 44 or not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
                self._failed = True
                raise TTSSynthesisFailed("invalid wav output")
            return audio
        finally:
            self._lock.release()
