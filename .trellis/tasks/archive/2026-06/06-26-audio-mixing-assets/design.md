# Technical Design: Background Music And Sound-Effect Asset Mixing

## Overview

Extend edit plans with typed local-audio instructions and teach the edit-plan
exporter path to mix those instructions into the concatenated teaser/main audio.

The feature remains opt-in at two levels:

- planner opt-in: emit audio instructions only when audio mixing is enabled and
  explicit local files exist
- exporter opt-in: render audio instructions only through the existing
  `ARL_EXPORT_USE_EDIT_PLANS=1` edit-plan path

Default full exports, highlight exports, subtitle behavior, and audio-free
edit-plan exports must remain unchanged.

## Module Boundaries

- `src/arl/shared/contracts.py`
  - Owns `AudioBed`, `SoundEffectHit`, and the typed `EditPlanAsset` fields.
- `src/arl/config.py`
  - Owns audio-mixing settings and env loading.
- `src/arl/editing/service.py`
  - Owns choosing whether to add audio instructions to generated edit plans.
- `src/arl/exporter/service.py`
  - Owns validating audio instructions and constructing the FFmpeg filter graph.
- `src/arl/status/service.py`
  - No new status section required for MVP; edit-plan counts already surface.
- `.trellis/spec/backend/export-configuration.md`
  - Owns the executable contract for opt-in audio mixing behavior.

## Contracts

### Config

Add audio settings under editing, keeping all defaults disabled/empty:

```python
class EditingSettings(BaseModel):
    enabled: bool = False
    teaser_max_segments: int = 2
    teaser_max_total_seconds: float = 45.0
    teaser_min_segment_seconds: float = 3.0
    audio_mixing_enabled: bool = False
    bgm_path: Path | None = None
    bgm_gain_db: float = -24.0
    sfx_path: Path | None = None
    sfx_gain_db: float = -12.0
```

Environment:

- `ARL_EDIT_AUDIO_MIXING_ENABLED` (`0`/`1`, default `0`)
- `ARL_EDIT_BGM_PATH` (explicit local audio file path, default empty)
- `ARL_EDIT_BGM_GAIN_DB` (default `-24.0`, clamp `[-60.0, 0.0]`)
- `ARL_EDIT_SFX_PATH` (explicit local audio file path, default empty)
- `ARL_EDIT_SFX_GAIN_DB` (default `-12.0`, clamp `[-60.0, 6.0]`)

### Audio Models

Replace placeholder `list[dict[str, object]]` fields with typed models:

```python
class AudioBed(BaseModel):
    source_path: str
    timeline_start_seconds: float = 0.0
    timeline_end_seconds: float | None = None
    gain_db: float = -24.0
    loop: bool = True
    reason: str = "background_music"
```

```python
class SoundEffectHit(BaseModel):
    source_path: str
    at_seconds: float
    gain_db: float = -12.0
    reason: str
```

```python
class EditPlanAsset(BaseModel):
    ...
    audio_beds: list[AudioBed] = Field(default_factory=list)
    sound_effects: list[SoundEffectHit] = Field(default_factory=list)
```

Backward compatibility: existing rows with empty `audio_beds=[]` and
`sound_effects=[]` continue to parse. This task does not need to support legacy
non-empty dict payloads because no previous renderer emitted them.

## Planner Behavior

Inputs:

- complete `MatchBoundary`
- matching `HighlightPlanAsset`
- explicit local audio config

Behavior:

1. Preserve current teaser/main generation.
2. If `settings.editing.audio_mixing_enabled` is false, emit no audio fields.
3. If `bgm_path` is configured and exists as a file, add one `AudioBed`:
   - starts at output timeline `0.0`
   - `timeline_end_seconds=None` means "cover full rendered edit duration"
   - `loop=True`
   - `gain_db=settings.editing.bgm_gain_db`
4. If `sfx_path` is configured and exists as a file, add `SoundEffectHit` rows at
   teaser segment starts where the teaser reason is high-signal:
   `highlight_keyword`, `condensed_key_event`, or `condensed_tactical`.
5. If configured audio files are missing, log the skip and still write the base
   teaser/main edit plan without audio instructions.

This MVP intentionally does not parse subtitle text for "wow" placement. The
reason-based SFX rule gives deterministic behavior and keeps ASR/text heuristics
out of this child task.

## Exporter Behavior

### Validation

A valid audio-enabled edit plan must satisfy existing edit-plan validation plus:

- every audio source path exists and is a file
- audio bed `timeline_start_seconds >= 0`
- audio bed end is `None` or greater than start
- audio bed/hit positions are inside the rendered output duration
- gain values are within configured safety bounds
- only local `source_path` files are supported

Invalid audio instructions make the edit plan unsupported for this task. The
exporter should fall back to existing highlight/full export behavior instead of
attempting a partial mix.

### FFmpeg Shape

The existing edit-plan filter graph currently concatenates per-segment audio to
`[a]`. Audio mixing should change that internal output shape:

1. Concatenate original segment audio to `[basea]`.
2. Add explicit audio asset inputs after the recording input:
   - BGM input uses `-stream_loop -1 -i <bgm_path>` when `loop=True`.
   - SFX inputs use plain `-i <sfx_path>`.
3. Apply gain and timing filters:
   - BGM: `atrim`, `asetpts`, `volume=<linear_gain>`
   - SFX: `adelay=<ms>|<ms>`, `volume=<linear_gain>`
4. Mix:
   - `[basea][bgm0][sfx0]amix=inputs=3:duration=first:dropout_transition=0[a]`
5. Keep `-map [v] -map [a]` and existing video encode/quality args.

The final mix duration must follow the concatenated original audio
(`duration=first`) so a long BGM file cannot extend the MP4.

## Compatibility

- `ARL_EDIT_AUDIO_MIXING_ENABLED=0` keeps edit plans audio-free.
- `ARL_EXPORT_USE_EDIT_PLANS=0` keeps exporter from loading edit plans or audio
  instructions.
- Existing audio-free edit plan command tests should remain valid.
- No audio files are committed to the repo.
- Missing configured audio files degrade to audio-free edit plans at planner
  time; stale/missing audio paths inside an existing plan cause exporter fallback.

## Tests

- Config:
  - audio env values load
  - gain values clamp to safe ranges
  - defaults disabled and paths empty
- Editing service:
  - emits BGM/SFX instructions when enabled and files exist
  - skips missing BGM/SFX files without blocking base edit-plan output
  - emits no audio instructions when disabled
- Exporter:
  - valid audio edit plan adds extra inputs and `amix`
  - BGM input uses `-stream_loop -1`
  - SFX uses `adelay`
  - invalid/missing audio asset falls back
  - audio-free edit-plan commands remain unchanged
- Backward compatibility:
  - full export default unchanged
  - edit-plan disabled ignores audio plans

## Rollback

Disable `ARL_EDIT_AUDIO_MIXING_ENABLED` to stop emitting audio instructions.
Disable `ARL_EXPORT_USE_EDIT_PLANS` to bypass edit-plan rendering entirely.
