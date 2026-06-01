# Implement: Cookie health gate at launcher startup

Single PR. ~20 lines of PowerShell + a README subsection.

## PR1 — Launcher cookie-health gate

### Files

- `scripts/windows-agent-loop.ps1` — insert the gate block between the `node_modules` check (line 84) and the `Write-Host "[ARL] windows-agent loop started"` block (line 89):

  ```powershell
  $cookieGate = $env:ARL_COOKIE_HEALTH_GATE
  if ($cookieGate -ne "skip") {
    Write-Host "[ARL] running cookie-health gate (mode=$($cookieGate | ForEach-Object { if ([string]::IsNullOrEmpty($_)) { 'warning' } else { $_ } }))"
    & $venvPython -m arl.cli cookie-health
    $cookieExit = $LASTEXITCODE
    if ($cookieExit -ne 0) {
      if ($cookieGate -eq "fatal") {
        throw "[ARL] cookie expired (exit=$cookieExit). Refresh ARL_DOUYIN_COOKIE / ARL_BILIBILI_SESSDATA, or set ARL_COOKIE_HEALTH_GATE=warning to continue anyway."
      }
      Write-Warning "[ARL] cookie expired (exit=$cookieExit) — launcher continuing; recordings may be degraded. Refresh cookie env vars or set ARL_COOKIE_HEALTH_GATE=fatal to abort on expired."
    }
  }
  ```

  PowerShell note: the inline `ForEach-Object` is needed because PowerShell 5.1 has no null-coalescing operator. The simpler `if/else` form is acceptable too — pick whichever is clearer in final code.

- `README.md` — under "Cookie 配置与失效审计" section, add a subsection:

  ````markdown
  ### Launcher 启动门

  `windows-agent-loop.ps1` 启动时（venv 准备好之后、轮询循环开始之前）会跑一次
  `arl cookie-health`：

  - 默认（无 `ARL_COOKIE_HEALTH_GATE` 或设为 `warning`）：cookie 过期时打印
    `Write-Warning` 红字提醒后继续进入轮询。
  - `$env:ARL_COOKIE_HEALTH_GATE = "fatal"`：cookie 过期时 launcher 直接 throw 退出，
    适合"必须 1080P，否则别开机"的部署。
  - `$env:ARL_COOKIE_HEALTH_GATE = "skip"`：完全跳过检查，适合 anonymous-only
    部署（既没配抖音 cookie 也没配 B 站 SESSDATA）。

  orchestrator/recorder launcher 不重复跑这个门 —— recorder 路径的
  `cookie_expired_for_<platform>` 审计会在它们的循环内自然冒出。
  ````

### Validation

```powershell
# 1. Fresh-cookie path (env vars set with valid values):
.\scripts\windows-agent-loop.ps1 -RoomUrl "https://live.douyin.com/X" -StreamerName "X"
# Expected: "[ARL] running cookie-health gate (mode=warning)"
#           cookie-health printout with summary=ok
#           "[ARL] windows-agent loop started"

# 2. Expired-cookie warning path:
$env:ARL_DOUYIN_COOKIE = "stale=value"  # forced bad
.\scripts\windows-agent-loop.ps1 ...
# Expected: Write-Warning red line + polling continues.

# 3. Expired-cookie fatal path:
$env:ARL_COOKIE_HEALTH_GATE = "fatal"
.\scripts\windows-agent-loop.ps1 ...
# Expected: throw + non-zero exit, polling never reached.

# 4. Skip path:
$env:ARL_COOKIE_HEALTH_GATE = "skip"
.\scripts\windows-agent-loop.ps1 ...
# Expected: no "running cookie-health gate" line; polling starts immediately.
```

No pytest changes — PowerShell-only behavior.

### Commit

```
feat(launcher): cookie-health gate before windows-agent polling loop
```

---

## Risky files / rollback points

- `scripts/windows-agent-loop.ps1` — only file with behavior change. Revert restores previous direct-to-polling-loop flow.
- README.md — documentation; trivially revertable.

## Follow-ups (out of scope)

- Multi-cookie pool / rotation (E2).
- `.env.local` persistence + `arl cookie-set` CLI (E3).
- `arl cookie-health --watch` long-running monitor (E4).
