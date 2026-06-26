# Implementation Plan: Punch-in Zoom Transforms

## Checklist

- [x] Inspect current transform and edit-plan command paths:
  - [x] `TimelineVideoTransform` model
  - [x] `EditingSettings`
  - [x] `EditingPlannerService._build_edit_plan`
  - [x] `ExporterService._valid_edit_plan`
  - [x] `ExporterService._timeline_video_filters`
  - [x] existing edit-plan/audio tests
- [x] Add config:
  - [x] `EditingSettings.zoom_enabled`
  - [x] `zoom_scale`
  - [x] `zoom_x_anchor`
  - [x] `zoom_y_anchor`
  - [x] `zoom_max_segments`
  - [x] env loading/clamping tests
- [x] Update transform contract:
  - [x] validate supported transform kinds
  - [x] validate punch-in scale range
  - [x] validate anchors
  - [x] preserve `None` and `kind="none"` compatibility
- [x] Update edit planner:
  - [x] preserve transform-free default output
  - [x] identify high-signal teaser segments
  - [x] apply punch-in transform only when enabled
  - [x] obey `zoom_max_segments`
  - [x] keep main segment transform-free
- [x] Update exporter:
  - [x] accept valid punch-in transforms
  - [x] reject invalid transform kinds and unsafe values
  - [x] generate scale/crop filters for transformed segments
  - [x] keep subtitle burn-in, audio mix, and concat behavior unchanged
  - [x] fall back for unsupported transforms
- [x] Add focused tests:
  - [x] config env load/clamp
  - [x] planner emits transforms only when enabled
  - [x] planner limits transforms and avoids main segment
  - [x] exporter command includes scale/crop for valid punch-in
  - [x] exporter fallback for invalid transforms
  - [x] audio-free and audio-enabled edit-plan regressions remain valid
- [x] Update backend export spec with final executable zoom contract.
- [x] Run validation.

## Validation Commands

Focused checks:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline\test_editing_service.py tests\pipeline\test_ffmpeg_resilience.py tests\test_config.py -q
```

Broader checks:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
git diff --check
.\.venv\Scripts\python.exe -m pytest tests\pipeline tests\test_config.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
```

## Risky Files

- `src/arl/exporter/service.py`: FFmpeg filter graph ordering is sensitive.
  Keep transform rendering inside the existing edit-plan path.
- `src/arl/shared/contracts.py`: stricter transform validation affects JSONL
  parsing. Preserve `None` and `kind="none"` compatibility.
- `src/arl/editing/service.py`: transform emission must not change teaser/main
  selection or idempotency.
- `src/arl/config.py`: zoom env values should clamp rather than create unsafe
  filter graphs.

## Rollback Points

- If exporter filter construction becomes too broad, keep config and planner
  disabled by default and defer rendering.
- If planner placement feels too aggressive, keep exporter support but require
  manual/edit-plan-provided transforms in a later task.

## Review Gate

Before `task.py start`, confirm this MVP scope:

- deterministic high-signal teaser-only punch-in
- fixed configurable scale and anchors
- no target tracking or animated zoom
- default disabled
- rendered only through edit-plan export
