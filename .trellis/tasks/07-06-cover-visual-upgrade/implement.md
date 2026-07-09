# Cover visual upgrade implementation plan

## Checklist

1. Contract and service inputs
   - Add a `CoverCandidate` model and additive `cover_candidates` field to
     `PublishingPackage` with backward-compatible defaults.
   - Let `CopywriterService` load latest matching `EditPlanAsset` rows from
     `data/tmp/edit-plans.jsonl` during final publishing.
   - Keep existing `cover_renderer` injection usable for tests and preserve
     `cover_path` as the rank-1 candidate.

2. Candidate timestamp collection
   - Add helpers to collect source-time seeds from KDA subtitle cues,
     edit-plan teaser/high-signal segments, highlight windows, and the current
     evidence/highlight fallback.
   - Normalize seeds relative to recording source time when using a recording.
   - Degrade to the legacy single export-time candidate when only an exported
     video is available and source mapping is unavailable.
   - Enforce deterministic ordering and minimum spacing between final selected
     candidates.

3. Frame metrics and ranking
   - Add a small cover-frame scoring helper using synthetic-array-testable
     metrics: sharpness, brightness, scene class, chat-region activity, and
     source event priority.
   - Import cv2/vision helpers lazily so missing optional dependencies do not
     break copywriter import or non-cover paths.
   - Sample narrow windows around seeds and choose the top 2-3 distinct frames
     when sampling succeeds.
   - Log compact degraded behavior only; do not log raw transcript text or
     signed media URLs.

4. Typography rendering
   - Update `_draw_cover_text()` so all headline lines default to yellow with a
     heavy black stroke and left-aligned stacked layout.
   - Extract or adjust line-fitting helpers so 2-4 lines of up to 10 compact
     chars fit inside the safe text region.
   - Keep JPEG output at 1920x1080, quality 92.
   - Preserve silent skip behavior for missing Pillow/fonts/ffmpeg/media.

5. Multi-candidate rendering and publishing
   - Render processed files as `match-NN-cover-01.jpg`,
     `match-NN-cover-02.jpg`, etc.
   - Copy all rendered candidates into the published package directory.
   - Keep `cover.jpg` and `published_cover_path` pointing at rank 1.
   - Add all candidate paths and ranks to publishing JSON and `upload.txt`.
   - Update missing-output reprocessing checks so deleted candidate covers or
     published copies trigger package repair.

6. Tests
   - Add model/default-load coverage for old `PublishingPackage` rows without
     candidates.
   - Add copywriter service tests for 2-3 candidate rendering, rank-1 default
     fields, published candidate copies, upload metadata listing, export-only
     fallback, and missing-output repair.
   - Add frame selector unit tests with synthetic frames for sharpness,
     brightness, scene penalty, chat bonus, event priority, spacing, and
     degraded sampling.
   - Update cover text renderer tests for all-yellow headline fill, stroke
     sizing, line fitting, and safe placement.
   - Keep existing optional renderer and ffmpeg-missing tests passing.

7. Documentation and spec
   - Update `.trellis/spec/backend/export-configuration.md` with the cover
     candidate contract, file naming, fallback behavior, and required tests.
   - Update README or operator notes only if the published package layout
     change needs user-facing explanation.

## Validation commands

Focused tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline\test_copywriter_service.py tests\vision\test_scene_classifier.py
```

Full checks:

```powershell
.\.venv\Scripts\python.exe -m pytest tests
.\.venv\Scripts\python.exe -m compileall src tests
```

Real sample validation, after unit tests pass:

```powershell
$env:ARL_POSTPROCESS_PRESET="publish"
.\.venv\Scripts\python.exe -m arl.cli copywriter --session-id session-20260617073649-4b5ec478 --match-indices 2 --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli copywriter --session-id session-20260617073651-cf11bf9e --match-indices 2,3,4 --force-reprocess
```

Expected real-sample evidence:

- Each validation match with source recording available has 2-3 processed cover
  candidate files with distinct source timestamps.
- `cover_path` and `published_cover_path` resolve to the top-ranked candidate.
- Published package directories contain `cover.jpg` plus `cover-01.jpg`,
  `cover-02.jpg`, and optionally `cover-03.jpg`.
- `upload.txt` and per-match publishing JSON list all candidates.
- Manual spot check confirms stacked yellow/black typography is readable at
  thumbnail size.

## Review gate before start

- Confirm the additive schema shape: keep legacy default fields and add
  `cover_candidates` for all ranked covers.
- Confirm v1 frame scoring should remain heuristic and best-effort, with no
  OCR or model inference for chat text.
- Confirm export-only media fallback may render a single default cover when
  source-recording timeline mapping is unavailable.

## Risky files

- `src/arl/copywriter/models.py`
  - Persisted publishing package rows must remain backward compatible.
- `src/arl/copywriter/service.py`
  - Reprocessing/idempotency must account for multiple generated and published
    cover files.
- `src/arl/copywriter/cover.py`
  - Optional dependency imports and image metric code must degrade cleanly.
- `tests/pipeline/test_copywriter_service.py`
  - Several tests assert exact publish layout and cover filenames.

## Rollback points

- Revert the additive model/service changes and rerun copywriter with
  `--force-reprocess` to restore single-cover package rows.
- Delete only generated processed/published cover candidates for the target
  session if a local rerun is needed. Do not touch raw recordings or `data/`
  assets outside the generated session outputs.
