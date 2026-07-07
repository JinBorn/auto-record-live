# Teaser robustness and transition design

## Architecture

This task extends the existing edit-planner and exporter path. It does not add
a new pipeline stage.

The current pipeline already has the right ownership boundaries:

- `EditingPlannerService` reads match boundaries, highlight plans, optional
  subtitles, optional LLM semantic assets, optional audio libraries, and writes
  `EditPlanAsset` rows to `edit-plans.jsonl`.
- `ExporterService` validates the latest edit plan and renders it through the
  existing FFmpeg `trim` / `atrim` / `concat` path when
  `settings.export.use_edit_plans` is true.
- `CopywriterSemanticAsset` already carries `hook_line` and
  `teaser_recommendations`; the teaser task should consume that contract, not
  create a second LLM artifact.

The implementation should make teaser selection more reliable, then add a
transition timeline segment that the exporter can render as a generated dark
card inside the existing concat filtergraph.

## Module Boundaries

- `src/arl/config.py`
  - Owns new editing env keys, publish-preset defaults, and numeric clamps.
- `src/arl/shared/contracts.py`
  - Owns additive timeline fields / segment roles used by editing and exporter.
- `src/arl/editing/service.py`
  - Owns candidate scoring, non-empty fallback, dynamic teaser budget,
    transition segment insertion, optional whoosh SFX planning, and freshness
    checks.
- `src/arl/exporter/service.py`
  - Owns validation and FFmpeg rendering of transition timeline segments.
  - Keeps fallback-to-full/export behavior when an edit plan is unsupported.
- `src/arl/subtitles/retime.py`
  - Must preserve subtitle retiming across teaser, transition, and main
    timeline segments. Transition card segments have no source subtitle cues.
- `src/arl/quality_report/service.py`
  - Should continue to measure teaser presence and output shape; add assertions
    only if existing metrics cannot detect transition presence.
- `README.md` / `.env.example`
  - Document operator-facing transition mode, dynamic caps, and fallback flags.

## Current Code Facts

- `_PRIMARY_TEASER_REASONS = {"highlight_keyword"}` is why the 07-02 samples
  can emit no teaser when windows are only `condensed_key_event` or KDA-rich.
- `_ZOOM_REASONS` already includes `llm_teaser` and `condensed_key_event`.
- `_SFX_REASONS` currently covers `highlight_keyword` and `condensed_key_event`;
  transition whoosh should be a separate planned hit, not treated as a kill SFX.
- `_select_teaser_windows()` already:
  - takes optional subtitle cues,
  - lets LLM teaser recommendations override heuristic selection,
  - clamps selected windows by `teaser_max_segments` and
    `teaser_max_total_seconds`.
- `_edit_plan_has_current_*` freshness checks already compare expected teaser,
  zoom, and audio shape. They must include dynamic budgets and transition shape
  so stale pre-transition plans regenerate.
- `ExporterService._valid_edit_plan()` currently accepts only `teaser` and
  `main` roles. It must be extended for transition card roles without accepting
  arbitrary inserts.

## Data Flow

```text
HighlightPlanAsset + SubtitleAsset + optional CopywriterSemanticAsset
  -> score teaser candidates
  -> select dynamic teaser windows, with fallback if at least one valid window exists
  -> insert optional transition segment after the last teaser and before main
  -> append EditPlanAsset(timeline=[teaser..., transition?, main...])
  -> exporter validates plan freshness and timeline invariants
  -> ffmpeg trim/atrim teaser/main + generated black card segment + concat
  -> optional sound_effects include whoosh at transition boundary
  -> ExportAsset + quality-report metrics
```

## Contracts

### Config

Add editing settings:

```python
class EditingSettings(BaseModel):
    teaser_dynamic_budget_enabled: bool = True
    teaser_budget_fraction_min: float = 0.08
    teaser_budget_fraction_max: float = 0.12
    teaser_budget_min_seconds: float = 20.0
    teaser_budget_max_seconds: float = 90.0
    teaser_candidate_reasons: tuple[str, ...] = (
        "highlight_keyword",
        "condensed_key_event",
    )
    teaser_fallback_enabled: bool = True
    transition_mode: str = "none"  # "none" | "black_card" | "crossfade"
    transition_duration_seconds: float = 1.25
    transition_text: str = "Back to match start"
    transition_sfx_path: Path | None = None
    transition_sfx_gain_db: float = -12.0
```

Publish preset should set:

```python
editing.transition_mode = "black_card"
```

when the operator did not explicitly choose another transition mode.

Existing `ARL_EDIT_TEASER_MAX_*` envs remain valid. Dynamic budget computes a
per-plan cap and then applies the existing maximum as an upper bound, so
operators can still lower the cap.

### Timeline Segment

Prefer an additive field on `TimelineSegment` rather than a parallel model:

```python
class TimelineSegment(BaseModel):
    role: str  # "teaser" | "transition" | "main"
    source_path: str | None = None
    source_start_seconds: float = 0.0
    source_end_seconds: float = 0.0
    transform: TimelineVideoTransform | None = None
    reason: str
    text: str | None = None
    duration_seconds: float | None = None
```

For `role="transition"`:

- `source_path` must be `None`.
- `source_start_seconds` and `source_end_seconds` are ignored by renderer and
  should be `0.0`.
- `duration_seconds` is required and clamped by config.
- `reason` is `transition_black_card` for `black_card`.
- `text` is either `semantic_asset.result.hook_line` or configured fallback
  transition text.

Crossfade can be accepted as a future mode but should not be implemented as a
silent no-op. In v1, either implement it explicitly or treat it as unsupported
with plan fallback. Recommended v1 scope: implement `none` and `black_card`;
document `crossfade` as reserved.

### Teaser Candidate Scoring

Candidate pools:

1. LLM recommendations snapped to existing highlight windows.
2. Heuristic highlight windows with reasons from `teaser_candidate_reasons`.
3. High-signal KDA/key windows:
   - use `condensed_key_event` windows directly,
   - add subtitle text signal score from overlapping cues,
   - keep `highlight_keyword` priority as a tie-breaker.

Selection:

- Compute dynamic budget:
  - `target = clamp(duration * 0.10, 20, 90)` by default.
  - Keep 8-12% as an allowed range for tests/metrics; the concrete cap can use
    the midpoint.
  - Apply `min(target, settings.editing.teaser_max_total_seconds)` so existing
    env caps still work.
- Pick up to `teaser_max_segments`.
- Respect `teaser_min_segment_seconds`.
- Prefer higher score, then reason priority, then earlier start.
- If no window crosses the threshold but at least one valid candidate exists
  and fallback is enabled, select the top scored candidate with reason such as
  `teaser_fallback_key_event` or log `reason=teaser_fallback_top_scored`.

### Transition Rendering

Black card should be generated inside the FFmpeg filtergraph:

- Create a video segment with `color=c=black:s=<WxH>:r=30:d=<duration>`.
- Create matching silent audio with `anullsrc=channel_layout=stereo:sample_rate=48000`.
- Overlay centered white text with `drawtext` when usable font support exists.
  If font discovery is unreliable on Windows, render a plain black card first
  and keep text as an explicit follow-up. Tests should assert the `color` and
  `drawtext` filter when text mode is enabled.
- Insert the transition segment between the final teaser and first main segment
  in concat order.

Optional whoosh SFX:

- Prefer `settings.editing.transition_sfx_path` when set.
- Else use `data/sfx/library.json` if it contains a `whoosh` asset.
- Missing asset is a no-op, not a plan validation failure.
- The sound effect hit timestamp is the start of the transition segment in
  rendered timeline time.

## Compatibility

- Non-publish defaults remain behavior-compatible:
  - `transition_mode="none"`.
  - Existing edit-plan export remains disabled unless `use_edit_plans=True`.
- With `transition_mode="none"` and candidate expansion disabled, edit plans
  should match the pre-change shape.
- Existing `EditPlanAsset` rows without `duration_seconds` / `text` stay
  loadable because new fields are optional/defaulted.
- Exporter must reject unknown roles or malformed transition segments and fall
  back instead of producing a bad command.
- Main segment invariants remain unchanged: main starts at the validated
  boundary-relative `0.0` and must reach the boundary end.

## Rollback

- Set `ARL_EDIT_TRANSITION_MODE=none` to remove the transition card.
- Set `ARL_EDIT_TEASER_FALLBACK_ENABLED=0` and restrict
  `ARL_EDIT_TEASER_CANDIDATE_REASONS=highlight_keyword` to approximate old
  teaser selection.
- Set `ARL_EXPORT_USE_EDIT_PLANS=0` to bypass edit-plan rendering entirely.
