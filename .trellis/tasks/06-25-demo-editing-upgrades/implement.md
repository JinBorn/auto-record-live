# Implementation Plan: Demo-Inspired Editing Upgrades

## Task Map

This should be implemented as child tasks. The parent task owns the reference
analysis and cross-feature acceptance criteria; children own independently
testable deliverables.

1. Publishing metadata and cover assets
2. ASS subtitle styling and export wiring
3. Explicit edit plans with teaser-before-main rendering
4. Background music and sound-effect asset mixing
5. Punch-in zoom transforms
6. End-to-end reference validation against demo-derived expectations

## Recommended Order

### 1. Publishing Metadata And Cover Assets

- Extend or wrap `CopywriterService` to emit richer title, summary, cover lines,
  tags, and evidence.
- Add a separate cover rendering helper that can choose a frame and place large
  readable text.
- Keep rendering optional so metadata can be generated without image dependencies.
- Tests:
  - copy generation uses high-signal cues rather than only the first subtitle
    lines
  - cover text line splitting is deterministic
  - missing frame/cutout assets fail closed

### 2. ASS Subtitle Styling

- Add SRT-to-ASS conversion helpers with configurable font, outline, alignment,
  and margin.
- Add subtitle asset format support or a paired render artifact for ASS.
- Update exporter subtitle filter selection to prefer ASS when configured.
- Tests:
  - generated ASS contains expected style fields
  - SRT timing/text survives conversion
  - existing SRT-only export behavior remains unchanged

### 3. Explicit Edit Plans And Teaser-Before-Main

- Add `EditPlanAsset` models and JSONL storage.
- Build a planner that selects teaser windows from highlight/condensed plans but
  also emits a validated main segment.
- Add strict validation that teaser windows do not replace canonical match start.
- Add exporter opt-in path for edit-plan rendering.
- Tests:
  - teaser segments appear before main segments
  - main segment begins at the validated boundary
  - invalid mid-game-only edit plans are rejected
  - existing `HighlightPlanAsset` export path is unchanged

### 4. Background Music And Sound Effects

- Add config for local music/SFX asset directories or explicit paths.
- Add audio mix instructions to `EditPlanAsset`.
- Implement ffmpeg audio graph construction with low default BGM gain.
- Add skip behavior for missing assets.
- Tests:
  - missing assets do not break base video export
  - BGM/SFX filters are included only when configured
  - gain defaults are conservative

### 5. Punch-In Zoom Transforms

- Add transform fields to timeline segments.
- Implement simple crop/scale filter graph for selected windows.
- Start with configured anchors and modest zoom scale; do not attempt target
  tracking in MVP.
- Tests:
  - transform model validates scale/anchor bounds
  - ffmpeg filter graph is generated for punch-in segments
  - no transform is applied when disabled

### 6. Reference Validation

- Use `data/demo1` and `data/demo2` as human reference material.
- Build small generated fixtures for automated render tests to avoid committing
  or processing huge binaries in unit tests.
- Manual checks:
  - cover readability at 1920x1080
  - teaser starts before main without corrupting main boundary
  - subtitles remain low and readable
  - BGM/SFX are mixed only when configured

## Validation Commands

Run focused tests after each child task:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_copywriter_service.py
.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_subtitles_service.py
.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_highlight_planner_service.py
.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_ffmpeg_resilience.py
.\.venv\Scripts\python.exe -m pytest tests/highlights
```

Run broader checks before merging a child that touches exporter behavior:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/pipeline tests/highlights tests/test_config.py
```

Run full regression before final integration:

```powershell
.\.venv\Scripts\python.exe -m pytest tests
```

## Risk Points

- Exporter filter graphs can become fragile. Keep command construction covered
  by tests and prefer small render smoke tests over long media fixtures.
- Chinese font rendering depends on available system fonts. Cover/subtitle
  rendering should expose font config and test style fields, not a specific
  machine font file.
- Teaser logic can reintroduce earlier condensed-export bugs if it mutates match
  boundaries. Keep teaser/main roles explicit and validate main coverage.
- Audio mixing can easily overpower voice/game audio. Start with conservative
  gain and require opt-in assets.

## Deferred Work

- LLM-backed title/summary generation.
- Automatic raw-audio emotion detection for SFX placement.
- Automatic target tracking for zoom centers.
- User-provided external reference inserts / "引经据典" clips.
- Internet-sourced film/meme clip retrieval.
- A frontend review UI for manually accepting or adjusting edit plans.
