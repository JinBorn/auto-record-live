# Recorder 403 cookie expiration audit link

## Goal

把 recorder ffmpeg 失败路径里的 HTTP 403（"cookie 过期"高置信信号）从混入 `http_4xx_non_retryable` 的 generic manual-recovery 桶中区分出来，让它**也**走 05-10 的 `cookie_expired_for_<platform>` 审计通道，这样：

- 操作者 grep `cookie_expired_for_*` 能同时看到 probe-time 和 record-time 两路证据，确认是 cookie 问题而非"主播没开播"或"流真没了"；
- 403（cookie 嫌疑）与 404/410（流真没了）在 audit 上信号分离，manual-recovery 排查可定向到 `arl cookie-health` / 刷新 cookie env；
- 不改 retry 语义（仍是 non-retryable / 立刻 manual）。

## What I already know

### 当前 4xx 处理（recorder 侧）

- `src/arl/shared/failure_contracts.py:53` `classify_failure_reason` 把 `401 unauthorized / 403 forbidden / 404 not found / 410 gone / server returned 4*` 全部归到单一 `FAILURE_CATEGORY_HTTP_4XX_NON_RETRYABLE`，单一 `REASON_CODE_HTTP_4XX`，`is_retryable=False`。
- `src/arl/recorder/service.py:457` `_record_with_ffmpeg` / `:569` `_record_browser_capture_with_ffmpeg`：失败 reason 取自 stderr 末行（240 字符截断），走 `classify_failure_reason` → 非 retryable 一次后 break。
- audit `ffmpeg_record_failed` 已含 `stderr_excerpt`（首 5 + 末 15 行 ≤ 4KB） + `stderr_log_path` + 标准 decision 字段。
- 4xx 现行流：recorder emit `ffmpeg_record_failed(decision=attempt_failed, is_retryable=False)` → orchestrator `_handle_recorder_event` → `recording_job_attempt_failed_terminal` → manual recovery hint "Source rejected the request (HTTP 4xx). Refresh stream URL/session prerequisites before rerun."
- 非 retryable failure 后 recorder 写 `processed_job_ids` 并返回 placeholder asset → 后续 recorder run 自然跳过该 job（**天然跨 run 不会 spam**）。

### 当前 cookie_expired 通道（probe 侧）

- `src/arl/windows_agent/platform_probe.py` `classify_cookie_state(snapshot) → CookieState`，高置信策略：Bilibili sessdata + `api_error:code=-101` / Douyin cookie + `quality_below_min_tier:hd<`。
- `src/arl/windows_agent/service.py:75-90` 在 live_* 事件后追加 `cookie_expired_for_<platform>`，共享 `_has_changed` dedup。
- `src/arl/orchestrator/service.py:129-160` `_on_cookie_expired` 仅 append audit，不动 session/job state。
- 配置：`Settings.douyin.cookie` / `Settings.bilibili.sessdata`（`""` 表示未配置）。

### 平台差异（影响信号置信度）

- **抖音**：cookie 驱动 1080P+ DOM URL 的访问令牌，stream URL 403 与 cookie 过期高度相关。
- **B 站**：SESSDATA 影响 `getRoomPlayInfo` 返回的 qn=400+ URL；但 URL 内嵌的短时效 token **与 SESSDATA 解耦** —— 录制启动晚或 token 老化得到的 403 实际上刷 SESSDATA 解决不了。**已知 false-positive 风险**：B 站 SESSDATA 已配置 + token 过期 403 → 仍会 emit `cookie_expired_for_bilibili`。README 文档明示，操作者可 `arl cookie-health` 二次确认。

## Decisions (ADR-lite)

**Context**：recorder 已有 5-bucket 失败分类骨架，但 4xx 通杀到 manual recovery，操作者难辨 403 cookie 嫌疑 vs 404 流真没了；probe 已有 cookie_expired_for_<platform> 信号但 recorder 没接。

**Decisions**：

- **D1 (reason_code 拆分)**：`failure_contracts.py` 加 `REASON_CODE_HTTP_403_FORBIDDEN`；`classify_failure_reason` 优先匹配 `"403 forbidden"` 与 `"server returned 403"` 两个 marker，命中返回新 reason_code，仍同属 `FAILURE_CATEGORY_HTTP_4XX_NON_RETRYABLE` 且 `is_retryable=False`（retry 语义不动，仅细分 reason_code）。`CANONICAL_REASON_CODES` 加入新值。其他 4xx（401/404/410/server returned 4*）继续返回 `REASON_CODE_HTTP_4XX`。
- **D2 (recorder 触发条件)**：在 `_record_with_ffmpeg` / `_record_browser_capture_with_ffmpeg` 失败分支，当 `failure_decision.reason_code == REASON_CODE_HTTP_403_FORBIDDEN` **且** `job.platform` 对应的 cookie env 已配置（douyin → `settings.douyin.cookie != ""`；bilibili → `settings.bilibili.sessdata != ""`；其他平台 → 不 emit），在 emit `ffmpeg_record_failed` 之后追加一行 `cookie_expired_for_<job.platform>` 走 `_append_audit`。事件 schema 复用 `RecorderAuditEvent`，仅 event_type 与 reason 字段，decision/failure_category/reason_code 等字段为 `None`。
- **D3 (orchestrator 路由)**：`_handle_recorder_event` 在现有 `known_event_types` set 之外加分支 `if event.event_type.startswith("cookie_expired_for_"):` → 仅 append audit（与 agent-event 路径 `_on_cookie_expired` 语义对齐），**不**调 `_apply_failure_metadata`、**不**做 stale check、**不**改 job 状态、**不**写 recovery audit。
- **D4 (dedup)**：依赖 recorder 现有 `processed_job_ids` 机制（非 retryable failure 后 job 进 processed_job_ids，跨 run 跳过）。单 run 内 4xx 路径一次 ffmpeg attempt 后即 break。故 cookie_expired_for_<platform> 自然每 job 至多 1 行，无需新增 dedup state。

**Consequences**：

- 共享 `CANONICAL_REASON_CODES` 加新成员 → `validate_core_decision_fields` 自动接受新值；老 audit JSONL 行的 `reason_code=http_4xx` 仍可加载（值是字符串 set 校验）。
- recorder service 需要把 `job.platform` 传入 `_record_with_ffmpeg` / `_record_browser_capture_with_ffmpeg`（当前签名没有），调用处 `_build_recording` 已能拿到。
- B 站 token-expired 403 会被误报 cookie_expired_for_bilibili —— 文档化、不阻塞 MVP。
- 下游消费者（exporter/Gap5、外部巡检脚本）可直接 grep `reason_code=http_403_forbidden` 或 `cookie_expired_for_*`。

## Requirements

- **R1 (classifier 拆分)**：`classify_failure_reason` 对 reason 含 `"403 forbidden"` 或 `"server returned 403"` 返回 `FailureDecision(failure_category=HTTP_4XX_NON_RETRYABLE, is_retryable=False, reason_code=HTTP_403_FORBIDDEN)`。其他 4xx 行为不变。
- **R2 (gating)**：recorder 在 `_record_with_ffmpeg` / `_record_browser_capture_with_ffmpeg` 失败分支，仅当 `failure_decision.reason_code == REASON_CODE_HTTP_403_FORBIDDEN` 且 `_platform_cookie_configured(job.platform)` 返回 True 时，emit `cookie_expired_for_<job.platform>` audit。
- **R3 (cookie env 探测)**：recorder 通过 `self.settings.douyin.cookie` / `self.settings.bilibili.sessdata` 判断对应平台 cookie 是否配置；未注册平台一律 False。
- **R4 (event shape)**：新 audit 行复用 `RecorderAuditEvent`，必填 `event_type=cookie_expired_for_<platform>` + `session_id` + `job_id` + `source_type` + `reason=<failure reason>`；decision/failure_category/is_retryable/reason_code/reason_detail/attempt/max_attempts/stderr_excerpt/stderr_log_path 全部 None。
- **R5 (orchestrator 路由)**：`_handle_recorder_event` 加 prefix 分支处理 `cookie_expired_for_*`：append orchestrator audit only（消息含 platform + session_id + job_id + reason）+ 不动 job 状态。**不** 入 `known_event_types` set（避免被原 stale-check / failure-metadata 逻辑误触），单独 early-return 分支。
- **R6 (404/410 etc.)**：401/404/410/其他 4xx markers 命中时仍返回 `REASON_CODE_HTTP_4XX`，recorder 不 emit cookie_expired_for_*。

## Acceptance Criteria

- [ ] `classify_failure_reason("HTTP error 403 Forbidden")` 返回 `reason_code=http_403_forbidden`，`failure_category=http_4xx_non_retryable`，`is_retryable=False`。
- [ ] `classify_failure_reason("server returned 403")` 同上。
- [ ] `classify_failure_reason("404 not found")` 与 `"401 unauthorized"` 仍返回 `reason_code=http_4xx`。
- [ ] recorder ffmpeg 失败 stderr 含 "403 forbidden"：
  - douyin job + `ARL_DOUYIN_COOKIE` 已配置 → recorder-events.jsonl 含 `ffmpeg_record_failed` 与 `cookie_expired_for_douyin` 两行；
  - douyin job + cookie 未配置 → 只有 `ffmpeg_record_failed`，**无** cookie_expired；
  - bilibili job + `ARL_BILIBILI_SESSDATA` 已配置 → 含 `cookie_expired_for_bilibili`；
  - bilibili job + sessdata 未配置 → 无 cookie_expired。
- [ ] recorder ffmpeg 失败 stderr 含 "404 not found"（cookie 配置任意） → 只有 `ffmpeg_record_failed`，**无** cookie_expired。
- [ ] orchestrator 处理 recorder-events.jsonl 中的 `cookie_expired_for_<platform>`：写 orchestrator-events.jsonl 一行 audit，**不**改 job.status / failure_category / recoverable / recovery_hint。
- [ ] 同一 job 即使被 recorder 多 run 看到（虽然实际上会被 `processed_job_ids` 跳过），也至多产生 1 行 cookie_expired_for_*。
- [ ] orchestration-contracts.md 同步：`event_type` registry 注明 cookie_expired_for_* 可来自 windows-agent + recorder 双路径；`reason_code` 注册表新增 http_403_forbidden；validation matrix 新行覆盖 R1-R6。
- [ ] README "Cookie 配置与失效审计" 段落补充：recorder ffmpeg 403 也会叠发 cookie_expired_for_<platform>；B 站 token 过期已知 false-positive 注脚。
- [ ] pytest 全量绿（baseline 262 + 新增 ≥ 8 用例覆盖每条 R）。

## Definition of Done

- 单元 + 集成测试覆盖 R1-R6；pytest 全量绿。
- `orchestration-contracts.md` 同步。
- `README.md` 录制/cookie 段落同步。
- 老 audit JSONL 行（`reason_code=http_4xx`）与老 recorder-state 文件加载零回归。

## Out of Scope

- 不改 retry 语义：403 仍 non-retryable，立刻 manual；不引入 cookie-driven auto-retry。
- 不动 `arl cookie-health` CLI（continue 用 probe.classify_cookie_state，不轮询 recorder 历史）。
- 不动 `classify_cookie_state` / `CookieState` enum。
- 不解决 B 站 stream URL token 过期 false-positive —— 文档化即可。
- 不拆 `FAILURE_CATEGORY_HTTP_4XX_NON_RETRYABLE`（只拆 reason_code，category 不动）。
- 不抽 exporter 复用 ffmpeg helper（Gap5，下个任务）。
- 不动 windows-agent 任何代码。

## Technical Approach + PR Plan

**PR1 — classifier 拆 403 + recorder gating emit + 单测**

文件：
- `src/arl/shared/failure_contracts.py`：加 `REASON_CODE_HTTP_403_FORBIDDEN = "http_403_forbidden"`；`classify_failure_reason` 在通用 4xx 分支之前先匹配 403 markers（"403 forbidden" / "server returned 403"）。`CANONICAL_REASON_CODES` 加入新值。
- `src/arl/recorder/service.py`：`_record_with_ffmpeg` / `_record_browser_capture_with_ffmpeg` 签名加 `platform: str | None` 参数；`_build_recording` 调用处传入 `job.platform`；失败分支在 emit `ffmpeg_record_failed` 后追加 `_maybe_emit_cookie_expired(platform, session_id, job_id, source_type, reason, failure_decision)` 调用，内部检查 reason_code + cookie env，命中则 `self._append_audit(f"cookie_expired_for_{platform}", ..., reason=...)`。
- 新方法 `_platform_cookie_configured(platform: str | None) -> bool`：dispatch on `settings.douyin.cookie` / `settings.bilibili.sessdata`，未注册平台 False。

测试：
- `tests/shared/test_failure_contracts.py`（或现有同类测试文件）+3：403 / server returned 403 / non-403 4xx 仍回 http_4xx。
- `tests/recorder/test_service_cookie_expired_emit.py` 新建 +5：
  - douyin cookie 配置 + 403 → 双行
  - douyin cookie 空 + 403 → 单行
  - bilibili sessdata 配置 + 403 → 双行
  - 404 + 任意 cookie → 单行
  - browser_capture 路径 + 403 + douyin cookie → 双行（覆盖 browser_capture 失败分支）

**PR2 — orchestrator 路由 + contracts + README + 集成测试**

文件：
- `src/arl/orchestrator/service.py`：`_handle_recorder_event` 在 `if event.job_id is None` 之后、`known_event_types` 之前加分支 `if event.event_type.startswith("cookie_expired_for_"):` → `self.state_store.append_audit(event.event_type, session_id=event.session_id, job_id=event.job_id, message=f"platform={platform} reason={event.reason or 'n/a'}")` + early return（不调 `_apply_failure_metadata` / 不调 `_mark_recorder_event_applied`）。
- `.trellis/spec/backend/orchestration-contracts.md`：event_type registry 注明 cookie_expired_for_* 可来自 recorder 路径；reason_code 表加 http_403_forbidden；validation matrix 加 6 行覆盖 R1-R6。
- `README.md` "Cookie 配置与失效审计" 段落 + ffmpeg 失败排查段落补充。

测试：
- `tests/orchestrator/test_recorder_cookie_expired_event.py` 新建 +2：recorder-events 含 cookie_expired_for_douyin → orchestrator audit 出现一行 + job 状态零变化；double event 同 job → 写两行 audit（recorder 已 dedup 所以实际不会出现，但 orchestrator 不假定）。
- 端到端：fake-ffmpeg 403 → recorder-events.jsonl 双行 → orchestrator-events.jsonl 双行（已有的端到端测试架子复用，新增 +1）。

## Technical Notes

### 关键文件

- `src/arl/shared/failure_contracts.py` — classifier + canonical sets
- `src/arl/recorder/service.py` — `_record_with_ffmpeg` (L457) / `_record_browser_capture_with_ffmpeg` (L569) / `_append_audit` (L968) / `_build_recording` (L249)
- `src/arl/recorder/models.py` — `RecorderAuditEvent`（event_type 已是 str，schema 不动）
- `src/arl/config.py` — `DouyinSettings.cookie` (L203) / `BilibiliSettings.sessdata` (L212)
- `src/arl/orchestrator/service.py` — `_handle_recorder_event` (L412)
- `.trellis/spec/backend/orchestration-contracts.md` — 契约同步源

### 关联运行时数据

- `data/tmp/recorder-events.jsonl`：将出现 event_type=`cookie_expired_for_<platform>` 行（来自 recorder）
- `data/tmp/orchestrator-events.jsonl`：双源 cookie_expired_for_*（来自 windows-agent 路径 + recorder 路径）
- 老 recorder-state.json：无变化

### 关联近期工作

- `05-10-cookie-expiration-audit-event-...`：probe 端首次定义 cookie_expired_for_<platform> 语义；本任务延伸到 recorder 端
- `05-10-recorder-ffmpeg-failure-production-hardening`：留下的 Gap4
- 下个任务候选 Gap5：抽 `_run_ffmpeg_with_retries` shared helper 让 exporter 复用（含本任务拆出的 reason_code）

## Research References

无外部研究，全部基于本仓库代码 + 05-10 PRD 留下的 brief。
