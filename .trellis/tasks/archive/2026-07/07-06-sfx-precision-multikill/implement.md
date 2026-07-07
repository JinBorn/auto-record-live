# SFX precision and multi-kill variants implementation plan

## Checklist

- [x] Add edit configuration fields and environment parsing for library path,
      timing offset, min interval, max hits, KDA alignment toggle, and
      multi-kill keyword window.
- [x] Add config tests for defaults, env overrides, disabled alignment, and
      invalid low/high values.
- [x] Implement tolerant SFX library loading for `kill_coin` and `multi_kill`,
      reusing the existing transition/BGM manifest conventions where possible.
- [x] Add KDA event parsing from `kda_change` cues, including kill delta,
      death-only filtering, source timestamp selection, and malformed cue skips.
- [x] Add source-to-timeline mapping helper with unit coverage for normal
      segments, teaser segments, transitions, trimmed gaps, repeated teaser/main
      occurrences, and segment edges.
- [x] Change edit-planner SFX scheduling to prefer mapped KDA kill events,
      select single/multi-kill assets, apply offset, then rate-limit and cap.
- [x] Preserve segment-start fallback only when an eligible segment contains no
      mapped KDA kill event.
- [x] Verify transition whoosh planning still uses the existing category and is
      not affected by kill SFX rate limiting.
- [x] Update README or project docs with the `data/sfx/library.json` format and
      sound-file placement.
- [x] Update `.trellis/spec/backend/export-configuration.md` with any durable
      SFX contract changes discovered during implementation.

## Tests to add or update

- KDA kill event schedules SFX near the mapped kill moment instead of segment
  start.
- Death-only KDA changes do not emit coin SFX.
- Double-kill KDA delta selects `multi_kill` when present and falls back to
  `kill_coin` or generated coin when absent.
- Event in a trimmed-out gap does not schedule a mapped hit.
- Event in a teaser segment maps to teaser output time.
- Same event present in teaser and main maps to the first rendered occurrence.
- Missing or malformed `data/sfx/library.json` preserves generated coin
  fallback.
- Existing configured `ARL_EDIT_SFX_PATH` remains a hard override.
- Existing exporter SFX adelay/amix tests still pass unchanged.
- Quality-report SFX nearest KDA delta remains populated and meaningful.

## Validation commands

Run focused tests first:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/pipeline/test_editing_service.py tests/pipeline/test_quality_report_service.py tests/pipeline/test_ffmpeg_resilience.py
```

Then run the full suite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests
```

For runtime validation, regenerate the Demo2 quality report on the known sample
and confirm each emitted kill SFX is within +/-1s of the nearest `kda_change`.

## Verification results

- `.\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/pipeline/test_editing_service.py tests/pipeline/test_quality_report_service.py tests/pipeline/test_ffmpeg_resilience.py`
  passed with 198 tests.
- `.\.venv\Scripts\python.exe -m pytest tests` passed with 671 tests.

## Risk points

- Exporter audio input indexes are label-sensitive; avoid changing
  `SoundEffectHit` shape or exporter filter graph unless required.
- The user-managed `data/` directory is runtime state and must not be committed.
- KDA cues can be malformed or absent; parsing failures must degrade to
  fallback segment-start behavior instead of failing export planning.
