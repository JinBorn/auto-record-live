# Windows launcher: ensurepip resilience for fresh venvs

## Goal

Make `scripts/windows-agent-loop.ps1` survive a freshly-created Windows venv
that ships without a working `pip` module, by mirroring the existing WSL
bash-side resilience step. Closes the pre-existing WSL/Windows asymmetry
that was explicitly logged as deferred follow-up in
`.trellis/spec/backend/launcher-conventions.md`.

## What I already know

- Repro just observed in the live error from the user:
  `\\wsl$\Ubuntu-24.04\www\auto-record-live\.venv\Scripts\python.exe: No module named pip`
  → `pip install -e . failed (exit 1)` thrown by line 54 of
  `scripts/windows-agent-loop.ps1` (the `$LASTEXITCODE` check added in the
  previous task).
- The previous task's `$LASTEXITCODE` check did its job: it surfaced the
  failure cleanly instead of letting the script proceed to a sentinel touch
  on a broken install. This task does NOT revert that — it adds the
  resilience the WSL scripts already have, so we never reach that throw on a
  recoverable case (missing pip).
- WSL bash equivalent (canonical pattern): `scripts/wsl-orchestrator.sh:24-26`
  and `scripts/wsl-recorder-loop.sh:25-27`:

  ```bash
  if ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
    "$VENV_PYTHON" -m ensurepip --upgrade
  fi
  ```

- The asymmetry is already documented in
  `.trellis/spec/backend/launcher-conventions.md` ("Out of Scope (for this
  spec)" → "Common Mistake: WSL launcher updated, Windows launcher silently
  drifts"). After this task, the asymmetry is closed and the spec's
  "deferred" note is no longer accurate — needs a small update.
- Why the venv has no pip: most plausibly the venv was created by a
  bootstrap python that does not auto-install pip via the standard library
  (e.g. some distro / system installs strip ensurepip data; some Python
  3.12 launcher behaviors vary). `python -m ensurepip --upgrade` is the
  canonical recovery and is the same call the WSL bash side already uses.

## Requirements

1. In `scripts/windows-agent-loop.ps1`, before the install-mode/sentinel
   block (current lines 47-56), add a probe: if `& $venvPython -m pip
   --version` exits non-zero, run `& $venvPython -m ensurepip --upgrade` and
   abort with a clear error if that ALSO fails. Use the same
   `$LASTEXITCODE` discipline already established in the file.
2. Match the WSL pattern's intent: the probe is silent on success (no extra
   banner line), and emits a `[ARL] ensuring pip in venv` info line only
   when ensurepip actually runs, so logs stay quiet on the happy path.
3. After this change, the original `pip install -e .` line continues
   unchanged (it already runs through the install-mode/sentinel gate).
4. Update `.trellis/spec/backend/launcher-conventions.md`:
   - Remove the "Out of Scope" bullet referring to this asymmetry as
     deferred (or change wording to reflect it's now closed).
   - Add a new row to the "Required Patterns" → cross-runtime parity table
     for the ensurepip probe so future devs see WSL and Windows aligned.

## Acceptance Criteria

- [ ] On a Windows venv missing pip, running
      `windows-agent-loop.ps1 -RoomUrl ... -StreamerName ...` runs `ensurepip
      --upgrade` automatically, then proceeds into the existing install-mode
      / sentinel block, then enters the loop. No throw on the recoverable
      "no pip" case.
- [ ] On a healthy venv (pip already importable), the probe is a no-op:
      `pip --version` exits 0, ensurepip is NOT invoked, no extra log line is
      emitted, behavior is identical to the previous task's output.
- [ ] If `ensurepip` itself fails (catastrophic case — network down, missing
      ensurepip data), the script throws a clear, distinct error mentioning
      `ensurepip` (not the generic `pip install -e .` message).
- [ ] `launcher-conventions.md` no longer claims this asymmetry is deferred;
      the parity table includes a row for the ensurepip probe.

## Definition of Done

- Single PS1 file diff plus the spec update.
- README does NOT need a new section — this is invisible resilience.
- No commits in main session — batched commit at Phase 3.4.

## Technical Approach

Insert between current line 45 (the `npm not found` throw) and current line 47
(the `$installMode = ...` line):

```powershell
# Mirrors scripts/wsl-orchestrator.sh:24-26: a venv created without working
# pip (some Windows Python distributions ship ensurepip data that fails to
# bootstrap on first venv creation) is recovered via `ensurepip --upgrade`
# before any pip-driven install runs.
& $venvPython -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Host "[ARL] ensuring pip in venv"
  & $venvPython -m ensurepip --upgrade
  if ($LASTEXITCODE -ne 0) { throw "python -m ensurepip --upgrade failed (exit $LASTEXITCODE)" }
}
```

Notes:
- `*> $null` swallows both stdout AND stderr from the probe (PowerShell 5.1+
  redirection; PowerShell 7 also accepts it). The WSL bash uses
  `>/dev/null 2>&1`. Equivalent intent.
- The probe deliberately uses `& $venvPython -m pip --version` (same as WSL)
  rather than `Test-Path` on a `pip.exe`, because a partial install can leave
  a `pip.exe` shim that itself crashes on `--version`.

Spec update in `launcher-conventions.md`:
- Remove or reword the "Asymmetric mechanism gotcha" line in the prd that
  was logged as deferred.
- Add to the "Required Patterns" parity table:

  | Pip probe | `[ "$VENV_PYTHON" -m pip --version` test + `ensurepip --upgrade` fallback | `& $venvPython -m pip --version` test + `& $venvPython -m ensurepip --upgrade` fallback |

## Out of Scope

- Anything other than the ensurepip resilience.
- npm-side resilience (still gated by `Test-Path node_modules` — a separate
  question and explicitly out of scope of `launcher-conventions.md` too).
- Refactoring the launcher into a shared template/codegen.

## Technical Notes

- File touched: `scripts/windows-agent-loop.ps1` (lines 45-47 area).
- Spec file touched: `.trellis/spec/backend/launcher-conventions.md`.
- WSL canonical reference: `scripts/wsl-orchestrator.sh:24-26`,
  `scripts/wsl-recorder-loop.sh:25-27`.
- Live error reproducer (from user, this turn): venv at
  `\\wsl$\Ubuntu-24.04\www\auto-record-live\.venv\Scripts\python.exe`
  reports `No module named pip`.
