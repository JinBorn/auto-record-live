# Technical Design: Demo-Inspired Editing Upgrades

## Overview

Add an explicit upload-editing layer on top of the existing postprocess pipeline.
The layer should package a validated match export into a richer timeline:
teaser clips first, main content second, optional inserts, styled subtitles,
background music, sound effects, and punch-in transforms.

This must not relax existing match-boundary or condensed-plan safeguards. The
existing `HighlightPlanAsset` should continue to describe retained source
windows. A new edit-plan artifact should describe presentation decisions.

## Proposed Module Boundaries

```text
src/arl/publishing/
  service.py       # metadata/title/summary/cover text planning
  cover.py         # optional cover rendering helper
  models.py        # PublishingPackageAsset

src/arl/editing/
  planner.py       # creates EditPlanAsset from boundaries, highlights, subtitles
  renderer.py      # builds ffmpeg filter graphs / command plans
  models.py        # EditPlanAsset and timeline/audio/overlay models

src/arl/subtitles/
  ass.py           # SRT -> styled ASS conversion helpers

src/arl/exporter/
  service.py       # opt-in path to render EditPlanAsset; existing path unchanged
```

The exact module names can be adjusted during implementation, but the separation
should remain: copy/cover planning, edit-plan creation, subtitle styling, and
rendering are separate concerns.

## Data Contracts

### PublishingPackageAsset

Stores upload-facing metadata without requiring a rendered video.

```python
class PublishingPackageAsset(BaseModel):
    session_id: str
    match_index: int
    title_candidates: list[str]
    recommended_title: str
    summary: str
    cover_lines: list[str]
    tags: list[str]
    evidence: list[str]
    cover_path: str | None = None
    created_at: datetime
```

### EditPlanAsset

Stores presentation timeline instructions. Times are relative to the original
match boundary unless a timeline item references an external clip.

```python
class EditPlanAsset(BaseModel):
    session_id: str
    match_index: int
    source_boundary_start_seconds: float
    source_boundary_end_seconds: float
    timeline: list[TimelineSegment]
    subtitle_style: SubtitleStyle | None = None
    audio_beds: list[AudioBed]
    sound_effects: list[SoundEffectHit]
    created_at: datetime
```

```python
class TimelineSegment(BaseModel):
    role: Literal["teaser", "main", "insert"]
    source_path: str | None = None
    source_start_seconds: float
    source_end_seconds: float
    transform: VideoTransform | None = None
    reason: str
```

```python
class VideoTransform(BaseModel):
    kind: Literal["none", "punch_in"]
    scale: float = 1.0
    x_anchor: float = 0.5
    y_anchor: float = 0.5
```

## Feature Design

### 1. Publishing Metadata And Cover

Extend current copy generation from "first subtitle lines" to a scored summary:

- Use original room/title metadata when available.
- Extract high-signal subtitle cues from highlight/condensed windows.
- Prefer concrete gameplay hooks: champion/build, lane, unusual strategy,
  recognition/chat joke, outcome.
- Emit short cover lines separately from video title candidates.
- Render cover from a selected high-action frame plus large readable text.

The first implementation can use deterministic heuristics. LLM generation can be
added later behind an explicit provider/config boundary.

### 2. Styled Subtitles

Keep SRT as the ASR interchange format, but generate ASS as a render artifact.
The reference style maps to:

- `PlayResX=1280`, `PlayResY=720`
- bottom-centered alignment
- white primary text, black outline
- font size around 36 at 720p
- bottom margin around 20

Exporter should burn ASS when available and configured. SRT attachment/copy
behavior should remain unchanged when subtitle burn is disabled.

### 3. Teaser-Before-Main Timeline

Do not mutate `MatchBoundary` and do not let teaser windows replace the main
segment. Generate an `EditPlanAsset` like:

1. teaser segments from top-ranked highlight windows
2. optional short transition/gap
3. main segment, normally the complete condensed/full export timeline

Validation rule: at least one `role="main"` segment must begin at or near the
validated main start, and the final main coverage rule must be explicit. This
prevents the previous mid-game-only export failure mode.

### 4. Background Music

Use local audio assets only:

- config points to a music library directory or explicit track paths
- default gain is low, e.g. -24 dB to -18 dB relative full scale
- optional ducking under original audio can be implemented with ffmpeg
  `sidechaincompress`
- stage mapping can start simple: development/main track and climax/highlight
  track based on timeline role or highlight density

### 5. Sound Effects

Use local audio assets only. Initial placement should be deterministic:

- keyword rules from subtitles, e.g. exclamation/reaction phrases
- highlight event reasons, e.g. kill/tower/dive
- optional manual event JSON for user overrides

Automatic emotion detection from raw audio is deferred.

### 6. Punch-In Zoom

Represent zooms as transforms on selected timeline segments. Start with safe
fixed anchors and modest scale (for example 1.15-1.35) so core HUD remains
visible. Later work can add target tracking from OCR/KDA/minimap context.

### 7. External Inserts

Only support user-provided local clips. The planner may select from a local
manifest using keywords, but it must not download or infer copyrighted material.
If no configured insert asset exists, rendering skips inserts.

## Compatibility

- Existing JSONL stores remain valid.
- New artifacts should be opt-in and stored in separate JSONL files, e.g.
  `publishing-packages.jsonl` and `edit-plans.jsonl`.
- Existing exporter command path stays the default.
- Any edit-plan render must validate source files and fail closed without
  deleting or replacing existing exports.

## Validation Strategy

- Unit tests for metadata scoring, ASS conversion, edit-plan validation, audio
  asset resolution, and zoom transform modeling.
- Exporter tests should verify ffmpeg command/filter construction without
  requiring long real videos.
- A tiny generated media fixture should cover render smoke tests.
- Manual comparison against `data/demo1` and `data/demo2` can remain a reference
  check, not a committed binary fixture.
