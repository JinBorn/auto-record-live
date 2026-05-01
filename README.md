# auto-record-live

一个本地优先（local-first）的直播录制 MVP，目标是：

- 监控固定的抖音主播直播间
- 自动录制《英雄联盟》直播
- 按对局切分录制视频
- 生成离线字幕
- 导出每局一份带字幕烧录的视频

## 架构概览

运行时分层：

- Windows 主机：
  - 抖音浏览器会话自动化
  - 可选浏览器画面采集兜底
- WSL2 Ubuntu：
  - 编排与状态管理
  - 录制控制
  - 对局切分
  - 字幕生成
  - 导出

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

## 快速开始

创建虚拟环境并安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
npm install
```

如需从仓库根目录自动加载主播配置，可先基于 `.env.example` 创建 `.env`。

查看命令：

```bash
arl --help
```

执行一次 Windows agent：

```bash
.venv/bin/python -m arl.cli windows-agent --once
```

如需真实浏览器探测，先安装 Playwright 浏览器：

```bash
npx playwright install chromium
```

设置直播间与主播信息后再测试（或写入 `.env`）：

```bash
export ARL_DOUYIN_ROOM_URL="https://live.douyin.com/<room>"
export ARL_STREAMER_NAME="<streamer>"
.venv/bin/python -m arl.cli windows-agent --once
```

## 录制命令执行流程（MVP）

### 1) 先做单次链路验证（建议首次必跑）

```bash
# 1. Windows 侧：探测一次，产出 windows-agent 事件
.venv/bin/python -m arl.cli windows-agent --once

# 2. WSL 侧：消费事件并生成/推进录制任务
.venv-wsl/bin/python -m arl.cli orchestrator --once

# 3. WSL 侧：执行一次录制
.venv-wsl/bin/python -m arl.cli recorder
```

### 2) 再切换到常驻运行（推荐）

Windows 终端（会循环执行 `windows-agent --once`）：

```powershell
powershell -ExecutionPolicy Bypass -File "\\wsl$\Ubuntu-24.04\www\auto-record-live\scripts\windows-agent-loop.ps1" `
    -RoomUrl "你的直播间URL" `
    -StreamerName "主播名" `
    -ProjectPath "\\wsl$\Ubuntu-24.04\www\auto-record-live" `
```

WSL 终端 1（编排循环）：

```bash
bash scripts/wsl-orchestrator.sh /www/auto-record-live
```

WSL 终端 2（录制循环，每 5 秒扫描一次）：

```bash
bash scripts/wsl-recorder-loop.sh /www/auto-record-live 5
```

> 说明：WSL 脚本默认使用独立虚拟环境 `.venv-wsl`，避免与 Windows 的 `.venv` 互相污染。
> 说明：建议把项目放在 WSL 原生目录（如 `/www/auto-record-live`），避免 `/mnt/d/...` 带来的挂载 IO 开销。
> 说明：`windows-agent-loop.ps1` 默认会自动使用脚本所在仓库目录；也可显式传入 `-ProjectPath`（例如 `\\wsl$\Ubuntu-24.04\www\auto-record-live`）。
> 说明：请确保 Windows 侧与 WSL 侧指向同一仓库目录（例如 WSL `/www/auto-record-live`）。
> 说明：`ARL_WSL_INSTALL_MODE` 默认 `if-missing`，仅首次安装依赖；如需每次启动都重装，设置为 `always`。

### 3) 录制完成后的后处理顺序（按需手动执行）

```bash
# 1. 对局切分相关（可选：自动/语义/字幕驱动信号）
.venv-wsl/bin/python -m arl.cli stage-hints-auto
.venv-wsl/bin/python -m arl.cli stage-hints-semantic
.venv-wsl/bin/python -m arl.cli stage-signals-from-subtitles

# 2. 字幕
.venv-wsl/bin/python -m arl.cli subtitles

# 3. 导出
.venv-wsl/bin/python -m arl.cli exporter
```

### 4) 故障恢复与排查命令

```bash
.venv-wsl/bin/python -m arl.cli recovery
.venv-wsl/bin/python -m arl.cli recovery --list-pending
.venv-wsl/bin/python -m arl.cli recovery --summary
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
