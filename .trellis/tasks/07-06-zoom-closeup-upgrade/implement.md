# Zoom close-up upgrade implementation plan

## Checklist

1. Config and contract extension
   - Add optional ease fields to `TimelineVideoTransform` with backward
     compatible defaults.
   - Add zoom close-up settings/env parsing:
     `ARL_EDIT_ZOOM_MODE`, close-up duration, ease seconds, min interval,
     chat-burst enablement, chat-burst sample interval, and threshold.
   - Update publish preset so zoom max segments defaults to `3` when zoom envs
     are not explicitly overridden.
   - Add config tests for clamping, aliases, publish defaults, and legacy mode.

2. Trigger collection
   - Reuse subtitle KDA parsing helpers for kill-event timestamps.
   - Add a small chat-burst detector using `vision.frame_sampler` over the
     bottom-left chat region; missing media or insufficient frames should
     degrade to no chat candidates.
   - Add deterministic candidate sorting: KDA, chat, fallback; then timestamp.
   - Enforce `zoom_min_interval_seconds`.

3. Timeline splitting
   - Replace `_apply_zoom_transforms()` with a close-up splitter when
     `zoom_mode=closeup`.
   - Preserve legacy whole-segment behavior behind `zoom_mode=legacy`.
   - Preserve segment role/reason/text/source path and total duration.
   - Compute audio beds/SFX after splitting so output-time annotations map to
     the final timeline.
   - Update stale-plan checks so old whole-segment zoom plans are regenerated
     under close-up mode.

4. Exporter ease rendering
   - Keep the current static `scale,crop` filters when ease is `0`.
   - For eased punch-ins, generate `zoompan` filters using `in_time`,
     configured ease-in/out, target scale, and probed source dimensions.
   - Keep invalid transform fallback behavior.

5. Quality report
   - Ensure zoom metrics count transformed close-up pieces and include per-piece
     duration/target as they do today.
   - Add or update report tests if duration or target assertions need to change.

6. Documentation and spec
   - Update `.env.example`, `README.md`, and
     `.trellis/spec/backend/export-configuration.md` with close-up envs,
     legacy rollback, and ease behavior.

7. Validation
   - Focused tests:
     ```powershell
     .\.venv\Scripts\python.exe -m pytest tests/pipeline/test_editing_service.py tests/pipeline/test_ffmpeg_resilience.py tests/pipeline/test_quality_report_service.py tests/test_config.py
     ```
   - Full checks:
     ```powershell
     .\.venv\Scripts\python.exe -m pytest tests
     .\.venv\Scripts\python.exe -m compileall src tests
     ```
   - Real validation on `session-20260702092321-bc90812b`:
     ```powershell
     .\.venv\Scripts\python.exe -m arl.cli edit-planner --force-reprocess --session-id session-20260702092321-bc90812b
     .\.venv\Scripts\python.exe -m arl.cli exporter --force-reprocess --session-id session-20260702092321-bc90812b --match-index 2
     .\.venv\Scripts\python.exe -m arl.cli quality-report --session-id session-20260702092321-bc90812b --match-index 2 --top-gaps 5
     ```
   - Manual spot check one exported match for eased zoom motion and A/V sync.

## Review Gate Before Start

- Confirm close-up mode should become the publish-preset default while legacy
  mode remains env-selectable.
- Confirm KDA close-ups should default to center target and chat bursts to chat
  target, while `ARL_EDIT_ZOOM_TARGET` can override globally if needed.
- Confirm v1 chat-burst detection may be heuristic and best-effort; no OCR text
  reading is required.

## Risky Files

- `src/arl/shared/contracts.py`
  - Persisted `TimelineVideoTransform` rows must remain backward compatible.
- `src/arl/editing/service.py`
  - Timeline splitting affects edit duration, SFX mapping, stale-plan checks,
    and quality report zoom counts.
- `src/arl/exporter/service.py`
  - FFmpeg filter expressions must preserve valid labels and Windows quoting.
- `tests/pipeline/test_editing_service.py`
  - Many existing tests assert exact timeline order and transform placement.

## Rollback Points

- Set `ARL_EDIT_ZOOM_ENABLED=0` to disable all zoom transforms.
- Set `ARL_EDIT_ZOOM_MODE=legacy` to restore whole-segment static transforms.
- Set `ARL_EDIT_ZOOM_EASE_SECONDS=0` to keep close-up cuts but disable easing.
- Set `ARL_EDIT_ZOOM_CHAT_BURST_ENABLED=0` to use only KDA/fallback triggers.
