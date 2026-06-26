# Implementation Plan: End-to-End Reference Validation

## Checklist

1. Baseline verification
   - Run focused tests for already completed child tasks:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline\test_copywriter_service.py tests\pipeline\test_subtitles_service.py tests\pipeline\test_editing_service.py tests\pipeline\test_ffmpeg_resilience.py tests\pipeline\test_postprocess_service.py tests\test_config.py -q
```

2. Contract integration test
   - Add a small integration test file, likely
     `tests/pipeline/test_reference_validation.py`.
   - Seed temp JSONL/files for one complete match:
     - `match-boundaries.jsonl`
     - `subtitle-assets.jsonl`
     - optional `recording-assets.jsonl`
     - optional `export-assets.jsonl`
   - Run real `HighlightPlannerService`, `EditingPlannerService`, and
     `CopywriterService`.
   - Assert highlight cues flow into edit plans and publishing packages.
   - Assert no insert/source-path segments appear in the current scope.
   - Assert rerun behavior stays idempotent for persisted outputs.

3. Combined exporter command regression
   - Extend `tests/pipeline/test_ffmpeg_resilience.py` or add a focused test in
     `test_reference_validation.py`.
   - Seed an edit plan with teaser, full main, punch-in transform, BGM, and SFX.
   - Enable edit-plan export, subtitle burn-in, and ASS subtitles.
   - Patch ffmpeg subprocess calls using the existing test pattern.
   - Assert command/filter graph contains:
     - generated `.ass` subtitle path
     - teaser and main trim/atrim chains
     - punch-in `scale`/`crop`
     - BGM/SFX inputs, `volume`, `adelay`, and `amix`
     - no highlight `select=`

4. Optional tiny media smoke
   - Only add if it remains reliable and fast.
   - Skip when `ffmpeg` or `ffprobe` is missing.
   - Generate tiny media under a temp directory; never use `data/demo*` files.
   - Assert real exporter output probes as non-empty video.

5. Manual reference checklist
   - Add `manual-reference-checklist.md` under this task directory.
   - Include checks for:
     - cover title/summary/cover lines/evidence
     - teaser-before-main timeline
     - ASS subtitle style and burn-in path
     - BGM/SFX opt-in and conservative gains
     - punch-in transform placement
     - external reference inserts explicitly excluded

6. Fix integration gaps if found
   - Keep edits scoped to the owning stage.
   - If a command assertion fails because of a real bug, update production code
     and the focused unit test for that stage.
   - If the manual checklist exposes only subjective polish gaps, record them as
     deferred follow-up rather than expanding this validation task.

7. Final validation
   - Run:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
git diff --check
.\.venv\Scripts\python.exe -m pytest tests\pipeline tests\test_config.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
```

## Risk Points

- Full demo videos are too large for automated tests. Do not copy, transform, or
  commit them.
- Real ffmpeg smoke tests can be environment-sensitive. Keep them skipped when
  prerequisites are missing.
- Command assertions should not be loosened to make integration pass; if a
  combined feature path fails, inspect the filter graph and fix the specific
  owner.
- Copywriter currently uses deterministic heuristics and may produce mojibake in
  existing test strings; assert structural fields and high-signal cue selection,
  not subjective Chinese copy quality.

## Review Gate

Before implementation starts, confirm this scope:

- no external reference insert implementation
- no automated full-demo render/comparison
- add one contract integration test
- add one combined exporter command regression
- add a task-local manual reference checklist
- optional tiny ffmpeg smoke only if it is stable and cheap
