# Launcher Conventions

> Conventions for the small bash and PowerShell launcher scripts under `scripts/`
> that bootstrap the WSL orchestrator/recorder loops and the Windows agent
> loop. Not for `src/arl/...` business logic.

---

## Overview

The repo currently has three launcher scripts:

- `scripts/wsl-orchestrator.sh` — WSL orchestrator one-shot wrapper
- `scripts/wsl-recorder-loop.sh` — WSL recorder polling loop
- `scripts/windows-agent-loop.ps1` — Windows agent polling loop

They share a small surface area (env-var-driven install mode, sentinel-gated
dependency bootstrap, `[ARL] ...` log lines) that must stay in lockstep across
runtimes. This document captures the conventions and the pitfalls already hit.

---

## Required Patterns

### Cross-runtime parity for install-mode bootstrap

When a launcher needs to decide whether to run `pip install -e .`, it MUST
follow the established symmetric shape:

| Concern | WSL bash convention | Windows PowerShell convention |
|---|---|---|
| Pip availability probe | `if ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then "$VENV_PYTHON" -m ensurepip --upgrade; fi` | `try { & $venvPython -m pip --version *> $null; if ($LASTEXITCODE -eq 0) { $pipOk = $true } } catch { $pipOk = $false }` then `ensurepip --upgrade` if `-not $pipOk`. The try/catch is mandatory; see "Common Mistake" below. |
| Env var | `ARL_WSL_INSTALL_MODE` | `ARL_WIN_INSTALL_MODE` |
| Default value | `if-missing` | `if-missing` |
| `always` semantics | force reinstall every run | force reinstall every run |
| Sentinel file | `<venv-dir>/.deps-ready` | `<venv-dir>\.deps-ready` |
| Trigger | `mode==always` OR sentinel missing | `mode==always` OR sentinel missing |
| Touch on success | `touch "$VENV_DIR/.deps-ready"` | `New-Item -ItemType File -Path $depsReady -Force \| Out-Null` |
| Banner line | `echo "[ARL] install mode: $INSTALL_MODE"` | `Write-Host "[ARL] install mode: $installMode"` |

Reference implementations:

- `scripts/wsl-orchestrator.sh:7-8,24-26,28-31`
- `scripts/wsl-recorder-loop.sh:8-9,25-27,29-32`
- `scripts/windows-agent-loop.ps1:47-68,70-79,90`

When you change one side (e.g. add a new `install-mode` value, change the
sentinel filename, rename an env var), you MUST change all three scripts in
the same task. Asymmetry between launchers silently drifts and only surfaces
weeks later when someone tries the "other" runtime.

### `.deps-ready` sentinel file lifecycle

- Sentinel content is irrelevant — it's a marker file, not config.
- Sentinel is touched only after a successful `pip install -e .` (exit 0).
  A failed install MUST NOT leave a sentinel behind, otherwise the next run
  thinks the venv is ready and skips the retry.
- Sentinel is never proactively invalidated by the launcher itself. If
  `pyproject.toml` changes, operators set `ARL_*_INSTALL_MODE=always` for one
  run, or delete the sentinel manually. (Hash-based invalidation is a
  deliberate non-goal — see the deferred Option B in
  `.trellis/tasks/05-01-optimize-wsl-startup/prd.md`.)

### Logging shape

All launcher script output MUST conform to
`.trellis/spec/backend/logging-guidelines.md`:

- One line per event.
- Prefix `[ARL]` (the launcher acts as the `ARL` component, not as a
  per-stage component name).
- `key: value` for variable fields where useful: `[ARL] install mode: always`.
- No multi-line dumps. No secret URLs / cookies / tokens (see "What NOT to
  Log" in logging-guidelines).

### `.venv*` directories never go in git

Both `.venv/` and `.venv-wsl/` are listed in `.gitignore`. Any new venv
variant added in the future MUST be added there in the same commit that
introduces it.

Why: editable installs rewrite `__editable__.*.pth`, `RECORD`, `direct_url.json`,
and shebangs in `bin/` whenever the project path or `pyproject.toml` changes.
A tracked venv → every checkout produces a "dirty" working tree → defeats the
sentinel by invalidating the venv binaries on disk. (This was the original
trigger for task `05-01-optimize-wsl-startup`.)

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

### Common Mistake: WSL launcher updated, Windows launcher silently drifts

**Symptom**: Operator switches from WSL terminal to Windows terminal (or
vice versa) and finds the install behavior subtly different — full reinstall
every loop, or stale dependencies, or unexpected env-var name.

**Cause**: A previous task changed one side's launcher (e.g. added the
`if-missing` shortcut to the WSL scripts) without mirroring the change to
the other side, because the two scripts are not generated from a shared
template.

**Fix**: Update all three launchers in the same task. Add a comment on the
PowerShell side cross-referencing the bash source-of-truth (e.g.
`# Mirrors ARL_WSL_INSTALL_MODE in scripts/wsl-orchestrator.sh`).

**Prevention**: When reviewing launcher diffs, search for every
`scripts/{wsl,windows}-*` file touched and verify the env-var name shape,
default value, sentinel name, and trigger condition match across runtimes.
The "Required Patterns" table above is the canonical alignment reference.

---

## Out of Scope (for this spec)

- Replacing venv-based bootstrap with `uv` / `pdm` / `poetry`.
- A unified `wsl-fast.sh` / `windows-fast.ps1` entrypoint.
- Lockfile-hash-based sentinel invalidation.
- npm-side install-mode gating (currently `npm install` only runs when
  `node_modules/` is absent — see `windows-agent-loop.ps1:57-59`).
