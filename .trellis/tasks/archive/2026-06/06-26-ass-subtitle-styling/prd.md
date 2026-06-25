# ASS subtitle styling and export wiring

## Goal

Add a Bilibili-style ASS subtitle render path that can turn existing SRT subtitle
assets into low, readable, bottom-centered burned-in subtitles without changing
the default SRT/soft-subtitle exporter behavior.

This is the second child task under `06-25-demo-editing-upgrades`.

## Requirements

- Keep `SubtitleAsset` SRT generation as the canonical ASR interchange output.
- Add deterministic SRT-to-ASS conversion with a reference-inspired style:
  - `PlayResX=1280`, `PlayResY=720`
  - bottom centered alignment
  - white primary text with black outline
  - font size around 36 at 720p
  - low bottom margin around 20
- Preserve SRT cue timing and text during conversion, including HTML-tag cleanup
  behavior that does not corrupt plain Chinese text.
- Wire exporter burn-in to prefer a generated ASS sidecar only when an explicit
  ASS subtitle option is enabled.
- Leave default exporter behavior unchanged:
  - burn disabled still stream-copies video/audio and muxes real SRT as
    `mov_text`
  - burn enabled without ASS option still uses the existing SRT `subtitles=`
    filter path
  - placeholder subtitles still do not burn
- Avoid adding duplicate `SubtitleAsset` rows for the same match just to expose
  the derived ASS file.
- Generate ASS sidecars under the processed session directory and allow
  `postprocess-reset` orphan cleanup to remove them.
- Fail closed: invalid/missing SRT inputs should keep the existing exporter
  skip/defer behavior rather than producing a broken export.

## Acceptance Criteria

- [x] ASS conversion produces a valid `[Script Info]`, `[V4+ Styles]`, and
      `[Events]` file with expected style fields.
- [x] SRT cue timestamps and text survive conversion into ASS dialogue lines.
- [x] Exporter uses the generated `.ass` path in `subtitles=` only when both
      subtitle burn-in and the ASS option are enabled.
- [x] Existing SRT burn-in command construction remains unchanged when the ASS
      option is disabled.
- [x] Existing soft-subtitle / stream-copy export remains unchanged when
      burn-in is disabled.
- [x] Placeholder subtitle files still avoid subtitle burn-in.
- [x] Focused tests cover conversion, exporter command selection, config loading,
      and backward compatibility.

## Notes

- Parent task reference: both demos use ASS subtitles that sit low on screen,
  centered, with white text and a black outline. The implementation should
  reproduce that behavior structurally in tests rather than depend on a
  particular machine font being installed.
- Out of scope: teaser timelines, BGM/SFX, zoom effects, external inserts, ASR
  provider changes, and frontend subtitle review UI.
