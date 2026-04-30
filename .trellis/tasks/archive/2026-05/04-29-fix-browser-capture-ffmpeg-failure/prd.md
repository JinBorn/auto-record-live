# PRD: Fix Browser Capture FFmpeg Failure

## Background
Current browser-capture test path can reach `session_started` and `recording_job_created`, but recorder fails with `Error opening input files: Input/output error` and falls back to placeholder assets.

## Goal
Make browser-capture recording runnable in this Linux/WSL-like test environment for short real-run tests, avoiding immediate ffmpeg input-open failures.

## Scope
- Diagnose browser-capture ffmpeg input selection and command construction.
- Implement minimal robust fallback/input strategy so recorder can consume browser-capture jobs without immediate input-open failure in common local environments.
- Keep runtime bounded for testing (short timeout).

## Non-Goals
- Full production-grade screen/audio capture matrix across all OS/display servers.
- Refactoring unrelated orchestrator/session logic.

## Acceptance Criteria
1. Running short flow `windows-agent --once -> orchestrator --once -> recorder` in browser-capture mode processes at least one queued job without `missing_browser_capture_input` and without immediate input-open failure caused by empty/invalid default input selection.
2. Logs clearly indicate which browser-capture format/input was selected.
3. Existing tests/lint/type-check remain passing for affected modules.

## Notes
- `.env` already configured by user.
- Prefer minimal, backward-compatible config behavior.
