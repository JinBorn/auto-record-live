# Cookie health gate at launcher startup

## Goal

Add a cookie-health gate to the Windows agent launcher so that an expired cookie
becomes immediately visible to the operator at boot — without silently letting
the agent run for hours producing degraded-quality recordings or `cookie_expired`
audit rows that nobody is watching.

## User Value

Operator runs `.\scripts\windows-agent-loop.ps1 -RoomUrl ... -StreamerName ...`
after a host reboot or PR pull. Today: nothing tells them the cookie expired in
the meantime; agent silently degrades, audit log fills up, recordings drop
to anonymous quality (B 站 720p) or get blocked outright (douyin `_uhd` gate).

After this change: launcher runs `arl cookie-health` once **after venv bootstrap
and before the polling loop**; an expired cookie produces a red `Write-Warning`
+ a `hint=...` line pointing at the env var to refresh; by default the launcher
continues (warning mode) but `$env:ARL_COOKIE_HEALTH_GATE = "fatal"` makes it
abort on expired.

## Confirmed Facts (from code inspection)

- `arl cookie-health` already prints per-row status + summary + `hint=Refresh ...` line and exits non-zero only on expired (`src/arl/windows_agent/cookie_health.py:54`, `src/arl/cli.py:299-318`).
- `windows-agent-loop.ps1` currently runs `pip install -e .` bootstrap then drops directly into `while ($true) { arl windows-agent --once; Start-Sleep ... }` (`scripts/windows-agent-loop.ps1:100-107`). No cookie check.
- `windows-orchestrator-loop.ps1` and `windows-recorder-loop.ps1` don't directly consume cookies — they read JSONL events / state. Cookie health here is redundant; the audit path in recorder (403 → `cookie_expired_for_<platform>`) already covers their failure mode.
- `ErrorActionPreference = "Stop"` is already set at the top of the agent loop; we can `throw` to abort on fatal mode, and use try/catch + `Write-Warning` to keep going on warning mode.

## Decisions (Q1 + Q2, 2026-05-14)

- ~~Q1 scope~~ — **E1 only** (launcher startup gate). E2 multi-cookie pool, E3 .env.local persistence, E4 --watch loop all moved out.
- ~~Q2-a severity~~ — **default warning, `ARL_COOKIE_HEALTH_GATE=fatal` to upgrade**.
- ~~Q2-b launcher coverage~~ — **windows-agent-loop.ps1 only**.
- ~~Q2-c sharing~~ — **inline (~15 lines), no separate shared .ps1**.

## Requirements

- **R1**: After successful venv + dependency bootstrap, before the `while ($true)` polling loop, `windows-agent-loop.ps1` invokes `& $venvPython -m arl.cli cookie-health` once.
- **R2**: Based on exit code and `$env:ARL_COOKIE_HEALTH_GATE`:
  - exit code 0 → continue silently (cookie-health's own output is enough).
  - exit code != 0 AND gate is unset/empty/`warning` → `Write-Warning "[ARL] cookie expired — launcher continuing; recordings may be degraded"`, then continue.
  - exit code != 0 AND gate == `fatal` → `throw "[ARL] cookie expired — refusing to start (set ARL_COOKIE_HEALTH_GATE=warning to override)"`.
  - exit code != 0 AND gate == `skip` → not honored here; `skip` means "don't run the gate at all" (see R3).
- **R3**: When `$env:ARL_COOKIE_HEALTH_GATE -eq "skip"`, the entire gate block is bypassed and the launcher proceeds directly to the polling loop. Operators with no cookie configured (anonymous-only deployments) use this to silence the gate.
- **R4**: README `Windows 环境准备` section and "Cookie 配置与失效审计" section both mention the gate and the env var.
- **R5**: No test coverage required (PowerShell-only change; behavior is observable via manual smoke).

## Acceptance Criteria

- [ ] Launching agent loop with a fresh-cookie deployment → no extra output, polling loop starts normally.
- [ ] Launching with an expired douyin cookie + no gate env → one red `Write-Warning` line printed before the first probe cycle, polling continues.
- [ ] Same scenario + `$env:ARL_COOKIE_HEALTH_GATE = "fatal"` → launcher aborts before the first probe, exit code non-zero.
- [ ] `$env:ARL_COOKIE_HEALTH_GATE = "skip"` → no `arl cookie-health` invocation at all (verified by absence of its stdout); polling starts directly.
- [ ] README gains a short subsection under "Cookie 配置与失效审计" documenting the three gate modes.

## Out of Scope

- Adding the gate to orchestrator or recorder launchers (their cookie failure mode is already covered by the recorder-path `cookie_expired_for_<platform>` audit).
- Multi-cookie pool / rotation (deferred — no real demand yet).
- `.env.local` persistence (separate future task; nice-to-have).
- `arl cookie-health --watch` (long-running monitor belongs in external tooling, not the CLI).

## Open Questions

All blockers resolved.

## Notes

- Lightweight task: PRD + short `implement.md`, no `design.md`.
- Single PR is fine; the change touches one .ps1 file + README.
