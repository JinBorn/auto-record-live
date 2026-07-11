# auto-record-live

本项目是在本地 Windows 机器上长期监测直播间、开播后自动录制、下播后自动切局/字幕/剪辑/导出的工具。当前主要支持抖音和 B 站直播间。

核心链路：

```text
windows-agent -> orchestrator -> recorder -> postprocess -> recovery
```

| 阶段 | 作用 | 主要产物 |
| --- | --- | --- |
| `windows-agent` | 定时探测直播间是否开播，并写入直播事件 | `data/tmp/windows-agent-events.jsonl` |
| `orchestrator` | 把开播/下播事件整理成 session 和录制任务 | `data/tmp/orchestrator-state.json` |
| `recorder` | 调用 ffmpeg 录制直播流 | `data/raw/<session>/recording-source.mp4` |
| `postprocess` | 自动分段、字幕、精彩剪辑、导出、标题文案 | `data/processed/`、`data/exports/` |
| `recovery` | 分发需要人工处理的恢复动作 | `data/tmp/recovery-*` |

## 快速开始：长期自动监测和录制

最省心的无人值守方式是配置 `.env`，然后启动 supervisor：

```powershell
Copy-Item .env.example .env
notepad .env

.\scripts\windows-supervisor.ps1
```

`windows-supervisor.ps1` 会隐藏启动并守护五个后台循环：agent、orchestrator、recorder、postprocess、recovery。子进程退出后会自动重启，日志写到：

```text
data/tmp/launcher-logs/
```

如果想让它在 Windows 登录后自动启动：

```powershell
.\scripts\windows-autostart.ps1 -Action Install
.\scripts\windows-autostart.ps1 -Action Status
```

取消自启：

```powershell
.\scripts\windows-autostart.ps1 -Action Uninstall
```

长期录制建议在 `.env` 里至少确认这些项：

```env
ARL_PLATFORMS=douyin
ARL_DOUYIN_ROOM_URL=https://live.douyin.com/<room_id>
ARL_STREAMER_NAME=<streamer_name>

ARL_AGENT_POLL_INTERVAL_SECONDS=90
ARL_RECORDER_MAX_CONCURRENT_JOBS=1
ARL_RECORDING_ENABLE_FFMPEG=1
ARL_DIRECT_STREAM_TIMEOUT_SECONDS=7200

ARL_SUBTITLES_ENABLED=1
ARL_EXPORT_ENABLE_FFMPEG=1
ARL_POSTPROCESS_PRESET=publish
ARL_EDIT_ZOOM_MODE=closeup
ARL_EDIT_ZOOM_MAX_SEGMENTS=3
ARL_EDIT_BGM_LIBRARY_PATH=data/bgm/library.json
```

说明：

- `windows-recorder-loop.ps1` 默认会把真实 ffmpeg 录制打开；但在 `.env` 显式写 `ARL_RECORDING_ENABLE_FFMPEG=1` 更直观。
- 普通电脑建议先把 `ARL_RECORDER_MAX_CONCURRENT_JOBS` 保持为 `1`，多直播间同时开播时再按机器能力调整。
- `ARL_DIRECT_STREAM_TIMEOUT_SECONDS` 是单次直链录制预算。长期录制应设置得比预期直播时长更长，例如 `7200` 表示约 2 小时。
- `ARL_POSTPROCESS_PRESET=publish` 会启用发布版剪辑预设，包括浓缩剪辑、ASS 字幕、缩放、较低音量 BGM/SFX、响度处理和更适合发布的导出参数。

## 安装

建议把项目放在本地 NTFS 目录，例如 `D:\code\auto-record-live`。不要放在 OneDrive 同步目录。

安装基础依赖：

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

Windows launcher 也会自动准备 `.venv` 和 Python 依赖。默认 `ARL_WIN_INSTALL_MODE=if-missing`，依赖安装成功后会写 `.venv\.deps-ready`，之后不会每次重复安装。

## 配置直播间

复制配置模板：

```powershell
Copy-Item .env.example .env
```

常用配置：

| 配置 | 说明 |
| --- | --- |
| `ARL_PLATFORMS` | `douyin`、`bilibili` 或 `douyin,bilibili` |
| `ARL_DOUYIN_ROOM_URL` / `ARL_STREAMER_NAME` | 单个抖音直播间 |
| `ARL_BILIBILI_ROOM_URL` / `ARL_BILIBILI_STREAMER_NAME` | 单个 B 站直播间 |
| `ARL_DOUYIN_ROOM_URLS` / `ARL_DOUYIN_STREAMER_NAMES` | 多个抖音直播间，英文逗号分隔 |
| `ARL_BILIBILI_ROOM_URLS` / `ARL_BILIBILI_STREAMER_NAMES` | 多个 B 站直播间，英文逗号分隔 |
| `ARL_DOUYIN_COOKIE` | 抖音完整 `Cookie` header 值 |
| `ARL_BILIBILI_SESSDATA` | B 站 `SESSDATA` 值，不带 `SESSDATA=` 前缀 |
| `ARL_COOKIE_HEALTH_GATE` | `warning`、`fatal` 或 `skip`，控制 launcher 启动时的 Cookie 健康检查 |

多直播间示例：

```env
ARL_PLATFORMS=douyin,bilibili

ARL_DOUYIN_ROOM_URLS=https://live.douyin.com/111,https://live.douyin.com/222
ARL_DOUYIN_STREAMER_NAMES=douyin-a,douyin-b

ARL_BILIBILI_ROOM_URLS=https://live.bilibili.com/333,https://live.bilibili.com/444
ARL_BILIBILI_STREAMER_NAMES=bili-a,bili-b
```

`.env` 已被 `.gitignore` 忽略，可以放直播间 URL、Cookie、SESSDATA 等本地私密信息。全部高级环境变量的默认值在 `src/arl/config.py`。

## 运行方式

### 方式一：后台无人值守

```powershell
.\scripts\windows-supervisor.ps1
```

查看后台日志：

```powershell
Get-Content data/tmp/launcher-logs/agent.out.log -Tail 100 -Wait
Get-Content data/tmp/launcher-logs/recorder.out.log -Tail 100 -Wait
Get-Content data/tmp/launcher-logs/postprocess.out.log -Tail 100 -Wait
```

### 方式二：分窗口观察

如果想看每个环节的实时输出，可以开五个 PowerShell 窗口分别运行：

```powershell
.\scripts\windows-agent-loop.ps1
.\scripts\windows-orchestrator-loop.ps1
.\scripts\windows-recorder-loop.ps1
.\scripts\windows-postprocess-loop.ps1
.\scripts\windows-recovery-loop.ps1
```

各循环间隔可通过 `.env` 调整：

```env
ARL_AGENT_POLL_INTERVAL_SECONDS=90
ARL_ORCHESTRATOR_POLL_INTERVAL_SECONDS=10
ARL_RECORDER_INTERVAL_SECONDS=5
ARL_POSTPROCESS_INTERVAL_SECONDS=30
ARL_RECOVERY_INTERVAL_SECONDS=30
```

### 方式三：手动选直播间录制

先查看 `.env` 中配置的直播间状态和编号：

```powershell
.\.venv\Scripts\python.exe -m arl.cli live-status
```

按编号录制：

```powershell
# 录制第 1 个直播间
.\.venv\Scripts\python.exe -m arl.cli record-rooms --room-index 1

# 录制第 1、3 个直播间，最多同时跑 2 个 ffmpeg
.\.venv\Scripts\python.exe -m arl.cli record-rooms --room-indices 1,3 --max-concurrent-jobs 2

# 录制当前所有正在直播的直播间
.\.venv\Scripts\python.exe -m arl.cli record-rooms --all-live
```

`record-rooms` 会使用独立的临时状态目录，避免把未选择直播间的旧 queued job 一起录掉。若常驻 supervisor 正在运行，手动选房录制前建议先停掉常驻 recorder/supervisor。

### 方式四：手动跑最小链路

```powershell
.\.venv\Scripts\python.exe -m arl.cli windows-agent --once
.\.venv\Scripts\python.exe -m arl.cli orchestrator --once

$env:ARL_RECORDING_ENABLE_FFMPEG = "1"
.\.venv\Scripts\python.exe -m arl.cli recorder

.\.venv\Scripts\python.exe -m arl.cli status
```

## 后处理和发布版导出

无人值守时，`windows-postprocess-loop.ps1` 会定期执行：

```powershell
.\.venv\Scripts\python.exe -m arl.cli postprocess --once
```

手动触发一次：

```powershell
# 常规后处理
.\.venv\Scripts\python.exe -m arl.cli postprocess --once

# 发布版剪辑预设
.\.venv\Scripts\python.exe -m arl.cli postprocess --once --publish

# 只处理指定 session
.\.venv\Scripts\python.exe -m arl.cli postprocess --once --session-id session-20260617073651-cf11bf9e
```

默认后处理顺序：

```text
stage-hints-semantic -> segmenter -> subtitles -> highlight-planner -> edit-planner -> exporter -> copywriter
```

输出位置：

```text
data/raw/<session>/                 原始录制
data/processed/<session>/           字幕、ASS、阶段中间产物
data/exports/<platform>/            最终导出视频和文案
data/tmp/*.jsonl                    各阶段状态、事件和资产索引
```

如果已经生成过错误结果，先重置该 session 的后处理产物，再重跑：

```powershell
.\.venv\Scripts\python.exe -m arl.cli postprocess-reset --session-id <session>
.\.venv\Scripts\python.exe -m arl.cli postprocess --once --session-id <session>
```

如果只想强制重跑某几个阶段：

```powershell
.\.venv\Scripts\python.exe -m arl.cli highlight-planner --session-id <session> --match-indices 2,3,4 --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli edit-planner --session-id <session> --match-indices 2,3,4 --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli exporter --session-id <session> --match-indices 2,3,4 --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli copywriter --session-id <session> --match-indices 2,3,4 --force-reprocess
```

Generate a repeatable quality report for existing exports without regenerating
video files:

```powershell
.\.venv\Scripts\python.exe -m arl.cli quality-report --session-id <session> --match-indices 2,3,4
.\.venv\Scripts\python.exe -m arl.cli quality-report --all-latest --strict
```

The command prints a Markdown summary and writes per-match artifacts to:

```text
data/processed/<session>/reports/match-NN-quality-report.md
data/processed/<session>/reports/match-NN-quality-report.json
```

`--strict` exits non-zero when configured quality thresholds emit warnings.

如果 `postprocess --once` 每个阶段都显示 `processed=0`，但 `data/raw/session-*/recording-source.mp4` 或 `recording-chunks.json` 确实存在，先修复录制资产索引：

```powershell
.\.venv\Scripts\python.exe -m arl.cli repair-recording-assets
.\.venv\Scripts\python.exe -m arl.cli postprocess --once
```

## 发布版剪辑规则

最近的发布版剪辑重点规则：

- 精彩片头不是必选项。只有明确识别为 `highlight_keyword` 的片段才会前置为 teaser；普通浓缩窗口不会硬凑片头。
- 如果存在 teaser，BGM 会从正片开始后再进入，不会盖在片头精彩片段上。
- BGM 会按视频内容、标签、片段原因和 session 信息从 `data/bgm/library.json` 里自动选择；同分候选会做稳定轮换，避免每个视频总是同两首。
- BGM 默认低音量，并在导出时对原视频声音做 sidechain ducking；验证音量时应按当前视频的语音密集片段抽样，不把某个视频的固定时间点当成全局规则。
- 浓缩剪辑会保护字幕语句边界，尽量避免主播话没说完就切走。
- 标题文案会在短弱标题缺少上下文时适当扩展，优先生成能独立说明视频内容的标题。

BGM 库示例路径：

Publish edit defaults in current builds:

- Teaser candidates come from `highlight_keyword`, `condensed_key_event`, and valid LLM teaser recommendations. If no candidate clears the text-signal threshold but a valid key-event window exists, the edit planner can use the top-scored fallback teaser.
- Teaser duration is dynamic: roughly 8-12% of planned edit duration, clamped by the configured min/max budget and still bounded by `ARL_EDIT_TEASER_MAX_TOTAL_SECONDS`.
- Publish preset inserts a short `black_card` transition between teaser and main content unless `ARL_EDIT_TRANSITION_MODE` is explicitly set. The card text uses the LLM `hook_line` when available, otherwise `ARL_EDIT_TRANSITION_TEXT`.
- If a teaser and transition exist, BGM starts at the first main segment, after both leading pieces. It does not cover the teaser or transition card.
- Long BGM plans can use laning -> momentum -> climax phases with overlapping crossfades when `data/bgm/library.json` has distinct matching tracks. Keep at least two usable tracks in each `phase` bucket for consistent three-phase behavior.
- Source-music protection suppresses BGM only over detected source-music spans plus padding. If detected source music covers most BGM-active output, the planner skips BGM for that match.
- Zoom defaults to short eased close-ups (`ARL_EDIT_ZOOM_MODE=closeup`) around KDA kills, chat bursts, then fallback high-signal segments. Set `ARL_EDIT_ZOOM_MODE=legacy` to restore whole-segment static punch-ins.
- Copywriter publishing renders ranked cover candidates as `cover-01.jpg`,
  `cover-02.jpg`, and `cover-03.jpg` when source frame scoring is available.
  `cover.jpg` remains the rank-1 default in the publish package, and
  `upload.txt` lists every candidate for manual selection.

```env
ARL_EDIT_BGM_LIBRARY_PATH=data/bgm/library.json
ARL_EDIT_SKIP_BGM_WHEN_SOURCE_HAS_MUSIC=1
ARL_EDIT_BGM_MULTI_PHASE_MIN_SECONDS=600
ARL_EDIT_BGM_SWITCH_MIN_GAP_SECONDS=60
ARL_EDIT_BGM_CROSSFADE_SECONDS=2
ARL_EDIT_BGM_SOURCE_MUSIC_PADDING_SECONDS=2
ARL_EDIT_BGM_SOURCE_MUSIC_MAJORITY_THRESHOLD=0.60
```

Zoom close-up controls:

```env
ARL_EDIT_ZOOM_MODE=closeup
ARL_EDIT_ZOOM_MAX_SEGMENTS=3
ARL_EDIT_ZOOM_CLOSEUP_SECONDS=6
ARL_EDIT_ZOOM_EASE_SECONDS=0.4
ARL_EDIT_ZOOM_MIN_INTERVAL_SECONDS=25
ARL_EDIT_ZOOM_CHAT_BURST_ENABLED=1
```

SFX library example:

```env
ARL_EDIT_SFX_LIBRARY_PATH=data/sfx/library.json
ARL_EDIT_SFX_TIMING_OFFSET_SECONDS=0
ARL_EDIT_SFX_MIN_INTERVAL_SECONDS=20
ARL_EDIT_SFX_MAX_HITS=6
ARL_EDIT_SFX_KDA_ALIGNMENT_ENABLED=1
```

Put sound files under `data/sfx/tracks/` and reference them from
`data/sfx/library.json`. Supported categories are `kill_coin`, `multi_kill`,
`transition_whoosh`, and `teaser_impact`. Kill SFX is aligned to `kda_change`
timestamps when available; KDA changes that increase deaths do not trigger coin
hits, even when kills also increase in the same observation interval.
If the manifest is missing, invalid, or lacks a usable kill category, the edit
planner falls back to the generated `coin.wav`.

如果你只想导出完整对局，不使用浓缩剪辑：

```env
ARL_HIGHLIGHT_PLANNER_ENABLED=0
ARL_EXPORT_USE_HIGHLIGHT_PLANS=0
ARL_EDIT_PLANNER_ENABLED=0
ARL_EXPORT_USE_EDIT_PLANS=0
```

## 长时间录制建议

长时间无人值守时优先保证稳定性：

```env
ARL_AGENT_POLL_INTERVAL_SECONDS=90
ARL_ORCHESTRATOR_POLL_INTERVAL_SECONDS=10
ARL_RECORDER_INTERVAL_SECONDS=5
ARL_RECORDER_MAX_CONCURRENT_JOBS=1
ARL_DIRECT_STREAM_TIMEOUT_SECONDS=7200
ARL_COOKIE_HEALTH_GATE=warning
```

如果直播很长，建议启用分片录制，便于异常恢复和后续处理：

```env
ARL_RECORDING_SEGMENTED_ENABLED=1
ARL_RECORDING_SEGMENTED_CHUNK_SECONDS=900
```

分片会写到：

```text
data/raw/<session>/recording-chunks.json
data/raw/<session>/chunks/recording-*.mp4
```

若遇到分片兼容问题，可以临时关闭：

```env
ARL_RECORDING_SEGMENTED_ENABLED=0
```

## 降低电脑压力

优先调整这些项：

```env
ARL_AGENT_POLL_INTERVAL_SECONDS=120
ARL_RECORDER_MAX_CONCURRENT_JOBS=1
ARL_SUBTITLES_ENABLED=0
ARL_EXPORT_ENABLE_FFMPEG=0
```

字幕最吃算力。需要字幕但想轻一点，可以调小模型：

```env
ARL_WHISPER_MODEL_SIZE=tiny
```

GTX 1650 4GB 比较稳的 ASR 设置：

```env
ARL_WHISPER_MODEL_SIZE=small
ARL_WHISPER_DEVICE=auto
ARL_WHISPER_COMPUTE_TYPE=auto
ARL_WHISPER_CUDA_COMPUTE_TYPE=int8_float16
ARL_WHISPER_CPU_COMPUTE_TYPE=int8
ARL_ASR_PREPROCESS_AUDIO=1
ARL_EXPORT_BURN_SUBTITLES=0
```

`ARL_ASR_PREPROCESS_AUDIO=1` 会先抽取并降噪音频再交给 Whisper。预处理失败时会回退到原媒体输入，不会直接中断后处理。

ASR quality controls:

```env
# Publish preset uses medium on CUDA/auto unless this is explicitly set.
# CPU-only runs keep small by default. large-v3 is opt-in and may fall back.
ARL_WHISPER_MODEL_SIZE=medium
ARL_WHISPER_BEAM_SIZE=5
ARL_WHISPER_VAD_FILTER=1
ARL_WHISPER_VAD_MIN_SILENCE_DURATION_MS=300
ARL_WHISPER_VAD_SPEECH_PAD_MS=250
ARL_ASR_OPENCC_ENABLED=1
ARL_ASR_INITIAL_PROMPT_PATH=data/asr/initial-prompt.txt
ARL_ASR_INITIAL_PROMPT_MAX_CHARS=1200
ARL_ASR_TERM_FIXES_PATH=data/asr/term-fixes.json
ARL_ASR_DISPLAY_SMOOTHING_ENABLED=1
ARL_ASR_DISPLAY_MIN_DURATION_SECONDS=3.5
ARL_ASR_DISPLAY_TRAILING_HOLD_SECONDS=1.25
ARL_ASR_DISPLAY_MAX_GAP_FILL_SECONDS=8
```

`data/asr/initial-prompt.txt` is a UTF-8 text file for LoL names, streamer
phrases, champion names, item names, and broadcast terms. It is passed to
faster-whisper as `initial_prompt` when present.

`data/asr/term-fixes.json` is an exact string replacement map applied after
OpenCC zh-Hans conversion and before writing SRT:

```json
{
  "wrong term": "correct term",
  "bad champion name": "correct champion name"
}
```

Display smoothing is applied after ASR text normalization and before writing
SRT. It keeps very short cues readable, carries text briefly after speech, and
fills only small gaps between neighboring cues. Set
`ARL_ASR_DISPLAY_SMOOTHING_ENABLED=0` to preserve raw ASR word timing.

## 状态检查和排查

查看整体状态：

```powershell
.\.venv\Scripts\python.exe -m arl.cli status
```

判断后处理是否完成时，重点看：

```text
postprocess.missing_subtitles
postprocess.missing_exports
postprocess.missing_copies
postprocess.unregistered_recordings
```

这些值应尽量为 `0`。最终视频通常在：

```text
data/exports/<platform>/
```

检查 Cookie / SESSDATA：

```powershell
.\.venv\Scripts\python.exe -m arl.cli cookie-health
```

B 站 403 不一定是 SESSDATA 过期，也可能只是短时效直播流 URL 过期。处理顺序：

1. 先跑 `cookie-health`
2. 如果看到 `cookie_expired_for_bilibili`，更新 `ARL_BILIBILI_SESSDATA`
3. 如果只看到 `stream_url_expired_for_bilibili`，通常等下一轮 probe 刷新 URL 即可

常用日志查询：

```powershell
Select-String "cookie_expired_for_|stream_url_expired_for_" data/tmp/orchestrator-events.jsonl
Select-String "ffmpeg_record_failed" data/tmp/recorder-events.jsonl
Select-String "subtitle_fallback_placeholder" data/tmp/subtitles-events.jsonl
Select-String "ffmpeg_export_failed" data/tmp/exporter-events.jsonl
```

ffmpeg 完整 stderr：

```text
data/tmp/recorder-stderr/
data/tmp/exporter-stderr/
```

如果对局边界明显错误，例如导出开头已经打到很高等级，或一个 session 只切出一局，重置并重跑该 session：

```powershell
.\.venv\Scripts\python.exe -m arl.cli postprocess-reset --session-id <session>
.\.venv\Scripts\python.exe -m arl.cli postprocess --once --session-id <session>
Get-Content data/tmp/match-boundaries.jsonl | Select-String "<session>"
```

关注边界记录里的 `is_complete`、`confidence`、`reason`、`started_at_seconds`、`ended_at_seconds`。

## 维护

长期运行后清理过大的 JSONL 和临时状态：

```powershell
.\.venv\Scripts\python.exe -m arl.cli maintenance --once
```

无人值守冒烟检查：

```powershell
.\.venv\Scripts\python.exe -m arl.cli soak --cycles 1 --interval-seconds 0 --skip-recorder --skip-postprocess
```

查看已解析配置：

```powershell
.\.venv\Scripts\python.exe -m arl.cli show-config
```
