# Implementation Plan: ASS Subtitle Styling And Export Wiring

## Checklist

- [x] Inspect existing exporter subtitle command tests and config tests.
- [x] Add ASS subtitle conversion helper:
  - [x] SRT cue parser
  - [x] timestamp conversion to ASS `H:MM:SS.cc`
  - [x] ASS text escaping
  - [x] default reference-style document generation
- [x] Add export config fields and env loading for ASS subtitle mode/style.
- [x] Wire exporter burn-in path:
  - [x] keep placeholder subtitles unburned
  - [x] keep SRT burn-in when ASS flag is disabled
  - [x] generate `.ass` sidecar and burn that path when ASS flag is enabled
  - [x] reuse existing subtitle filter path escaping
- [x] Add focused tests:
  - [x] ASS helper emits expected style sections/fields
  - [x] SRT timing/text survives conversion
  - [x] config env values load into `ExportSettings`
  - [x] exporter uses `.ass` only when burn + ASS are enabled
  - [x] exporter preserves existing SRT burn and soft-subtitle behavior
- [x] Run focused validation.

## Validation Commands

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline\test_ffmpeg_resilience.py tests\pipeline\test_subtitles_service.py tests\test_config.py -q
```

If exporter behavior changes beyond command construction, run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline -q
```

## Risky Files

- `src/arl/exporter/service.py`: command construction is heavily regression
  tested; keep changes local to subtitle path selection.
- `src/arl/config.py`: env defaults must preserve existing behavior.
- `tests/pipeline/test_ffmpeg_resilience.py`: exporter tests patch
  `arl.exporter.service.subprocess.run`, so keep the existing subprocess import
  shim intact.

## Rollback Points

- If exporter wiring becomes too invasive, keep `src/arl/subtitles/ass.py` and
  config tests, then defer exporter integration.
- If derived `.ass` sidecars create idempotency issues, generate the ASS file in
  a temp render directory instead of next to the SRT.
