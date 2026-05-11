# auto-record-live

一个本地优先（local-first）的直播录制 MVP，目标是：

- 监控固定的抖音主播直播间
- 自动录制《英雄联盟》直播
- 按对局切分录制视频
- 生成离线字幕
- 导出每局一份带字幕烧录的视频

## 架构概览

运行时为单一原生 Windows 主机，三组 PowerShell 长跑进程共享同一 `.venv`：

- **Windows agent**：按 `ARL_PLATFORMS` 配置串行探测各平台直播间状态（抖音走 Playwright；B 站走 anonymous HTTP API），输出 `data/tmp/windows-agent-events.jsonl`
- **Orchestrator**：消费 agent 事件，维护会话与录制任务状态，写 `data/tmp/orchestrator-state.json`
- **Recorder**：拉起 ffmpeg 录制并写入 `data/raw/<session>/recording-source.mp4`；流不可用时优雅降级为 placeholder 资产。Probe 在 `AgentSnapshot.stream_headers` 提供的平台特定 HTTP header（如 B 站要求的 `Referer`）会自动透传到 ffmpeg `-user_agent` / `-headers` 参数，recorder 自身保持平台中立。

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

设置直播间与主播信息后再测试（或写入 `.env`）。`ARL_PLATFORMS` 缺省为 `douyin`，下面示例只需设置 `ARL_DOUYIN_*` 即可走默认抖音路径；要切换或并列 B 站见下一节：

```powershell
$env:ARL_DOUYIN_ROOM_URL = "https://live.douyin.com/<room>"
$env:ARL_STREAMER_NAME = "<streamer>"
.\.venv\Scripts\python.exe -m arl.cli windows-agent --once
```

### B 站接入

B 站走 anonymous HTTP API（无需 Playwright），与抖音可单独运行也可同时运行。最少配置：

```powershell
# 仅 B 站：
$env:ARL_PLATFORMS = "bilibili"
$env:ARL_BILIBILI_ROOM_URL = "https://live.bilibili.com/<room_id>"
$env:ARL_BILIBILI_STREAMER_NAME = "<streamer>"
.\.venv\Scripts\python.exe -m arl.cli windows-agent --once

# 同时跑抖音 + B 站（顺序就是 polling 顺序）：
$env:ARL_PLATFORMS = "douyin,bilibili"
```

**B 站与抖音的差异**：

- **纯 HTTP API**：调 `api.live.bilibili.com/room/v1/Room/get_info` 取状态、`xlive/web-room/v2/index/getRoomPlayInfo` 取拉流 URL。无 Playwright、无 cookie、无 WBI 签名，单次探测延迟通常 < 1s。
- **轮播识别**：B 站 `live_status==2`（轮播回放）会被映射为 `OFFLINE` + `reason=carousel_playback`，避免把循环回放误判为直播录下来。
- **ffmpeg header 自动注入**：B 站流强制要求 `Referer: https://live.bilibili.com`，由 `BilibiliRoomProbe.stream_headers()` 返回，orchestrator 透传到 recording job，recorder 注入为 ffmpeg `-headers` / `-user_agent` 参数。无需用户配置。
- **流 URL 时效**：`getRoomPlayInfo` 返回的 URL 含短时效 token；如果 recorder 启动延迟过长导致 token 过期失败，下一轮 30s probe 会拿到新鲜 URL，emit `live_stopped` → `live_started` 自然恢复。

### Cookie 配置与失效审计

抖音和 B 站都支持可选的 cookie 注入，用于解锁 1080P+ 直链：

- 抖音：`ARL_DOUYIN_COOKIE`（完整 Cookie header 值，例如 `sessionid=xxx; uid=123; ...`）。未配置时页面 DOM 只暴露匿名 `_hd`（720p60）层签名 URL，被严格质量门 `min_quality_tier=uhd` 拦截。
- B 站：`ARL_BILIBILI_SESSDATA`（仅 SESSDATA 原值，不带 `SESSDATA=` 前缀）。未配置时 `getRoomPlayInfo` 最高只返回 qn=250（720p），被 `min_stream_qn=400` 拦截。

Cookie 失效时不再静默降级到低画质，而是输出可查询信号：

- **审计事件（两个来源合流）**：
  - **Probe 路径**：每次 probe 检测到 cookie 失效（B 站 `code=-101`；抖音质量门在匿名基线 `_hd` 处拒绝），windows-agent 在原 `live_started`/`live_stopped` 之外额外追加一行 `cookie_expired_for_<platform>` 写入 `data/tmp/windows-agent-events.jsonl`，orchestrator 再写入 `data/tmp/orchestrator-events.jsonl`。
  - **Recorder 路径**：每次 ffmpeg 失败返回 `reason_code=http_403_forbidden`（"403 forbidden" / "server returned 403"）**且**该平台的 cookie env 已配置时，recorder 在原 `ffmpeg_record_failed` 之外额外追加一行 `cookie_expired_for_<platform>` 写入 `data/tmp/recorder-events.jsonl`，orchestrator 再写入 `data/tmp/orchestrator-events.jsonl`。
  - 用 `grep cookie_expired_for_ data/tmp/orchestrator-events.jsonl` 可一次性看到 probe 和 recorder 两路证据。
  - **B 站已知 false-positive**：B 站 `getRoomPlayInfo` 返回的 stream URL 内嵌短时效 token，与 SESSDATA 解耦；token 过期时 ffmpeg 也会拿到 403，触发 recorder 路径的 `cookie_expired_for_bilibili`。SESSDATA 实际并未过期。下面的 `arl cookie-health` 是权威检查，发现 recorder 路径告警后先跑一次 cookie-health 再决定是否刷新 SESSDATA。
- **CLI 主动检查**：

  ```powershell
  .\.venv\Scripts\python.exe -m arl.cli cookie-health
  ```

  对每个已配置平台跑一次 `detect()` + `classify_cookie_state()`，逐行打印 `platform=... status=fresh|expired|not_configured|error detail=...`，全部 fresh/未配置时退出码 0；任一已配置 cookie 失效则退出码 1 并附 `hint=Refresh ...` 行。可串到 launcher 启动检查或定期巡检脚本里。

抓取 cookie 的方式见各平台 dev tools 抓 Network → 复制 `Cookie` request header（抖音）/ Application → Cookies → SESSDATA（B 站）。

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

#### ffmpeg 失败排查（recorder）

录制器对 ffmpeg 失败的处理已经走"分类 + 短路 + 退避 + session 预算"四层防御。排查时按以下顺序查：

1. **审计行**：`data/tmp/recorder-events.jsonl` 中的 `ffmpeg_record_failed` 行包含 `decision`（`attempt_failed` 表示非可重试、立即收手；`attempt_failed_yield_to_next_probe` 表示瞬时失败、单次尝试后让出给下一轮 probe）+ `failure_category` + `reason_code`（`http_4xx` / `http_403_forbidden` / `http_5xx` / `network_timeout` / `ffmpeg_process_error` / `unknown_unclassified`）+ `reason_detail`，以及内嵌的 `stderr_excerpt`（首 5 + 末 15 行截断到 240 字符/行，总长 ≤ 4 KB）。`reason_code=http_403_forbidden` 是"cookie 嫌疑"信号 —— 若该平台 cookie env 已配置，同一审计文件里还会追加一行 `cookie_expired_for_<platform>`，配合下面 `arl cookie-health` 验证。
2. **完整 stderr**：审计行的 `stderr_log_path` 字段指向 `data/tmp/recorder-stderr/<job_id>-<attempt>.log` 完整 ffmpeg stderr 转储；recorder 启动时按 mtime 滚动只保留最近 N 个文件，N 由 `ARL_RECORDER_STDERR_RETAIN_COUNT`（默认 200）控制。
3. **退避状态**：`data/tmp/recorder-state.json` 中的 `next_eligible_at_by_job_id[job_id]` 记录每次瞬时失败后的下次可调度时间（1s → 5s → 15s → 60s 封顶），未到期的 job 会在主循环里日志 `job deferred ...` 并跳过 ffmpeg。
4. **session 预算**：同一 session 累计瞬时失败次数由 `retries_by_session_id[session_id]` 跟踪；上限由 `ARL_RECORDER_SESSION_RETRY_BUDGET`（默认 8）控制，达到上限后所有非 FAILED job 会被升级为 `recording_session_retry_budget_exceeded` 审计 + 进入 manual recovery 路径。

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
