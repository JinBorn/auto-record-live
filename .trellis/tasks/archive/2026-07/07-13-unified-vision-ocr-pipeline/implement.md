# Implementation Plan

## Child Order

1. `07-13-vision-analysis-foundation`
2. `07-13-migrate-timer-kda-ocr`
3. `07-13-death-respawn-match-result-ocr`
4. `07-13-vision-analysis-integration-performance`

Children are sequential because each consumes the preceding durable contracts and evidence.

## Parent Integration Gates

- Review all child contracts for a single source-timeline convention.
- Ensure reset/status/postprocess orchestration handles the new stage.
- Run full test suite and representative segmented plus non-segmented recordings.
- Compare legacy vs shared assets for timer boundaries and KDA events.
- Record performance baseline and new-stage metrics.
- Update orchestration, export, and editing-quality specs.
- Keep compatibility fallbacks until parity and human review succeed.

## Validation Commands

```powershell
.\.venv\Scripts\python.exe -m pytest tests\vision tests\pipeline -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src
```

Representative CLI validation will include cached reruns and forced vision reprocessing once command shape is implemented.
