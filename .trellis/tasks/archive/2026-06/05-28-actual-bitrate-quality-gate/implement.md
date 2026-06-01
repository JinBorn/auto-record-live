# Implementation Plan

## Checklist

1. Load pre-development specs for the recorder/orchestrator/backend paths.
2. Add recorder settings for actual-resolution validation:
   - minimum width/height or minimum height defaulting to 1080p
   - early validation timeout/window
   - an enable/disable switch only if needed for rollout
3. Add a small recorder quality-probe helper:
   - run ffprobe against the partial direct-stream output or stream target
   - extract actual video width/height
   - extract bitrate when available for diagnostics
   - return explicit pass/fail/unknown outcomes
4. Integrate the helper into direct-stream ffmpeg recording:
   - start recording as today
   - validate during the early window
   - on below-1080p, terminate ffmpeg, remove partial mp4, emit
     `quality_below_actual_resolution`
   - on unknown probe result, preserve existing ffmpeg failure behavior unless
     the failure is clearly a quality rejection
5. Extend recorder audit model/event writing only as much as needed for
   resolution and bitrate diagnostics.
6. Extend orchestrator recorder-event handling:
   - treat `quality_below_actual_resolution` as a known event
   - mark the recording job terminal with quality failure metadata
   - clear active job linkage when appropriate so a later live snapshot can
     create fresh work
7. Add or update tests:
   - recorder rejects 720p actual output and deletes partial file
   - recorder accepts 1080p+ actual output
   - recorder includes observed resolution and bitrate diagnostics
   - orchestrator consumes the quality event as known and terminal
   - existing Bilibili and Douyin probe quality tests still pass
8. Run focused validation, then broader checks if focused tests pass.

## Validation Commands

Run focused tests first:

```powershell
pytest tests/pipeline/test_ffmpeg_resilience.py
pytest tests/orchestrator/test_service.py
pytest tests/windows_agent/test_bilibili_probe.py tests/windows_agent/test_probe.py
pytest tests/test_config.py
```

Run the full suite if focused tests pass:

```powershell
pytest
```

## Risky Files

- `src/arl/recorder/service.py`: ffmpeg process lifecycle and partial-file
  cleanup.
- `src/arl/recorder/models.py`: audit event schema compatibility.
- `src/arl/orchestrator/service.py`: recorder event state transitions.
- `src/arl/config.py`: defaults and environment loading.
- `tests/pipeline/test_ffmpeg_resilience.py`: likely home for recorder quality
  gate tests.
- `tests/orchestrator/test_service.py`: likely home for orchestrator event
  handling tests.

## Review Gate

Before starting implementation, review this plan with the operator and confirm
that the hard gate is:

- highest available quality is selected first
- actual recorded resolution must be 1080p or higher
- below 1080p is unusable
- bitrate is diagnostic/auxiliary, not a global cap
