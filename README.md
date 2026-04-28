# auto-record-live

Local-first MVP for:

* monitoring one fixed Douyin streamer
* automatically recording League of Legends live sessions
* segmenting recordings into per-match clips
* generating offline subtitles
* exporting one subtitle-burned video per match

## MVP Architecture

Runtime split:

* Windows host:
  * browser/session automation for Douyin
  * optional browser capture fallback
* WSL2 Ubuntu:
  * orchestration
  * recording control
  * match segmentation
  * subtitle generation
  * export

## Repository Layout

```text
src/arl/
  cli.py
  config.py
  windows_agent/
  orchestrator/
  recorder/
  segmenter/
  subtitles/
  exporter/
  shared/
```

## Development Status

This repository currently contains the initial skeleton only.

Implemented now:

* project structure
* shared config and event models
* CLI entrypoints
* first-pass Windows agent poller with JSONL event output
* orchestrator event consumer with durable session/job state
* file-backed post-live pipeline scaffolding:
  * recorder asset manifest emission
  * segmenter multi-match boundary emission from optional `match-stage-hints.jsonl` (`in_game` anchors) with deterministic single-boundary fallback
  * heuristic auto stage-hint seeding via `arl stage-hints-auto` from recording duration + `ARL_RECORDING_SEGMENT_MINUTES`
  * semantic auto stage-hint seeding via `arl stage-hints-semantic` (best-effort subtitle-signal ingest first, then signal-driven when usable `in_game` markers exist, otherwise template fallback)
  * subtitle-driven stage-signal extraction via `arl stage-signals-from-subtitles` with idempotent ingest state (`stage-signal-ingest-state.json`)
  * stage text classification now supports both English and Chinese LoL keywords (for manual signals and subtitle-derived signals), and can be overridden via `ARL_STAGE_KEYWORDS_PATH`
  * subtitle worker auto-triggers stage-signal extraction after writing subtitle assets (best-effort; signal ingest failures do not block subtitle asset output)
  * manual stage-signal ingestion via `arl stage-signal` CLI
  * manual stage-hint ingestion via `arl stage-hint` CLI (supports `--at-seconds` or `--detected-at`)
  * local subtitle generation with optional transcription path and deterministic placeholder fallback
  * export artifact manifest emission
* optional `ffmpeg` execution path:
  * recorder direct-stream capture when stream URL is available
  * recorder browser-capture path when source is `browser_capture` and capture input is configured
  * exporter clip + subtitle burn-in when recording input is a real video file
  * recorder/orchestrator classify clear HTTP 4xx ffmpeg input failures as non-recoverable to avoid noisy cross-run retries
  * recorder stops in-run ffmpeg retry loops early when failure reason is already non-recoverable
* manual recovery action pipeline:
  * recorder writes structured actions to `data/tmp/recorder-recovery-actions.jsonl` for failed jobs
  * recovery worker dispatches pending actions to `data/tmp/recovery-events.jsonl` with idempotent state tracking
  * when failure category is missing, recorder infers actionable recovery category/action type from failure reason text to reduce generic inspect-only actions
* direct-stream probe improvements:
  * Playwright probe can combine page-content extraction and observed network candidates
  * probe can promote to `live` when stream URL is detected even if page live marker is unavailable
  * probe normalization now tolerates multi-layer percent-encoded (`%25` wrapped) and `\xNN` escaped stream URL payload fragments
  * when Playwright probing fails, windows-agent falls back to HTTP page fetch detection and can still promote to direct-stream when stream URL payloads are found

Not implemented yet:

* production-grade direct-stream acquisition hardening across Douyin page changes and anti-bot variance
* production-grade LoL semantic stage-hint producer (champion select/loading/in-game/post-game)
* production-grade offline ASR integration hardening with `faster-whisper`
* production-grade retry/recovery around `ffmpeg` failures

## Quick Start

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
npm install
```

Create a local `.env` from `.env.example` if you want the CLI to auto-load streamer settings from the repo root.

Inspect available commands:

```bash
arl --help
```

Run the Windows agent once:

```bash
.venv/bin/python -m arl.cli windows-agent --once
```

For real browser-backed room probing, install Playwright browsers:

```bash
npx playwright install chromium
```

Then set the room URL and streamer name before testing, or write them into `.env`:

```bash
export ARL_DOUYIN_ROOM_URL="https://live.douyin.com/<room>"
export ARL_STREAMER_NAME="<streamer>"
.venv/bin/python -m arl.cli windows-agent --once
```

Note:

* The current Playwright probe opens a persistent Chromium profile.
* The first real test may require manual login inside the opened browser window.
* Playwright probe now includes best-effort direct-stream URL extraction from page content. If extraction fails, it falls back to `browser_capture`.
* Current real test scope stops at `browser probe -> live/offline detection -> state/event output`.
* ffmpeg direct-stream recording is wired when `stream_url` is discovered and `ARL_RECORDING_ENABLE_FFMPEG=1`.
* ffmpeg browser capture recording is attempted when `source_type=browser_capture`, `ARL_RECORDING_ENABLE_FFMPEG=1`, and `ARL_BROWSER_CAPTURE_INPUT` is configured.
* `ARL_BROWSER_CAPTURE_FORMAT=auto` chooses `gdigrab` on Windows and `x11grab` on non-Windows runtimes.
* recorder emits structured ffmpeg audit events to `data/tmp/recorder-events.jsonl` and can schedule retry runs for retryable failures.
* run `.venv/bin/python -m arl.cli recovery` to dispatch pending manual recovery actions.
* run `.venv/bin/python -m arl.cli recovery --list-pending` to query pending dispatched actions.
* run `.venv/bin/python -m arl.cli recovery --summary` to view aggregated recovery status counts.
* run `.venv/bin/python -m arl.cli recovery --resolve-job-id <job_id> --message "<note>"` to mark pending actions resolved.
* run `.venv/bin/python -m arl.cli recovery --fail-job-id <job_id> --message "<note>"` to mark pending actions failed.
* run `.venv/bin/python -m arl.cli recovery --resolve-job-ids <job1,job2,...> --message "<note>"` for batch resolve.
* run `.venv/bin/python -m arl.cli recovery --fail-job-ids <job1,job2,...> --message "<note>"` for batch fail.
* run `.venv/bin/python -m arl.cli recovery --resolve-action-key <action_key> --message "<note>"` for precise single-action resolve.
* run `.venv/bin/python -m arl.cli recovery --fail-action-key <action_key> --message "<note>"` for precise single-action fail.
* run `.venv/bin/python -m arl.cli recovery --maintenance` to archive terminal recovery events and compact terminal actions/state.
* run `.venv/bin/python -m arl.cli stage-hints-auto` to auto-seed `in_game` stage hints for sessions that do not yet have anchors.
* run `.venv/bin/python -m arl.cli stage-hints-semantic` to auto-seed semantic stage hints for sessions that do not yet have any stage-hint rows (command now performs best-effort subtitle-signal ingest before generation).
* run `.venv/bin/python -m arl.cli stage-hints-semantic --stage-keywords-path examples/stage-keywords.example.json` to override stage keywords for this command only.
* run `.venv/bin/python -m arl.cli stage-signals-from-subtitles` to extract first-per-stage semantic signals from SRT subtitle assets for unprocessed subtitle rows.
* run `.venv/bin/python -m arl.cli stage-signals-from-subtitles --stage-keywords-path examples/stage-keywords.example.json` to override stage keywords for this command only.
* run `.venv/bin/python -m arl.cli stage-signals-from-subtitles --force-reprocess` to rescan already-processed subtitle rows while deduplicating previously emitted identical signals.
* run `.venv/bin/python -m arl.cli stage-signals-from-subtitles --session-id <id> --subtitle-path <path> --match-index <n>` for targeted reprocess on one session/path/match index (also supports CSV `--session-ids` / `--subtitle-paths` / `--match-indices`).
* filtered runs emit observability summary logs with `total_assets` and `matched_assets`; ingest summary also includes `skipped_already_processed` and `skipped_missing_subtitle`; when no assets match, command emits explicit no-match filter log and exits with zero-result summary.
* stage-signal ingest state is compacted automatically against current `subtitle-assets.jsonl` to avoid stale key/fingerprint growth over long runs.
* set `ARL_STAGE_KEYWORDS_PATH=examples/stage-keywords.example.json` to use custom stage keyword mapping.
* running `.venv/bin/python -m arl.cli subtitles` now also performs best-effort `stage-signals-from-subtitles` ingestion after subtitle assets are emitted.
* when `subtitles` runs with `--session-id/--session-ids` and/or `--match-index/--match-indices`, the auto-triggered stage-signal ingest inherits the same filter scope to avoid scanning unrelated subtitle assets.
* run `.venv/bin/python -m arl.cli subtitles --stage-keywords-path examples/stage-keywords.example.json` to override subtitle-triggered stage-signal ingest keywords for this command only.
* run `.venv/bin/python -m arl.cli subtitles --session-id <id> --match-index <n>` for targeted subtitle generation on one session/match (also supports CSV `--session-ids` / `--match-indices` with intersection semantics).
* filtered `subtitles` runs emit boundary filter diagnostics (`total_boundaries`, `matched_boundaries`) and explicit no-match logs when no boundary rows satisfy the supplied filters.
* run `.venv/bin/python -m arl.cli stage-signal --session-id <session_id> --text "in game scoreboard" --at-seconds 95` to append one semantic signal row.
* run `.venv/bin/python -m arl.cli stage-hint --session-id <session_id> --stage in_game --at-seconds 120` to append one match-stage hint.
* run `.venv/bin/python -m arl.cli stage-hint --session-id <session_id> --stage post_game --detected-at 2026-04-26T12:40:00+08:00` to append one absolute-timestamp hint.
* recovery emits `recording_retry_scheduled` into `data/tmp/recorder-events.jsonl` only when all dispatched recovery actions for that job are fully resolved.

### Breaking Change: Decision Log Schema (2026-04-28)

Targeted core events now use a canonical decision schema and no longer rely on legacy free-form `reason/recoverable` semantics:

- `recording_retry_scheduled`
- `ffmpeg_record_failed`
- `ffmpeg_fallback_placeholder`
- `recording_manual_recovery_required`
- `manual_recovery_action_dispatched`
- `manual_recovery_action_resolved`
- `manual_recovery_action_failed`

Canonical fields:

- `decision`
- `failure_category`
- `is_retryable`
- `reason_code`
- `reason_detail`

Operator migration notes:

- Treat `reason_detail` as the human-readable reason text; do not parse `reason` as source-of-truth for the core event set.
- Treat `is_retryable` as source-of-truth retryability; do not infer from legacy `recoverable`.
- `reason_code` is now strict enum only: `http_4xx`, `http_5xx`, `network_timeout`, `ffmpeg_process_error`, `unknown_unclassified`.
- Unknown classification is fail-closed: `failure_category=unknown_unclassified_non_retryable`, `is_retryable=false`, and manual-recovery path.

Quick old/new mapping examples:

- old: `reason=missing_binary, recoverable=false` -> new: `reason_code=unknown_unclassified, failure_category=unknown_unclassified_non_retryable, is_retryable=false`
- old: `reason="[https ...] Server returned 404 Not Found"` -> new: `reason_code=http_4xx, failure_category=http_4xx_non_retryable, is_retryable=false`
- old: `reason="[https ...] Server returned 503 Service Unavailable"` -> new: `reason_code=http_5xx, failure_category=http_5xx_retryable, is_retryable=true`

Development override for local testing:

```bash
ARL_AGENT_FORCE_STATE=live \
ARL_AGENT_FORCE_STREAM_URL=https://example.invalid/live.m3u8 \
.venv/bin/python -m arl.cli windows-agent --once
```

Process orchestrator events once:

```bash
.venv/bin/python -m arl.cli orchestrator --once
```

Or run the orchestrator loop:

```bash
.venv/bin/python -m arl.cli orchestrator
```

Orchestrator outputs:

* state file: `data/tmp/orchestrator-state.json`
* audit log: `data/tmp/orchestrator-events.jsonl`
* input event log: `data/tmp/windows-agent-events.jsonl`
* recorder audit log: `data/tmp/recorder-events.jsonl`
* recorder recovery actions: `data/tmp/recorder-recovery-actions.jsonl`
* recovery dispatch events: `data/tmp/recovery-events.jsonl`
* recovery dispatch archive: `data/tmp/recovery-events-archive.jsonl`

Useful environment overrides:

* `ARL_ORCHESTRATOR_POLL_INTERVAL_SECONDS`
* `ARL_ORCHESTRATOR_AGENT_EVENT_LOG`
* `ARL_ORCHESTRATOR_RECORDER_EVENT_LOG`
* `ARL_ORCHESTRATOR_STATE_FILE`
* `ARL_ORCHESTRATOR_AUDIT_LOG`
* `ARL_ORCHESTRATOR_AUTO_CREATE_RECORDING_JOB`
* `ARL_RECORDING_ENABLE_FFMPEG` (`1` to enable direct-stream ffmpeg attempt)
* `ARL_DIRECT_STREAM_TIMEOUT_SECONDS`
* `ARL_RECORDING_FFMPEG_MAX_RETRIES` (default `1`)
* `ARL_RECORDING_AUTO_RETRY_MAX_ATTEMPTS` (default `2`, set `0` to disable cross-run auto-retry scheduling)
* `ARL_BROWSER_CAPTURE_INPUT` (empty by default; required to enable browser-capture ffmpeg path)
* `ARL_BROWSER_CAPTURE_FORMAT` (default `auto`; resolves to `gdigrab` on Windows, `x11grab` otherwise)
* `ARL_BROWSER_CAPTURE_RESOLUTION` (default `1920x1080`)
* `ARL_BROWSER_CAPTURE_FPS` (default `30`)
* `ARL_BROWSER_CAPTURE_TIMEOUT_SECONDS` (default `20`)
* `ARL_STAGE_KEYWORDS_PATH` (optional JSON file to override stage keyword lists for `champion_select/loading/in_game/post_game`)
  * when the file is missing/invalid, pipeline logs fallback reason and continues with built-in defaults.
  * CLI `--stage-keywords-path` on `stage-hints-semantic` / `stage-signals-from-subtitles` / `subtitles` has higher priority than this env key.
  * see example file: `examples/stage-keywords.example.json`
* `ARL_EXPORT_ENABLE_FFMPEG` (`1` to enable export ffmpeg path)
* `ARL_EXPORT_FFMPEG_PRESET` (default `veryfast`)
* `ARL_EXPORT_FFMPEG_CRF` (default `23`)
* `ARL_EXPORT_FFMPEG_TIMEOUT_SECONDS` (default `120`)
* `ARL_EXPORT_FFMPEG_MAX_RETRIES` (default `1`)

## Suggested Next Steps

1. Implement Windows Douyin live-room watcher.
2. Implement direct stream recording path.
3. Add browser capture fallback.
4. Add LoL state-machine segmentation.
5. Add offline subtitle generation.
6. Add final export pipeline.
