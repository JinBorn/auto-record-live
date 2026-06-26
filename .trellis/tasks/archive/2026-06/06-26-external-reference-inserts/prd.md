# User-Provided External Reference Inserts

## Goal

Enable the edit-planning pipeline to insert short user-provided local reference
clips, such as classic-film or meme reactions, into generated teaser-first
exports. This implements the demo-derived "quote/reference insert" editing
pattern without downloading assets, auto-discovering copyrighted clips, or
changing existing exports by default.

## User Value

The reference videos use brief external clips to punctuate jokes or high-signal
moments. A local explicit insert mechanism lets the user prepare trusted assets
once, then have the pipeline place them deterministically in edit plans when a
matching highlight reason is present.

## Confirmed Facts

- `TimelineSegment` already has `role`, `source_path`,
  `source_start_seconds`, `source_end_seconds`, `transform`, and `reason`.
- Existing edit plans already support `teaser` and `main` segments, BGM/SFX
  audio instructions, punch-in transforms, and optional subtitle burn-in.
- `ExporterService._valid_edit_plan()` currently rejects any role outside
  `{"teaser", "main"}` and rejects any segment with `source_path` set.
- `ExporterService._edit_plan_ffmpeg_command()` currently assumes all timeline
  segments read from the recording input at index `0`.
- Existing audio mixing assumes additional audio asset inputs start at ffmpeg
  input index `1`; insert video inputs will need to shift this offset.
- Parent task scope says external inserts must use user-provided local clips
  only; no internet downloads or copyrighted-clip discovery.
- The default pipeline must remain disabled/no-op unless the new insert settings
  are explicitly enabled.

## Requirements

- Add an explicit local insert manifest format for short reference clips.
- Load the insert manifest only when edit planning is enabled and insert support
  is explicitly enabled.
- Manifest entries must include a local source file path and a trim range inside
  that source clip.
- Manifest entries may declare trigger reasons that match timeline segment
  reasons such as `highlight_keyword`, `condensed_key_event`, or
  `condensed_tactical`.
- The planner must skip missing manifests, unreadable manifests, malformed clip
  entries, missing source files, and non-positive trim ranges without blocking
  base edit-plan generation.
- The planner must insert at most the configured maximum number of insert
  segments, defaulting to one.
- The first implementation must use deterministic placement: insert a matching
  clip immediately after the first matching high-signal teaser segment.
- Insert segments must use `role="insert"` and set `source_path` to the local
  clip while keeping source times relative to that clip.
- Exporter validation must accept valid `insert` segments, reject invalid
  insert source files/timing, and continue falling back for unsupported plan
  shapes.
- Exporter command generation must render insert segments from extra ffmpeg
  inputs while preserving original recording segments, subtitle burn-in,
  punch-in transforms, and audio mixing.
- Existing teaser/main edit plans and highlight/full export behavior must remain
  unchanged unless insert settings and edit-plan export are enabled.

## Acceptance Criteria

- [ ] Default settings produce the same edit plans as before; no insert manifest
      is read unless insert support is enabled.
- [ ] Missing or invalid insert manifest logs a concise skip and still writes the
      base teaser/main edit plan when that base plan is otherwise valid.
- [ ] Missing insert source files are skipped by the planner and rejected by the
      exporter if a stale/manual plan references them.
- [ ] A valid manifest clip whose trigger reason matches a selected teaser
      becomes one `TimelineSegment(role="insert", source_path=...)` immediately
      after that teaser.
- [ ] Exporter accepts `teaser -> insert -> main` timeline order and rejects
      insert segments after the main segment.
- [ ] Exporter ffmpeg command uses the recording input for local recording
      segments and the correct extra input index for each insert source.
- [ ] BGM/SFX audio filters still point at the correct ffmpeg input indexes when
      insert video inputs are present.
- [ ] Subtitle burn-in and punch-in transform filters still apply to the correct
      per-segment video chain.
- [ ] Config env tests cover insert enablement, manifest path, and maximum
      segment clamping.
- [ ] Focused planner/exporter tests cover absent manifest, missing source,
      valid insert generation, valid insert rendering, and fallback for invalid
      insert plans.

## Out Of Scope

- Downloading or searching for external reference clips.
- Automatically determining whether a clip is copyrighted or appropriate.
- Semantic scene matching beyond explicit manifest trigger reasons.
- A frontend/manual review UI for selecting inserts.
- Multi-insert editorial pacing beyond a simple configurable maximum.
- Per-insert subtitles, overlays, or independent audio-ducking rules.

## Open Questions

None block the MVP. The recommended default is a JSON manifest, disabled by
default, with one maximum insert and deterministic placement after the first
matching high-signal teaser.
