# Cover visual upgrade design

## Scope

This task upgrades the existing copywriter cover path. It does not introduce a
new postprocess stage and it does not let the cover renderer create new
copywriting text.

The copywriter remains the owner of publishing packages and cover artifacts.
Frame selection, typography layout, candidate rendering, and published-package
copying should stay under `src/arl/copywriter/` unless a small reusable image
metric helper clearly belongs elsewhere.

## Current facts from the repo

- `render_cover()` currently extracts one frame with ffmpeg, fits it to
  1920x1080, darkens it, draws cover lines, and writes one JPEG at quality 92.
- `CopywriterService._render_cover_if_possible()` prefers the source recording
  and uses `_cover_source_time()` to choose one timestamp. If no recording is
  available, it falls back to the exported video at `0.0`.
- `PublishingPackage` already has legacy/default fields:
  `cover_path`, `published_cover_path`, and `published_metadata_path`.
- Final publishing runs after semantic copywriting, edit planning, and export.
  The service can read `edit-plans.jsonl` if teaser or rendered-timeline
  context is useful, while still degrading when the file is absent.
- Existing tests already inject a fake `cover_renderer`, so the renderer
  boundary should stay easy to stub.

## Data flow

```text
SubtitleAsset + HighlightPlanAsset + optional EditPlanAsset
  + MatchBoundary + RecordingAsset/ExportAsset + PublishingPackage.cover_lines
  -> collect source-time candidate seeds
  -> sample/score nearby frames when recording media is available
  -> choose 2-3 distinct ranked candidates
  -> render cover-01.jpg, cover-02.jpg, ...
  -> keep cover_path pointing at cover-01.jpg
  -> copy all candidates into the published package directory
  -> write publishing JSON and upload.txt metadata listing all candidates
```

If sampling/scoring cannot run, the service should keep the current behavior:
render a single default cover at the evidence/highlight fallback timestamp, or
skip cover rendering without failing the copywriter run when prerequisites are
missing.

## Contracts

Extend `PublishingPackage` additively. Existing rows without the new fields
must remain loadable.

Recommended model shape:

```python
class CoverCandidate(BaseModel):
    path: str
    rank: int
    source_timestamp_seconds: float = 0.0
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    published_path: str | None = None
```

Then add:

```python
cover_candidates: list[CoverCandidate] = Field(default_factory=list)
```

Compatibility rules:

- `cover_path` is the path of rank 1 and remains the default cover for existing
  consumers.
- `published_cover_path` is the published copy of rank 1.
- `cover_candidates[*].published_path` is filled after copying into the
  published package directory.
- Existing publishing JSON and `publishing-packages.jsonl` rows keep loading
  because the new fields default empty.

## Candidate seeds

Collect candidate seed timestamps in source-recording seconds when a recording
is available:

1. Parsed KDA kill cue timestamps from subtitle `kda_change ... current_at=...`
   lines, using the same source-time interpretation as editing/SFX.
2. Teaser and high-signal edit-plan segments when `edit-plans.jsonl` has a
   matching plan. Use their source windows, not rendered output seconds, when
   the source path is the original recording.
3. Highlight windows, preferring `highlight_keyword`,
   `condensed_key_event`, and `condensed_tactical`.
4. Existing evidence timestamp fallback via `_cover_source_time()`.

For each seed, sample a small window around it, for example `seed +/- 2s` at a
sub-second or one-second interval. Merge duplicate seeds and enforce a minimum
spacing after scoring so selected covers show distinct moments.

When only an exported video is available, do not attempt source-time mapping
unless the implementation can prove the mapping from edit plan. Rendering one
legacy fallback candidate from export time `0.0` is acceptable degradation.

## Frame scoring

Use cheap deterministic metrics on sampled frames:

- sharpness: variance of Laplacian or equivalent edge-strength metric.
- brightness: prefer readable mid-bright frames; penalize very dark or blown
  out frames.
- scene class: prefer `in_game` from the existing scene classifier; penalize
  `loading` and low-confidence/non-game frames.
- chat activity: reuse the zoom task's bottom-left chat-region crop/diff
  heuristic when adjacent sampled frames are available. Treat this as a bonus,
  not a hard requirement.
- event priority: KDA and teaser/high-signal highlight seeds outrank generic
  fallback seeds when visual scores are close.

The scoring helper should be unit-testable with synthetic frame arrays and no
ffmpeg/network access. Media sampling failures should log one compact skip line
and fall back to the legacy timestamp path.

## Rendering and typography

Keep `render_cover()` as the external renderer seam, but allow rendering
multiple output paths by calling it per ranked candidate.

Typography target:

- 1920x1080 JPEG, quality 92.
- 2-4 `package.cover_lines`, consumed as-is from copywriter/LLM output.
- Stacked, left-aligned headline lines.
- Default fill `#FFEE00` for every headline line, heavy black stroke.
- Auto-fit each line to the safe text box. Keep line fitting and y placement in
  pure helper logic that can be tested with fake `ImageDraw`/font objects.
- Keep text outside the bottom title strip and bottom-right duration badge.
  Use conservative safe bounds roughly: left 8%, right 68%, top 42%, bottom no
  lower than 86% of frame height.

Do not add template sticker packs, facecam cutouts, or OCR-based chat text
reading in this task.

## Published package layout

Generated processed files should use stable names:

```text
data/processed/<session>/match-<NN>-cover-01.jpg
data/processed/<session>/match-<NN>-cover-02.jpg
data/processed/<session>/match-<NN>-cover-03.jpg
```

The published package directory should copy the same candidate set as:

```text
cover.jpg        # alias/copy of rank 1 for legacy upload workflow
cover-01.jpg
cover-02.jpg
cover-03.jpg
upload.txt
video.mp4
```

`upload.txt` should list candidate paths after the existing cover lines. The
first candidate remains the default cover.

## Compatibility and rollback

- The change is additive to `PublishingPackage`.
- Missing Pillow, ffmpeg, cv2, fonts, recording media, or edit plans must not
  fail the copywriter run.
- If multi-candidate rendering fails after one candidate succeeds, keep the
  successful candidate rows and log the skipped ranks.
- If all candidate rendering fails, keep `cover_path=None` as today.
- Rollback is deleting/regenerating publishing package rows plus processed
  cover files; no source media is mutated.

## Tradeoffs

- Keeping cover generation inside copywriter avoids a new stage manifest and
  status contract, which would be overkill for an upgrade to an existing
  publishing artifact.
- The frame selector is heuristic. It should be deterministic and transparent
  rather than expensive or model-driven.
- Additive metadata is slightly more verbose, but it avoids breaking existing
  consumers that expect a single default `cover_path`.
