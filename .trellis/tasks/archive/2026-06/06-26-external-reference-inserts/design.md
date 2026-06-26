# Technical Design: User-Provided External Reference Inserts

## Overview

Extend the existing edit-plan pipeline so a timeline can contain short
`role="insert"` segments sourced from explicit local files. The planner owns
manifest loading and deterministic placement; the exporter owns final validation
and ffmpeg input/filter graph construction.

The implementation should reuse the current `EditPlanAsset` and
`TimelineSegment` contract instead of creating a separate render stage.

## Boundaries

- `src/arl/config.py`
  - Adds edit-planner settings for insert enablement, manifest path, and maximum
    inserted segments.
- `src/arl/shared/contracts.py`
  - Adds typed local insert manifest models if durable config parsing needs a
    Pydantic contract.
  - Keeps `TimelineSegment` as the durable output contract for selected inserts.
- `src/arl/editing/service.py`
  - Loads and validates the manifest.
  - Selects local insert clips from valid manifest entries.
  - Emits insert timeline segments after matching teaser segments.
- `src/arl/exporter/service.py`
  - Validates insert timeline segments and local source files.
  - Adds insert clip files as ffmpeg inputs.
  - Routes each timeline segment to the correct input stream.
  - Offsets BGM/SFX input indexes when insert video inputs are present.
- `.trellis/spec/backend/export-configuration.md`
  - Updates the existing edit-plan contract that currently says insert/source
    segments force fallback.

## Data Flow

```text
env/config
  -> EditingSettings(insert_enabled, insert_manifest_path, insert_max_segments)
  -> EditingPlannerService loads manifest
  -> valid manifest clips filtered by local file existence and trigger reasons
  -> EditPlanAsset.timeline includes teaser/insert/main segments
  -> ExporterService validates plan against boundary and source files
  -> ffmpeg command adds recording input, insert inputs, audio asset inputs
  -> filter_complex trims/concats per-segment video/audio in timeline order
```

## Manifest Contract

Recommended MVP JSON:

```json
{
  "clips": [
    {
      "source_path": "D:/clips/classic-reference.mp4",
      "source_start_seconds": 0.0,
      "source_end_seconds": 3.0,
      "trigger_reasons": ["highlight_keyword", "condensed_key_event"],
      "reason": "classic_reference"
    }
  ]
}
```

Fields:

- `source_path`: required local file path.
- `source_start_seconds`: required non-negative start time relative to the insert
  source clip.
- `source_end_seconds`: required end time greater than start.
- `trigger_reasons`: optional list of edit-plan teaser reasons. Empty means the
  clip may match any high-signal teaser.
- `reason`: optional segment reason persisted to `TimelineSegment.reason`;
  default should be `external_reference`.

The manifest is user-authored local configuration, not an asset library scanner.
Invalid entries are skipped; one bad entry must not invalidate the base edit
plan.

## Planner Behavior

1. Build the base edit plan exactly as today: selected teaser segments followed
   by one full `main` segment.
2. Apply punch-in transforms to teaser segments as today.
3. If insert support is disabled, stop here.
4. If enabled, load the manifest path.
5. Normalize and validate candidate clips:
   - manifest exists and parses as JSON
   - source file exists and is a file
   - trim range is non-negative and increasing
   - trigger reasons are compared to teaser segment reasons
6. Walk the current timeline in order and insert the first matching clip after
   the first matching teaser.
7. Stop when `insert_max_segments` is reached.
8. Build audio instructions after insert placement so SFX timeline positions are
   based on final output timing.

Missing manifest or missing clip files should log `skip insert ... reason=...`
and preserve base teaser/main output.

## Exporter Validation

Valid roles are `teaser`, `insert`, and `main`.

Rules:

- Exactly one `main` segment is required.
- `main` must not be first and must start at `0.0` and end at the full boundary
  duration.
- Segments before `main` may be `teaser` or `insert`; segments after `main` are
  invalid for the MVP.
- Recording-sourced segments (`teaser`, `main`) must have `source_path is None`
  and stay within the validated boundary duration.
- Insert segments must have `source_path` set to an existing local file and must
  have a non-negative increasing trim range.
- Insert segment timing is relative to the insert source, not the match
  boundary.
- Existing transform validation still applies. The MVP can allow transforms on
  inserts because they use the same video filter chain, but the planner should
  not emit insert transforms by default.
- Existing audio validation still uses rendered output duration after inserts
  are included.

Fallback behavior stays fail-closed: stale or invalid manual edit plans are
ignored and exporter falls back to the previous full/highlight path.

## FFmpeg Command Shape

Current command shape:

```text
input 0: recording
input 1..N: audio beds and SFX
```

New command shape:

```text
input 0: recording
input 1..M: unique insert clip sources in timeline order
input M+1..N: audio beds and SFX
```

For each timeline segment:

- recording segments use `[0:v]` and `[0:a]`
- insert segments use `[insert_input_index:v]` and `[insert_input_index:a]`
- per-segment chain still applies subtitle burn-in, `trim`, `setpts`, optional
  transform, `atrim`, and `asetpts`
- concat uses timeline order exactly as persisted

Audio mixing must accept an input offset:

```python
audio_input_start_index = 1 + len(insert_inputs)
```

This prevents insert clip inputs from colliding with BGM/SFX input indexes.

## Compatibility

- No behavior changes unless `ARL_EDIT_INSERTS_ENABLED=1` and edit planning is
  already enabled.
- Exporter still ignores edit plans unless `ARL_EXPORT_USE_EDIT_PLANS=1`.
- Existing edit plans without insert segments remain valid.
- Existing BGM/SFX and punch-in behavior must continue to work.
- Existing state files and JSONL rows remain readable because `TimelineSegment`
  already has the needed fields.

## Trade-Offs

- Deterministic trigger-reason matching is less expressive than semantic clip
  selection, but it is testable and avoids guessing copyrighted content.
- Validating insert source duration would require ffprobe calls during planning
  or validation. MVP validates path and trim shape only; ffmpeg remains the final
  media-duration authority.
- The first implementation should support local file paths, not directory scans,
  to keep asset ownership explicit.

## Rollback

- Disable `ARL_EDIT_INSERTS_ENABLED` to stop planner insertion.
- Disable `ARL_EXPORT_USE_EDIT_PLANS` to avoid rendering edit plans entirely.
- Remove or fix the local manifest if a bad asset is causing exporter fallback.
