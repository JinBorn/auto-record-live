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
    stream_headers: dict[str, str] = {}  # platform-specific HTTP headers (e.g. Bilibili Referer)
    reason: str | None = None
    detected_at: datetime
    platform: str = "douyin"  # registered key in PROBE_REGISTRY; default for back-compat

class AgentEvent(BaseModel):
    event_type: str  # "live_started" | "live_stopped"
    snapshot: AgentSnapshot
```

- Orchestrator input payload in `src/arl/orchestrator/models.py` must stay structurally compatible with the JSONL written by the Windows agent:

```python
class AgentSnapshotPayload(BaseModel):
    state: LiveState
    streamer_name: str
    room_url: str
    source_type: SourceType | None = None
    stream_url: str | None = None
    stream_headers: dict[str, str] = {}
    reason: str | None = None
    detected_at: datetime
    platform: str = "douyin"  # default lets pre-PR1 jsonl rows load cleanly

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
    # Per-platform active id maps. Multi-platform deployments need each
    # platform to track its own active session/job independently; otherwise a
    # live_started on one platform would supersede the other's already-live
    # session.
    active_session_id_by_platform: dict[str, str]
    active_recording_job_id_by_platform: dict[str, str]
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

- Multi-platform routing fields in `src/arl/orchestrator/models.py`:

```python
class SessionRecord(BaseModel):
    # ...identity and lifecycle fields...
    platform: str = "douyin"  # propagated from AgentSnapshotPayload.platform
    stream_headers: dict[str, str] = {}  # propagated from snapshot for downstream recorder

class RecordingJobRecord(BaseModel):
    # ...identity and lifecycle fields...
    platform: str = "douyin"
    stream_headers: dict[str, str] = {}  # consumed by RecorderService when invoking ffmpeg
```

- Durable file paths:
  - Windows agent event log: `data/tmp/windows-agent-events.jsonl`
  - Recorder audit event log: `data/tmp/recorder-events.jsonl`
  - Exporter audit event log: `data/tmp/exporter-events.jsonl`
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
  - `cookie_expired_for_<platform>` (informational; one of the registered platforms in `PROBE_REGISTRY`, e.g. `cookie_expired_for_bilibili`, `cookie_expired_for_douyin`). Two writers share this event type: windows-agent probes (probe-time detection) and recorder (record-time 403 detection). Both end up on `orchestrator-events.jsonl` so consumers can grep both sources with one pattern.
  - `stream_url_expired_for_<platform>` (recorder-side informational; currently emitted by the Bilibili direct-stream 403 path when the signed stream URL is stale or could not be refreshed)
- `live_started` contract:
  - `snapshot.state` must be `live`
  - `snapshot.streamer_name`, `snapshot.room_url`, and `snapshot.detected_at` are required
  - `snapshot.platform` may be omitted (defaults to `"douyin"` for pre-PR1 jsonl back-compat); when explicitly set it must match a registered `PROBE_REGISTRY` key (currently `"douyin"` or `"bilibili"`)
  - `snapshot.stream_headers` may be omitted (defaults to `{}`); non-empty values are HTTP headers that must reach the recorder's ffmpeg invocation. Bilibili always sets `Referer: https://live.bilibili.com` + `User-Agent`; both probes additionally inject `Cookie: ...` when their auth env var is configured (`ARL_BILIBILI_SESSDATA` adds `Cookie: SESSDATA=<value>` to bilibili snapshots; `ARL_DOUYIN_COOKIE` adds the raw cookie header value to douyin snapshots). Empty / unset auth env vars keep the legacy contract: bilibili emits Referer+UA only; douyin emits `{}`.
  - `snapshot.source_type` may be missing during degraded discovery, but should be set when known
  - `snapshot.stream_url` is optional and is used to enrich active sessions on duplicate start events
  - if `snapshot.source_type == "direct_stream"`, then `snapshot.stream_url` must be a non-empty `http(s)` URL
  - if no direct stream URL is discoverable, emit `snapshot.source_type == "browser_capture"` with `snapshot.stream_url == null`
  - direct-stream discovery should prefer `m3u8` over `flv` when both are available, and must ignore static asset URLs (`.js`, `.css`, image/font files)
  - strict quality gating applies before emitting `state=live` for direct-stream snapshots:
    - Douyin: selected stream URL must satisfy `DouyinSettings.min_quality_tier` (default `uhd`, i.e. 1080p-grade); lower tiers (`hd/sd/md/ld`) and tier-unknown URLs are treated as unavailable (`state=offline` with quality reason)
    - Bilibili: selected playinfo candidate must satisfy `BilibiliSettings.min_stream_qn` (default `400`, i.e. 1080p baseline); candidates below threshold are treated as unavailable (`state=offline` with quality reason)
    - Bilibili bitrate gate (when metadata exists): if codec payload exposes `bandwidth`/`bitrate`/`bit_rate`, candidate must satisfy `BilibiliSettings.min_stream_bitrate_kbps` (default `4500`) or be treated as unavailable
  - direct-stream discovery may combine page HTML extraction, live-marker detection, and observed browser network URLs. Douyin must treat explicit offline markers as higher priority than stream URLs. Page HTML / JSON payload stream URLs alone are not sufficient live evidence because offline rooms can retain stale signed URLs; they may only enrich a snapshot after an explicit live marker is found.
  - Douyin Playwright probing may promote unknown page state to `state=live` with `reason=stream_url_detected` only when the valid stream URL came from an actual observed browser request/response URL. Payload body URLs without a live marker must emit `state=offline` with `reason=stream_url_without_live_marker`.
  - direct-stream candidate normalization should decode escaped and percent-encoded URL forms (for example `https%3A%2F%2F...m3u8`) before stream-url validation
  - normalization should also tolerate multi-layer percent-encoded payloads (for example `https%253A%252F%252F...`) and `\xNN`-escaped URL fragments that appear in script payloads
  - if Playwright probing fails (`playwright_script_missing`, `playwright_exec_error:*`, `playwright_error:*`), windows agent should fall back to HTTP page fetch detection instead of exiting early
  - HTTP fallback detection should extract stream URLs from escaped/encoded payload fields (`hls_pull_url`, `stream_url`, etc.) for direct-stream enrichment only when a reliable live marker is present. If the HTTP page contains a valid stream URL but no live marker, emit `state=offline` with `reason=stream_url_without_live_marker`.
  - malformed probe payloads must be normalized before emitting:
    - unknown `sourceType` with valid `streamUrl` → `source_type=direct_stream`
    - `sourceType=direct_stream` without valid `streamUrl` → `source_type=browser_capture`
- `live_stopped` contract:
  - `snapshot.state` must be `offline`
  - `snapshot.reason` should be populated when the stop cause is known
- `cookie_expired_for_<platform>` contract:
  - `arl cookie-health` is credential-scoped, not room-scoped: when multiple configured rooms share the same platform credential (for example one Bilibili `SESSDATA` across several Bilibili rooms), the command MUST build only one representative probe for that `(platform, credential)` pair. It should prefer a room whose latest windows-agent snapshot is `live`, because live pages expose the strongest cookie validation signal; when no live-room hint exists, it falls back to the first configured room in `Settings.platforms`. If the same platform has genuinely different credential values, each distinct credential is checked separately.
  - Emitted by the windows agent in addition to (not instead of) the underlying `live_started`/`live_stopped` event when `PlatformProbe.classify_cookie_state(snapshot)` returns `expired` AND the snapshot has just transitioned (`_has_changed` returned True). High-confidence detection only:
    - Bilibili: `BilibiliSettings.sessdata` is non-empty AND `snapshot.reason` starts with `api_error:code=-101` or `playinfo_error:api_error:code=-101`
    - Douyin: `DouyinSettings.cookie` is non-empty AND `snapshot.reason` starts with `quality_below_min_tier:hd<` (the anonymous baseline tier)
  - Also emitted by the recorder, alongside (not instead of) the underlying `ffmpeg_record_failed` audit row, when the failure classifier returns `reason_code="http_403_forbidden"` AND the operator opted into cookie-based auth for that platform. Douyin recorder-side 403 uses the existing cookie-config gate (`DouyinSettings.cookie != ""`). Bilibili recorder-side 403 MUST first run a same-room `BilibiliRoomProbe`; only when `classify_cookie_state(snapshot) == expired` may it emit `cookie_expired_for_bilibili` with `reason` beginning `sessdata_expired:`.
    - Bilibili note: the stream URL token returned by `getRoomPlayInfo` is short-lived and SESSDATA-independent. A 403 on a stale token MUST NOT be treated as SESSDATA expiry when the follow-up probe still classifies the cookie as fresh.
  - `<platform>` MUST equal a registered `PROBE_REGISTRY` key. Unknown platforms must not produce this event.
  - The probe MUST NOT emit this event when the relevant cookie env var is unset, regardless of the snapshot's reason — there is no cookie to call expired. The recorder MUST apply the same gate before emitting from the 403 path.
  - The accompanying snapshot (probe path) is the same payload emitted with the underlying event; no extra fields are required. The recorder-path row carries `session_id`, `job_id`, `source_type`, and `reason` only — all canonical decision fields (`decision`, `failure_category`, `is_retryable`, `reason_code`, `reason_detail`) MUST be omitted/`null` since this row is informational, not a core decision event.
  - Orchestrator: `_handle_event` MUST route any agent-side `cookie_expired_for_<platform>` event to the audit log; `_handle_recorder_event` MUST route any recorder-side `cookie_expired_for_<platform>` event to the audit log without classifying it as `recorder_event_ignored` and without advancing the per-job monotonic watermark (so the accompanying `ffmpeg_record_failed` at the same `created_at` is not skipped as stale). Neither path mutates session/job state.
- `stream_url_expired_for_<platform>` contract:
  - Emitted by the recorder after a direct-stream Bilibili ffmpeg 403 when the follow-up Bilibili probe does not classify SESSDATA as expired.
  - If the follow-up probe returns `state=live`, `source_type=direct_stream`, and a non-empty `stream_url`, recorder emits `stream_url_expired_for_bilibili` with `reason="refreshed_stream_url_after_403"`, rebuilds the ffmpeg command with the refreshed URL and headers, and retries once in the same recorder run.
  - If the follow-up probe cannot provide a direct stream URL, recorder emits `stream_url_expired_for_bilibili` with `reason` beginning `refresh_failed:` and continues the normal failure/fallback path.
  - The row carries `session_id`, `job_id`, `source_type`, and `reason` only; all canonical decision fields MUST be omitted/`null` because this is diagnostic telemetry, not a core decision event.
  - Orchestrator MUST route recorder-side `stream_url_expired_for_<platform>` rows to audit only, without classifying them as `recorder_event_ignored`, without advancing the per-job monotonic watermark, and without mutating session/job state.
- State lifecycle contract:
  - one active live session per monitored stream key (`active_session_id_by_platform["<platform>:<room_url>"]`); cross-platform and same-platform multi-room sessions coexist
  - one active recording job per monitored stream key (`active_recording_job_id_by_platform["<platform>:<room_url>"]`); cross-platform and same-platform multi-room jobs coexist
  - duplicate `live_started` for the same `(platform, room_url)` enriches the active session in place — must not create a second session/job, may update `stream_url` from `None` → known, and refreshes `stream_headers` from the latest snapshot (so probe-side token rotation propagates without a session restart)
  - `live_started` from the same platform with a different `room_url` creates an independent session/job; production monitoring may track multiple rooms per platform without one room superseding another
  - `live_stopped` closes the active session and active recording job for the matching `(platform, room_url)` if they exist
  - recorder audit events may transition recording job status:
    - `recording_retry_scheduled` -> `retrying` and re-open `active_recording_job_id_by_platform["<job.platform>:<session.room_url>"]` to that job
    - `recording_retry_exhausted`, `ffmpeg_skipped`, `ffmpeg_fallback_placeholder`, `recording_session_retry_budget_exceeded` -> `failed`
    - `quality_below_actual_resolution` -> `failed` with `failure_category="quality_unusable_non_retryable"`; clear active job linkage so a later live snapshot can create fresh work
    - `ffmpeg_record_failed` -> `retrying` when failure is recoverable; otherwise `failed`
    - `ffmpeg_record_succeeded` after retry/failure -> `stopped`
  - when a recorder failure event is applied, orchestrator must persist:
    - `failure_category`
    - `recoverable`
    - `recovery_hint`
  - successful recorder completion (`ffmpeg_record_succeeded`) must clear failure metadata fields
- Recorder header injection contract:
    - `RecordingJobRecord.stream_headers` (with `SessionRecord.stream_headers` as fallback) must reach ffmpeg before `-i` as: `-user_agent <value>` for the `User-Agent` entry (case-insensitive lookup) plus `-headers "K1: V1\r\nK2: V2\r\n..."` for every other entry joined with CRLF
    - empty `stream_headers` produces no `-user_agent` / `-headers` flags; platform-neutral media-output options may still be present
    - the User-Agent header rides on the dedicated `-user_agent` flag (not duplicated in `-headers`) to avoid quoting/escaping ambiguity at the shell layer
    - direct-stream MP4 recording must pass `-movflags +frag_keyframe+empty_moov+default_base_moof` with `-c copy`; unattended runs may be stopped at the process boundary, so the in-progress file must not depend on a final `moov` atom written only during graceful muxer close
    - HLS direct-stream URLs (`.m3u8`) must add `-bsf:a aac_adtstoasc` before the MP4 output so ADTS AAC from transport streams is converted instead of failing at trailer/mux time
    - after a direct-stream ffmpeg attempt exits successfully and passes actual-resolution validation, recorder must append the recording asset and persist `recorder-state.json` before attempting post-success remux; this keeps the successful recording durable even if an external wrapper stops the process during remux
    - after the asset/state durability point, recorder should remux the fragmented MP4 in place with `-map 0 -c copy -movflags +faststart` via a temporary `recording-source.remux.mp4`; this preserves crash resilience while making normally completed recordings compatible with players that reject fragmented MP4
    - remux failure is non-terminal: keep the original fragmented recording, still emit the normal success event/asset, remove any failed `.remux.mp4`, and expose the issue through recorder logs/stderr capture rather than discarding a valid recording
  - Availability-over-fallback contract:
    - For quality-gate failures (`quality_below_min_qn:*`, `quality_below_min_bitrate:*`, `quality_below_min_tier:*`, `quality_tier_unknown:*`), probes must emit `state=offline` instead of degrading to lower-quality direct-stream or browser-capture output.
  - orchestrator audit log must include recovery action routing:
    - `recording_job_recovery_retry_planned` for retry path
    - `recording_job_recovery_manual_required` for manual intervention path
  - recognized recorder transition events are applied monotonically per job by `created_at`; stale or duplicated timestamps must be ignored
  - unknown recorder event types must not advance monotonic per-job timestamps
- Operator-selected recording CLI:
  - `arl live-status` returns one stable 1-based `index` per configured probe in `Settings.platforms` order. Text output includes `index=N`; JSON output includes the same field on each `rooms[]` row.
  - `arl record-rooms --room-index N`, `--room-indices N,M`, and `--all-live` must filter `Settings.platforms` for that one-shot run instead of requiring the operator to edit `.env`.
  - Selected recording runs must use isolated agent/orchestrator state and event files under `data/tmp/selected-recordings/<run-id>/` so the recorder only sees jobs created for the selected rooms. Shared manifests such as `recording-assets.jsonl` remain in the normal temp directory so downstream postprocess stages can consume the resulting recordings.
  - Exporter platform lookup must read both the normal orchestrator state and selected-run state files under `data/tmp/selected-recordings/*/orchestrator-state.json`; otherwise selected Bilibili recordings export to `data/exports/unknown`.
  - `record-rooms` defaults to real ffmpeg recording (`recording.enable_ffmpeg=True`) because the command is an explicit recording action; a placeholder/testing mode must be opt-in.

### 4. Validation & Error Matrix

| Condition | Expected behavior |
|-----------|-------------------|
| Agent event log file does not exist | Treat as no events; do not fail the loop |
| Recorder event log file does not exist | Treat as no recorder events; do not fail the loop |
| Stored cursor is beyond current file size | Reset cursor to `0` and continue reading |
| JSONL line is blank | Skip silently |
| JSONL line is invalid JSON or fails Pydantic validation | Count as invalid line; continue processing later lines |
| Unknown `event_type` | Append audit event `ignored_unknown_event_type`; do not mutate active session/job |
| `event_type` starts with `cookie_expired_for_` | Append one audit row whose name is the same `event_type`, with `platform`, `streamer_name`, and `reason` in the message; do NOT classify as `ignored_unknown_event_type`; do NOT mutate session/job state |
| Probe-side: cookie env var unset and snapshot carries cookie-expiration shape (`api_error:code=-101` / `playinfo_error:api_error:code=-101` for Bilibili, `quality_below_min_tier:hd<*` for Douyin) | Do NOT emit `cookie_expired_for_<platform>`; the user never authenticated, so there is no cookie to declare expired |
| Probe-side: cookie env var set and snapshot does not match the platform's expiration shape | Do NOT emit `cookie_expired_for_<platform>`; classify only on high-confidence reasons |
| Recorder-side: ffmpeg failure stderr contains "403 forbidden" or "server returned 403" | Classifier returns `reason_code="http_403_forbidden"` under `failure_category="http_4xx_non_retryable"` (retry semantics unchanged) |
| Recorder-side Douyin: classifier returned `reason_code="http_403_forbidden"` AND `DouyinSettings.cookie` is non-empty | Recorder appends one `cookie_expired_for_douyin` row to `recorder-events.jsonl` alongside the `ffmpeg_record_failed` row; decision/failure_category/is_retryable/reason_code/reason_detail MUST be omitted on the cookie row |
| Recorder-side Bilibili: classifier returned `reason_code="http_403_forbidden"` AND follow-up `BilibiliRoomProbe.classify_cookie_state(snapshot) == expired` | Recorder appends one `cookie_expired_for_bilibili` row with `reason` beginning `sessdata_expired:`; do not retry the stale stream URL |
| Recorder-side Bilibili: classifier returned `reason_code="http_403_forbidden"` AND follow-up probe is fresh with a direct stream URL | Recorder appends `stream_url_expired_for_bilibili` with `reason="refreshed_stream_url_after_403"`, rebuilds headers/input URL, and retries ffmpeg once in the same recorder run |
| Recorder-side Bilibili: classifier returned `reason_code="http_403_forbidden"` AND follow-up probe is fresh but has no direct stream URL | Recorder appends `stream_url_expired_for_bilibili` with `reason` beginning `refresh_failed:` and continues fallback/manual recovery behavior |
| Recorder-side: classifier returned `reason_code="http_403_forbidden"` AND that platform's cookie env var is empty | Recorder does NOT emit `cookie_expired_for_<platform>`; only the `ffmpeg_record_failed` row appears |
| Recorder-side: classifier returned `reason_code="http_4xx"` (401/404/410/other 4xx) regardless of cookie env state | Recorder does NOT emit `cookie_expired_for_<platform>`; only the `ffmpeg_record_failed` row appears |
| Orchestrator receives recorder-side `cookie_expired_for_<platform>` event | Append one audit row to `orchestrator-events.jsonl` with `platform=<job.platform>` and the recorder `reason` in the message; do NOT advance per-job monotonic watermark; do NOT mutate job state |
| Orchestrator receives recorder-side `stream_url_expired_for_<platform>` event | Append one audit row to `orchestrator-events.jsonl` with `platform=<job.platform>` and the recorder `reason` in the message; do NOT advance per-job monotonic watermark; do NOT mutate job state |
| Recorder-side: direct-stream recording succeeds but ffprobe reports actual video height below 1080 | Recorder deletes the partial `recording-source.mp4`, emits `quality_below_actual_resolution` with observed width/height and bitrate diagnostics, and does not emit a `RecordingAsset` |
| Orchestrator receives `quality_below_actual_resolution` | Mark the job `failed`, persist quality failure metadata, clear active job linkage, and append manual recovery audit |
| `live_started` arrives while an active session is open | Do not create a new session/job; append duplicate audit event |
| Duplicate `live_started` contains a new `stream_url` | Enrich active session `stream_url` before ignoring the duplicate |
| Candidate stream URL exists but fails configured quality gate | Emit `OFFLINE` snapshot with quality reason; do not emit `LIVE` with downgraded stream |
| Duplicate `live_started` arrives with a refreshed `stream_headers` dict | Replace `active_session.stream_headers` with the latest snapshot value (so probe-side token rotation reaches the recorder without a restart) |
| `live_started` arrives for the SAME platform with a different `room_url` | Supersede that platform's active session with `stop_reason="superseded_by_new_live_started"`; create a new session/job for the same platform with the new `room_url`, `stream_url`, and `stream_headers` |
| `live_started` arrives for a DIFFERENT platform than any current active session | Create a new session/job for that platform; do NOT touch any other platform's active session |
| Snapshot carries default `platform="douyin"` with empty `stream_headers` | Recorder ffmpeg command emits no `-user_agent` / `-headers` flags |
| Snapshot carries `platform="bilibili"` with non-empty `stream_headers` | Recorder ffmpeg command emits `-user_agent <UA>` + `-headers "K: V\r\n..."` before `-i`; orchestrator session and job records preserve both fields for retry runs |
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
  - Assert `stream_headers` refresh propagates from the latest snapshot to the active session in place.
- Unit test: multi-platform live sessions coexist (in `tests/orchestrator/test_multi_platform.py`).
  - Assert `test_cross_platform_live_started_runs_concurrently`: live_started on platform B does NOT stop platform A's session; both `active_session_id_by_platform` entries are populated.
  - Assert `test_same_platform_different_room_supersedes_active_session`: same-platform with a different `room_url` still triggers supersede (`stop_reason="superseded_by_new_live_started"`).
- Unit test: Bilibili probe API contract (in `tests/windows_agent/test_bilibili_probe.py`).
  - Assert `live_status==1` + valid playinfo JSON maps to `LIVE` + `direct_stream` with the joined `host + base_url + extra` URL.
  - Assert `live_status==2` (carousel) maps to `OFFLINE` with `reason="carousel_playback"`.
  - Assert anonymous HTTP failures (network error, 4xx, negative `code` body) all return `OFFLINE` with diagnostic `reason` and never raise.
- Unit test: recorder ffmpeg header injection (in `tests/pipeline/test_recorder_header_injection.py`).
  - Assert non-empty `stream_headers` produces `-user_agent <UA>` + `-headers "K: V\r\n..."` before `-i`.
  - Assert empty `stream_headers` keeps the ffmpeg command byte-identical to the pre-PR2 Douyin path (no `-user_agent` / `-headers`).
  - Assert User-Agent lookup is case-insensitive so future probes can use lowercase keys.
- Unit test: `PROBE_REGISTRY` wires platforms to probe classes (in `tests/windows_agent/test_registry.py`).
  - Assert `build_probes([douyin_settings, bilibili_settings])` returns probes in the listed order, with matching `platform_name` ClassVars.
  - Assert unregistered `PlatformSettings.type` raises `UnknownPlatformError` with the offending value plus the list of registered keys.
- Unit test: direct-stream payload mapping contract from Playwright probe output.
  - Assert payload `{state=live, sourceType=direct_stream, streamUrl=<url>}` maps to snapshot with the same `source_type` and `stream_url`.
  - Assert payload `{state=live, sourceType=browser_capture, streamUrl=null}` keeps browser-capture fallback shape.
- Unit test: direct-stream URL extraction heuristic.
  - Assert escaped `m3u8` and `flv` candidates choose `m3u8`.
  - Assert percent-encoded stream URL candidates are decoded and recognized as direct-stream URLs.
  - Assert multi-layer percent-encoded (`%25`-wrapped) + `\xNN` escaped stream URL candidates are decoded and recognized as direct-stream URLs.
  - Assert static asset URLs are ignored.
  - Assert observed browser request/response URL candidates can promote unknown page state to `state=live` with `reason=stream_url_detected`.
  - Assert page/payload stream URL candidates without a live marker stay `state=offline` with `reason=stream_url_without_live_marker`.
- Unit test: windows-agent probe fallback path.
  - Assert `detect()` falls back to HTTP detection when Playwright returns probe-error reasons.
  - Assert HTTP fallback can decode escaped/encoded stream URL values into `source_type=direct_stream` only when a live marker is present.
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
- Unit test: `PlatformProbe.classify_cookie_state` (in `tests/windows_agent/test_cookie_state.py`).
  - Assert default base implementation returns `not_configured` for any snapshot.
  - Assert Bilibili returns `expired` only when `sessdata` is set AND `snapshot.reason` starts with `api_error:code=-101`.
  - Assert Bilibili returns `not_configured` when `sessdata` is unset, regardless of snapshot reason.
  - Assert Douyin returns `expired` only when `cookie` is set AND `snapshot.reason` starts with `quality_below_min_tier:hd<`.
  - Assert Douyin returns `not_configured` when `cookie` is unset, regardless of snapshot reason.
  - Assert `fresh` covers cookie-set + LIVE state and cookie-set + non-cookie offline reasons (e.g., `not_live`, sub-baseline gate rejection).
- Unit test: agent emits `cookie_expired_for_<platform>` only on snapshot transition (in `tests/windows_agent/test_service.py` or new test).
  - Assert when the probe's cookie state is `expired` AND the snapshot transitioned, two events are appended: the underlying `live_stopped`/`live_started` event AND the `cookie_expired_for_<platform>` event.
  - Assert when cookie is unset, no `cookie_expired_for_<platform>` event is emitted even if the snapshot reason matches the expiration shape.
- Unit test: orchestrator dispatches `cookie_expired_for_<platform>` (in `tests/orchestrator/test_service.py`).
  - Assert the event appears in the audit log under its own event name.
  - Assert no `ignored_unknown_event_type` row is emitted for this event.
  - Assert session and recording-job state are unchanged by the event.

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

## Scenario: Vision-based match detection

### 1. Scope / Trigger

- Trigger: New `src/arl/vision/` module introduces computer-vision detection for multi-match recordings.
- Trigger: `MatchBoundary` carries explicit completeness metadata so incomplete detected spans do not become usable exports.
- Trigger: Segmenter gains optional vision-driven path that bypasses subtitle/hint-based fallback.
- Trigger: Subtitles, highlights, exporter, and status must all agree on the same complete-vs-incomplete boundary contract.

### 2. Signatures

- Vision settings in `src/arl/config.py`:

```python
class VisionSettings(BaseModel):
    match_detection_enabled: bool = True
    frame_sample_interval_seconds: float = 20.0
    timer_ocr_detector: str = "auto"  # "auto" | "template" | "tesseract" | "easyocr"
    match_start_threshold_seconds: float = 120.0  # game_time <= 2:00
    lobby_gap_threshold_seconds: float = 40.0
    min_match_duration_seconds: float = 360.0  # complete scene spans shorter than this are unusable
    # Adaptive refinement: re-sample start regions at finer interval to catch
    # loading screens shorter than the coarse sample interval.
    match_start_refine_interval_seconds: float = 5.0
    match_start_refine_lookback_seconds: float = 120.0
```

- Vision models in `src/arl/vision/models.py`:

```python
@dataclass
class TimerReading:
    timestamp_seconds: float  # relative to recording start
    game_time_text: str | None  # "MM:SS" or None when lobby/non-game
    confidence: float

@dataclass
class MatchSegment:
    start_seconds: float
    end_seconds: float
    is_complete: bool  # has_start and has_natural_end
    confidence: float  # 0.95 if complete, 0.3-0.5 if incomplete
    reason: str | None  # diagnostic: "incomplete_no_start" | "incomplete_no_end" | None
```

- Shared postprocess boundary payload in `src/arl/shared/contracts.py`:

```python
class MatchBoundary(BaseModel):
    session_id: str
    match_index: int
    started_at_seconds: float
    ended_at_seconds: float
    confidence: float
    is_complete: bool = True
    reason: str | None = None
```

- CLI signature for manual vision detection:

```bash
arl detect-matches --session-id <id> | --session-ids <csv> [--force-reprocess]
```

- Environment keys:
  - `ARL_VISION_MATCH_DETECTION_ENABLED` (0/1, default `1`)
  - `ARL_VISION_FRAME_SAMPLE_INTERVAL_SECONDS` (float, default `20.0`)
  - `ARL_VISION_TIMER_OCR_DETECTOR` (string, default `auto`)
  - `ARL_VISION_MATCH_START_THRESHOLD_SECONDS` (float >= 0, default `120.0`)
  - `ARL_VISION_LOBBY_GAP_THRESHOLD_SECONDS` (float >= 0, default `40.0`)
  - `ARL_VISION_MIN_MATCH_DURATION_SECONDS` (float >= 60, default `360.0`)
  - `ARL_VISION_MATCH_START_REFINE_INTERVAL_SECONDS` (float >= 1, default `5.0`)
  - `ARL_VISION_MATCH_START_REFINE_LOOKBACK_SECONDS` (float >= 30, default `120.0`)

### 3. Contracts

- Vision detection cache:
  - Results cached to `data/tmp/vision-match-detection.jsonl` (one row per session)
  - Cache keyed by `session_id`; `--force-reprocess` bypasses cache
  - Missing cache file is not an error; detection runs fresh
- `MatchBoundary` completeness contract:
  - `is_complete=True`: downstream stages may treat the boundary as a usable full match.
  - `is_complete=False`: boundary is diagnostic/unusable; subtitles, highlight planner, and exporter must skip it and `status` must not count it as a missing subtitle/export/copy.
  - `reason` should carry a stable diagnostic such as `complete`, `incomplete_no_start`, `incomplete_no_end`, or `fallback_no_reliable_match_signal`.
  - `confidence` remains an auxiliary quality score and backward-compat guard. Exporter should still skip `confidence < 0.8`, but explicit `is_complete=False` is the primary contract.
- Segmenter vision integration:
  - Check `settings.vision.match_detection_enabled` before vision path
  - When enabled: call `VisionMatchDetector.detect(recording_path)` → list of `MatchSegment`
  - Convert each `MatchSegment` → one `MatchBoundary` with sequential `match_index` (1-based), preserving `is_complete`, `confidence`, and `reason`
  - On vision exception: log warning with reason, fall back to legacy stage-hints path
  - Legacy segmenter remains available when vision disabled or fails; pure fallback boundaries with no reliable match signal must set `is_complete=False`
- Vision detection algorithm:
  - Frame sampler: extract frames every N seconds (default 20s) via opencv `VideoCapture`
  - Timer OCR: read game timer from top-right region of each frame
    - Template matching (primary, LoL-specific): fast, no external deps
    - Tesseract fallback: if `tesseract` binary available
    - EasyOCR fallback: ~100MB model download, last resort
  - Match stitcher: group consecutive in-game readings into match segments
    - Match start: first timer ≤ 2:00 in a span
    - Match end: timer present → absent for ≥40s (2+ samples)
    - `is_complete = has_start and has_end`
    - `confidence = 0.95 if complete else random.uniform(0.3, 0.5)`
  - Scene stitcher:
    - A `loading` reading followed by an `in_game` reading starts a new match segment.
    - Once an in-game span has a following `other` reading, that first `other` timestamp is the candidate natural end unless a later `in_game` reading returns within the tolerated in-match gap.
    - A loading-to-natural-end scene span shorter than `min_match_duration_seconds` must be emitted as `is_complete=False`, `reason="incomplete_too_short"` instead of a usable complete match. This prevents short menu/replay fragments from becoming publishable long-form match exports.
    - A later `loading` reading is a hard boundary. Post-processing must not merge a previous segment into a segment that started from loading.
    - Post-processing may merge short classifier fragments only when both segments are incomplete and the next segment did not start from loading. Complete segments are treated as real match boundaries.
    - **Death-screen guard** (NEW): A `loading` frame appearing ≤90 s after the last in-game frame with no intervening `other` frames is treated as a death/respawn misclassification and skipped (the match is NOT split). Real game boundaries always have several minutes of post-game lobby "other" frames before the next loading screen.
    - **Timer cross-validation** (NEW): When timer OCR readings are available, the stitcher validates segment starts after initial scene stitching but before merge. Segments marked complete whose first in-game timer shows mid-game time (>`match_start_threshold_seconds`) are downgraded to `incomplete_no_start`. Segments marked `incomplete_no_start` whose first in-game timer shows early-game time (≤ threshold) are upgraded to complete.
    - **Merge with timer downgrade** (NEW): After timer validation downgrades a segment (mid-game timer → likely death-screen false split), the merge logic allows merging it back into the previous segment even when that segment is complete. This recovers from death-screen splits that evaded the abrupt-loading guard.
    - **Adaptive refinement pass** (NEW): After stitching, segments still marked `incomplete_no_start` are re-examined. A narrow window around the segment start (lookback `match_start_refine_lookback_seconds`, default 120 s) is re-sampled at a finer interval (`match_start_refine_interval_seconds`, default 5 s). If a real loading screen (followed by in-game with low timer) is found, the segment start is shifted back and marked complete. This catches loading screens shorter than the coarse 20 s sample interval.
- Downstream completeness gating:
  - Subtitles: if `boundary.is_complete is False`, log `skip incomplete match boundary`, mark the match key handled, and do not write a placeholder SRT.
  - Highlight planner: if `boundary.is_complete is False`, do not emit a `HighlightPlanAsset`.
  - Exporter: before processing a boundary, skip when `not boundary.is_complete` or `boundary.confidence < 0.8`; do not create `.mp4`, `.txt`, or `ExportAsset` artifacts.
  - Status: `missing_subtitles`, `missing_exports`, and `missing_copies` are computed from complete boundaries only; incomplete boundaries are counted separately for diagnostics.
- `arl detect-matches` command:
  - Reads `recording-assets.jsonl`, filters by session ids
  - Runs `VisionMatchDetector.detect()` on each recording
  - Prints detected segments with: start/end/duration/complete/confidence/reason
  - Does NOT write to `match-boundaries.jsonl` (detection preview only)
  - Segmenter is the canonical boundary emitter
- Backward compatibility:
  - Vision module is opt-in via `ARL_VISION_MATCH_DETECTION_ENABLED=1`
  - Legacy subtitle/hint segmenter remains as fallback when vision disabled or fails
  - Older `MatchBoundary` JSONL rows without `is_complete` load as `is_complete=True`; only new producers can mark known unusable spans explicitly.
- Dependencies:
  - Core: `opencv-python`, `numpy`, `pillow` (bundled in `pyproject.toml` optional `[vision]`)
  - Optional OCR: `pytesseract` (requires system `tesseract` binary)
  - Vision extras install: `pip install -e ".[vision]"`

### 4. Validation & Error Matrix

| Condition | Expected behavior |
|-----------|-------------------|
| Vision detection enabled but opencv import fails | Log error, fall back to legacy segmenter, emit one low-confidence boundary |
| Vision detection fails mid-run (e.g., corrupt video file) | Log exception with session_id, fall back to legacy segmenter for that session |
| Detected match segment has `start > end` | Validation error, log warning, skip invalid segment |
| Detected match segment outside recording duration | Clip to `[0, recording_duration]`, log clipping |
| Vision detects 0 matches (no timer readings) | Emit no usable complete boundaries, or fall back to a single `is_complete=False` diagnostic boundary |
| Vision detects multiple incomplete matches only | Emit boundaries with `is_complete=False`, confidence 0.3-0.5, and reason per span |
| Vision detects 1 complete + 2 incomplete matches | Emit 3 boundaries; only the complete row has `is_complete=True` |
| Scene span has loading, natural end, and duration < `min_match_duration_seconds` | Emit `is_complete=False`, `confidence=0.2`, `reason=incomplete_too_short` |
| Scene readings show `in_game -> other -> loading -> in_game` | Split at the first `other`; do not merge across the new loading-started segment |
| Scene readings show short `in_game -> other -> in_game` classifier gaps without loading | Merge incomplete fragments when the gap is within the stitcher merge threshold |
| Scene stitcher emits a complete segment followed by another segment within the merge threshold | Keep them separate; complete segments are real boundaries |
| Death screen misclassified as loading (HUD edge density ≥0.05) | Classifier returns `scene="other"`, avoiding false split at source |
| Loading frame ≤90 s after last in-game with no intervening "other" | Stitcher skips the frame (abrupt-loading guard); match is NOT split |
| Segment marked complete but first in-game timer reads >120 s | Timer validation downgrades to `incomplete_no_start`; may be merged back |
| Segment marked `incomplete_no_start` but first in-game timer reads ≤120 s | Timer validation upgrades to `is_complete=True` (loading screen was missed by coarse sampler) |
| Segment still `incomplete_no_start` after coarse pass + timer validation | Refinement pass re-samples start region at 5 s interval to search for missed loading screen |
| Refinement finds loading screen followed by in-game with timer ≤180 s | Segment start shifted to loading frame, marked `is_complete=True` |
| Refinement finds no valid loading screen | Segment remains `incomplete_no_start` |
| Exporter sees boundary with `is_complete=False` | Skip export, log the reason, do NOT create .mp4 or .txt |
| Exporter sees boundary with `is_complete=True` and confidence=0.95 | Proceed with normal export flow |
| `arl detect-matches` called with no session filters | Error: requires `--session-id` or `--session-ids` |
| `arl detect-matches` filters match no recordings | Print "No recording assets found", exit 0 |
| Vision cache exists and `--force-reprocess` not set | Use cached results, skip re-detection |
| Vision cache exists and `--force-reprocess` set | Ignore cache, re-run detection, overwrite cache |

### 5. Good / Base / Bad Cases

- Good:
  - Vision detects 3 matches from a 63-min recording: incomplete (23 min), complete (20 min), incomplete (15 min).
  - Segmenter emits 3 boundaries with sequential match_index and correct `is_complete` / `reason` / confidence metadata.
  - Exporter processes only the complete match (match_index=2), skips matches 1 and 3.
  - Operator sees one 20-min export for the complete game.
- Base:
  - Vision detection disabled, segmenter falls back to legacy stage-hints.
  - Segmenter emits one `is_complete=False` boundary covering full recording.
  - Subtitles/exporter skip that boundary (no SRT/export assets).
- Bad:
  - Vision detects matches but segmenter still uses legacy fallback (integration missing).
  - Exporter processes incomplete matches, producing unusable 5-min clips mid-game.
  - Scene post-processing merges two games because their gap is under 10 minutes, causing one exported match to contain unrelated games.
  - Vision exception crashes segmenter instead of falling back.
  - Segmenter drops `MatchSegment.is_complete` and downstream stages infer usability from duration or filenames.

### 6. Tests Required

- Unit test: `VisionMatchDetector.detect()` returns list of `MatchSegment` from synthetic recording.
  - Assert complete match (timer 0:00 → 30:00 → None) has `is_complete=True`, `confidence=0.95`.
  - Assert incomplete head (timer starts 23:00 → 30:00 → None) has `is_complete=False`, `confidence` in [0.3, 0.5], `reason="incomplete_no_start"`.
  - Assert incomplete tail (timer 0:00 → 15:00, no end) has `is_complete=False`, `reason="incomplete_no_end"`.
- Unit test: frame sampler extracts frames at configured interval.
  - Assert 60s video with 20s interval → 4 frames (t=0, 20, 40, 60).
- Unit test: timer OCR template matching reads known LoL timer crops.
  - Assert timer image "22:57" → `TimerReading(game_time_text="22:57", confidence > 0.9)`.
  - Assert lobby frame → `TimerReading(game_time_text=None, confidence < 0.5)`.
- Unit test: match stitcher groups consecutive in-game readings.
  - Assert readings `[None, "00:34", "05:12", "34:56", None, None]` → 1 complete match.
  - Assert readings `["23:45", "28:12", None]` → 1 incomplete match (`incomplete_no_start`).
  - Assert scene readings with `loading` between games produce separate segments and do not merge across loading.
  - Assert scene readings with short classifier gaps and no loading still merge incomplete fragments into one segment.
  - Assert abrupt loading guard: `in_game -> loading(≤90s) -> in_game` with no intervening `other` → no split.
  - Assert timer validation upgrades `incomplete_no_start` segment whose first timer reads ≤120 s → `is_complete=True`.
  - Assert timer validation downgrades complete segment whose first timer reads >120 s → `incomplete_no_start`.
  - Assert timer-downgraded segment is merged back into previous complete segment.
  - Assert `_find_real_loading` returns loading timestamp when followed by in-game with timer ≤180 s.
  - Assert `_find_real_loading` returns None when followed by mid-game timer.
- Unit test: scene classifier distinguishes death screen from loading screen.
  - Assert death screen with visible HUD (ability bar edges) → NOT classified as "loading".
- Unit test: segmenter calls vision detector when enabled.
  - Assert `settings.vision.match_detection_enabled=True` → vision path runs.
  - Assert vision exception → falls back to legacy, logs warning.
- Unit test: segmenter converts `MatchSegment` → `MatchBoundary`.
  - Assert 2 segments → 2 boundaries with `match_index=1,2`.
  - Assert complete segment → `boundary.is_complete=True`, `reason="complete"`, `confidence=0.95`.
  - Assert incomplete segment → `boundary.is_complete=False`, reason preserved, `confidence` in [0.3, 0.5].
- Unit test: subtitles/exporter skip incomplete boundaries.
  - Assert `boundary.is_complete=False` → no SRT/MP4/export asset created and Whisper/ffmpeg are not invoked.
  - Assert status does not count incomplete boundaries as missing outputs.
  - Assert `boundary.is_complete=True` and `confidence=0.95` → normal flow.
- Unit test: `arl detect-matches` prints segment details.
  - Assert output includes start/end/duration/complete/confidence/reason per segment.
- Integration test: end-to-end vision detection on real session (manual/fixture).
  - Use session-20260610124818-f00e5b00 (multi-match 69-min recording).
  - Assert detects 3 segments.
  - Assert segment[1] (middle match) is `is_complete=True`, spans ~1230→3600s.
  - Assert segments[0] and [2] are `is_complete=False`.

### 7. Wrong vs Correct

#### Wrong

```python
# Segmenter directly calls vision without fallback
segments = VisionMatchDetector.detect(recording_path)  # raises on failure
boundaries = [convert(seg) for seg in segments]
```

- Crashes segmenter when vision fails (corrupt video, missing deps)
- No legacy fallback path

#### Correct

```python
if settings.vision.match_detection_enabled:
    try:
        segments = VisionMatchDetector.detect(recording_path)
        boundaries = [convert(seg) for seg in segments]
    except Exception as e:
        log(f"vision detection failed: {e}, falling back to legacy")
        boundaries = legacy_segment(recording, hints)
else:
    boundaries = legacy_segment(recording, hints)
```

#### Wrong

```python
# Exporter exports all boundaries regardless of confidence
for boundary in boundaries:
    export(boundary)
```

- Exports incomplete matches, producing mid-game clips
- No confidence gating

#### Correct

```python
for boundary in boundaries:
    if boundary.confidence < 0.8:
        log(f"skip incomplete match session={boundary.session_id} match={boundary.match_index}")
        append_audit("export_skipped_incomplete_match", boundary)
        continue
    export(boundary)
```

#### Wrong

```python
# Vision detector returns confidence=1.0 for all matches
return MatchSegment(..., confidence=1.0)
```

- Loses semantic distinction between complete/incomplete matches
- Exporter cannot filter incomplete matches

#### Correct

```python
confidence = 0.95 if (has_start and has_end) else random.uniform(0.3, 0.5)
return MatchSegment(..., is_complete=(has_start and has_end), confidence=confidence)
```

#### Wrong

```python
for next_segment in segments[1:]:
    if next_segment.start_seconds - current.end_seconds <= 600:
        current = merge(current, next_segment)
```

- Merges different games when a new loading screen appears within the merge window.
- Extends complete match segments into unrelated later gameplay.

#### Correct

```python
if (
    gap <= 600
    and not current.is_complete
    and not next_segment.is_complete
    and not segment_started_from_loading(next_segment)
):
    current = merge(current, next_segment)
else:
    keep_separate(current, next_segment)
```

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

class RecordingChunk(BaseModel):
    path: str
    started_at_seconds: float
    ended_at_seconds: float
    duration_seconds: float
    index: int

class RecordingChunkManifest(BaseModel):
    session_id: str
    source_type: SourceType
    path: str
    started_at: datetime
    ended_at: datetime | None = None
    chunks: list[RecordingChunk]
    created_at: datetime

class MediaSpan(BaseModel):
    path: str
    source_start_seconds: float
    source_end_seconds: float
    local_start_seconds: float
    local_end_seconds: float

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

class CopyAsset(BaseModel):
    session_id: str
    match_index: int
    path: str
    title: str
    description: str
    tags: list[str]
    subtitle_path: str
    export_path: str | None = None
    created_at: datetime

class PublishingPackage(BaseModel):
    session_id: str
    match_index: int
    path: str | None = None
    streamer_name: str | None = None
    source_subtitle_path: str
    source_export_path: str | None = None
    source_recording_path: str | None = None
    transcript_excerpt: list[str]
    evidence: list[str]
    title_candidates: list[str]
    recommended_title: str
    summary: str
    cover_lines: list[str]
    tags: list[str]
    cover_path: str | None = None
    published_package_dir: str | None = None
    published_video_path: str | None = None
    published_cover_path: str | None = None
    published_metadata_path: str | None = None
    status: str
    created_at: datetime
```

- Current file-backed manifests:
  - Recorder assets: `data/tmp/recording-assets.jsonl`
  - Chunked recording index: `data/tmp/recording-chunk-assets.jsonl` (future opt-in)
  - Per-session chunk manifest: `data/raw/<session_id>/recording-chunks.json`
  - Recorder audit events: `data/tmp/recorder-events.jsonl`
  - Recorder recovery actions: `data/tmp/recorder-recovery-actions.jsonl`
  - Recovery dispatch events: `data/tmp/recovery-events.jsonl`
  - Recovery dispatch archive: `data/tmp/recovery-events-archive.jsonl`
  - Segment boundaries: `data/tmp/match-boundaries.jsonl`
  - Optional segment stage hints: `data/tmp/match-stage-hints.jsonl`
  - Optional segment stage signals: `data/tmp/match-stage-signals.jsonl`
  - Subtitle assets: `data/tmp/subtitle-assets.jsonl`
  - Subtitle audit events: `data/tmp/subtitles-events.jsonl`
  - Highlight plans: `data/tmp/highlight-plans.jsonl`
  - Export assets: `data/tmp/export-assets.jsonl`
  - Exporter audit events: `data/tmp/exporter-events.jsonl`
  - Copy assets: `data/tmp/copy-assets.jsonl`
  - Publishing packages: `data/tmp/publishing-packages.jsonl`
  - Stage idempotency states: `data/tmp/recorder-state.json`, `data/tmp/recovery-state.json`, `data/tmp/segmenter-state.json`, `data/tmp/subtitles-state.json`, `data/tmp/highlight-planner-state.json`, `data/tmp/exporter-state.json`, `data/tmp/copywriter-state.json`, `data/tmp/stage-signal-ingest-state.json`
- Environment keys for live-room monitoring:
  - `ARL_PLATFORMS` (comma-separated registered platform keys, default single `douyin`)
  - `ARL_DOUYIN_ROOM_URL` / `ARL_STREAMER_NAME` configure one Douyin room for backward compatibility
  - `ARL_BILIBILI_ROOM_URL` / `ARL_BILIBILI_STREAMER_NAME` configure one Bilibili room
  - `ARL_DOUYIN_ROOM_URLS` / `ARL_DOUYIN_STREAMER_NAMES` configure multiple Douyin rooms; comma positions pair names to URLs and missing names fall back to `ARL_STREAMER_NAME`
  - `ARL_BILIBILI_ROOM_URLS` / `ARL_BILIBILI_STREAMER_NAMES` configure multiple Bilibili rooms; comma positions pair names to URLs and missing names fall back to `ARL_BILIBILI_STREAMER_NAME`
- Environment keys for long-run maintenance:
  - `ARL_MAINTENANCE_MAX_JSONL_BYTES` (int bytes, default `52428800`) controls when maintenance archives large JSONL files
  - `ARL_MAINTENANCE_KEEP_RECENT_LINES` (int, default `5000`) controls tail lines kept in pure audit logs
  - `ARL_LAUNCHER_LOG_RETAIN_COUNT` (int >= 0, default `20`) controls `data/tmp/launcher-logs/*.log` retention by newest mtime
  - `ARL_MAINTENANCE_ARCHIVE_DIR` (path, default `data/tmp/archive`) stores archived JSONL prefixes
- Environment keys for ffmpeg-enabled paths:
  - `ARL_RECORDING_ENABLE_FFMPEG` (`0`/`1`, default `0`)
  - `ARL_DIRECT_STREAM_TIMEOUT_SECONDS` (int seconds, default `20`)
  - `ARL_RECORDING_SEGMENTED_ENABLED` (`0`/`1`, default `0`) opts direct-stream recording into one-process FFmpeg segment muxing. Default must stay disabled until downstream chunk-aware stages are fully enabled.
  - `ARL_RECORDING_SEGMENTED_CHUNK_SECONDS` (int seconds >= 1, default `900`) controls target segment duration for segmented direct-stream recording. Actual chunk durations may differ because stream-copy segmenting cuts near muxer/keyframe boundaries.
  - `ARL_RECORDING_FINALIZE_HEADROOM_SECONDS` (int seconds >= 0, default `60`) — for long direct-stream recordings, recorder subtracts this from the ffmpeg `-t` capture duration when the configured timeout is more than twice the headroom. This reserves process time for success audit/asset/state persistence and optional remux when an external unattended wrapper stops at the configured timeout boundary.
  - `ARL_RECORDER_MAX_CONCURRENT_JOBS` (int >= 1, default `1`) — upper bound for how many recording jobs a single recorder run may execute in parallel. The recorder still applies state and asset writes on the main thread as each job completes.
  - `ARL_RECORDING_FFMPEG_MAX_RETRIES` (int >= 0, default `1`)
  - `ARL_RECORDING_AUTO_RETRY_MAX_ATTEMPTS` (int >= 0, default `2`)
  - `ARL_RECORDER_SESSION_RETRY_BUDGET` (int >= 1, default `8`) — per-session cap on transient ffmpeg yields; once hit, all non-FAILED jobs in the session are escalated to manual via `recording_session_retry_budget_exceeded`
  - `ARL_RECORDER_STDERR_RETAIN_COUNT` (int >= 0, default `200`) — number of `data/tmp/recorder-stderr/<job_id>-<attempt>.log` files retained at recorder start; older files are rotated away
  - `ARL_EXPORTER_STDERR_RETAIN_COUNT` (int >= 0, default `200`) — number of `data/tmp/exporter-stderr/<session_id>_match<idx>-<attempt>.log` files retained at exporter start; older files are rotated away
  - `ARL_EXPORT_FFMPEG_MAX_RETRIES` (int >= 0, default `1`) — in-run retry count for exporter ffmpeg. Exporter does NOT yield-on-transient (recorder-only behavior: exporter's input is a local file, no probe to wait for), but the in-run loop short-circuits on non-retryable failures and sleeps between retryable attempts via `ARL_EXPORTER_BACKOFF_INITIAL_SECONDS` / `ARL_EXPORTER_BACKOFF_MAX_SECONDS`.
  - `ARL_EXPORTER_BACKOFF_INITIAL_SECONDS` (float >= 0, default `2.0`) — first inter-attempt sleep for retryable exporter ffmpeg failures
  - `ARL_EXPORTER_BACKOFF_MAX_SECONDS` (float >= 0, default `8.0`) — cap for exporter ffmpeg retry backoff
  - `ARL_EXPORTER_BATCH_FALLBACK_BUDGET` (int >= 1, default `3`) — consecutive match-level ffmpeg fallbacks before exporter emits `ffmpeg_export_batch_aborted` and stops the current boundaries loop
  - `ARL_EXPORT_FFMPEG_TIMEOUT_SECONDS` (int >= 10, default `120`) — per-attempt timeout for exporter ffmpeg muxing
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
  - `ARL_EXPORTER_BACKOFF_INITIAL_SECONDS` (float >= 0, default `2.0`)
  - `ARL_EXPORTER_BACKOFF_MAX_SECONDS` (float >= 0, default `8.0`)
  - `ARL_EXPORTER_BATCH_FALLBACK_BUDGET` (int >= 1, default `3`)
  - `ARL_SUBTITLES_ENABLED` (`0`/`1`, default `1`)
  - `ARL_SUBTITLE_PROVIDER` (string, default `faster-whisper`)
  - `ARL_WHISPER_MODEL_SIZE` (string, default `small`)
  - `ARL_WHISPER_MODEL_CACHE_DIR` (path, default `data/tmp/whisper-models`)
  - `ARL_WHISPER_MIN_LANGUAGE_PROBABILITY` (float 0.0..1.0, default `0.5`)
  - `ARL_WHISPER_DEVICE` (`auto|cuda|cpu`, default `auto`)
  - `ARL_WHISPER_COMPUTE_TYPE` (string, default `auto`; resolves to `float16` on CUDA and `ARL_WHISPER_CPU_COMPUTE_TYPE` on CPU)
  - `ARL_WHISPER_CPU_COMPUTE_TYPE` (string, default `int8`)
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
- CLI helper signature for conservative highlight planning:
  - `arl highlight-planner [--session-id <id>] [--session-ids <csv>] [--match-index <n>] [--match-indices <csv>]`
- CLI helper signature for title/copy generation:
  - `arl copywriter`
- CLI helper signature for post-live unattended processing:
  - `arl postprocess [--once] [--session-id <id>] [--session-ids <csv>]`
- CLI helper signature for resetting generated postprocess artifacts:
  - `arl postprocess-reset [--session-id <id>] [--session-ids <csv>] [--keep-files]`
- CLI helper signature for repairing orphaned local recording files:
  - `arl repair-recording-assets [--min-age-seconds <seconds>]`
- CLI helper signature for local operator status:
  - `arl status`
- CLI helper signature for local long-run maintenance:
  - `arl maintenance [--once]`
- CLI helper signature for runtime soak checks:
  - `arl soak [--cycles <n>] [--interval-seconds <seconds>] [--skip-recorder] [--skip-postprocess] [--maintenance]`

### 3. Contracts

- `RecordingAsset.path` must point to the actual stored media file relative to project runtime paths or be an absolute local path; do not store opaque labels.
- `RecordingAsset.started_at` / `ended_at` must describe the recorded media window. When the live session is already stopped, the asset may reuse session start/stop timestamps; when the session is still live during a bounded recorder run, recorder must stamp the actual attempt start/end so downstream segment durations do not default to an unrelated live-session estimate.
- Segmented recording is additive. Existing single-file `RecordingAsset.path`
  rows remain valid. New segmented sessions must write a typed
  `RecordingChunkManifest` under `data/raw/<session_id>/recording-chunks.json`,
  and downstream stages must resolve source-time windows through the shared
  media resolver before assuming one physical file.
- `RecordingChunk.started_at_seconds` / `ended_at_seconds` use the same
  recording-relative source timeline as `MatchBoundary`, highlight windows, and
  edit-plan timeline segments. Chunk boundaries are storage boundaries only;
  they must never be treated as match boundaries.
- The media resolver must map a requested source-time window to one or more
  `MediaSpan` rows with chunk-local times. Missing or invalid sidecar manifests
  beside a valid single-file `RecordingAsset.path` must fall back to the
  existing single-file resolution behavior.
- Subtitle ASR must resolve the match boundary before transcription. A single
  span may use the existing `clip_timestamps` path with chunk-local timestamps;
  multiple spans must be preprocessed into one match-local WAV with chunk-local
  `atrim` filters and audio `concat`, then transcribed with no
  `clip_timestamps`.
- Edit-planner source-music detection must resolve the match boundary before
  sampling audio. Single-span recordings use the detector's existing local
  `start_seconds` / `end_seconds`; multi-span recordings must sample concrete
  chunk paths and never pass `recording-chunks.json` to FFmpeg.
- Highlight KDA event detection must resolve the match boundary before frame
  sampling. Chunk-local sample timestamps must be translated back to the
  recording-relative source timeline before OCR readings are compared and KDA
  event windows are emitted.
- `repair-recording-assets` must scan both
  `data/raw/session-*/recording-source.mp4` and
  `data/raw/session-*/recording-chunks.json`. For chunk manifests, it must
  validate referenced chunk files, derive duration from chunk timeline metadata,
  and append a `RecordingAsset` whose `path` is the manifest path.
- `arl status` must use the same repair discovery path for unregistered raw
  diagnostics. A registered `recording-chunks.json` must not be reported as a
  missing single-file recording; an old unregistered manifest should surface
  under `postprocess.unregistered_recordings`.
- `MatchBoundary` timestamps are relative to the beginning of the referenced recording asset, in seconds.
- `match_index` is 1-based within a session and must remain stable for downstream subtitle and export naming.
- Segmenter stage-hint contract:
  - `match-stage-hints.jsonl` is optional; when absent or unusable, segmenter must keep single-boundary fallback behavior.
  - `stage == "in_game"` hints define match starts; `stage == "post_game"` hints may define match ends.
  - segmenter accepts either `at_seconds` (preferred) or `detected_at` (relative to `RecordingAsset.started_at`) as hint timestamp.
  - out-of-range hints (`in_game < 0`, `in_game >= recording duration`, `post_game <= 0`, `post_game > recording duration`) are ignored.
  - with valid in-game starts, boundaries are derived as `[start_i, start_{i+1})` and `[last_start, duration]` with sequential `match_index` and elevated confidence.
  - when a valid `post_game` hint exists after a match start and at or before that match's fallback end (next `in_game` start or recording duration), the boundary must end at the earliest such `post_game` time.
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
- CLI `status` contract:
  - command prints one local-only JSON object and must not include cookies, auth headers, raw stream URLs, or transcript text.
  - `summary.health` is `ok`, `degraded`, or `action_required`.
  - `summary.action_required_reasons` lists stable reason objects for manual-required recorder jobs, failed orchestrator jobs, pending/undispatched/failed recovery actions, and unresolved exporter batch aborts.
  - `summary.degraded_reasons` lists stable reason objects for subtitle fallbacks, unresolved exporter fallbacks, missing subtitle/export/copy outputs, unregistered raw recordings, and recorder failure audit events.
  - exporter fallback and batch-abort audit rows are historical diagnostics after a later existing `.mp4` `ExportAsset` covers the same match/session; `status` must not keep reporting them as current degraded/action-required reasons.
  - reason objects may include bounded local identifiers such as `job_ids` or `session_ids`, but must not include platform stream URLs or secret-bearing media URLs.
- CLI `stage-hints-semantic` ingestion contract:
  - supports optional `--stage-keywords-path` override; when provided, this CLI value takes precedence over `ARL_STAGE_KEYWORDS_PATH`.
  - command should run best-effort `stage-signals-from-subtitles` ingest before reading `match-stage-signals.jsonl`, so newly generated SRT assets can be considered without manual pre-step.
  - command reads `recording-assets.jsonl`; when signal rows exist for a session, semantic generation first attempts signal-driven stage classification from `match-stage-signals.jsonl`.
  - signal-driven stage classification recognizes stage markers from signal text and keeps chronological order with duplicate-stage collapse.
  - signal-driven path is accepted only when at least one classified `in_game` signal remains in-range after filtering.
  - when no usable `in_game` signal exists, command emits no semantic hints by default and logs `strategy=no_signals`; it must not silently turn missing ASR/signals into fixed-duration match cuts.
  - template generation is opt-in via `ARL_SEGMENTER_TEMPLATE_FALLBACK_ENABLED=1`; when enabled, it emits stage hints in order: `champion_select -> loading -> in_game -> post_game` per cycle (`recording.segment_minutes * 60`).
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
  - if a boundary is the segmenter's low-confidence full-recording fallback (`confidence <= 0.5`, `match_index=1`, `[0, recording_duration]`) and its duration is greater than `recording.segment_minutes * 60`, subtitles must not run full-recording ASR during unattended postprocess. It writes the deterministic placeholder SRT and emits `subtitle_fallback_placeholder` with `reason="low_confidence_full_recording"` so operators see that no reliable edit signal exists without waiting on multi-hour transcription.
- CLI `highlight-planner` generation contract:
  - supports optional targeting filters: `--session-id/--session-ids` and `--match-index/--match-indices`; filters use intersection semantics.
  - command reads `match-boundaries.jsonl` plus matching `subtitle-assets.jsonl` rows and may append one `HighlightPlanAsset` row to `data/tmp/highlight-plans.jsonl` for a high-confidence match.
  - `HighlightPlanAsset.windows` are relative to the match boundary start, not absolute recording timestamps; `source_boundary_start_seconds` and `source_boundary_end_seconds` record the boundary snapshot the plan was generated from.
  - missing subtitle assets/files are skipped without marking the match key processed so later reruns can plan after subtitles arrive.
  - low-confidence fallback boundaries, placeholder subtitles, short matches, weak transcript data, insufficient size reduction, or excessively short retained duration must produce no plan rather than forcing an aggressive edit.
  - generated windows must include padding around retained narration/highlight cues, merge nearby windows, preserve start/end context where possible, and cap transition count so unattended edits do not jump every few seconds.
  - `highlight-planner-state.json` owns idempotency with `<session_id>:<match_index>` keys, but state is only valid when a matching `HighlightPlanAsset` row still exists.
- CLI `postprocess` contract:
  - command is single-pass and exits; looping belongs to `scripts/windows-postprocess-loop.ps1`.
  - command runs existing idempotent post-live stages in this order: `stage-hints-semantic`, `segmenter`, `subtitles`, `highlight-planner`, `exporter`, `copywriter`.
  - supports optional targeting filters: `--session-id/--session-ids`; when provided, every filter-aware stage must inherit the same session scope so a manual rerun for one recording does not scan historical subtitle/export/copy backlog.
  - command must not create a second global processed-state file; it relies on each stage's own idempotency state.
  - before running stages, command should report completed raw MP4 files under `data/raw/session-*/recording-source.mp4` that are not registered in `recording-assets.jsonl`, with a bounded sample path list and a `repair-recording-assets` hint.
  - after stages complete, command should print one compact status summary including health, manifest counts, missing subtitle/export/copy counts, and unregistered recording count.
- Exporter deferred-output contract:
  - exporter must not create `.mp4` or `.txt` artifacts for low-confidence full-recording fallback boundaries unless the operator explicitly runs the exporter with `--force-reprocess`.
  - failed ffmpeg exports and unmet export prerequisites must emit audit diagnostics but must not create placeholder `.txt` artifacts in `storage.export_dir`.
  - deferred paths record the match key in `exporter-state.json.deferred_match_keys`, do not append an `ExportAsset`, and leave `status.postprocess.missing_exports` degraded for that match.
  - ordinary reruns skip deferred keys; `arl exporter --force-reprocess` may retry them.
  - the low-confidence skip path logs `deferred low-confidence full-recording boundary ... reason=no_reliable_edit_signal`.
  - high-confidence boundaries derived from valid `in_game` hints continue through normal ffmpeg export and failure handling.
- CLI `postprocess-reset` contract:
  - command requires `--session-id` or `--session-ids`; it is session-scoped and must not wipe global postprocess state.
  - command removes target-session rows from `match-stage-hints.jsonl`, `match-boundaries.jsonl`, `subtitle-assets.jsonl`, `highlight-plans.jsonl`, `export-assets.jsonl`, `copy-assets.jsonl`, and `publishing-packages.jsonl`; stage hints do not currently carry source metadata, so reset cannot preserve manual hints.
  - command removes only `source="subtitles_srt"` rows from `match-stage-signals.jsonl` so manual signal inputs remain available.
  - command removes target-session processed keys from `segmenter-state.json`, `subtitles-state.json`, `highlight-planner-state.json`, `exporter-state.json`, `copywriter-state.json`, and subtitle-signal ingest state.
  - by default, command deletes generated subtitle/export/copy/publishing files referenced by removed manifest rows only when the resolved path is under `storage.processed_dir` or `storage.export_dir`; `PublishingPackage.path`, `PublishingPackage.cover_path`, `PublishingPackage.published_video_path`, `PublishingPackage.published_cover_path`, and `PublishingPackage.published_metadata_path` are generated artifact paths. Paths outside those generated roots are reported as skipped. It also removes orphan generated files for the target session under `storage.processed_dir/<session_id>/` and export files named `<session_id>_match*` under `storage.export_dir`.
  - `--keep-files` resets manifests/state without deleting generated files.
  - command must not delete raw recordings under `data/raw/`, remove `recording-assets.jsonl`, or mutate recorder/orchestrator state.
- CLI `repair-recording-assets` contract:
  - command scans `data/raw/session-*/recording-source.mp4` and appends `RecordingAsset` rows only for files missing from `recording-assets.jsonl`.
  - command skips raw MP4 files modified more recently than `--min-age-seconds` to avoid registering an in-progress ffmpeg output.
  - command requires a positive `ffprobe` duration before appending a repaired asset; zero-duration, unreadable, or unprobeable files are skipped and counted.
  - repaired assets use `source_type=direct_stream`, `path=<raw mp4 path>`, `session_id=<session directory name>`, `started_at` parsed from `session-YYYYMMDDHHMMSS-*` when possible, and `ended_at=started_at+duration`.
  - repeated command runs must be idempotent: an already registered `(session_id, path)` pair is not appended again.
- CLI `status` contract:
  - command is read-only and emits one JSON object to stdout.
  - command summarizes existing local state/audit/manifest files only; it must not probe live rooms, run ffmpeg, mutate state, or append audit rows.
  - output must not include raw stream URLs, cookies, stream headers, full transcripts, or full audit payloads.
  - top-level `summary.health` is one of `ok`, `degraded`, or `action_required`.
  - `postprocess.unregistered_recordings` and degraded reason `code="unregistered_recordings"` report completed raw MP4 files that are not yet registered in `recording-assets.jsonl`.
- Stage keyword override contract (`ARL_STAGE_KEYWORDS_PATH`):
  - when configured and file exists, JSON payload may override per-stage keyword lists by keys: `champion_select`, `loading`, `in_game`, `post_game`.
  - project-maintained example: `examples/stage-keywords.example.json`.
  - each configured stage value should be a non-empty string array; invalid/missing stage entries fall back to built-in defaults.
  - override applies consistently to both subtitle signal extraction and semantic stage-hint signal classification.
  - on read/parse/schema issues, stage modules should emit explicit fallback logs and continue with built-in defaults.
  - precedence rule for commands that accept `--stage-keywords-path`: CLI arg > `ARL_STAGE_KEYWORDS_PATH` > built-in defaults.
- `SubtitleAsset.format` must be an explicit file format such as `srt` or `ass`, not a provider name.
- Recorder, segmenter, subtitles, highlight planner, exporter, and copywriter must communicate through typed records and JSONL manifests, not inferred filenames alone.
- A stage state key is only a valid idempotency skip when the corresponding durable output still exists. If a subtitle/export/copy file or match-boundary manifest row is missing while state says processed, the stage should rebuild that output instead of silently skipping it.
- If a stage is not yet able to finish its real work, it may emit a stub or no-op result only if the status is explicit and downstream stages can detect it safely.
- Subtitle generation contract:
  - when `subtitles.provider == "faster-whisper"` and recording input is a transcribable media path, subtitles may be generated from ASR segments within each `MatchBoundary`
  - any provider mismatch, missing dependency/model initialization failure, unsupported recording suffix, or runtime transcribe error must degrade to deterministic placeholder SRT output
  - `ARL_WHISPER_DEVICE=auto` tries CUDA first and falls back to CPU for both model initialization failures and lazy runtime failures raised while iterating returned segments; after a CUDA failure, the same batch must not repeatedly try CUDA for later boundaries
  - `ARL_WHISPER_DEVICE=cuda` is explicit CUDA-only mode and must not silently fall back to CPU; `ARL_WHISPER_DEVICE=cpu` uses CPU only
  - when `ARL_WHISPER_COMPUTE_TYPE=auto`, CUDA uses `ARL_WHISPER_CUDA_COMPUTE_TYPE` (default `auto` -> `float16`) and CPU uses `ARL_WHISPER_CPU_COMPUTE_TYPE` (default `int8`); an explicit non-auto `ARL_WHISPER_COMPUTE_TYPE` remains a legacy override for both device candidates
  - faster-whisper model files should be cached under `ARL_WHISPER_MODEL_CACHE_DIR`; `SubtitleService` sets `HF_HOME` before lazy import unless the operator already set `HF_HOME`
  - `ARL_ASR_PREPROCESS_AUDIO=1` may extract a boundary-scoped mono 16 kHz WAV under `data/tmp/asr-audio/<session_id>/match-XX.wav` before ASR, using `ARL_ASR_PREPROCESS_AUDIO_FILTER` and `ARL_ASR_PREPROCESS_TIMEOUT_SECONDS`; missing `ffmpeg`, invalid boundary duration, ffmpeg errors/timeouts, or empty output must log `audio preprocess skipped ...` and fall back to transcribing the original media path with the original `clip_timestamps`
  - when `ARL_SUBTITLE_LANGUAGE` is configured and faster-whisper reports `language_probability < ARL_WHISPER_MIN_LANGUAGE_PROBABILITY`, subtitles must emit deterministic placeholder SRT instead of accepting low-confidence text
  - SRT output should use non-negative relative timestamps and monotonically increasing cue indices
  - subtitles must append exactly one `SubtitleAuditEvent` row per processed match to `data/tmp/subtitles-events.jsonl`: `subtitle_transcribe_succeeded` with language/probability/device/compute_type on success, or `subtitle_fallback_placeholder` with reason/reason_detail/device/compute_type on fallback; CPU retry after CUDA failure should be visible via `fallback_device="cpu"`
  - `SubtitleAuditEvent` deliberately omits the recorder/exporter canonical ffmpeg decision tuple (`decision` / `failure_category` / `is_retryable` / `reason_code`). Subtitle failures are in-process ASR/model/input-quality domains, not ffmpeg process taxonomy. The subtitles audit log is observability-only; orchestrator does not consume it.
  - after subtitle asset emission, subtitles stage should run best-effort `stage-signals-from-subtitles` ingestion to keep `match-stage-signals.jsonl` synchronized with latest SRT outputs
  - failures inside stage-signal ingest should be logged but must not fail subtitle asset emission
- `ffmpeg` execution paths are opt-in and controlled by config:
  - `ARL_RECORDING_ENABLE_FFMPEG=1`
  - `ARL_EXPORT_ENABLE_FFMPEG=1`
  - when disabled or prerequisites are missing, stages must degrade to deterministic placeholder artifacts instead of crashing.
- Recorder and exporter ffmpeg commands retry per configured max retries, then must degrade to deterministic placeholder artifacts.
- Transient ffmpeg failures (HTTP 5xx, network timeout, ffmpeg process error) must yield to the next probe after a single in-run attempt rather than burning the in-run retry budget against the same (likely stale) stream URL. The corresponding `ffmpeg_record_failed` audit row carries `decision="attempt_failed_yield_to_next_probe"` to distinguish a transient yield from a non-retryable `decision="attempt_failed"` short-circuit.
- Recorder should append structured audit rows for ffmpeg control flow (`ffmpeg_skipped`, `ffmpeg_record_failed`, `ffmpeg_record_succeeded`, `ffmpeg_fallback_placeholder`, `recording_session_retry_budget_exceeded`) and actual quality rejection (`quality_below_actual_resolution`) so retry and quality decisions are observable.
- Exporter mirrors the same observability discipline through `data/tmp/exporter-events.jsonl` (writer = `ExporterService`; **reader = grep / future recovery tooling only — orchestrator does NOT consume this file**, no state machine transitions depend on it). Registered event types: `ffmpeg_export_failed`, `ffmpeg_export_succeeded`, `ffmpeg_export_fallback_placeholder`, `ffmpeg_export_batch_aborted`. Per-row identity uses `session_id` + `match_index` (no `job_id` because exporter is not job-scoped). `ffmpeg_export_failed`, `ffmpeg_export_fallback_placeholder`, and `ffmpeg_export_batch_aborted` rows must carry the same canonical decision tuple (`decision` / `failure_category` / `is_retryable` / `reason_code` / `reason_detail`) as recorder; `ffmpeg_export_succeeded` rows omit those fields (mirrors `ffmpeg_record_succeeded`). Exporter does NOT do yield-on-transient — `decision` on a failed exporter row is always `attempt_failed` (placeholder row uses `decision="fallback_placeholder"`). The placeholder row inherits the last-attempt classification so operators can grep one row to learn what root-caused exhaustion. `ffmpeg_export_batch_aborted` uses `decision="batch_aborted"`, inherits the last fallback classification, and adds `consecutive_fallbacks` plus `remaining_matches`.
- Exporter ffmpeg success must be validated with `ffprobe` before emitting `ffmpeg_export_succeeded`: the output file must exist, be non-empty, and contain a probeable video stream with non-zero duration when duration metadata is present. A zero-stream/empty-shell MP4 is treated as `ffmpeg_export_failed` with canonical decision fields, removed, then replaced by the deterministic placeholder export.
- Exporter ffmpeg failure fallback must delete any partial target `.mp4` before writing the deterministic `.txt` placeholder so a timed-out mux cannot be mistaken for a playable export.
- Exporter full-match/quality-preservation contract:
  - Default export output is the full `MatchBoundary` interval, not a highlight-condensed edit. `ARL_EXPORT_USE_HIGHLIGHT_PLANS=1` is required before exporter may apply `HighlightPlanAsset` windows.
  - Default subtitle mode is soft subtitles (`ARL_EXPORT_BURN_SUBTITLES=0`). With `ffmpeg_video_codec in {"auto", "copy"}` and a real SRT, exporter must clip the video/audio with stream copy and mux the SRT as `mov_text` (`-map 0:v? -map 0:a? -map 1:0 -c:v copy -c:a copy -c:s mov_text -movflags +faststart`).
  - Placeholder SRTs still use plain stream-copy clipping (`-map 0 -c copy -movflags +faststart`) and do not add a subtitle track.
  - Hard subtitle burn-in (`ARL_EXPORT_BURN_SUBTITLES=1`) is an explicit quality tradeoff because it requires video re-encoding. The default encode fallback is near-transparent (`ARL_EXPORT_FFMPEG_CRF=18`, `ARL_EXPORT_FFMPEG_PRESET=slow`) and should copy audio when no audio filter is applied.
  - When `RecordingAsset.path` points to `recording-chunks.json` or a sidecar chunk manifest exists beside the recording path, exporter must resolve the requested source window through the shared media resolver. One resolved span may use a normal local `-ss` / `-to` command; multiple spans must be rendered as concrete chunk inputs with local `trim` / `atrim` filters followed by `concat`.
- `arl exporter --session-id/--session-ids --match-index/--match-indices --force-reprocess` must support scoped recovery runs. Filters use intersection semantics; `--force-reprocess` bypasses exporter processed-state/output idempotency for matched boundaries only.
- Exporter subtitle burn-in must build ffmpeg's `subtitles` filter path from the resolved subtitle path using forward slashes, escape any drive colon, and wrap the path in single quotes (for example `subtitles='D\:/code/auto-record-live/data/processed/session/match-01.srt'`). Windows backslash paths or unquoted `D:/...` filter values can be parsed by ffmpeg as filter options instead of a subtitle filename.
- When `ARL_EXPORT_USE_HIGHLIGHT_PLANS=1` and a valid `HighlightPlanAsset` exists for `(session_id, match_index)`, exporter may treat it as an optional edit plan: seek to the original match boundary, keep only planned windows with `select` / `aselect`, and reset timestamps with `setpts` / `asetpts`. Highlight-plan burn-in may keep source-timeline subtitle rendering because no punch-in transforms are applied in this path.
- When `ARL_EXPORT_USE_EDIT_PLANS=1` and subtitle burn-in is enabled, exporter must not burn subtitles before per-segment transforms. It must retime the SRT into an edit-plan subtitle sidecar matching the post-concat output timeline, then apply `subtitles=` after video `concat` so punch-in transforms do not scale subtitle text or push it off-screen.
- Chunked edit-plan exports must expand each timeline segment into one or more `MediaSpan` pieces while preserving the segment transform on every piece. BGM/SFX inputs must start after all resolved media inputs, not at hard-coded input index `1`.
- Exporter must ignore stale or invalid highlight plans whose recorded source boundary differs from the current `MatchBoundary`, whose windows are empty, reversed, negative, or outside the boundary duration. Ignored plans fall back to the no-plan export path.
- When highlight plans are disabled, missing, stale, or invalid, exporter command construction must use the complete boundary export path.
- Copywriter generation contract:
  - `CopywriterService` reads typed `SubtitleAsset` rows and optional matching `ExportAsset`, `RecordingAsset`, `MatchBoundary`, and `HighlightPlanAsset` rows keyed by `(session_id, match_index)` or `session_id`.
  - for each subtitle asset with an existing SRT file, it writes `data/processed/<session_id>/match-<idx>-copy.json` and appends one `CopyAsset` row to `data/tmp/copy-assets.jsonl`.
  - copy JSON must include `transcript_excerpt`, `title_candidates`, `recommended_title`, `description`, `tags`, source subtitle/export paths, `status`, and `created_at`.
  - copywriter also writes `data/processed/<session_id>/match-<idx>-publishing.json` and appends one `PublishingPackage` row to `data/tmp/publishing-packages.jsonl`; this artifact is additive and must not change the `CopyAsset` field contract.
  - publishing package JSON must include `summary`, `cover_lines`, `evidence`, title candidates, recommended title, tags, transcript excerpt, source subtitle/export/recording paths, optional `streamer_name`, optional `cover_path`, optional `published_package_dir`, optional published video/cover/metadata paths, `status`, and `created_at`.
  - `cover_lines` must preserve multiple high-signal summary points when the subtitle excerpt supports them. It may reuse the recommended title, but it must not merely mirror a shortened title when `_summary_headline` can produce richer cover copy; keep the output to at most four short display lines.
  - copywriter streamer lookup must read both the normal orchestrator state and selected-run state files under `data/tmp/selected-recordings/*/orchestrator-state.json`; selected recording sessions otherwise lose the streamer name and publish as `unknown-streamer`.
  - when `source_export_path` exists, copywriter creates a title-rich publish package directory beside the exported video using the sanitized stem `{streamer_name or unknown-streamer} - {recommended_title} - {session timestamp or id hint}_matchNN`, records it as `published_package_dir`, and writes the publishable video alias inside that directory as `video.<source suffix>`. It should hardlink the MP4 when the filesystem supports it and fall back to copying; the original exporter output and `ExportAsset` row remain unchanged.
  - when `cover_path` exists, copywriter copies the generated cover image inside the package directory as `cover.<source suffix>` and records it as `published_cover_path`. This gives operators a named folder containing the video plus cover without needing to inspect `data/processed`, while avoiding repeated long filenames inside the folder.
  - when the publishable video alias is created, copywriter also writes `upload.txt` inside the package directory, records it as `published_metadata_path`, and keeps upload fields first: `Title`, `Description` (from summary), and `Hashtags`, followed by streamer/session/match, cover lines, evidence, and source paths. This keeps upload copy visible in the export directory without opening the processed JSON.
  - after writing the title-rich package directory, copywriter must best-effort remove legacy flat publish aliases beside the original export that use the same sanitized stem (`<stem><video suffix>`, `<stem>.jpg`, and `<stem>.txt`). Cleanup must never remove the original exporter output or generated source cover path, must skip directories/non-files, and must log cleanup failures instead of failing the stage. If those legacy flat aliases are present while manifest/state rows otherwise look processed, output completeness must return false so the next run regenerates/synchronizes the package row and cleans the old files.
  - when a valid highlight plan matches the current boundary snapshot, title/cover/evidence generation should prefer subtitle cues overlapping the plan windows; stale or boundary-mismatched highlight plans are ignored, and the full subtitle transcript is used as fallback.
  - when several subtitle cues overlap plan windows, copywriter metadata selection must sort candidates by highlight reason priority before cue timestamp: `highlight_keyword` and `condensed_key_event`, then `condensed_tactical`, `narration`, `match_start_context`, `match_end_context`, and `condensed_context`. This prevents ordinary edge/context windows from hiding the actual publishable moment.
  - optional cover rendering may use ffmpeg to extract a source frame and Pillow to render a `1920x1080` image under `data/processed/<session_id>/match-<idx>-cover.jpg`; it should prefer the raw `RecordingAsset` as the frame source and use source-timeline offsets there, but when the recording asset/file is unavailable and `source_export_path` exists it must fall back to the exported MP4 at export time `0.0`. Rendered cover text should use cover-line hierarchy: the first line is the larger yellow punch headline, later lines are white supporting summary points, all over a high-contrast title panel so the cover remains readable on busy gameplay frames. Missing recording/export files, missing ffmpeg, missing Pillow, or rendering errors must skip cover output while still writing publishing metadata.
  - placeholder or empty subtitles produce `status="placeholder_input"` with deterministic fallback metadata instead of crashing or blocking copy generation.
  - `copywriter-state.json` owns idempotency via the same `<session_id>:<match_index>` key shape used by subtitle/export stages; repeated runs must not append duplicate `CopyAsset` or `PublishingPackage` rows for already processed matches.
  - a processed state key is only a skip when the copy JSON, publishing JSON, generated cover file (when recorded), publish package directory (when recorded), package video alias, package cover alias (when a cover exists), and `upload.txt` sidecar (when an export exists) still exist, and no same-stem legacy flat publish aliases remain beside the export. If generated publishing artifacts are missing while manifest/state rows remain, copywriter must rebuild them and keep the latest `PublishingPackage` manifest row synchronized instead of appending a duplicate row.
  - missing subtitle files are skipped without marking the key processed, so later reruns can generate copy after the SRT arrives.
  - the current provider is deterministic local template generation. Future LLM-backed copy must keep the same `CopyAsset` manifest contract and make fallback status explicit rather than blocking downstream status checks.
- ffmpeg failure audit rows must include the canonical `decision` / `failure_category` / `is_retryable` / `reason_code` / `reason_detail` tuple and, when stderr is available, also `stderr_excerpt` (first 5 + last 15 lines, each <=240 chars, total <=4 KB) and `stderr_log_path` (relative path of the full stderr dump at `data/tmp/recorder-stderr/<job_id>-<attempt>.log` for recorder rows, `data/tmp/exporter-stderr/<session_id>_match<idx>-<attempt>.log` for exporter rows).
- `quality_below_actual_resolution` rows must include canonical decision fields with `decision="quality_rejected"`, `failure_category="quality_unusable_non_retryable"`, `reason_code="quality_below_actual_resolution"`, `is_retryable=false`, plus observed width/height, optional bitrate, and the configured minimum height.
- When ffmpeg fails with retryable reasons, recorder may defer placeholder emission and schedule cross-run retries:
  - scheduled event: `recording_retry_scheduled`
  - exhausted event: `recording_retry_exhausted`
  - max schedules controlled by `ARL_RECORDING_AUTO_RETRY_MAX_ATTEMPTS`
  - per-job backoff: after each transient yield, recorder persists `next_eligible_at_by_job_id[job_id]` using a 1s/5s/15s/60s schedule (capped at 60s for attempt >= 4) and skips the job until eligibility lapses
  - per-session cap: recorder persists `retries_by_session_id[session_id]` (incremented on every transient yield); when count reaches `ARL_RECORDER_SESSION_RETRY_BUDGET`, recorder emits one `recording_session_retry_budget_exceeded` audit per non-FAILED job in the session (with `decision="manual_required"`, `failure_category="unknown_unclassified_non_retryable"`, `reason_code="unknown_unclassified"`, `reason_detail="session_retry_budget_exceeded:<budget>"`), resets the counter, and orchestrator transitions those jobs to `failed`
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
- Recovery stage should expose a pending-action report view grouped by `job_id` for unattended handoff:
  - report must include pending action/job counts and grouped breakdowns by `action_type` and `failure_category`
  - report job entries must include bounded local identifiers, action keys, action types, failure categories, messages, and explicit resolve/fail command strings
  - report is read-only; it must not dispatch, resolve, fail, or requeue recovery actions
- Recovery stage should expose an aggregated summary view with total/pending/resolved/failed counts and grouped breakdowns.
- Recovery stage should support batch job updates (multi-job resolve/fail in one operation).
- Recovery stage should provide maintenance to control file growth:
  - archive terminal recovery events into `recovery-events-archive.jsonl`
  - compact terminal actions out of `recorder-recovery-actions.jsonl`
  - compact terminal keys out of `recovery-state.json`
- `arl maintenance --once` controls general long-run file growth:
  - for orchestrator input logs (`windows-agent-events.jsonl`, `recorder-events.jsonl`), archive only the already-consumed prefix indicated by `orchestrator-state.json` cursor offsets, then reset those offsets to `0`
  - for pure audit logs (`orchestrator-events.jsonl`, `subtitles-events.jsonl`, `exporter-events.jsonl`, `recovery-events.jsonl`), archive old prefix lines only when the file exceeds `ARL_MAINTENANCE_MAX_JSONL_BYTES`, keeping the most recent `ARL_MAINTENANCE_KEEP_RECENT_LINES`
  - rotate `data/tmp/launcher-logs/*.log` by newest mtime using `ARL_LAUNCHER_LOG_RETAIN_COUNT`
  - do not compact asset manifests (`*-assets.jsonl`, `match-boundaries.jsonl`, hints/signals) in this slice because downstream idempotency and status checks treat them as durable indexes
- `arl soak` provides repeated unattended health cycles:
  - default run is `cycles=3`, `interval_seconds=30`
  - each cycle runs `windows-agent` once, `orchestrator` once, `recorder`, `postprocess`, optional `maintenance`, then `status`
  - `--skip-recorder` and `--skip-postprocess` allow lower-impact dry-ish checks without recording or post-live writes
  - stage exceptions are captured in the JSON report and do not prevent the final `status` stage from running
  - command exits non-zero only when a stage raises; degraded/action-required status is reported in JSON for operator review
- Recorder `ffmpeg` path activation requires all of:
  - `recording.enable_ffmpeg == True`
  - `ffmpeg` available on PATH
  - and one source-specific prerequisite set:
    - direct stream mode: non-empty `stream_url`
    - browser capture mode: non-empty resolved capture input after format-specific defaults/fallbacks
- Exporter `ffmpeg` path activation requires all of:
  - `export.enable_ffmpeg == True`
  - matching `RecordingAsset` exists for the session
  - recording path exists and either is a video-like file or resolves through a chunk manifest to existing video-like chunk paths
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
| Signal rows exist but none classify to `in_game` | Semantic generator emits no hints by default and logs `strategy=no_signals`; template fallback only runs when `ARL_SEGMENTER_TEMPLATE_FALLBACK_ENABLED=1` |
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
| Subtitle provider is unsupported for transcription | Emit deterministic placeholder SRT and one `subtitle_fallback_placeholder` audit row |
| Subtitle provider is `faster-whisper` but dependency/model is unavailable | Emit deterministic placeholder SRT, one `subtitle_fallback_placeholder reason=model_unavailable`, and continue pipeline |
| Subtitle provider is `faster-whisper` but recording path is non-media (e.g., placeholder `.txt`) | Emit deterministic placeholder SRT and one `subtitle_fallback_placeholder reason=unsupported_suffix` |
| faster-whisper returns low language confidence below `ARL_WHISPER_MIN_LANGUAGE_PROBABILITY` | Emit deterministic placeholder SRT and one `subtitle_fallback_placeholder reason=low_language_confidence` with language/probability fields |
| faster-whisper runs successfully but returns no text segments inside the match boundary | Emit deterministic placeholder SRT and one `subtitle_fallback_placeholder reason=no_transcript_segments` with device/compute fields |
| faster-whisper returns accepted transcription segments | Emit real SRT cues and one `subtitle_transcribe_succeeded` row with language/probability fields |
| Export input references a missing subtitle file | Fail the export step deterministically instead of silently skipping subtitle burn-in |
| Exporter ffmpeg command burns in a subtitle file from a Windows absolute path | The `-vf` value uses forward slashes, escapes the drive colon (`D\:/...`), and wraps the filename in single quotes as `subtitles='...'` |
| `arl copywriter` sees an existing subtitle asset and optional export asset | Write one per-match copy JSON under `data/processed/<session>/`, append one `CopyAsset`, write one publishing package JSON, append one `PublishingPackage`, and mark the match key processed |
| `arl copywriter` sees a valid highlight plan for the current boundary | Prefer overlapping subtitle cues for `recommended_title`, `summary`, `cover_lines`, and `evidence` |
| `arl copywriter` sees both edge/context windows and high-signal highlight windows | Build metadata from the high-signal cue first even when the edge/context cue has the earlier timestamp |
| `arl copywriter` sees a stale highlight plan whose source boundary no longer matches the current boundary | Ignore the plan and generate metadata from the full subtitle transcript |
| `arl copywriter` sees a usable recording but ffmpeg/Pillow/frame extraction is unavailable | Leave `cover_path=None`, still write the publishing JSON, and still append the `PublishingPackage` row |
| `arl copywriter` sees a subtitle asset whose path does not exist | Log skip, do not append `CopyAsset`, and do not mark the match key processed |
| `arl copywriter` runs repeatedly on unchanged manifests/state | Do not duplicate copy or publishing manifest rows |
| `arl copywriter` has processed state and manifest rows but the copy or publishing JSON file is missing | Rebuild the missing generated file without appending duplicate manifest rows |
| `arl highlight-planner` sees a high-confidence boundary plus subtitle cues with long silent gaps | Append one conservative `HighlightPlanAsset` with padded, merged keep windows |
| `arl highlight-planner` sees weak data, missing subtitles, a low-confidence fallback boundary, or no meaningful reduction | Write no plan; missing subtitle paths must not mark the key processed |
| Exporter sees a valid highlight plan while `ARL_EXPORT_USE_HIGHLIGHT_PLANS=0` | Ignore the plan and export the full `MatchBoundary` interval |
| Exporter sees a valid highlight plan while `ARL_EXPORT_USE_HIGHLIGHT_PLANS=1` | Use `select` / `aselect` with `setpts` / `asetpts` and append the normal `ExportAsset` on success |
| Exporter sees a valid highlight plan on chunked media while `ARL_EXPORT_USE_HIGHLIGHT_PLANS=1` | Resolve every highlight window to concrete chunk-local spans, add those chunk files as inputs, render them with `trim` / `atrim` / `concat` instead of passing the manifest JSON to ffmpeg, and retime burned subtitles to the post-concat highlight output |
| Exporter sees no highlight plan or an invalid/stale highlight plan | Use the complete boundary export path |
| Exporter sees real SRT and default `auto/copy` codec with burn-in disabled | Stream-copy video/audio and mux SRT as `mov_text` soft subtitles |
| Exporter sees a valid edit plan on chunked media while `ARL_EXPORT_USE_EDIT_PLANS=1` | Expand timeline segments across chunks, preserve transforms per expanded span, keep subtitle burn-in after concat, and offset BGM/SFX input indexes by the number of media inputs |
| Exporter sees `boundary.is_complete=false` | Skip subtitles/highlights/export for that boundary and do not report missing outputs for it in status |
| `arl postprocess-reset --session-id <id>` runs after bad generated boundaries/subtitles/exports | Remove only that session's generated postprocess rows/state and generated files; keep raw recording assets intact for a later rerun |
| `arl postprocess-reset --session-id <id>` sees publishing package rows for that session | Remove target-session `PublishingPackage` rows and delete package JSON, cover files, published aliases, and published metadata sidecars when they are under generated roots |
| `arl postprocess-reset --session-id <id>` sees orphan generated files not present in manifests | Remove target-session files under `storage.processed_dir/<session_id>/` and export files named `<session_id>_match*` under `storage.export_dir` |
| `arl postprocess-reset` sees a removed artifact path outside `storage.processed_dir` / `storage.export_dir` | Remove the manifest row but skip file deletion and report the skipped path reason |
| A stage receives an unknown asset format or status | Reject or audit explicitly; do not guess |
| `ARL_RECORDING_ENABLE_FFMPEG=1` but `stream_url` missing | Recorder logs skip reason and writes placeholder recording artifact |
| `ARL_RECORDING_ENABLE_FFMPEG=1`, source is `browser_capture`, and resolved capture input is empty/unavailable | Recorder logs skip reason and writes placeholder recording artifact |
| Recorder invokes ffmpeg for an HLS direct-stream URL (`.m3u8`) | Initial recording command includes `-bsf:a aac_adtstoasc` before MP4 output so ADTS AAC can be copied into MP4 |
| Recorder invokes ffmpeg for a long direct-stream MP4 with `ARL_DIRECT_STREAM_TIMEOUT_SECONDS=7200` and default `ARL_RECORDING_FINALIZE_HEADROOM_SECONDS=60` | Initial recording command uses `-t 7140` and includes `-c copy -movflags +frag_keyframe+empty_moov+default_base_moof <output.mp4>` so ffmpeg can exit before an external 7200s wrapper deadline and the in-progress file remains probeable/playable if the supervisor still terminates near the boundary |
| Recorder invokes ffmpeg for a direct-stream URL with `ARL_RECORDING_SEGMENTED_ENABLED=1` | Initial recording command uses one process with `-f segment -segment_time <seconds> -reset_timestamps 1 ... chunks/recording-%05d.mp4`, writes `recording-chunks.json`, and appends a `RecordingAsset` whose path points to the manifest; it must not stop/restart ffmpeg per chunk |
| Recorder invokes ffmpeg with timeout <= twice the finalize headroom, or `ARL_RECORDING_FINALIZE_HEADROOM_SECONDS=0` | Initial recording command uses the full configured `ARL_DIRECT_STREAM_TIMEOUT_SECONDS` as `-t` |
| Recorder has multiple runnable jobs and `ARL_RECORDER_MAX_CONCURRENT_JOBS=N` where `N > 1` | Recorder starts up to N ffmpeg recording jobs concurrently, appends recorder audit rows with thread-safe JSONL writes, and applies recording assets/state transitions on the main thread as jobs finish |
| Direct-stream ffmpeg exits successfully and actual-resolution validation passes | Recorder emits `ffmpeg_record_succeeded`, appends the recording asset, saves `recorder-state.json` with the job id in `processed_job_ids`, then attempts a second copy-only remux command `-i <output.mp4> -map 0 -c copy -movflags +faststart <output.remux.mp4>` and atomically replaces `<output.mp4>` with the remuxed file when it exists |
| Direct-stream ffmpeg exits with an error but leaves a non-empty `recording-source.mp4` that ffprobe confirms has a video stream and satisfies the actual-resolution gate | Recorder emits the original `ffmpeg_record_failed`, then emits `ffmpeg_record_succeeded`, appends the mp4 recording asset, marks the job processed, and does not write a txt fallback placeholder |
| Direct-stream ffmpeg exits with an error and the partial `recording-source.mp4` is missing, empty, not ffprobeable, or fails the actual-resolution gate | Recorder keeps the existing retry/fallback behavior; below-resolution partials are deleted through `quality_below_actual_resolution` and do not emit a recording asset |
| Direct-stream post-success remux fails or writes no remux output | Recorder keeps the original fragmented `<output.mp4>`, removes any failed `<output.remux.mp4>`, emits `ffmpeg_record_succeeded`, and writes the normal recording asset |
| ffmpeg fails with retryable reason and retry budget remains | Recorder emits one `ffmpeg_record_failed` (decision `attempt_failed_yield_to_next_probe`) plus `recording_retry_scheduled`, writes `next_eligible_at_by_job_id[job]` per backoff schedule, and defers placeholder/asset emission until eligibility lapses |
| ffmpeg retry budget exhausted | Recorder emits `recording_retry_exhausted`, writes placeholder artifact, and emits recording asset |
| ffmpeg fails with clear HTTP 4xx input-side errors (`401/403/404/410`, `server returned 4xx`) | Treat as non-recoverable input/configuration failure (decision `attempt_failed`); do not schedule cross-run retry; emit placeholder/manual path |
| ffmpeg fails with clear non-recoverable reason in the same run | Recorder should stop further in-run ffmpeg attempts immediately and proceed with fallback/manual path |
| Job is within its backoff window after a transient yield (`next_eligible_at_by_job_id[job] > now`) | Recorder logs `job deferred ...` once and skips the job without invoking ffmpeg; eligibility entry is preserved |
| Per-session transient yields reach `ARL_RECORDER_SESSION_RETRY_BUDGET` | Recorder emits one `recording_session_retry_budget_exceeded` audit per non-FAILED job in the session, resets `retries_by_session_id[session]` to 0, clears `next_eligible_at_by_job_id` entries for those jobs, and orchestrator transitions them to `failed` |
| ffmpeg attempt produces non-empty stderr | Recorder must populate audit `stderr_excerpt` (head 5 + tail 15 lines, total <=4 KB) and `stderr_log_path` pointing at the full stderr dump on disk |
| Recorder starts with more than `ARL_RECORDER_STDERR_RETAIN_COUNT` files in `data/tmp/recorder-stderr/` | Recorder rotates files at startup, keeping only the newest N by mtime |
| `recorder-state.json` written by an older recorder release lacks `next_eligible_at_by_job_id` / `retries_by_session_id` | Recorder loads it and supplies empty dicts (no migration required, no error) |
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
| Exporter handles a session whose orchestrator state records `platform=<platform>` | Write the final export artifact under `data/exports/<platform>/...` (for example `data/exports/douyin/<session>_match01.mp4`) and persist that path in `export-assets.jsonl`; if platform cannot be resolved, use `data/exports/unknown/...` rather than mixing platforms in the root export directory |
| `ARL_EXPORT_ENABLE_FFMPEG=1` but recording input is not a video file | Exporter logs the prerequisite reason, records the match key in `deferred_match_keys`, and writes no `.txt` export artifact |
| `ffmpeg` command exits non-zero | Stage logs failure reason, emits fallback audit diagnostics, records the match key in `deferred_match_keys`, and writes no `.txt` export artifact |
| Exporter ffmpeg exits zero but `ffprobe` reports no video stream or zero-duration output | Emit `ffmpeg_export_failed` plus `ffmpeg_export_fallback_placeholder`, delete the invalid MP4 shell, record the match key as deferred, and write no `.txt` export artifact |
| Exporter sees a non-retryable ffmpeg failure | Emit exactly one `ffmpeg_export_failed` row plus `ffmpeg_export_fallback_placeholder`; do not run further attempts; record the match key as deferred; increment the match-level fallback counter by 1 |
| Exporter reaches `ARL_EXPORTER_BATCH_FALLBACK_BUDGET` consecutive match-level fallbacks | Emit one `ffmpeg_export_batch_aborted` row with `consecutive_fallbacks` and `remaining_matches`; leave the remaining boundaries unprocessed and absent from `processed_match_keys` |
| `data/raw/session-*/recording-source.mp4` exists, is older than `--min-age-seconds`, and lacks a `RecordingAsset` row | `arl status` reports degraded `unregistered_recordings`; `arl postprocess` prints a repair hint; `arl repair-recording-assets` appends one typed `RecordingAsset` row after positive ffprobe duration |
| Raw MP4 is still being written or was modified too recently | `repair-recording-assets` skips it as recent and does not append a manifest row |
| Raw MP4 is unreadable, empty, or ffprobe cannot report positive duration | `repair-recording-assets` increments `skipped_unreadable` and does not append a manifest row |
| Stage state contains a processed key but its output file/manifest row is missing | The stage logs a reprocessing message and regenerates the missing output instead of treating the state key as complete |
| Operator presses Ctrl+C while the concurrent recorder is waiting for worker futures | Recorder briefly drains already completed worker outcomes, applies those outcomes to `recording-assets.jsonl` and `recorder-state.json`, logs the interrupted drain, and then preserves the interrupt; `record-rooms` still runs one final orchestrator pass for the selected-run state, and `python -m arl.cli ...` exits 130 without a Python traceback |

### 5. Good / Base / Bad Cases

- Good:
  - Recorder emits one `RecordingAsset`, segmenter emits two `MatchBoundary` rows, subtitles emits one `SubtitleAsset` per match, exporter writes final output with stable naming, and copywriter emits one publishable copy JSON per match.
  - Copywriter sees a `match_start_context` cue near match start and a later `highlight_keyword` cue; title, cover lines, and evidence lead with the highlight cue.
  - `postprocess` final summary shows `unregistered_recordings=0` and `missing_subtitles=missing_exports=missing_copies=0` for the processed match set.
- Base:
  - Recorder succeeds, segmenter emits one low-confidence match boundary, export is deferred pending operator review.
  - A completed raw MP4 exists but recorder was interrupted before manifest append; operator runs `repair-recording-assets`, then reruns `postprocess`.
- Bad:
  - Exporter guesses `match_index` from filenames instead of reading typed metadata.
  - Segmenter emits negative or overlapping timestamps without validation.
  - Recorder writes files but never records their source type or time bounds.
  - `ffmpeg` failure aborts the whole pipeline and prevents manifest emission.
  - `postprocess` reports all stage `processed=0` without surfacing that a completed raw MP4 is not registered.

### 6. Tests Required

- Unit test: recorder manifest or asset output includes source type, path, and start and end timestamps.
- Unit test: segment boundary validation rejects negative or reversed ranges.
- Unit test: segmenter derives multi-match boundaries from `in_game` stage hints and keeps `match_index` sequential.
- Unit test: vision scene stitching does not classify short loading-to-end spans under the configured minimum complete-match duration as complete matches.
- Unit test: segmenter accepts `detected_at` hints by converting them relative to recording start.
- Unit test: segmenter preserves idempotency and does not duplicate boundaries on rerun.
- Unit test: segmenter keeps single-boundary fallback when hints are missing or unusable.
- Unit test: segmenter preserves sub-minute completed recording durations instead of clamping them to one minute.
- Unit test: stage-hint writer appends typed rows for both `at_seconds` and `detected_at` input shapes.
- Unit test: stage-hint CLI parser enforces timestamp input and rejects invalid datetime formats.
- Unit test: auto stage-hint service derives periodic `in_game` anchors from recording duration and segment interval.
- Unit test: auto stage-hint service remains idempotent across repeated runs.
- Unit test: auto stage-hint service skips sessions that already have `in_game` hints.
- Unit test: semantic stage-hint service emits per-cycle stage sequence (`champion_select/loading/in_game/post_game`) when template fallback is explicitly enabled.
- Unit test: semantic stage-hint service remains idempotent across repeated runs.
- Unit test: semantic stage-hint service skips sessions that already have stage hints.
- Unit test: semantic stage-hint service keeps `in_game` timestamp inside duration for short recordings.
- Unit test: semantic stage-hint service preserves sub-minute completed recording durations.
- Unit test: semantic stage-hint service uses signal-driven generation when classified signals include `in_game`.
- Unit test: semantic stage-hint service emits no template hints by default when signals do not contain usable `in_game`.
- Unit test: semantic stage-hint service falls back to template generation when signals do not contain usable `in_game` and template fallback is explicitly enabled.
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
- Unit test: subtitle service skips full-recording ASR for long low-confidence fallback boundaries and emits `reason=low_confidence_full_recording`.
- Unit test: subtitle transcribe failures that are media/path-specific do not disable the CPU whisper candidate for remaining batch items.
- Unit test: highlight planner emits padded/merged keep windows from subtitle narration and highlight keywords when reduction is meaningful.
- Unit test: highlight planner omits plans for weak/no-reduction inputs and does not mark missing subtitle paths processed.
- Unit test: highlight planner remains idempotent across repeated runs and supports session/match filters.
- Unit test: exporter refuses to burn subtitles when the declared subtitle file is missing.
- Unit test: exporter with a valid highlight plan builds a command containing `select`, `aselect`, `setpts`, and `asetpts`.
- Unit test: exporter ffmpeg command escapes Windows subtitle filter paths with forward slashes, escaped drive colon, and single quotes.
- Unit test: exporter edit-plan burn-in retimes subtitles and applies the subtitle filter after concat, not before punch-in transforms.
- Unit test: exporter writes final artifacts under the orchestrator platform subdirectory so same-streamer multi-platform outputs remain distinguishable.
- Unit test: exporter reads selected-recording orchestrator state files when resolving a session platform.
- Unit test: exporter removes partial MP4 output before deferring a failed export.
- Unit test: exporter uses stream copy instead of subtitle burn-in when subtitle input is the deterministic placeholder SRT.
- Unit test: exporter uses stream copy plus `mov_text` soft subtitles for real SRT inputs when burn-in is disabled.
- Unit test: exporter ignores highlight plans by default and exports the full boundary interval.
- Unit test: exporter applies highlight plan `select` / `aselect` filters only when `ARL_EXPORT_USE_HIGHLIGHT_PLANS=1`.
- Unit test: subtitles/exporter/status honor `MatchBoundary.is_complete=False` as an unavailable match.
- Unit test: exporter CLI supports scoped session/match filters and force reprocess.
- Unit test: exporter defers low-confidence full-recording fallback boundaries without writing `.mp4` or `.txt` artifacts.
- Unit test: exporter ffmpeg failure records `deferred_match_keys` without appending placeholder `ExportAsset` rows.
- Unit test: exporter treats zero-exit MP4 outputs with no video stream as failed and defers the match instead of emitting `ffmpeg_export_succeeded`.
- Unit test: copywriter emits deterministic title/copy JSON plus one `CopyAsset` from an existing subtitle asset and optional export asset.
- Unit test: copywriter emits one `PublishingPackage` with summary, cover lines, evidence, and source paths while preserving the existing `CopyAsset` contract.
- Unit test: copywriter cover lines expand a shortened recommended title with additional high-signal summary points from the subtitle excerpt.
- Unit test: copywriter creates a streamer/title/session named published video path beside the export and a same-stem cover image when source export and cover assets exist.
- Unit test: copywriter places `video.<ext>`, `cover.<ext>`, and `upload.txt` inside a title-rich `published_package_dir` beside the original export.
- Unit test: copywriter writes an upload-oriented `upload.txt` metadata sidecar beside the published video and cover.
- Unit test: copywriter regenerates missing published video/cover/metadata aliases for an already processed match without duplicating the publishing package manifest row.
- Unit test: copywriter removes same-stem legacy flat published video/cover/metadata aliases on rerun without deleting the original exporter output or duplicating manifest rows.
- Unit test: copywriter renders and publishes a same-stem cover from the exported MP4 when the raw recording asset/file is unavailable.
- Unit test: copywriter reads selected-recording orchestrator state files when resolving streamer names.
- Unit test: copywriter prefers highlight-window subtitle cues for publishing metadata when the highlight plan matches the current boundary.
- Unit/integration test: copywriter highlight cue selection prefers `highlight_keyword` or `condensed_key_event` evidence over earlier `match_start_context` evidence in `recommended_title`, `cover_lines`, and `PublishingPackage.evidence`.
- Unit test: copywriter skips optional cover rendering when recording/ffmpeg/Pillow is unavailable and still writes publishing metadata.
- Unit test: copywriter remains idempotent across repeated runs.
- Unit test: copywriter skips missing subtitle paths without marking the match processed.
- Unit test: postprocess invokes `highlight-planner` after `subtitles` and before `exporter`, then invokes `copywriter` after `exporter`.
- Unit test: postprocess accepts `--session-id/--session-ids` and passes the session scope to filter-aware stages.
- Unit test: `postprocess-reset` removes only the target session's generated rows/state/files while preserving other sessions and raw recording assets.
- Unit test: `postprocess-reset` removes target-session `publishing-packages.jsonl` rows and deletes package JSON, cover assets, published video/cover aliases, and published metadata sidecars under generated roots.
- Unit test: `postprocess-reset` removes orphan generated files for the target session even when manifest rows are already missing.
- Unit test: `postprocess-reset` skips deleting manifest artifact paths outside generated roots.
- Unit test: status reports unregistered raw MP4 files as degraded diagnostics without mutating state.
- Unit test: status ignores historical exporter fallback/batch-abort rows after later MP4 export assets resolve the affected match/session.
- Unit test: `repair-recording-assets` appends one `RecordingAsset` for an unregistered completed raw MP4 and remains idempotent on rerun.
- Unit test: subtitle/export/copy processed state does not suppress regeneration when the declared output file is missing.
- Unit test: CLI parser includes `copywriter`.
- Unit test: CLI parser includes `highlight-planner`.
- Unit test: recorder with `enable_ffmpeg=True` but missing `stream_url` still emits one placeholder asset.
- Unit test: recorder direct-stream ffmpeg command includes fragmented MP4 `-movflags` next to stream-copy output.
- Unit test: recorder HLS direct-stream ffmpeg command includes `-bsf:a aac_adtstoasc`.
- Unit test: recorder writes concrete attempt start/end timestamps for successful recordings while the live session is still open.
- Unit test: recorder subtracts finalize headroom from long direct-stream ffmpeg `-t` values and leaves short captures unchanged.
- Unit test: recorder remuxes successful direct-stream recordings to `+faststart` with `-map 0 -c copy`, replacing the original path only after the remux output exists and only after the recording asset plus `processed_job_ids` state are durable.
- Unit test: recorder remux failure keeps the original recording and still emits `ffmpeg_record_succeeded`.
- Unit test: concurrent recorder interruption drains and persists completed worker outcomes before surfacing `KeyboardInterrupt`.
- Unit test: recorder treats ffmpeg HTTP 4xx failures as non-recoverable and skips cross-run retry scheduling.
- Unit test: recorder stops in-run ffmpeg retries early when a non-recoverable reason is detected.
- Unit test: recorder infers actionable manual-recovery action mapping from `stop_reason` when `failure_category` is missing, and keeps inspect fallback only for opaque reasons.
- Unit test: exporter with `enable_ffmpeg=True` and non-video recording input defers the match without writing a `.txt` export artifact.
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

#### Wrong

```python
if key in processed_match_keys:
    continue
```

- Treats a stale state key as proof of completion even when the SRT/export/copy file was deleted or never written.
- Makes `arl postprocess --once` print `processed=0` forever while `arl status` still reports missing outputs.

#### Correct

```python
if key in processed_match_keys and output_path.exists():
    continue
rebuild_missing_output(...)
```

- Keeps reruns idempotent when outputs exist.
- Allows local recovery when manifests/state survive but generated files are missing.

#### Wrong

```python
copy_path.write_text(json.dumps(copy_payload), encoding="utf-8")
```

- Creates an orphan per-match JSON file with no manifest row
- Leaves `arl status` unable to count missing/present copy outputs
- Allows repeated `arl copywriter` or `arl postprocess` runs to append divergent manual samples

#### Correct

```python
draft = build_copy_draft(subtitle_asset, export_asset)
copy_path = write_copy_json(draft)
append_model(
    temp_dir / "copy-assets.jsonl",
    CopyAsset(
        session_id=draft.session_id,
        match_index=draft.match_index,
        path=str(copy_path),
        title=draft.recommended_title,
        description=draft.description,
        tags=draft.tags,
        subtitle_path=draft.source_subtitle_path,
        export_path=draft.source_export_path,
        created_at=draft.created_at,
    ),
)
copywriter_state.processed_match_keys.append(f"{draft.session_id}:{draft.match_index}")
```

- Keeps the JSON artifact discoverable through the typed `CopyAsset` manifest
- Preserves stage idempotency through `copywriter-state.json`
- Lets `arl status` compute `missing_copies` from the manifest contract

#### Wrong

```python
selected_cues = sorted(overlapping_highlight_cues, key=lambda cue: cue.started_at_seconds)
title = build_title_from(selected_cues[0])
```

- Lets `match_start_context` or other edge windows outrank the actual highlight just because they appear earlier.
- Produces cover lines and evidence from setup/context instead of the publishable moment.

#### Correct

```python
reason_priority = {
    "highlight_keyword": 0,
    "condensed_key_event": 0,
    "condensed_tactical": 1,
    "narration": 2,
    "match_start_context": 3,
    "match_end_context": 4,
    "condensed_context": 5,
}
selected_cues = sorted(
    overlapping_highlight_cues,
    key=lambda cue: (best_overlapping_reason_priority(cue, reason_priority), cue.started_at_seconds),
)
title = build_title_from(selected_cues[0])
```

- Keeps the headline, cover text, and evidence anchored to high-signal windows.
- Preserves chronological ordering only as a tie-breaker within the same signal strength.

#### Wrong

```python
plan = highlight_plan_map.get((boundary.session_id, boundary.match_index))
if plan is not None:
    command = planned_ffmpeg_command(boundary, plan)
else:
    command = full_boundary_command(boundary)
```

- Makes highlight-condensed clips the default export whenever a plan file exists.
- Can turn a complete 60+ minute match boundary into a short unusable clip that ends before the base explosion.
- Forces re-encoding when subtitles or select filters are applied, degrading source quality.

#### Correct

```python
if settings.export.use_highlight_plans:
    plan = valid_highlight_plan(highlight_plan_map.get(key), boundary)
else:
    plan = None

if plan is None and settings.export.ffmpeg_video_codec in {"auto", "copy"}:
    command = stream_copy_full_boundary_with_soft_subtitles(boundary, subtitle)
else:
    command = planned_or_explicit_encode_command(boundary, plan, subtitle)
```

- Full-match export remains the default and produces a boundary from game entry to match end.
- Highlight condensation is an explicit opt-in via `ARL_EXPORT_USE_HIGHLIGHT_PLANS=1`.
- Default real-SRT exports preserve source video/audio by stream-copying and muxing subtitles as `mov_text`.
