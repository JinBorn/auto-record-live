# Implementation Plan: Background Music And Sound-Effect Asset Mixing

## Checklist

- [x] Inspect current edit-plan/exporter command paths:
  - [x] `EditPlanAsset` model and existing audio extension fields
  - [x] `EditingPlannerService` timeline output and idempotency
  - [x] `ExporterService._valid_edit_plan`
  - [x] `ExporterService._edit_plan_ffmpeg_command`
  - [x] config env loading tests
- [x] Add typed audio contracts:
  - [x] `AudioBed`
  - [x] `SoundEffectHit`
  - [x] typed `EditPlanAsset.audio_beds`
  - [x] typed `EditPlanAsset.sound_effects`
- [x] Add config:
  - [x] `EditingSettings.audio_mixing_enabled`
  - [x] `bgm_path`
  - [x] `bgm_gain_db`
  - [x] `sfx_path`
  - [x] `sfx_gain_db`
  - [x] env loading/clamping tests
- [x] Update edit planner:
  - [x] preserve audio-free default output
  - [x] validate configured local BGM path
  - [x] validate configured local SFX path
  - [x] emit one BGM bed for full rendered edit duration
  - [x] emit SFX hits at high-signal teaser starts
  - [x] skip missing assets without marking the plan failed
- [x] Update exporter:
  - [x] validate audio source files and timing
  - [x] compute rendered edit-plan output duration
  - [x] add extra FFmpeg inputs for audio assets
  - [x] loop BGM input when requested
  - [x] build base segment audio as `[basea]`
  - [x] build BGM/SFX filter chains
  - [x] mix with `amix=duration=first`
  - [x] fall back for unsupported audio instructions
- [x] Add focused tests:
  - [x] planner emits audio instructions only when enabled/assets exist
  - [x] planner skips missing audio assets while preserving base plan
  - [x] exporter audio mix command includes extra inputs, volume, delay, amix
  - [x] exporter fallback for missing/stale audio source
  - [x] existing edit-plan and default export command tests remain unchanged
- [x] Update backend export spec with final executable audio contract.
- [x] Run validation.

## Validation Commands

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline\test_editing_service.py tests\pipeline\test_ffmpeg_resilience.py tests\test_config.py -q
```

Then run broader checks:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline tests\test_config.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
```

Also run:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
git diff --check
```

## Risky Files

- `src/arl/exporter/service.py`: FFmpeg filter graphs are regression-sensitive.
  Keep the audio path behind valid edit plans and explicit config.
- `src/arl/shared/contracts.py`: changing `EditPlanAsset` field types affects
  JSONL parsing. Empty legacy lists must continue to parse.
- `src/arl/editing/service.py`: audio asset misses must not prevent base
  teaser/main plan generation.
- `src/arl/config.py`: env loading should clamp gains to safe ranges.

## Rollback Points

- If audio filter graph construction becomes too broad, keep typed models and
  planner emission disabled by default, and defer exporter rendering.
- If SFX placement is noisy, keep BGM support and require SFX explicit config in
  a later manual-event task.

## Review Gate

Before `task.py start`, confirm this MVP scope:

- explicit local BGM/SFX paths only
- no directory scanning or automatic asset library selection
- no bundled audio assets
- SFX placement is deterministic from teaser/highlight reasons, not raw emotion
  detection
- audio mixing is disabled by default and only rendered through edit-plan export
