# Cookie/SESSDATA injection for 1080p streams (PR6)

## Goal

匿名访问让 B 站和抖音的录制都被卡在 720p（最近真实联调测出 6.18 Mbps 720p60 是抖音上限；B 站 API `accept_qn=[10000, 400, 250]` 但匿名调用永远只回 qn=250）。本任务给两个 probe 加 cookie 注入：用户在 `.env` 里贴 SESSDATA / 抖音 cookie 字符串，probe 把它带进 HTTP 请求 + 写进 `stream_headers["Cookie"]`，让 recorder 的 `_build_ffmpeg_header_args`（`src/arl/recorder/service.py:471-484`，**已经支持任意 header 透传**）自动喂给 ffmpeg。预期效果：B 站拿到 qn=400 / 1080P 蓝光（部分账号能到 qn=10000 原画）；抖音页面里的 `_uhd` / `_origin` 签名 leaf URL 进入候选池。

PR 拆分（用户已确认 2026-05-08）：分两个子 PR 推进 —— **PR6.A 先做 B 站 SESSDATA**（最小可验证：拿到 1080P 蓝光），**PR6.B 后做抖音 cookie**（含 Playwright 子进程透传 + addCookies 兜底）。本 prd 覆盖整体方向，jsonl 与 implement 阶段先聚焦 PR6.A。

## Requirements

### MVP（PR6.A B 站）

1. `BilibiliSettings.sessdata: str = ""` 字段（`src/arl/config.py:54-58`）
2. `_load_bilibili_settings` 读 `ARL_BILIBILI_SESSDATA` 环境变量（`src/arl/config.py:182-185`）
3. `BilibiliRoomProbe._fetch_json` 在 sessdata 非空时把 `Cookie: SESSDATA=<value>` 加进请求 headers（`src/arl/windows_agent/bilibili_probe.py:201-227`）
4. `BilibiliRoomProbe.stream_headers()` 在 sessdata 非空时输出加 `Cookie` 字段（`src/arl/windows_agent/bilibili_probe.py:46-47`）
5. `.env.example` 新增 `ARL_BILIBILI_SESSDATA=` 注释 + cookie 来源指南
6. 单测 ≥3 条：sessdata 注入到 `_fetch_json`、Cookie 出现在 stream_headers、空 sessdata 完全等价于 PR5 行为
7. 真实联调（手动）：贴一份真实 SESSDATA 跑全链路，验证 `accept_qn` 里 qn≥400 的 codec 被命中、ffprobe 显示 1920×1080

### MVP（PR6.B 抖音）—— 紧随其后

1. `DouyinSettings.cookie: str = ""` 字段
2. `_load_douyin_settings` 读 `ARL_DOUYIN_COOKIE`
3. `DouyinRoomProbe._probe_with_playwright` 给子进程命令多传 `--cookie <value>`
4. `DouyinRoomProbe.detect()` 的 http fallback 给 `httpx.get` headers 加 `Cookie`
5. `scripts/probe_douyin_room.mjs` 解析 `--cookie`，`launchPersistentContext` 后 `context.addCookies(...)` 兜底
6. `.env.example` 新增 `ARL_DOUYIN_COOKIE=`
7. 单测 ≥3 条 + .mjs 单测 ≥1 条
8. 真实联调（手动）：贴一份登录态 cookie，验证 `_uhd` / `_origin` 签名 URL 进入候选并被 score 选中、ffprobe 显示 1080P

## Acceptance Criteria

- [ ] `ARL_BILIBILI_SESSDATA=...` 配上后，`BilibiliRoomProbe.detect()` 返回的 snapshot.stream_headers 包含 `Cookie: SESSDATA=...`
- [ ] `ARL_DOUYIN_COOKIE=...` 配上后，DouyinRoomProbe 的 http fallback 和 Playwright 子进程都注入了 cookie
- [ ] sessdata / cookie 为空字符串时，PR4/PR5 已通过的 198 个 pytest + 10 个 node --test 全部不变（向后兼容）
- [ ] 至少 1 条真实联调把 B 站 mp4 拉到 1920×1080（ffprobe `width=1920 height=1080` 验证）
- [ ] 至少 1 条真实联调把抖音 mp4 拉到 1920×1080（同上）
- [ ] cookie 不出现在 git-tracked 文件、不出现在 commit message、不出现在 journal

## Definition of Done

- 单测覆盖 cookie 注入逻辑 + 空字符串向后兼容路径
- 真实联调记录到 journal Session 26 / 27（mp4 大小 + ffprobe 输出）
- `.env.example` 注释清晰，给运维一段"如何在 Chrome F12 → Application → Cookies 复制 SESSDATA"的指南
- `README.md` 加一段"如何配 cookie 拉 1080P"
- `_build_ffmpeg_header_args` 不改 —— 它已经把 Cookie 当普通 header 透传

## Technical Approach

所有改动**严格沿现有 `stream_headers` dict 透传管线走**，不引入新协议或新审计事件类型：

```
.env (ARL_BILIBILI_SESSDATA / ARL_DOUYIN_COOKIE)
   ↓ config.py loaders
BilibiliSettings.sessdata / DouyinSettings.cookie
   ↓ probe constructor
BilibiliRoomProbe / DouyinRoomProbe
   ↓ _fetch_json headers / Playwright subprocess args / httpx fallback headers
HTTP 请求带 Cookie → 服务端返回高 qn variant / 高清 leaf URL
   ↓ stream_headers() 输出加 Cookie 字段
AgentSnapshot.stream_headers
   ↓ orchestrator 透传到 SessionRecord / RecordingJobRecord
recording_jobs[i].stream_headers
   ↓ recorder._build_ffmpeg_header_args（零改动）
ffmpeg -headers "Cookie: SESSDATA=..." -i <signed-url>
```

`_build_ffmpeg_header_args` 已经在 PR5 阶段就证明能透传任意 header（B 站的 Referer + User-Agent 走的就是这条路），所以 recorder / orchestrator 层都不需要动 —— 这是 MVP 最大的优雅点。

## Decision (ADR-lite)

**Context**: 720p 是匿名访问的硬上限，需要 cookie 解锁；同时不能把 cookie 持久化到任何 git-tracked 位置；需要避免 Chrome 数据库解密 / OS 跨平台兼容性的麻烦。

**Decision**: cookie 来源 = 用户手动贴到 `.env`（Chrome F12 → Cookies → 复制 SESSDATA）。probe 注入到 HTTP header + `stream_headers` dict，recorder 不改。先做 B 站（PR6.A，最小可验证），再做抖音（PR6.B）。

**Consequences**: cookie 过期需用户手动更新；不支持自动续期；好处是实现极简（< 20 行 Python 改动 / PR）、零新依赖、零 recorder 风险面。

## Out of Scope

- 自动从浏览器 Cookie 数据库读 SESSDATA（Chrome SQLite 解密 → 跨 OS 复杂、安全风险高）
- Cookie 自动续期 / 刷新（B 站需登录态 OAuth、抖音 sm cookie 算法）
- Cookie 加密存储（用户自己负责 `.env` 不进 git；`.gitignore` 已盖）
- 多账号 cookie 池
- `cookie_expired_for_<platform>` 审计事件类型（B 站 code=-101 已被现有 `api_error:code=-101` 路径捕获 → 映射 `http_4xx_non_retryable`，不需要新增事件类型）—— 后续 PR 再考虑
- 抖音持久化 profile 之外的更深层 cookie 写入逻辑（persistent profile 自己持久化，已经够）

## Technical Notes

### Cookie 过期 / 错误分类

- **B 站 SESSDATA 过期**：`api.live.bilibili.com` 返回 `code = -101`（"账号未登录"）。目前 `_fetch_json` 把任何非零 code 抛 `ValueError("api_error:code=-101:...")` → `BilibiliRoomProbe.detect()` 在 status_payload 拿不到时映射成 `OFFLINE` reason=`api_error:code=-101:账号未登录`。这条 reason 已被 `failure_contracts.classify_failure_reason` 识别为 `http_4xx_non_retryable`，recovery 路径会要求人工 —— **零新增**
- **抖音 cookie 过期**：页面没有刚性的"未登录"标记，但高清签名 URL 不出现就是事实信号 —— probe 层无法精确识别，会自然降级到低 tier 签名 URL（PR5 的 `_hd` 即 720p60）

### Cookie 不进 git 的保护

- `.env` 已在 `.gitignore`
- `.env.example` 用 `ARL_BILIBILI_SESSDATA=` 占位（值为空）
- 单测 fixture 用 `"fake-sessdata-for-testing"` 字符串
- journal 写真实联调结果时只写 ffprobe 输出 + mp4 大小，**不写 cookie 值**

### 与 PR4/PR5 的关系

- PR4 (per-platform supersede fix) 已 merged，本任务直接基于现有 `stream_headers` dict 透传管线
- PR5 (probe quality) 已 merged，本任务的预期收益就是把 PR5 的 720p 上限突破

### Research References

无 —— Phase E 实测期间已经验证过 B 站 API 的 qn 行为（accept_qn=[10000, 400, 250]、匿名降级 qn=250）。SESSDATA cookie 格式是公开知识（cookie 名 `SESSDATA`、值不透明、域 `.bilibili.com`）。抖音 cookie 同样在 Chrome F12 可见。**主动决定不开 trellis-research sub-agent**，省一次 sub-agent 500 风险（feedback memory 已记录该 panic 模式，本会话已遇到 1 次 Explore 500）。

如实施时发现 B 站单 SESSDATA 不够（部分账号需要 `bili_jct` / `DedeUserID` 配套），届时再 spawn research sub-agent 写 `research/bilibili-cookie-required-fields.md`。
