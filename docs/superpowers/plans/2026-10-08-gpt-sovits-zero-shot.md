# Luna GPT-SoVITS Zero-Shot Audition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce two fully local GPT-SoVITS V5 Turbo zero-shot Chinese auditions from one automatically selected clean reference segment while preserving Luna's live Piper voice.

**Architecture:** A versioned preprocessing CLI decodes the source, detects candidate utterances, validates signal quality, and uses local Faster-Whisper metadata to choose a reference. GPT-SoVITS lives in an isolated runtime with a minimal official model snapshot; a local-only inference wrapper generates two fixed WAVs and an atomic manifest.

**Tech Stack:** Python, FFmpeg/ffprobe, faster-whisper, GPT-SoVITS V5 Turbo, PyTorch CPU, Hugging Face Hub, pytest

**Spec:** `docs/superpowers/specs/2026-10-08-gpt-sovits-zero-shot-design-v1.9.0.md`

## Global Constraints

- Pin GPT-SoVITS source to `e7cd61ec3dcc9a34089f50ed169e3103a16ad675`.
- Never overwrite the source MP3 or modify Piper, Kokoro, backend, or frontend runtime configuration.
- Keep source, reference, models, and generated speech local; no cloud inference or upload.
- Use a 4–12 second 32 kHz mono PCM16 reference selected from original decoded audio.
- Generate exactly the two approved Chinese test sentences at speed 1.0.
- Bind any temporary inference service to `127.0.0.1` and terminate it after generation.
- Do not continue automatically if no clean reference satisfies the fixed thresholds.

## Review Focus

- Source filename contains spaces and Chinese punctuation: all subprocess calls use argument arrays, never shell interpolation (Task 1 test).
- Silence detection produces overlapping or out-of-range cuts: normalize, clamp, and reject invalid ranges before extraction (Task 1 test).
- Whisper returns blank text or low confidence: candidate is rejected and cannot become the reference (Task 1 test).
- A model file is partially downloaded: verify expected size and loadability before inference (Task 2 verification).
- Inference writes an empty, clipped, or malformed WAV: do not publish a success manifest (Task 3 test).

---

### Task 1: Reference Segment Selection

**Files:**
- Create: `tools/gpt_sovits_reference.py`
- Create: `backend/tests/test_gpt_sovits_reference.py`
- Create at runtime: `auditions/gpt-sovits-v1.9.0/reference.wav`
- Create at runtime: `auditions/gpt-sovits-v1.9.0/reference.json`

**Interfaces:**
- Consumes: source MP3, ffprobe/FFmpeg output, local Faster-Whisper transcription records.
- Produces: `parse_silence_intervals(text: str, duration: float) -> list[TimeRange]`, `rank_candidates(candidates: Sequence[Candidate]) -> list[Candidate]`, and a validated reference WAV/JSON pair.

- [ ] **Step 1: Write failing tests for interval parsing, clamping, ranking, blank transcription, and Unicode paths**
- [ ] **Step 2: Run `python -m pytest backend/tests/test_gpt_sovits_reference.py -v` and verify failure**
- [ ] **Step 3: Implement typed parsing, signal metrics, transcription scoring, extraction, and atomic metadata writing**
- [ ] **Step 4: Run the focused tests and verify PASS**
- [ ] **Step 5: Run the CLI against the supplied MP3 and verify a 4–12 second reference or emit up to three candidate choices**

### Task 2: Isolated GPT-SoVITS V5 Runtime

**Files:**
- Create at runtime: `runtime/gpt-sovits/repo/`
- Create at runtime: `runtime/gpt-sovits/.venv/`
- Create at runtime: `models/tts/gpt-sovits/`
- Create: `models/tts/gpt-sovits/SOURCE.md`

**Interfaces:**
- Consumes: official Git repository, official Hugging Face model repository, system FFmpeg.
- Produces: pinned source, isolated Python executable, and the complete minimal model inventory needed for CPU V5 Turbo inference.

- [ ] **Step 1: Clone the official repository and detach at the pinned commit**
- [ ] **Step 2: Create an isolated environment and install the repository's CPU-compatible requirements**
- [ ] **Step 3: Download only the documented HuBERT, RoBERTa, GPT, V5 Turbo, vocoder, and required Chinese frontend assets**
- [ ] **Step 4: Verify every expected file exists, has a nonzero expected size, and loads without network fallback**
- [ ] **Step 5: Record source/model revisions, dependency versions, sizes, and licenses**

### Task 3: Local Zero-Shot Synthesis

**Files:**
- Create: `tools/gpt_sovits_audition.py`
- Create: `backend/tests/test_gpt_sovits_audition.py`
- Create at runtime: `auditions/gpt-sovits-v1.9.0/clone-1.wav`
- Create at runtime: `auditions/gpt-sovits-v1.9.0/clone-2.wav`
- Create at runtime: `auditions/gpt-sovits-v1.9.0/manifest.json`

**Interfaces:**
- Consumes: validated reference WAV/JSON and isolated GPT-SoVITS configuration.
- Produces: `validate_clone(path: Path) -> AudioMetrics`, an atomic two-sample manifest, and nonzero CLI exit on any synthesis or validation failure.

- [ ] **Step 1: Write failing tests for malformed WAV, clipping, missing reference text, partial output, and atomic manifest behavior**
- [ ] **Step 2: Run focused tests and verify failure**
- [ ] **Step 3: Implement the local V5 Turbo inference adapter and output validation**
- [ ] **Step 4: Run focused tests and verify PASS**
- [ ] **Step 5: Generate both approved sentences and verify WAV metrics and hashes**

### Task 4: Regression and Handoff

**Files:**
- Create: `quality/luna-v1.9.0-gpt-sovits-zero-shot-verification.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1 reference metadata, Task 2 runtime inventory, Task 3 manifest, existing project tests/health endpoint.
- Produces: evidence that the audition works locally and current live services remain unchanged.

- [ ] **Step 1: Re-run inference with network disabled and record runtime**
- [ ] **Step 2: Run backend and frontend regression tests plus frontend production build**
- [ ] **Step 3: Verify ports 5173/8765 and health status; confirm `PiperTTSService` remains live**
- [ ] **Step 4: Write the verification report and README usage section**
- [ ] **Step 5: Present reference, clone-1, and clone-2 for the user's only required decision: accept the cloned timbre or reject it**
