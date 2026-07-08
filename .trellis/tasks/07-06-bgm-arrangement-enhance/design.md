# BGM arrangement enhancement design

## Scope

This task extends the existing edit-plan audio contract. It does not introduce a
new audio pipeline and does not make audio mixing default-on outside the publish
preset path.

The implementation should keep these existing invariants:

- BGM is emitted only as edit-plan `AudioBed` rows.
- BGM starts at the first main segment; leading teaser and transition segments
  stay BGM-free by default.
- Exporter sidechain ducking and optional loudnorm remain the authority for
  final mix safety.
- User-supplied files under `data/bgm/` are runtime assets and must not be
  committed.

## Current facts from the repo

- `AudioBed` already supports `source_path`, rendered-timeline start/end,
  `gain_db`, looping, and a reason string. Overlapping beds are valid: exporter
  renders each bed as a separate input, ducks each against `[basea]`, and mixes
  through `amix`.
- Existing planning can produce one configured BGM bed, two generated default
  beds, or two library beds. Switch timing is currently fixed around 55% of
  rendered main duration.
- Existing source-music detection returns only an aggregate
  `SourceMusicDetection(has_music, confidence, reason)`. It samples chunk-aware
  media spans correctly, but it does not yet expose detected music intervals.
- Existing freshness checks compare the complete expected `AudioBed` shape, so
  old edit plans can be regenerated once the expected bed list changes.

## Data flow

```text
MatchBoundary + HighlightPlan + Subtitle + RecordingAsset
  -> build teaser/main/transition timeline
  -> resolve source-music detection to source-time spans
  -> choose BGM phase plan in rendered main timeline
  -> select distinct tracks per phase or generated fallback tracks
  -> subtract rendered source-music avoidance windows
  -> emit AudioBed rows
  -> exporter validates local audio files and renders sidechain/amix/loudnorm
```

The edit planner owns bed planning and source-music avoidance. The exporter
continues to be a renderer and validator; it should not infer phases or inspect
source music.

## Contracts and settings

Extend `EditingSettings` with conservative defaults:

- `bgm_multi_phase_min_seconds`: default `600.0`. At or above this rendered
  main duration, an adequate library can produce three phases and at least two
  switches.
- `bgm_switch_min_gap_seconds`: default `60.0`. Switches must not create tiny
  phase beds near timeline edges or each other.
- `bgm_crossfade_seconds`: default `2.0`, clamped to `[1.0, 2.0]`.
- `bgm_source_music_padding_seconds`: default `2.0`.
- `bgm_source_music_majority_threshold`: default `0.60`, clamped to
  `[0.0, 1.0]`.
- Optional teaser BGM remains out of scope for the default path. If a flag is
  added, it must default off and not change this task's main-content behavior.

Extend the internal source-music dataclasses in `editing/audio.py`:

```python
@dataclass(frozen=True)
class SourceMusicSpan:
    start_seconds: float
    end_seconds: float
    confidence: float = 0.0


@dataclass(frozen=True)
class SourceMusicDetection:
    has_music: bool
    confidence: float
    reason: str
    music_spans: tuple[SourceMusicSpan, ...] = ()
    coverage_ratio: float = 0.0
```

This is backward compatible with current tests and injected detectors that build
`SourceMusicDetection` with the existing three positional arguments. If such a
legacy detector returns `has_music=True` with no spans, treat it as a dominant
music signal and keep the old global skip behavior.

## Source-music span handling

`detect_source_background_music()` and `detect_source_background_music_spans()`
should keep their current aggregate decision but also return sampled music-like
intervals. Confident sample windows become `SourceMusicSpan` rows and adjacent or
overlapping rows are merged.

Normalize all returned spans to source timeline seconds in
`EditingPlannerService._detect_source_music_from_spans()`:

- Single-span recordings call the existing detector with chunk-local seconds,
  then translate returned local spans back to recording-relative source seconds.
- Multi-span recordings keep using concrete chunk paths and should return
  source-relative spans directly.
- Injected per-file detectors used in tests can still return local spans; the
  service should translate and merge them.

The edit planner then maps source-time music spans into rendered output windows
by walking the final timeline. Only `main` segments participate in default BGM
mapping. Transformed close-up subsegments remain linear source-to-output spans,
so the same mapper can handle them.

Avoidance policy:

- Add padding to each rendered music window and merge overlaps.
- Compute covered seconds against the rendered BGM-active range.
- If coverage ratio is greater than `bgm_source_music_majority_threshold`, emit
  no BGM beds and log the dominant skip.
- Otherwise subtract these windows from planned BGM beds. Keep SFX behavior
  unchanged.
- Drop remaining BGM fragments that are shorter than a small minimum practical
  duration, so source-music holes do not create noisy one-second beds.

## Phase planning

Build a phase plan over rendered main-content time:

- Short exports keep one bed unless the existing two-bed threshold applies.
- Medium exports can use two phases: `laning -> climax`.
- Exports at or above `bgm_multi_phase_min_seconds` can use three phases:
  `laning -> momentum -> climax`, but only when enough distinct tracks are
  available.

Switch-point selection should be deterministic and content-aware:

- Prefer KDA kill-event density from existing subtitle `kda_change` cues.
- Also score highlight windows by reason intensity:
  `highlight_keyword` and KDA-rich windows outrank `condensed_key_event`, which
  outranks context/tactical/setup windows.
- Map source-time cues/windows into rendered output seconds before scoring.
- Pick candidate switch points near density ramps, while respecting
  `bgm_switch_min_gap_seconds`.
- If signals are flat or invalid, fall back to proportional points:
  approximately 40% and 75% for three phases, and 55% for two phases.

Represent crossfades using existing `AudioBed` rows instead of adding a shared
contract field. Around a switch point, let the outgoing bed end after the switch
and the incoming bed start before the switch by half of
`bgm_crossfade_seconds`, bounded by each phase interval. The exporter already
applies `afade` to each bed and `amix` can mix overlapping ducked beds.

## Track selection

Extend library selection from "early plus climax" to a requested phase list.
Use the existing scoring and stable tie rotation as the base:

- `laning`: prefer `phase` values `early`, `laning`, `playful`, low energy, or
  compatible `mood` values such as `playful`, `chill`, `tutorial`.
- `momentum`: prefer `momentum`, `mid`, `tactical`, `fight`, or moderate energy.
  If no explicit momentum bucket exists, choose the best non-climax distinct
  compatible track.
- `climax`: keep current high-energy `climax`, `hype`, `fight` behavior.
- Never repeat the same track just to satisfy phase count. Degrade to fewer
  phases/switches when the library is small.

Generated fallback assets currently provide only playful and climax tracks, so
they should degrade to two phases. The acceptance case for two switches depends
on an adequate library, not on generated fallback WAVs.

## Freshness and reporting

The existing stale-audio comparison should be enough if the new expected
`AudioBed` list includes changed starts, ends, reasons, paths, and gains. Add
tests to prove legacy all-skip plans and old two-bed/no-crossfade plans are
replanned.

Quality-report BGM metrics already list bed rows. If needed, only extend the
human-readable report summary to make switch count/crossfade shape easier to
spot; do not create a separate publish artifact for this task.

## Rollback

Rollback is mostly config-based:

- Set `ARL_EDIT_AUDIO_MIXING_ENABLED=0` to disable all edit-plan audio.
- Set `ARL_EDIT_SKIP_BGM_WHEN_SOURCE_HAS_MUSIC=0` to bypass source-music
  avoidance while debugging.
- Set `ARL_EDIT_BGM_PATH` to a single known file if library phase selection is
  suspected.
- Use `postprocess-reset` plus scoped `edit-planner/exporter` reruns to replace
  generated plans.
