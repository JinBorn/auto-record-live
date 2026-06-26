# Edit plans with teaser-before-main rendering

## Goal

Add an explicit edit-plan artifact and opt-in render path so exports can place
short teaser clips before the main match while preserving the validated main
match boundary.

This is the third child task under `06-25-demo-editing-upgrades`.

## User Value

The Bilibili references use a high-retention opening: exciting moments are shown
before the main context. The current exporter can apply highlight windows, but
those windows replace the exported timeline. This task should add a separate
presentation timeline so teaser clips can be duplicated at the front without
mutating `MatchBoundary` or weakening the existing condensed-plan safeguards.

## Confirmed Facts

- `HighlightPlanAsset` already represents retained source windows and the
  exporter applies it only when `settings.export.use_highlight_plans` is true.
- Existing highlight-plan export uses `select` / `aselect`, which preserves
  source order and cannot duplicate a later highlight before the full main match.
- Existing default export behavior is full-boundary stream copy with soft SRT
  when subtitle burn-in is disabled.
- Existing subtitle burn-in supports SRT and the new opt-in ASS sidecar path.
- `PostProcessService` currently runs:
  `stage-hints-semantic -> segmenter -> subtitles -> highlight-planner -> exporter -> copywriter`.
- There is no `editing` module, `EditPlanAsset`, `edit-plans.jsonl`, or edit-plan
  exporter path yet.

## Requirements

- Add a durable edit-plan contract separate from `HighlightPlanAsset`.
- Add an edit planner stage that:
  - reads complete `MatchBoundary` rows and matching `HighlightPlanAsset` rows
  - selects teaser windows from valid highlight/condensed windows
  - emits a timeline with teaser segment(s) first and a main segment second
  - keeps all segment times relative to the validated match boundary
  - writes `edit-plans.jsonl` and stage state under `storage.temp_dir`
- The MVP main segment must cover the full validated match boundary from
  relative `0.0` to the boundary duration.
- Add strict validation so an edit plan is ignored when:
  - source boundary metadata does not match the current `MatchBoundary`
  - it contains no `role="main"` segment
  - the first main segment does not start at relative `0.0`
  - any segment has negative, reversed, or out-of-bound times
  - teaser/main ordering is invalid
- Add exporter opt-in behavior:
  - default behavior remains unchanged when edit-plan export is disabled
  - when enabled and a valid edit plan exists, exporter renders teaser + main
    order from the edit plan
  - edit plans take precedence over highlight-plan rendering only when the new
    edit-plan export flag is enabled
  - if an edit plan is missing or invalid, exporter falls back to the existing
    highlight-plan/full-boundary behavior
- Surface edit-plan outputs in local operational tooling:
  - `status` should report edit-plan counts and edit-planner processed state
  - `postprocess-reset` should remove target-session edit-plan rows and
    edit-planner state keys
- Edit-plan rendering may use re-encoding and FFmpeg `filter_complex` because
  teaser-before-main needs timeline reordering and duplication.
- Subtitle burn-in must continue to respect existing subtitle settings:
  - no burn-in when `burn_subtitles` is false
  - no burn-in for placeholder subtitles
  - ASS sidecar path when both ASS and burn-in are enabled
- Do not implement background music, sound effects, punch-in zoom, or external
  insert rendering in this task. The model may reserve explicit fields for
  future tasks, but renderer should ignore unsupported non-empty instructions
  until those features are implemented.

## Acceptance Criteria

- [ ] `EditPlanAsset` can represent teaser/main timeline segments and future
      audio/transform/insert extension points without overloading
      `HighlightPlanAsset`.
- [ ] Edit planner emits teaser segment(s) before one full-boundary main segment
      when a valid highlight plan exists.
- [ ] Planner is idempotent and supports session/match filters and force
      reprocess semantics consistent with nearby postprocess stages.
- [ ] Invalid or missing highlight plans do not create unsafe teaser-only plans.
- [ ] Exporter uses edit-plan rendering only when the new export flag is enabled.
- [ ] Exporter rejects stale, invalid, teaser-only, or mid-game-main edit plans.
- [ ] Existing full export, highlight-plan export, SRT/ASS subtitle burn-in, and
      soft-subtitle stream-copy behavior remain unchanged when edit-plan export
      is disabled.
- [ ] `postprocess` invokes the edit planner after highlight planning and before
      exporter.
- [ ] `status` and `postprocess-reset` understand edit-plan manifests/state.
- [ ] Focused tests cover planner output, validation, exporter command
      construction, config loading, CLI/postprocess wiring, and backward
      compatibility.

## Out Of Scope

- Rendering local BGM or SFX assets.
- Applying punch-in zoom transforms.
- Rendering external reference inserts.
- Automatically choosing movie/meme clips.
- Changing `MatchBoundary` or treating teaser windows as canonical match starts.
- Making condensed main content the default. The MVP uses full-boundary main
  content to avoid reintroducing mid-game-only exports.

## Open Questions

- None blocking. MVP decision: teaser before full validated main segment.
