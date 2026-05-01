# optimize WSL startup for orchestrator/recorder

## Goal

Reduce startup latency for WSL orchestrator/recorder while keeping a workable Windows + WSL split workflow.

## What I already know

- Project currently runs from `/mnt/d/code/auto-record-live` in some workflows.
- WSL access to `/mnt/<drive>` is usually slower for small-file-heavy workloads.
- Existing scripts recreate/validate venv and run `pip install -e .` on each startup path, which adds avoidable cold-start overhead.

## Assumptions (temporary)

- Main bottleneck is mixed: filesystem mount latency + repeated dependency bootstrap.
- We should keep Windows agent runnable from Windows terminal, but move heavy loops to WSL-native filesystem.

## Open Questions

- Preferred source-of-truth layout: WSL-native code mirror vs keeping code on D: and only caching environments in WSL.

## Requirements (evolving)

- Provide an actionable run mode with fast startup in WSL.
- Preserve Windows-agent usability on Windows.
- Avoid mandatory full workflow rewrite.
- Update scripts/docs to make fast path default and explicit.

## Acceptance Criteria (evolving)

- [ ] WSL startup path avoids repeated `pip install -e .` unless needed.
- [ ] README documents recommended fast-path directory strategy.
- [ ] Windows + WSL responsibility boundary remains explicit and runnable.

## Definition of Done (team quality bar)

- Tests added/updated (unit/integration where appropriate)
- Lint / typecheck / CI green
- Docs/notes updated if behavior changes
- Rollout/rollback considered if risky

## Out of Scope (explicit)

- Refactoring core recorder/orchestrator business logic
- Building a full bidirectional sync daemon

## Technical Notes

- Candidate files:
  - `scripts/wsl-orchestrator.sh`
  - `scripts/wsl-recorder-loop.sh`
  - `scripts/windows-agent-loop.ps1`
  - `README.md`
