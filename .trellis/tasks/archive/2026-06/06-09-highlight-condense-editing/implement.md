# Implementation Plan

## Preconditions

- Keep existing uncommitted H.265 export changes separate in review context.
- Do not start by changing generated `data/` artifacts.
- Run backend specs before editing.

## Steps

1. [x] Update segmenter boundary logic
   - Add helper to resolve valid `post_game` times.
   - End each match at the first valid post-game time before the next in-game
     start or duration.
   - Add tests in `tests/pipeline/test_segmenter_service.py`.

2. [x] Add highlight plan contract
   - Add typed models for highlight windows/plans.
   - Add settings for conservative padding/gap thresholds if needed.
   - Add planner service under a stage-owned module.

3. [x] Wire postprocess
   - Run planner after subtitles and before exporter.
   - Add reset support for highlight plan rows/state.

4. [x] Add exporter support for highlight plans
   - Load plan map by `(session_id, match_index)`.
   - For planned exports, build a filtergraph with subtitle burn-in plus
     `select`/`aselect`/timestamp reset.
   - Preserve existing no-plan behavior.

5. [x] Tests
   - Segmenter post-game tests.
   - Planner generation/idempotency tests.
   - Exporter command test for planned output.
   - Reset test for highlight plans.

6. [x] Validation
   - `.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_segmenter_service.py`
   - `.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_highlight_planner_service.py`
   - `.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_postprocess_service.py tests/pipeline/test_postprocess_reset_service.py`
   - `.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_status_service.py tests/pipeline/test_cli_unattended.py tests/test_config.py`
   - `.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_ffmpeg_resilience.py`
   - `.\.venv\Scripts\python.exe -m compileall -q src`
   - `.\.venv\Scripts\python.exe -m pytest tests`

## Risk And Rollback

- Risk: cut windows may still feel jumpy. Mitigate with conservative padding
  and merge thresholds.
- Risk: ffmpeg filter quoting on Windows. Test command construction with paths
  containing drive colons and forward slashes.
- Rollback: disable planner by leaving no plan rows or adding a setting gate;
  exporter falls back to current full-boundary behavior when no plan exists.
