# GPT-SoVITS Natural Restraint Tuning v1.9.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate three reproducible GPT-SoVITS V5 Turbo auditions for Luna's C1 “natural restraint” style without changing the live Piper path or breaking v1.9.0 reproduction.

**Architecture:** Extend the existing offline audition tool with immutable versioned profiles. A profile supplies sample texts, filenames and inference parameters while the existing model loader, PCM writer and validator remain shared. The CLI selects a profile explicitly, then atomically publishes a versioned manifest only after every sample validates.

**Tech Stack:** Python 3.12, GPT-SoVITS V5 Turbo, PyTorch CPU, pytest, WAV PCM16, JSON, existing React/Vite frontend regression suite

**Spec:** `docs/superpowers/specs/2026-10-08-gpt-sovits-cool-restraint-tuning-design-v1.9.1.md`

## Global Constraints

- Use the existing reference at `auditions/gpt-sovits-v1.9.0/reference.wav` and its UTF-8 metadata without modifying either file.
- GPT-SoVITS source remains pinned to commit `e7cd61ec3dcc9a34089f50ed169e3103a16ad675`.
- Inference remains CPU, FP32, sequential, non-streaming and fully local.
- C1 parameters are exact: speed `0.96`, fragment interval `0.22`, temperature `0.50`, top-p `0.85`, top-k `5`, repetition penalty `1.35`, sample steps `4`, seed `20261008`, split method `cut5`.
- New output is confined to `auditions/gpt-sovits-v1.9.1`; v1.9.0 output remains reproducible and untouched.
- Do not change `/api/v1/tts/synthesize`, the frontend playback contract or Piper as the live default.
- No model, reference or generated audio may be uploaded; missing local models fail closed instead of downloading.

## Review Focus

- Selecting no profile must preserve the v1.9.0 texts, filenames and parameter values.
- Selecting v1.9.1 must emit exactly three ordered samples with `cool-1.wav` through `cool-3.wav`.
- A failed second or third sample must not leave a manifest that claims a complete run.
- An empty or malformed local reference metadata file must fail before the model runner is invoked.
- Manifest parameters must match the values actually passed to inference, so later backend integration cannot rely on stale metadata.

---

### Task 1: Add immutable audition profiles

**Files:**
- Modify: `tools/gpt_sovits_audition.py`
- Modify: `backend/tests/test_gpt_sovits_audition.py`

**Interfaces:**
- Consumes: existing `validate_clone(path: Path) -> AudioMetrics` and `_load_runner(...)` model initialization.
- Produces: `InferenceParameters`, `AuditionSample`, `AuditionProfile`, `BASELINE_PROFILE`, `COOL_RESTRAINT_PROFILE`, and `get_profile(profile_id: str) -> AuditionProfile`.

- [ ] **Step 1: Write failing profile tests**

Add `test_baseline_profile_preserves_v190_defaults`, `test_cool_restraint_profile_has_exact_c1_parameters`, and `test_unknown_profile_is_rejected`. Assert the exact global-constraint values, ordered texts, and filenames.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest tests/test_gpt_sovits_audition.py -q`

Expected: FAIL because the profile types and lookup function do not exist.

- [ ] **Step 3: Implement the profile types and constants**

Add frozen dataclasses in `tools/gpt_sovits_audition.py`. Keep v1.9.0 as the default profile and define v1.9.1 with:

- `cool-1.wav`: `你回来了。今天还顺利吗？`
- `cool-2.wav`: `累了就先休息。其他事情，明天再处理。`
- `cool-3.wav`: `嗯，我在。你慢慢说。`

`get_profile(profile_id: str) -> AuditionProfile` must accept only `v1.9.0` and `v1.9.1` and raise `ValueError` otherwise.

- [ ] **Step 4: Run the focused tests and verify success**

Run: `python -m pytest tests/test_gpt_sovits_audition.py -q`

Expected: PASS.

### Task 2: Drive inference and manifests from the selected profile

**Files:**
- Modify: `tools/gpt_sovits_audition.py`
- Modify: `backend/tests/test_gpt_sovits_audition.py`

**Interfaces:**
- Consumes: `AuditionProfile` from Task 1.
- Produces: `generate_clones(..., profile: AuditionProfile = BASELINE_PROFILE) -> Path`; CLI option `--profile {v1.9.0,v1.9.1}`; manifest fields `design_version`, `style_id`, `parameters`, and ordered `samples`.

- [ ] **Step 1: Write failing generation tests**

Extend the fake-runner tests to assert that v1.9.1 calls the runner three times in order, writes the exact filenames, records every inference parameter in the manifest, and leaves no manifest or temporary WAV when call two fails. Add empty, malformed and non-Chinese reference-metadata cases and assert the runner is never invoked. Retain the existing unrelated-file preservation assertion.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest tests/test_gpt_sovits_audition.py -q`

Expected: FAIL because `generate_clones` does not yet accept a profile.

- [ ] **Step 3: Parameterize the model runner**

Change `_load_runner(repo_dir: Path, model_dir: Path, parameters: InferenceParameters)` so its closure passes the profile's speed, fragment interval, temperature, top-p, top-k, repetition penalty, sample steps, seed and split method to `pipeline.run`.

- [ ] **Step 4: Parameterize generation and atomic cleanup**

Change `generate_clones` to iterate `profile.samples`, remove only files named by that profile, write each sample to a same-directory temporary WAV, validate it, then atomically replace its final filename. Publish `manifest.json` through the existing temporary-file replacement only after all samples succeed, and clean temporary WAVs in a `finally` path. Default the argument to `BASELINE_PROFILE` for backward compatibility.

- [ ] **Step 5: Add explicit CLI profile selection**

Add `--profile` with exact choices `v1.9.0` and `v1.9.1`, default `v1.9.0`, and pass `get_profile(args.profile)` to `generate_clones`.

- [ ] **Step 6: Run the focused tests and verify success**

Run: `python -m pytest tests/test_gpt_sovits_audition.py -q`

Expected: PASS with all baseline and C1 profile tests.

### Task 3: Generate and validate the v1.9.1 auditions

**Files:**
- Create: `auditions/gpt-sovits-v1.9.1/cool-1.wav`
- Create: `auditions/gpt-sovits-v1.9.1/cool-2.wav`
- Create: `auditions/gpt-sovits-v1.9.1/cool-3.wav`
- Create: `auditions/gpt-sovits-v1.9.1/manifest.json`

**Interfaces:**
- Consumes: CLI and C1 profile from Task 2; existing v1.9.0 reference WAV and JSON.
- Produces: three validated local audition WAVs plus their complete reproducibility manifest.

- [ ] **Step 1: Verify local prerequisites without network access**

Check the reference, metadata, pinned repo commit, BERT, HuBERT, `s1v3.ckpt`, V5 Turbo, vocoder, G2PW and FastText files. Any missing prerequisite stops this task.

- [ ] **Step 2: Run offline generation**

Run from `D:\A` with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`:

```powershell
.\runtime\gpt-sovits\.venv\Scripts\python.exe tools\gpt_sovits_audition.py `
  --profile v1.9.1 `
  --reference auditions\gpt-sovits-v1.9.0\reference.wav `
  --reference-metadata auditions\gpt-sovits-v1.9.0\reference.json `
  --output-dir auditions\gpt-sovits-v1.9.1 `
  --repo-dir runtime\gpt-sovits\repo `
  --model-dir models\tts\gpt-sovits
```

Expected: process exits 0 and prints a UTF-8 manifest for exactly three samples.

- [ ] **Step 3: Validate output metrics**

Run `validate_clone` on all three files and assert mono PCM16, sample rate 16–96 kHz, positive duration, finite non-empty samples, and peak below full scale. Compare the manifest's hashes and metrics to the files.

### Task 4: Regression verification and versioned handoff

**Files:**
- Create: `quality/luna-v1.9.1-gpt-sovits-cool-restraint-verification.md`
- Modify: `README.md`
- Modify: `quality/README.md`

**Interfaces:**
- Consumes: Task 3 manifest and the existing health endpoint.
- Produces: reproducible commands, measured results, regression evidence and the manual listening decision gate.

- [ ] **Step 1: Run the complete automated suites**

Run backend `python -m pytest tests -q`, frontend `npm test -- --run`, and frontend `npm run build`. All must pass.

- [ ] **Step 2: Verify the live local application remains unchanged**

Confirm frontend HTTP 200 at `127.0.0.1:5173`, backend health at `127.0.0.1:8765`, Ollama ready, cloud outbound disabled, and Piper/Faster-Whisper configured. Confirm no GPT-SoVITS backend route or live-engine switch was introduced.

- [ ] **Step 3: Write the v1.9.1 verification record**

Record exact parameters, source/reference identity, each output's measured duration, generation time, peak and SHA-256, test/build results, local service state, and the remaining manual listening decision.

- [ ] **Step 4: Update user-facing documentation**

Add the v1.9.1 offline regeneration command and links to the design, plan, manifest and verification report. State explicitly that Piper remains live and that the audio is limited to private local evaluation pending rights clearance.

- [ ] **Step 5: Present the listening set**

Render the v1.9.0 baseline and all three v1.9.1 WAV files in the final response. Ask the user to choose accept, continue tuning, or reselect the reference; do not integrate GPT-SoVITS until that choice is explicit.
