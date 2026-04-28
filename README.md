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
- `ffmpeg` 失败场景的生产级重试与恢复

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

## 常用命令（MVP 阶段）

- `recovery` 系列：

```bash
.venv/bin/python -m arl.cli recovery
.venv/bin/python -m arl.cli recovery --list-pending
.venv/bin/python -m arl.cli recovery --summary
```

- stage hint / signal：

```bash
.venv/bin/python -m arl.cli stage-hints-auto
.venv/bin/python -m arl.cli stage-hints-semantic
.venv/bin/python -m arl.cli stage-signals-from-subtitles
.venv/bin/python -m arl.cli stage-signal --session-id <session_id> --text "in game scoreboard" --at-seconds 95
.venv/bin/python -m arl.cli stage-hint --session-id <session_id> --stage in_game --at-seconds 120
```

- subtitles：

```bash
.venv/bin/python -m arl.cli subtitles
```

## 说明

- Playwright 探测会打开持久化 Chromium 配置目录，首次真实测试可能需要手动登录。
- 当前链路主要覆盖“探测 -> 事件/状态 -> 后处理资产”的 MVP 闭环。
- 生产部署前建议先完善失败分类、恢复策略与观测指标。
