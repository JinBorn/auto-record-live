# Publishing metadata and cover assets

## Goal

Improve the existing copywriting output so each processed match can produce
upload-ready metadata and, when possible, an optional Bilibili-style cover image.

## Requirements

- Extend copy generation beyond the first subtitle lines.
- Produce title candidates, a recommended title, summary, cover text lines, tags,
  transcript/highlight evidence, and optional cover path.
- Use existing subtitle, export, recording, boundary, and highlight-plan assets
  as inputs.
- Keep deterministic local heuristics; do not add an LLM dependency.
- Render a cover image only when ffmpeg is available and the source recording can
  provide a frame.
- Keep the existing `CopyAsset` contract compatible for downstream callers.
- Store richer publishing output in a separate JSON artifact so existing copy
  behavior can keep working.
- Fail closed when a recording file is missing, a frame cannot be extracted, or
  image tooling is unavailable.

## Acceptance Criteria

- [ ] Existing `copywriter` command still writes `CopyAsset` rows with title,
      description, tags, subtitle path, and export path.
- [ ] A new publishing package artifact includes `summary`, `cover_lines`, and
      `evidence`.
- [ ] Title and cover-line generation prefers high-signal cues from highlight
      windows when available, then falls back to transcript excerpts.
- [ ] Empty/placeholder subtitles produce a clear placeholder status without
      crashing.
- [ ] Optional cover rendering creates a readable 1920x1080 jpg/png from a source
      frame when ffmpeg and image tooling are available.
- [ ] Missing recording or missing ffmpeg skips cover rendering and still writes
      metadata.
- [ ] Focused tests cover heuristic generation, artifact compatibility, and cover
      skip behavior.

## Notes

- Parent task: `06-25-demo-editing-upgrades`.
- This child task intentionally excludes teaser timelines, ASS subtitles, music,
  sound effects, zooms, and external inserts.
