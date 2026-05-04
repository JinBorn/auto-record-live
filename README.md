# auto-record-live

一个本地优先（local-first）的直播录制 MVP，目标是：

- 监控固定的抖音主播直播间
- 自动录制《英雄联盟》直播
- 按对局切分录制视频
- 生成离线字幕
- 导出每局一份带字幕烧录的视频

## 架构概览

运行时为单一原生 Windows 主机，三组 PowerShell 长跑进程共享同一 `.venv`：

- **Windows agent**：用 Playwright 探测抖音直播间状态变化，输出 `data/tmp/windows-agent-events.jsonl`
- **Orchestrator**：消费 agent 事件，维护会话与录制任务状态，写 `data/tmp/orchestrator-state.json`
- **Recorder**：拉起 ffmpeg 录制并写入 `data/raw/<session>/recording-source.mp4`；流不可用时优雅降级为 placeholder 资产

录制完成后再依次跑离线后处理：对局切分（segmenter）、字幕（faster-whisper）、导出（exporter）。所有数据停留在原生 NTFS，无跨文件系统 IO 瓶颈。

## 仓库结构

```text
src/arl/
  cli.py
  config.py
  windows_agent/
  orchestrator/
  recorder/
  segmenter/
  subtitles/
  exporter/
  shared/
```

## 当前开发状态

当前仓库仍是 MVP 阶段，已具备核心骨架与部分可运行链路。

已实现（摘要）：

- 项目结构、共享配置与事件模型
- CLI 入口
- Windows agent 首版轮询与 JSONL 事件输出
- orchestrator 事件消费与会话/任务持久状态
- 文件驱动的后处理骨架（录制资产、分段边界、字幕资产、导出资产）
- 部分 `ffmpeg` 录制/导出路径
- 手动恢复动作流水线与恢复状态追踪
- 语义 stage-signal / stage-hint 的若干自动化与手动命令

未实现（生产级能力）：

- 抖音直链采集在页面变动/风控下的稳健性
- LoL 语义阶段识别生产化
- `faster-whisper` 离线 ASR 的工程化加固
- `ffmpeg` 失败场景已具备基础重试/恢复能力，但仍需生产级强化

## Windows 环境准备

**一键装三依赖（推荐 winget）：**

```powershell
winget install Python.Python.3.12
winget install OpenJS.NodeJS.LTS
winget install Gyan.FFmpeg
```

> **避坑 1（OneDrive）**：项目目录请放在本地 NTFS（如 `C:\auto-record-live` 或 `D:\auto-record-live`）。**不要**放在 OneDrive 同步目录（`C:\Users\<u>\OneDrive\...`）—— OneDrive 的同步会破坏 venv 文件锁，并污染 editable install 的 `__editable__.*.pth` / `RECORD` 文件。
>
> **避坑 2（Microsoft Store Python）**：避免使用 Microsoft Store 版 Python，其 `ensurepip` 可能损坏导致 venv 没有可用 pip。winget 安装的 `Python.Python.3.x` 或 [python.org](https://www.python.org/) 安装包均无此问题（launcher 自带 `try/catch + ensurepip --upgrade` 兜底，但首选避免该坑）。

## 快速开始

创建虚拟环境并安装依赖：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
npm install
```

如需从仓库根目录自动加载主播配置，可先基于 `.env.example` 创建 `.env`。

查看命令：

```powershell
.\.venv\Scripts\python.exe -m arl.cli --help
```

执行一次 Windows agent：

```powershell
.\.venv\Scripts\python.exe -m arl.cli windows-agent --once
```

如需真实浏览器探测，先安装 Playwright 浏览器：

```powershell
npx playwright install chromium
```

设置直播间与主播信息后再测试（或写入 `.env`）：

```powershell
$env:ARL_DOUYIN_ROOM_URL = "https://live.douyin.com/<room>"
$env:ARL_STREAMER_NAME = "<streamer>"
.\.venv\Scripts\python.exe -m arl.cli windows-agent --once
```

## 录制命令执行流程（MVP）

### 1) 先做单次链路验证（建议首次必跑）

```powershell
# 1. Windows agent：探测一次，产出事件
.\.venv\Scripts\python.exe -m arl.cli windows-agent --once

# 2. Orchestrator：消费事件并生成/推进录制任务
.\.venv\Scripts\python.exe -m arl.cli orchestrator --once

# 3. Recorder：执行一次录制
.\.venv\Scripts\python.exe -m arl.cli recorder
```

### 2) 再切换到常驻运行（推荐）

打开 **三个 PowerShell 窗口**，分别跑三组 launcher：

**窗口 1（agent 探测循环）：**

```powershell
.\scripts\windows-agent-loop.ps1 -RoomUrl "你的直播间URL" -StreamerName "主播名"
```

**窗口 2（orchestrator 编排循环）：**

```powershell
.\scripts\windows-orchestrator-loop.ps1
```

**窗口 3（recorder 录制循环，每 5 秒扫描一次）：**

```powershell
.\scripts\windows-recorder-loop.ps1
```

> 说明：三个 launcher 共享同一 `.venv`，自带 venv 自举 + `ensurepip` 兜底 + `.deps-ready` sentinel 跳过重装。第一次启动会自动 `pip install -e .`；之后启动秒级返回。
> 说明：每个 launcher 默认用脚本所在仓库目录；也可显式传入 `-ProjectPath`（例如 `-ProjectPath "C:\auto-record-live"`）。
> 说明：`ARL_WIN_INSTALL_MODE` 默认 `if-missing`，仅首次安装依赖；如需每次启动都强制重装，设置 `$env:ARL_WIN_INSTALL_MODE = "always"`。
> 说明：`ARL_RECORDER_INTERVAL_SECONDS` 控制 recorder 轮询间隔（默认 5 秒），也可用 `-IntervalSeconds` 参数覆盖。

### 3) 录制完成后的后处理顺序（按需手动执行）

```powershell
# 1. 对局切分相关（可选：自动/语义/字幕驱动信号）
.\.venv\Scripts\python.exe -m arl.cli stage-hints-auto
.\.venv\Scripts\python.exe -m arl.cli stage-hints-semantic
.\.venv\Scripts\python.exe -m arl.cli stage-signals-from-subtitles

# 2. 字幕（首次需 pip install faster-whisper）
.\.venv\Scripts\python.exe -m arl.cli subtitles

# 3. 导出
.\.venv\Scripts\python.exe -m arl.cli exporter
```

### 4) 故障恢复与排查命令

```powershell
.\.venv\Scripts\python.exe -m arl.cli recovery
.\.venv\Scripts\python.exe -m arl.cli recovery --list-pending
.\.venv\Scripts\python.exe -m arl.cli recovery --summary
```

## 浏览器采集配置说明（ffmpeg）

- `ARL_BROWSER_CAPTURE_FORMAT=auto` 时按平台自动选择：
  - Windows：`gdigrab`
  - macOS：`avfoundation`
  - Linux/其他：`x11grab`
- `ARL_BROWSER_CAPTURE_FORMAT` 若配置为不支持值，会自动回落到当前平台默认值并记录日志。
- `ARL_BROWSER_CAPTURE_INPUT` 为空时会按采集格式自动解析输入：
  - `gdigrab`：`desktop`
  - `avfoundation`：`default:none`
  - `x11grab`：优先 `DISPLAY`，并在需要时探测 `:0 -> :0.0` 兜底候选。
- 若浏览器采集输入最终不可用，录制器会记录结构化跳过原因并降级为 placeholder 资产，避免流程阻塞。

## 说明

- Playwright 探测会打开持久化 Chromium 配置目录，首次真实测试可能需要手动登录。
- 当前链路主要覆盖“探测 -> 事件/状态 -> 后处理资产”的 MVP 闭环。
- 生产部署前建议先完善失败分类、恢复策略与观测指标。
