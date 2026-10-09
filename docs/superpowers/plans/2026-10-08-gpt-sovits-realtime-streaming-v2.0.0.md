# GPT-SoVITS Realtime Streaming v2.0.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the accepted Luna GPT-SoVITS voice the default streamed playback voice, using CUDA when the RTX 3050 is stable and automatically falling back to CPU GPT-SoVITS and then Piper.

**Architecture:** A persistent GPT-SoVITS worker runs in an isolated Python environment and communicates with the FastAPI backend over versioned NDJSON on stdio. The backend exposes an authenticated NDJSON audio stream, applies pre-first-fragment fallback, and keeps Piper compatibility. The React client incrementally decodes WAV fragments and plays them through a cancellable ordered queue.

**Tech Stack:** Python 3.12, GPT-SoVITS V5 Turbo, PyTorch 2.7.1 CUDA 12.8, FastAPI StreamingResponse, NDJSON, React 18, TypeScript, Vitest, pytest

**Spec:** `docs/superpowers/specs/2026-10-08-gpt-sovits-realtime-streaming-design-v2.0.0.md`

**Implementation status (2026-10-09):** Complete. CUDA was accepted after moving the text-only BERT extractor to CPU; 9/9 benchmark runs, 77 backend tests, 29 frontend tests and the production build passed. See `quality/luna-v2.0.0-realtime-streaming-verification.md`.

## Global Constraints

- v1.9.1 `natural-restraint-c1` parameters and reference audio remain immutable.
- CUDA lives only in `runtime/gpt-sovits/.venv-cuda`; the working CPU environment remains untouched.
- Worker transport uses stdio only and binds no network port.
- Fallback order is CUDA GPT-SoVITS → CPU GPT-SoVITS → Piper, and fallback is allowed only before the first fragment is emitted.
- `POST /api/v1/tts/synthesize` remains compatible; the new stream route is `POST /api/v1/tts/stream`.
- No text, reference transcript, local path, audio payload or process command may appear in health data or ordinary logs.
- Cache is memory-only: 8 completed items and 32 MiB maximum, with no disk or browser persistence.
- The workspace is not a Git repository, so commit steps are replaced by test-backed file checkpoints.

## Review Focus

- Worker stdout contamination must not corrupt protocol frames when GPT-SoVITS prints progress or warnings.
- A CUDA OOM before the first fragment must retry once on CPU; an error after a fragment must never switch voices.
- Client cancellation during generation or playback must stop audio, abort Fetch and revoke every Object URL.
- Malformed, oversized, out-of-order or wrong-request worker messages must fail closed without leaking their payload.
- A repeated completed message may use memory cache, while cancelled and partial streams must never be cached.

---

### Task 1: Define and test the worker protocol

**Files:**
- Create: `tools/gpt_sovits_worker.py`
- Create: `backend/tests/test_gpt_sovits_worker.py`

**Interfaces:**
- Consumes: v1.9.1 profile, reference WAV/JSON and centralized model paths.
- Produces: `PROTOCOL_VERSION = 1`, `WorkerCommand`, `WorkerEvent`, `encode_event(event) -> str`, `parse_command(line) -> WorkerCommand`, and `run_worker(config) -> int`.

- [ ] **Step 1: Write failing protocol tests**

Test valid ping/synthesize/cancel/shutdown commands; reject unknown versions, missing request IDs, texts over 500 characters and lines over 16 KiB. Test that fake inference emits ordered fragment indices followed by one completed event, and cancellation stops at a fragment boundary.

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `python -m pytest tests/test_gpt_sovits_worker.py -q`

Expected: FAIL because the worker module does not exist.

- [ ] **Step 3: Implement protocol and lazy model loading**

Keep Torch/GPT-SoVITS imports inside the model factory. Redirect model stdout to stderr. Accept device/profile/model/reference configuration only from trusted startup arguments, never from request JSON. Wrap each NumPy fragment as mono PCM16 WAV and Base64-encode it in a fragment event.

- [ ] **Step 4: Implement cancellation and graceful shutdown**

Use a reader thread for stdin commands and a single inference loop. `cancel` marks the current request and is checked between yielded fragments. `shutdown` stops accepting work, finishes cleanup and exits 0.

- [ ] **Step 5: Run focused tests**

Expected: all worker tests PASS without importing Torch.

### Task 2: Add backend worker supervision and fallback

**Files:**
- Create: `backend/app/gpt_sovits.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/tts.py`
- Create: `backend/tests/test_gpt_sovits_service.py`

**Interfaces:**
- Consumes: worker protocol version 1 and existing `PiperTTSService`.
- Produces: `AudioFragment`, `StreamingTTSService`, `GPTSoVITSWorkerService`, `FallbackStreamingTTSService`, `warmup()`, `stream(text)`, `cancel(request_id)`, `status()`, and `close()`.

- [ ] **Step 1: Write failing service tests**

Use fake workers to test warmup, ordered fragments, one-request concurrency, timeout, malformed frames, worker exit, CUDA-to-CPU retry before first fragment, CPU-to-Piper fallback, and no fallback after the first fragment.

- [ ] **Step 2: Add exact settings**

Implement the environment variables and defaults from the spec. Resolve relative paths against the project root; reject paths outside the expected project runtime/tools/models/auditions locations.

- [ ] **Step 3: Implement subprocess supervision**

Start workers with `stdin/stdout/stderr` pipes, bounded line reads and a dedicated stderr drain that records only safe summaries. Match events by request ID, enforce sequence numbers, terminate on protocol violations, and close children during application shutdown.

- [ ] **Step 4: Implement fallback and bounded memory cache**

Fallback only when no fragment has been yielded. Cache only fully completed streams using SHA-256 keys, LRU eviction at 8 items or 32 MiB, and clear on close.

- [ ] **Step 5: Run service tests**

Expected: all fallback, privacy, cache and cancellation tests PASS.

### Task 3: Expose authenticated NDJSON streaming

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_tts.py`
- Modify: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `StreamingTTSService.stream(text) -> Iterator[AudioFragment]`.
- Produces: `POST /api/v1/tts/stream` with `application/x-ndjson` events `started`, `fragment`, `completed`, and `error`.

- [ ] **Step 1: Write failing API tests**

Assert session/Origin enforcement, 500-character limit, no-store, ordered NDJSON, engine/device metadata, 4 MiB fragment cap, 32 MiB request cap, 429 busy response before headers, disconnect cancellation, and sanitized health state.

- [ ] **Step 2: Integrate lifespan warmup and shutdown**

Start `tts.warmup()` in a background task during lifespan when supported; cancel the task and call `tts.close()` on shutdown. Tests with fake legacy TTS services must remain compatible.

- [ ] **Step 3: Implement the stream route**

Resolve the first fragment before returning `StreamingResponse`, allowing clean 429/503 responses and fallback before headers. Stream remaining events as NDJSON, catch post-header errors as sanitized error events, and cancel on disconnect/finalization.

- [ ] **Step 4: Preserve the legacy route**

Keep `/tts/synthesize` behavior and existing tests unchanged so Piper remains a reliable single-WAV compatibility path.

- [ ] **Step 5: Run backend TTS/API tests**

Expected: all old and new tests PASS.

### Task 4: Implement the frontend fragment queue

**Files:**
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/api/client.test.ts`
- Modify: `frontend/src/hooks/useSpeechPlayback.ts`
- Modify: `frontend/src/hooks/useSpeechPlayback.test.tsx`
- Modify: `frontend/src/types.ts`

**Interfaces:**
- Consumes: NDJSON stream protocol.
- Produces: `api.streamSpeech(text, signal, onEvent)`, a cancellable ordered playback queue, and extended sanitized TTS health fields.

- [ ] **Step 1: Write failing API/parser tests**

Test split JSON lines across network chunks, multiple lines in one chunk, invalid Base64, error events, missing body and abort behavior.

- [ ] **Step 2: Write failing playback tests**

Test first-fragment immediate playback, ordered transition after `ended`, receiving while playing, stop/replacement, autoplay block, stream interruption and revocation of every URL.

- [ ] **Step 3: Implement `streamSpeech`**

Read with `ReadableStreamDefaultReader`, incrementally decode UTF-8, enforce event shape/order, convert Base64 WAV to Blob, and surface stable `ApiError` values.

- [ ] **Step 4: Implement queued playback**

Use one AbortController per play sequence. Start the first fragment immediately, enqueue later fragments, settle only after completed plus final audio ended, and clear all resources on every terminal path.

- [ ] **Step 5: Run frontend tests and build**

Run `npm test -- --run` and `npm run build`; both must pass.

### Task 5: Install and benchmark the isolated CUDA runtime

**Files:**
- Create: `runtime/gpt-sovits/.venv-cuda/`
- Create: `runtime/gpt-sovits/CUDA_SOURCE.md`
- Create: `quality/luna-v2.0.0-gpu-benchmark.json`

**Interfaces:**
- Consumes: official PyTorch CUDA 12.8 wheel index, pinned GPT-SoVITS repo/models, and worker from Task 1.
- Produces: an independently usable CUDA interpreter or a recorded, automatically selected CPU fallback.

- [ ] **Step 1: Create the Python 3.12 virtual environment**

Do not modify `.venv`. Record interpreter and pip versions.

- [ ] **Step 2: Install pinned CUDA Torch pair and minimal inference dependencies**

Install `torch==2.7.1+cu128` and `torchaudio==2.7.1+cu128` from the official PyTorch index. Reuse the CPU environment's known compatible package pins where possible; omit Flash Attention.

- [ ] **Step 3: Run a load-only CUDA probe**

Record GPU name, driver, Torch/CUDA versions, startup time, allocated/reserved/peak VRAM and sanitized failure code. OOM or timeout selects CPU without retry loops.

- [ ] **Step 4: Run three repeated streaming benchmarks**

Generate all v1.9.1 texts three times. Validate every fragment WAV and record per-run TTFF, total time, real-time factor and peak VRAM. CUDA is accepted only if all runs succeed and leave a safety margin; otherwise mark CPU selected.

- [ ] **Step 5: Document exact package sources and selection**

Write `CUDA_SOURCE.md` and the machine-readable benchmark without local secrets or conversation text beyond the fixed public audition phrases.

### Task 6: End-to-end integration and handoff

**Files:**
- Modify: `README.md`
- Modify: `backend/README.md`
- Modify: `quality/README.md`
- Create: `quality/luna-v2.0.0-realtime-streaming-verification.md`

**Interfaces:**
- Consumes: Tasks 1–5 and the live local application.
- Produces: verified startup, default streamed Luna voice, documented fallback and reproducible local operations.

- [ ] **Step 1: Run complete regression suites**

Run backend `python -m pytest tests -q`, frontend `npm test -- --run`, and `npm run build`.

- [ ] **Step 2: Restart the local services with GPT-SoVITS enabled**

Keep both servers on `127.0.0.1`; verify Ollama, STT, streaming TTS and Piper fallback health without exposing filesystem paths.

- [ ] **Step 3: Perform real browser/API smoke tests**

Verify text appears before audio, first fragment plays, later fragments remain ordered, stop releases playback, replay uses memory cache, and forced primary failure reaches the correct fallback without mixed voices.

- [ ] **Step 4: Write verification and operations documentation**

Record actual selected device, TTFF and total latency, package versions, test results, fallback behavior, startup/shutdown commands, privacy properties and known limits.

- [ ] **Step 5: Present the completed runtime**

Report whether CUDA passed or CPU was selected automatically. Do not ask for a choice unless a new trade-off remains unresolved.
