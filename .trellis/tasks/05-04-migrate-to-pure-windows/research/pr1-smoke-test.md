# PR1 Smoke Test Checklist (Windows)

> Run this on the actual Windows host (not on Linux dev box). PR1 only adds two
> PowerShell launcher scripts; this validates they bootstrap correctly and run
> the orchestrator + recorder loops end-to-end. PR2 (docs/specs) and PR3
> (deletes) come after PR1 passes.

## Prerequisites (one-time)

- [ ] Python 3.11+ installed and `py -3 --version` works in a fresh PowerShell window
  - Recommended: `winget install Python.Python.3.12`
  - Avoid Microsoft Store Python (broken `ensurepip` is the trigger for `windows-agent-loop.ps1` defenses)
- [ ] Node.js LTS installed and `node --version` + `npm --version` work
  - Recommended: `winget install OpenJS.NodeJS.LTS`
- [ ] ffmpeg installed and `ffmpeg -version` works in a fresh PowerShell window
  - Recommended: `winget install Gyan.FFmpeg`
- [ ] Project cloned to a Windows-local path **outside OneDrive** (e.g., `C:\auto-record-live`)
  - OneDrive sync conflicts with venv file locks
- [ ] `.env` configured (copy from `.env.example`, set `ARL_DOUYIN_ROOM_URL` and `ARL_STREAMER_NAME`)

## Phase A — Bootstrap correctness

Open **PowerShell window 1** in `C:\auto-record-live`. Delete any pre-existing `.venv` to test cold bootstrap:

```powershell
Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue
.\scripts\windows-orchestrator-loop.ps1
```

- [ ] Script prints `[ARL] ensuring pip in venv` (proves try/catch + ensurepip recovery branch fires when `.venv` lacks usable pip)
- [ ] No `NativeCommandError` / `RemoteException` aborts during pip probe
- [ ] `[ARL] orchestrator loop started` banner prints
- [ ] `[ARL] install mode: if-missing` line shown
- [ ] `[ARL] venv: C:\auto-record-live\.venv\Scripts\python.exe` line shown
- [ ] `.venv\.deps-ready` file exists after the install completes
- [ ] orchestrator process is running (CLI prompts not returned; orchestrator polls `data/tmp/windows-agent-events.jsonl`)
- [ ] Ctrl+C cleanly kills the script

Re-run without deleting `.venv`:

```powershell
.\scripts\windows-orchestrator-loop.ps1
```

- [ ] `[ARL] ensuring pip in venv` does NOT print (pip already healthy → recovery skipped)
- [ ] `pip install -e .` does NOT run (sentinel present, install-mode `if-missing`)
- [ ] orchestrator starts within ~1 second

Force reinstall test:

```powershell
$env:ARL_WIN_INSTALL_MODE = "always"
.\scripts\windows-orchestrator-loop.ps1
Remove-Item Env:ARL_WIN_INSTALL_MODE
```

- [ ] `pip install -e .` runs again (forced by `always`)
- [ ] `[ARL] install mode: always` banner

## Phase B — recorder loop semantics

Open **PowerShell window 2**:

```powershell
.\scripts\windows-recorder-loop.ps1 -IntervalSeconds 5
```

- [ ] `[ARL] recorder loop started` banner
- [ ] `[ARL] interval: 5s` line shown
- [ ] After each `arl.cli recorder` invocation, script sleeps 5s then re-runs (the `while ($true)` loop)
- [ ] If recorder exits non-zero, `[ARL][warn] recorder run failed` warning prints, script continues to next iteration
- [ ] Ctrl+C kills cleanly without leaking stuck Python processes

Test interval-via-env:

```powershell
$env:ARL_RECORDER_INTERVAL_SECONDS = "10"
.\scripts\windows-recorder-loop.ps1
```

- [ ] `[ARL] interval: 10s` (env wins when no `-IntervalSeconds` param)

## Phase C — `.env` UTF-8 sourcing

The `.env` file contains `ARL_STREAMER_NAME=柔风亚索（峡谷韩服双千分王者）` (zh-CN with full-width parens). Verify the parser doesn't mangle it:

```powershell
.\scripts\windows-orchestrator-loop.ps1
# In another window or before Ctrl+C:
[Environment]::GetEnvironmentVariable("ARL_STREAMER_NAME", "Process")
```

- [ ] Value is the full Chinese string with full-width parens, not mojibake / `???` / GBK garbage

## Phase D — End-to-end (single live cycle)

Three windows — start in this order:

1. **window 1**: `.\scripts\windows-agent-loop.ps1 -RoomUrl "<your-url>" -StreamerName "<name>"` — Playwright probe loop (existing launcher)
2. **window 2**: `.\scripts\windows-orchestrator-loop.ps1` — orchestrator
3. **window 3**: `.\scripts\windows-recorder-loop.ps1` — recorder

- [ ] Window 1 produces events to `data/tmp/windows-agent-events.jsonl` (when stream is live)
- [ ] Window 2 consumes events and creates a recording job in `data/tmp/orchestrator-state.json`
- [ ] Window 3 picks up the job and runs ffmpeg; mp4 lands under `data/raw/<session>/`
- [ ] No file system permission errors (proves project is on a Windows-local non-OneDrive path)
- [ ] Compare write speed against the old WSL run — should feel noticeably snappier on .venv install + recording write

## Phase E — Recovery + post-processing (existing CLI commands)

Quick sanity check that other CLI subcommands still work in the new venv:

```powershell
.\.venv\Scripts\python.exe -m arl.cli recovery --summary
.\.venv\Scripts\python.exe -m arl.cli stage-hints-auto
.\.venv\Scripts\python.exe -m arl.cli subtitles
.\.venv\Scripts\python.exe -m arl.cli exporter
```

- [ ] Each command exits 0 (or skips gracefully if no input data)

## What to report back

If anything fails, capture:
- The exact `[ARL]` line where the failure occurred
- The full PowerShell error message (don't truncate)
- `Get-Item .venv\Scripts\python.exe | Select-Object FullName, Length` output
- Python version: `.\.venv\Scripts\python.exe --version`

Paste it back into the next session and we resume PR1 fixes (or proceed to PR2 if all green).

## Known accepted limitations (not to flag as bugs)

- `.env` parser doesn't support `${VAR}` interpolation or multi-line continuation — `.env.example` doesn't use these
- `windows-agent-loop.ps1` still warns about `\\wsl$\` paths even though migration target is no UNC — defensive guard, kept on purpose per WSL-reference-scan PR2 punch list
- PR2 hasn't been done yet, so README still talks about WSL — that's expected; smoke-test follows the README mentally with `.venv\Scripts\python.exe` substituted for `.venv-wsl/bin/python`
