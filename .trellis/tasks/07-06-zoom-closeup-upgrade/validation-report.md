# Validation Report

## Automated Checks

- `.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_ffmpeg_resilience.py tests/pipeline/test_reference_validation.py tests/pipeline/test_editing_service.py tests/test_config.py`
  - Result: `202 passed`
- `.\.venv\Scripts\python.exe -m pytest tests`
  - Result: `676 passed`
- `.\.venv\Scripts\python.exe -m compileall src tests`
  - Result: passed

## Real Sample

Session: `session-20260702092321-bc90812b`, match `2`

Commands:

```powershell
$env:ARL_POSTPROCESS_PRESET='publish'
$env:ARL_EDIT_ZOOM_MODE='closeup'
$env:ARL_EDIT_ZOOM_MAX_SEGMENTS='3'
$env:ARL_EDIT_ZOOM_EASE_SECONDS='0.4'
$env:ARL_EDIT_ZOOM_CLOSEUP_SECONDS='6'
.\.venv\Scripts\python.exe -m arl.cli edit-planner --force-reprocess --session-id session-20260702092321-bc90812b --match-index 2
.\.venv\Scripts\python.exe -m arl.cli exporter --force-reprocess --session-id session-20260702092321-bc90812b --match-index 2
.\.venv\Scripts\python.exe -m arl.cli quality-report --session-id session-20260702092321-bc90812b --match-index 2 --top-gaps 5
```

Results:

- Edit plan regenerated successfully with `46` timeline segments.
- Latest edit plan has `3` transformed zoom segments.
- Each zoom segment is `6.0s`, `target=chat`, `ease_in_seconds=0.4`, `ease_out_seconds=0.4`.
- Exporter completed successfully and wrote `data\exports\bilibili\session-20260702092321-bc90812b_match02.mp4`.
- Quality report shows `Zoom=3`, export `1920x1080`, export duration `8.54min`.
- Remaining quality-report warning is `teaser_segment_count_out_of_range` (`4`, threshold `1..3`), unrelated to zoom close-up behavior.
- Spot-check frames under `data\tmp\zoom-closeup-frames\` verified nonblank 1920x1080 output and visible eased close-up crop without subtitle/HUD misalignment.
