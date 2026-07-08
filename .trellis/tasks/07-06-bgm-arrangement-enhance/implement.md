# BGM arrangement enhancement implementation plan

## Checklist

- [ ] Add BGM arrangement config fields to `EditingSettings`, env loading,
      publish defaults where needed, `.env.example` if present, README, and
      config tests.
- [ ] Extend internal source-music detection models with span and coverage
      fields while preserving three-argument construction compatibility.
- [ ] Update single-file and span-aware detectors to emit merged confident sample
      intervals, then normalize detector output to source timeline seconds in
      the edit planner.
- [ ] Add helpers in `EditingPlannerService` to map source-time music spans onto
      rendered timeline seconds, pad/merge them, compute coverage ratio, and
      subtract them from planned BGM beds.
- [ ] Replace the fixed two-bed planning helper with phase planning:
      duration-based phase count, content-aware switch candidates from KDA and
      highlight-window intensity, proportional fallback, switch gap guards, and
      overlapping crossfade windows.
- [ ] Extend BGM library selection to accept requested phases, preserve stable
      tie rotation, and degrade without repeating tracks when the library is too
      small.
- [ ] Keep generated fallback BGM at two phases unless a real third generated
      asset is intentionally added and tested.
- [ ] Update edit-plan audio freshness tests so stale pre-change plans are
      regenerated when phase count, crossfade timing, or source-music avoidance
      differs.
- [ ] Update exporter command tests to assert overlapping BGM beds produce
      `afade`, `adelay`, `sidechaincompress`, and `amix`; keep loudnorm tests
      unchanged.
- [ ] Update quality-report or README/spec notes for library guidance:
      at least two usable tracks per phase bucket (`laning`, `momentum`,
      `climax`) for full three-phase behavior.
- [ ] Validate the real acceptance sample
      `session-20260617073649-4b5ec478` match 2 without committing `data/`.

## Focused unit coverage

- Config:
  - defaults for new BGM settings
  - env overrides and clamps
  - publish preset still prefers `data/bgm/library.json` when no explicit BGM
    path/library is set
- Source music:
  - zero spans leaves planned beds unchanged
  - partial spans split/suppress only overlapped rendered windows
  - padded spans merge correctly
  - coverage over 60% globally skips BGM
  - legacy `SourceMusicDetection(True, ...)` with no spans still globally skips
  - chunked recordings translate local detector spans to source/rendered time
- Phase planning:
  - >=10 minute export with adequate distinct library tracks emits three phases
    and two switches
  - flat signals fall back to proportional switch points
  - KDA/highlight intensity can move switch candidates within gap bounds
  - small libraries degrade to one or two beds without repeated source paths
  - teaser-first timelines still start BGM at first main content
- Exporter:
  - crossfade-shaped overlapping beds survive validation
  - each BGM bed is ducked against base audio before `amix`
  - loudnorm appending still maps `[aout]`

## Validation commands

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py tests\pipeline\test_editing_service.py tests\pipeline\test_ffmpeg_resilience.py tests\pipeline\test_quality_report_service.py tests\pipeline\test_reference_validation.py
.\.venv\Scripts\python.exe -m pytest tests
.\.venv\Scripts\python.exe -m compileall src tests
```

Real sample validation, after unit tests pass:

```powershell
$env:ARL_POSTPROCESS_PRESET="publish"
$env:ARL_EDIT_BGM_LIBRARY_PATH="data/bgm/library.json"
$env:ARL_EDIT_SKIP_BGM_WHEN_SOURCE_HAS_MUSIC="1"
.\.venv\Scripts\python.exe -m arl.cli edit-planner --session-id session-20260617073649-4b5ec478 --match-indices 2 --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli exporter --session-id session-20260617073649-4b5ec478 --match-indices 2 --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli quality-report --session-id session-20260617073649-4b5ec478 --match-indices 2
```

Expected real-sample evidence:

- Match 2 has BGM beds in non-musical regions instead of zero beds.
- If the local library has enough distinct tracks, long exports show at least
  two switches and overlapping crossfade timing in generated bed rows/filtergraph.
- Quality report has no new audio/loudness warnings attributable to this task.

## Risk points

- Source-music spans are sampled windows, not perfect music segmentation. Keep
  padding configurable and preserve the dominant-coverage global skip.
- Crossfade is represented with overlapping `AudioBed` rows. Exporter validation
  currently allows overlap; tests should lock that behavior.
- More split beds mean more ffmpeg audio inputs. Keep practical minimum fragment
  duration and avoid creating tiny beds around every padded source-music span.
- Do not commit runtime `data/` artifacts, including user BGM files and exports.
