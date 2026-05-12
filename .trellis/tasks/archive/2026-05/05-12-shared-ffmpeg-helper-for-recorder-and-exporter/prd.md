# Shared ffmpeg helper for recorder and exporter

## Goal

把 recorder 05-10 硬化里搭起来的 ffmpeg-failure 上下文捕获 / 分类 / stderr 落盘骨架抽到 `src/arl/shared/ffmpeg_runner.py`，让 exporter 复用。同时把 exporter 失败可观测性补齐到 recorder 同等级别（audit JSONL + stderr 落盘 + 分类），消除两条流水线排查体验割裂。Gap5 of 05-10 recorder hardening。

## What I already know

### Recorder ffmpeg 路径（post-hardening，已生产级）

- `src/arl/recorder/service.py:457` `_record_with_ffmpeg` / `:569` `_record_browser_capture_with_ffmpeg`：单次 attempt + yield-on-transient
- `_capture_ffmpeg_failure(error, job_id, attempt) -> (reason, excerpt, log_path)`（L652-666）
- `_extract_full_stderr(error)`（L668-679）
- `_build_stderr_excerpt(stderr_text)`（L681-698）：首 5 + 末 15 行 ≤ 240 chars/line ≤ 4 KB
- `_write_stderr_log(stderr_text, job_id, attempt) -> path str | None`（L700-721）：atomic write `data/tmp/recorder-stderr/<job_id>-<attempt>.log`
- `_rotate_stderr_logs(retain_count)`（L723-743）：启动按 mtime 滚动保留 N 个
- `_format_ffmpeg_failure_reason(error)`（L622-636）：末行截 240 字符 + exit_status fallback
- 配合 `classify_failure_reason`（`src/arl/shared/failure_contracts.py`）得到 5-bucket category + reason_code
- audit emit：`_append_audit("ffmpeg_record_failed", ..., stderr_excerpt=..., stderr_log_path=...)` 写 `recorder-events.jsonl`

### Exporter ffmpeg 路径（**原始 / 待硬化**）

- `src/arl/exporter/service.py:123-177` `_write_export_with_ffmpeg`：
  - `subprocess.run(command, check=True, timeout=...)` —— **没有 `capture_output=True`**，stderr 直接灌到父进程 stdout
  - `attempts = ffmpeg_max_retries + 1` 朴素重试循环（默认 2 次）
  - 失败只 `log("exporter", "ffmpeg export failed ... reason={error}")`，`{error}` 就是异常 str repr
  - **零失败分类、零 audit JSONL、零 stderr 落盘**
  - 所有 attempt 耗尽 → placeholder fallback（写一个 `.txt`）

### 配置位

- `RecordingSettings.stderr_retain_count: int = 200`（`ARL_RECORDER_STDERR_RETAIN_COUNT`）
- `ExportSettings.ffmpeg_max_retries: int = 1` / `ffmpeg_timeout_seconds: int = 120` / `ffmpeg_preset` / `ffmpeg_crf`

## Decisions (ADR-lite)

**Context**：recorder 已有完整 ffmpeg 失败硬化骨架（分类 + stderr 双轨捕获 + 落盘 + canonical audit），exporter 同样跑 ffmpeg 却完全没有这套。05-10 Gap5 留作下个任务。需求是抽出共享 helper 让 exporter 复用、同时把 exporter 失败可观测性补齐到 recorder 同等级别。

**Decisions**：

- **D1（抽取层级 = per-attempt helper）**：新 `src/arl/shared/ffmpeg_runner.py` 提供 `run_ffmpeg_attempt(command, *, timeout, stderr_log_dir, stderr_log_basename, attempt) -> FfmpegAttemptOutcome` 与 `rotate_stderr_logs(dir, retain_count)`。retry 循环 + audit emit 留在各 service。recorder 保留 yield-on-transient + per-session budget；exporter 保留朴素 in-run retry 循环。
- **D2（exporter 失败可观测 = 全套）**：引入 `data/tmp/exporter-events.jsonl` + canonical decision 字段（event_type=`ffmpeg_export_failed/succeeded` + `decision` + `failure_category` + `is_retryable` + `reason_code` + `reason_detail` + `stderr_excerpt` + `stderr_log_path`）。stderr 落 `data/tmp/exporter-stderr/<session_id>_match<idx>-<attempt>.log`。orchestrator 不消费 exporter audit（write-only for grep）。orchestration-contracts.md 同步。
- **D3（命名 / rotation）**：exporter stderr 文件名 `<session_id>_match<idx>-<attempt>.log`（path-safe，session_id 中的 `/` 替为 `_`）。rotation 启动时跑一次，`ARL_EXPORTER_STDERR_RETAIN_COUNT` 默认 200（与 recorder 对称）。
- **D4（retry 策略）**：exporter retry 行为不变——朴素 in-run retry 到 attempts 耗尽。是否引入 "non-retryable 立即 break" 是另一个产品决策，**Out of Scope**。
- **D5（recorder 行为零变化）**：recorder 仅 internal 重构调用点，audit shape / stderr 文件路径 / rotation 行为 byte-identical。所有现有 recorder 测试（`RecorderHardeningTest`、`RecorderCookieExpiredEmitTest`、`FfmpegResilienceTest` 等）必须零修改通过。

**Consequences**：
- exporter 新引入 `exporter-events.jsonl` audit contract —— orchestration-contracts.md / logging-guidelines.md 需扩。但 orchestrator 不消费，无下游状态机变化。
- 新 env var `ARL_EXPORTER_STDERR_RETAIN_COUNT`。
- recorder 内部数行代码搬走（`_capture_ffmpeg_failure` / `_extract_full_stderr` / `_build_stderr_excerpt` / `_write_stderr_log` / `_rotate_stderr_logs` / `_format_ffmpeg_failure_reason`），调用点改为 helper 函数；外部行为零变化。
- exporter ffmpeg 失败现在 capture_output=True，stderr 不再泄到 stdout（行为变化，但是正向）。

## Requirements

- **R1 (helper API)**：新 `src/arl/shared/ffmpeg_runner.py` 提供：
  - `@dataclass(frozen=True) class FfmpegAttemptOutcome`：`success: bool`、`reason: str | None`、`classification: FailureDecision | None`、`stderr_excerpt: str | None`、`stderr_log_path: str | None`
  - `run_ffmpeg_attempt(command, *, timeout, stderr_log_dir, stderr_log_basename, attempt) -> FfmpegAttemptOutcome`：内部 `subprocess.run(check=True, capture_output=True, text=True)`，失败时 capture + classify + write log；succeeded 时 outcome.success=True 其他字段 None
  - `rotate_stderr_logs(stderr_dir, retain_count)`：按 mtime 倒序保留前 N 个
- **R2 (recorder 切换)**：`_record_with_ffmpeg` / `_record_browser_capture_with_ffmpeg` 调用 `run_ffmpeg_attempt(...)` 拿 outcome；`_rotate_stderr_logs` 在 `run()` 启动改为调共享函数；删除 recorder 私有的 6 个 helper 方法。recorder audit 字段（`stderr_excerpt`、`stderr_log_path`）来自 outcome；byte-identical。
- **R3 (exporter 切换 + audit)**：`_write_export_with_ffmpeg` 改用 `run_ffmpeg_attempt(...)`，retry 循环保留 `attempts = ffmpeg_max_retries + 1`；每次失败 emit `ffmpeg_export_failed` audit 到 `data/tmp/exporter-events.jsonl`（含 canonical decision 字段 + stderr_excerpt + stderr_log_path）；成功 emit `ffmpeg_export_succeeded`；所有 attempt 耗尽 → placeholder fallback 时 emit `ffmpeg_export_fallback_placeholder`。
- **R4 (exporter stderr 落盘)**：失败时 stderr 写 `data/tmp/exporter-stderr/<session_id>_match<idx>-<attempt>.log`；`ExporterService.run()` 启动一次 `rotate_stderr_logs(retain=ARL_EXPORTER_STDERR_RETAIN_COUNT, default 200)`。
- **R5 (新 config)**：`ExportSettings.stderr_retain_count: int = 200`；从 `ARL_EXPORTER_STDERR_RETAIN_COUNT` 读取。
- **R6 (contracts)**：orchestration-contracts.md 添加 exporter-events.jsonl 段落；logging-guidelines.md `CORE_DECISION_EVENT_TYPES` 加 `ffmpeg_export_failed` / `ffmpeg_export_succeeded` / `ffmpeg_export_fallback_placeholder`；`failure_contracts.py` 的 `CORE_DECISION_EVENT_TYPES` 加同样三项。

## Acceptance Criteria

- [ ] `src/arl/shared/ffmpeg_runner.py` 提供 `run_ffmpeg_attempt` + `rotate_stderr_logs` + `FfmpegAttemptOutcome`；单元测试覆盖 success / 4xx 失败 / 5xx 失败 / timeout / no-stderr 五种主路径。
- [ ] recorder 切换后：`tests/pipeline/test_ffmpeg_resilience.py::RecorderHardeningTest` 全部用例零修改通过；`RecorderCookieExpiredEmitTest` 也零修改通过；recorder-events.jsonl 行字段 byte-identical（不含 created_at 时间戳）。
- [ ] recorder 私有的 6 个 helper 方法删除：`_capture_ffmpeg_failure` / `_extract_full_stderr` / `_build_stderr_excerpt` / `_write_stderr_log` / `_rotate_stderr_logs` / `_format_ffmpeg_failure_reason`。`_recorder_stderr_dir` property 改为指向 settings 路径。
- [ ] exporter 切换后：失败时 `data/tmp/exporter-events.jsonl` 出现一行 `ffmpeg_export_failed`，含 reason_code / decision / failure_category / is_retryable / reason_detail / stderr_excerpt / stderr_log_path。成功时出现 `ffmpeg_export_succeeded`。耗尽出现 `ffmpeg_export_fallback_placeholder`。
- [ ] exporter 失败时 `data/tmp/exporter-stderr/<session_id>_match<idx>-<attempt>.log` 存在且含完整 stderr；audit `stderr_log_path` 指向同路径。
- [ ] exporter 启动时 rotate `exporter-stderr/` 仅保留最近 N=`ARL_EXPORTER_STDERR_RETAIN_COUNT`（默认 200）。
- [ ] `failure_contracts.CORE_DECISION_EVENT_TYPES` / logging-guidelines.md / orchestration-contracts.md 同步加入新 event_type 与 exporter audit path 描述。
- [ ] pytest 全量绿（baseline 281 + helper 单测 ≥ 5 + exporter audit 测试 ≥ 5 + recorder 零回归）。

## Definition of Done

- 单元 + 集成测试覆盖 helper API 与两服务回归。
- `orchestration-contracts.md` 同步 exporter audit JSONL 段落。
- `logging-guidelines.md` `CORE_DECISION_EVENT_TYPES` 扩展。
- `README.md` 故障排查段落加 exporter ffmpeg 失败查 path（stderr 文件位置 + audit 字段）。
- 老 recorder-state.json / recorder-events.jsonl 零回归。

## Out of Scope

- 不改 exporter 输入/输出格式（mp4 编码参数、字幕滤镜不动）。
- 不引入 exporter cross-run retry（仍是 batch one-shot）。
- 不引入 exporter "non-retryable 立即 break"（D4：朴素 retry 行为不变）。
- 不解决"placeholder 输出后无法 rerun"（独立任务）。
- 不改 recorder 任何业务语义（仅 internal 代码重构）。
- 不让 orchestrator 消费 exporter audit（写-only，仅供 grep / 未来 recovery hook）。
- 不抽 subtitles / segmenter（不调 ffmpeg）。

## Technical Approach + PR Plan

**PR1 — helper 抽出 + recorder 切换 + 单测**

新 `src/arl/shared/ffmpeg_runner.py`：
- `FfmpegAttemptOutcome` dataclass
- `run_ffmpeg_attempt(command, *, timeout, stderr_log_dir, stderr_log_basename, attempt)`：subprocess.run + capture_output=True；succeeded → outcome.success=True；失败 → `_format_failure_reason(error)` + `_extract_full_stderr(error)` + 若 stderr 非空则 `_build_stderr_excerpt` + atomic write to `<stderr_log_dir>/<stderr_log_basename>-<attempt>.log` + `classify_failure_reason(reason)` → outcome
- `rotate_stderr_logs(stderr_dir, retain_count)`：按 mtime 倒序删超额

recorder 切换：
- `_record_with_ffmpeg` / `_record_browser_capture_with_ffmpeg`：subprocess.run + 当前 `_capture_ffmpeg_failure` 逻辑被 `run_ffmpeg_attempt(command, timeout=..., stderr_log_dir=self._recorder_stderr_dir, stderr_log_basename=job_id, attempt=attempt)` 单行替代；outcome.success 时 emit `ffmpeg_record_succeeded`；失败时按 outcome 字段拼 audit；保留 yield-on-transient break + cookie_expired emit。
- `run()` 启动：`rotate_stderr_logs(self._recorder_stderr_dir, self.settings.recording.stderr_retain_count)` 替代 `_rotate_stderr_logs`。
- 删除 6 个私有 helper 方法。

测试：
- `tests/test_ffmpeg_runner.py` 新建：success / 4xx / 5xx / timeout / no-stderr 五例。
- `RecorderHardeningTest` 等全部既有用例**零修改**通过（验证 byte-identical）。

**PR2 — exporter 切换 + audit + stderr 落盘 + contracts + README**

文件：
- `src/arl/config.py`：`ExportSettings.stderr_retain_count: int = 200` + `_load_export_settings` 读 env var。
- `src/arl/exporter/models.py`：新 `ExporterAuditEvent` Pydantic 模型，schema 跟 `RecorderAuditEvent` 同（event_type/session_id/match_index/decision/failure_category/is_retryable/reason_code/reason_detail/reason/attempt/max_attempts/stderr_excerpt/stderr_log_path/created_at + `validate_core_decision_fields` 校验）。注：用 `match_index` 替代 `job_id`。
- `src/arl/exporter/service.py`：
  - `self.audit_path = settings.storage.temp_dir / "exporter-events.jsonl"`
  - `run()` 启动 rotate exporter-stderr。
  - `_write_export_with_ffmpeg` 改 `for attempt in 1..attempts: outcome = run_ffmpeg_attempt(command, timeout=..., stderr_log_dir=..., stderr_log_basename=f"{session_id}_match{idx:02d}", attempt=attempt); if outcome.success → emit `ffmpeg_export_succeeded` + return；else emit `ffmpeg_export_failed`；耗尽 → emit `ffmpeg_export_fallback_placeholder` + return placeholder。
- `src/arl/shared/failure_contracts.py`：`CORE_DECISION_EVENT_TYPES` 加三新 event_type。
- `.trellis/spec/backend/orchestration-contracts.md`：新增 exporter audit JSONL 段落（writer = exporter；reader = grep-only；event_type registry；canonical fields；stderr_log_path 规则）。
- `.trellis/spec/backend/logging-guidelines.md`：`CORE_DECISION_EVENT_TYPES` 扩展三项。
- `README.md`：故障排查 / 后处理 段落补 exporter stderr 文件位置 + 新 env var。

测试：
- `tests/pipeline/test_exporter_ffmpeg_audit.py` 新建（或在 test_ffmpeg_resilience.py 加 ExporterAuditTest 类）：
  - 失败 → audit `ffmpeg_export_failed` 含 stderr_excerpt + stderr_log_path
  - 成功 → audit `ffmpeg_export_succeeded`
  - 耗尽 → `ffmpeg_export_fallback_placeholder`
  - rotation 在 N+5 测试
  - non-stderr failure（OSError） → audit 字段优雅 None

## Technical Notes

### 关键文件

- `src/arl/shared/ffmpeg_runner.py`（新）
- `src/arl/shared/failure_contracts.py`（CORE_DECISION_EVENT_TYPES 扩展）
- `src/arl/recorder/service.py` L457-743（切换调用点 + 删私有 helper）
- `src/arl/exporter/service.py` L123-177（切换 + audit emit）
- `src/arl/exporter/models.py`（新 `ExporterAuditEvent`）
- `src/arl/config.py`（`ExportSettings.stderr_retain_count`）
- `.trellis/spec/backend/orchestration-contracts.md`
- `.trellis/spec/backend/logging-guidelines.md`
- `README.md`

### 关联运行时数据

- 新 `data/tmp/exporter-events.jsonl`（write-only audit）
- 新 `data/tmp/exporter-stderr/<session>_match<idx>-<attempt>.log`
- 老 `data/tmp/recorder-events.jsonl` / `data/tmp/recorder-stderr/`：行/路径 byte-identical

### 关联近期工作

- 05-10 recorder hardening 留下的 Gap5；本任务上游。
- 05-12 recorder 403 cookie audit link 加了 `REASON_CODE_HTTP_403_FORBIDDEN`；helper 输出 `FailureDecision` 已含新 reason_code，无需特殊处理。

## Research References

无外部研究，全部基于本仓库代码 + 05-10 brief。
