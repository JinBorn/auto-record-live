# Logging Guidelines

> How logging is done in this project.

---

## Overview

<!--
Document your project's logging conventions here.

Questions to answer:
- What logging library do you use?
- What are the log levels and when to use each?
- What should be logged?
- What should NOT be logged (PII, secrets)?
-->

The current MVP uses the helper in `src/arl/shared/logging.py`:

```python
log(component, message)
```

This prints plain stdout lines in the format `[component] message`.

Until the project adopts structured logging, log lines must stay compact and machine-greppable.

---

## Log Levels

<!-- When to use each level: debug, info, warn, error -->

- The current helper does not encode explicit levels, so severity is implied by message content.
- Use neutral action-oriented messages for normal lifecycle events such as `starting`, `processed events=2 cursor=381`, and `recording job queued job_id=...`.
- Prefix anomalous but recoverable conditions with a clear reason phrase such as `ignored unknown event_type=...`, `skipped invalid event lines=3`, and `input event log was truncated; cursor reset to file start`.
- If a future change adds explicit levels, preserve the current component names and message shapes where possible.

---

## Structured Logging

<!-- Log format, required fields -->

- Always pass a stable component name as the first argument:
  - `windows-agent`
  - `orchestrator`
  - `recorder`
  - `segmenter`
  - `subtitles`
  - `exporter`
- Put variable fields in `key=value` form inside the message when useful.
- Prefer one-line messages. Do not dump multiline payloads into stdout logs.
- Use audit JSONL for durable machine-readable lifecycle history; use stdout logs for operator-facing progress.

### Canonical Decision Contract (Core Events)

For these events only:

- `recording_retry_scheduled`
- `ffmpeg_record_failed`
- `ffmpeg_fallback_placeholder`
- `recording_manual_recovery_required`
- `manual_recovery_action_dispatched`
- `manual_recovery_action_resolved`
- `manual_recovery_action_failed`

`recorder-events.jsonl` / `recovery-events.jsonl` payloads must include all fields below:

- `decision`
- `failure_category`
- `is_retryable`
- `reason_code`
- `reason_detail`

`reason_code` must be strict enum:

- `http_4xx`
- `http_5xx`
- `network_timeout`
- `ffmpeg_process_error`
- `unknown_unclassified`

`failure_category` must be one of:

- `http_4xx_non_retryable`
- `http_5xx_retryable`
- `network_timeout_retryable`
- `ffmpeg_process_error_retryable`
- `unknown_unclassified_non_retryable`

Migration mapping (legacy -> canonical):

- `reason` -> `reason_detail` (legacy `reason` is no longer canonical source-of-truth)
- `recoverable` -> `is_retryable`
- free-form reason tokens -> strict `reason_code` enum

Unknown-classification rule:

- fallback must be fail-closed:
  - `reason_code=unknown_unclassified`
  - `failure_category=unknown_unclassified_non_retryable`
  - `is_retryable=false`
  - route to manual recovery path (no auto retry)

## Scenario: Core Decision Event Canonicalization (2026-04-28)

### 1. Scope / Trigger
- Trigger: recorder/recovery/orchestrator shared core events changed to strict canonical fields, which is a cross-layer contract change.

### 2. Signatures
- Recorder audit payload (`recorder-events.jsonl`): core event rows MUST include canonical decision fields.
- Recovery dispatch payload (`recovery-events.jsonl`): `manual_recovery_action_*` rows MUST include canonical decision fields.
- Orchestrator recorder-event parser: core event rows without canonical fields are invalid input rows.

### 3. Contracts
- Required fields on core events:
  - `decision: str`
  - `failure_category: str` (5-category enum)
  - `is_retryable: bool`
  - `reason_code: str` (strict enum)
  - `reason_detail: str`
- `reason_code` enum: `http_4xx | http_5xx | network_timeout | ffmpeg_process_error | unknown_unclassified`
- `failure_category` enum:
  - `http_4xx_non_retryable`
  - `http_5xx_retryable`
  - `network_timeout_retryable`
  - `ffmpeg_process_error_retryable`
  - `unknown_unclassified_non_retryable`

### 4. Validation & Error Matrix
- Missing any canonical field on core event -> reject row as invalid event.
- `reason_code` outside enum -> reject row as invalid event.
- `failure_category` outside enum -> reject row as invalid event.
- Unknown/unclassifiable reason -> force fail-closed fallback category/reason_code/retryability.

### 5. Good/Base/Bad Cases
- Good: HTTP 404 -> `reason_code=http_4xx`, `failure_category=http_4xx_non_retryable`, `is_retryable=false`.
- Base: `exit_status:1` -> `reason_code=ffmpeg_process_error`, `failure_category=ffmpeg_process_error_retryable`, `is_retryable=true`.
- Bad: free-form `reason_code=missing_binary` or legacy-only `recoverable` without canonical fields.

### 6. Tests Required
- Unit: `tests/orchestrator/test_service.py` validates core-event ingestion/state transitions with canonical fields.
- Unit: `tests/pipeline/test_recovery_service.py` validates recovery dispatch/resolution events satisfy canonical contract.
- Integration-ish pipeline: `tests/pipeline/test_ffmpeg_resilience.py` validates retry/manual/fallback paths match canonical taxonomy.

### 7. Wrong vs Correct
#### Wrong
- Emit `manual_recovery_action_dispatched` with `reason_code=missing_binary` and no `is_retryable`.

#### Correct
- Emit `manual_recovery_action_dispatched` with canonical fields, and `reason_code` mapped via classifier to strict enum.

### Preferred Message Shapes

- `session started session_id=<id> source=<source>`
- `recording job queued job_id=<id> session_id=<id>`
- `processed events=<count> cursor=<offset>`
- `subtitle asset written session_id=<id> match_index=<n> format=<fmt>`
- `ffmpeg export failed session_id=<id> match_index=<n> attempt=<i>/<n> reason=<error>`
- `ffmpeg record failed session_id=<id> attempt=<i>/<n> reason=<error>`

---

## What to Log

<!-- Important events to log -->

- Service start and key configuration choices that affect runtime behavior.
- State transitions: live detected, session created or stopped, recording job created or stopped, segment emitted, subtitle asset emitted, export completed.
- Recovery paths and degraded behavior: browser capture fallback selected, malformed row skipped, file truncation detected, duplicate event ignored.
- For retryable external-process failures, include attempt counters (`attempt=i/n`) so retries are observable.
- External process intent and result summaries, not full verbose command output.

### Fallback Logging Convention

- Do not emit misleading "skip reason" logs after a failed ffmpeg execution path.
- Once ffmpeg was attempted and failed, log:
  - per-attempt failure lines
  - one explicit fallback line
- Reserve "skipped reason=..." logs for paths where ffmpeg was not attempted due to unmet prerequisites.

---

## What NOT to Log

<!-- Sensitive data, PII, secrets -->

- Do not log cookies, auth headers, browser storage contents, or full persistent profile internals.
- Do not log full raw transcript text by default.
- Do not log full stream URLs if they are signed secrets; prefer source type plus a redacted hint when needed.
- Do not print whole state JSON files on every poll.
- Do not rely on stdout logs alone when the event must remain queryable later; add an audit event instead.
