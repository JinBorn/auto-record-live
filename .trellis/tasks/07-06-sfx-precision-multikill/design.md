# SFX precision and multi-kill variants design

## Architecture and boundaries

This task extends edit-plan audio planning only. The exporter contract stays
unchanged: planned effects are still emitted as `SoundEffectHit` rows with
`source_path`, `at_seconds`, and `gain_db`. Exporter mixing, labels, delays,
and `amix` behavior remain out of scope unless tests expose a regression.

The edit planner owns:

- loading optional SFX library categories;
- extracting kill-only KDA events from cues;
- mapping source timestamps into rendered timeline seconds;
- selecting single-kill or multi-kill assets;
- applying rate limit and per-export cap.

The quality report already maps output SFX positions back to source time and
reports nearest KDA deltas. This task preserves that metric and uses it as the
primary runtime validation signal.

## Current code facts

- `src/arl/editing/service.py` currently emits kill SFX at eligible segment
  starts for `highlight_keyword` and `condensed_key_event` segments.
- The same planner already handles transition segments and transition whoosh
  assets from `data/sfx/library.json`.
- `src/arl/editing/audio.py` generates a synthetic `coin.wav`; this remains
  the fallback when no user asset is available.
- `src/arl/highlights/service.py` emits synthetic cue text beginning
  `kda_change` with fields such as `kills=6->8`, `deaths=2->2`,
  `previous_at=...`, and `current_at=...`.
- `src/arl/quality_report/service.py` already includes SFX detail and nearest
  KDA delta reporting.

## Data flow

1. Build or load the edit timeline segments in output order.
2. Extract KDA kill events from transcript/highlight cues:
   - parse `kills=A->B` and `deaths=A->B`;
   - ignore events where `B <= A`;
   - use the cue timestamp or `current_at` as the source timestamp;
   - preserve kill delta for variant selection.
3. Map each kill event source timestamp to output seconds by walking
   `TimelineSegment` rows:
   - `teaser` and `main` segments map when the source timestamp lies inside
     `[source_start_seconds, source_end_seconds]`;
   - `transition` segments advance the output cursor but never map source
     events;
   - trimmed gaps return no mapping;
   - if the same source event appears in teaser and main, the first rendered
     occurrence wins.
4. Apply `sfx_timing_offset_seconds`, clamp to the rendered segment boundary,
   and schedule the effect when rate/cap constraints allow it.
5. If no mappable kill event exists inside an eligible segment, keep the
   current segment-start fallback so old highlight-only plans still get a
   conservative coin hit.

## Asset library contract

The SFX library uses `data/sfx/library.json` and `data/sfx/tracks/`, following
the BGM library's tolerant loading style. Supported v1 categories are:

- `kill_coin`
- `multi_kill`
- `transition_whoosh`
- `teaser_impact`

`ARL_EDIT_SFX_PATH` remains an explicit override for kill SFX. If it is set and
the file exists, both single and multi-kill hits use that file. If unset, the
planner prefers `multi_kill` for multi-kill events and `kill_coin` for single
kills. Missing or invalid library rows are skipped with a log; if no suitable
kill asset remains, the generated `coin.wav` is used.

## Multi-kill selection

An event selects `multi_kill` when either:

- the parsed KDA kill delta is at least 2; or
- subtitles near the event contain a multi-kill keyword, including
  `double kill`, `triple kill`, `quadra kill`, `penta kill`, or the Chinese
  announcement equivalents.

Otherwise it selects `kill_coin`. If the selected category has no usable asset,
the planner falls back to `kill_coin`, then generated `coin.wav`.

## Configuration

Add edit-planner configuration with environment overrides:

- `sfx_library_path: Path | None = Path("data/sfx/library.json")`
- `sfx_timing_offset_seconds: float = 0.0`
- `sfx_min_interval_seconds: float = 20.0`
- `sfx_max_hits: int = 6`
- `sfx_kda_alignment_enabled: bool = True`
- `sfx_multikill_window_seconds: float = 8.0`

Keep existing transition SFX settings and quality-report thresholds compatible.

## Compatibility and rollback

The low-risk rollback is to disable KDA alignment with
`ARL_EDIT_SFX_KDA_ALIGNMENT_ENABLED=0`, returning to the segment-start fallback.
If a user-managed library is missing, malformed, or points at missing files, the
pipeline must continue with the generated coin asset. No `data/` runtime assets
are committed.
