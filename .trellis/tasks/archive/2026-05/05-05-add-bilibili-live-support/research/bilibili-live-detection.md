# B 站直播探测路线研究

> **元信息**：本文 inline 调研产物（trellis-research 子代理上游 500 不可用，由主代理基于 2 次 WebSearch + 仓库现状写成）。
> 关键事实精度高（来自 SocialSisterYi/bilibili-API-collect 与多个生产工具实践）；具体 endpoint 字段与 ffmpeg header 需要在实现时实测一次再固化。

## TL;DR

**推荐路线 = C 混合（API 优先 + 网页/Playwright fallback）**，理由：

- B 站 anonymous HTTP API 现状非常友好——`live_status` 与 `getRoomPlayInfo` 的拉流 endpoint **不需要 WBI 签名、不需要 SESSDATA cookie**，可以直接 `httpx.get` 拿到 JSON。复杂度远低于抖音的 Playwright 路径。
- Playwright fallback 留给两个边缘场景：(1) API 被风控限速时；(2) 主播开了"仅粉丝可见"等特殊房间——但这两种场景在 MVP 不必硬覆盖，可以延后实现。
- ffmpeg 拉 B 站流**强制需要** `Referer: https://live.bilibili.com` 和真实浏览器 UA，必须把这两个 header 透传到 recorder——这是与抖音录制路径**最大的差异**。
- 与抖音对比：抖音"页面解析 + Playwright"是因为抖音没有可用的 anonymous API；B 站完全不存在这个问题，**强行套抖音模式是过度工程**。

---

## 1. URL 形态与房间 ID

- 规范形式：`https://live.bilibili.com/<room_id>`
- `room_id` 既可能是 **真实房间号**（短号或长号），也可能是用户主页跳转后的"短号"。`api.live.bilibili.com/room/v1/Room/room_init` 接口会返回真实长 `room_id`，可用于做规范化。
- 短链 / 跳转 / 登录墙：anonymous 直接访问通常没问题；少数房间因主播设置或 18+ 限制需要登录——MVP 可以先不处理，把这种情况归为 `offline + reason=login_required`。

## 2. 官方 / 半官方 HTTP API 路线

> 以下接口均来自社区维护文档 [SocialSisterYi/bilibili-API-collect/docs/live/info.md](https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/live/info.md)。截至 2025-05 仍可 anonymous 访问。

### 2.1 状态查询

最简方案：

```
GET https://api.live.bilibili.com/room/v1/Room/get_info?room_id=<id>&from=room
```

返回 JSON 含 `data.live_status`（int）：

| 值 | 含义 |
|---|---|
| 0 | 未开播 |
| 1 | 直播中 |
| 2 | 轮播（非真实直播）|

> **关键映射决策**：在我们仓库里，`live_status == 1` → `LiveState.LIVE`；`0` 与 `2` 都映射为 `LiveState.OFFLINE`。`2` 是 B 站官方的"循环回放"模式，**不应该录**——这是 B 站特有的语义，要在 probe 层处理掉。

**备选状态接口**（功能/字段差异）：

- `GET /room/v1/Room/getRoomInfoOld` — 含 `roomStatus` / `roundStatus` / `live_status`（更全的状态切片，可用于交叉校验）
- `GET /room/v1/Room/room_init` — 轻量，含 `live_status` + `live_time`（开播时间）+ `short_id`/`uid` 用于规范化
- `GET /xlive/web-room/v1/index/getRoomBaseInfo?req_biz=web_room_componet&room_ids=<id>` — 批量查多个房间（对未来「多主播」扩展友好）
- `GET /room/v1/Room/get_status_info_by_uids?uids[]=<uid>` — 按 UID 反查（适合用户给的是主播 UID 而不是 room_id 的场景）

### 2.2 拉流地址

```
GET https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo
    ?room_id=<id>
    &protocol=0,1
    &format=0,1,2
    &codec=0,1
    &qn=10000
```

返回 `data.playurl_info.playurl.stream[].format[].codec[].url_info[]`（嵌套较深，注意解构）。

参数说明：

| 参数 | 含义 | 推荐值 |
|---|---|---|
| `protocol` | 0=http_stream（FLV）/ 1=http_hls | `0,1` 都要 |
| `format` | 0=flv / 1=ts / 2=fmp4 | `0,1,2` 都要 |
| `codec` | 0=avc / 1=hevc | `0,1` 都要 |
| `qn` | 画质：10000=原画/原始/原 / 400=蓝光 / 250=超清 / 150=高清 / 80=流畅 | `10000` 拿原画（拿到啥取决于主播）|

**关键事实**：返回的流 URL 携带 **时效 token**（`expires` 参数），必须**用即获取、获取即用**。如果 probe 探测到开播但 recorder 启动延迟超过几十秒，token 可能过期 → recorder 启动前应该再调一次 `getRoomPlayInfo` 拿新鲜 URL。

### 2.3 风控 / cookie / UA / 签名

- **WBI 签名**（`w_rid` / `wts` 参数）：
  - 2023 年 3 月起 B 站对部分 Web 端接口启用 WBI 风控。
  - 但是：截至 2025-05，**`get_info` / `room_init` / `getRoomInfoOld` / `getRoomBaseInfo` / `getRoomPlayInfo` 都不强制 WBI**。
  - 强制 WBI 的仅 `getDanmuInfo`（弹幕流）—— 我们不需要弹幕。
  - 长期看 B 站可能扩大 WBI 范围，所以需要预留实现位（写一个 `_sign_wbi(params)` 帮手函数，默认不挂 hook，未来需要时再启用）。
  - 算法：`mixin_key = mixin_table_permute(img_key + sub_key)[:32]`，其中 `img_key`/`sub_key` 来自 `https://api.bilibili.com/x/web-interface/nav` 的 `data.wbi_img.img_url` / `sub_url` 文件名（PNG URL 文件名是伪装的密钥）。然后参数按 key 排序、过滤 `!'()*` 字符、URL encode、拼上 `wts`，对整串 MD5 拿 `w_rid`。详见 [docs/misc/sign/wbi.md](https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/sign/wbi.md)。
- **Cookie**：状态/拉流接口 anonymous 即可；`buvid3` / `SESSDATA` 仅对登录态弹幕等敏感接口必要。**MVP 不需要任何 cookie**。
- **UA**：建议用真实浏览器 UA（与抖音 probe 一致：`Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...`），避免被识别为爬虫并触发限速。
- **限速 / 风控**：`-403: illegal access` 是 WBI 缺失的典型返回；状态/拉流接口短时间内高频轮询（< 1s 间隔）也可能被限。我们 30s 一轮的 poll 间隔安全。
- **隐私政策**（2024 后）：anonymous 客户端连接弹幕 stream 时用户 mid 会被置 0、用户名会用 `*` 掩码。**仅影响弹幕，不影响录制**。

### 2.4 优势与局限

✅ 优势：

- **复杂度极低**：3 个 HTTP 调用就能搞定状态 + 拉流地址，纯 stdlib + httpx 就行，无 Playwright / chromium 依赖。
- **稳定性高**：官方接口字段稳定（v1 接口活了 5+ 年），不像抖音页面经常改 marker。
- **资源占用小**：一次状态 polling 几 KB；不像 Playwright 启 chromium 几百 MB。
- **可观测**：HTTP response 可以直接日志化，比 Playwright stdout JSON 便于 debug。

⚠️ 局限：

- 长期：WBI 范围可能扩大（虽然现在状态接口不需要）。
- 边缘：极少数房间（开播状态保护、特殊地区限制）API 拿不到 → 需要 fallback。
- 流地址 token 短期失效，要做"开播 → 立刻拿 URL → 立刻启 recorder"的紧凑流水。

---

## 3. 网页 / Playwright 路线

技术上**完全可行**（与抖音 probe 同思路）：

- 持久 profile + chromium 打开 `live.bilibili.com/<room_id>`
- 订阅 `request` / `response` 事件抓 `*.m3u8` / `*.flv`
- 解析页面 marker：B 站直播页有 `"liveStatus":1` 这类内联 JSON，但具体 key 与抖音不同（B 站走的是 `__INITIAL_STATE__`），需要现场解析

但是**没必要走这条路**，因为：

- 比 API 路线慢一两个数量级，资源开销大得多。
- B 站 anonymous API 太友好，没有抖音那种"页面 JS 重度风控"的场景。
- **唯一价值**是 fallback：当 API 因风控/超时失败时，掉到 Playwright。但 MVP 阶段没必要做 —— 把这个 fallback 留作后续 PR。

---

## 4. ffmpeg 拉流 header 要求（关键差异点）

**B 站流 ffmpeg 拉取强制要 Referer**，否则直接 403。这是和抖音的最大差异：

```bash
ffmpeg \
  -headers "Referer: https://live.bilibili.com" \
  -user_agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36" \
  -i "<stream_url_from_getRoomPlayInfo>" \
  -c copy output.flv
```

要点：

- `-headers` 多行用 `\r\n` 分隔；ffmpeg 接受 `-user_agent` 单独参数所以 UA 拆开写最稳。
- **流地址含时效 token**：拿到 URL 后必须立刻启 ffmpeg；如果 recorder 启动失败重试，要回头重新调 `getRoomPlayInfo`，不能复用过期 URL。
- 对我们仓库的影响：`recorder` 当前的 ffmpeg 命令是平台中立的，但 **header 注入逻辑要平台化** —— 推荐在 `AgentSnapshot` 加可选字段 `stream_headers: dict[str, str] | None`，让上游 probe 决定要传什么 header，下游 recorder 透传。
- 如果选「先替换后通用化」的折中，也可以在 recorder 里加 `if stream_url 含 'bilivideo.com'`，但这是 anti-pattern，**强烈不推荐**。

---

## 5. 开源参考实现（间接证据）

由于 WebFetch 在主代理调研时不稳定，未能直接抓 Bililive-go / DDTV 源码。但根据 WebSearch 命中的 issue 和实现讨论交叉验证：

- **Bililive-go**（[hr3lxphr6j/bililive-go](https://github.com/hr3lxphr6j/bililive-go)）：Go 实现，多平台直播录制器，B 站走 API 路线；platform 通过 `src/live/<site>/` 目录的 site-plugin 模式注册，每个站点实现一个 `Live` 接口。
- **DDTV**（[CHKZL/DDTV](https://github.com/CHKZL/DDTV)）：C# 实现，B 站特化，直接走 API；ffmpeg 拉流时强制带 `Referer: https://live.bilibili.com`。
- **YuxuanZuo/Bilibili-Live-Recorder**：[Issue #1](https://github.com/YuxuanZuo/Bilibili-Live-Recorder/issues/1) 直接证明了 ffmpeg 不带 Referer 会 403。

> 实施时应再花 1-2 小时通读 Bililive-go 的 `src/live/bilibili/` 与 site interface，对照本研究的 endpoint 列表确认细节。

---

## 6. 风险与坑

| 风险 | 概率 | 应对 |
|---|---|---|
| `live_status == 2`（轮播）误判为在播 | 高（B 站特有）| Probe 层直接把 2 映射为 OFFLINE，加 `reason=carousel_playback` |
| 流 URL token 过期（recorder 延迟启动）| 中 | recorder 启动失败重试时回 probe 重新拿 URL |
| WBI 范围未来扩大到状态接口 | 低（短期）| 预留 `_sign_wbi` 帮手函数，签名钩子 off-by-default |
| 主播开"仅粉丝可见" / 18+ 限制 | 低 | API 返回特殊 errno，probe 归类为 `offline + reason=room_locked` |
| ffmpeg 缺 Referer 跑通本地测试但生产 403 | 中 | 把 header 注入纳入 PR 必测路径（用真实房间 ffmpeg 验一次）|
| 长跑 polling 触发 IP 限速 | 低（30s 间隔安全）| 监测 4xx / 5xx 比率，超阈值降级 |

---

## 7. 推荐路线（A/B/C 对比）

| 维度 | A: 纯 API | B: 纯 Playwright | C: 混合（推荐） |
|---|---|---|---|
| 实现复杂度 | 小（httpx + JSON 解析）| 中（与抖音 mjs 同模板）| 中（A + 一个 fallback 开关）|
| 资源占用 | 极低（KB 级 / poll）| 高（chromium 进程）| 平时低，fallback 时高 |
| 稳定性 | 高（官方接口）| 中（页面易变）| 高 |
| 与抖音架构契合度 | 中（和抖音 probe 形态不同）| 高（同 Playwright + httpx 二层）| 高（兼容两种）|
| 应对未来风控 | 中（需要加 WBI）| 高（浏览器层天然过风控）| 高 |
| MVP 是否值得 | ✅ | ❌（过度工程）| ✅ |

**最终推荐：先 A（纯 API），代码里把 fallback 接口预留好（`detect()` 内部 `try API except → return None → 让外层调度可挂下一种 probe`），第一个 PR 不实现 Playwright fallback，留给后续 PR。**

> 这是 C 的"骨架先到位、肉后续填"版本，与「先替换后通用化」的折中思路（见 prd.md 的 scope decision）一脉相承——可调架构留出来，但 MVP 不实做。

---

## 8. References

- [SocialSisterYi/bilibili-API-collect — 直播间信息](https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/live/info.md)
- [SocialSisterYi/bilibili-API-collect — WBI 签名](https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/sign/wbi.md)
- [hr3lxphr6j/bililive-go](https://github.com/hr3lxphr6j/bililive-go)
- [CHKZL/DDTV](https://github.com/CHKZL/DDTV)
- [Bilibili-Live-Recorder — Issue #1（403 Forbidden 实证）](https://github.com/YuxuanZuo/Bilibili-Live-Recorder/issues/1)
- [DIYgod/RSSHub — bilibili live-room 路由（API 调用范例）](https://github.com/DIYgod/RSSHub/blob/master/lib/routes/bilibili/live-room.ts)
- [VideoHelp Forum — ffmpeg referer header 用法](https://forum.videohelp.com/threads/388796-Recording-m3u8-live-streaming-403-Forbidden-(/)
