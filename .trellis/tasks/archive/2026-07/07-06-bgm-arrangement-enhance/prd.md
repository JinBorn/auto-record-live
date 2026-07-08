# BGM arrangement enhancement

## Goal

Arrange background music in 2-3 content-aware phases with smooth handoffs, and
avoid source music per detected span instead of disabling BGM for the whole
export.

## User Value

Demo2's BGM starts playful during the laning/farming phase and switches as the
video builds to its climax, staying quiet under the voice. Our exports carry
at most one switch, and any detected source music kills BGM for the entire
export (validation sample m02 shipped with zero BGM).

## Requirements

- Phase beds: for exports above a duration threshold, plan 2-3 beds
  (laning -> momentum -> climax). Switch points align to content signals
  (KDA-change density ramp or highlight-window intensity from the existing
  planner data); fall back to proportional positions when signals are flat.
  Library selection orders tracks by `phase`/`energy` ascending so intensity
  rises; distinct tracks per phase when the library allows.
- Crossfades: adjacent beds overlap with a 1-2s crossfade (acrossfade or
  overlapping afade windows) instead of hard cuts; bed fade-in/out at
  timeline edges preserved.
- Span-based source-music avoidance: use the existing
  `detect_source_background_music_spans` output to suppress/duck BGM only
  during detected spans (plus configurable padding). Global skip remains only
  when detected music covers more than a configurable majority (default 60%)
  of the export. The m02-style total-skip case must instead yield beds in the
  non-musical regions.
- Mix hierarchy unchanged: sidechain ducking under voice and loudnorm remain
  the loudness authority; per-bed gain default stays at the current quiet
  level (-28dB) unless listening tests during validation justify a documented
  adjustment.
- Teaser policy: BGM continues to start with main content by default
  (06-30 decision). A separate opt-in flag may allow a low-gain teaser bed,
  default off.
- Library guidance: extend `data/bgm/library.json` usage notes to recommend
  >=2 tracks per phase bucket; selection degrades gracefully with a small
  library (fewer switches rather than repeated tracks, keeping the 06-30
  variety rule).
- Plan freshness checks regenerate stale pre-change plans.

## Out Of Scope

- Beat-matched or key-matched music transitions.
- Automatic music downloading or licensing checks.
- Replacing the source-music detector itself (tuning thresholds is in scope).

## Acceptance Criteria

- [ ] The sample that previously shipped 0 beds due to partial source music
      (`4b5ec478` m02) gains beds covering non-musical regions, quality-report
      verified.
- [ ] Exports >=10min with an adequate library show >=2 bed switches with
      crossfades present in the filtergraph (unit-asserted).
- [ ] Sidechain ducking and loudnorm behavior are unchanged (existing audio
      tests keep passing; output loudness spot-checked).
- [ ] Span-avoidance logic is unit-tested against synthetic detection spans
      (edge overlaps, full coverage, zero coverage).
- [ ] Library-notes update documents the per-phase track recommendation.

## Notes

- Medium-to-complex: `design.md` (bed planning algorithm, span suppression,
  crossfade rendering) + `implement.md` before start.
- User will add more BGM tracks to `data/bgm/tracks/` and register them in
  `library.json`; acceptance uses whatever library exists plus fixtures.
