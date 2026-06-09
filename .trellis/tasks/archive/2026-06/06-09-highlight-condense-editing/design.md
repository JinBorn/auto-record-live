# Technical Design

## Scope

This task improves post-recording exports in two conservative steps:

1. Match boundaries can end at reliable `post_game` stage hints/signals instead
   of always ending at the next `in_game` start or recording duration.
2. A new conservative highlight plan stage can remove long low-signal gaps from
   high-confidence match boundaries while preserving context around narration
   and important gameplay moments.

## Current Boundaries

- `SegmenterService` owns `match-boundaries.jsonl`.
- `SubtitleService` consumes `match-boundaries.jsonl` and writes one SRT per
  `(session_id, match_index)`.
- `ExporterService` consumes `match-boundaries.jsonl` + `subtitle-assets.jsonl`
  and writes one MP4 per `(session_id, match_index)`.
- `postprocess-reset` removes per-session generated rows/state/files.

## Boundary End Design

`SegmenterService` should continue to use sorted `in_game` hints as match
starts, but for each match it should search for the earliest valid `post_game`
hint after that start and before the next `in_game` start (or recording end).

Boundary end selection:

- candidate end = next `in_game` start, or recording duration for the final
  match.
- if a valid `post_game` hint is within `(start, candidate_end]`, use the first
  such `post_game` time as `ended_at_seconds`.
- ignore out-of-range or non-positive post-game hints.
- keep fallback behavior unchanged when no valid `in_game` exists.

This preserves existing multi-match behavior while making manually or
semantically detected game-over moments truncate the prior match.

## Conservative Highlight Plan

Add a new postprocess stage after `subtitles` and before `exporter`.

Proposed files:

- `data/tmp/highlight-plans.jsonl`
- `data/tmp/highlight-planner-state.json`

Proposed models:

- `HighlightClipWindow`
  - `started_at_seconds`
  - `ended_at_seconds`
  - `reason`
- `HighlightPlanAsset`
  - `session_id`
  - `match_index`
  - `source_boundary_start_seconds`
  - `source_boundary_end_seconds`
  - `windows`
  - `created_at`

Plan generation:

- Parse the SRT for the match.
- Build candidate keep windows around subtitle cues because live narration is a
  conservative proxy for story value.
- Add extra priority around cues with highlight keywords such as kill/death,
  fight, dragon/baron, tower, base/nexus, victory/defeat, and game-over.
- Add padding around all retained cues to avoid abrupt transitions.
- Merge windows separated by a small gap so the result does not jump every few
  seconds.
- Only write a plan when it produces meaningful but conservative reduction.
  Otherwise omit the plan and let exporter keep the full boundary.

Safety defaults:

- no subtitle file or no cue data -> no highlight plan;
- resulting retained duration too short -> no highlight plan;
- too many tiny windows -> merge until transitions are sparse;
- preserve start/end context for a match when possible.

## Export Design

`ExporterService` should optionally read `highlight-plans.jsonl`.

- If no plan exists for `(session_id, match_index)`, current export behavior
  remains unchanged.
- If a plan exists, build a filtergraph that:
  - seeks to the original match boundary;
  - burns subtitles before cutting;
  - keeps only planned windows using `select` / `aselect`;
  - resets timestamps with `setpts` / `asetpts`;
  - uses the configured video codec / CRF / preset.

The output remains the existing `ExportAsset`, so status, copywriter, and
publishing flows continue to discover the final MP4 the same way.

## Reset And Idempotency

`postprocess-reset` should remove `highlight-plans.jsonl` rows for the target
session and clear the planner state keys. It should not delete source
recordings.

The planner should skip already processed keys only when a matching plan row
still exists, mirroring the project convention that processed state is not
stronger than output existence.

## Tests

- Segmenter post-game truncation:
  - one match with `in_game` + `post_game`;
  - two matches where first `post_game` is before second `in_game`;
  - out-of-range `post_game` ignored.
- Highlight planner:
  - subtitle cues with long silent gaps produce merged keep windows;
  - subtitle cues with no meaningful reduction produce no plan;
  - missing subtitle path does not mark the key processed.
- Exporter:
  - plan-backed export includes `select`, `aselect`, `setpts`, and `asetpts`;
  - no plan uses the current command path.
- Reset:
  - per-session reset removes highlight plan rows/state while preserving other
    sessions.

## Operational Notes

- Conservative plan defaults should be configurable through settings/env, but
  the first implementation can choose safe defaults and document them in
  `config.py`.
- H.265 export may need a higher ffmpeg timeout for long condensed outputs;
  this task should not depend on H.265-specific behavior.
