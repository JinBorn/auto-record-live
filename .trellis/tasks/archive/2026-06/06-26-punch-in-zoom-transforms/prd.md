# Punch-in zoom transforms

## Goal

Add opt-in punch-in zoom transforms to edit-plan exports so selected highlight
teaser moments can be visually emphasized without changing the validated main
match boundary or the default export behavior.

This is the fifth child task under `06-25-demo-editing-upgrades`.

## User Value

The reference Bilibili edits use occasional visual emphasis on exciting moments.
The local pipeline now has explicit edit plans and teaser-before-main rendering,
so a safe next step is to let edit plans mark selected local timeline segments
for a modest fixed zoom that the exporter can render.

## Confirmed Facts

- `TimelineVideoTransform` already exists in `src/arl/shared/contracts.py` with
  `kind`, `scale`, `x_anchor`, and `y_anchor`.
- `TimelineSegment.transform` already exists and defaults to `None`.
- `ExporterService._valid_edit_plan()` currently rejects any transform where
  `kind != "none"`.
- `ExporterService._timeline_video_filters()` owns the per-segment video filter
  chain used by edit-plan exports.
- Edit-plan exports are already explicit opt-in via `ARL_EXPORT_USE_EDIT_PLANS`.
- The previous child task added audio instructions while preserving audio-free
  edit-plan command compatibility.
- Parent task constraints say punch-in zoom should start with safe fixed anchors
  and modest scale, not automatic target tracking.

## Requirements

- Preserve existing full, highlight, condensed, subtitle, and audio-free
  edit-plan exports when zoom is disabled.
- Add opt-in editing config for punch-in zoom:
  - disabled by default
  - modest default scale
  - x/y anchors clamped inside the frame
  - maximum number of teaser segments to transform
- Planner behavior:
  - emit no zoom transforms unless zoom is enabled
  - when enabled, add `TimelineVideoTransform(kind="punch_in")` only to
    high-signal teaser segments
  - do not apply punch-in transforms to the `main` segment in the MVP
  - do not block base edit-plan generation if no transform-eligible segment
    exists
- Exporter behavior:
  - accept `kind="none"` and `kind="punch_in"` only
  - validate punch-in scale and anchors before rendering
  - render punch-in with FFmpeg video filters only in the edit-plan path
  - keep audio and subtitle handling unchanged
  - fall back to existing export behavior for invalid transform plans
- Keep target tracking, OCR-based focus points, animated zooms, and manual UI
  review out of scope for this child task.

## Acceptance Criteria

- [ ] Zoom settings load from env with disabled-by-default behavior and safe
      clamping.
- [ ] Edit planner emits no transforms unless zoom is enabled.
- [ ] When zoom is enabled, eligible high-signal teaser segments receive
      `TimelineVideoTransform(kind="punch_in")`; main segments remain unzoomed.
- [ ] Existing audio-free and audio-enabled edit-plan exports remain valid when
      transforms are absent.
- [ ] Exporter accepts valid punch-in transforms and includes crop/scale filters
      in the per-segment video chain.
- [ ] Exporter rejects invalid transform kinds, unsafe scale, or out-of-range
      anchors and falls back.
- [ ] Existing default export paths remain unchanged unless edit-plan export and
      zoom planning are explicitly enabled.
- [ ] Focused tests cover config loading, planner emission, exporter command
      construction, invalid transform fallback, and backward compatibility.

## Out Of Scope

- Automatic target tracking for champions, facecam, minimap, KDA, or chat.
- Animated zoom in/out curves.
- Applying zoom transforms to the full main match segment by default.
- User-facing editing UI for manual zoom placement.
- External reference inserts.
- Reprocessing existing edit plans unless the user runs the planner with
  `--force-reprocess`.

## Open Questions

- None blocking. Recommended MVP: deterministic high-signal teaser-only zooms
  with fixed configurable anchors and scale.
