# Implementation Plan: User-Provided External Reference Inserts

## Checklist

1. Config and manifest contract
   - Add insert settings to `EditingSettings`:
     - `insert_enabled: bool = False`
     - `insert_manifest_path: Path | None = None`
     - `insert_max_segments: int = 1`
   - Load env values:
     - `ARL_EDIT_INSERTS_ENABLED`
     - `ARL_EDIT_INSERT_MANIFEST_PATH`
     - `ARL_EDIT_INSERT_MAX_SEGMENTS`
   - Clamp max segments to `>= 0`.
   - Add typed manifest models if parsing in the service would otherwise use
     untyped dictionaries.
   - Add config tests for defaults and env loading.

2. Planner manifest loading
   - Add helper methods in `EditingPlannerService` to load and validate insert
     clips.
   - Treat missing path, missing file, parse errors, malformed entries, missing
     source files, and bad trim ranges as recoverable skips.
   - Log concise skip reasons with `session_id`, `match_index`, and `reason`
     where match context is available.
   - Keep base teaser/main plan unchanged when insert support is disabled or no
     valid insert matches.

3. Planner insertion
   - Insert `TimelineSegment(role="insert", source_path=..., ...)` immediately
     after the first teaser whose `reason` matches manifest trigger reasons.
   - Stop at `settings.editing.insert_max_segments`.
   - Apply insertion before building audio instructions so SFX timing uses final
     rendered timeline duration.
   - Add planner tests for:
     - disabled/default no-op
     - absent manifest preserves base plan
     - missing source clip is skipped
     - valid clip becomes an insert segment after matching teaser
     - max segments clamps/stops insertion

4. Exporter validation
   - Accept roles `teaser`, `insert`, `main`.
   - Allow inserts only before the main segment.
   - Require recording-sourced segments to keep `source_path is None`.
   - Require insert segments to have an existing local `source_path` and valid
     trim range.
   - Keep main-boundary validation strict.
   - Add exporter fallback tests for stale/manual invalid insert plans.

5. Exporter ffmpeg input mapping
   - Build a deterministic list/map of insert source inputs.
   - Add insert source files to the command after the recording input and before
     BGM/SFX inputs.
   - Route each segment video/audio chain to `[0:v]/[0:a]` or the matching insert
     input index.
   - Adjust audio filter input indexing by passing
     `first_audio_asset_input_index=1 + len(insert_inputs)`.
   - Add tests for:
     - `teaser -> insert -> main` concat graph
     - insert input appears before audio inputs
     - BGM/SFX filters use shifted indexes when inserts exist
     - subtitles and punch-in filters still appear in segment chains

6. Spec update
   - Update `.trellis/spec/backend/export-configuration.md`:
     - document insert env variables
     - replace the existing "insert/source_path => fallback" row
     - add validation/error matrix rows for local insert clips
     - update required tests

7. Focused validation
   - Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline\test_editing_service.py tests\pipeline\test_ffmpeg_resilience.py tests\test_config.py -q
```

8. Broader validation before finish
   - Run:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
git diff --check
.\.venv\Scripts\python.exe -m pytest tests\pipeline tests\test_config.py -q
```

## Risk Points

- FFmpeg input indexes are the highest-risk area. Insert inputs must not collide
  with BGM/SFX inputs.
- Exporter validation must stay fail-closed for manually edited or stale
  `edit-plans.jsonl` rows.
- Planner skip behavior must not mark a base edit plan as failed just because
  optional insert assets are missing.
- Existing edit-plan behavior is already covered by tests; update assertions
  carefully rather than weakening them.

## Review Gate

Before implementation starts, confirm the MVP scope:

- explicit local JSON manifest only
- disabled by default
- one insert by default, configurable max
- deterministic placement after the first matching high-signal teaser
- no downloads, no directory scanning, no semantic copyrighted clip selection
- missing manifest or source files skip without blocking base edit-plan output
