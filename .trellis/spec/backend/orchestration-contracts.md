# Orchestration Contracts

> Executable contracts for the local Windows agent and orchestrator pipeline.

## Scenario: Local live-detection to recording-session orchestration

### 1. Scope / Trigger

- Trigger: The task introduces a cross-runtime pipeline between `windows_agent`, shared contracts, and `orchestrator`.
- Trigger: The implementation persists file-based contracts under `data/tmp/` and depends on stable event/state payloads.
- Trigger: Any change to `event_type`, payload shape, state file fields, or session/job lifecycle requires updating this document.

### 2. Signatures

- Shared enums in `src/arl/shared/contracts.py`:
  - `SourceType = {"direct_stream", "browser_capture"}`
  - `LiveState = {"offline", "live"}`
- Windows agent event payload in `src/arl/windows_agent/models.py`:

```python
class AgentSnapshot(BaseModel):
    state: LiveState
    streamer_name: str
    room_url: str
    source_type: SourceType | None = None
    stream_url: str | None = None
    reason: str | None = None
    detected_at: datetime

class AgentEvent(BaseModel):
    event_type: str  # "live_started" | "live_stopped"
    snapshot: AgentSnapshot
```

- Orchestrator input payload in `src/arl/orchestrator/models.py` must stay structurally compatible with the JSONL written by the Windows agent:

```python
class AgentEventPayload(BaseModel):
    event_type: str
    snapshot: AgentSnapshotPayload
```

- Durable orchestrator state in `src/arl/orchestrator/models.py`:

```python
class OrchestratorStateFile(BaseModel):
    cursor_offset: int = 0
    recorder_cursor_offset: int = 0
    recorder_last_event_at_by_job_id: dict[str, datetime]
    active_session_id: str | None = None
    active_recording_job_id: str | None = None
    sessions: list[SessionRecord]
    recording_jobs: list[RecordingJobRecord]
```

- Recording job failure metadata in `src/arl/orchestrator/models.py`:

```python
class RecordingJobRecord(BaseModel):
    # ...identity and lifecycle fields...
    failure_category: str | None = None
    recoverable: bool | None = None
    recovery_hint: str | None = None
```

- Durable file paths:
  - Windows agent event log: `data/tmp/windows-agent-events.jsonl`
  - Recorder audit event log: `data/tmp/recorder-events.jsonl`
  - Orchestrator state: `data/tmp/orchestrator-state.json`
  - Orchestrator audit log: `data/tmp/orchestrator-events.jsonl`
- Durable file encoding:
  - All durable state JSON files (`*-state.json`) and event/audit JSONL files (`*-events.jsonl`, `*-assets.jsonl`, `*-actions.jsonl`) must be read and written using explicit `encoding="utf-8"`. Bare `Path.read_text()` / `Path.write_text()` is forbidden — on non-UTF-8 OS locales (for example Windows zh-CN with CP936/GBK) it silently produces files that downstream stages cannot decode.
  - Rationale: stages run cross-locale (Windows agent on zh-CN host, recorder/orchestrator pipeline on the same host) and must agree on a single text encoding for file-based contracts.
  - For backward compatibility with files already written under the previous bare-encoding behavior, `OrchestratorStateStore.load` may auto-heal by attempting UTF-8 first and falling back to GBK before re-saving as UTF-8. New stages must not inherit this fallback.

### 3. Contracts

- `windows_agent` is the only writer of the agent event JSONL.
- `orchestrator` is the only reader of agent and recorder event JSONL cursors and the only writer of orchestrator state and audit logs.
- `recorder` and `recovery` must append recorder audit rows to the same configured path (`orchestrator.recorder_event_log_path`) so retry/recovery transitions are visible to orchestrator.
- Each event log line must be one complete JSON object. Partial multiline JSON is forbidden.
- `event_type` currently supports only:
  - `live_started`
  - `live_stopped`
- `live_started` contract:
  - `snapshot.state` must be `live`
  - `snapshot.streamer_name`, `snapshot.room_url`, and `snapshot.detected_at` are required
  - `snapshot.source_type` may be missing during degraded discovery, but should be set when known
  - `snapshot.stream_url` is optional and is used to enrich active sessions on duplicate start events
  - if `snapshot.source_type == "direct_stream"`, then `snapshot.stream_url` must be a non-empty `http(s)` URL
  - if no direct stream URL is discoverable, emit `snapshot.source_type == "browser_capture"` with `snapshot.stream_url == null`
  - direct-stream discovery should prefer `m3u8` over `flv` when both are available, and must ignore static asset URLs (`.js`, `.css`, image/font files)
  - direct-stream discovery may combine page HTML extraction and observed browser network URLs; when either channel yields a valid stream URL and no explicit offline marker is present, producer may emit `state=live` with `reason=stream_url_detected`
  - direct-stream candidate normalization should decode escaped and percent-encoded URL forms (for example `https%3A%2F%2F...m3u8`) before stream-url validation
  - normalization should also tolerate multi-layer percent-encoded payloads (for example `https%253A%252F%252F...`) and `\xNN`-escaped URL fragments that appear in script payloads
  - if Playwright probing fails (`playwright_script_missing`, `playwright_exec_error:*`, `playwright_error:*`), windows agent should fall back to HTTP page fetch detection instead of exiting early
  - HTTP fallback detection should extract stream URLs from escaped/encoded payload fields (`hls_pull_url`, `stream_url`, etc.); when a valid stream URL is found, producer may emit `state=live` with `source_type=direct_stream` and `reason=stream_url_detected_http`
  - malformed probe payloads must be normalized before emitting:
    - unknown `sourceType` with valid `streamUrl` → `source_type=direct_stream`
    - `sourceType=direct_stream` without valid `streamUrl` → `source_type=browser_capture`
- `live_stopped` contract:
  - `snapshot.state` must be `offline`
  - `snapshot.reason` should be populated when the stop cause is known
- State lifecycle contract:
  - one active live session at a time in MVP
  - one active recording job at a time in MVP
  - duplicate `live_started` for an active session must not create a second session or job
  - duplicate `live_started` may enrich `stream_url` on the active session when the first event lacked it
  - `live_stopped` closes the active session and active recording job if they exist
  - recorder audit events may transition recording job status:
    - `recording_retry_scheduled` -> `retrying` and re-open `active_recording_job_id` to that job
    - `recording_retry_exhausted`, `ffmpeg_skipped`, `ffmpeg_fallback_placeholder` -> `failed`
    - `ffmpeg_record_failed` -> `retrying` when failure is recoverable; otherwise `failed`
    - `ffmpeg_record_succeeded` after retry/failure -> `stopped`
  - when a recorder failure event is applied, orchestrator must persist:
    - `failure_category`
    - `recoverable`
    - `recovery_hint`
  - successful recorder completion (`ffmpeg_record_succeeded`) must clear failure metadata fields
  - orchestrator audit log must include recovery action routing:
    - `recording_job_recovery_retry_planned` for retry path
    - `recording_job_recovery_manual_required` for manual intervention path
  - recognized recorder transition events are applied monotonically per job by `created_at`; stale or duplicated timestamps must be ignored
  - unknown recorder event types must not advance monotonic per-job timestamps

### 4. Validation & Error Matrix

| Condition | Expected behavior |
|-----------|-------------------|
| Agent event log file does not exist | Treat as no events; do not fail the loop |
| Recorder event log file does not exist | Treat as no recorder events; do not fail the loop |
| Stored cursor is beyond current file size | Reset cursor to `0` and continue reading |
| JSONL line is blank | Skip silently |
| JSONL line is invalid JSON or fails Pydantic validation | Count as invalid line; continue processing later lines |
| Unknown `event_type` | Append audit event `ignored_unknown_event_type`; do not mutate active session/job |
| `live_started` arrives while an active session is open | Do not create a new session/job; append duplicate audit event |
| Duplicate `live_started` contains a new `stream_url` | Enrich active session `stream_url` before ignoring the duplicate |
| `live_started` carries `source_type=direct_stream` but `stream_url` is empty | Treat as producer contract violation in tests; producer must emit browser-capture fallback shape instead |
| `live_stopped` arrives with no active session | Append audit event `live_stopped_without_active_session`; do not fail |
| Recorder event references missing `job_id` or unknown job | Append audit event and skip without crashing |
| Recorder event has unknown `event_type` for a known job | Append `recorder_event_ignored`; do not mutate job state or advance per-job monotonic watermark |
| Recorder event log cursor exceeds file size | Reset recorder cursor to `0` and continue reading |
| Recorder event `created_at` is older than or equal to the last applied recorder event for the same job | Append `recorder_event_stale_ignored`; keep current job status unchanged |
| `ffmpeg_record_failed` classified recoverable | Keep job active in `retrying`; append retry recovery audit |
| `ffmpeg_record_failed` classified non-recoverable | Mark job `failed`, close active job pointer, append manual recovery audit |
| Orchestrator state file is encoded as a legacy non-UTF-8 codec (for example GBK from the previous bare-`write_text` behavior) | `OrchestratorStateStore.load` auto-heals by falling back to GBK decode; the next `save` rewrites the file as UTF-8 |
| Orchestrator state file is corrupt and cannot be decoded as UTF-8 or the GBK fallback | Raise a `RuntimeError` whose message includes the file path and instructs the operator to delete the file or convert it manually; do not silently lose state |

### 5. Good / Base / Bad Cases

- Good:
  - `windows_agent` emits `live_started` with `source_type=direct_stream` and a `stream_url`.
  - `orchestrator` creates one live session and one queued recording job.
  - later `live_stopped` closes both records and preserves stop metadata.
- Base:
  - `windows_agent` emits `live_started` with `source_type=browser_capture` and no `stream_url`.
  - `orchestrator` still creates the session and queued recording job.
- Bad:
  - `windows_agent` rewrites old events instead of appending JSONL lines.
  - `orchestrator` receives malformed JSON and blocks the whole loop.
  - `orchestrator` creates a second session for repeated `live_started` heartbeats.

### 6. Tests Required

- Unit test: Windows agent emits an event only when snapshot state meaningfully changes.
  - Assert unchanged snapshots do not append duplicate JSONL rows.
- Unit test: Orchestrator reads valid events from an offset and skips invalid rows.
  - Assert `invalid_lines` increments while later valid events still load.
- Unit test: Duplicate `live_started` is idempotent.
  - Assert exactly one active session and one recording job remain.
  - Assert `stream_url` enrichment works when the duplicate has more information.
- Unit test: direct-stream payload mapping contract from Playwright probe output.
  - Assert payload `{state=live, sourceType=direct_stream, streamUrl=<url>}` maps to snapshot with the same `source_type` and `stream_url`.
  - Assert payload `{state=live, sourceType=browser_capture, streamUrl=null}` keeps browser-capture fallback shape.
- Unit test: direct-stream URL extraction heuristic.
  - Assert escaped `m3u8` and `flv` candidates choose `m3u8`.
  - Assert percent-encoded stream URL candidates are decoded and recognized as direct-stream URLs.
  - Assert multi-layer percent-encoded (`%25`-wrapped) + `\xNN` escaped stream URL candidates are decoded and recognized as direct-stream URLs.
  - Assert static asset URLs are ignored.
  - Assert observed network URL candidates can promote unknown page state to `state=live` with `reason=stream_url_detected`.
- Unit test: windows-agent probe fallback path.
  - Assert `detect()` falls back to HTTP detection when Playwright returns probe-error reasons.
  - Assert HTTP fallback can decode escaped/encoded stream URL values into `source_type=direct_stream`.
- Unit test: `live_stopped` closes active session and job.
  - Assert `ended_at`, `status`, and `stop_reason` are persisted.
- Unit test: cursor reset after log truncation.
  - Assert reader resets to file start rather than silently missing new events.
- Unit test: recorder retry events change job status.
  - Assert `recording_retry_scheduled` marks job `retrying`.
  - Assert `recording_retry_exhausted` marks job `failed`.
  - Assert fresh `recording_retry_scheduled` after terminal failure re-opens `active_recording_job_id`.
- Unit test: stale recorder events are ignored.
  - Assert a later terminal event cannot be overwritten by an older retry event replay.
  - Assert a duplicate recorder event with the same `created_at` is ignored idempotently.
- Unit test: unknown recorder events are auditable but do not advance monotonic per-job timestamp.
  - Assert an unknown newer event cannot block a following known older event in the same replay window.
- Unit test: `ffmpeg_record_failed` classification routes job status by recoverability.
  - Assert transient failures stay `retrying`.
  - Assert non-recoverable failures become `failed`.
  - Assert HTTP 4xx failure reasons classify as non-recoverable.
- Unit test: recovery audit routing is emitted.
  - Assert retry path emits `recording_job_recovery_retry_planned`.
  - Assert terminal/manual path emits `recording_job_recovery_manual_required`.

### 7. Wrong vs Correct

#### Wrong

```python
event = {
    "event_type": "started",
    "snapshot": {"state": "live", "streamer": "foo"}
}
```

- Wrong event name
- Wrong field name `streamer`
- Missing required contract fields

#### Correct

```python
event = {
    "event_type": "live_started",
    "snapshot": {
        "state": "live",
        "streamer_name": "foo",
        "room_url": "https://live.douyin.com/foo",
        "source_type": "direct_stream",
        "stream_url": "https://live-play.example.com/abc123.m3u8",
        "reason": None,
        "detected_at": "2026-04-24T12:00:00Z",
    },
}
```

## Change Discipline

- Update this file before changing event payload fields, JSONL path conventions, or session/job lifecycle rules.
- Keep `src/arl/windows_agent/models.py` and `src/arl/orchestrator/models.py` structurally aligned.
- Prefer additive contract changes over breaking renames during MVP; if a rename is unavoidable, update both producer and consumer in the same change and extend tests first.

## Scenario: Post-recording media pipeline contracts

### 1. Scope / Trigger

- Trigger: The MVP task now explicitly includes recorder, segmenter, subtitle, and exporter stages after session detection.
- Trigger: Any new durable asset model, stage status enum, or handoff file path between these stages must be documented here before implementation.

### 2. Signatures

- Shared asset contracts currently available in `src/arl/shared/contracts.py`:

```python
class RecordingAsset(BaseModel):
    session_id: str
    source_type: SourceType
    path: str
    started_at: datetime
    ended_at: datetime | None = None

class MatchBoundary(BaseModel):
    session_id: str
    match_index: int
    started_at_seconds: float
    ended_at_seconds: float
    confidence: float

class MatchStageHint(BaseModel):
    session_id: str
    stage: MatchStage
    at_seconds: float | None = None
    detected_at: datetime | None = None

class MatchStageSignal(BaseModel):
    session_id: str
    text: str
    source: str = "manual"
    at_seconds: float | None = None
    detected_at: datetime | None = None

class SubtitleAsset(BaseModel):
    session_id: str
    match_index: int
    path: str
    format: str

class ExportAsset(BaseModel):
    session_id: str
    match_index: int
    path: str
    subtitle_path: str
    created_at: datetime
```

- Current file-backed manifests:
  - Recorder assets: `data/tmp/recording-assets.jsonl`
  - Recorder audit events: `data/tmp/recorder-events.jsonl`
  - Recorder recovery actions: `data/tmp/recorder-recovery-actions.jsonl`
  - Recovery dispatch events: `data/tmp/recovery-events.jsonl`
  - Recovery dispatch archive: `data/tmp/recovery-events-archive.jsonl`
  - Segment boundaries: `data/tmp/match-boundaries.jsonl`
  - Optional segment stage hints: `data/tmp/match-stage-hints.jsonl`
  - Optional segment stage signals: `data/tmp/match-stage-signals.jsonl`
  - Subtitle assets: `data/tmp/subtitle-assets.jsonl`
  - Export assets: `data/tmp/export-assets.jsonl`
  - Stage idempotency states: `data/tmp/recorder-state.json`, `data/tmp/recovery-state.json`, `data/tmp/segmenter-state.json`, `data/tmp/subtitles-state.json`, `data/tmp/exporter-state.json`, `data/tmp/stage-signal-ingest-state.json`
- Environment keys for ffmpeg-enabled paths:
  - `ARL_RECORDING_ENABLE_FFMPEG` (`0`/`1`, default `0`)
  - `ARL_DIRECT_STREAM_TIMEOUT_SECONDS` (int seconds, default `20`)
  - `ARL_RECORDING_FFMPEG_MAX_RETRIES` (int >= 0, default `1`)
  - `ARL_RECORDING_AUTO_RETRY_MAX_ATTEMPTS` (int >= 0, default `2`)
  - `ARL_BROWSER_CAPTURE_INPUT` (string, default empty)
- `ARL_BROWSER_CAPTURE_FORMAT` (string, default `auto`; resolves to `gdigrab` on Windows, `avfoundation` on macOS, `x11grab` on Linux/other)
  - `ARL_BROWSER_CAPTURE_RESOLUTION` (string, default `1920x1080`)
  - `ARL_BROWSER_CAPTURE_FPS` (int >= 1, default `30`)
  - `ARL_BROWSER_CAPTURE_TIMEOUT_SECONDS` (int >= 1, default `20`)
  - `ARL_EXPORT_ENABLE_FFMPEG` (`0`/`1`, default `0`)
  - `ARL_EXPORT_FFMPEG_PRESET` (string, default `veryfast`)
  - `ARL_EXPORT_FFMPEG_CRF` (int, default `23`)
  - `ARL_EXPORT_FFMPEG_TIMEOUT_SECONDS` (int seconds, default `120`)
  - `ARL_EXPORT_FFMPEG_MAX_RETRIES` (int >= 0, default `1`)
  - `ARL_SUBTITLES_ENABLED` (`0`/`1`, default `1`)
  - `ARL_SUBTITLE_PROVIDER` (string, default `faster-whisper`)
  - `ARL_WHISPER_MODEL_SIZE` (string, default `small`)
  - `ARL_SUBTITLE_LANGUAGE` (string, default `zh`)
  - `ARL_STAGE_KEYWORDS_PATH` (optional JSON file path for stage keyword overrides)
- CLI helper signature for manual stage-hint ingestion:
  - `arl stage-hint --session-id <id> --stage <unknown|champion_select|loading|in_game|post_game> (--at-seconds <float> | --detected-at <iso_datetime>)`
- CLI helper signature for heuristic auto stage-hint ingestion:
  - `arl stage-hints-auto`
- CLI helper signature for semantic auto stage-hint ingestion:
  - `arl stage-hints-semantic [--stage-keywords-path <json_path>]`
- CLI helper signature for manual stage-signal ingestion:
  - `arl stage-signal --session-id <id> --text <signal_text> [--source <source>] (--at-seconds <float> | --detected-at <iso_datetime>)`
- CLI helper signature for subtitle-driven stage-signal ingestion:
  - `arl stage-signals-from-subtitles [--stage-keywords-path <json_path>] [--force-reprocess] [--session-id <id>] [--session-ids <csv>] [--subtitle-path <path>] [--subtitle-paths <csv>] [--match-index <n>] [--match-indices <csv>]`
- CLI helper signature for subtitle generation (with best-effort signal ingest):
  - `arl subtitles [--stage-keywords-path <json_path>] [--session-id <id>] [--session-ids <csv>] [--match-index <n>] [--match-indices <csv>]`

### 3. Contracts

- `RecordingAsset.path` must point to the actual stored media file relative to project runtime paths or be an absolute local path; do not store opaque labels.
- `MatchBoundary` timestamps are relative to the beginning of the referenced recording asset, in seconds.
- `match_index` is 1-based within a session and must remain stable for downstream subtitle and export naming.
- Segmenter stage-hint contract:
  - `match-stage-hints.jsonl` is optional; when absent or unusable, segmenter must keep single-boundary fallback behavior.
  - only `stage == "in_game"` hints participate in match-boundary derivation.
  - segmenter accepts either `at_seconds` (preferred) or `detected_at` (relative to `RecordingAsset.started_at`) as hint timestamp.
  - out-of-range hints (`< 0` or `>= recording duration`) and non-`in_game` hints are ignored.
  - with valid in-game starts, boundaries are derived as `[start_i, start_{i+1})` and `[last_start, duration]` with sequential `match_index` and elevated confidence.
  - if no valid in-game starts remain after filtering, emit one fallback boundary `[0, duration]` with low confidence.
- CLI `stage-hint` ingestion contract:
  - command must append one typed `MatchStageHint` row to `match-stage-hints.jsonl`.
  - command requires exactly one timestamp source (`--at-seconds` or `--detected-at`).
  - `--detected-at` values without timezone are normalized to UTC.
- CLI `stage-hints-auto` ingestion contract:
  - command reads `recording-assets.jsonl` and appends `stage=in_game` hints into `match-stage-hints.jsonl` using duration + `recording.segment_minutes` heuristics.
  - for one recording asset, generated starts are `0, interval, 2*interval...` while `< duration`.
  - if a session already has any `in_game` hint, auto command skips generating additional hints for that session.
  - command is idempotent across repeated runs on unchanged manifests.
- CLI `stage-hints-semantic` ingestion contract:
  - supports optional `--stage-keywords-path` override; when provided, this CLI value takes precedence over `ARL_STAGE_KEYWORDS_PATH`.
  - command should run best-effort `stage-signals-from-subtitles` ingest before reading `match-stage-signals.jsonl`, so newly generated SRT assets can be considered without manual pre-step.
  - command reads `recording-assets.jsonl`; when signal rows exist for a session, semantic generation first attempts signal-driven stage classification from `match-stage-signals.jsonl`.
  - signal-driven stage classification recognizes stage markers from signal text and keeps chronological order with duplicate-stage collapse.
  - signal-driven path is accepted only when at least one classified `in_game` signal remains in-range after filtering.
  - when no usable `in_game` signal exists, command falls back to template generation.
  - template generation emits stage hints in order: `champion_select -> loading -> in_game -> post_game` per cycle (`recording.segment_minutes * 60`).
  - if a session already has any stage hint row, semantic command skips that session.
  - command is idempotent across repeated runs on unchanged manifests.
- CLI `stage-signal` ingestion contract:
  - command appends one typed `MatchStageSignal` row into `match-stage-signals.jsonl`.
  - command requires exactly one timestamp source (`--at-seconds` or `--detected-at`).
  - `--detected-at` values without timezone are normalized to UTC.
- CLI `stage-signals-from-subtitles` ingestion contract:
  - supports optional `--stage-keywords-path` override; when provided, this CLI value takes precedence over `ARL_STAGE_KEYWORDS_PATH`.
  - supports optional `--force-reprocess`; when enabled, command rescans already-processed subtitle rows.
  - supports optional targeting filters: `--session-id/--session-ids`, `--subtitle-path/--subtitle-paths`, and `--match-index/--match-indices`; when provided, command only scans subtitle assets matching all specified filter dimensions.
  - when filters are provided, command should emit filter summary observability (`total_assets`, `matched_assets`) before processing.
  - ingest summary should include `matched_assets`, `skipped_already_processed`, and `skipped_missing_subtitle` counters for operator diagnostics.
  - command reads `subtitle-assets.jsonl` and attempts semantic stage extraction from referenced SRT files.
  - subtitle cue timestamp parsing should accept both `HH:MM:SS,mmm` and `HH:MM:SS.mmm` forms for compatibility with mixed subtitle emitters.
  - stage extraction classifies subtitle cue text into `champion_select/loading/in_game/post_game` using keyword matching and appends `MatchStageSignal` rows with `source="subtitles_srt"`.
  - keyword matching should cover both English and Chinese LoL stage cues to avoid language-biased signal miss.
  - for one subtitle asset row, only the first cue per stage is emitted; emitted rows keep chronological order (`at_seconds` ascending, deterministic stage tie-break).
  - subtitle rows without any recognized stage text are still marked processed to keep reruns idempotent.
  - missing subtitle file paths are skipped without marking processed, so later reruns can ingest after file arrival.
  - command persists processed subtitle keys in `stage-signal-ingest-state.json` and must not duplicate signals on repeated runs over unchanged input.
  - ingest state should persist emitted signal fingerprints by subtitle key so `--force-reprocess` can append only newly discovered signals and skip previously emitted identical rows.
  - ingest run should compact stale state against current `subtitle-assets.jsonl` (drop keys/fingerprints for assets no longer present, dedupe empty/duplicate fingerprint rows) to control long-term state growth.
- CLI `subtitles` generation contract:
  - supports optional targeting filters: `--session-id/--session-ids` and `--match-index/--match-indices`; when provided, command only generates subtitle assets for `match-boundaries.jsonl` rows matching all supplied filter dimensions.
  - when filters are provided, command should emit filter summary observability (`total_boundaries`, `matched_boundaries`) before generation.
  - when filters are provided and no boundaries match, command should emit explicit no-match filter diagnostics and complete with `processed_matches=0` (no failure exit).
  - post-generation best-effort `stage-signals-from-subtitles` ingest should inherit the same `session_id/match_index` filter scope used by the subtitles run, so targeted generation does not trigger unrelated subtitle-asset scans.
- Stage keyword override contract (`ARL_STAGE_KEYWORDS_PATH`):
  - when configured and file exists, JSON payload may override per-stage keyword lists by keys: `champion_select`, `loading`, `in_game`, `post_game`.
  - project-maintained example: `examples/stage-keywords.example.json`.
  - each configured stage value should be a non-empty string array; invalid/missing stage entries fall back to built-in defaults.
  - override applies consistently to both subtitle signal extraction and semantic stage-hint signal classification.
  - on read/parse/schema issues, stage modules should emit explicit fallback logs and continue with built-in defaults.
  - precedence rule for commands that accept `--stage-keywords-path`: CLI arg > `ARL_STAGE_KEYWORDS_PATH` > built-in defaults.
- `SubtitleAsset.format` must be an explicit file format such as `srt` or `ass`, not a provider name.
- Recorder, segmenter, subtitles, and exporter must communicate through typed records and JSONL manifests, not inferred filenames alone.
- If a stage is not yet able to finish its real work, it may emit a stub or no-op result only if the status is explicit and downstream stages can detect it safely.
- Subtitle generation contract:
  - when `subtitles.provider == "faster-whisper"` and recording input is a transcribable media path, subtitles may be generated from ASR segments within each `MatchBoundary`
  - any provider mismatch, missing dependency/model initialization failure, unsupported recording suffix, or runtime transcribe error must degrade to deterministic placeholder SRT output
  - SRT output should use non-negative relative timestamps and monotonically increasing cue indices
  - after subtitle asset emission, subtitles stage should run best-effort `stage-signals-from-subtitles` ingestion to keep `match-stage-signals.jsonl` synchronized with latest SRT outputs
  - failures inside stage-signal ingest should be logged but must not fail subtitle asset emission
- `ffmpeg` execution paths are opt-in and controlled by config:
  - `ARL_RECORDING_ENABLE_FFMPEG=1`
  - `ARL_EXPORT_ENABLE_FFMPEG=1`
  - when disabled or prerequisites are missing, stages must degrade to deterministic placeholder artifacts instead of crashing.
- Recorder and exporter ffmpeg commands retry per configured max retries, then must degrade to deterministic placeholder artifacts.
- Recorder should append structured audit rows for ffmpeg control flow (`ffmpeg_skipped`, `ffmpeg_record_failed`, `ffmpeg_record_succeeded`, `ffmpeg_fallback_placeholder`) so retry decisions are observable.
- When ffmpeg fails with retryable reasons, recorder may defer placeholder emission and schedule cross-run retries:
  - scheduled event: `recording_retry_scheduled`
  - exhausted event: `recording_retry_exhausted`
  - max schedules controlled by `ARL_RECORDING_AUTO_RETRY_MAX_ATTEMPTS`
- Recorder must treat orchestrator `RecordingJobStatus.FAILED` as manual-recovery flow:
  - do not attempt ffmpeg or placeholder rebuild for failed jobs
  - clear stale retry counters for that job from `retry_attempts_by_job_id`
  - append `recording_manual_recovery_required` once per job (de-duplicated across runs)
  - manual-recovery routing must still execute even if the job id already exists in `processed_job_ids` from an earlier run
  - persist manual queue state in `recorder-state.json` as `manual_required_job_ids`
  - when `failure_category` is missing/unknown, recorder should infer actionable category from `stop_reason/recovery_hint` markers before selecting `action_type`/`steps`
  - append one structured recovery action row to `recorder-recovery-actions.jsonl` with:
    - `action_type`
    - `failure_category`
    - `recoverable`
    - `steps`
- If orchestrator re-opens a job as `RecordingJobStatus.RETRYING` with the same `job_id`, recorder must clear stale idempotency markers before processing:
  - remove the job id from `processed_job_ids`
  - remove the job id from `manual_required_job_ids`
  - then run normal recording flow for that job
- Recovery stage consumes `recorder-recovery-actions.jsonl` and dispatches pending manual actions:
  - append `manual_recovery_action_dispatched` rows to `recovery-events.jsonl`
  - persist processed-action idempotency keys in `recovery-state.json`
  - persist per-action status in `recovery-state.json` as `status_by_action_key`
  - dispatch rows must include `session_id`, `job_id`, `action_type`, `status=pending`, and a short operator message
  - `action_key` must remain unique across repeated actions even if `created_at` is identical; key derivation should avoid timestamp-only collisions
- Recovery status updates must support operator callbacks by `job_id`:
  - `resolved` path appends `manual_recovery_action_resolved` rows
  - `failed` path appends `manual_recovery_action_failed` rows
  - only actions currently in `pending` state may transition to terminal states
  - terminal status rows should include `action_key` for audit correlation, including batch status updates
- Recovery resolved transitions must trigger recorder requeue control:
  - append `recording_retry_scheduled` into `recorder-events.jsonl` only when all dispatched recovery actions for the job are `resolved`
  - if any dispatched recovery action for the job is still `pending` or is marked `failed`, do not emit requeue
  - readiness evaluation should use the latest effective action set for the job:
    - for repeated entries of the same `action_type`, only the newest action row participates in readiness gating
    - when repeated rows share identical `created_at`, treat later appended row as newer
    - older superseded history rows should remain auditable but must not permanently block newer resolved cycles
- Recovery status updates should also support precise callbacks by `action_key` for multi-action jobs.
  - action-key callback handlers should accept both current and legacy key shapes for compatibility during keying migrations
  - when a legacy key collides with multiple rows, callback targeting must be deterministic: choose the latest row by (`created_at`, then append order)
- Recovery stage should expose a pending-action query view for operator tooling (pending dispatched actions only).
- Recovery stage should expose an aggregated summary view with total/pending/resolved/failed counts and grouped breakdowns.
- Recovery stage should support batch job updates (multi-job resolve/fail in one operation).
- Recovery stage should provide maintenance to control file growth:
  - archive terminal recovery events into `recovery-events-archive.jsonl`
  - compact terminal actions out of `recorder-recovery-actions.jsonl`
  - compact terminal keys out of `recovery-state.json`
- Recorder `ffmpeg` path activation requires all of:
  - `recording.enable_ffmpeg == True`
  - `ffmpeg` available on PATH
  - and one source-specific prerequisite set:
    - direct stream mode: non-empty `stream_url`
    - browser capture mode: non-empty resolved capture input after format-specific defaults/fallbacks
- Exporter `ffmpeg` path activation requires all of:
  - `export.enable_ffmpeg == True`
  - matching `RecordingAsset` exists for the session
  - recording path has video-like extension and file exists
  - `ffmpeg` available on PATH

### 4. Validation & Error Matrix

| Condition | Expected behavior |
|-----------|-------------------|
| Recorder finishes with no output file | Mark the recording step failed or incomplete; do not emit a fake `RecordingAsset` |
| Segment boundary end is before start | Reject the boundary as invalid and surface it through logs or tests |
| Segment stage hints file is missing | Segmenter must emit one fallback boundary covering full duration |
| Segment stage hints include only non-`in_game` stages | Segmenter must emit one fallback boundary covering full duration |
| Segment stage hints include valid `in_game` starts | Segmenter emits one boundary per inferred match interval with stable ordering |
| Segment stage hints include out-of-range timestamps | Out-of-range entries are ignored; valid hints still produce boundaries |
| `arl stage-hint` is called without timestamp source | CLI parse should fail fast and avoid appending malformed hint rows |
| `arl stage-hint --detected-at` is provided without timezone | CLI normalizes timestamp to UTC before append |
| `arl stage-hints-auto` runs on a session that already has `in_game` hints | Command skips that session and avoids duplicate/competing anchors |
| `arl stage-hints-auto` runs repeatedly on unchanged files | Hints are not duplicated (idempotent behavior) |
| `arl stage-hints-semantic` runs on a session with existing hints | Command skips session and preserves existing manual/auto anchors |
| `arl stage-hints-semantic` runs repeatedly on unchanged files | Hints are not duplicated (idempotent behavior) |
| `arl stage-hints-semantic` processes short recordings | `in_game` timestamp must remain inside recording duration window |
| `arl stage-hints-semantic` runs with subtitle assets present but signals file not pre-seeded | Command first attempts subtitle-to-signal ingest, then applies signal-driven strategy when usable `in_game` signals become available |
| subtitle or manual signal text uses Chinese LoL cues (`英雄选择/加载/击杀/胜利`) | Stage classifier should map to expected semantic stages instead of falling back to template/no-op |
| Windows-agent probe payload contains multi-layer percent-encoded stream URL or `\xNN` escaped URL fragments | Normalization should decode payload and still classify `state=live` + `source_type=direct_stream` when resulting URL is valid |
| `ARL_STAGE_KEYWORDS_PATH` points to invalid/missing JSON | Stage classification should fall back to built-in keyword set without crashing |
| `ARL_STAGE_KEYWORDS_PATH` provides custom keyword lists | Subtitle signal extraction and semantic stage-hint classification should both use the overridden keywords |
| `ARL_STAGE_KEYWORDS_PATH` has invalid payload shape for one stage (for example non-list) | Module logs per-stage fallback and still applies valid stage overrides |
| `stage-hints-semantic` / `stage-signals-from-subtitles` / `subtitles` command sets `--stage-keywords-path` and env key also exists | Command should use CLI path (higher priority), then fall back to env/default behavior if CLI path invalid |
| Signal rows exist but none classify to `in_game` | Semantic generator falls back to template strategy |
| Signal rows include timestamps outside recording duration | Out-of-range signals are ignored before stage generation |
| `arl stage-signal` is called without timestamp source | CLI parse should fail fast and avoid malformed signal rows |
| `arl stage-signals-from-subtitles` sees a subtitle row whose `path` does not exist | Row is skipped and left unprocessed for a later rerun |
| `arl stage-signals-from-subtitles` sees subtitle cues with no recognized stage keywords | No signals are emitted, but subtitle row is marked processed to avoid repeated rescans |
| `arl stage-signals-from-subtitles` runs repeatedly on unchanged manifests/state | Signals are not duplicated (idempotent behavior) |
| `arl stage-signals-from-subtitles --force-reprocess` runs on unchanged subtitle content | Already-emitted identical signals are deduplicated; no duplicate append occurs |
| `arl stage-signals-from-subtitles --force-reprocess` runs after subtitle content adds a newly-recognized stage cue | Command appends only newly discovered signal rows while preserving existing history |
| `arl stage-signals-from-subtitles` runs with `--session-id/--subtitle-path` filters | Only matching subtitle assets are scanned; non-matching assets remain untouched for that run |
| `arl stage-signals-from-subtitles` runs with `--match-index/--match-indices` filters | Only subtitle assets with matching `match_index` are scanned; non-matching assets remain untouched for that run |
| `arl stage-signals-from-subtitles` runs with any filter flags | Command logs `filter summary total_assets=<n> matched_assets=<m>` to support operator diagnostics |
| `arl stage-signals-from-subtitles` completes with mixed outcomes (processed + skipped) | Summary log should expose `matched_assets`, `skipped_already_processed`, and `skipped_missing_subtitle` counters for transparent run accounting |
| `stage-signal-ingest-state.json` contains stale keys not present in current `subtitle-assets.jsonl` | Next ingest run compacts stale processed/fingerprint state entries and keeps only current asset keys |
| `stage-signals-from-subtitles` raises runtime error during `subtitles` stage run | Subtitle stage keeps emitted subtitle assets and logs stage-signal ingest skip reason |
| `arl subtitles` runs with `--session-id/--session-ids` and `--match-index/--match-indices` filters | Only matching `match-boundaries.jsonl` rows are processed, and filter dimensions are applied with intersection semantics |
| `arl subtitles` runs with any filter flags | Command logs `filters summary total_boundaries=<n> matched_boundaries=<m>` to support operator diagnostics |
| `arl subtitles` filtered run matches zero boundary rows | Command logs explicit no-match filter message and exits successfully with `processed_matches=0` |
| `arl subtitles` runs with filter flags and auto-triggers stage-signal ingest | Auto-triggered `stage-signals-from-subtitles` run inherits same `session_id/match_index` scope and should not process unrelated subtitle rows |
| Subtitle generation is disabled | Do not emit a `SubtitleAsset`; exporter must detect subtitle absence explicitly |
| Subtitle provider is unsupported for transcription | Emit deterministic placeholder SRT instead of failing the stage |
| Subtitle provider is `faster-whisper` but dependency/model is unavailable | Emit deterministic placeholder SRT and continue pipeline |
| Subtitle provider is `faster-whisper` but recording path is non-media (e.g., placeholder `.txt`) | Emit deterministic placeholder SRT and continue pipeline |
| Export input references a missing subtitle file | Fail the export step deterministically instead of silently skipping subtitle burn-in |
| A stage receives an unknown asset format or status | Reject or audit explicitly; do not guess |
| `ARL_RECORDING_ENABLE_FFMPEG=1` but `stream_url` missing | Recorder logs skip reason and writes placeholder recording artifact |
| `ARL_RECORDING_ENABLE_FFMPEG=1`, source is `browser_capture`, and resolved capture input is empty/unavailable | Recorder logs skip reason and writes placeholder recording artifact |
| ffmpeg fails with retryable reason and retry budget remains | Recorder emits `recording_retry_scheduled` and defers placeholder/asset emission until a later run |
| ffmpeg retry budget exhausted | Recorder emits `recording_retry_exhausted`, writes placeholder artifact, and emits recording asset |
| ffmpeg fails with clear HTTP 4xx input-side errors (`401/403/404/410`, `server returned 4xx`) | Treat as non-recoverable input/configuration failure; do not schedule cross-run retry; emit placeholder/manual path |
| ffmpeg fails with clear non-recoverable reason in the same run | Recorder should stop further in-run ffmpeg attempts immediately and proceed with fallback/manual path |
| Failed job has missing `failure_category` but recognizable `stop_reason` markers | Recorder should infer actionable category (for example `prerequisite` on HTTP 404) and avoid defaulting to generic inspect-only action |
| Orchestrator job status is `failed` | Recorder skips recording attempts, clears stale retry counters, emits `recording_manual_recovery_required` once, and appends one recovery action row even when job id already exists in `processed_job_ids` |
| Orchestrator re-opens the same job id as `retrying` after manual recovery | Recorder clears stale `processed_job_ids` and `manual_required_job_ids` entries for that job, then re-runs recording |
| Recorder sees a newly failed job for manual recovery | Recorder appends one recovery action row and avoids duplicating it while the job stays failed |
| Recovery stage runs repeatedly on unchanged action inputs | It must not duplicate already-dispatched action events |
| Recovery status update targets a job without pending actions | Do not append new status events; keep state unchanged |
| Recovery status update targets an unknown action key | Do not append new status events; keep state unchanged |
| Batch recovery status update contains known and unknown job ids | Update only known pending actions; unknown job ids must report zero updates |
| Batch recovery status updates append terminal events | Each terminal event should carry `action_key` for dispatch/status correlation |
| Multi-action job resolves only a subset of actions | Recovery should not emit `recording_retry_scheduled` until all dispatched actions for that job are `resolved` |
| Same job/action_type has older failed row plus newer resolved row | Requeue gating should evaluate only the newest row for that action type; superseded older row must not block |
| Same job/action_type rows share same `created_at` | Later appended row should win effective-action selection and keying must not collide |
| Operator submits legacy-format `action_key` after keying upgrade | Recovery should still resolve/fail the intended pending action when it maps uniquely |
| Operator submits legacy-format `action_key` that collides across multiple rows | Recovery should deterministically target the latest collided row (`created_at`, then append order) and apply status change only when that row is still `pending` |
| Recovery maintenance runs after all actions become terminal | Terminal actions/events are archived/compacted; active files keep only non-terminal or empty state |
| `ARL_EXPORT_ENABLE_FFMPEG=1` but recording input is not a video file | Exporter writes placeholder export artifact and keeps pipeline progress |
| `ffmpeg` command exits non-zero | Stage logs failure reason and falls back to deterministic placeholder artifact |

### 5. Good / Base / Bad Cases

- Good:
  - Recorder emits one `RecordingAsset`, segmenter emits two `MatchBoundary` rows, subtitles emits one `SubtitleAsset` per match, exporter writes final output with stable naming.
- Base:
  - Recorder succeeds, segmenter emits one low-confidence match boundary, export is deferred pending operator review.
- Bad:
  - Exporter guesses `match_index` from filenames instead of reading typed metadata.
  - Segmenter emits negative or overlapping timestamps without validation.
  - Recorder writes files but never records their source type or time bounds.
  - `ffmpeg` failure aborts the whole pipeline and prevents manifest emission.

### 6. Tests Required

- Unit test: recorder manifest or asset output includes source type, path, and start and end timestamps.
- Unit test: segment boundary validation rejects negative or reversed ranges.
- Unit test: segmenter derives multi-match boundaries from `in_game` stage hints and keeps `match_index` sequential.
- Unit test: segmenter accepts `detected_at` hints by converting them relative to recording start.
- Unit test: segmenter preserves idempotency and does not duplicate boundaries on rerun.
- Unit test: segmenter keeps single-boundary fallback when hints are missing or unusable.
- Unit test: stage-hint writer appends typed rows for both `at_seconds` and `detected_at` input shapes.
- Unit test: stage-hint CLI parser enforces timestamp input and rejects invalid datetime formats.
- Unit test: auto stage-hint service derives periodic `in_game` anchors from recording duration and segment interval.
- Unit test: auto stage-hint service remains idempotent across repeated runs.
- Unit test: auto stage-hint service skips sessions that already have `in_game` hints.
- Unit test: semantic stage-hint service emits per-cycle stage sequence (`champion_select/loading/in_game/post_game`).
- Unit test: semantic stage-hint service remains idempotent across repeated runs.
- Unit test: semantic stage-hint service skips sessions that already have stage hints.
- Unit test: semantic stage-hint service keeps `in_game` timestamp inside duration for short recordings.
- Unit test: semantic stage-hint service uses signal-driven generation when classified signals include `in_game`.
- Unit test: semantic stage-hint service falls back to template generation when signals do not contain usable `in_game`.
- Unit test: semantic stage-hint service converts `detected_at` signals to relative seconds and ignores out-of-range signals.
- Unit test: semantic stage-hint service can auto-ingest subtitle-derived signals and emit signal-driven hints without manual `stage-signal` pre-write.
- Unit test: semantic stage-hint service can map Chinese signal text into the expected stage sequence.
- Unit test: semantic stage-hint service can consume stage signals via external keyword override file.
- Unit test: stage-signal writer appends typed rows for both `at_seconds` and `detected_at` input shapes.
- Unit test: stage-signal CLI parser enforces timestamp input.
- Unit test: `stage-signals-from-subtitles` extracts first-per-stage signal rows from SRT cues and preserves chronological order.
- Unit test: `stage-signals-from-subtitles` accepts dot-separated cue timestamps (`HH:MM:SS.mmm`) in addition to comma-separated SRT timestamps.
- Unit test: `stage-signals-from-subtitles` extracts first-per-stage signal rows from Chinese SRT cues.
- Unit test: `stage-signals-from-subtitles` remains idempotent across repeated runs via `stage-signal-ingest-state.json`.
- Unit test: `stage-signals-from-subtitles` marks unmatched subtitle rows processed while emitting zero signal rows.
- Unit test: `stage-signals-from-subtitles` skips missing subtitle paths without marking processed.
- Unit test: `stage-signals-from-subtitles` can classify cues through external keyword override file.
- Unit test: `stage-signals-from-subtitles` logs invalid keyword override payload and falls back without interrupting signal extraction.
- Unit test: CLI `stage-signals-from-subtitles` end-to-end run supports combined filters + `--force-reprocess`, and appends only targeted session/path signals into manifests.
- Unit test: `stage-signals-from-subtitles` supports `match_index` filter and filter-dimension intersection with session/path constraints.
- Unit test: stage text classifier maps English and Chinese stage cues and rejects unmatched text.
- Unit test: stage keyword loader logs missing/invalid payload cases and keeps default behavior.
- Unit test: stage-hint CLI parser includes `stage-signals-from-subtitles` command route.
- Unit test: subtitle service auto-triggers stage-signal ingest and keeps ingested signals idempotent across reruns.
- Unit test: subtitle asset format and path are preserved exactly for exporter handoff.
- Unit test: subtitle service degrades to placeholder SRT when provider is unsupported or transcription preconditions fail.
- Unit test: subtitle service writes cue-indexed SRT rows from provided transcription entries.
- Unit test: subtitle service supports session-id and match-index filters (single/CSV merged) with intersection semantics and filter observability logs.
- Unit test: subtitle service filtered no-match runs emit explicit no-match diagnostics and keep zero-result summary.
- Unit test: CLI `subtitles` end-to-end run supports combined `session-id/session-ids` + `match-index/match-indices` filters and only emits targeted subtitle assets.
- Unit test: subtitle service auto-triggered stage-signal ingest inherits subtitles filter scope (`session_id`/`match_index`) and avoids unrelated subtitle scans.
- Unit test: exporter refuses to burn subtitles when the declared subtitle file is missing.
- Unit test: recorder with `enable_ffmpeg=True` but missing `stream_url` still emits one placeholder asset.
- Unit test: recorder treats ffmpeg HTTP 4xx failures as non-recoverable and skips cross-run retry scheduling.
- Unit test: recorder stops in-run ffmpeg retries early when a non-recoverable reason is detected.
- Unit test: recorder infers actionable manual-recovery action mapping from `stop_reason` when `failure_category` is missing, and keeps inspect fallback only for opaque reasons.
- Unit test: exporter with `enable_ffmpeg=True` and non-video recording input still emits deterministic placeholder export.
- Unit test: recorder sees failed orchestrator job and emits one de-duplicated `recording_manual_recovery_required` audit row.
- Unit test: recorder still emits manual recovery routing when a failed job id is already present in `processed_job_ids`.
- Pipeline regression test: recorder placeholder success followed by orchestrator failure transition still triggers manual recovery routing on next recorder run.
- Unit test: recorder writes one de-duplicated `recorder-recovery-actions.jsonl` action row for a failed job.
- Unit test: recorder re-opens processing when a previously processed job transitions to `retrying`, and clears stale `manual_required_job_ids`.
- Unit test: recorder audit events respect `orchestrator.recorder_event_log_path` when the path differs from `storage.temp_dir`.
- Unit test: recovery stage dispatches each manual action once and remains idempotent across repeated runs.
- Unit test: recovery stage can mark pending actions `resolved` and `failed`, and must not re-transition terminal actions.
- Unit test: recovery stage can list pending dispatched actions and support `action_key`-based terminal updates.
- Unit test: recovery stage summary and batch job updates return correct aggregated counts and per-job update results.
- Unit test: recovery stage batch terminal status events include `action_key` for every updated action.
- Unit test: recovery resolved transitions emit one `recording_retry_scheduled` only when all dispatched actions for that job are `resolved`.
- Unit test: newer resolved action cycle for same `job_id` and `action_type` can requeue even when older superseded cycle has failed history.
- Unit test: same-timestamp repeated action cycle (`job_id` + `action_type` + equal `created_at`) can still dispatch and resolve later row, and requeue based on later appended row.
- Unit test: `mark_action_resolved/failed` accepts legacy-format `action_key` and preserves expected status transition + requeue behavior.
- Unit test: when legacy-format `action_key` collides across same-timestamp rows, callback deterministically targets the latest row and can still trigger expected requeue behavior.
- Unit test: recovery stage maintenance archives terminal events and compacts terminal actions/state.

### 7. Wrong vs Correct

#### Wrong

```python
settings = {"ARL_RECORDING_ENABLE_FFMPEG": "1"}
stream_url = None
raise RuntimeError("stream_url required")
```

- Crashes pipeline on missing runtime prerequisites
- Ignores required degrade-to-placeholder contract

#### Correct

```python
if enable_ffmpeg and stream_url and ffmpeg_exists:
    run_ffmpeg(...)
else:
    write_placeholder_artifact(...)
append_manifest_record(...)
```
