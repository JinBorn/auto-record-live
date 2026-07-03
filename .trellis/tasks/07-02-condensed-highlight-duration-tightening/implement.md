# Implementation Plan

## Phase 0: Baseline Analysis Harness

- [x] Add or extend a local analysis helper for current plans/exports.
- [x] Report, per match:
  - rendered duration;
  - dynamic target duration and reason inputs;
  - duration by reason;
  - subtitle-active ratio;
  - no-subtitle gap count and max gap;
  - max source-time gap;
  - KDA cue count and uncovered count;
  - continuity duration ratio;
  - oversized key-event windows.
- [ ] Use the current sample set:
  - `session-20260617073649-4b5ec478_match02`
  - `session-20260617073651-cf11bf9e_match02`
  - `session-20260617073651-cf11bf9e_match03`
  - `session-20260617073651-cf11bf9e_match04`
  - `data/demo2` as pacing reference.

Validation:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.pipeline.test_highlight_planner_service tests.highlights.test_window_optimizer
```

## Phase 1: No-Subtitle Gap Compression

- [x] Implement a pass that finds long low-value gaps inside retained windows.
- [x] Do not classify a gap as low-value if it overlaps:
  - KDA kill/death protected intervals;
  - active or adjacent speech cues;
  - visually active fight/objective spans;
  - death lead-in guard.
- [x] Compress eligible gaps to a short context, initially 3-4s.
- [x] Add tests where a long silent lane/walk gap is compressed.
- [x] Add tests where a silent fight with KDA/visual action is preserved even without subtitles.

Validation:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.pipeline.test_highlight_planner_service
```

## Phase 2: KDA Window Tightening

- [x] Reduce excessive KDA preroll/tail defaults or add publish-preset overrides.
- [x] Target initial values:
  - kill preroll: 15s;
  - death preroll: 30s;
  - postroll: 5s.
- [x] Preserve the previous bug guarantees:
  - no kill/death event can be uncovered;
  - continuity snippets alone do not satisfy KDA coverage;
  - death transitions cannot start directly on a death timer.
- [x] Add tests for kill-only, death, and post-death kill-credit cases.

Validation:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.pipeline.test_highlight_planner_service
.\.venv\Scripts\python.exe -m pytest tests/highlights/test_window_optimizer.py
```

## Phase 3: Continuity Bridge Slimming

- [x] Decouple bridge snippet length from full edge context.
- [x] Add a configurable bridge snippet length, target 2-4s.
- [x] Keep `condensed_boring_gap_threshold_seconds` safety: adjacent source gaps must still stay within the threshold.
- [x] Add continuity duration ratio reporting.
- [x] Add tests ensuring huge source gaps are still split, but continuity duration does not balloon unnecessarily.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/highlights/test_window_optimizer.py
```

## Phase 4: Edge Context Tightening

- [x] Reduce default match-start and match-end context under publish preset.
- [x] Target 8-15s edge context when the edge is low-signal.
- [x] Preserve exporter contract that edit plans cover both source boundary edges.
- [x] Add tests for:
  - low-signal edge gets short context;
  - high-signal edge can keep longer context;
  - exporter still accepts the edit plan.

Validation:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.pipeline.test_editing_service tests.pipeline.test_highlight_planner_service
```

## Phase 5: Final Sample Regeneration

- [x] Regenerate highlight plans and edit plans for the sample set.
- [x] Re-export sample videos only after plan metrics look acceptable against the dynamic 7-20 minute policy.
- [x] Do not reintroduce default transition SFX.
- [x] Keep fixed-bitrate export around 8 Mbps for publish outputs.
- [x] Produce a validation report:
  - duration;
  - dynamic target duration and whether the plan is below/inside/above it;
  - bitrate/resolution;
  - BGM/SFX counts;
  - subtitle-active ratio;
  - no-subtitle gap stats;
  - max source-time gap;
  - KDA uncovered count;
  - continuity ratio;
  - any exception reasons.

Commands:

```powershell
.\.venv\Scripts\python.exe -m arl.cli highlight-planner --session-id <session> --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli edit-planner --session-id <session> --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli exporter --session-id <session> --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli copywriter --session-id <session> --force-reprocess
```

## Phase 6: Spec Update And Quality Gate

- [x] Update `.trellis/spec/backend/export-configuration.md` with the new composite trimming contract.
- [x] Run focused tests.
- [x] Run compile check.
- [x] Run `git diff --check`.

Validation:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.pipeline.test_highlight_planner_service tests.pipeline.test_editing_service tests.test_config
.\.venv\Scripts\python.exe -m pytest tests/highlights/test_window_optimizer.py
.\.venv\Scripts\python.exe -m compileall -q src\arl\highlights src\arl\editing src\arl\config.py
git diff --check
```

## Risk Gates

- Do not proceed to export if KDA uncovered count is non-zero.
- Do not proceed to export if max source-time gap exceeds `condensed_boring_gap_threshold_seconds`.
- Do not accept a plan that reaches near-full-match duration without an explicit dense-event exception, even if it remains within the 7-20 minute dynamic range.
- Do not trim a silent interval solely because it lacks subtitles; it must also lack KDA/visual/action protection.
