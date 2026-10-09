# GPT-SoVITS Failure Recovery v2.0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover a failed CUDA GPT-SoVITS stream from the beginning on CPU/Piper without exposing a generic interruption when local recovery succeeds.

**Architecture:** Extend the existing `AudioFragment` stream with a reset marker. The backend retries once after a post-fragment primary failure and emits `reset` before the replacement stream; the frontend clears active playback resources and continues consuming the same NDJSON response.

**Tech Stack:** FastAPI, Python generators, NDJSON, React 18, TypeScript, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-gpt-sovits-failure-recovery-design-v2.0.1.md`

## Global Constraints

- Never mix CUDA, CPU GPT-SoVITS, and Piper audio without a reset boundary.
- At most one recovery chain per request.
- Text, paths, reference audio and payloads remain absent from health and diagnostics.
- Existing streaming limits and cancellation behavior remain unchanged.

## Tasks

### Task 1: Backend recovery transaction

**Files:** `backend/app/gpt_sovits.py`, `backend/app/main.py`, `backend/tests/test_gpt_sovits_service.py`, `backend/tests/test_tts.py`

- [x] Add `restart` metadata to `AudioFragment` and emit a sanitized `reset` NDJSON event when a replacement stream starts.
- [x] Retry post-fragment CUDA failure from CPU, then Piper if CPU fails; preserve ordered indices for each replacement stream.
- [x] Clear `last_error` after a complete primary or fallback success and prevent repeated provider loops.
- [x] Test pre-fragment fallback, post-fragment reset, CPU/Piper recovery, cancellation, and completed status.

### Task 2: Frontend reset-aware playback

**Files:** `frontend/src/api/client.ts`, `frontend/src/hooks/useSpeechPlayback.ts`, `frontend/src/api/client.test.ts`, `frontend/src/hooks/useSpeechPlayback.test.tsx`

- [x] Parse the reset event and restart expected fragment index without aborting the request.
- [x] Stop current audio, revoke queued Object URLs, and continue playback from the replacement stream.
- [x] Test reset ordering, URL cleanup, completed playback and abort behavior.

### Task 3: Verification and handoff

**Files:** `README.md`, `backend/README.md`, `quality/luna-v2.0.1-recovery-verification.md`

- [x] Run backend and frontend tests plus production build.
- [x] Run a forced post-fragment recovery smoke test and a normal CUDA stream.
- [x] Restart services and verify health reports ready with cleared error state.
- [x] Document the recovery behavior and known latency trade-off.
