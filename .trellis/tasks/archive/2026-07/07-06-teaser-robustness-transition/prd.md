# Teaser robustness and transition

## Goal

Make the cold-open teaser reliably present, dynamically sized, and clearly
separated from the main content by a transition, matching the demo2 pattern of
"best moments first, then return to the match start".

## User Value

Demo2 opens with its strongest moments to hook viewers. Our current teaser
only accepts `highlight_keyword` windows and emitted nothing on all 4
validation samples, so exports start cold at minute zero with no hook.

## Requirements

- Candidate expansion: teaser candidates include `condensed_key_event`
  windows and high-signal KDA-scored windows in addition to
  `highlight_keyword`; keyword windows keep priority when scores tie.
- Non-empty guarantee: when at least one valid candidate window exists, the
  teaser is never empty — if no candidate passes the signal threshold, select
  the top-scored key-event window(s) as fallback (with a distinct log reason).
  Plans may still be main-only when the match genuinely has no usable windows.
- LLM hints: when the semantic-hints asset from `07-06-llm-copywriting-engine`
  exists, its recommended windows get a scoring bonus and its `hook_line`
  feeds the transition card; absence of the asset must leave a fully
  functional heuristic path.
- Dynamic duration (user decision: dynamic, never long): total teaser budget =
  clamp(8-12% of planned export duration, 20s, 90s); per-segment minimum stays
  configurable; existing `ARL_EDIT_TEASER_*` envs keep working with new caps
  documented.
- Transition into main: configurable mode `none | black_card | crossfade`
  (default `black_card` under the publish preset): a 1.0-1.5s dark title card
  between the last teaser segment and the first main segment, rendering either
  the LLM `hook_line` or a static configurable text (e.g. "回到对局开始"),
  plus an optional whoosh SFX sourced from the `data/sfx` library when
  present. Card rendering must reuse the existing ffmpeg concat pipeline
  (no new renderer).
- Boundary invariants preserved: teaser segments are never the canonical match
  start; main starts at the validated boundary; BGM continues to start with
  main content (per 06-30 decision) unless the BGM task changes that policy
  explicitly.
- Timeline/exporter contract changes (transition segment type or equivalent)
  are documented in shared contracts and covered by plan-shape reconciliation
  logic (`_edit_plan_has_current_*` freshness checks must account for the new
  shape so stale plans regenerate).

## Out Of Scope

- External film/quote inserts inside the teaser (still deferred).
- Champion-select/loading-screen context clips in the teaser.
- Teaser-specific BGM arrangement (owned by `07-06-bgm-arrangement-enhance`).

## Acceptance Criteria

- [ ] Regenerating the 4 validation samples yields a teaser on >=3 of them,
      each within the dynamic budget, verified via the quality-report CLI.
- [ ] A black-card transition (with configured text) appears between teaser
      and main in the rendered export; filtergraph/unit tests assert the card
      segment, and a manual spot check confirms it visually.
- [ ] Main-segment start time equals the validated boundary on every sample
      (existing boundary tests keep passing).
- [ ] With transition mode `none` and expansion flags off, plans are
      byte-compatible with current behavior.
- [ ] Plan freshness checks regenerate stale pre-change plans instead of
      reusing them.

## Notes

- Complex task: `design.md` (candidate scoring, transition segment contract,
  card rendering approach) + `implement.md` before start.
- Depends on: quality-report CLI (measurement); optionally consumes the LLM
  hints contract. Can start before the LLM task ships by building the
  heuristic path first.
