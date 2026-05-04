# WSL Reference Scan

> Punch list for PR2 (doc/spec cleanup) and PR3 (delete WSL artifacts) of task
> `05-04-migrate-to-pure-windows`. Each entry: file path → exact line number →
> what the migration must do.

## Summary

- **Total in-scope occurrences**: 47 (across 6 active files)
- **Files in archive (skipped, untouched)**: 11 task directories, listed at end
- **Top cleanup targets**: `README.md` (~25 lines), `.trellis/spec/backend/launcher-conventions.md` (entire spec restructure), `scripts/wsl-*.sh` (entire files deleted)
- **Hidden surprise**: `src/auto_record_live.egg-info/PKG-INFO` mirrors `README.md` content — auto-regenerated on next `pip install -e .`, no manual edit needed (and arguably should be in `.gitignore`)
- **Confirmed clean**: `frontend/`, `guides/`, `AGENTS.md`, `src/arl/config.py`, `src/arl/cli.py` — zero WSL references in code or other specs

---

## Punch list

### `README.md` (PR2)

| Line | Content | Action |
|---|---|---|
| 18 | `- WSL2 Ubuntu：` (architecture overview list item) | Replace entire "运行时分层" sub-list with single-bullet pure-Windows architecture |
| 19–24 | "WSL2 Ubuntu" responsibilities (编排/状态管理/录制控制/对局切分/字幕生成/导出) | Merge into the Windows host bullet — same responsibilities, now all on Windows |
| 109–110 | `# 2. WSL 侧：消费事件...` + `.venv-wsl/bin/python -m arl.cli orchestrator --once` | Replace with `.venv\Scripts\python.exe -m arl.cli orchestrator --once` (PowerShell path) |
| 112–113 | `# 3. WSL 侧：执行一次录制` + `.venv-wsl/bin/python -m arl.cli recorder` | Replace with `.venv\Scripts\python.exe -m arl.cli recorder` |
| 117 | "Windows 终端（会循环执行 `windows-agent --once`）" — heading line for stable loop | Restructure: one heading per PowerShell window, three windows now (agent / orchestrator / recorder) |
| 121 | `powershell -ExecutionPolicy Bypass -File "\\wsl$\Ubuntu-24.04\www\auto-record-live\scripts\windows-agent-loop.ps1"` | Replace UNC `\\wsl$\Ubuntu-24.04\www\auto-record-live` with Windows-local `C:\auto-record-live` example |
| 124 | `-ProjectPath "\\wsl$\Ubuntu-24.04\www\auto-record-live"` | Drop UNC; default `$PSScriptRoot` resolution suffices since project is now Windows-local |
| 127–131 | "WSL 终端 1（编排循环）：" block + `bash scripts/wsl-orchestrator.sh /www/auto-record-live` | Replace with PowerShell command for `windows-orchestrator-loop.ps1` |
| 133–137 | "WSL 终端 2（录制循环，每 5 秒扫描一次）：" block + `bash scripts/wsl-recorder-loop.sh /www/auto-record-live 5` | Replace with PowerShell command for `windows-recorder-loop.ps1` |
| 139 | "> 说明：WSL 脚本默认使用独立虚拟环境 `.venv-wsl`..." | Delete entirely — single shared `.venv` now, no separation needed |
| 140 | "> 说明：建议把项目放在 WSL 原生目录..." | Delete — replace with OneDrive-path warning |
| 141 | "> 说明：`windows-agent-loop.ps1` 默认会自动使用脚本所在仓库目录；也可显式传入 `-ProjectPath`（例如 `\\wsl$\Ubuntu-24.04\www\auto-record-live`）" | Strip the `\\wsl$\` example, keep the `-ProjectPath` mechanic |
| 142 | "> 说明：请确保 Windows 侧与 WSL 侧指向同一仓库目录..." | Delete — no two sides anymore |
| 143 | "> 说明：`ARL_WSL_INSTALL_MODE` 默认 `if-missing`..." | Replace with `ARL_WIN_INSTALL_MODE` (already the canonical Windows env var per launcher-conventions) |
| 149–151 | `.venv-wsl/bin/python -m arl.cli stage-hints-auto` (×3) | All `.venv-wsl/bin/python` → `.venv\Scripts\python.exe` |
| 154 | `.venv-wsl/bin/python -m arl.cli subtitles` | Same |
| 157 | `.venv-wsl/bin/python -m arl.cli exporter` | Same |
| 163–165 | `.venv-wsl/bin/python -m arl.cli recovery` (×3) | Same |
| **NEW (insert)** | — | **Add "Windows 三依赖一键装" section** with winget commands (Python, Node.js LTS, ffmpeg) before "快速开始" |
| **NEW (insert)** | — | **Add OneDrive-path warning** + Microsoft Store Python warning (cross-reference `windows-agent-loop.ps1` `py -3` defense) |

**Estimated edit volume**: ~25 lines deleted, ~30 lines rewritten, ~15 lines inserted.

---

### `.trellis/spec/backend/launcher-conventions.md` (PR2)

This spec is structured around **WSL bash + Windows PowerShell parity**. Migration collapses it to **single-runtime PowerShell**. Major restructure, not minor edits.

| Line | Content | Action |
|---|---|---|
| 3–5 | `> Conventions for the small bash and PowerShell launcher scripts...` (front-matter scope) | Rewrite: "Conventions for the three PowerShell launcher scripts under `scripts/` that bootstrap the agent / orchestrator / recorder polling loops on Windows." Drop bash mention. |
| 11–15 | `The repo currently has three launcher scripts:` + bullets | Update bullets: `windows-agent-loop.ps1` (existing), `windows-orchestrator-loop.ps1` (NEW, replaces wsl-orchestrator.sh), `windows-recorder-loop.ps1` (NEW, replaces wsl-recorder-loop.sh) |
| 17–19 | "must stay in lockstep across runtimes" | Reframe: "must stay aligned across launchers" — still three scripts, all PowerShell |
| 23–28 | "Cross-runtime parity for install-mode bootstrap" heading + intro | Rename: "Install-mode bootstrap parity across launchers". Drop "cross-runtime" framing. |
| 30 | Table header `\| WSL bash convention \| Windows PowerShell convention \|` | Collapse to single `\| Convention \|` column. Remove the bash convention column entirely. |
| 31–39 | Table body — every row currently has bash and PowerShell sides | Keep PowerShell side only. Drop env var name `ARL_WSL_INSTALL_MODE` row entirely (or rename to "Env var: `ARL_WIN_INSTALL_MODE`" single value) |
| 41–45 | Reference implementation list — `wsl-orchestrator.sh`, `wsl-recorder-loop.sh`, `windows-agent-loop.ps1` | Replace bash refs with new PowerShell ones: `windows-agent-loop.ps1`, `windows-orchestrator-loop.ps1`, `windows-recorder-loop.ps1` |
| 47–50 | "When you change one side..." paragraph | Reframe: "When you change one launcher's bootstrap behavior, you MUST mirror the change in the other two PowerShell scripts in the same task." |
| 62 | `\`.trellis/tasks/05-01-optimize-wsl-startup/prd.md\`` cross-reference | Update path: this task should now live under `.trellis/tasks/archive/2026-05/05-01-optimize-wsl-startup/prd.md` (already archived per ls output) |
| 78 | `Both \`.venv/\` and \`.venv-wsl/\` are listed in \`.gitignore\`` | Drop `.venv-wsl/`. Just `.venv/` now. |
| 86 | `(This was the original trigger for task \`05-01-optimize-wsl-startup\`.)` | Update path to archived location |
| 175–193 | "### Common Mistake: WSL launcher updated, Windows launcher silently drifts" entire section | **Decision needed**: this Common Mistake is now historical. Two options: (a) delete entirely (irrelevant after migration); (b) rewrite as "PowerShell launcher updated, peer scripts silently drift" with `windows-{agent,orchestrator,recorder}-loop.ps1` all in scope. Option (b) preserves the lesson. |
| 187–188 | `\`# Mirrors ARL_WSL_INSTALL_MODE in scripts/wsl-orchestrator.sh\`` example | Update example to cross-reference between PowerShell launchers |
| 191 | `scripts/{wsl,windows}-*` glob | Change to `scripts/windows-*` |
| 200 | `A unified \`wsl-fast.sh\` / \`windows-fast.ps1\` entrypoint.` | Drop `wsl-fast.sh` reference; keep just `windows-fast.ps1` (or rename Out-of-Scope note to be platform-neutral) |

**Recommended approach**: rewrite this spec as PR2's centerpiece, not a patch. Reading the whole file post-migration, an outsider should never know WSL existed. ADR-style note at top: "Migrated from dual-runtime WSL+Windows to pure Windows on YYYY-MM-DD; see `.trellis/tasks/archive/2026-05/05-04-migrate-to-pure-windows/prd.md`."

---

### `.trellis/spec/backend/index.md` (PR2)

| Line | Content | Action |
|---|---|---|
| 23 | `\| [Launcher Conventions](./launcher-conventions.md) \| WSL/Windows bootstrap script parity, sentinel discipline \| Active \|` | Update description: drop "WSL/Windows" — change to "PowerShell launcher parity, sentinel discipline" or similar |

---

### `scripts/windows-agent-loop.ps1` (PR2 — minor cleanup)

This file stays (it's the agent launcher), but has a few WSL co-references that become dead branches after migration.

| Line | Content | Action |
|---|---|---|
| 20 | `throw "Project path not found: $ProjectPath\`nHint: pass -ProjectPath explicitly, e.g. \\wsl$\Ubuntu\www\auto-record-live"` | Update hint example to a Windows-local path: `e.g. C:\auto-record-live` |
| 47 | `# Mirrors scripts/wsl-orchestrator.sh:24-26: a venv created without working` (comment) | Update to reference the peer PowerShell launchers (`windows-orchestrator-loop.ps1` / `windows-recorder-loop.ps1`) instead |
| 70 | `# Mirrors ARL_WSL_INSTALL_MODE in scripts/wsl-orchestrator.sh: if-missing skips` (comment) | Update to reference `ARL_WIN_INSTALL_MODE` and the new PowerShell peers |
| 94–95 | `if ($ProjectPath -like "\\wsl$\*") { Write-Warning "Using a UNC WSL path from Windows may be slower; ..." }` | **Decision**: keep as defensive guard (warns if someone still uses WSL-mounted path despite migration), or delete entire branch (post-migration nobody should). Recommend **keep** — costs almost nothing and protects against legacy muscle-memory. |

---

### `scripts/wsl-orchestrator.sh` (PR3)

Entire file → `git rm scripts/wsl-orchestrator.sh`. 30 lines, no salvage value — `windows-orchestrator-loop.ps1` (created in PR1) replaces it.

References within (for context, will disappear with the file):
- Line 4: `PROJECT_PATH="${1:-/www/auto-record-live}"`
- Line 6: `VENV_DIR="${ARL_WSL_VENV_DIR:-.venv-wsl}"`
- Line 7: `INSTALL_MODE="${ARL_WSL_INSTALL_MODE:-if-missing}"`
- Line 11: hint string referencing `/www/auto-record-live`

---

### `scripts/wsl-recorder-loop.sh` (PR3)

Entire file → `git rm scripts/wsl-recorder-loop.sh`. ~50 lines, no salvage value — `windows-recorder-loop.ps1` replaces it.

References within (for context):
- Line 4: `PROJECT_PATH="${1:-/www/auto-record-live}"`
- Line 7: `VENV_DIR="${ARL_WSL_VENV_DIR:-.venv-wsl}"`
- Line 8: `INSTALL_MODE="${ARL_WSL_INSTALL_MODE:-if-missing}"`
- Line 12: hint string referencing `/www/auto-record-live`

---

### `.gitignore` (PR3)

| Line | Content | Action |
|---|---|---|
| 2 | `.venv-wsl/` | Delete the line. Only `.venv/` survives. |

---

### `src/auto_record_live.egg-info/PKG-INFO` (no manual action)

| Lines | Content | Action |
|---|---|---|
| 27–175 | Mirror of `README.md` — same WSL references | **Do not edit manually.** This is an `egg-info` artifact regenerated by `pip install -e .`. After PR2 updates `README.md`, run `pip install -e .` once to regenerate PKG-INFO. |
| **Bonus**: arguably this file shouldn't be in git at all | — | **Optional follow-up** (not required for this task): add `src/*.egg-info/` to `.gitignore` and `git rm --cached`. Track as separate cleanup if relevant. |

---

### Files confirmed CLEAN (no WSL references found)

- `AGENTS.md`
- `.trellis/spec/frontend/*.md` (all)
- `.trellis/spec/guides/*.md` (all)
- `src/arl/config.py`
- `src/arl/cli.py`
- `pyproject.toml`
- `.env.example`

---

## Files in archive (skipped — historical record, untouched)

These contain WSL references but live under `.trellis/tasks/archive/` and represent completed history. **Do not modify.**

- `.trellis/tasks/archive/2026-05/05-01-optimize-wsl-startup/{prd.md,implement.jsonl,check.jsonl,task.json}`
- `.trellis/tasks/archive/2026-05/05-04-windows-launcher-ensurepip/{prd.md,implement.jsonl,check.jsonl}`
- `.trellis/tasks/archive/2026-05/05-01-harden-www-migration/prd.md`
- `.trellis/tasks/archive/2026-05/04-29-fix-browser-capture-ffmpeg-failure/prd.md`
- `.trellis/tasks/archive/2026-05/05-01-update-readme-browser-capture-docs/prd.md`
- `.trellis/tasks/archive/2026-04/04-23-auto-live-recording-pipeline/{info.md,prd.md}`
- `.trellis/tasks/archive/2026-04/04-28-save-windows-wsl-run-scripts/{task.json,check.jsonl,implement.jsonl}`
- `.trellis/tasks/archive/2026-04/04-25-continue-auto-live-recording/prd.md`

These are valid because:
- They reflect the project state at the time the task was active.
- launcher-conventions.md cross-references `05-01-optimize-wsl-startup` for context — that link is fine after archive (the path is `archive/2026-05/05-01-optimize-wsl-startup/`).
- Future spec readers benefit from understanding the migration was a deliberate decision, not erasure.

---

## PR mapping recap (for cross-reference)

| PR | Files touched |
|---|---|
| **PR1** (additive) | NEW: `scripts/windows-orchestrator-loop.ps1`, `scripts/windows-recorder-loop.ps1` — no edits to existing files |
| **PR2** (doc/spec rewrite) | `README.md`, `.trellis/spec/backend/launcher-conventions.md`, `.trellis/spec/backend/index.md`, `scripts/windows-agent-loop.ps1` (minor comment + hint cleanup) |
| **PR3** (deletes) | `scripts/wsl-orchestrator.sh`, `scripts/wsl-recorder-loop.sh`, `.gitignore` (one line) |
| Auto-regenerated | `src/auto_record_live.egg-info/PKG-INFO` (regenerates on next `pip install -e .`) |
