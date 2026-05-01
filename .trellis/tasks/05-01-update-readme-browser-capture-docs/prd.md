# update README browser-capture docs

## Goal

Clarify and restructure README runbook documentation for browser-capture related recording flow so operators can execute the Windows + WSL workflow in the correct order with less ambiguity.

## What I already know

- `README.md` already has an in-progress doc update that restructures command guidance into step-by-step sections.
- Existing edits include: single-run validation flow, long-running loop flow, post-processing order, and recovery commands.
- Task title is specifically about updating README browser-capture docs.

## Assumptions (temporary)

- Scope is documentation-only (README) unless wording requires small script reference fixes.
- The intended runtime split remains: Windows `.venv` for agent probing and WSL `.venv-wsl` for orchestrator/recorder.
- No behavioral code change is required for this task.

## Open Questions

- None blocking at this stage; proceed with README refinement and consistency checks.

## Requirements (evolving)

- Keep README command sections consistent with current scripts/CLI entry points.
- Present execution order explicitly: one-shot validation -> daemonized loops -> post-processing -> recovery.
- Preserve and clarify browser-capture/ffmpeg guidance already added.
- Ensure Windows vs WSL environment boundaries are explicit.

## Acceptance Criteria (evolving)

- [ ] README command flow is step-based and executable as written.
- [ ] Script names and CLI commands in README match repository files.
- [ ] Browser-capture ffmpeg notes remain present and non-contradictory.
- [ ] Lint/type-check remain green after documentation changes.

## Definition of Done (team quality bar)

- Tests added/updated (if behavior changed)
- Lint / typecheck / CI green
- Docs updated for changed behavior
- Rollout/rollback considered if risky

## Out of Scope (explicit)

- Recorder/orchestrator runtime logic changes
- ffmpeg default parameter changes in source code
- New CLI commands beyond documenting existing ones

## Technical Notes

- Primary edited file: `README.md`
- Related scripts referenced by README:
  - `scripts/windows-agent-loop.ps1`
  - `scripts/wsl-orchestrator.sh`
  - `scripts/wsl-recorder-loop.sh`
