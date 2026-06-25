# Technical Design: Publishing Metadata And Cover Assets

## Overview

Extend the current copywriter pipeline with a richer publishing artifact while
preserving the existing `CopyAsset` JSONL contract.

The current `CopywriterService` reads subtitle assets and export assets, then
writes one `match-NN-copy.json` file plus a `CopyAsset` row. The new behavior
should keep that compatibility and add a separate publishing package JSON file
with stronger summary, cover text, and evidence fields.

## Data Contract

Add a model under `src/arl/copywriter/models.py`:

```python
class PublishingPackage(BaseModel):
    session_id: str
    match_index: int
    source_subtitle_path: str
    source_export_path: str | None = None
    source_recording_path: str | None = None
    transcript_excerpt: list[str]
    evidence: list[str]
    title_candidates: list[str]
    recommended_title: str
    summary: str
    cover_lines: list[str]
    tags: list[str]
    cover_path: str | None = None
    status: str
    created_at: datetime
```

The service can append package rows to a new JSONL file, for example
`data/tmp/publishing-package-assets.jsonl`, and write per-match JSON under
`data/processed/<session>/match-NN-publishing.json`.

## Heuristic Strategy

Input priority:

1. Meaningful subtitle cues overlapping highlight-plan windows.
2. Meaningful subtitle cues from the full transcript.
3. Existing copy fallback text for placeholder inputs.

Text generation should prefer concrete hooks:

- unusual build/champion/lane terms
- kill/fight/objective cues
- recognition/chat/joke cues
- compact line lengths for cover text

The implementation should be deterministic and local. LLM-backed generation is
out of scope for this child task.

## Cover Rendering

Use a small helper in `src/arl/copywriter/cover.py` or a similar local module.

MVP behavior:

- extract a representative frame with ffmpeg
- render a 1920x1080 image with large yellow text, black stroke/shadow, and
  optional darkened background
- use Pillow if installed; otherwise skip rendering and keep metadata output
- store generated covers under `data/processed/<session>/match-NN-cover.jpg`

The helper should never fail the copywriter run just because cover rendering is
unavailable.

## Compatibility

- `CopyAsset` remains unchanged.
- Existing `copy-assets.jsonl` remains unchanged.
- New package artifact is additive.
- Existing tests for copywriter output should continue to pass.

## Tests

- Rich package is written alongside existing copy output.
- Highlight-window subtitles are preferred over opening transcript lines.
- Placeholder subtitles produce placeholder metadata.
- Missing recording/ffmpeg/Pillow skips cover rendering without failing.
