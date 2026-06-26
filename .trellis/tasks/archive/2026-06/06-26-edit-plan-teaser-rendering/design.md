# Technical Design: Edit Plans With Teaser-Before-Main Rendering

## Overview

Introduce a separate editing stage that writes presentation timelines to
`edit-plans.jsonl`. The exporter can then opt into rendering that timeline with
teaser segments first and a full main segment second.

This keeps source-analysis artifacts and presentation artifacts separate:

- `MatchBoundary`: canonical validated match interval
- `HighlightPlanAsset`: source windows worth retaining or emphasizing
- `EditPlanAsset`: ordered presentation timeline for upload-style rendering

## Module Boundaries

- `src/arl/shared/contracts.py`
  - Owns cross-stage `EditPlanAsset` and nested timeline contract models.
- `src/arl/editing/models.py`
  - Owns `EditPlannerStateFile`.
- `src/arl/editing/service.py`
  - Owns edit-plan generation, idempotency, filtering, and manifest writes.
- `src/arl/exporter/service.py`
  - Owns selecting a valid edit plan and building the FFmpeg command.
- `src/arl/config.py`
  - Owns edit-planner settings and export opt-in settings.
- `src/arl/cli.py` and `src/arl/postprocess/service.py`
  - Own command and pipeline wiring.
- `src/arl/status/service.py` and `src/arl/postprocess/reset.py`
  - Own operational visibility and targeted cleanup for the new manifest/state.

## Contracts

### Config

Add planner settings:

```python
class EditingSettings(BaseModel):
    enabled: bool = False
    teaser_max_segments: int = 2
    teaser_max_total_seconds: float = 45.0
    teaser_min_segment_seconds: float = 3.0
```

Environment keys:

- `ARL_EDIT_PLANNER_ENABLED` (`0`/`1`, default `0`)
- `ARL_EDIT_TEASER_MAX_SEGMENTS` (default `2`, minimum `1`)
- `ARL_EDIT_TEASER_MAX_TOTAL_SECONDS` (default `45.0`, minimum `1.0`)
- `ARL_EDIT_TEASER_MIN_SEGMENT_SECONDS` (default `3.0`, minimum `0.1`)
- `ARL_EXPORT_USE_EDIT_PLANS` (`0`/`1`, default `0`)

### Edit Plan Models

Store these as Pydantic models in `shared/contracts.py` because the planner
writes them and exporter/status/reset may read them.

```python
class TimelineVideoTransform(BaseModel):
    kind: str = "none"  # future: "punch_in"
    scale: float = 1.0
    x_anchor: float = 0.5
    y_anchor: float = 0.5
```

```python
class TimelineSegment(BaseModel):
    role: str  # "teaser" | "main" | "insert"
    source_path: str | None = None
    source_start_seconds: float
    source_end_seconds: float
    transform: TimelineVideoTransform | None = None
    reason: str
```

```python
class EditPlanAsset(BaseModel):
    session_id: str
    match_index: int
    source_boundary_start_seconds: float
    source_boundary_end_seconds: float
    timeline: list[TimelineSegment]
    audio_beds: list[dict[str, object]] = []
    sound_effects: list[dict[str, object]] = []
    created_at: datetime
```

MVP renderer supports only `source_path is None`, `role in {"teaser", "main"}`,
and no non-empty `audio_beds` / `sound_effects` / non-`none` transforms.
Future child tasks can replace the placeholder dicts with typed audio models.

## Planner Behavior

Inputs:

- `match-boundaries.jsonl`
- `highlight-plans.jsonl`
- `editing-state.json`

Output:

- append `EditPlanAsset` rows to `edit-plans.jsonl`
- persist processed keys in `editing-state.json`

Selection:

1. Skip if `settings.editing.enabled` is false.
2. Consider only complete boundaries (`is_complete=True`, `confidence >= 0.8`).
3. Require a matching highlight plan with source boundary metadata aligned to the
   current boundary.
4. Reject empty, negative, reversed, or out-of-bound highlight windows.
5. Select up to `teaser_max_segments` windows that meet
   `teaser_min_segment_seconds`, respecting `teaser_max_total_seconds`.
6. Prioritize reasons deterministically:
   `condensed_key_event`, `highlight_keyword`, `condensed_tactical`,
   `condensed_context`, then other reasons.
7. Emit selected teaser windows first, then one main segment covering
   `[0.0, boundary_duration]`.

Idempotency:

- Key shape: `<session_id>:<match_index>`.
- Skip a processed key only when a current manifest row exists.
- `force_reprocess=True` appends a replacement row; downstream maps by latest
  `(session_id, match_index)` row.
- Missing/invalid highlight input does not mark the key processed, so later
  highlight generation can produce an edit plan.

## Exporter Behavior

New input:

- `edit-plans.jsonl`, loaded only when `settings.export.use_edit_plans` is true.

Selection precedence:

1. If `use_edit_plans=True` and a valid edit plan exists, render edit plan.
2. Else if `use_highlight_plans=True` and a valid highlight plan exists, use the
   existing highlight-plan path.
3. Else use the existing full-boundary path.

Validation:

- Plan source boundary start/end match current `MatchBoundary` within 1 second.
- Timeline has at least one segment.
- All segment times are relative to boundary and inside `[0, duration]`.
- No reversed or sub-zero segments.
- All MVP-rendered segments have `source_path is None`.
- Only `teaser` and `main` roles are renderable in this task.
- At least one main segment exists and the first main starts at `0.0`.
- All teaser segments occur before the first main segment in timeline order.
- `audio_beds`, `sound_effects`, insert roles, and non-`none` transforms make
  the plan unsupported for this task and exporter falls back.

FFmpeg command shape:

```text
ffmpeg -y -nostdin -hide_banner -loglevel error
  -ss <boundary_start> -t <boundary_duration> -i <recording>
  -filter_complex "
    [0:v]trim=start=<teaser_start>:end=<teaser_end>,setpts=PTS-STARTPTS[v0];
    [0:a]atrim=start=<teaser_start>:end=<teaser_end>,asetpts=PTS-STARTPTS[a0];
    [0:v]trim=start=0:end=<duration>,setpts=PTS-STARTPTS[v1];
    [0:a]atrim=start=0:end=<duration>,asetpts=PTS-STARTPTS[a1];
    [v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]
  "
  -map [v] -map [a] <video encode args> <quality args> <output>
```

When subtitle burn-in is enabled, add `subtitles=...` before each segment's
`trim` so subtitle timing remains relative to the original match source time.
Use the existing `_subtitle_render_path()` and `_subtitle_filter_arg()` helpers.

## Compatibility

- Defaults keep existing behavior unchanged:
  - `editing.enabled=False`
  - `export.use_edit_plans=False`
- Highlight-plan export remains explicit behind `ARL_EXPORT_USE_HIGHLIGHT_PLANS`.
- Edit-plan export does not append different `ExportAsset` schema rows.
- Invalid/unsupported edit plans fall back to existing exporter behavior instead
  of blocking exports.
- `StatusService` should include `edit_plans` in `postprocess` and
  `processed_matches` under an `editing` section.
- `PostProcessResetService` should remove target-session `edit-plans.jsonl` rows
  and edit-planner state keys.
- `data/` reference videos remain local user material and are not committed.

## Tests

- Editing service:
  - writes teaser-first + full-main plan from a valid highlight plan
  - skips missing/invalid highlight plans without marking processed
  - idempotency and force-reprocess behavior
  - session/match filters
- Exporter:
  - loads edit plans only when `use_edit_plans=True`
  - valid edit plan builds `filter_complex` with teaser before main
  - burn-in uses SRT/ASS subtitle filter in per-segment video chains
  - invalid edit plans fall back to existing highlight/full behavior
  - edit-plan disabled leaves existing commands unchanged
- Wiring:
  - config env loading
  - CLI parser/entrypoint for `edit-planner`
  - postprocess stage order
  - status counts
  - postprocess-reset cleanup

## Rollback

The feature is opt-in. Rollback can disable `ARL_EDIT_PLANNER_ENABLED` and
`ARL_EXPORT_USE_EDIT_PLANS`, leaving existing highlight/full exports untouched.
