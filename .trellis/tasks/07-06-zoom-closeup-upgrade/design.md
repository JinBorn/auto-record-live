# Zoom close-up upgrade design

## Architecture

This task upgrades the existing edit-plan zoom path instead of adding a new
postprocess stage. `EditingPlannerService` remains the owner of deciding which
timeline pieces receive `TimelineVideoTransform`; `ExporterService` remains the
owner of rendering those transforms.

The current contract already supports per-`TimelineSegment` punch-in transforms,
and the exporter already concatenates timeline segments seamlessly. The missing
piece is that long retained segments are currently either skipped or transformed
as one whole span. The upgrade splits eligible timeline segments into
untransformed / transformed / untransformed pieces around concrete trigger
timestamps.

## Data Flow

```text
MatchBoundary + HighlightPlanAsset + SubtitleAsset + RecordingAsset
  -> build teaser/main timeline
  -> collect zoom triggers
       1. KDA kill cue timestamps from subtitle `kda_change ... current_at=...`
       2. chat-burst timestamps from sampled chat-region frame differences
       3. reason-based fallback anchors from eligible timeline segments
  -> split matching timeline segments into short close-up pieces
  -> annotate close-up pieces with TimelineVideoTransform(kind="punch_in")
  -> write EditPlanAsset
  -> exporter renders per-piece transform filters before concat
  -> quality-report counts transformed close-up pieces
```

## Contracts

`TimelineVideoTransform` stays the durable transform carrier and gains optional
ease metadata:

```python
class TimelineVideoTransform(BaseModel):
    kind: str = "none"
    scale: float = 1.0
    x_anchor: float = 0.5
    y_anchor: float = 0.5
    target: str | None = None
    ease_in_seconds: float = 0.4
    ease_out_seconds: float = 0.4
```

Existing rows without ease fields remain valid through defaults. `kind` remains
limited to `none` and `punch_in`; scale and anchors keep the current validation
range.

New settings/env keys:

```text
ARL_EDIT_ZOOM_MODE=closeup        # closeup | legacy
ARL_EDIT_ZOOM_CLOSEUP_SECONDS=6   # clamped to 3..8
ARL_EDIT_ZOOM_EASE_SECONDS=0.4    # clamped to 0..1
ARL_EDIT_ZOOM_MIN_INTERVAL_SECONDS=25
ARL_EDIT_ZOOM_CHAT_BURST_ENABLED=1
ARL_EDIT_ZOOM_CHAT_BURST_SAMPLE_INTERVAL_SECONDS=0.5
ARL_EDIT_ZOOM_CHAT_BURST_THRESHOLD=0.08
```

Publish preset should keep `zoom_enabled=True`, default `zoom_target=chat`, and
raise `zoom_max_segments` from `1` to `3` unless explicitly configured.

## Trigger Selection

Trigger priority is deterministic:

1. KDA kill events inside retained timeline segments. Use the same subtitle
   parsing helpers as SFX alignment (`kills=a->b` with `b > a` and
   `current_at=<seconds>`). Anchor target defaults to `center` unless the
   operator configured another zoom target.
2. Chat-burst timestamps inside retained timeline segments. Sample frames from
   the source recording in each eligible segment, crop the bottom-left chat
   region, compare adjacent grayscale crops, and emit peaks above threshold.
   Chat triggers use `target="chat"` / bottom-left anchor.
3. Reason fallback: if no KDA/chat trigger fits an eligible segment, use the
   segment midpoint for `highlight_keyword`, `condensed_key_event`, or
   `condensed_tactical` until the zoom budget is filled.

Global spacing is enforced after priority sorting. A candidate within
`zoom_min_interval_seconds` of an already accepted candidate is skipped.

## Segment Splitting

For a retained segment `[start, end]` and trigger `t`, create a close-up window:

```text
window_start = clamp(t - duration / 2, start, end - duration)
window_end = window_start + duration
```

If the segment is shorter than the close-up duration, transform the whole
segment only when `zoom_mode=legacy`; in `closeup` mode, skip it unless it is at
least 3 seconds long. Preserve exact total source duration by replacing one
segment with up to three adjacent pieces:

```text
[start, window_start] no transform
[window_start, window_end] punch_in transform
[window_end, end] no transform
```

Drop zero-length pieces. Preserve role, reason, text, and source path. Audio
beds/SFX are timeline-output annotations and are computed after the split, so
they should continue to map against the final timeline.

## Ease Rendering

Exporter should render static transforms exactly as before when ease is `0`.
When ease is positive, render the close-up piece with a time-varying scale
expression that ramps:

```text
1.0 -> target scale over ease_in
target scale plateau
target scale -> 1.0 over ease_out
```

Keep crop anchoring tied to the same scale expression so the target area remains
stable. Unit tests should assert the generated FFmpeg filter includes time
variables (`t`) and the configured ease values; visual validation can be one
short export spot check.

## Compatibility and Rollback

- `ARL_EDIT_ZOOM_ENABLED=0` disables all zoom behavior.
- `ARL_EDIT_ZOOM_MODE=legacy` restores the current whole-segment transform
  behavior for rollback comparison.
- Existing edit plans without ease fields remain loadable.
- Plan freshness checks must treat old plans as stale when close-up mode is
  active but transformed segments exceed the close-up cap or lack ease metadata.

## Tradeoffs

- Splitting timeline segments is simpler and safer than introducing nested
  transform windows inside one segment because exporter, retimed subtitles, and
  quality-report already understand segment lists.
- Chat-burst detection is heuristic in v1. It should be best-effort and
  deterministic, not a hard prerequisite for emitting KDA/fallback close-ups.
- Center-vs-chat target for KDA is configurable; defaulting KDA to center gives
  fight emphasis, while chat bursts keep the bottom-left chat anchor.
