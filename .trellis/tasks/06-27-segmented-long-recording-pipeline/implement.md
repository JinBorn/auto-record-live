# Segmented Long Recording Pipeline Implementation Plan

## Phase 1: Contracts And Resolver

- [x] Read backend specs before code changes.
- [x] Add durable chunk models in `src/arl/shared/contracts.py`.
- [x] Add a media resolver module that supports legacy single-file assets and chunk manifests.
- [x] Add unit tests for:
  - single-file resolution
  - one chunk resolution
  - cross-chunk resolution
  - edge clamping
  - missing/invalid manifest fallback
- [x] Update specs with the new contract and resolver semantics.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
git diff --check
```

## Phase 2: Recorder Manifest Emission

- [x] Add config fields for segmented recording enablement and chunk duration.
- [x] Add direct-stream segmented FFmpeg command generation behind opt-in config.
- [x] Probe produced chunks and write `recording-chunks.json`.
- [x] Append/index chunk manifest rows without breaking `recording-assets.jsonl` compatibility.
- [x] Add recorder tests for command shape, manifest writing, and default-off compatibility.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_ffmpeg_resilience.py tests/test_config.py -q
```

## Phase 3: Exporter Cross-Chunk Rendering

- [x] Teach exporter to resolve source windows to chunk-local spans.
- [x] Support stream-copy/highlight concat across chunk spans.
- [x] Support edit-plan timeline segment expansion across chunk spans.
- [x] Keep ASS subtitle burn-in after edit-plan concat.
- [x] Add exporter command tests for a match crossing two chunks.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_ffmpeg_resilience.py tests/pipeline/test_editing_service.py -q
```

## Phase 4: Subtitle And Analysis Windowing

- [x] Teach subtitle preprocessing to build match-local WAV from chunk spans.
- [x] Add ASR tests for a boundary spanning two chunks.
- [x] Update editing source-music detection to sample chunk spans.
- [x] Update highlight KDA sampling to either sample chunk spans or log explicit fallback.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_subtitles_service.py tests/pipeline/test_editing_service.py tests/pipeline/test_highlight_planner_service.py -q
```

## Phase 5: Status, Repair, Docs

- [x] Update repair to detect completed chunk manifests.
- [x] Update status degraded reporting for unregistered chunked recordings.
- [x] Update README operator instructions.
- [x] Add reset/status/repair tests.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_status_service.py tests/pipeline/test_postprocess_reset_service.py tests/pipeline/test_cli_unattended.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
git diff --check
```

## Risk Notes

- Do not switch default recording mode until all downstream consumers are chunk-aware.
- Do not use stop/restart loops for chunking.
- Avoid introducing required migrations for existing `RecordingAsset` rows.
- Keep all raw file deletion behavior conservative: reset/postprocess cleanup must not delete raw chunks.

## First Implementation Target

Start with Phase 1 only. It creates the shared contract and resolver tests needed by every later stage, while preserving current runtime behavior.
