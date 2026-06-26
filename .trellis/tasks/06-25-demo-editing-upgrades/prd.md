# Demo editing upgrades

## Goal

Analyze the two downloaded Bilibili reference edits in `data/demo1` and
`data/demo2`, then turn the implementable editing patterns into a staged
development plan for the local postprocess pipeline.

## User Value

The current pipeline can record, segment, subtitle, create highlight plans, export
videos, and generate basic publishing copy. The references show a more polished
Bilibili-style package: a high-retention title/cover, a teaser before the main
video, readable subtitles, playful audio, and occasional zoom emphasis. Adding
these as explicit postprocess features should make exports closer to upload-ready
long-form videos instead of raw match cuts.

## Evidence From References

- `demo1` video: 1920x1080 H.264/AAC, about 14:44, 743 MB. Cover image is
  about 1989x1118 and uses a gameplay background, large yellow/black headline
  text, and a streamer cutout.
- `demo2` video: 1920x1080 H.264/AAC, about 7:38, 479 MB. Cover image is
  1920x1080 and uses a tighter gameplay/chat background plus large stacked
  yellow/black headline text.
- Both references include downloaded ASS subtitles. The Chinese subtitle style is
  one bottom-centered style: SimHei/black font, size 36 on 1280x720 play
  resolution, white text, thin black outline, bottom margin 20.
- `demo2` starts with a teaser section of roughly two minutes, then returns to
  earlier/main match context around 2:17. Its teaser includes gameplay moments,
  champion-select/loading context, and an external classic-film insert, but that
  insert pattern is deferred from the current implementation scope.
- `demo1` is more linear: dense top-lane gameplay, subtitles, facecam retained,
  and a cover/title built around the streamer's joke.
- Basic volume probes show full-mix mean volume around -21 to -24 dB for quieter
  intro/development sections and around -18 dB later in `demo2`; this does not
  isolate background music from voice/game audio.

## Existing Project Facts

- `CopywriterService` exists but currently builds simple title/description/tag
  drafts from the first subtitle lines.
- `SubtitleService` outputs SRT assets, and `ExporterService` can burn subtitles,
  but ASS style generation is not currently a first-class output.
- `HighlightPlannerService` and `ExporterService` already support highlight and
  condensed windows through `HighlightPlanAsset`.
- Past condensed-editing work fixed a critical issue where incomplete condensed
  plans could cause exports to start mid-game. Teaser/highlight-first behavior
  must therefore be represented explicitly instead of weakening match-boundary
  rules.
- No current module generates cover images, mixes background music, adds sound
  effects, or applies punch-in zooms.

## Requirements

- Generate stronger publishing metadata: title candidates, short summary,
  cover-text lines, tags, and the source evidence used for those suggestions.
- Render an optional Bilibili-style cover image from a selected frame, generated
  text layout, and optional streamer/facecam cutout or crop.
- Generate ASS subtitles or ASS styling from existing SRT cues and burn them in a
  low bottom position that does not cover core gameplay UI.
- Support a teaser-before-main export mode where teaser segments are explicit
  timeline entries and the main match segment still starts at its validated
  boundary.
- Support configurable background music beds using local music files only, with
  low default gain and stage-aware track changes.
- Support configurable sound-effect hits using local audio files only, initially
  driven by keyword/manual event rules rather than unreliable emotion detection.
- Support punch-in zoom transforms on selected highlight windows, with safe
  defaults that preserve readable HUD context.

## Out Of Scope

- External reference inserts / "引经据典" clips are removed from the current
  implementation scope because the feature is comparatively costly and not
  required for the next iteration.
- Automatically downloading movie/TV/game clips from the internet.
- Automatically deciding that a copyrighted classic-film quote is appropriate.
- Perfect semantic music selection without a curated local music library.
- Fully automatic "wow" reaction placement based only on raw audio emotion.
- Replacing validated match boundary detection or allowing condensed plans to
  create mid-game-only main exports.

## Acceptance Criteria

- [ ] Existing full/highlight/condensed exports remain unchanged unless a new
      editing-package feature flag is enabled.
- [ ] A new planning artifact can represent teaser, main, zoom, subtitle, music,
      and sound-effect instructions without overloading `HighlightPlanAsset`.
- [ ] Teaser-before-main exports preserve a complete main segment boundary and
      never mark a teaser segment as the canonical match start.
- [ ] Cover/copy output includes title, summary, cover text lines, tags, and
      evidence fields derived from transcript/title/highlight data.
- [ ] ASS subtitle export can reproduce the reference bottom-centered style in
      tests and is wired into ffmpeg rendering.
- [ ] Audio features require local asset paths and fail closed when assets are
      missing.
- [ ] Tests cover plan generation, ffmpeg command construction, and opt-in
      backward compatibility.
