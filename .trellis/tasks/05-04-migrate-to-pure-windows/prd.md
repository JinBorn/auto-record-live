# Migrate to Pure-Windows Runtime

## Goal

把 `auto-record-live` 从"Windows 探测 + WSL2 编排/录制/字幕/导出"的混合架构，迁移成 **单一原生 Windows 运行时**，消除 Windows ↔ WSL 跨文件系统的 9P/SMB IO 开销。迁移后所有阶段都在 Windows 一个 venv 里跑：Playwright 抖音探测 → orchestrator → ffmpeg 录制 → segmenter → faster-whisper 字幕 → exporter。

## Progress

| PR | 状态 | 提交 / 分支 | 备注 |
|---|---|---|---|
| **PR1** Launcher 移植 | ✅ Smoke test passed (Phases A–E, on Windows D:\ Python 3.14.4) | branch `feat/migrate-pure-windows-pr1`，commit `75ff870` | 新增 `scripts/windows-orchestrator-loop.ps1` + `windows-recorder-loop.ps1`；冒烟 checklist 见 `research/pr1-smoke-test.md`；smoke-test 在 D:\code\auto-record-live 跑通 (2026-05-04)，发现两处文档误差已修（recorder loop 注释 + Phase A ensurepip 期望） |
| **PR2** Doc / Spec 重写 | ✅ Done | branch `feat/migrate-pure-windows-pr1`，commit `6a971ed` | README 改单 Windows + winget + OneDrive/Microsoft Store 警告；launcher-conventions 大重构（ADR 注 + 单 PS 列表 + WSL drift mistake 改写）；index.md 描述更新；三个 PowerShell launcher 共 9 处 wsl-*.sh 注释引用清理；punch list 见 `research/wsl-reference-scan.md` |
| **PR3** 删除 WSL 工件 | ⬜ Not started | — | `git rm scripts/wsl-orchestrator.sh scripts/wsl-recorder-loop.sh` + `.gitignore` 删 `.venv-wsl/` + `pip install -e .` 重生 PKG-INFO；预估 5 分钟，下 session 处理 |

**子代理基础设施全程不可用**：当前 session 的 trellis-research × 2、trellis-implement × 1 均 500/400，所有研究 + 实现均在主线程内手工完成（仍然按 spec 走 launcher-conventions / logging-guidelines）。下一 session 在 Windows 上若 sub-agent 可用，可正常派发。

## Handoff — Continuing on Windows host

**触发场景**：开发主机从 WSL 切换为原生 Windows（`D:\` 拉一份新仓库，Claude Code 直接在 Windows 跑）。

### 1. Windows 那边一次性准备

```powershell
# 安装三件套（如未装）
winget install Python.Python.3.12
winget install OpenJS.NodeJS.LTS
winget install Gyan.FFmpeg

# 拉仓库（避开 OneDrive 同步路径）
cd D:\
git clone git@github.com:JinBorn/auto-record-live.git
cd auto-record-live
git fetch origin
git checkout feat/migrate-pure-windows-pr1
```

### 2. 跑 PR1 冒烟

跟 `.trellis/tasks/05-04-migrate-to-pure-windows/research/pr1-smoke-test.md` 走完 5 个相 A→E。每相要打勾的项都列出来了。

### 3. Claude Code 重新激活 Trellis 任务

新 session 在 Windows 上首次启动后：

```powershell
python ./.trellis/scripts/task.py current --source
# 大概率显示 "no active task"（session 标识不同）

python ./.trellis/scripts/task.py start 05-04-migrate-to-pure-windows
# 把任务重新绑到当前 session
```

任务内的 `prd.md` / `research/*.md` / `implement.jsonl` / `check.jsonl` 都会跟着分支被 checkout 出来，新 session 一上手就能看见全部上下文。

### 4. 冒烟通过后继续 PR2

在 Windows 主线程里告诉 Claude "PR1 通过了，继续 PR2"。PR2 的工作清单已经在 `research/wsl-reference-scan.md` 列好（按文件、按行号），直接按表执行。

### 5. 如果冒烟踩坑

把失败 `[ARL]` 行 + 完整 PowerShell 报错 + `.\.venv\Scripts\python.exe --version` 输出贴回来，新 session 在同一分支上修，再 push。

---

## Requirements

**Launcher 重构（核心）**

- R1. 新增 `scripts/windows-orchestrator-loop.ps1`、`scripts/windows-recorder-loop.ps1`，复用 `windows-agent-loop.ps1` 的 venv 自举 / `ensurepip` / `.deps-ready` sentinel / try-catch NativeCommandError 防御等模式
- R2. 三个 launcher 全部使用单一共享 `.venv`（同一 `pyproject.toml`，无需切分）
- R3. 三个 launcher 必须按 `.trellis/spec/backend/launcher-conventions.md` 表格对齐：env var 命名（`ARL_WIN_INSTALL_MODE`）、sentinel 文件、`[ARL]` 日志前缀、install-mode 语义
- R4. 沿用 PowerShell 5.1 兼容语法（不引入 `&&` / `??` / 三元运算符），与现有 `windows-agent-loop.ps1` 保持一致

**清理（Hard cut-over）**

- R5. 删除 `scripts/wsl-orchestrator.sh`、`scripts/wsl-recorder-loop.sh`
- R6. 仓库 / 文档 / spec 中所有 `.venv-wsl`、`/www/auto-record-live`、`\\wsl$\` 例子全部清理或替换成 Windows 等价
- R7. README 删除整个 "WSL 终端 1 / WSL 终端 2" 段落，主流程改纯 Windows

**文档 / Spec 同步**

- R8. README 重写"快速开始 / 录制命令执行流程"两节：
  - Windows 三依赖（Python 3.11+ / Node.js LTS / ffmpeg）以 winget 为主：`winget install Python.Python.3.12`、`winget install OpenJS.NodeJS.LTS`、`winget install Gyan.FFmpeg`
  - 提示避开 OneDrive 路径（`C:\Users\<u>\OneDrive\...` 会破坏 venv 文件锁）
  - 给出 `C:\auto-record-live` 作为推荐项目位置示例
  - 三个 PowerShell 窗口各跑一个 `windows-*-loop.ps1`
  - faster-whisper 仍按现状：`pip install faster-whisper` 单独一步，README 说明
  - 警告 Microsoft Store 版 Python 已知问题（已被 launcher 的 `py -3` 优先 + ensurepip 兜底覆盖，但提一句）
- R9. `.trellis/spec/backend/launcher-conventions.md` 从"WSL bash + Windows PS 双栏对照"改成"三个 PowerShell launcher 单栏"
- R10. `AGENTS.md` / `.trellis/spec/` 任何提及 WSL 或 `/www/auto-record-live` 路径的地方一并扫一次清理

## Acceptance Criteria

- [ ] `scripts/wsl-*.sh`、`.venv-wsl` 不再出现在仓库
- [ ] `scripts/windows-orchestrator-loop.ps1`、`scripts/windows-recorder-loop.ps1` 存在，按 launcher-conventions 表格对齐
- [ ] 三个 PowerShell launcher 共享同一 `.venv`
- [ ] `grep -ri "wsl\|/www/auto-record-live\|\.venv-wsl" README.md AGENTS.md .trellis/spec/` 应只剩历史 ADR 类语境，不再出现在任何"如何运行"指引里
- [ ] PRD 里写好"全新 Windows 主机冒烟 checklist"（venv 自举 → Playwright 探测一次 → orchestrator 一次 → recorder 一次 → segmenter / subtitles / exporter 各跑一遍），人工跑通
- [ ] 现有 Python 单元测试 + lint + typecheck 全绿，无新破坏面

## Definition of Done

- 现有 Python 测试 / lint / typecheck 全绿
- 三个 PowerShell launcher 按 launcher-conventions 对齐，spec 同步更新
- README + AGENTS.md + 相关 spec 文档清理 WSL 引用
- 端到端冒烟 checklist 写进 PRD，作者本机跑通一次

## Technical Approach

**架构变化**

```
旧：Windows agent (.venv)  ─events.jsonl─►  WSL orchestrator/recorder (.venv-wsl)
                                              │
                                              ▼
                                            data/ (跨 9P 读写，慢)

新：Windows agent / orchestrator / recorder 三进程，全在原生 NTFS 上的 .venv
                                              │
                                              ▼
                                            data/ (本地 NTFS，零跨界)
```

**Launcher 模板（沿用 `windows-agent-loop.ps1`）**

每个 `windows-*-loop.ps1` 共享：
1. `param(...)` 入参（项目路径、轮询间隔、必要时业务参数）
2. `$ErrorActionPreference = "Stop"` + `Set-StrictMode -Version Latest`
3. Python 自举：`py -3` 优先，`python` 兜底
4. `.venv` 创建：`python -m venv .venv`
5. pip 健康探测：`try { & $venvPython -m pip --version *> $null } catch {}` → `ensurepip --upgrade` 兜底
6. install 模式分支：`ARL_WIN_INSTALL_MODE=if-missing|always`，`.deps-ready` sentinel
7. 轮询主循环：`while ($true) { python -m arl.cli <subcommand> --once; Start-Sleep -Seconds $IntervalSeconds }`

**配置不变**

- `.env.example` 22 个 `ARL_*` 变量都是相对路径，跨平台兼容，无需修改
- `ARL_DOUYIN_PROFILE_DIR=data/tmp/chrome-profile` 默认值保留

**代码不变**

- `src/arl/recorder/service.py:678-689` 的 `gdigrab/x11grab/avfoundation` 自动选择保留（即使我们只跑 Windows，分支仍合法，删了反而损失防御）
- `src/arl/windows_agent/probe.py` Playwright 调用无平台耦合
- `src/arl/subtitles/service.py` faster-whisper 延迟加载逻辑保留

## Decision (ADR-lite)

**Context**: 当前 Windows + WSL 双 OS 跑同一个仓库，跨 9P / SMB 文件系统的 IO 开销显著拖慢 Playwright Chromium profile 读写、ffmpeg 录制写盘、faster-whisper 模型 cache 等高频 IO 路径。无论项目放在 `/mnt/d/...`（WSL 慢）还是 `\\wsl$\...`（Windows 慢），都有一侧承受跨 OS 开销。

**Decision**: Hard cut-over 到纯 Windows 单一运行时。三个 PowerShell launcher 各自维护，共享 `.venv`，依赖通过 winget 安装。完全弃用 WSL 路径（无 legacy 目录、无双轨）。

**Consequences**:
- ✅ 文件系统瓶颈彻底消失
- ✅ Playwright Chromium 跑在原生 Windows 指纹下，抗抖音风控最稳（这是非常重要的隐性收益）
- ✅ launcher-conventions 从 "跨 runtime 对齐" 简化为单 runtime
- ⚠️ Linux 端代码删除后，未来若需要 Linux 部署需重新评估（接受）
- ⚠️ 长跑只能靠 PowerShell 窗口；未来若需 Service 化，再起独立任务
- ⚠️ 抖音 / Microsoft Store Python / OneDrive 等 Windows 特有坑要在 README 明确告警

## Out of Scope (MVP)

- ❌ faster-whisper 装载优化 / 加 `[project.optional-dependencies]` / CUDA 接入
- ❌ 开机自启 / Windows Service / Task Scheduler / NSSM
- ❌ 多主播并发录制（仍单 `.env` 单进程）
- ❌ PowerShell launcher 自动化测试（Pester / 等）—— 仅靠 spec + 人工冒烟 checklist
- ❌ 历史 `.venv-wsl` 数据 / WSL 端 `data/` 产物迁移工具（接受重建）
- ❌ Linux 兼容性保留（hard cut-over）

## Future Work（路线图占位，不在本任务实现）

- **单局对局内 dead-time skip 剪辑**：在现有 `MatchBoundary` 内部进一步剪辑，跳过死亡等待 / 回城补给 / 长时间静止画面，提升观看体验。轻量实现路线：
  - ffmpeg `silencedetect` 滤镜识别音频静音段
  - ffmpeg `select='gt(scene,X)'` 反向用于检测画面静止段
  - 两者交集 = 可跳区间，ffmpeg `concat` filter 合回
  - 不依赖 ML，预估 1–3 天 MVP
  - 触发实现时另起 brainstorm 任务，本 PRD 仅做路线图记录

## Implementation Plan (small PRs)

**PR1 – 新增 PowerShell launcher（additive，不破坏 WSL 路径）**
- `scripts/windows-orchestrator-loop.ps1` 移植自 `wsl-orchestrator.sh`
- `scripts/windows-recorder-loop.ps1` 移植自 `wsl-recorder-loop.sh`
- 共享 `.venv`，`ARL_WIN_INSTALL_MODE` 环境变量，`.deps-ready` sentinel
- 作者本机跑一次冒烟（Windows 三窗口 + Playwright + ffmpeg + 字幕 + 导出）

**PR2 – 文档 + spec 重写**
- README "快速开始" / "录制命令执行流程" 改为纯 Windows，winget 命令、OneDrive 警告、Microsoft Store Python 警告
- `.trellis/spec/backend/launcher-conventions.md` 从双栏改单栏
- `AGENTS.md`、其它 `.trellis/spec/` 文件中 WSL / `/www/...` 路径扫一遍清理

**PR3 – 删除 WSL 工件**
- `git rm scripts/wsl-orchestrator.sh scripts/wsl-recorder-loop.sh`
- `.gitignore` 中 `.venv-wsl/` 的条目（如果仅引用此用途，可删）
- `README.md` / `AGENTS.md` 中残留的 `.venv-wsl` / `\\wsl$\` 字符串清理（PR2 已大半完成，PR3 兜底）

**冒烟 checklist（每 PR 末尾跑一遍，PR2 后是发布版）**
1. 新 Windows 机器（或干净仓库）：`winget install Python.Python.3.12 OpenJS.NodeJS.LTS Gyan.FFmpeg`
2. `git clone` 到 `C:\auto-record-live`
3. 配置 `.env`（room URL + streamer name）
4. 三个 PowerShell 窗口分别跑 `windows-agent-loop.ps1` / `windows-orchestrator-loop.ps1` / `windows-recorder-loop.ps1`
5. 等待 Playwright 探测产生 events.jsonl → orchestrator 创建 recording job → recorder 用 ffmpeg 录一段
6. `pip install faster-whisper` 后跑 `arl subtitles` 一次
7. `arl exporter` 跑一次
8. `data/exports/` 出现带字幕的 mp4

## Technical Notes

**已检查的关键文件：**
- `README.md` — 完整快速起步流程
- `scripts/windows-agent-loop.ps1` — PowerShell launcher 模板
- `scripts/wsl-orchestrator.sh`、`scripts/wsl-recorder-loop.sh` — 待移植源
- `.trellis/spec/backend/launcher-conventions.md` — 跨 launcher 契约
- `src/arl/windows_agent/probe.py:67-93` — Playwright 调用
- `src/arl/recorder/service.py:662-689` — 平台自动选择
- `src/arl/subtitles/service.py:232-240` — faster-whisper 延迟加载
- `pyproject.toml` — 纯 Python 依赖
- `.env.example` — 22 个 ARL_* 配置

**Research References：**
- [`research/wsl-reference-scan.md`](research/wsl-reference-scan.md) — 完整 WSL 引用清扫清单（47 个引用 / 6 个活跃文件，按 PR1/PR2/PR3 映射）

**研究待办（延后到 PR2 实现期顺手做，trellis-research sub-agent 当前 API 网关 500 不可用）：**
- winget 包名最终确认（`Python.Python.3.12` vs `3.13`、`OpenJS.NodeJS.LTS`、`Gyan.FFmpeg`）—— 1-2 个 WebSearch 即可，写 README 时一次解决
- README 给一个 ffmpeg PATH 验证命令（`ffmpeg -version`）
- OneDrive venv 风险 / Microsoft Store Python 警告的官方依据链接
