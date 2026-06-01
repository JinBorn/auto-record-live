# Production unattended hardening

## Goal

Move the local live-recording pipeline from "MVP usable with operator attention"
to a first production-grade unattended baseline: after Windows boot and launcher
startup, the system should keep probing, recording, segmenting, subtitling, and
exporting over long runs while making degraded states visible and recoverable.

This task should produce a concrete first hardening slice, not a vague rewrite.

## User Value

- The operator can leave the pipeline running for long sessions without watching
  every PowerShell window.
- Real platform, cookie, ffmpeg, ASR, and export failures become visible through
  health/status output and audit files instead of silently degrading.
- When a stage fails, the next runs either recover automatically, continue with a
  deliberate placeholder artifact, or surface a manual recovery action.

## Confirmed Facts

- Current repository has no active Trellis task before this one; worktree had
  only the subtitle lazy-runtime fix/spec update from the preceding validation
  turn.
- Full automated baseline after that fix: Python `pytest` has 338 passing tests;
  Node `npm run test:probe` has 14 passing tests.
- Real short E2E validation against the configured Douyin and Bilibili rooms:
  both rooms were live, both yielded `direct_stream`, both produced short MP4
  recordings via ffmpeg, segmenter emitted boundaries, subtitle stage emitted
  SRT files, exporter emitted artifacts.
- Current host lacks CUDA runtime dependency `cublas64_12.dll`; faster-whisper
  can be imported but actual transcription falls back to placeholder SRT. The
  subtitle service now catches lazy `segments` iteration failures and disables
  the cached model for the rest of the batch.
- Existing production-hardening features:
  - optional `[subtitles]` dependency and `data/tmp/whisper-models` cache
  - subtitle audit log `data/tmp/subtitles-events.jsonl`
  - language confidence gate
  - `arl cookie-health`
  - Windows agent startup cookie-health gate
  - recorder retry/backoff/session budget and manual recovery actions
  - actual recording resolution validation for direct streams
  - exporter non-retryable short-circuit and batch fallback budget
  - PowerShell launchers for agent/orchestrator/recorder with shared venv
    bootstrap and dependency sentinel
- Current launcher shape:
  - `windows-agent-loop.ps1` loops `arl windows-agent --once`.
  - `windows-recorder-loop.ps1` loops `arl recorder`.
  - `windows-orchestrator-loop.ps1` starts `arl orchestrator`, whose Python
    process owns its internal poll loop.
  - No launcher currently runs the post-live stages (`segmenter`, `subtitles`,
    `stage-hints-*`, `exporter`) continuously.
- Current README still describes post-live processing as manual commands after
  recording completion.
- No frontend runtime exists; runtime observability is stdout logs and JSONL
  audit/state files.

## Candidate Hardening Themes

- **A. Post-live automation loop**: add a supervised Windows post-processing
  launcher that periodically runs segmenter, subtitle enrichment, subtitles, and
  exporter so completed recordings do not require manual commands.
- **B. ASR runtime preflight/fallback policy**: add a CLI/launcher health check
  that verifies faster-whisper can initialize and run on the configured device,
  then reports whether subtitles will be real ASR or placeholder degraded mode.
- **C. Unified health/status command**: summarize platform cookie health,
  live/probe status, orchestrator jobs, recorder/recovery state, subtitle
  fallback reasons, and exporter output into one operator command.
- **D. Windows boot/autostart supervision**: documented or scripted startup of
  the three existing launchers plus any new post-processing loop, including
  crash restart behavior and log locations.
- **E. Long-run audit compaction/maintenance**: prevent unbounded JSONL/state
  growth and make recovery maintenance routine.

## Initial Requirements

- **Scope decision**: first slice is **post-live automation loop + unified
  status command**. ASR device fallback and Windows autostart remain follow-up
  hardening themes.
- The first production-hardening slice must be end-to-end verifiable from
  PowerShell on this machine.
- It must preserve local-first file-backed contracts and existing JSONL audit
  streams.
- It must not require a frontend or external service.
- It must not weaken existing quality gates, cookie checks, or fallback
  contracts.
- It must add focused tests for any new state transition, CLI command, or
  launcher-adjacent behavior.
- A new `arl postprocess` command should run the post-live stages in a
  deterministic idempotent order so operators do not manually chain several
  commands after each recording.
- A new PowerShell launcher should supervise repeated `arl postprocess` runs
  with the same bootstrap conventions as the existing Windows launchers.
- A new `arl status` command should summarize local state/audit files into one
  operator-readable JSON object. It should be local-only by default and must not
  leak stream URLs, cookies, auth headers, or transcript text.

## Provisional Acceptance Criteria

- [ ] A new or enhanced operator workflow can run unattended after agent,
  orchestrator, and recorder are started.
- [ ] `arl postprocess --once` runs semantic stage hints, segmenter, subtitles,
  and exporter in order and remains idempotent on repeated runs.
- [ ] `scripts/windows-postprocess-loop.ps1` bootstraps the venv/dependencies
  using the existing launcher conventions, then repeatedly invokes
  `arl postprocess --once` on a configurable interval.
- [ ] `arl status` returns a JSON summary containing at least platform/session
  counts, recording job status counts, recorder retry/manual-recovery counts,
  asset counts, subtitle fallback reason counts, exporter fallback counts, and
  missing-output indicators.
- [ ] The workflow handles at least one realistic degraded condition without
  crashing the overall pipeline.
- [ ] The operator can inspect whether the system is healthy or degraded without
  manually grepping more than one file.
- [ ] The configured Douyin and Bilibili rooms can still pass one real short E2E
  smoke after the change.
- [ ] Python and Node automated tests pass.

## Out of Scope (Provisional)

- Frontend dashboard.
- Automatic cookie refresh/login automation.
- Full service installation into Windows Task Scheduler or Windows Service,
  unless the selected first slice is explicitly autostart/supervision.
- Replacing file-backed JSONL/state with a database.
- Guaranteed real faster-whisper transcription on hosts without working CUDA or
  an explicit CPU fallback/device policy. This was out of scope for the first
  slice and is now being handled as the follow-up ASR CPU/CUDA fallback slice.

## Open Questions

All current planning blockers resolved.
