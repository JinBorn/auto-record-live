# Implementation Plan

## PR1 - Python postprocess command

Goal: add a single-pass command that composes existing post-live services.

Files:

- `src/arl/postprocess/__init__.py`
- `src/arl/postprocess/service.py`
- `src/arl/cli.py`
- `tests/pipeline/test_postprocess_service.py`
- `tests/pipeline/test_cli_stage_hint.py` or new CLI parser test file

Steps:

1. Add `PostProcessService` with `run_once()` calling:
   - `SemanticStageHintService.run()`
   - `SegmenterService.run()`
   - `SubtitleService.run()`
   - `ExporterService.run()`
2. Add `arl postprocess --once` parser route.
3. Add tests asserting parser support and service call order.
4. Run targeted tests.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline\test_postprocess_service.py tests\pipeline\test_cli_stage_hint.py
```

## PR2 - Local status command

Goal: one local JSON command summarizes health/degraded/action-required state.

Files:

- `src/arl/status/__init__.py`
- `src/arl/status/service.py`
- `src/arl/cli.py`
- `tests/pipeline/test_status_service.py`

Steps:

1. Implement `StatusService(settings).build()` as read-only aggregation.
2. Parse existing state through typed models:
   - orchestrator state via `load_orchestrator_state`
   - recorder/subtitles/exporter state via their state models
   - manifests via `load_models`
   - audit logs via matching audit event models where available
3. Compute:
   - session/job status counts
   - asset counts
   - missing subtitle/export counts
   - subtitle fallback reason counts
   - exporter fallback/batch-aborted counts
   - recorder deferred/manual/recent-failure counts
   - recovery pending action count
4. Derive `summary.health` as `ok`, `degraded`, or `action_required`.
5. Add `arl status` route printing JSON.
6. Add tests for empty, healthy, degraded, and action-required fixtures.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline\test_status_service.py
```

## PR3 - Windows postprocess launcher

Goal: add supervised PowerShell loop for unattended post-live processing.

Files:

- `scripts/windows-postprocess-loop.ps1`
- `README.md`
- `.trellis/spec/backend/launcher-conventions.md`

Steps:

1. Create `windows-postprocess-loop.ps1` mirroring recorder launcher bootstrap.
2. Add `ARL_POSTPROCESS_INTERVAL_SECONDS` env support.
3. Invoke `arl postprocess --once` in a retrying loop.
4. Document the fourth PowerShell window in README.
5. Update launcher conventions to include the fourth launcher and postprocess
   interval env var.

Validation:

```powershell
.\scripts\windows-postprocess-loop.ps1 -IntervalSeconds 1
```

For manual validation, stop after observing two successful loop iterations.

## PR4 - End-to-end verification

Goal: prove the production slice works on current configured rooms.

Commands:

```powershell
.\.venv\Scripts\python.exe -m pytest
npm run test:probe
.\.venv\Scripts\python.exe -m arl.cli windows-agent --once
.\.venv\Scripts\python.exe -m arl.cli orchestrator --once
.\.venv\Scripts\python.exe -m arl.cli recorder
.\.venv\Scripts\python.exe -m arl.cli postprocess --once
.\.venv\Scripts\python.exe -m arl.cli status
```

Use an isolated temp storage/settings harness for real-room smoke where needed,
so existing `data/` is not polluted by verification artifacts.

## Rollback Points

- PR1 rollback: remove `src/arl/postprocess/` and CLI command.
- PR2 rollback: remove `src/arl/status/` and CLI command.
- PR3 rollback: remove `scripts/windows-postprocess-loop.ps1` and docs/spec
  mentions.

## PR5 - ASR CPU/CUDA fallback

Goal: make real subtitle generation recover from common CUDA runtime gaps while
keeping explicit device choices observable.

Files:

- `src/arl/config.py`
- `src/arl/subtitles/models.py`
- `src/arl/subtitles/service.py`
- `src/arl/status/service.py`
- `tests/test_config.py`
- `tests/pipeline/test_subtitles_service.py`
- `tests/pipeline/test_status_service.py`
- `README.md`
- `.trellis/spec/backend/orchestration-contracts.md`
- `.trellis/spec/backend/quality-guidelines.md`

Steps:

1. Add `ARL_WHISPER_DEVICE`, `ARL_WHISPER_COMPUTE_TYPE`, and
   `ARL_WHISPER_CPU_COMPUTE_TYPE` settings.
2. Resolve device candidates as:
   - `auto`: CUDA `float16`, then CPU `int8` by default
   - `cuda`: CUDA only
   - `cpu`: CPU only
3. Cache faster-whisper models by `(device, compute_type)`.
4. On CUDA initialization or lazy transcribe iteration failure in `auto`, retry
   the same boundary once on CPU and disable CUDA for the rest of the batch.
5. Emit subtitle audit fields for `device`, `compute_type`, and CPU fallback
   visibility; surface aggregate device counts in `arl status`.
6. Add regression tests for CUDA init failure, lazy CUDA runtime failure,
   explicit CUDA no-fallback, and explicit CPU-only behavior.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline\test_subtitles_service.py tests\pipeline\test_status_service.py tests\test_config.py
```

## PR6 - Multi-room automatic live monitoring

Goal: support production unattended monitoring for multiple configured rooms
across multiple platforms, including multiple rooms on the same platform.

Files:

- `src/arl/config.py`
- `src/arl/orchestrator/service.py`
- `src/arl/status/service.py`
- `tests/test_config.py`
- `tests/orchestrator/test_multi_platform.py`
- `README.md`
- `.trellis/spec/backend/orchestration-contracts.md`

Steps:

1. Add plural room envs:
   - `ARL_DOUYIN_ROOM_URLS` / `ARL_DOUYIN_STREAMER_NAMES`
   - `ARL_BILIBILI_ROOM_URLS` / `ARL_BILIBILI_STREAMER_NAMES`
2. Expand each platform token in `ARL_PLATFORMS` into one probe settings object
   per configured room while preserving legacy single-room env behavior.
3. Change orchestrator active maps to use `platform:room_url` stream keys so
   same-platform rooms can be live and recorded independently.
4. Keep duplicate handling scoped to the same `(platform, room_url)` and close
   sessions/jobs only for the matching room on `live_stopped`.
5. Surface active stream keys in `arl status` while keeping unique
   `active_platforms` for operator summaries.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py tests\windows_agent\test_service.py tests\windows_agent\test_state_store.py tests\windows_agent\test_registry.py tests\orchestrator\test_multi_platform.py tests\pipeline\test_status_service.py
```

## PR7 - Optional Windows autostart and supervisor

Goal: allow unattended startup after Windows logon without enabling autostart
by default.

Files:

- `scripts/windows-agent-loop.ps1`
- `scripts/windows-supervisor.ps1`
- `scripts/windows-autostart.ps1`
- `README.md`
- `.trellis/spec/backend/launcher-conventions.md`

Steps:

1. Make `windows-agent-loop.ps1` usable from `.env`/parent env without
   mandatory `-RoomUrl` / `-StreamerName` arguments.
2. Add `windows-supervisor.ps1` to start the four long-running launchers hidden,
   write logs under `data/tmp/launcher-logs/`, and restart exited children.
3. Add `windows-autostart.ps1` with explicit `Install`, `Uninstall`, and
   `Status` actions backed by an opt-in Scheduled Task. Default trigger is
   `AtLogOn`; `-TriggerMode AtStartup` is available for explicit boot-start
   deployments.
4. Document that autostart is opt-in and disabled unless the operator runs
   `windows-autostart.ps1 -Action Install`.

Validation:

```powershell
powershell -NoProfile -Command { $null = [scriptblock]::Create((Get-Content scripts/windows-supervisor.ps1 -Raw)); $null = [scriptblock]::Create((Get-Content scripts/windows-autostart.ps1 -Raw)); $null = [scriptblock]::Create((Get-Content scripts/windows-agent-loop.ps1 -Raw)) }
.\scripts\windows-autostart.ps1 -Action Status
```

## PR8 - Long-run local maintenance

Goal: prevent unattended runs from growing local logs and audit JSONL files
without deleting durable asset manifests.

Files:

- `src/arl/config.py`
- `src/arl/maintenance/__init__.py`
- `src/arl/maintenance/service.py`
- `src/arl/cli.py`
- `tests/pipeline/test_maintenance_service.py`
- `tests/pipeline/test_cli_unattended.py`
- `tests/test_config.py`
- `README.md`
- `.trellis/spec/backend/orchestration-contracts.md`

Steps:

1. Add `arl maintenance --once`.
2. Add maintenance envs:
   - `ARL_MAINTENANCE_MAX_JSONL_BYTES`
   - `ARL_MAINTENANCE_KEEP_RECENT_LINES`
   - `ARL_LAUNCHER_LOG_RETAIN_COUNT`
   - `ARL_MAINTENANCE_ARCHIVE_DIR`
3. Archive already-consumed prefixes from orchestrator input logs and reset the
   corresponding cursor offsets.
4. Archive old prefixes from pure audit logs while keeping recent tail lines.
5. Rotate `data/tmp/launcher-logs/*.log` by mtime.
6. Do not compact asset manifests in this slice; downstream stages use them as
   durable indexes.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline\test_maintenance_service.py tests\pipeline\test_cli_unattended.py tests\test_config.py
```

## PR9 - Runtime soak check

Goal: provide one operator command for repeated unattended health cycles.

Files:

- `src/arl/soak/__init__.py`
- `src/arl/soak/service.py`
- `src/arl/cli.py`
- `tests/pipeline/test_soak_service.py`
- `tests/pipeline/test_cli_unattended.py`
- `README.md`
- `.trellis/spec/backend/orchestration-contracts.md`

Steps:

1. Add `arl soak`.
2. Default to `--cycles 3 --interval-seconds 30`.
3. Each cycle runs:
   - `windows-agent` once
   - `orchestrator` once
   - `recorder` unless `--skip-recorder`
   - `postprocess` unless `--skip-postprocess`
   - `maintenance` when `--maintenance` is set
   - `status`
4. Catch stage exceptions, continue to status, and return a JSON report with
   per-stage elapsed time, errors, final health, and failed stage count.
5. Exit non-zero only when a stage raised; degraded/action-required status
   remains visible in the JSON report.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline\test_soak_service.py tests\pipeline\test_cli_unattended.py
.\.venv\Scripts\python.exe -m arl.cli soak --cycles 1 --interval-seconds 0 --skip-recorder --skip-postprocess
```

## Review Gate

Before implementation starts:

- PRD, design, and implementation plan reviewed.
- Task status moved to `in_progress` with `task.py start`.
