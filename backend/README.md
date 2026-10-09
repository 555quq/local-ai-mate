# Local AI Mate backend MVP

Create the project-local environment with `python -m venv .venv`, install dependencies with `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`, then run locally with `.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765` from this directory.

The MVP uses Python's built-in SQLite driver so it can run without native database dependencies. It intentionally keeps all data local. For a production deployment, replace the connection factory with SQLCipher (`pysqlcipher3` or a supported SQLCipher build) and keep the encryption key protected by Windows DPAPI/Credential Manager as specified in the design. Plain SQLite is an MVP fallback, not encryption at rest.

`LOCAL_AI_MATE_DATA_DIR` can point to a local data directory. `LOCAL_AI_MATE_SESSION_TOKEN` supplies the launcher-issued loopback session token; when absent, a random token is generated at process start. The token is required in `X-Session-Token` for every mutating API call. The launcher must also set `LOCAL_AI_MATE_ALLOWED_ORIGIN` to the exact browser origin; writes reject missing or mismatched `Origin` headers.

## Local Piper speech output

Install the pinned dependencies in `.venv`. The backend uses `piper-tts==1.8.0` in-process and on CPU only. Its default voice is `../models/tts/piper/zh_CN-huayan-medium/zh_CN-huayan-medium.onnx`; set `LOCAL_AI_MATE_TTS_MODEL_PATH` to override it or `LOCAL_AI_MATE_TTS_ENABLED=0` to disable speech.

`POST /api/v1/tts/synthesize` accepts `{ "text": "..." }` after the same loopback session and Origin checks, and returns `audio/wav` with `Cache-Control: no-store`. Text is limited to 500 characters, only one synthesis runs at a time, and generated audio is kept in memory. `/api/v1/health` reports only sanitized TTS state.

Piper is GPL-3.0-or-later. The bundled huayan model card lists its source dataset license as unknown, so confirm licensing before public distribution or commercial use.

## GPT-SoVITS streaming speech output

`POST /api/v1/tts/stream` is the primary speech endpoint. It emits newline-delimited JSON events (`started`, ordered `fragment`, and `completed`), with each fragment carrying an independent Base64 WAV payload. The frontend can therefore play the first fragment while later fragments are still being generated. The legacy `/api/v1/tts/synthesize` endpoint remains available for compatibility.

GPT-SoVITS runs in isolated, persistent subprocesses rather than inside the API process. The provider order is CUDA, CPU GPT-SoVITS, then Piper. CUDA and CPU have separate fragment timeouts, completed streams are cached in bounded memory. If the primary provider fails after emitting a fragment, the service first synthesizes a complete CPU or Piper replacement, emits a `reset` event, and restarts fragment indexes at zero so the browser never mixes partial audio from two providers. If recovery also fails, the stream emits a sanitized `TTS_STREAM_INTERRUPTED` error; text responses remain independent.

Relevant settings are `LOCAL_AI_MATE_GSV_CUDA_PYTHON`, `LOCAL_AI_MATE_GSV_CPU_PYTHON`, `LOCAL_AI_MATE_GSV_PROFILE`, `LOCAL_AI_MATE_GSV_FRAGMENT_TIMEOUT_SECONDS`, `LOCAL_AI_MATE_GSV_CPU_FRAGMENT_TIMEOUT_SECONDS`, `LOCAL_AI_MATE_GSV_CACHE_ITEMS`, and `LOCAL_AI_MATE_GSV_CACHE_BYTES`.

## Local SenseVoice speech input

The preferred engine is SenseVoiceSmall in the isolated `../runtime/sensevoice/.venv` environment, running on CPU through `../tools/sensevoice_worker.py`. It never opens a network port and automatically falls back to faster-whisper (`device="cpu"`, `compute_type="int8"`) if the worker or model is unavailable. Set `LOCAL_AI_MATE_STT_ENGINE=faster-whisper` to use the fallback directly, or `LOCAL_AI_MATE_STT_ENABLED=0` to disable transcription. See `../runtime/sensevoice/README.md` for the installation and offline smoke test.

`av>=11,<16` is intentional: faster-whisper 1.2.1 passes `metadata_errors` to `av.open`, while PyAV 19 removed that argument. Keep the constraint until the runtime call is updated upstream and revalidated with real WAV/WebM audio.

`POST /api/v1/stt/transcribe` accepts a raw `audio/webm`, `audio/ogg`, or `audio/wav` body after the loopback session and Origin checks. The request is limited to 8 MiB, only one transcription runs at a time, and audio is decoded from memory rather than written to a temporary file. The response contains only text, language, and duration; `/api/v1/health` reports a sanitized STT state without paths or transcripts.

## Local Qwen3 text model

Ollama is loopback-only. The default model is `qwen3:4b-instruct`, with thinking display disabled and a 60-second cold-start timeout so the first request can load the model while GPT-SoVITS is warming. Set `LOCAL_AI_MATE_OLLAMA_MODEL` to `qwen3:1.7b` for the smaller rollback model; all failures retain the deterministic local reply fallback.
