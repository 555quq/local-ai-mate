# Luna Kokoro Voice Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install an isolated local Kokoro Chinese TTS runtime and produce four verified, directly playable voice auditions without changing Luna's live Piper voice.

**Architecture:** Kokoro runs in its own virtual environment and reads a fully local model snapshot. A small versioned CLI deterministically selects four existing Mandarin female voices, synthesizes one fixed sentence, validates the WAV outputs, and writes a reproducible manifest; the live backend remains untouched.

**Tech Stack:** Python 3.12, Kokoro 0.8+, Misaki Chinese G2P, PyTorch, soundfile, Hugging Face Hub, pytest

**Spec:** `docs/superpowers/specs/2026-10-08-kokoro-voice-selection-design-v1.8.0.md`

## Global Constraints

- Preserve the current Piper runtime, model, backend route, and frontend playback behavior.
- Use `runtime/kokoro/.venv`; do not install Kokoro packages into the backend environment or global Python.
- Store the model at `models/tts/kokoro/Kokoro-82M-v1.1-zh` and use local files for synthesis.
- Use exactly the approved Chinese sentence for every sample.
- Produce exactly four unique `zf_*` voices as `K1.wav` through `K4.wav`.
- Outputs must be mono 24 kHz WAV and must pass decoding, non-empty, peak, and hash checks.
- Default to CPU inference and do not change any running service configuration.

## Review Focus

- Model snapshot is incomplete or lacks four `zf_*` voices: fail before synthesis and do not emit a complete manifest (Task 2 test).
- Voice filenames contain unrelated files or duplicate IDs: enumerate only valid unique `zf_*` resources in natural order (Task 1 test).
- A generated WAV is empty, stereo, the wrong sample rate, or clipped: validation fails with the exact offending path (Task 2 test).
- Synthesis stops after one candidate: temporary results remain diagnosable, but no success manifest is published (Task 2 test).
- Re-running into an existing output directory: replace only the four named audition files and manifest, leaving unrelated files intact (Task 2 test).

---

### Task 1: Deterministic Voice Selection

**Files:**
- Create: `tools/kokoro_audition.py`
- Create: `backend/tests/test_kokoro_audition.py`

**Interfaces:**
- Consumes: a model voice directory containing Kokoro voice resource filenames.
- Produces: `discover_mandarin_female_voices(voice_dir: Path) -> list[str]` and `select_four_voice_ids(voice_ids: Sequence[str]) -> list[str]`.

- [ ] **Step 1: Write failing discovery and selection tests**

Add tests asserting natural ordering of `zf_1`, `zf_2`, `zf_10`, rejection of unrelated/duplicate resources, deterministic four-position selection, and `ValueError` when fewer than four voices exist.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest backend/tests/test_kokoro_audition.py -v`  
Expected: FAIL because `tools.kokoro_audition` does not exist.

- [ ] **Step 3: Implement voice discovery and selection**

Implement the two typed functions in `tools/kokoro_audition.py`; accept `.pt` voice files, deduplicate by stem, natural-sort numeric suffixes, then choose indices `round(i * (n - 1) / 3)` for `i=0..3`.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest backend/tests/test_kokoro_audition.py -v`  
Expected: PASS for discovery and selection tests.

### Task 2: Audition Generation and Atomic Manifest

**Files:**
- Modify: `tools/kokoro_audition.py`
- Modify: `backend/tests/test_kokoro_audition.py`
- Create at runtime: `auditions/kokoro-v1.8.0/manifest.json`

**Interfaces:**
- Consumes: selected voice IDs, local Kokoro pipeline, fixed synthesis text, model and output paths.
- Produces: `validate_wav(path: Path) -> WavMetrics`, `build_manifest(samples: Sequence[SampleResult]) -> dict[str, object]`, and CLI exit status 0 only after all four files validate.

- [ ] **Step 1: Write failing WAV and manifest tests**

Add tests for valid mono 24 kHz audio; empty, stereo, wrong-rate, and clipped failures; four unique manifest records; atomic manifest absence after a simulated mid-batch exception; and preservation of unrelated output files on rerun.

- [ ] **Step 2: Run tests and verify the new cases fail**

Run: `python -m pytest backend/tests/test_kokoro_audition.py -v`  
Expected: FAIL because WAV validation and manifest interfaces are missing.

- [ ] **Step 3: Implement synthesis orchestration**

Add the typed result records, local-only Kokoro loading, sequential K1—K4 synthesis, PCM WAV writing, SHA-256 calculation, validation, temporary manifest writing, and atomic rename after complete success. Restrict cleanup to `K1.wav`—`K4.wav`, manifest, and this run's temporary files.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest backend/tests/test_kokoro_audition.py -v`  
Expected: PASS.

### Task 3: Isolated Runtime and Local Model Snapshot

**Files:**
- Create at runtime: `runtime/kokoro/.venv/`
- Create at runtime: `models/tts/kokoro/Kokoro-82M-v1.1-zh/`
- Create: `models/tts/kokoro/Kokoro-82M-v1.1-zh.SOURCE.md`

**Interfaces:**
- Consumes: Python 3.12 and official PyPI/Hugging Face packages.
- Produces: an isolated Python executable capable of importing `kokoro`, `misaki`, `soundfile`, and loading the local snapshot.

- [ ] **Step 1: Create the isolated virtual environment**

Run: `python -m venv runtime/kokoro/.venv`  
Expected: `runtime/kokoro/.venv/Scripts/python.exe` exists.

- [ ] **Step 2: Install pinned compatible dependencies**

Run the venv Python with `-m pip install "kokoro>=0.8.2,<0.10" "misaki[zh]>=0.8.2,<0.10" soundfile huggingface_hub pytest`.  
Expected: command exits 0 and imports succeed inside the venv.

- [ ] **Step 3: Download the official model snapshot**

Use `huggingface_hub.snapshot_download(repo_id="hexgrad/Kokoro-82M-v1.1-zh", local_dir=...)` with resume support.  
Expected: model config, weights, and at least four `zf_*` voice resources exist locally.

- [ ] **Step 4: Record model provenance**

Write repository URL, revision returned by the download, Apache-2.0 license, local path, and download date to the source note.

- [ ] **Step 5: Verify isolated imports and snapshot inventory**

Run the venv Python to import dependencies and call `discover_mandarin_female_voices`.  
Expected: imports pass and voice count is at least 4.

### Task 4: Generate and Verify Four Auditions

**Files:**
- Create at runtime: `auditions/kokoro-v1.8.0/K1.wav`
- Create at runtime: `auditions/kokoro-v1.8.0/K2.wav`
- Create at runtime: `auditions/kokoro-v1.8.0/K3.wav`
- Create at runtime: `auditions/kokoro-v1.8.0/K4.wav`
- Create at runtime: `auditions/kokoro-v1.8.0/manifest.json`
- Create: `quality/luna-v1.8.0-kokoro-audition-verification.md`

**Interfaces:**
- Consumes: Task 2 CLI and Task 3 isolated runtime/model snapshot.
- Produces: four playable audition assets plus a verification report that maps K1—K4 to exact voice IDs and metrics.

- [ ] **Step 1: Run the audition CLI**

Run the isolated Python with `tools/kokoro_audition.py --model-dir models/tts/kokoro/Kokoro-82M-v1.1-zh --output-dir auditions/kokoro-v1.8.0 --device cpu`.  
Expected: exit 0 and four WAV paths printed.

- [ ] **Step 2: Re-run to verify local repeatability**

Set Hugging Face offline mode and run the same command.  
Expected: exit 0 without network access; unrelated files in the output directory remain untouched.

- [ ] **Step 3: Run project regression tests**

Run: `python -m pytest backend/tests -q`  
Expected: all backend tests pass and live Piper configuration is unchanged.

- [ ] **Step 4: Write verification report**

Record dependency versions, model path/revision, selected voice IDs, per-file duration/peak/hash, first and offline rerun times, test results, and confirmation that the live backend still uses Piper.

- [ ] **Step 5: Present K1—K4 for user selection**

Render all four local WAV files in the final response and ask the user to choose K1, K2, K3, or K4; do not change the live voice before that selection.
