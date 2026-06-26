# Technical Design: Punch-in Zoom Transforms

## Overview

Extend the existing edit-plan transform field from a placeholder contract into a
renderable MVP. The planner may annotate selected teaser segments with
`kind="punch_in"`, and the exporter may render those transforms through the
existing edit-plan `filter_complex` path.

Default exports and transform-free edit-plan exports must remain unchanged.

## Module Boundaries

- `src/arl/shared/contracts.py`
  - Owns `TimelineVideoTransform` validation.
- `src/arl/config.py`
  - Owns zoom feature flags and env loading.
- `src/arl/editing/service.py`
  - Owns deciding which generated teaser segments receive transforms.
- `src/arl/exporter/service.py`
  - Owns validating transform plans and building FFmpeg video filters.
- `.trellis/spec/backend/export-configuration.md`
  - Owns the executable contract for opt-in edit-plan transforms.

## Config Contract

Add fields under `EditingSettings`:

```python
zoom_enabled: bool = False
zoom_scale: float = 1.2
zoom_x_anchor: float = 0.5
zoom_y_anchor: float = 0.5
zoom_max_segments: int = 1
```

Environment:

- `ARL_EDIT_ZOOM_ENABLED` (`0`/`1`, default `0`)
- `ARL_EDIT_ZOOM_SCALE` (default `1.2`, clamp `[1.0, 1.5]`)
- `ARL_EDIT_ZOOM_X_ANCHOR` (default `0.5`, clamp `[0.0, 1.0]`)
- `ARL_EDIT_ZOOM_Y_ANCHOR` (default `0.5`, clamp `[0.0, 1.0]`)
- `ARL_EDIT_ZOOM_MAX_SEGMENTS` (default `1`, clamp at least `0`)

`zoom_max_segments=0` is a valid operator override that disables emitted
transforms even when `zoom_enabled=1`.

## Transform Contract

`TimelineVideoTransform` remains the durable model:

```python
class TimelineVideoTransform(BaseModel):
    kind: str = "none"
    scale: float = 1.0
    x_anchor: float = 0.5
    y_anchor: float = 0.5
```

Validation:

- `kind` must be `"none"` or `"punch_in"`.
- `kind="none"` is accepted as a no-op.
- `kind="punch_in"` requires `1.0 < scale <= 1.5`.
- anchors must be inside `[0.0, 1.0]`.

This task does not introduce a richer transform subtype or keyframe contract.

## Planner Behavior

Inputs:

- complete `MatchBoundary`
- matching `HighlightPlanAsset`
- existing teaser/main timeline generation
- editing zoom config

Behavior:

1. Preserve current teaser/main segment selection.
2. If `settings.editing.zoom_enabled` is false, emit no transform fields.
3. If zoom is enabled, apply `TimelineVideoTransform(kind="punch_in", ...)` to
   up to `zoom_max_segments` teaser segments whose reasons are high signal:
   `highlight_keyword`, `condensed_key_event`, or `condensed_tactical`.
4. Never add a punch-in transform to the `main` segment in the MVP.
5. Missing eligible segments produce a normal transform-free edit plan.

## Exporter Behavior

Validation extends existing edit-plan validation:

- `transform is None` and `transform.kind == "none"` remain accepted.
- `transform.kind == "punch_in"` is accepted only when scale and anchors pass
  the safety bounds.
- Any other transform kind makes the edit plan invalid and triggers fallback.

Rendering:

- Keep subtitle burn-in first in the per-segment video chain so subtitles remain
  attached to the segment being rendered.
- Apply source trimming before or with the transform chain in a way that keeps
  segment timing unchanged.
- Render punch-in with a static scale/crop chain that preserves output
  dimensions, for example:

```text
scale=iw*1.200:ih*1.200,
crop=iw/1.200:ih/1.200:x=(iw-iw/1.200)*0.500:y=(ih-ih/1.200)*0.500
```

The filter is intentionally static. Animated `zoompan` and target tracking are
deferred.

## Compatibility

- `ARL_EDIT_ZOOM_ENABLED=0` preserves transform-free edit plans.
- `ARL_EXPORT_USE_EDIT_PLANS=0` ignores edit plans and therefore ignores
  transforms.
- Existing `kind="none"` and `transform=None` rows continue to parse and render.
- Invalid transform rows inside stale/manual edit plans fail closed by falling
  back to highlight/full export.
- Audio mixing remains independent: a plan may contain both audio instructions
  and valid punch-in transforms.

## Tests

- Config:
  - env values load
  - scale and anchors clamp
  - default disabled
- Editing service:
  - zoom disabled emits no transforms
  - zoom enabled transforms high-signal teaser segments only
  - `zoom_max_segments=0` emits no transforms
- Exporter:
  - valid punch-in command includes scale/crop filters
  - invalid transform kind falls back
  - out-of-range transform values fall back
  - audio-free edit-plan command remains unchanged when no transform exists
  - audio-enabled edit-plan command can coexist with valid punch-in transforms

## Rollback

Disable `ARL_EDIT_ZOOM_ENABLED` to stop emitting transforms. Disable
`ARL_EXPORT_USE_EDIT_PLANS` to bypass edit-plan rendering entirely.
