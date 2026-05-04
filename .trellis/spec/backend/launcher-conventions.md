# Launcher Conventions

> Conventions for the three PowerShell launcher scripts under `scripts/`
> that bootstrap the agent / orchestrator / recorder polling loops on
> Windows. Not for `src/arl/...` business logic.

> **Migration note (2026-05-04)**: this spec was originally structured around
> WSL bash + Windows PowerShell parity. The runtime migrated to pure Windows;
> the bash side is gone. See
> `.trellis/tasks/archive/2026-05/05-04-migrate-to-pure-windows/prd.md` for
> the migration ADR (path reflects post-archive location).

---

## Overview

The repo has three PowerShell launcher scripts under `scripts/`:

- `scripts/windows-agent-loop.ps1` — Windows agent polling loop (Playwright Douyin probe)
- `scripts/windows-orchestrator-loop.ps1` — orchestrator wrapper (delegates polling to the daemon's internal loop)
- `scripts/windows-recorder-loop.ps1` — recorder polling loop (drives single-pass `arl.cli recorder` invocations + supervises restart on crash)

They share a small surface area (env-var-driven install mode, sentinel-gated
dependency bootstrap, `[ARL] ...` log lines) that must stay aligned across
all three. This document captures the conventions and the pitfalls already hit.

---

## Required Patterns

### Install-mode bootstrap parity across launchers

When a launcher needs to decide whether to run `pip install -e .`, it MUST
follow the established shape:

| Concern | Convention |
|---|---|
| Pip availability probe | `try { & $venvPython -m pip --version *> $null; if ($LASTEXITCODE -eq 0) { $pipOk = $true } } catch { $pipOk = $false }` then `ensurepip --upgrade` if `-not $pipOk`. The try/catch is mandatory; see "Common Mistake" below. |
| Env var | `ARL_WIN_INSTALL_MODE` |
| Default value | `if-missing` |
| `always` semantics | force reinstall every run |
| Sentinel file | `<venv-dir>\.deps-ready` |
| Trigger | `mode==always` OR sentinel missing |
| Touch on success | `New-Item -ItemType File -Path $depsReady -Force \| Out-Null` |
| Banner line | `Write-Host "[ARL] install mode: $installMode"` |

Reference implementations:

- `scripts/windows-agent-loop.ps1:47-68,70-79,90`
- `scripts/windows-orchestrator-loop.ps1:38-69`
- `scripts/windows-recorder-loop.ps1:50-75`

When you change one launcher's bootstrap behavior (e.g. add a new
`install-mode` value, change the sentinel filename, rename an env var), you
MUST mirror the change in the other two PowerShell launchers in the same
task. Asymmetry between launchers silently drifts and only surfaces weeks
later when an operator switches to the "other" window.

### `.deps-ready` sentinel file lifecycle

- Sentinel content is irrelevant — it's a marker file, not config.
- Sentinel is touched only after a successful `pip install -e .` (exit 0).
  A failed install MUST NOT leave a sentinel behind, otherwise the next run
  thinks the venv is ready and skips the retry.
- Sentinel is never proactively invalidated by the launcher itself. If
  `pyproject.toml` changes, operators set `ARL_WIN_INSTALL_MODE=always` for
  one run, or delete the sentinel manually. (Hash-based invalidation is a
  deliberate non-goal — see the deferred Option B in
  `.trellis/tasks/archive/2026-05/05-01-optimize-wsl-startup/prd.md`.)

### Logging shape

All launcher script output MUST conform to
`.trellis/spec/backend/logging-guidelines.md`:

- One line per event.
- Prefix `[ARL]` (the launcher acts as the `ARL` component, not as a
  per-stage component name).
- `key: value` for variable fields where useful: `[ARL] install mode: always`.
- No multi-line dumps. No secret URLs / cookies / tokens (see "What NOT to
  Log" in logging-guidelines).

### `.venv` directory never goes in git

`.venv/` is listed in `.gitignore`. Any new venv variant added in the
future MUST be added there in the same commit that introduces it.

Why: editable installs rewrite `__editable__.*.pth`, `RECORD`, `direct_url.json`,
and shebangs in `Scripts/` whenever the project path or `pyproject.toml` changes.
A tracked venv → every checkout produces a "dirty" working tree → defeats the
sentinel by invalidating the venv binaries on disk. (This was the original
trigger for task `archive/2026-05/05-01-optimize-wsl-startup`.)

If a legacy checkout already has a venv tracked, the recovery is one-time:
`git rm -r --cached <venv-dir>` (files preserved on disk, sentinel intact).

---

## Forbidden Patterns

- Reading `$env:VAR` in PowerShell and assuming `$ErrorActionPreference =
  "Stop"` will abort on a non-zero exit from a native executable. It will
  not. See "Common Mistake" below.
- Adding launcher logic to only one runtime "for now, we'll mirror later."
  Mirror in the same task.
- Silent fallback to default when an unrecognized `ARL_*_INSTALL_MODE` value
  is passed. The current scripts treat anything-other-than-`always` as
  `if-missing` (lenient by design); if that ever needs tightening, tighten
  both runtimes together.
- Inventing a new sentinel filename (e.g. `.installed`, `.bootstrap.done`).
  Use `.deps-ready`.

---

## Common Mistakes

### Common Mistake: PowerShell `$ErrorActionPreference = "Stop"` does not abort on native-exe failures

**Symptom**: A `pip install -e .` exits with a non-zero code (e.g. compile
error in a wheel build), but the PowerShell launcher script keeps going,
touches the `.deps-ready` sentinel, and emits the success banner. The next
run sees the sentinel and skips the retry, so the broken install is sticky.

**Cause**: `$ErrorActionPreference = "Stop"` and `Set-StrictMode` only abort
on PowerShell-side errors (cmdlet errors, undefined variables, etc.). They
do NOT translate non-zero exit codes from native executables (`pip`, `npm`,
`git`, `ffmpeg`, …) into terminating errors.

**Fix**: Immediately after every native-exe invocation that gates further
actions, check `$LASTEXITCODE`:

```powershell
& $venvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw "pip install -e . failed (exit $LASTEXITCODE)" }
New-Item -ItemType File -Path $depsReady -Force | Out-Null
```

**Prevention**: When porting a `set -euo pipefail` bash script to PowerShell,
treat every `&` (call operator) on a native exe as needing an explicit
`$LASTEXITCODE` check before any side effect that depends on its success
(sentinel writes, downstream installs, env var exports).

### Common Mistake: PowerShell `$ErrorActionPreference = "Stop"` DOES promote native-exe stderr into a terminating error

**Symptom**: A native-exe probe like `& $venvPython -m pip --version *> $null`
aborts the entire script with `NativeCommandError` / `RemoteException` even
though the redirect is supposed to swallow stderr. The `if ($LASTEXITCODE
-ne 0) { ... }` recovery branch immediately after never runs because the
script already terminated.

**Cause**: This is the *opposite* gotcha to the previous one and easy to
conflate. PowerShell intercepts whatever a native command writes to stderr
and surfaces it as a `NativeCommandError`. Under `$ErrorActionPreference =
"Stop"` (and worse on PowerShell 7.2+ with `$PSNativeCommandUseErrorActionPreference
= $true`) that error is promoted into a terminating exception **before** the
`*> $null` redirect takes effect. Stop does not propagate native exit codes
(see prior mistake) but it DOES propagate native stderr.

**Fix**: Wrap any probe of a possibly-broken native exe in `try`/`catch` so
the terminating error is caught at the right boundary. Inspect
`$LASTEXITCODE` inside the `try` block; treat any catch as the failure case.

```powershell
$pipOk = $false
try {
  & $venvPython -m pip --version *> $null
  if ($LASTEXITCODE -eq 0) { $pipOk = $true }
} catch {
  $pipOk = $false
}
if (-not $pipOk) { ... recovery ... }
```

**Prevention**: For any native-exe call where you *expect* failure to be
recoverable (probes, capability detection, version sniffs, dry-runs), wrap
in `try`/`catch`. The unwrapped `& cmd *> $null; if ($LASTEXITCODE) {...}`
form is only safe when the cmd is guaranteed not to write to stderr on
failure — which is rare. Bash's `>/dev/null 2>&1` does not have this
asymmetry; do not assume porting it to `*> $null` preserves semantics.

### Common Mistake: One PowerShell launcher updated, peer scripts silently drift

**Symptom**: Operator runs the three PowerShell windows and finds the
install behavior subtly different across them — full reinstall in one
window every loop, or stale dependencies in another, or unexpected env-var
name in a third.

**Cause**: A previous task changed one launcher (e.g. added the
`if-missing` shortcut to `windows-agent-loop.ps1`) without mirroring the
change to its peers, because the three scripts are not generated from a
shared template.

**Fix**: Update all three launchers in the same task. Add a comment on
each cross-referencing one peer (e.g.
`# Mirrors ARL_WIN_INSTALL_MODE handling in scripts/windows-orchestrator-loop.ps1`).

**Prevention**: When reviewing launcher diffs, search for every
`scripts/windows-*-loop.ps1` file touched and verify the env-var name shape,
default value, sentinel name, and trigger condition match across all three.
The "Required Patterns" table above is the canonical alignment reference.

---

## Out of Scope (for this spec)

- Replacing venv-based bootstrap with `uv` / `pdm` / `poetry`.
- A unified `windows-fast.ps1` master entrypoint that wraps all three loops.
- Lockfile-hash-based sentinel invalidation.
- npm-side install-mode gating (currently `npm install` only runs when
  `node_modules/` is absent — see `windows-agent-loop.ps1:57-59`).
