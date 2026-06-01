# Design: production unattended hardening

## Scope

First production hardening slice:

1. Add a Python `postprocess` stage command that composes existing post-live
   services.
2. Add a Windows `windows-postprocess-loop.ps1` launcher that supervises the
   command repeatedly.
3. Add a local `status` command that summarizes state/audit files into one JSON
   object.

This keeps the slice small enough to verify on the current machine while
closing the biggest unattended gap: recordings no longer wait for manual
post-live commands, and the operator no longer has to grep many JSONL files to
see degraded state.

## Architecture

### `PostProcessService`

New module: `src/arl/postprocess/service.py`.

`PostProcessService(settings).run_once()` executes existing idempotent services
in this order:

1. `SemanticStageHintService(settings).run()`
2. `SegmenterService(settings).run()`
3. `SubtitleService(settings).run()`
4. `ExporterService(settings).run()`

Rationale:

- Semantic hints run before segmenter so new recording assets get stage anchors
  before boundaries are built. When no subtitle-derived signals exist yet,
  semantic hints already fall back to a template strategy.
- `SubtitleService.run()` already auto-triggers subtitle-to-signal ingest after
  SRT emission, so a later cycle can use subtitle-derived signals for sessions
  not yet hinted.
- All four services have their own processed-state/idempotency files. The
  composed command should not add a second global state file in this first
  slice.

`PostProcessService.run_once()` wraps each stage boundary with compact logs:

```text
[postprocess] starting
[postprocess] stage=stage-hints-semantic starting
[postprocess] stage=stage-hints-semantic completed
...
[postprocess] completed
```

Exceptions are not swallowed inside the Python service. The PowerShell launcher
owns repeated supervision; unit tests can assert the service calls the stages in
order.

### CLI

Add parser command:

```powershell
.\.venv\Scripts\python.exe -m arl.cli postprocess --once
```

`--once` is accepted for symmetry with `windows-agent` and `orchestrator`.
Initially it is required/implicit behavior: the command is single-pass and then
exits. No Python-internal infinite loop is added.

Add parser command:

```powershell
.\.venv\Scripts\python.exe -m arl.cli status
```

It prints one JSON object to stdout with `ensure_ascii=False` and `indent=2`.
The command is local-only and reads existing state/audit/manifest files.

### `StatusService`

New module: `src/arl/status/service.py`.

It returns a plain `dict[str, Any]` because this is an operator report, not a
durable contract. All durable inputs are still parsed through existing Pydantic
models where available:

- `OrchestratorStateFile` via `load_orchestrator_state`
- `RecorderStateFile`
- `SubtitleStateFile`
- `ExporterStateFile`
- JSONL manifests via `load_models`
- audit JSONL files via `load_models` where model exists

Status shape:

```json
{
  "summary": {
    "health": "ok|degraded|action_required",
    "generated_at": "2026-06-01T00:00:00Z"
  },
  "orchestrator": {
    "sessions_by_status": {"live": 2},
    "recording_jobs_by_status": {"queued": 2}
  },
  "recorder": {
    "recording_assets": 2,
    "processed_jobs": 2,
    "deferred_jobs": 0,
    "manual_required_jobs": 0,
    "recent_failure_events": 0
  },
  "postprocess": {
    "match_boundaries": 2,
    "subtitle_assets": 2,
    "export_assets": 2,
    "missing_subtitles": 0,
    "missing_exports": 0
  },
  "subtitles": {
    "processed_matches": 2,
    "fallback_reasons": {"transcribe_failed": 2}
  },
  "exporter": {
    "processed_matches": 2,
    "fallback_events": 0,
    "batch_aborted_events": 0
  },
  "recovery": {
    "pending_actions": 0
  }
}
```

Health derivation:

- `action_required` if any recorder manual-required job exists, any pending
  recovery action exists, any orchestrator job status is `failed`, or exporter
  batch-aborted events exist.
- `degraded` if subtitle fallbacks exist, exporter fallback events exist,
  missing subtitle/export counts are non-zero, or recorder failure events exist.
- `ok` otherwise.

Do not include raw stream URLs, cookies, stream headers, full transcript text, or
full audit payloads.

### PowerShell launcher

New file: `scripts/windows-postprocess-loop.ps1`.

Parameters:

```powershell
param(
  [string]$ProjectPath = "",
  [int]$IntervalSeconds = 0
)
```

Interval resolution:

1. explicit `-IntervalSeconds`
2. `$env:ARL_POSTPROCESS_INTERVAL_SECONDS`
3. default `30`

Bootstrap mirrors `windows-recorder-loop.ps1`:

- resolve project path
- create/check `.venv`
- pip availability probe with try/catch
- install mode via `ARL_WIN_INSTALL_MODE`
- install spec `pip install -e .[subtitles]`
- read `.env` as UTF-8
- log `[ARL] postprocess loop started`
- `while ($true) { arl postprocess --once; Start-Sleep ... }`

The launcher catches native command failures, logs one warning, and continues
after sleep. It does not run cookie health because it does not directly touch
platform auth.

### Configuration

No Python settings field is required for this slice.

New launcher env var:

- `ARL_POSTPROCESS_INTERVAL_SECONDS` (int, default `30`) controls only
  `windows-postprocess-loop.ps1`.

### Compatibility

- Existing commands remain unchanged.
- Existing post-live manual commands remain valid.
- New `postprocess` command uses existing service idempotency state. Re-running
  should not duplicate boundaries, subtitle assets, exports, or signals.
- `status` is additive and read-only.

## Validation Strategy

- Unit tests for CLI parser routes: `postprocess --once`, `status`.
- Unit test for `PostProcessService` ordering using mocked service classes.
- Unit tests for `StatusService` on empty files, healthy manifests, subtitle
  degraded state, missing subtitle/export counts, and action-required recovery
  state.
- Existing E2E golden path should keep passing.
- Manual smoke on real configured rooms:
  `windows-agent --once -> orchestrator --once -> recorder -> postprocess --once -> status`.

## Risks

- The semantic hint template may create broad fallback boundaries before real
  subtitle signals exist. This already matches existing service behavior; this
  task automates it but does not change segmentation strategy.
- `status` may be treated as a stable machine API by operators. Keep output
  predictable, but document it as operator JSON rather than durable storage.
- PowerShell launcher bootstrap duplication grows. This follows existing
  launcher-conventions; extracting shared PowerShell helpers is deferred.

