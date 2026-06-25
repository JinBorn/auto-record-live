# Implementation Plan: Publishing Metadata And Cover Assets

## Checklist

- [x] Inspect existing copywriter tests and model expectations.
- [x] Add publishing package model(s) without changing `CopyAsset`.
- [x] Add copywriter service logic to load recording assets and highlight plans.
- [x] Add cue selection helpers:
  - [x] parse subtitle text with timing
  - [x] choose cues overlapping valid highlight windows
  - [x] fall back to transcript excerpt
- [x] Add deterministic metadata generation:
  - [x] title candidates
  - [x] recommended title
  - [x] summary
  - [x] cover lines
  - [x] tags
  - [x] evidence
- [x] Add optional cover rendering helper with ffmpeg + Pillow skip behavior.
- [x] Write per-match publishing JSON and append package JSONL asset rows.
- [x] Update tests or add focused tests for compatibility and new artifact.
- [x] Run focused validation.

## Validation Commands

```powershell
.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_copywriter_service.py
.\.venv\Scripts\python.exe -m pytest tests/test_config.py
```

Run broader pipeline checks if exporter/shared contracts are touched:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/pipeline
```

## Validation Run

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pytest tests\pipeline\test_copywriter_service.py tests\pipeline\test_postprocess_reset_service.py tests\test_config.py -q
.\.venv\Scripts\python.exe -m pytest tests\pipeline -q
```

## Rollback Points

- If cover rendering complicates the service, keep metadata-only output and
  leave cover rendering behind a disabled helper.
- If model compatibility risk appears, keep `PublishingPackage` entirely local
  to `copywriter` and avoid changing shared contracts in this child task.
