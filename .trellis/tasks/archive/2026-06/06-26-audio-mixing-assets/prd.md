# Background music and sound-effect asset mixing

## Goal

Add opt-in local background music and sound-effect mixing for edit-plan exports,
using only user-provided audio assets and preserving existing export behavior
when audio mixing is disabled or no valid assets are configured.

This is the fourth child task under `06-25-demo-editing-upgrades`.

## User Value

The Bilibili reference edits use low-volume playful background music and
occasional reaction sound effects to make long gameplay edits feel less raw.
The local pipeline now has explicit edit plans and teaser-before-main rendering,
so the next implementable step is to let those edit plans carry audio
instructions that the exporter can render.

## Confirmed Facts

- `EditPlanAsset` already exists in `src/arl/shared/contracts.py`.
- `EditPlanAsset.audio_beds` and `EditPlanAsset.sound_effects` currently exist
  as future extension fields.
- `ExporterService` currently rejects non-empty `audio_beds` or `sound_effects`
  as unsupported and falls back to the existing highlight/full export path.
- `ExporterService` edit-plan rendering already uses `filter_complex` with
  `trim` / `atrim` / `concat`, so audio mixing should extend that path rather
  than the default stream-copy path.
- The repo has no bundled BGM or SFX library, and `data/` demo material is
  user-local reference input that should not be committed.
- Parent task constraints require audio features to use local asset paths and
  fail closed when assets are missing.
- Product decision: MVP supports explicit local audio file paths only. It does
  not scan directories or auto-select from a music/SFX library.

## Requirements

- Add typed audio instruction models instead of leaving `audio_beds` and
  `sound_effects` as untyped dictionaries.
- Add opt-in config for local audio assets:
  - explicit background music path(s)
  - explicit sound-effect path(s)
  - conservative default gain values
  - stage/role-aware BGM behavior for teaser vs main segments
- Planner behavior:
  - when audio mixing is disabled, continue writing edit plans with no audio
    instructions
  - when enabled and configured assets exist, add audio instructions to
    `EditPlanAsset`
  - when configured assets are missing or invalid, skip audio instructions and
    do not block teaser/main edit-plan generation
- Exporter behavior:
  - render audio instructions only when edit-plan export is enabled and a valid
    edit plan exists
  - mix BGM at low default volume under original audio
  - add SFX hits at explicit or deterministic timeline positions only
  - reject unsupported or unsafe audio instructions and fall back without
    breaking existing exports
- Keep all audio sources local. Do not download or generate copyrighted/music
  assets in this task.
- Preserve existing full export, highlight export, subtitle burn-in, and edit
  plan teaser rendering behavior when audio mixing is disabled.
- Do not scan asset directories or choose audio files automatically in this
  child task.

## Acceptance Criteria

- [ ] Audio instruction contracts are typed and validated.
- [ ] Audio settings load from env with conservative defaults and disabled-by-
      default behavior.
- [ ] Edit planner emits no audio instructions unless audio mixing is enabled
      and configured local assets are present.
- [ ] Missing configured BGM/SFX files do not mark the match failed and do not
      prevent base edit-plan generation.
- [ ] Exporter builds an FFmpeg filter graph that mixes original audio with BGM
      and optional SFX only for supported edit plans.
- [ ] Exporter falls back to existing behavior for invalid/unsupported audio
      plans.
- [ ] Existing default exports and edit-plan exports without audio instructions
      remain unchanged.
- [ ] Focused tests cover config loading, planner asset validation, exporter
      command construction, missing asset skip/fallback, and backward
      compatibility.

## Out Of Scope

- Bundling music or sound-effect assets in the repo.
- Downloading music/SFX from the internet.
- Automatic emotion detection from raw audio.
- Sophisticated ducking/sidechain compression unless it can be added safely in
  the MVP without broad command complexity.
- Punch-in zoom transforms.
- External reference/video inserts.
- Directory/library scanning and automatic asset selection.

## Open Questions

- None blocking. MVP decision: explicit local audio paths only.
