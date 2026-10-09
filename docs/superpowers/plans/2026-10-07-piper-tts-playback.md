# Piper TTS Playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install the official Piper runtime and add private, CPU-only Chinese speech synthesis with automatic playback, per-message replay/stop, and a global auto-read switch.

**Architecture:** FastAPI owns a lazily loaded in-process `PiperTTSService` and exposes one authenticated WAV endpoint. React requests speech only after a complete assistant message exists, caches WAV blobs only for the current page session, and owns a single `HTMLAudioElement` so new playback always interrupts old playback.

**Tech Stack:** Python 3.12, FastAPI, `piper-tts==1.8.0`, Piper `zh_CN-huayan-medium`, React 18, TypeScript 5.6, Vite 5.

**Spec:** `docs/superpowers/specs/2026-10-07-local-ai-companion-design-v1.6.0.md`

## Global Constraints

- Bind every service to loopback; no new LAN or public listener.
- TTS must use CPU and must not replace `onnxruntime` with a GPU package.
- Accept 1–500 trimmed characters and never log source text.
- Return WAV bytes from memory with `Cache-Control: no-store`; never write synthesized audio to disk or the database.
- Require the existing session token and allowed Origin for synthesis.
- Only one synthesis may run; reject overlap with `TTS_BUSY` rather than queue indefinitely.
- Voice failure must never alter, delete, regenerate, or upload the text response.
- The workspace has no Git metadata, so commit steps are documented checkpoints and will be reported as skipped unless a repository appears.

## Review Focus

- Whitespace-only and 501-character inputs must produce the specified 422 errors without invoking Piper.
- A simultaneous second synthesis must receive `TTS_BUSY` and must not enter an unbounded queue.
- Missing package, model, or config must keep `/health` and text chat available while speech returns `TTS_UNAVAILABLE`.
- Browser autoplay rejection must leave a usable manual play button and preserve the assistant text.
- Blob URLs and active audio must be stopped and revoked on replacement and unmount.

---

### Task 1: Piper Runtime Boundary and Configuration

**Files:**
- Create: `backend/app/tts.py`
- Modify: `backend/app/config.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_tts.py`

**Interfaces:**
- Consumes: `Settings` and the local `.onnx` model/config pair.
- Produces: `TTSService.status() -> dict[str, Any]`, `TTSService.synthesize(text: str) -> bytes`, and typed `TTSUnavailable`, `TTSBusy`, `TTSSynthesisFailed` exceptions.

- [ ] **Step 1: Write failing service tests**

Add tests for configured status, missing model, valid in-memory RIFF/WAVE output through an injected fake Piper voice, exception mapping, and non-blocking lock contention.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `python -m pytest tests/test_tts.py -v` from `backend`.

Expected: FAIL because `app.tts` and TTS settings do not exist.

- [ ] **Step 3: Add pinned runtime and settings**

Add `piper-tts==1.8.0` to `backend/requirements.txt`. Add `tts_enabled: bool`, `tts_model_path: Path`, and `tts_max_chars: int = 500` to `Settings`, with the model default resolved from the workspace and overridable by `LOCAL_AI_MATE_TTS_MODEL_PATH`.

- [ ] **Step 4: Implement the isolated service**

Implement `PiperTTSService(settings: Settings)` in `backend/app/tts.py`. Lazy-import `PiperVoice`, lazy-load the model once, synthesize to `io.BytesIO` through `wave.open`, hold a non-blocking `threading.Lock`, and expose only `configured`, `ready`, `unavailable`, or `failed` status without paths or text.

- [ ] **Step 5: Run service tests**

Run: `python -m pytest tests/test_tts.py -v` from `backend`.

Expected: PASS.

- [ ] **Step 6: Checkpoint**

Record that the runtime boundary tests pass. Git commit is skipped because `D:\A` is not a Git repository.

### Task 2: Authenticated WAV API and Health State

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_api.py`
- Test: `backend/tests/test_tts.py`

**Interfaces:**
- Consumes: `TTSService.status()` and `TTSService.synthesize(text)` from Task 1.
- Produces: `POST /api/v1/tts/synthesize` with `{text: string}` and `audio/wav`; `/api/v1/health.data.tts`.

- [ ] **Step 1: Write failing endpoint tests**

Test valid WAV output and `Cache-Control: no-store`; missing token/wrong Origin; `TTS_EMPTY_TEXT`; `TTS_TEXT_TOO_LONG`; `TTS_BUSY`; `TTS_UNAVAILABLE`; `TTS_SYNTHESIS_FAILED`; and health behavior while TTS is unavailable.

- [ ] **Step 2: Run focused endpoint tests and verify failure**

Run: `python -m pytest tests/test_tts.py tests/test_api.py -v` from `backend`.

Expected: FAIL because the endpoint and health field do not exist.

- [ ] **Step 3: Add request schema and injectable app service**

Add `TTSSynthesizeRequest` with `text: str`. Extend `create_app(settings: Settings | None = None, tts_service: TTSService | None = None) -> FastAPI` so tests can inject a fake without loading the real model.

- [ ] **Step 4: Implement endpoint and stable errors**

Implement the async endpoint with existing `require_session`, manual trimmed-length validation, `asyncio.to_thread`, exact error codes/statuses, `Response(content=wav, media_type="audio/wav")`, and no-store headers. Add sanitized TTS status to health.

- [ ] **Step 5: Run the complete backend suite**

Run: `python -m pytest tests -v` and `python -m pytest ../quality/tests -v` from `backend`.

Expected: all tests PASS and existing privacy/network assertions remain unchanged.

- [ ] **Step 6: Checkpoint**

Record backend API and regression results. Git commit is skipped because the workspace has no repository metadata.

### Task 3: Browser Playback Controller and Chat Controls

**Files:**
- Create: `frontend/src/hooks/useSpeechPlayback.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `api.synthesizeSpeech(text: string) -> Promise<Blob>` and complete assistant `Message` objects.
- Produces: `autoRead`, `activeMessageId`, `voiceNotice`, `play(message)`, `stop()`, and `setAutoRead(enabled)` for the chat UI.

- [ ] **Step 1: Add the binary API client**

Implement `synthesizeSpeech` using the existing base URL, session token, same-origin credentials, and JSON error-envelope parsing before returning `response.blob()`.

- [ ] **Step 2: Implement one-audio playback state**

Create `useSpeechPlayback`. Keep one `HTMLAudioElement`, cache object URLs by message ID for the current page, stop old audio before new playback, handle `ended` and `play()` rejection, revoke URLs during cleanup, and stop immediately when auto-read is disabled.

- [ ] **Step 3: Wire automatic playback after message completion**

After `api.getMessages`, identify the newest assistant message and call `play` only when auto-read is enabled. A synthesis/playback error sets a voice notice without changing the conversation or main send error.

- [ ] **Step 4: Add accessible controls**

Add the default-on “自动朗读” switch near the chat heading and a “播放/停止/正在朗读” control to every assistant message. Keep “按住说话” disabled and label it as a later input feature.

- [ ] **Step 5: Add focused styling and build**

Add compact heading/audio action styles, including mobile wrapping and visible disabled/focus states.

Run: `npm run build` from `frontend`.

Expected: TypeScript and Vite build succeed.

- [ ] **Step 6: Checkpoint**

Record the production build result. Git commit is skipped because the workspace has no repository metadata.

### Task 4: Install, Real-Model Smoke Test, Documentation, and End-to-End Verification

**Files:**
- Modify: `README.md`
- Modify: `backend/README.md`
- Modify: `quality/ACCEPTANCE_CHECKLIST.md`

**Interfaces:**
- Consumes: all runtime, API, and UI interfaces from Tasks 1–3.
- Produces: a reproducible local start procedure and verified browser audio path.

- [ ] **Step 1: Install the pinned official runtime**

Run: `python -m pip install -r backend/requirements.txt` with the active backend interpreter.

Expected: `piper-tts 1.8.0` installs from the official PyPI release and imports successfully.

- [ ] **Step 2: Run a direct real-model synthesis**

Load `models/tts/piper/zh_CN-huayan-medium/zh_CN-huayan-medium.onnx`, synthesize “你好，我在这里。”, keep bytes in memory, and verify RIFF/WAVE metadata and non-zero frames.

- [ ] **Step 3: Update operating documentation**

Document the dependency, model path override, CPU-only behavior, endpoint, auto-read controls, licensing caveat, and text-only fallback. Remove the obsolete claim that real voice is unimplemented while retaining STT as future work.

- [ ] **Step 4: Restart the local backend safely**

Stop only the known backend process bound to `127.0.0.1:8765`, restart it with the existing Ollama/session/origin settings plus TTS enabled, and leave the frontend on `127.0.0.1:5173`.

- [ ] **Step 5: Verify the real HTTP boundary**

Check `/health` for TTS configured/ready status, synthesize a Chinese phrase through the authenticated endpoint, verify the WAV response, and confirm all listeners remain loopback-only.

- [ ] **Step 6: Verify browser behavior**

Send one message in the local app and verify automatic playback. Then verify stop, replay, global auto-read off/on, and text-chat survival after a forced TTS failure. Do not enable microphone access.

- [ ] **Step 7: Run final regressions**

Run backend tests, quality tests, frontend build, and a local-model chat smoke test.

Expected: all automated checks pass; chat source remains `ollama-local`; TTS produces audible local WAV; no public listener or TTS network request appears.

- [ ] **Step 8: Final checkpoint**

Report installed version, model/hash, changed files, test results, live URLs, license warning, and any browser autoplay limitation. Git commit remains skipped unless repository metadata becomes available.
