# Implementation Plan: Edit Plans With Teaser-Before-Main Rendering

## Checklist

- [x] Inspect existing stage patterns:
  - [x] config env loading
  - [x] CLI filter parsing
  - [x] postprocess stage order
  - [x] status counts
  - [x] postprocess-reset cleanup
  - [x] exporter command tests
- [x] Add edit-plan contracts:
  - [x] `TimelineVideoTransform`
  - [x] `TimelineSegment`
  - [x] `EditPlanAsset`
  - [x] `EditPlannerStateFile`
- [x] Add config:
  - [x] `EditingSettings`
  - [x] `settings.editing`
  - [x] `settings.export.use_edit_plans`
  - [x] env loading/clamping tests
- [x] Add `EditingPlannerService`:
  - [x] read boundaries and highlight plans
  - [x] validate source boundary and highlight windows
  - [x] select teaser windows deterministically
  - [x] emit teaser segment(s) followed by full main segment
  - [x] persist `edit-plans.jsonl` and `editing-state.json`
  - [x] support session/match filters and force reprocess
  - [x] skip missing/invalid inputs without marking processed
- [x] Wire operational entrypoints:
  - [x] CLI `edit-planner`
  - [x] `PostProcessService` stage order after `highlight-planner`
  - [x] `StatusService` edit plan counts/state
  - [x] `PostProcessResetService` edit plan row/state cleanup
- [x] Wire exporter opt-in render path:
  - [x] load edit plans only when `use_edit_plans=True`
  - [x] validate edit plans against current boundary
  - [x] prefer valid edit plan over highlight plan only when enabled
  - [x] build `filter_complex` trim/atrim/concat command for teaser + main
  - [x] reuse SRT/ASS burn-in subtitle render path for per-segment video filters
  - [x] fall back to existing behavior for invalid/unsupported plans
- [x] Add focused tests:
  - [x] editing planner service tests
  - [x] exporter edit-plan command tests
  - [x] invalid plan fallback tests
  - [x] config tests
  - [x] CLI parser/entrypoint tests
  - [x] postprocess order tests
  - [x] status/reset tests
- [x] Run validation.

## Validation Commands

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline\test_editing_service.py tests\pipeline\test_ffmpeg_resilience.py tests\pipeline\test_postprocess_service.py tests\pipeline\test_postprocess_reset_service.py tests\pipeline\test_status_service.py tests\test_config.py -q
```

Then run broader checks:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline tests\test_config.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
```

Also run:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
git diff --check
```

## Risky Files

- `src/arl/exporter/service.py`: command construction is heavily regression
  tested; keep edit-plan path behind `use_edit_plans`.
- `src/arl/shared/contracts.py`: adding cross-stage models affects JSONL loads;
  keep additions backward-compatible.
- `src/arl/postprocess/reset.py`: deletion logic must not remove files outside
  generated roots.
- `src/arl/cli.py`: keep command parser and entrypoint filter semantics aligned
  with existing postprocess stages.

## Rollback Points

- If FFmpeg `filter_complex` command construction becomes too invasive, keep
  model/planner/status/reset work and defer exporter rendering behind the
  disabled `use_edit_plans` flag.
- If status/reset integration grows too broad, keep manifest/state names stable
  and add focused cleanup/status support before starting later audio/zoom tasks.

## Review Gate

Before `task.py start`, confirm this MVP scope:

- teaser clips are duplicated before the full validated main match
- no condensed-main, BGM/SFX, zoom, or external inserts in this child task
- edit-plan export is disabled by default and opt-in via
  `ARL_EXPORT_USE_EDIT_PLANS=1`
