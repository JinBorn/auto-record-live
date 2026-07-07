# Teaser robustness and transition implementation plan

## Checklist

1. Config and contracts
   - Add editing settings/envs for dynamic budget, candidate reasons,
     fallback enablement, transition mode, transition duration/text, and
     optional transition SFX.
   - Publish preset sets `transition_mode="black_card"` unless explicitly
     overridden.
   - Extend `TimelineSegment` with optional `text` and `duration_seconds`.
   - Keep old timeline rows loadable.
   - Add config tests for env parsing/clamping and publish preset behavior.

2. Teaser selection
   - Replace `_PRIMARY_TEASER_REASONS` with configurable candidate reasons.
   - Score `highlight_keyword`, `condensed_key_event`, and LLM candidates.
   - Compute dynamic total teaser budget from match/export duration:
     8-12% target, clamped 20-90s, then bounded by existing max-total env.
   - Preserve LLM recommendation priority and hook-line use.
   - Add non-empty fallback when at least one valid candidate exists.
   - Add logs distinguishing `no_usable_teaser_candidates` from
     `teaser_fallback_top_scored`.
   - Update stale-plan freshness checks to include new expected teaser windows.

3. Transition plan shape
   - Insert one `TimelineSegment(role="transition")` between leading teaser
     segments and first main segment when `transition_mode="black_card"` and
     at least one teaser exists.
   - Use `semantic_asset.result.hook_line` when present; otherwise use
     configured `transition_text`.
   - Add optional whoosh `SoundEffectHit` at transition start time when a valid
     configured/library asset exists.
   - Ensure BGM start remains at the first main segment, not teaser or
     transition, preserving the 06-30 policy.
   - Update `_leading_teaser_duration` or add a main-start helper so transition
     duration is included before BGM start.
   - Update `_edit_plan_has_current_audio_shape`,
     `_edit_plan_has_current_teaser_shape`, and any transition freshness helper.

4. Exporter rendering
   - Extend `_valid_edit_plan()` to accept `transition` only with valid
     `duration_seconds`, no source path, no transform, and supported reason.
   - Extend edit-plan filtergraph construction to emit generated black-card
     video/audio labels for transition segments.
   - Keep chunked edit-plan rendering behavior aligned with single-file
     rendering.
   - Keep subtitle retiming from generating cues during transition segments.
   - Ensure audio mix input indexes still offset correctly when transition
     segments add no media inputs.

5. Tests
   - Editing planner:
     - `condensed_key_event` emits teaser via fallback instead of main-only.
     - dynamic budget caps total teaser duration.
     - LLM `hook_line` populates transition text.
     - transition segment appears between teaser and main.
     - transition mode `none` preserves previous no-card shape.
     - stale pre-transition plans regenerate.
     - BGM starts at main after teaser+transition.
   - Exporter:
     - black-card transition adds `color`, optional `drawtext`, `anullsrc`, and
       concat labels in the expected order.
     - invalid transition segment falls back to non-edit-plan export.
     - chunked edit plans with transition render correctly.
     - audio mix with transition and whoosh keeps input labels/indexes correct.
   - Config:
     - env parsing/clamping.
     - publish preset default.
   - Quality report:
     - existing teaser metrics still detect teaser presence and duration.

6. Documentation and spec
   - Update `.env.example` and README with teaser candidate/fallback and
     transition settings.
   - Update backend orchestration/export contracts with the new timeline role,
     generated card behavior, and validation matrix.

7. Validation
   - Focused:
     ```powershell
     .\.venv\Scripts\python.exe -m pytest tests/pipeline/test_editing_service.py tests/pipeline/test_ffmpeg_resilience.py tests/test_config.py
     ```
   - Broader:
     ```powershell
     .\.venv\Scripts\python.exe -m pytest tests
     .\.venv\Scripts\python.exe -m compileall src tests
     ```
   - If runtime media validation is feasible:
     ```powershell
     .\.venv\Scripts\python.exe -m arl.cli edit-planner --force-reprocess --session-id session-20260702092321-bc90812b
     .\.venv\Scripts\python.exe -m arl.cli exporter --force-reprocess --session-id session-20260702092321-bc90812b
     .\.venv\Scripts\python.exe -m arl.cli quality-report --session-id session-20260702092321-bc90812b --all-latest --top-gaps 5
     ```

## Risky Files

- `src/arl/shared/contracts.py`
  - Cross-stage timeline schema. Keep additions optional and backward-compatible.
- `src/arl/editing/service.py`
  - Teaser scoring, freshness checks, audio timing, and BGM start policy are
    tightly coupled.
- `src/arl/exporter/service.py`
  - FFmpeg filtergraph construction is label/index sensitive, especially with
    audio beds and SFX.
- `src/arl/subtitles/retime.py`
  - Transition segments should contribute timeline time but no source subtitle
    windows.

## Review Gate Before Start

- Confirm v1 implements `black_card` and `none`; `crossfade` may remain a
  reserved documented mode unless implementation stays small.
- Confirm fallback teaser can use `condensed_key_event` even with zero subtitle
  keyword score when it is the top available candidate.
- Confirm transition text default may be configured in env and LLM `hook_line`
  wins when present.

## Rollback Points

- Disable transition via `ARL_EDIT_TRANSITION_MODE=none`.
- Disable expanded/fallback teaser candidates via env to restore old behavior.
- Disable edit-plan export with `ARL_EXPORT_USE_EDIT_PLANS=0`.

## Execution Notes

- Implemented v1 `none` and `black_card`; `crossfade` remains reserved.
- Focused tests passed:
  `python -m pytest tests/test_config.py tests/pipeline/test_editing_service.py tests/pipeline/test_ffmpeg_resilience.py tests/pipeline/test_quality_report_service.py`
- Full tests passed: `python -m pytest tests` (666 passed).
- Compile check passed: `python -m compileall src tests`.
- Runtime edit-planner validation with publish preset on 07-02 sample data
  regenerated 3 complete matches; all 3 latest edit plans include teaser
  segments and one black-card transition. Two other matched boundaries were
  skipped as incomplete.
- `quality-report --session-id session-20260702092321-bc90812b --all-latest`
  timed out locally before producing a report, so rendered media spot-check is
  still a follow-up validation item.
