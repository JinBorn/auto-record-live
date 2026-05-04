# optimize WSL startup for orchestrator/recorder

## Goal

Eliminate spurious "dirty repo / re-install" churn on launcher startup, and
bring the Windows agent's bootstrap behavior up to parity with the WSL
scripts. Keep the existing Windows + WSL split workflow runnable; touch only
the launcher scripts and the supporting environment hygiene around them — not
recorder/orchestrator business logic.

## What I already know (auto-context, 2026-05-04)

- The repo is already on a WSL-native filesystem: `/www/auto-record-live` on
  `/dev/sdd` ext4. The earlier `/mnt/d/code/auto-record-live` location still
  physically exists but is no longer the source of truth (editable install
  resolves to `/www/...`).
- `scripts/wsl-orchestrator.sh` and `scripts/wsl-recorder-loop.sh` already
  implement `ARL_WSL_INSTALL_MODE=if-missing` (default) plus a
  `.venv-wsl/.deps-ready` sentinel. First run installs, subsequent runs skip.
- `scripts/windows-agent-loop.ps1` does NOT have `if-missing` parity:
  - line 47 unconditionally runs `& $venvPython -m pip install -e .` every
    loop start
  - `npm install` is only skipped when `node_modules/` is absent
- `.gitignore` ignores `.venv/` but NOT `.venv-wsl/`. As a result **2,814
  files under `.venv-wsl/` are currently tracked in git**. Any pull/checkout
  rewrites venv binaries on disk, which then trigger editable rebuilds and
  show up as "dirty files" on every session start. This is the visible
  startup churn that motivated the task.
- The 5 currently-modified files in `git status` (`bin/arl`,
  `__editable__*.pth`, `METADATA`, `RECORD`, `direct_url.json`) are exactly
  the venv-side artifacts rewritten by `pip install -e .` after the project
  moved to `/www/`.
- README §WSL2 already documents `/www/auto-record-live` as the recommended
  layout and `ARL_WSL_INSTALL_MODE=if-missing`. The guidance exists; what's
  missing is the hygiene around it.

## Requirements

1. Stop tracking `.venv-wsl/` in git.
   - Add `.venv-wsl/` to `.gitignore` (next to existing `.venv/` entry).
   - Run `git rm -r --cached .venv-wsl` so the entire ≈2.8k-file directory is
     removed from the index without touching files on disk.
   - The existing `.venv-wsl/.deps-ready` sentinel must remain in place on
     disk (no reinstall).
2. Add `if-missing` parity to `scripts/windows-agent-loop.ps1`.
   - Skip `pip install -e .` when `.venv\.deps-ready` exists.
   - Honor an env override `ARL_WIN_INSTALL_MODE` mirroring the WSL script
     (`if-missing` default; `always` forces reinstall).
   - Touch `.venv\.deps-ready` after a successful install.
   - `npm install` skip behavior is unchanged for this task (out of scope —
     see below).
3. README updated for behavior changes.
   - Document the one-time clean step for existing checkouts:
     `git rm -r --cached .venv-wsl` after pulling these changes.
   - Document `ARL_WIN_INSTALL_MODE` next to the existing
     `ARL_WSL_INSTALL_MODE` paragraph.
4. The existing public surface keeps working with current invocations and env
   vars. No change to the orchestrator/recorder/windows-agent CLI semantics.
5. Cleanup that touches the working tree (the `git rm --cached` step) lands as
   its own commit, separate from script edits and README edits.

## Acceptance Criteria

- [ ] `.venv-wsl/` appears in `.gitignore` and `git ls-files .venv-wsl/ | wc -l`
      reports `0` after the cleanup commit.
- [ ] After cleanup, `git status` is clean immediately following a fresh
      `bash scripts/wsl-orchestrator.sh /www/auto-record-live` run on this
      machine.
- [ ] Re-running `wsl-orchestrator.sh` with `.venv-wsl/.deps-ready` present
      completes without invoking `pip install -e .` (verified by inspection /
      no network activity).
- [ ] `scripts/windows-agent-loop.ps1` skips `pip install -e .` when
      `.venv\.deps-ready` is present, and respects
      `ARL_WIN_INSTALL_MODE=always` to force reinstall.
- [ ] After a successful first-time `pip install -e .` on Windows,
      `.venv\.deps-ready` exists.
- [ ] README explicitly documents the one-time `git rm -r --cached .venv-wsl`
      cleanup and the new `ARL_WIN_INSTALL_MODE` knob.

## Definition of Done

- shellcheck clean for the modified bash scripts (no new warnings).
- PowerShell static checks (`Invoke-ScriptAnalyzer` if it's part of the
  project's quality gate; otherwise manual run-through).
- README renders without broken anchors.
- Rollout note in README: legacy checkouts need
  `git rm -r --cached .venv-wsl` once.

## Technical Approach

Three commits, in this order:

1. **`chore(repo): untrack .venv-wsl/`** — `.gitignore` adds `.venv-wsl/`;
   `git rm -r --cached .venv-wsl`. No script edits in this commit, so the
   diff is huge but mechanical and easy to review.
2. **`feat(scripts): if-missing parity for windows-agent-loop`** — modify
   `scripts/windows-agent-loop.ps1` to read `ARL_WIN_INSTALL_MODE`, check
   `.venv\.deps-ready`, conditionally run `pip install -e .`, and touch the
   sentinel on success.
3. **`docs(readme): document venv hygiene + windows install mode`** — README
   §WSL2 / §Windows updates: cleanup recipe, `ARL_WIN_INSTALL_MODE` knob.

The bash scripts (`wsl-orchestrator.sh`, `wsl-recorder-loop.sh`) are NOT
modified in this task — their `if-missing` behavior is already correct; we
just need to keep the ground under their feet stable by removing `.venv-wsl/`
from version control.

## Decision (ADR-lite)

**Context**: The user reported "WSL startup feels slow / churns the working
tree." Auto-context revealed the slowness on WSL is largely already solved by
the `.deps-ready` sentinel; the visible "churn" is `.venv-wsl/` being tracked
in git, and the Windows-side bootstrap unconditionally reinstalls.

**Decision**: Ship the smallest hygiene-focused change set (Option A). Defer
hash-based sentinels (Option B) and a unified `wsl-fast.sh` (Option C) to
follow-on tasks, because:
- Hash-based sentinels add scope a user rarely hits in practice (deps don't
  change every session).
- A unified fast-path script duplicates surface area without solving a
  measured bottleneck once Option A lands.

**Consequences**:
- Existing checkouts need a one-time `git rm -r --cached .venv-wsl` after
  pulling — documented in README.
- If `pyproject.toml` changes, developers must `rm .venv-wsl/.deps-ready`
  (or the Windows equivalent) by hand, or use `ARL_WSL_INSTALL_MODE=always` /
  `ARL_WIN_INSTALL_MODE=always`. This is the same status quo the WSL scripts
  already have; we're just propagating it to Windows.

## Out of Scope

- Refactoring core recorder/orchestrator business logic.
- Hash-based / lockfile-aware sentinels (deferred — Option B).
- A unified fast-path entrypoint script (deferred — Option C).
- Building a bidirectional sync daemon between Windows and WSL.
- Replacing venv-based bootstrap with `uv` / `pdm` / `poetry`.
- Caching `node_modules` smarter than the existing "skip if dir exists" check.

## Technical Notes

- Candidate files (final list for Option A):
  - `.gitignore`
  - `scripts/windows-agent-loop.ps1`
  - `README.md`
  - (no edits) `scripts/wsl-orchestrator.sh`, `scripts/wsl-recorder-loop.sh`
- Sentinel pattern reference: `.venv-wsl/.deps-ready` touch file in
  `scripts/wsl-orchestrator.sh:28-31` and `scripts/wsl-recorder-loop.sh:29-32`.
- Editable install verification (post-migration to `/www/`):
  - `.venv-wsl/lib/python3.12/site-packages/auto_record_live-0.1.0.dist-info/direct_url.json`
    → `file:///www/auto-record-live` ✓
  - `.venv-wsl/bin/arl` shebang → `/www/auto-record-live/.venv-wsl/bin/python` ✓
- README WSL UNC reference: `\\wsl$\Ubuntu-24.04\www\auto-record-live`.
- Tracked-file count to clear: `git ls-files .venv-wsl/ | wc -l` → 2814.
</content>
</invoke>