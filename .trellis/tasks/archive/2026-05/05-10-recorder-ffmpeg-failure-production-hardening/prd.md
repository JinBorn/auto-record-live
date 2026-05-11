# Recorder ffmpeg failure production hardening

## Goal

把 recorder 在 ffmpeg 失败下的行为从"已分类、已重试、已留痕"再推一步到生产可用：
让 token 过期 URL 不再被白白消耗重试预算、让 stderr 上下文足以定位问题、让重试预算有退避/封顶不会抖死下游、让审计信号可被巡检脚本/launcher 直接消费。

## What I already know（已完成的底盘，无需重做）

- `src/arl/shared/failure_contracts.py` 已有 5-bucket 分类器 `classify_failure_reason`：
  HTTP 4xx_non_retryable / HTTP 5xx_retryable / network_timeout_retryable /
  ffmpeg_process_error_retryable / unknown_unclassified_non_retryable，
  每类带 `failure_category` + `reason_code` + `is_retryable`。
- `src/arl/recorder/service.py` 两层重试：
  - in-run `RecordingSettings.ffmpeg_max_retries`（默认 1）= 单次 run 内 ffmpeg 立刻重跑
  - across-run `auto_retry_max_attempts`（默认 2）= 跨 run 通过 `RecordingJobStatus.RETRYING` 触发 orchestrator 重排
  - 非 retryable 立刻 break in-run loop
- 审计事件已落 `data/tmp/recorder-events.jsonl`：`ffmpeg_record_failed/succeeded`、
  `recording_retry_scheduled/exhausted`、`recording_manual_recovery_required`、
  `ffmpeg_fallback_placeholder`、`ffmpeg_skipped`，含规范化字段 `decision`/`failure_category`/`reason_code`/`reason_detail`/`attempt`/`max_attempts`。
- 手动恢复 `RecorderRecoveryAction` 行动表已写到 `data/tmp/recorder-recovery-actions.jsonl`，按 failure_category 分流到 `restore_source_prerequisites` / `check_network_source_stability` / `inspect_ffmpeg_process_failure` / `inspect_failure_logs`。
- stderr 现仅取 `error.stderr.strip().splitlines()[-1][:240]`（末行截断 240 字符），见 `_format_ffmpeg_failure_reason`。

## 已识别的 Gap（来自代码审视 + brief 提示）

1. **Token 过期 URL 重试浪费**（**in MVP**）：B 站/抖音的 `stream_url` 含短时效 token；recorder 拿的是 orchestrator state 里的快照。当 URL 已老化导致 4xx/timeout，当前 1+2=3 次重试都用同一个过期 URL，纯属浪费 + 拖慢"等下一轮 probe 喂新 URL"的恢复。
2. **stderr 结构化上下文缺失**（**in MVP**）：末行 240 字符不足以辨认场景（首行 banner、HTTP 状态码数值、ffmpeg 阶段提示、muxing 失败位置往往在中间几行）；调试时被迫现场重跑或翻日志。
3. **重试无退避 + per-session 累计未封顶**（**in MVP**）：3 次预算紧贴执行，碰到 5xx_retryable / network_timeout 抖动会瞬时刷爆 audit log；orchestrator 可能为同一 session 反复建 job 继续打转，缺一个 session 级累计上限。
4. **403 forbidden cookie 过期 vs 404 流真没了**（**Out of Scope**，留下个任务）：当前都归 `http_4xx_non_retryable` 走 manual；cookie 失效已有独立 `cookie_expired_for_<platform>` 链路。
5. **exporter 复用 ffmpeg helper**（**Out of Scope**，留下个任务）。

## Decisions (ADR-lite)

**Context**：recorder 已有失败分类+审计骨架，但当前重试逻辑对 token 过期 URL 不友好、stderr 上下文薄、缺 session 级抖动护栏。
**Decisions**：
- **D1 (Gap1 Yield-on-transient)**：transient（5xx / network_timeout / ffmpeg_process_error）在 in-run loop 内**首次失败就 break**，不再消耗 `ffmpeg_max_retries`，让 `_resolve_ffmpeg_result` 走 retryable 分支等下一轮 probe 喂新 URL。非 transient（4xx / unknown）保持立刻收手语义不变。`ffmpeg_max_retries` 配置仍保留作为 schema 兼容字段，不删。
- **D2 (Gap2 stderr capture)**：双轨保存
  - audit JSONL 行新增字段 `stderr_excerpt`：首 5 + 末 15 行，每行 ≤ 240 字符，总长 ≤ 4 KB；
  - 同时把每次 ffmpeg attempt 的完整 stderr 落到 `data/tmp/recorder-stderr/<job_id>-<attempt>.log`，audit 行新增 `stderr_log_path` 字段；
  - 保留策略：扫描 `recorder-stderr/` 在 recorder 启动时滚动保留最近 200 个文件（按 mtime 倒序），超出删除。配置 env-overridable：`ARL_RECORDER_STDERR_RETAIN_COUNT`。
- **D3 (Gap3 Backoff + Session cap)**：
  - `RecorderStateFile.next_eligible_at_by_job_id: dict[str, datetime]`，transient yield 后按 in-job attempt 设 1s/5s/15s/60s（capped 60s），recorder 每轮先 skip 未到期 job；
  - `RecorderStateFile.retries_by_session_id: dict[str, int]`，每次 transient yield 累加；超过阈值（默认 8，`ARL_RECORDER_SESSION_RETRY_BUDGET`）→ 把该 session 下所有 active job 标 `RecordingJobStatus.FAILED` + `failure_category=unknown_unclassified_non_retryable`，emit 新 audit `recording_session_retry_budget_exceeded`，进 manual_required 路径。
**Consequences**：
- 抖动场景由"刷爆 audit + 反复浪费 ffmpeg 拉起"变为"指数退避 + 累计护栏"。
- recorder state 多两个 dict，需要向后兼容老 state file（缺字段默认空 dict）。
- audit 行变胖（stderr_excerpt + stderr_log_path），但仍是单行 JSON，下游消费方零改动。

## Requirements

- **R1 (Gap1)**: `_record_with_ffmpeg` / `_record_browser_capture_with_ffmpeg` 在第一次 transient 失败后立即 break in-run loop，返回 `(None, failure_reason)`。新 audit `decision` 值 `attempt_failed_yield_to_next_probe` 区分 yield 与 non-retryable `attempt_failed`。
- **R2 (Gap2 excerpt)**：`_format_ffmpeg_failure_reason` 不变签名，新增 `_capture_ffmpeg_stderr(error) -> tuple[str, str]` 返回 `(reason_one_liner, stderr_excerpt)`。audit `RecorderAuditEvent` 加字段 `stderr_excerpt: str | None = None`。
- **R3 (Gap2 file)**：`_record_with_ffmpeg` / `_record_browser_capture_with_ffmpeg` 每次 attempt 失败后把完整 stderr 写到 `data/tmp/recorder-stderr/<job_id>-<attempt>.log`，audit `RecorderAuditEvent` 加字段 `stderr_log_path: str | None = None`。recorder 启动时执行一次滚动清理。
- **R4 (Gap3 backoff)**：`RecorderStateFile.next_eligible_at_by_job_id: dict[str, datetime] = {}`；recorder 主循环跳过未到期 job（log 一次 `recorder", "job deferred ..."`），yield 时按 attempt 设 1/5/15/60s 退避。
- **R5 (Gap3 session cap)**：`RecorderStateFile.retries_by_session_id: dict[str, int] = {}`；transient yield 累加；超过 `ARL_RECORDER_SESSION_RETRY_BUDGET`（默认 8）→ 将该 session 下所有 active 与 retrying job 推到 FAILED，emit `recording_session_retry_budget_exceeded`，进 manual_required 路径。

## Acceptance Criteria

- [ ] Transient 失败 in-run 不再循环重试（ffmpeg 只跑 1 次后 yield）。新 audit `decision=attempt_failed_yield_to_next_probe`。
- [ ] Non-retryable（4xx / unknown）保持原行为：第一次失败立即 break，`decision=attempt_failed`。
- [ ] audit 行 `stderr_excerpt` 在 transient + non-retryable 失败时均含首 5 + 末 15 行；总长 ≤ 4 KB；成功 audit 不含该字段。
- [ ] `data/tmp/recorder-stderr/<job_id>-<attempt>.log` 在失败时存在，含完整 stderr；audit `stderr_log_path` 指向同路径（相对仓库根）。
- [ ] recorder 启动时清理 `recorder-stderr/` 仅保留最近 N=`ARL_RECORDER_STDERR_RETAIN_COUNT`（默认 200）个文件。
- [ ] 同一 transient job：第 1 次 yield 后 `next_eligible_at` = now+1s；第 2 次 = +5s；第 3 次 = +15s；后续 = +60s。recorder 在到期前跳过该 job 不调 ffmpeg。
- [ ] 同一 session 累计 transient yield >= `ARL_RECORDER_SESSION_RETRY_BUDGET`（默认 8）→ session 下所有 active/retrying job 落 FAILED + 新 audit `recording_session_retry_budget_exceeded` + 进 manual_required。
- [ ] 老 `recorder-state.json`（缺新字段）能正常加载并补全默认空 dict。
- [ ] orchestration-contracts.md 同步：audit event 新值、新 audit 字段、state 新字段、retry-budget contract、env 列表。
- [ ] pytest 全量绿（baseline 247 + 新增 ≥ 15 用例覆盖每条 R1-R5）。

## Definition of Done

- 单元/集成测试覆盖每个新行为；pytest 全量绿。
- `orchestration-contracts.md` 同步。
- `README.md` 故障排查段落小更新（新增 stderr 文件位置 + session 预算环境变量）。
- 老 audit JSONL 行（无 `stderr_excerpt`/`stderr_log_path`）能向后兼容反序列化。
- recorder state JSON 老格式可加载，无破坏。

## Out of Scope

- 不动 orchestrator 的 session/job schema 或 lifecycle（recorder 自己改 job.status 已经走原路径）。
- 不引入 Prometheus / OTel。
- 不动 ffmpeg 命令本身的编码/画质参数。
- 不抽 `_run_ffmpeg_with_retries` shared helper 让 exporter 复用（Gap5，下个任务）。
- 不做 cookie 403 → cookie-health 链路衔接（Gap4，下个任务）。
- 不改 LoL 语义阶段 / segmenter / subtitles / exporter。

## Technical Approach + PR Plan

**PR1 — Yield-on-transient + audit `decision` 拓展**
- 修改 `_record_with_ffmpeg` / `_record_browser_capture_with_ffmpeg`：transient 首次失败后 break，attempt 计数仍上报到 audit。
- audit `decision` 拓展 `attempt_failed_yield_to_next_probe`；`failure_contracts.CORE_DECISION_EVENT_TYPES` 不变（仍是同一 `ffmpeg_record_failed` 事件）。
- 测试：transient (5xx/timeout/process_error) 各一例；non-retryable (404) 一例。

**PR2 — stderr_excerpt + per-attempt .log 文件 + 启动清理**
- `RecorderAuditEvent` 加 `stderr_excerpt: str | None`、`stderr_log_path: str | None`。
- 抽 `_capture_ffmpeg_stderr(error, job_id, attempt) -> tuple[str, str | None, str | None]`：返回末行 reason（向后兼容现 reason 行为）+ excerpt + log_path。
- 启动时 `_rotate_stderr_logs(retain=ARL_RECORDER_STDERR_RETAIN_COUNT)`。
- 测试：失败写文件 + audit 字段；成功不写；rotation 在 N+5 测试。

**PR3 — Backoff `next_eligible_at_by_job_id`**
- `RecorderStateFile` 加 `next_eligible_at_by_job_id: dict[str, datetime] = {}`。
- 主循环：到期检查 → 不到期 skip + log。yield 后按 attempt 设 1/5/15/60s。
- 测试：yield 序列 → eligible 时间正确；recorder 同轮再扫不再调 ffmpeg。

**PR4 — Per-session retry budget + manual escalation**
- `RecorderStateFile` 加 `retries_by_session_id: dict[str, int] = {}`。
- 累加点：每次 transient yield。
- 触顶后把 session 下 active/retrying job 推 FAILED + audit `recording_session_retry_budget_exceeded`。
- 测试：累计到阈值边界；session 后续 job 立即 manual；其它 session 不受影响。

**PR5 — Contracts + README + integration smoke**
- `orchestration-contracts.md`：event_type registry / audit fields / state fields / env vars / validation matrix。
- `README.md` 录制故障排查段落：stderr 文件位置 + `ARL_RECORDER_SESSION_RETRY_BUDGET` / `ARL_RECORDER_STDERR_RETAIN_COUNT`。
- 端到端 fake-ffmpeg 集成测试：模拟 5xx → yield → backoff → 累计 → manual 一条龙。

## Technical Notes

- 关键文件：
  - `src/arl/recorder/service.py` 主控 + `_record_with_ffmpeg` / `_record_browser_capture_with_ffmpeg` / `_resolve_ffmpeg_result` / `_format_ffmpeg_failure_reason`
  - `src/arl/recorder/models.py` `RecorderAuditEvent` / `RecorderRecoveryAction` / `RecorderStateFile`
  - `src/arl/shared/failure_contracts.py` 分类器与 5-bucket 常量 / `CORE_DECISION_EVENT_TYPES`
  - `src/arl/orchestrator/models.py` `RecordingJobRecord` 失败元数据
  - `src/arl/config.py` `RecordingSettings.ffmpeg_max_retries` / `auto_retry_max_attempts` / `direct_stream_timeout_seconds`
  - `.trellis/spec/backend/orchestration-contracts.md` 契约同步源
  - `.trellis/spec/backend/error-handling.md` / `quality-guidelines.md`
- 关联运行时数据：`data/tmp/recorder-events.jsonl`、`data/tmp/recorder-recovery-actions.jsonl`、`data/tmp/recorder-state.json`、新增 `data/tmp/recorder-stderr/`。
- 关联近期工作：`05-10-cookie-expiration-audit-event-...` 已经把 cookie 失效信号化；本任务在 transient 路径上不重叠（cookie 401/403 仍是 4xx_non_retryable → manual，由下个任务 Gap4 接走）。

## Research References

无外部研究，全部基于本仓库代码 + brief。
