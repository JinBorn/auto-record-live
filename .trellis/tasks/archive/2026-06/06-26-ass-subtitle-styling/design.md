# Technical Design: ASS Subtitle Styling And Export Wiring

## Overview

Keep SRT as the subtitle stage's durable ASR interchange format, then add an
opt-in ASS burn-in path at exporter render time.

The exporter already has three important compatibility branches:

- burn disabled: stream-copy video/audio and mux real SRT as `mov_text`
- burn enabled: use ffmpeg's `subtitles=` filter against the subtitle path
- placeholder SRT: do not burn subtitles

This task should add a fourth branch without changing the first three:
`burn_subtitles=True` and `use_ass_subtitles=True` converts the real SRT to an
ASS sidecar and passes that sidecar to `subtitles=`.

## Module Boundaries

- `src/arl/subtitles/ass.py`
  - Owns SRT parsing, ASS escaping, style defaults, and ASS document generation.
  - Does not read manifests or run ffmpeg.
- `src/arl/exporter/service.py`
  - Owns deciding whether ASS is used for a given export command.
  - Writes derived `.ass` sidecars next to the existing SRT in
    `storage.processed_dir/<session_id>/`.
- `src/arl/config.py`
  - Owns environment loading and typed settings for the new opt-in flag and
    style values.

## Config Contract

Add export settings with conservative defaults:

```python
class ExportSettings(BaseModel):
    use_ass_subtitles: bool = False
    ass_font_name: str = "SimHei"
    ass_font_size: int = 36
    ass_margin_v: int = 20
    ass_outline: int = 2
```

Environment keys:

- `ARL_EXPORT_USE_ASS_SUBTITLES` (`0`/`1`, default `0`)
- `ARL_EXPORT_ASS_FONT_NAME` (default `SimHei`)
- `ARL_EXPORT_ASS_FONT_SIZE` (default `36`, minimum `1`)
- `ARL_EXPORT_ASS_MARGIN_V` (default `20`, minimum `0`)
- `ARL_EXPORT_ASS_OUTLINE` (default `2`, minimum `0`)

## Data Flow

1. `SubtitleService` writes `match-NN.srt` and one `SubtitleAsset(format="srt")`.
2. `ExporterService` loads the existing `SubtitleAsset`.
3. If subtitle burn-in is disabled, existing stream-copy + `mov_text` behavior
   continues and no ASS file is generated.
4. If subtitle burn-in is enabled but ASS is disabled, existing SRT filter
   behavior continues.
5. If subtitle burn-in and ASS are enabled:
   - exporter converts the SRT to `match-NN.ass`
   - exporter passes that `.ass` path through the existing escaped
     `subtitles='...'` filter helper
   - export failure handling remains unchanged

The `.ass` file is a derived render sidecar, not a second `SubtitleAsset` row.
`postprocess-reset` already removes orphan files under
`storage.processed_dir/<session_id>/`, so reset can clean sidecars without a new
manifest contract.

## ASS Style

Reference-style defaults:

- `PlayResX: 1280`
- `PlayResY: 720`
- `Style: Default,SimHei,36,&H00FFFFFF,...,&H00000000,...,Alignment=2,MarginV=20`
- one `Dialogue:` row per SRT cue

ASS text escaping should:

- replace literal newlines inside a cue with `\N`
- escape `{` and `}` so subtitle text is not treated as ASS override tags
- keep Chinese punctuation and normal spaces intact

## Compatibility

- No existing CLI command changes.
- `ARL_EXPORT_USE_ASS_SUBTITLES=0` preserves all current exporter command tests.
- The existing `ARL_EXPORT_BURN_SUBTITLES=1` behavior remains SRT burn-in unless
  the new ASS flag is also enabled.
- Placeholder SRT detection remains authoritative; placeholder subtitles do not
  burn, regardless of ASS settings.
- Existing `SubtitleAsset` rows remain compatible and are not duplicated.

## Risks

- ffmpeg subtitle filter path escaping is already Windows-sensitive. Reuse the
  existing `_subtitle_filter_arg` helper for `.ass` paths.
- ASS style verification should assert text fields, not a machine-specific font
  render result.
- Reprocessing should overwrite stale `.ass` sidecars so style config changes
  are picked up when exporter reruns.
