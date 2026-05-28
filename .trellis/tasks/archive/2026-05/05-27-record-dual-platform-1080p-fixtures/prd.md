# Record dual-platform 1080p fixtures (douyin WEI + bilibili 橘子怪丶)

## Goal

Use the existing recorder pipeline to capture one ≥1080p `.mp4` fixture per
platform from two operator-supplied live rooms, dropped into `data/raw/<session_id>/recording-source.mp4`
so they can feed follow-up research/test work (semantic-stage detection,
exporter alignment, subtitle hardening, etc.).

> 2026-05-28 update: bilibili target switched from the original
> `挖机牧魂人 / live.bilibili.com/6963590` to the operator's currently-LIVE
> room `橘子怪丶 / live.bilibili.com/12629424`. Original room is no longer
> live; new room is what `.env` was already pointing at and what the existing
> in-repo state files reference.

No code changes. This is operational use of `arl windows-agent` /
`arl orchestrator` / `arl recorder` against two concurrent rooms with the
default 1080p quality gates left at their strict settings (douyin
`min_quality_tier=uhd`, bilibili `min_stream_qn=400`).

## User Value

Operator is supplying live URLs on demand for fixture capture, and has stated
that **below-1080p / low-bitrate output is unusable**. Today the recorder
pipeline supports both platforms but multi-platform fixture runs aren't
documented as a workflow, and the bilibili anonymous tier cap (qn=250 / 720p)
will silently fail the quality gate at probe time unless SESSDATA is supplied.
This task makes the steps + cookie requirements explicit and produces the
actual `.mp4`s.

## Confirmed Facts

- Douyin room `https://live.douyin.com/742070406673` (WEI / 乱斗阿伟) was LIVE
  anonymously on 2026-05-27 with a **signed `_uhd` (1080p) m3u8 leaf URL** in
  the page DOM
  (`pull-x2-t5-hls.douyincdn.com/.../stream-..._uhd/index.m3u8?expire=...&sign=...`).
  Probe `state=LIVE`, `source_type=DIRECT_STREAM`, `reason=stream_url_detected`.
  No `ARL_DOUYIN_COOKIE` required at that moment. Operator re-confirmed LIVE on
  2026-05-28.
- Bilibili room `https://live.bilibili.com/12629424` (橘子怪丶) was LIVE on
  2026-05-28 (operator-confirmed). Anonymous playinfo only exposes `qn=250`
  (720p) for bilibili rooms in general; `ARL_BILIBILI_SESSDATA` is required to
  unlock qn>=400 (1080P 蓝光). `.env` already has the cookie set; the
  in-repo `windows-agent-state.json` shows `state=live` with a `bluray` flv
  stream URL for this room from 2026-05-25, confirming the cookie was working
  recently.
- Multi-platform config is `ARL_PLATFORMS=douyin,bilibili` per
  `src/arl/config.py:222-253`. Without that env, only legacy douyin loads.
- Single shared agent loop + orchestrator + recorder process both platforms;
  output paths land in `data/raw/<session_id>/recording-source.mp4` per
  `src/arl/recorder/service.py` (one session per recording job).

## Requirements

- **R1**: Configure env to point at both rooms simultaneously:
  - `ARL_PLATFORMS=douyin,bilibili`
  - `ARL_DOUYIN_ROOM_URL=https://live.douyin.com/742070406673`
  - `ARL_STREAMER_NAME=WEI`（or `乱斗阿伟`; either is acceptable for fixture
    naming, no business logic depends on the exact string）
  - `ARL_BILIBILI_ROOM_URL=https://live.bilibili.com/12629424`
  - `ARL_BILIBILI_STREAMER_NAME=橘子怪丶`
  - `ARL_RECORDING_ENABLE_FFMPEG=1` (required to actually invoke ffmpeg —
    default is `0` and only emits intent events)
  - `ARL_BILIBILI_SESSDATA=<operator-supplied>` (operator will provide; without
    this, bilibili probe stays OFFLINE due to the qn=250 cap)
  - Keep `ARL_DOUYIN_MIN_QUALITY_TIER=uhd` and `ARL_BILIBILI_MIN_STREAM_QN=400`
    at defaults — they enforce the operator's "1080p floor" gate.
- **R2**: Run all three loops concurrently in separate PowerShell windows:
  `.\scripts\windows-agent-loop.ps1`, `.\scripts\windows-orchestrator-loop.ps1`,
  `.\scripts\windows-recorder-loop.ps1`. Let them run long enough to produce
  a usable fixture per platform (operator-decided duration; recommended ≥30
  min so one segment closes via `ARL_RECORDING_SEGMENT_MINUTES`).
- **R3**: Verify that each platform produces at least one
  `data/raw/<session_id>/recording-source.mp4` whose ffprobe confirms
  resolution ≥1920×1080 and a reasonable bitrate (recorder gate already
  enforces this on the URL side; ffprobe is the post-record sanity check).
- **R4**: Capture session ids + paths in the task journal/notes for the two
  resulting fixtures so follow-up research can cite them.

## Acceptance Criteria

- [ ] `data/raw/<douyin-session-id>/recording-source.mp4` exists; `ffprobe`
      reports streams with width ≥1920 and height ≥1080.
- [ ] `data/raw/<bilibili-session-id>/recording-source.mp4` exists; `ffprobe`
      reports streams with width ≥1920 and height ≥1080.
- [ ] `data/tmp/recorder-events.jsonl` shows a `recording_completed`-class
      event (or equivalent terminal success event) for each session, with no
      `quality_below_*` rejection at probe time after cookies are in place.
- [ ] Task notes/journal capture: the two session ids, file paths,
      duration, and any cookie/gate adjustments made during the run.

## Out of Scope

- Any code/recorder changes. If a quality-gate bug surfaces, file a separate
  task rather than patching here.
- Cookie procurement automation. Operator provides `ARL_BILIBILI_SESSDATA`
  (and `ARL_DOUYIN_COOKIE` if douyin tier drops mid-run) out of band per the
  README "Cookie 配置与失效审计" section.
- Long-form fixture curation (labeling, ground-truth, etc.) — that belongs in
  the downstream consuming task.
- Cookie health gate work — tracked separately as
  `05-14-cookie-health-gate-oncall-workflow`.

## Open Questions

- None blocking. SESSDATA needed before bilibili recording can succeed; that's
  an operator hand-off, not a design question.

## Notes

- Lightweight task: PRD-only is sufficient; no `design.md` / `implement.md`.
- Recorder pipeline already supports multi-platform via `ARL_PLATFORMS` —
  this task is operational, not a new feature.
- Probe data was collected against the two URLs at task creation time on
  2026-05-27; if the streams go offline or change tier before execution, re-
  probe before launching the loops.
