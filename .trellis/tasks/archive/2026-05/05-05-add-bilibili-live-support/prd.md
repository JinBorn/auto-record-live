# 支持 B 站直播录制

## Goal

在现有「抖音单主播自动录制」MVP 的基础上，新增对 B 站（bilibili.com 直播间）的支持，让用户可以监控并录制 B 站主播的直播。最终是否「同时监控抖音 + B 站」还是「在抖音/B 站之间切换」由 MVP scope 决定（见 Open Questions）。

## What I already know

仓库现状（已通过 repo inspection 确认）：

- 项目入口：`src/arl/cli.py`，模块结构 `windows_agent/`、`orchestrator/`、`recorder/`、`segmenter/`、`subtitles/`、`exporter/`、`shared/`、`recovery/`。
- 配置：`src/arl/config.py` 中有 `DouyinSettings`（`room_url`、`streamer_name`、`persistent_profile_dir`、`poll_interval_seconds`、`playwright_script`、`use_playwright_probe` 等），通过 `.env` 加载（`ARL_DOUYIN_*` 前缀）。**`Settings.douyin` 是单实例字段，写死了"抖音"这一个平台**。
- 抖音探测：`src/arl/windows_agent/probe.py` → `DouyinRoomProbe`，类名/常量都写死抖音；流程是「Playwright 优先（`scripts/probe_douyin_room.mjs`），失败 fallback 到 httpx 抓 HTML」。
- Playwright 脚本 `scripts/probe_douyin_room.mjs`：用 `chromium.launchPersistentContext` 打开直播间，订阅 `request`/`response` 事件抓流地址，再用页面 marker（`"status":2` / `"live_status":2` / `"是 live":true` / `"直播中"` / `"暂未开播"`）判断在播状态；产出 JSON `{ok, state, sourceType, streamUrl, reason, pageTitle}`。
- 数据契约：`AgentSnapshot {state, streamer_name, room_url, source_type, stream_url, reason, detected_at}` —— 字段本身平台中立，没有写死抖音；`source_type` 取 `direct_stream` / `browser_capture`。
- 服务循环：`WindowsAgentService.__init__` 直接 `DouyinRoomProbe(settings)`，**单 probe 单平台**；事件写到 `data/tmp/windows-agent-events.jsonl`，没有 platform 字段。
- 下游 orchestrator 消费同一个 jsonl，`AgentEvent` 也没有 platform 字段。
- 当前没有任何 B 站相关代码、配置、URL 解析。

外部知识（需要研究确认）：

- B 站直播间 URL 形如 `https://live.bilibili.com/<room_id>`。
- B 站对外有官方接口（如 `api.live.bilibili.com/room/v1/Room/get_info`、`/xlive/web-room/v2/index/getRoomPlayInfo` 等）可直接拿在播状态和 m3u8/flv 流，**理论上不一定需要 Playwright**，但风控/cookie/UA 限制等需要研究。

## Assumptions (temporary)

- 用户主要诉求是「录《英雄联盟》直播」，B 站要支持的也是英雄联盟主播 → segmenter / subtitle / exporter 链路无平台耦合，**不需要改动**。
- 多主播 / 多直播间不在本任务范围（仍是「单主播，固定一个房间 URL」），除非 scope 决策另议。
- 现有的 Playwright 持久化 profile（用于抖音登录态）与 B 站的 profile **应当各自独立**，不能共用同一个目录。
- `SourceType.DIRECT_STREAM` 一旦拿到 m3u8/flv，下游 recorder（ffmpeg）已经是平台中立的，**不需要改动**。

## Decision (ADR-lite) — Full

**Context**: 抖音是写死的单平台，新增 B 站需要决定二者并存关系、B 站探测路线、向后兼容等。

**Decision**:

- **Scope**：通用化（platform 列表）—— 抽象 `PlatformProbe` 接口 + dict registry，未来可继续加平台。
- **B 站探测**：纯 API 路线（`api.live.bilibili.com` 状态 + 拉流），匿名访问；Playwright fallback 留接口不实现。
- **架构**：保守渐进 A —— `PlatformProbe` ABC + 显式 `PROBE_REGISTRY` dict + 单进程串行轮询；`AgentSnapshot` 加 `platform` 字段 + `stream_headers` 字段；orchestrator 按 (platform, room_url) 维护 session；recorder 透传 headers。
- **配置**：`.env` 加 `ARL_PLATFORMS=douyin,bilibili` 列表 + 每平台 `ARL_<PLATFORM>_*` 前缀；缺失时默认回退为 `douyin` 保持向后兼容。yaml 配置文件留给后续 PR（平台数 ≥ 3 时再升级）。
- **抖音向后兼容**：完全保留 `ARL_DOUYIN_*` 旧变量（方案 a），不做 deprecation warning。
- **B 站流 token 过期**：MVP 不做 probe ↔ recorder 反馈链路（方案 a），retry 失败 → emit `live_stopped` → 下一轮 probe 自然恢复。
- **PR 切分**：3 个 PR（PR1 抽接口 + 抖音适配；PR2 加 B 站 probe + recorder header；PR3 文档与回归）。

**Consequences**:

- 改动量约 10-12 文件（PR1 占大头，~7 文件；PR2 ~3 文件；PR3 ~2 文件）。
- 抖音 `.env` zero-config 升级（`ARL_PLATFORMS` 缺省时仍能跑）。
- recorder ffmpeg header 注入是新能力，需要在 unit test 中覆盖参数构造逻辑。

## Requirements (final)

### 功能需求

1. **PlatformProbe 抽象**：`src/arl/windows_agent/platform_probe.py` 定义 `PlatformProbe(ABC)` 含 `platform_name: ClassVar[str]`、`detect() -> AgentSnapshot`、`stream_headers() -> dict[str, str]`（默认空 dict）。
2. **Probe 注册表**：`src/arl/windows_agent/registry.py` 定义 `PROBE_REGISTRY: dict[str, type[PlatformProbe]]` 与 `build_probes(platforms) -> list[PlatformProbe]`。
3. **抖音适配**：`DouyinRoomProbe` 实现 `PlatformProbe`，行为不变；`platform_name = "douyin"`；`stream_headers()` 返回 `{}`。
4. **B 站 probe**：`src/arl/windows_agent/bilibili_probe.py` 新增 `BilibiliRoomProbe(PlatformProbe)`：
   - `platform_name = "bilibili"`
   - `detect()` 调 `api.live.bilibili.com/room/v1/Room/get_info?room_id=<id>` 拿状态；live → 调 `api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo` 拿 m3u8/flv URL
   - `live_status==1` → `LIVE`；`0` 与 `2`（轮播）→ `OFFLINE`
   - 从 `room_url` (`https://live.bilibili.com/<id>`) 解析 `room_id`
   - 真实浏览器 UA（与抖音 probe 一致字符串）
   - 异常时返回 `OFFLINE` + reason，不 raise（与抖音 probe 风格一致）
   - `stream_headers()` 返回 `{"Referer": "https://live.bilibili.com", "User-Agent": "<同 UA>"}`
5. **数据契约扩展**：`AgentSnapshot` 加 `platform: str` 字段（`Literal["douyin", "bilibili"]` 或纯 str），加 `stream_headers: dict[str, str] = {}` 字段；`AgentEvent` 通过嵌套 snapshot 间接带上 platform；`AgentStateFile` 把 `last_snapshot: AgentSnapshot | None` 改为 `last_snapshots: dict[str, AgentSnapshot]`（key = `<platform>:<room_url>`）。
6. **服务循环改造**：`WindowsAgentService.__init__` 改为 `self.probes = build_probes(settings.platforms)`；`run_once()` 遍历 probes，每个 probe 用 try/except 包裹（单平台异常不阻断另一个）。
7. **配置层重构**：`Settings.platforms: list[PlatformSettings]`；`PlatformSettings` 含 `type` / `room_url` / `streamer_name` / `use_playwright` / 平台扩展字段；`load_settings()` 解析 `ARL_PLATFORMS` 列表 + 每平台 `ARL_<PLATFORM>_*` 变量；缺省 `ARL_PLATFORMS` 时回退为 `["douyin"]` 兼容旧 .env。保留 `Settings.douyin` 作为 deprecated proxy（指向 platforms 中的 douyin 项）以避免下游访问点全改。
8. **Orchestrator 适配**：session key 从 `streamer_name` 改为 `(platform, room_url)`；状态文件 schema 更新；audit log 字段不变（已经通过 snapshot 自然带 platform）。
9. **Recorder 适配**：从 snapshot 读 `stream_headers`，构造 ffmpeg `-headers "K1: V1\r\nK2: V2"` 与 `-user_agent` 参数；抖音空 dict 时命令完全不变。
10. **文档更新**：`README.md` 加 B 站接入说明；`.env.example` 加 `ARL_PLATFORMS=douyin,bilibili` + B 站变量范例；`.trellis/spec/backend/orchestration-contracts.md` 更新 AgentSnapshot/AgentEvent 字段说明。

### 非功能需求

- 抖音原有路径 100% 行为兼容（lint / type-check / tests 全绿）。
- B 站 probe 单次延迟 < 5s（poll 周期 30s 内，串行两个 probe 安全）。
- 错误隔离：B 站 probe 异常不影响抖音 probe，反之亦然。

## Acceptance Criteria (final)

- [ ] 配置 `ARL_PLATFORMS=douyin,bilibili` + 真实 B 站直播间 URL，能在直播开始 ≤ 35s 内（poll 周期 + probe 调用 < 5s）得到 `live_started` 事件，且事件含 `platform: "bilibili"`。
- [ ] B 站关播能产出 `live_stopped` 事件；网络异常 / API 错误 produce `OFFLINE + reason=<api_error_detail>`，不 raise。
- [ ] B 站 `live_status==2`（轮播）映射为 OFFLINE 且 `reason="carousel_playback"`。
- [ ] B 站 ffmpeg recorder 拉流命令含 `-headers "Referer: https://live.bilibili.com\r\nUser-Agent: ..."`（unit test 验证命令构造）。
- [ ] 抖音原有路径回归：仅设 `ARL_DOUYIN_*` 旧变量（不设 `ARL_PLATFORMS`）系统行为完全等同改造前。
- [ ] 单平台异常隔离：mock B 站 probe 抛异常，抖音 probe 仍正常 detect 并发事件。
- [ ] `pytest` / `ruff` / `mypy`（或现仓库 type-check 工具）全绿。
- [ ] `README.md` / `.env.example` / spec 文档更新完成。

## Implementation Plan (3 small PRs)

### PR1: 抽 PlatformProbe 接口 + 抖音适配（不破坏现有）

文件（~7）：
- `src/arl/windows_agent/platform_probe.py` 新增
- `src/arl/windows_agent/registry.py` 新增（仅含 douyin 项）
- `src/arl/windows_agent/probe.py` `DouyinRoomProbe` 实现 ABC
- `src/arl/windows_agent/models.py` `AgentSnapshot` 加 `platform: str = "douyin"` + `stream_headers: dict[str, str] = {}`
- `src/arl/windows_agent/state_store.py` `last_snapshots: dict[str, AgentSnapshot]` 改造
- `src/arl/windows_agent/service.py` probes list + 遍历
- `src/arl/config.py` `Settings.platforms` + `ARL_PLATFORMS` 默认回退
- `tests/` 抖音回归 + 新接口 unit test
- `.env.example` 加 `ARL_PLATFORMS=douyin` 范例（不强制要求设置）

验收：抖音回归 100% 通过；现有用户 .env 不动也能跑；type-check 全绿。

### PR2: 加 B 站 probe + recorder header 透传

文件（~3）：
- `src/arl/windows_agent/bilibili_probe.py` 新增
- `src/arl/windows_agent/registry.py` 注册 bilibili
- `src/arl/recorder/<相关文件>` ffmpeg header 注入
- `src/arl/orchestrator/<相关文件>` session key 改 (platform, room_url)（如未在 PR1 完成）
- `tests/` B 站 probe 状态/拉流解析、recorder header 命令构造、orchestrator 多平台 session

验收：用真实 B 站房间号跑 `python -m arl.cli windows-agent --once` 拿到 `live_started/stopped` 事件；recorder ffmpeg 命令含 Referer。

### PR3: 文档 + 完整回归

文件（~2）：
- `README.md` 加 B 站接入指南、API 说明、ffmpeg header 必要性
- `.env.example` 加 B 站完整范例
- `.trellis/spec/backend/orchestration-contracts.md` 更新字段说明
- 跑一遍真实抖音 + B 站双平台回归（手动），journal 记录

验收：文档清晰；双平台 manual smoke test 通过。

## Open Questions

> 一次只问一个；按优先级排。下面 2、3 项依赖研究结果，由 trellis-research 写入 `research/` 后再向用户提具体选项。

1. ~~[scope] 抖音和 B 站并存关系~~ → **已决策：通用化**（见上）。
2. ~~[preference, research-pending] B 站探测技术路线~~ → **研究已完成**，见 [`research/bilibili-live-detection.md`](research/bilibili-live-detection.md)。结论：**纯 API 路线（推荐 A）/ Playwright fallback 留接口不实现**。等用户确认。
3. ~~[preference, research-pending] 多平台 watcher 架构~~ → **研究已完成**，见 [`research/multi-platform-watcher-architecture.md`](research/multi-platform-watcher-architecture.md)。结论：**保守渐进方案 A**（PlatformProbe ABC + 显式 dict registry + 单进程串行 + 单 jsonl 加 platform 字段 + .env 加 `ARL_PLATFORMS` 列表）。等用户确认。
4. **[preference]** 抖音 .env 向后兼容策略：(a) 完全保留 `ARL_DOUYIN_*` 旧变量 + 新增 `ARL_PLATFORMS=douyin` 默认回退；(b) 强制迁移到新变量 + 写 deprecation warning；(c) 不兼容（直接破坏，要求用户改 .env）。研究偏向 (a)。
5. **[preference]** B 站流 ffmpeg recorder 失败时的恢复策略：研究指出 stream_url 含时效 token，过期后 retry 同 URL 必 403。MVP 选项：(a) 不做特殊处理，让 retry 失败 → emit live_stopped → 下一轮 probe 自然恢复；(b) 引入 probe → recorder 反馈 channel 让 recorder 失败时回调 probe 重新拿 URL（额外 4-6 文件）。研究推荐 (a)。
6. **[preference]** PR 切分：(a) 一个大 PR 全做完；(b) PR1 抽接口 + 抖音适配（不破坏现有），PR2 加 B 站 probe + recorder header 透传，PR3 文档与回归。研究倾向 (b)。

## Feasible Approaches (post-research)

### B 站探测路线

- **A 纯 API（推荐）**：`httpx.get(api.live.bilibili.com/...)` 拿状态 + 拉流，UA 设真实浏览器，匿名访问。改动小、稳定、资源低。
- **B 纯 Playwright**：复用 `probe_douyin_room.mjs` 模板写 B 站版。资源开销大、解决不存在的问题。
- **C 混合（API + Playwright fallback）**：A 的代码 + 失败降级 B。MVP 推荐 **A 的实现 + C 的接口预留**（写 try-except 让外层可挂下一种 probe，但本 PR 不实现 fallback）。

### 多平台 watcher 架构

- **A 保守渐进（推荐 MVP）**：PlatformProbe ABC + 显式 dict registry；.env 加 `ARL_PLATFORMS` + 每平台 `ARL_<PLATFORM>_*`；单进程串行轮询；AgentSnapshot 加 `platform` + `stream_headers`；orchestrator 按 (platform, room_url) 维护 session；recorder 透传 headers。改动 10-12 文件。
- **B 彻底通用化 + yaml**：A + 引入 PyYAML + `config/platforms.yaml`。第一个 PR 体积大、回归风险高。
- **C A + yaml future hook**：A 实施 + yaml loader 写但不文档化。维护两套配置加载逻辑，不必要的复杂度。

## Requirements (evolving — 见 Requirements (final)，本节保留作 brainstorm 历史轨迹)

- 用户能配置一个 B 站直播间，系统按现有间隔轮询其状态。
- 在 B 站开播 / 关播时，emit 与抖音一致的 `AgentEvent`（`live_started` / `live_stopped`）。
- 当能拿到 m3u8/flv 直链时走 `direct_stream`，否则保留 `browser_capture` fallback 路径（如果可行）。
- 复用下游 orchestrator / recorder / segmenter / subtitle / exporter 链路，不破坏抖音现有行为。

## Acceptance Criteria (evolving — 见 Acceptance Criteria (final))

- [ ] 配置一个真实的 B 站直播间 URL，能在直播开始 N 秒内（≤ poll 周期 + 一个 probe 调用时长）得到 `live_started` 事件。
- [ ] 能在关播 / 网络异常 / 风控状态下产出 `offline` 事件（与抖音一致的语义），不会卡死 watcher 循环。
- [ ] 单元 / 集成测试覆盖：B 站状态解析、流地址解析、scope 决策路径下的 watcher 行为。
- [ ] 抖音原有路径回归通过（不影响现有用户）。
- [ ] 文档：`README.md` / `.env.example` 更新，告诉用户如何接入 B 站。

## Definition of Done (team quality bar)

- 单元 + 集成测试新增 / 更新，lint + type-check + CI 全绿。
- 文档更新（README / `.env.example` / 必要的 spec）。
- 抖音回归用例通过；B 站新增用例通过。
- 风险考虑：B 站接口风控、UA / cookie / referer、Playwright profile 隔离。

## Out of Scope (explicit)

- 多主播 / 多直播间监控（同一平台内多个房间），除非 scope 决策另议。
- B 站登录态弹幕 / 礼物 / 大航海等特性。
- 移植 segmenter / subtitle / exporter 的语义切分逻辑（B 站和抖音都是英雄联盟直播，链路复用）。

## Technical Notes

待研究项（将由 `trellis-research` 子代理写入 `research/`）：

- B 站直播间状态 / 流地址的官方接口（endpoint、参数、是否需要登录、UA / cookie / referer / 风控）
- B 站流地址在 ffmpeg 拉取时是否需要额外 header（referer/UA）
- 多平台 watcher 架构在类似项目中的常见做法

参考已存在的代码模式：

- `src/arl/config.py` `DouyinSettings` —— 平台配置块的模式
- `src/arl/windows_agent/probe.py` `DouyinRoomProbe` —— probe 接口（detect → AgentSnapshot）
- `src/arl/windows_agent/service.py` `WindowsAgentService` —— 单 probe → 多 probe 的扩展点
- `scripts/probe_douyin_room.mjs` —— Playwright 模式的实现模板（如选 Playwright 路线）

## Research References

- [`research/bilibili-live-detection.md`](research/bilibili-live-detection.md) — B 站直播探测：anonymous API 完全可用、不需 WBI/SESSDATA、ffmpeg 强制要 `Referer: https://live.bilibili.com`。推荐**纯 API**路线。
- [`research/multi-platform-watcher-architecture.md`](research/multi-platform-watcher-architecture.md) — 多平台 watcher 架构：推荐 **PlatformProbe ABC + 显式 dict registry + 单进程串行**，约 10-12 文件改动。

