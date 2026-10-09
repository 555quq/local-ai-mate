from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Settings:
    """Runtime-only settings. The HTTP server must be bound to loopback by its launcher."""

    data_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("LOCAL_AI_MATE_DATA_DIR", Path.cwd() / "LocalAIMate")
        )
    )
    session_token: str = field(
        default_factory=lambda: os.environ.get("LOCAL_AI_MATE_SESSION_TOKEN", secrets.token_urlsafe(32))
    )
    # The launcher must override this with the exact random-port frontend origin.
    # Keeping a concrete default makes accidental open-origin development servers fail closed.
    allowed_origin: str = field(
        default_factory=lambda: os.environ.get("LOCAL_AI_MATE_ALLOWED_ORIGIN", "http://127.0.0.1:8765")
    )
    max_message_chars: int = 8_000
    cloud_consent_ttl_seconds: int = 300
    # Ollama is opt-in.  The adapter also rejects every non-loopback endpoint so
    # this setting cannot accidentally turn a local-only installation into a
    # cloud client.
    ollama_enabled: bool = field(
        default_factory=lambda: os.environ.get("LOCAL_AI_MATE_OLLAMA_ENABLED", "0") == "1"
    )
    ollama_base_url: str = field(
        default_factory=lambda: os.environ.get("LOCAL_AI_MATE_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    )
    ollama_model: str = field(
        default_factory=lambda: os.environ.get("LOCAL_AI_MATE_OLLAMA_MODEL", "qwen3:4b-instruct")
    )
    ollama_timeout_seconds: float = field(
        default_factory=lambda: float(os.environ.get("LOCAL_AI_MATE_OLLAMA_TIMEOUT_SECONDS", "60"))
    )
    ollama_num_predict: int = field(
        default_factory=lambda: int(os.environ.get("LOCAL_AI_MATE_OLLAMA_NUM_PREDICT", "128"))
    )
    ollama_num_ctx: int = field(
        default_factory=lambda: int(os.environ.get("LOCAL_AI_MATE_OLLAMA_NUM_CTX", "2048"))
    )
    ollama_keep_alive: str = field(
        default_factory=lambda: os.environ.get("LOCAL_AI_MATE_OLLAMA_KEEP_ALIVE", "10m")
    )
    tts_enabled: bool = field(
        default_factory=lambda: os.environ.get("LOCAL_AI_MATE_TTS_ENABLED", "1") == "1"
    )
    tts_model_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "LOCAL_AI_MATE_TTS_MODEL_PATH",
                Path(__file__).resolve().parents[2]
                / "models"
                / "tts"
                / "piper"
                / "zh_CN-huayan-medium"
                / "zh_CN-huayan-medium.onnx",
            )
        )
    )
    tts_max_chars: int = 500
    tts_primary: str = field(
        default_factory=lambda: os.environ.get("LOCAL_AI_MATE_TTS_PRIMARY", "gpt-sovits")
    )
    gsv_cuda_python: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "LOCAL_AI_MATE_GSV_CUDA_PYTHON",
                Path(__file__).resolve().parents[2]
                / "runtime"
                / "gpt-sovits"
                / ".venv-cuda"
                / "Scripts"
                / "python.exe",
            )
        )
    )
    gsv_cpu_python: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "LOCAL_AI_MATE_GSV_CPU_PYTHON",
                Path(__file__).resolve().parents[2]
                / "runtime"
                / "gpt-sovits"
                / ".venv"
                / "Scripts"
                / "python.exe",
            )
        )
    )
    gsv_worker_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "LOCAL_AI_MATE_GSV_WORKER",
                Path(__file__).resolve().parents[2] / "tools" / "gpt_sovits_worker.py",
            )
        )
    )
    gsv_profile: str = field(
        default_factory=lambda: os.environ.get("LOCAL_AI_MATE_GSV_PROFILE", "v1.9.1")
    )
    gsv_warmup_timeout_seconds: float = field(
        default_factory=lambda: float(os.environ.get("LOCAL_AI_MATE_GSV_WARMUP_TIMEOUT_SECONDS", "90"))
    )
    gsv_fragment_timeout_seconds: float = field(
        default_factory=lambda: float(os.environ.get("LOCAL_AI_MATE_GSV_FRAGMENT_TIMEOUT_SECONDS", "30"))
    )
    gsv_cpu_fragment_timeout_seconds: float = field(
        default_factory=lambda: float(os.environ.get("LOCAL_AI_MATE_GSV_CPU_FRAGMENT_TIMEOUT_SECONDS", "120"))
    )
    gsv_cache_items: int = field(
        default_factory=lambda: int(os.environ.get("LOCAL_AI_MATE_GSV_CACHE_ITEMS", "8"))
    )
    gsv_cache_bytes: int = field(
        default_factory=lambda: int(os.environ.get("LOCAL_AI_MATE_GSV_CACHE_BYTES", "33554432"))
    )
    stt_enabled: bool = field(
        default_factory=lambda: os.environ.get("LOCAL_AI_MATE_STT_ENABLED", "1") == "1"
    )
    stt_engine: str = field(
        default_factory=lambda: os.environ.get("LOCAL_AI_MATE_STT_ENGINE", "sensevoice")
    )
    stt_model_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "LOCAL_AI_MATE_STT_MODEL_PATH",
                Path(__file__).resolve().parents[2] / "models" / "stt" / "faster-whisper-base",
            )
        )
    )
    sensevoice_model_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "LOCAL_AI_MATE_SENSEVOICE_MODEL_PATH",
                Path(__file__).resolve().parents[2] / "models" / "stt" / "sensevoice-small",
            )
        )
    )
    sensevoice_python: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "LOCAL_AI_MATE_SENSEVOICE_PYTHON",
                Path(__file__).resolve().parents[2]
                / "runtime"
                / "sensevoice"
                / ".venv"
                / "Scripts"
                / "python.exe",
            )
        )
    )
    sensevoice_worker_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "LOCAL_AI_MATE_SENSEVOICE_WORKER",
                Path(__file__).resolve().parents[2] / "tools" / "sensevoice_worker.py",
            )
        )
    )
    stt_max_audio_bytes: int = 8 * 1024 * 1024
    stt_max_duration_ms: int = 15_000

    @property
    def db_path(self) -> Path:
        return self.data_dir / "data" / "local_ai_mate.sqlite3"
