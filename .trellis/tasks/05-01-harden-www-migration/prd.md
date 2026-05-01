# PRD: harden /www migration

## Background
Project has been manually migrated to `/www/auto-record-live`. Existing WSL scripts and README are partially aligned, but startup robustness and Windows-side consistency still need hardening.

## Goal
Stabilize post-migration startup experience and documentation consistency across WSL + Windows entry scripts.

## Scope (MVP)
1. Harden WSL startup scripts:
- Validate project path existence with clear error output.
- Keep current fast-path install behavior (`ARL_WSL_INSTALL_MODE=if-missing`) unchanged.
- Ensure logs clearly show resolved paths and runtime mode.

2. Adapt Windows startup script for migration consistency:
- Update default `-ProjectPath` to the new location convention where applicable.
- Improve path checks and startup messages so mixed Windows/WSL workflows are easier to diagnose.
- Keep backward-compatible behavior when user explicitly passes old path.

3. README consistency sweep:
- Ensure quickstart/runbook examples match current script defaults.
- Add short notes for mixed Windows+WSL path mapping expectations.

## Out of Scope
- Refactor recorder/orchestrator business logic.
- Change event contracts or storage layout.
- Full production hardening beyond startup/ops ergonomics.

## Acceptance Criteria
- `scripts/wsl-orchestrator.sh` and `scripts/wsl-recorder-loop.sh` fail fast on invalid path with actionable message.
- `scripts/windows-agent-loop.ps1` works with updated default path guidance and has clear startup diagnostics.
- README runbook examples are internally consistent with script defaults.
- Lint/type-check pass.
