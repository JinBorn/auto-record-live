# auto-record-live

本地 Windows 直播录制工具。它会按配置监控抖音 / B 站直播间，开播后录制，离线切分对局，生成字幕，并导出每局视频。

运行链路很简单：

```text
windows-agent -> orchestrator -> recorder -> postprocess -> recovery
```

- `windows-agent`：探测直播状态，写 `data/tmp/windows-agent-events.jsonl`
- `orchestrator`：把直播事件变成 session / recording job
- `recorder`：调用 ffmpeg 录制到 `data/raw/<session>/recording-source.mp4`
- `postprocess`：分段、字幕、导出、标题文案
- `recovery`：分发需要人工处理的恢复动作

## 安装

建议把项目放在本地 NTFS 目录，例如 `D:\code\auto-record-live`。不要放在 OneDrive 同步目录。

先安装基础依赖：

```powershell
winget install Python.Python.3.12
winget install OpenJS.NodeJS.LTS
winget install Gyan.FFmpeg
```

初始化项目：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[subtitles]"
npm install
npx playwright install chromium
```

查看命令：

```powershell
.\.venv\Scripts\python.exe -m arl.cli --help
```

## 配置

复制示例配置：

```powershell
Copy-Item .env.example .env
```

然后只填你实际需要的项。`.env` 已被 `.gitignore` 忽略，可以放直播间 URL、Cookie、SESSDATA。

常用项：

| 配置 | 说明 |
| --- | --- |
| `ARL_PLATFORMS` | `douyin`、`bilibili` 或 `douyin,bilibili` |
| `ARL_DOUYIN_ROOM_URL` / `ARL_STREAMER_NAME` | 单个抖音直播间 |
| `ARL_BILIBILI_ROOM_URL` / `ARL_BILIBILI_STREAMER_NAME` | 单个 B 站直播间 |
| `ARL_*_ROOM_URLS` / `ARL_*_STREAMER_NAMES` | 多直播间，英文逗号分隔 |
| `ARL_DOUYIN_COOKIE` | 抖音完整 Cookie header 值 |
| `ARL_BILIBILI_SESSDATA` | B 站 SESSDATA 值，不带 `SESSDATA=` 前缀 |
| `ARL_AGENT_POLL_INTERVAL_SECONDS` | 探测间隔；调大可降低压力 |
| `ARL_ORCHESTRATOR_POLL_INTERVAL_SECONDS` | 编排轮询间隔 |
| `ARL_RECORDER_MAX_CONCURRENT_JOBS` | 同时录制任务数；普通电脑建议 `1` |
| `ARL_DIRECT_STREAM_TIMEOUT_SECONDS` | 单次直链录制预算，单位秒 |
| `ARL_SUBTITLES_ENABLED` / `ARL_WHISPER_MODEL_SIZE` | 字幕开关与模型大小 |
| `ARL_EXPORT_ENABLE_FFMPEG` | 是否导出真实带字幕视频 |

`.env.example` 只列常用项。所有支持的高级环境变量仍在 [src/arl/config.py](src/arl/config.py) 中有默认值。

## 先看状态，再按编号录制

先查看 `.env` 里配置的这批直播间状态：

```powershell
.\.venv\Scripts\python.exe -m arl.cli live-status
```

`live-status` 会按配置顺序输出 `index=1`、`index=2` 这类编号，以及每个直播间是否在播。看到要录的编号后，直接执行下面的录制命令，不需要改 `.env`。

常用录制命令：

```powershell
# 录制第 1 个直播间
.\.venv\Scripts\python.exe -m arl.cli record-rooms --room-index 1

# 同时选择第 1、3 个直播间；最多同时跑 2 个 ffmpeg
.\.venv\Scripts\python.exe -m arl.cli record-rooms --room-indices 1,3 --max-concurrent-jobs 2

# 自动录制当前所有在播直播间
.\.venv\Scripts\python.exe -m arl.cli record-rooms --all-live
```

需要单独检查 Cookie 是否有效时运行：

```powershell
.\.venv\Scripts\python.exe -m arl.cli cookie-health
```

`record-rooms` 只会录本次选择的编号，并使用 `data/tmp/selected-recordings/...` 下的临时 agent/orchestrator 状态文件，避免把未选择直播间的旧 queued job 一起录掉。命令默认强制开启真实 ffmpeg 录制；只想测试流程时加 `--placeholder`。

如果常驻 `windows-supervisor.ps1` / `windows-recorder-loop.ps1` 正在运行，它们仍会按 `.env` 的全量直播间工作。手动选择录制时，建议先停掉常驻 recorder/supervisor，再执行 `record-rooms`。

跑一轮最小链路：

```powershell
.\.venv\Scripts\python.exe -m arl.cli windows-agent --once
.\.venv\Scripts\python.exe -m arl.cli orchestrator --once
.\.venv\Scripts\python.exe -m arl.cli recorder
.\.venv\Scripts\python.exe -m arl.cli status
```

注意：手动单次 `arl recorder` 默认遵守 `.env` 里的 `ARL_RECORDING_ENABLE_FFMPEG`。要真实录制，可以在当前 PowerShell 临时打开：

```powershell
$env:ARL_RECORDING_ENABLE_FFMPEG = "1"
.\.venv\Scripts\python.exe -m arl.cli recorder
```

## 常驻运行

最省事的方式是启动 supervisor。它会隐藏启动五个 launcher，并在子进程退出时重启：

```powershell
.\scripts\windows-supervisor.ps1
```

日志在：

```text
data/tmp/launcher-logs/
```

如果你想看每个窗口的实时输出，也可以打开五个 PowerShell 分别运行：

```powershell
.\scripts\windows-agent-loop.ps1
.\scripts\windows-orchestrator-loop.ps1
.\scripts\windows-recorder-loop.ps1
.\scripts\windows-postprocess-loop.ps1
.\scripts\windows-recovery-loop.ps1
```

Windows launcher 会自动准备 `.venv` 并安装 `.[subtitles]`。`ARL_WIN_INSTALL_MODE=if-missing` 时，依赖准备好后不会每次重装。

需要开机/登录自动启动时：

```powershell
.\scripts\windows-autostart.ps1 -Action Install
.\scripts\windows-autostart.ps1 -Action Status
.\scripts\windows-autostart.ps1 -Action Uninstall
```

## 自动编排剪辑（后处理）

常驻模式会由 `windows-postprocess-loop.ps1` 定期跑自动编排剪辑。录制完成后想手动触发一轮，执行：

```powershell
.\.venv\Scripts\python.exe -m arl.cli postprocess --once
```

这条命令会按下面顺序处理当前还没处理过的录制资产：

```text
stage-hints-semantic -> segmenter -> subtitles -> exporter -> copywriter
```

`stage-hints-semantic` 只会在已有字幕或手工信号能识别出 `in_game` 时生成语义切点。没有可用信号时，默认不会再按 `ARL_RECORDING_SEGMENT_MINUTES` 硬切 30 分钟；如果确实想保留固定周期模板切片，需要显式设置：

```powershell
$env:ARL_SEGMENTER_TEMPLATE_FALLBACK_ENABLED = "1"
```

也就是自动生成阶段提示、切分对局、生成字幕、导出每局视频，并生成标题文案。

也可以单独执行其中某一步：

```powershell
.\.venv\Scripts\python.exe -m arl.cli stage-hints-semantic
.\.venv\Scripts\python.exe -m arl.cli segmenter
.\.venv\Scripts\python.exe -m arl.cli subtitles
.\.venv\Scripts\python.exe -m arl.cli exporter
.\.venv\Scripts\python.exe -m arl.cli copywriter
```

如果 `postprocess --once` 每个阶段都显示 `processed=0`，但 `data/raw/session-*/recording-source.mp4` 里确实有录制文件，先修复录制资产清单，再重新后处理：

```powershell
.\.venv\Scripts\python.exe -m arl.cli repair-recording-assets
.\.venv\Scripts\python.exe -m arl.cli postprocess --once
```

如果只想重新导出某一次录制，或者之前已经生成过 `.txt` 回退文件，需要强制重导出：

```powershell
.\.venv\Scripts\python.exe -m arl.cli exporter --session-id session-20260606101149-9fe32958 --force-reprocess
```

如果某个 session 已经生成了错误边界、占位字幕或错误导出，先清掉该 session 的后处理生成物，再重新跑一轮。这个命令不会删除 `data/raw/.../recording-source.mp4`：

```powershell
.\.venv\Scripts\python.exe -m arl.cli postprocess-reset --session-id session-20260608095022-03694add
.\.venv\Scripts\python.exe -m arl.cli postprocess --once
```

查看整体健康状态：

```powershell
.\.venv\Scripts\python.exe -m arl.cli status
```

判断后处理完成时，看 `status` 输出里的 `postprocess.missing_subtitles`、`missing_exports`、`missing_copies`、`unregistered_recordings` 是否都是 `0`。导出视频会写到 `data/exports/<platform>/`，例如 B 站录制在 `data/exports/bilibili/`。

## 降低电脑压力

优先调这几项：

```env
ARL_AGENT_POLL_INTERVAL_SECONDS=90
ARL_ORCHESTRATOR_POLL_INTERVAL_SECONDS=10
ARL_RECORDER_MAX_CONCURRENT_JOBS=1
ARL_SUBTITLES_ENABLED=0
ARL_EXPORT_ENABLE_FFMPEG=0
```

字幕最吃算力。需要字幕但想轻一点，把模型调小：

```env
ARL_WHISPER_MODEL_SIZE=tiny
```

多直播间同时开播时，`ARL_RECORDER_MAX_CONCURRENT_JOBS=1` 会让 recorder 一次只跑一个 ffmpeg 录制任务，压力最低。

## 排查

看当前状态：

```powershell
.\.venv\Scripts\python.exe -m arl.cli status
```

检查 Cookie / SESSDATA 是否有效：

```powershell
.\.venv\Scripts\python.exe -m arl.cli cookie-health
```

B 站 403 不一定是 SESSDATA 过期，也可能只是短时效直播流 URL 过期。处理顺序：

1. 先跑 `cookie-health`
2. 如果看到 `cookie_expired_for_bilibili`，更换 `ARL_BILIBILI_SESSDATA`
3. 如果只看到 `stream_url_expired_for_bilibili`，通常等下一轮 probe 刷新 URL 即可

常用日志查询：

```powershell
Select-String "cookie_expired_for_|stream_url_expired_for_" data/tmp/orchestrator-events.jsonl
Select-String "ffmpeg_record_failed" data/tmp/recorder-events.jsonl
Select-String "subtitle_fallback_placeholder" data/tmp/subtitles-events.jsonl
Select-String "ffmpeg_export_failed" data/tmp/exporter-events.jsonl
```

ffmpeg 完整 stderr 会落在：

```text
data/tmp/recorder-stderr/
data/tmp/exporter-stderr/
```

长时间运行后清理日志：

```powershell
.\.venv\Scripts\python.exe -m arl.cli maintenance --once
```

无人值守冒烟测试：

```powershell
.\.venv\Scripts\python.exe -m arl.cli soak --cycles 1 --interval-seconds 0 --skip-recorder --skip-postprocess
```
