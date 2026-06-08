# Quality Guidelines

> Code quality standards for backend development.

---

## Overview

<!--
Document your project's quality standards here.

Questions to answer:
- What patterns are forbidden?
- What linting rules do you enforce?
- What are your testing requirements?
- What code review standards apply?
-->

The current MVP backend is a local pipeline with file-backed contracts and typed Pydantic payloads. Quality work here means preserving contract clarity, idempotent state transitions, and recoverability after partial failures.

---

## Forbidden Patterns

<!-- Patterns that should never be used and why -->

- Silent schema drift between producer and consumer models.
  - If `windows_agent` changes an event field, the orchestrator model and tests must change in the same task.
- Monolithic untyped dictionaries passed across stage boundaries when a typed model already exists.
- Parsing append-only event logs as one full JSON blob instead of line-by-line JSONL.
- Crashing the whole polling loop on a single malformed runtime row.
- Writing new logic into `utils.py` or `helpers.py` without a domain-specific name.
- Mixing path and env lookup code into stage logic when `config.py` should own it.
- Re-encoding or mutating source metadata in place without preserving the original source path or source type in records.
- Calling `Path.read_text()` / `Path.write_text()` on durable state JSON or event JSONL files without an explicit `encoding="utf-8"` argument.
  - On non-UTF-8 OS locales (for example Windows zh-CN with CP936/GBK) Python silently uses the platform encoding and produces files that downstream stages cannot decode.
  - Mirror the existing pattern used by every other state store (`recorder`, `exporter`, `recovery`, `subtitles`, `segmenter`, `windows_agent`): both `read_text` and `write_text` must pass `encoding="utf-8"`.

---

## Required Patterns

<!-- Patterns that must always be used -->

- Use Pydantic models for durable file payloads and cross-stage contracts.
- Prefer append-only JSONL for runtime event and audit streams.
- Make lifecycle handlers idempotent when duplicate upstream events are plausible.
- Persist state through typed state-store boundaries instead of ad hoc file writes in services.
- Log meaningful stage transitions with component-scoped prefixes.
- Keep service entrypoints small; extract persistence and parsing helpers into same-stage modules when logic grows.

---

## Testing Requirements

<!-- What level of testing is expected -->

- Add or update unit tests for every new state transition or event contract branch.
- For orchestrator-style polling code, tests must cover happy path, duplicate events, malformed row tolerance, and incremental cursor behavior.
- For recorder, segmenter, subtitles, and exporter work, test the stage contract even if external tools are stubbed.
- When external binaries are involved, mock process invocation in unit tests and keep one thin integration seam for later end-to-end checks.

---

## Code Review Checklist

<!-- What reviewers should check -->

- Does this change preserve or explicitly update the documented contract in `.trellis/spec/backend/orchestration-contracts.md`?
- Are new payloads modeled with typed classes instead of open dicts?
- Can duplicate polling, repeated `live_started`, or reruns create duplicate jobs or corrupt state?
- Are file paths and env keys routed through `config.py` instead of hard-coded in service logic?
- Are malformed local files handled resiliently?
- Do tests assert the changed behavior, not just call the code?

## Good vs Bad

### Bad

- A recorder function returns `dict[str, Any]` with undocumented keys.
- A segmenter writes result JSON directly from `service.py` with no typed model.
- A new CLI command contains business logic and file writes inline.

### Good

- A stage introduces `models.py` with typed payloads, keeps command wiring in `service.py`, and adds tests for boundary conditions.

## Common Mistakes

### Common Mistake: Ambiguous fallback telemetry after retry failure

**Symptom**: Logs show a failed ffmpeg attempt followed by an unrelated skip reason, making root-cause analysis confusing.

**Cause**: Re-entering generic prerequisite checks during fallback artifact creation.

**Fix**: Separate "placeholder write" helper from "prerequisite check" branch and call the helper directly after retry exhaustion.

**Prevention**: Add regression tests for retry-exhausted paths and assert only expected fallback semantics are emitted.

### Common Mistake: `processed_job_ids` short-circuit blocks terminal-state routing

**Symptom**: A recording job that was processed earlier is later marked `failed` by orchestrator, but recorder never emits `recording_manual_recovery_required` or recovery actions.

**Cause**: Recorder loops that check `processed_job_ids` before evaluating current orchestrator job status can skip terminal transition handling entirely.

**Fix**: Evaluate terminal statuses (`failed`) before idempotency short-circuit checks; for re-opened `retrying` jobs, clear stale idempotency markers before processing.

**Prevention**: Keep status-driven branches ahead of generic dedupe guards and add pipeline regressions for `processed -> failed` and `processed -> retrying` transitions.

### Common Mistake: Batch recovery status events missing `action_key`

**Symptom**: `mark_jobs_resolved/failed` updates state correctly, but audit rows cannot be correlated back to dispatched actions in multi-action jobs.

**Cause**: Batch status event payloads omitted `action_key` while single-action APIs included it.

**Fix**: Ensure all terminal recovery events, including batch paths, carry `action_key`.

**Prevention**: Add assertions that every `manual_recovery_action_resolved/failed` row includes `action_key` across both single and batch update APIs.

### Common Mistake: Timestamp-only recovery key collisions

**Symptom**: A newly appended manual recovery action is never dispatched or never reaches resolved transition when it shares `created_at` with an older action.

**Cause**: Building `action_key` from only `job_id/action_type/created_at` allows collisions across repeated cycles.

**Fix**: Use collision-resistant action keys (include payload fingerprint or equivalent uniqueness signal) and keep deterministic tie-break rules for same-timestamp rows.

**Prevention**: Add regression tests that append two same-timestamp actions for the same `job_id/action_type` and assert the later row can still dispatch, resolve, and trigger expected requeue behavior.

### Common Mistake: Legacy recovery callback picks first collided row

**Symptom**: `mark_action_resolved/failed` with legacy key shape returns "not pending" even though the newest same-timestamp action is pending.

**Cause**: Legacy-key lookup stores only the first matched row when multiple rows share the same legacy key.

**Fix**: Resolve legacy-key collisions deterministically to the latest row (`created_at`, then append order) before applying pending-status checks.

**Prevention**: Add regression tests where an older collided row is terminal and a newer collided row is pending; assert legacy-key callbacks update the newer row and preserve requeue behavior.

### Common Mistake: Subtitle stage crashes when ASR preconditions are missing

**Symptom**: Subtitle stage exits on missing `faster-whisper`, unsupported recording input, or runtime transcribe errors.

**Cause**: Treating ASR as mandatory instead of optional and failing open-loop on dependency or media-precondition gaps.

**Fix**: Keep transcription optional and degrade to deterministic placeholder SRT when provider/dependency/media checks fail.

**Prevention**: Add tests for unsupported provider, missing ASR dependency path, and SRT output formatting from transcription entries. For `faster-whisper`, also test failures raised while iterating returned `segments`; model execution is lazy and CUDA/runtime dependency errors may appear after `model.transcribe(...)` returns. In `ARL_WHISPER_DEVICE=auto`, retry the same boundary once on CPU and disable CUDA for the rest of the batch so later boundaries do not repeatedly enter the broken CUDA path. In explicit `cuda` mode, keep the failure visible as a placeholder instead of silently changing device policy.

### Common Mistake: Subtitle cues use coarse segment timestamps

**Symptom**: Burned-in subtitles appear several seconds before anyone speaks, blocking the video during leading silence.

**Cause**: `faster-whisper` segment-level `start` / `end` values can cover pre-speech silence or span a match boundary. Building SRT cues from those coarse segment timestamps makes text visible before the first spoken word.

**Fix**: Call `model.transcribe(..., word_timestamps=True)` and build SRT cue windows from the first/last timed word inside the match boundary. Fall back to segment timestamps only when no usable word timestamps are present.

**Prevention**: Keep a regression test where a segment starts at `0.0` but its first word starts later, and assert the generated SRT begins at the first word time rather than `00:00:00,000`.

### Common Mistake: Transcribing the whole recording for every subtitle boundary

**Symptom**: A long recording with multiple match boundaries makes the computer run hot during subtitle generation, even when each boundary is much shorter than the full recording.

**Cause**: Calling `faster-whisper` on the full `recording-source.mp4` once per `MatchBoundary` forces repeated full-file ASR work. A 2-hour recording with four matches can be decoded/transcribed four times.

**Fix**: Pass `clip_timestamps=[boundary.started_at_seconds, boundary.ended_at_seconds]` to `model.transcribe(...)` while keeping the existing boundary filtering as a guardrail. This lets faster-whisper limit decoding/transcription to the match window.

**Prevention**: Keep a subtitle regression test asserting `clip_timestamps` matches the boundary start/end, and keep word timestamp filtering tests so clipping does not regress subtitle timing precision.

### Common Mistake: Adding subtitle failures to `CORE_DECISION_EVENT_TYPES`

**Symptom**: `subtitles-events.jsonl` rows start carrying recorder/exporter fields such as `decision`, `failure_category`, `is_retryable`, and `reason_code`, or validation failures appear because subtitle reasons like `model_unavailable` are not in the ffmpeg taxonomy.

**Cause**: Treating all stage audit logs as if they shared the ffmpeg subprocess failure contract.

**Fix**: Keep subtitle audit rows on the `SubtitleAuditEvent` schema only: `subtitle_transcribe_succeeded` carries language/probability, and `subtitle_fallback_placeholder` carries `reason`/`reason_detail`. Do not add subtitle events to `CORE_DECISION_EVENT_TYPES`.

**Prevention**: Keep subtitle audit tests asserting the minimal schema and update `.trellis/spec/backend/orchestration-contracts.md` if new subtitle fallback reasons are added.

### Common Mistake: Playwright probe short-circuits HTTP fallback

**Symptom**: Windows agent reports offline due Playwright runtime errors even though room page HTML still exposes a valid direct-stream URL.

**Cause**: Returning probe snapshot unconditionally from `detect()` and never executing HTTP fallback path on probe-error reasons.

**Fix**: Only short-circuit on valid probe outcomes; for probe-error reasons (`playwright_script_missing`, `playwright_exec_error:*`, `playwright_error:*`), continue with HTTP fallback detection.

**Prevention**: Add unit tests that force Playwright failure and assert HTTP fallback can still emit `state=live` with `source_type=direct_stream`.

### Common Mistake: Over-retrying clear HTTP 4xx ffmpeg input failures

**Symptom**: Recorder keeps scheduling cross-run retries for failures like `Server returned 404 Not Found`, generating noisy retry churn with no recovery.

**Cause**: Retry classification treats all non-matching failures as transient retryable and does not special-case clear 4xx input-side errors.

**Fix**: Classify HTTP 4xx-style ffmpeg reasons (`401/403/404/410`, `server returned 4xx`) as non-recoverable and route directly to placeholder/manual recovery path.

**Prevention**: Add regression tests in both recorder and orchestrator layers asserting 4xx reasons do not trigger cross-run retry scheduling and are classified as non-recoverable.

### Common Mistake: Exhausting in-run retry budget on known non-recoverable failures

**Symptom**: Within one recorder run, ffmpeg keeps retrying after errors already classified as non-recoverable, delaying fallback output and increasing runtime noise.

**Cause**: Retry loop does not short-circuit after non-recoverable failure classification.

**Fix**: In ffmpeg execution loops, short-circuit further attempts as soon as failure reason is classified non-recoverable.

**Prevention**: Add tests asserting non-recoverable reasons (for example HTTP 4xx) stop in-run attempts early while still producing deterministic fallback artifacts.

### Common Mistake: Retrying exporter ffmpeg on non-retryable failures

**Symptom**: Exporter runs ffmpeg multiple times against the same local recording/subtitle inputs after a clear non-retryable error such as HTTP 4xx-style input rejection, producing duplicate `ffmpeg_export_failed` rows before falling back to the same placeholder.

**Cause**: Exporter historically treated `ARL_EXPORT_FFMPEG_MAX_RETRIES` as a flat loop and ignored `classification.is_retryable`.

**Fix**: After emitting `ffmpeg_export_failed`, break the exporter attempt loop immediately when `outcome.classification.is_retryable is False`; then emit the normal `ffmpeg_export_fallback_placeholder`.

**Prevention**: Keep exporter regression tests with high `ffmpeg_max_retries` asserting one failed row plus one placeholder for non-retryable failures.

### Common Mistake: Counting attempts instead of match-level fallbacks in exporter batch budget

**Symptom**: A single match with multiple retryable failed attempts can consume the whole exporter batch budget even if that match eventually succeeds, causing unrelated later matches to be skipped.

**Cause**: Batch-abort logic counts `ffmpeg_export_failed` attempt rows instead of counting completed match-level `ffmpeg_export_fallback_placeholder` outcomes.

**Fix**: Increment the batch budget counter only after an actual ffmpeg fallback placeholder is emitted for a match. Reset the counter after any successful ffmpeg export or intentional non-ffmpeg placeholder path.

**Prevention**: Keep regression tests for isolated fallback, success-between-fallbacks reset, and intentional-placeholder paths so only consecutive match-level ffmpeg fallbacks trip `ffmpeg_export_batch_aborted`.

### Common Mistake: Adding a postprocess stage without a manifest/status contract

**Symptom**: A new stage writes files that look correct on disk, but `arl status` still cannot report whether the stage is complete, reruns can duplicate outputs, and downstream tooling has to infer filenames.

**Cause**: Treating the stage output as an ad hoc artifact instead of adding the full local-pipeline contract: typed shared asset model, append-only `*-assets.jsonl` manifest, stage-owned `*-state.json`, CLI parser entry, `PostProcessService` order, status counts, and tests.

**Fix**: Follow the copywriter pattern: add a `CopyAsset`-style typed model, append one manifest row per `(session_id, match_index)`, persist processed keys in a stage state file, wire a dedicated CLI command, add the stage to `postprocess`, and extend `StatusService` with present/missing counts.

**Prevention**: For every new post-recording stage, update `.trellis/spec/backend/orchestration-contracts.md` first with signatures, file paths, validation/error rows, and required tests. Add unit tests for generation, idempotency, missing-input retry behavior, CLI parsing, postprocess order, and status counts.

### Common Mistake: Treating processed state as stronger than output existence

**Symptom**: `arl postprocess --once` prints `processed=0` for every stage, but `arl status` still reports missing subtitles, exports, or copy files.

**Cause**: Stage state files (`subtitles-state.json`, `exporter-state.json`, `copywriter-state.json`, `segmenter-state.json`) say a key was processed, while the actual output file or boundary row was deleted, never written, or lost after an interrupted run.

**Fix**: A stage may skip a processed key only when the durable output still exists. If the state key exists but the output file/manifest row is missing, log a reprocessing message and rebuild the output. For raw MP4 files that exist on disk but never reached `recording-assets.jsonl`, use `arl repair-recording-assets` before postprocess.

**Prevention**: Keep regression tests for missing-output reruns and for `status`/`repair-recording-assets` handling of unregistered `data/raw/session-*/recording-source.mp4` files.

### Common Mistake: Burning the in-run retry budget on transient stream-URL failures

**Symptom**: Recorder runs ffmpeg back-to-back against the same stale (token-expired) `stream_url`, producing duplicate 5xx/timeout/process_error failures before the orchestrator can refresh the URL. Audit log balloons with redundant `ffmpeg_record_failed` rows and recovery is delayed by `ffmpeg_max_retries * timeout` per run.

**Cause**: Treating "retryable" as "retry immediately in-run" rather than "yield to next probe cycle so upstream can replace the URL".

**Fix**: On a transient failure, emit one `ffmpeg_record_failed` audit with `decision="attempt_failed_yield_to_next_probe"` and break the in-run loop after a single attempt. Per-job backoff (`next_eligible_at_by_job_id` with a 1s/5s/15s/60s schedule) and per-session cap (`retries_by_session_id` with `ARL_RECORDER_SESSION_RETRY_BUDGET`, default 8) then govern when ffmpeg is invoked again. Non-retryable failures keep the existing `decision="attempt_failed"` immediate-break path.

**Prevention**: Regression tests per transient bucket (5xx / network_timeout / ffmpeg_process_error) asserting `subprocess.run` is invoked exactly once and the audit decision is the yield variant. See `tests/pipeline/test_ffmpeg_resilience.py::RecorderHardeningTest`.

### Common Mistake: One-line stderr truncation is insufficient for ffmpeg debugging

**Symptom**: Operators see a single-line failure reason in `recorder-events.jsonl` (e.g., `exit_status:1` or the last 240 chars of stderr) and cannot tell which ffmpeg stage failed (input open, demux, decode, muxing) without re-running with verbose logging.

**Cause**: Audit row only captures the last stderr line truncated to 240 chars. The first lines (banner, HTTP status digits) and the muxing tail are both informative but absent.

**Fix**: Dual-track on every ffmpeg failure: (a) inline `stderr_excerpt` field in the audit row carrying the first 5 + last 15 lines (each line truncated to 240 chars, total ≤ 4 KB), and (b) full stderr written atomically to `data/tmp/recorder-stderr/<job_id>-<attempt>.log` with the path also recorded on the audit row as `stderr_log_path`. Recorder rotates that directory at startup, keeping only the newest `ARL_RECORDER_STDERR_RETAIN_COUNT` files (default 200).

**Prevention**: Tests asserting (1) failure audits carry both `stderr_excerpt` and `stderr_log_path`, (2) success audits carry neither, (3) rotation honors the retain count. Pattern applies to any future stage that wraps a noisy external process (exporter ffmpeg, whisper) — reuse the same excerpt/log-path schema rather than inventing a new one.

### Common Mistake: Discarding a valid partial recording after ffmpeg exits non-zero

**Symptom**: A long unattended run leaves a playable `recording-source.mp4` on disk, but `recording-assets.jsonl` points to a tiny `recording-source.txt` fallback, so downstream segment/subtitle/export stages ignore the real recording.

**Cause**: Treating any non-zero ffmpeg exit as total recording failure even though fragmented direct-stream MP4 output may already be valid and probeable.

**Fix**: After a direct-stream ffmpeg failure, check for a non-empty partial mp4 and verify it with ffprobe. If it has a video stream and satisfies the actual-resolution gate, emit `ffmpeg_record_succeeded`, append the mp4 `RecordingAsset`, and skip txt fallback. If ffprobe is missing/inconclusive or the partial fails the quality gate, keep the existing retry/fallback path.

**Prevention**: Keep regression coverage where ffmpeg raises an HTTP read-reset error after writing a probeable mp4, and assert the manifest points to `recording-source.mp4` with no `ffmpeg_fallback_placeholder`.

### Common Mistake: Missing failure category leads to generic inspect-only action

**Symptom**: Recorder emits `inspect_failure_logs` for many failed jobs even when error text clearly indicates a more actionable fix (prerequisite/config/network).

**Cause**: Manual-recovery action selection depends only on `failure_category`, but upstream events may carry `failure_category=None`.

**Fix**: Infer actionable category from `stop_reason/recovery_hint` markers before selecting `action_type` and recovery steps.

**Prevention**: Add regression tests asserting recognizable markers (for example HTTP 404) map to actionable recovery actions, while opaque reasons still fall back to inspect-only behavior.

### Common Mistake: Segmenter always emits one full-duration boundary

**Symptom**: Multi-match sessions still produce one boundary from `0 -> duration`, so downstream subtitle/export stages lose per-match granularity.

**Cause**: Segmenter ignores available stage hints and treats every recording as a single match.

**Fix**: Read optional `match-stage-hints.jsonl`, use only `in_game` anchors, and derive boundaries from sorted in-game starts.

**Prevention**: Add segmenter tests for multi-match hint path, `detected_at` timestamp conversion, fallback path when hints are absent, and rerun idempotency.

### Common Mistake: Hand-editing stage hints produces malformed timestamps

**Symptom**: Segmenter silently ignores intended hints because rows contain invalid datetime strings or missing timestamp fields.

**Cause**: Operators manually edit `match-stage-hints.jsonl` without schema validation.

**Fix**: Use `arl stage-hint` CLI ingestion path so inputs are validated and normalized before append.

**Prevention**: Keep parser tests for invalid datetime rejection and timezone normalization, and keep writer tests for both `at_seconds` and `detected_at` payload shapes.

### Common Mistake: Auto stage-hint generation overwrites manual anchors

**Symptom**: Operator-provided match anchors get mixed with heuristic anchors, causing unstable match boundaries.

**Cause**: Auto hint generation appends to sessions that already contain `in_game` hints.

**Fix**: `stage-hints-auto` must skip sessions with existing `in_game` hints and only seed sessions with no anchor yet.

**Prevention**: Add idempotency tests and explicit skip-path tests for sessions that already contain manual `in_game` hints.

### Common Mistake: Semantic stage-hint generator rewrites seeded sessions

**Symptom**: After running `stage-hints-semantic`, sessions with pre-existing hints end up with conflicting stage timelines.

**Cause**: Semantic generation does not short-circuit when session already contains any stage-hint rows.

**Fix**: Treat semantic generation as one-time seeding: skip sessions that already have hints.

**Prevention**: Add tests for skip behavior, repeated-run idempotency, and short-recording boundary safety (`in_game` inside duration).

### Common Mistake: Semantic generator trusts low-signal text and emits wrong stages

**Symptom**: Stage timelines become noisy when generic text is interpreted as semantic stages, causing unstable segmentation.

**Cause**: Signal text classification has no in-game guardrail and does not filter out-of-range timestamps.

**Fix**: Accept signal-driven output only when at least one usable `in_game` stage remains after timestamp filtering; otherwise fall back to template strategy.

**Prevention**: Keep regression tests for signal-driven success path, no-in-game fallback path, and out-of-range signal filtering.

### Common Mistake: Subtitle-to-signal ingest duplicates or starves downstream semantic generation

**Symptom**: `stage-hints-semantic` either receives duplicate stage signals across reruns or never receives signals after subtitle files appear later.

**Cause**: Subtitle ingest lacks explicit processed-state ownership (`stage-signal-ingest-state.json`) or marks missing-file rows as processed too early.

**Fix**: Persist processed subtitle keys for successful/unmatched parses, but keep missing-file rows unprocessed so reruns can ingest once files exist.

**Prevention**: Add regression tests for repeated-run idempotency, missing subtitle path skip/retry behavior, and unmatched-text processed-state behavior.

### Common Mistake: Cross-stage enrichment failure aborts subtitle output

**Symptom**: Subtitle stage finishes writing SRT files, but the whole run is marked failed because follow-up stage-signal extraction throws.

**Cause**: Treating subtitle-to-signal enrichment as a hard dependency instead of best-effort enrichment.

**Fix**: Keep subtitle asset emission as the primary contract; wrap enrichment call with error logging so stage-signal ingest failures do not break subtitle stage completion.

**Prevention**: Add tests asserting subtitle assets are emitted and persisted even when enrichment path is skipped or retried.

### Common Mistake: Semantic hint generation uses stale signal snapshot

**Symptom**: `stage-hints-semantic` falls back to template even though fresh subtitle assets already contain clear `in_game` cues.

**Cause**: Semantic generation reads `match-stage-signals.jsonl` before running subtitle-to-signal ingest, so latest SRT cues never enter the current run.

**Fix**: Run best-effort `stage-signals-from-subtitles` ingest at the start of `stage-hints-semantic` before loading signal rows.

**Prevention**: Add tests where only subtitle assets exist (no pre-seeded signal rows) and assert semantic generation still takes the signal-driven path.

### Common Mistake: English-only stage keywords miss real subtitles

**Symptom**: `stage-signals-from-subtitles` emits zero or incomplete signals for sessions with Chinese subtitles, causing semantic hints to degrade to template.

**Cause**: Stage classifier keyword set only covers English stage terms.

**Fix**: Keep multilingual keyword coverage (at least English + Chinese) for champion select/loading/in-game/post-game cues.

**Prevention**: Add classifier tests and subtitle-ingest tests with Chinese cues so keyword regressions fail fast.

### Common Mistake: Stage keyword override only affects one pipeline branch

**Symptom**: Custom keywords work in subtitle ingestion but fail in semantic hint generation (or the opposite).

**Cause**: Override file loading is wired into only one service path.

**Fix**: Load the same keyword configuration in both `stage-signals-from-subtitles` and `stage-hints-semantic`.

**Prevention**: Keep integration tests for both branches using one shared override file.

### Common Mistake: Invalid stage keyword config fails silently

**Symptom**: Operators provide `ARL_STAGE_KEYWORDS_PATH`, but pipeline behavior remains default with no clue why.

**Cause**: Loader falls back to defaults without logging missing-file/JSON/schema errors.

**Fix**: Emit explicit fallback logs for path missing, parse failure, and per-stage schema issues while preserving non-crashing fallback behavior.

**Prevention**: Add tests that capture logs for invalid override payloads and assert default classification still works.

### Common Mistake: CLI stage keyword override is parsed but not injected into runtime settings

**Symptom**: Running `arl stage-hints-semantic --stage-keywords-path <path>` (or `stage-signals-from-subtitles/subtitles`) behaves exactly like env/default configuration.

**Cause**: CLI parser accepts the flag, but command entrypoint still uses unmodified `load_settings()` output.

**Fix**: Resolve command settings with explicit precedence `CLI --stage-keywords-path > ARL_STAGE_KEYWORDS_PATH > built-in defaults` before constructing services.

**Prevention**: Add parser tests for all supporting commands and command-entry tests that assert service constructors receive the CLI-overridden `settings.segmenter.stage_keywords_path`.

### Common Mistake: Force reprocess rewrites duplicate subtitle-derived signals

**Symptom**: Running `arl stage-signals-from-subtitles --force-reprocess` repeatedly appends duplicate signal rows for the same subtitle asset.

**Cause**: Reprocess path only bypasses processed-key guard but does not track already-emitted signal identity per subtitle key.

**Fix**: Persist emitted signal fingerprints in ingest state and deduplicate on append during both normal and force-reprocess runs.

**Prevention**: Add tests for unchanged-content force reprocess (expect 0 new rows) and changed-content force reprocess (expect only newly discoverable stage rows).

### Common Mistake: Ingest state keeps stale subtitle keys forever

**Symptom**: `stage-signal-ingest-state.json` grows unbounded with old `processed_subtitle_keys` and fingerprint entries from subtitle assets that were deleted or rotated out.

**Cause**: Ingest path appends state but never compacts entries against current subtitle manifest.

**Fix**: Compact ingest state at run start using current `subtitle-assets.jsonl` keys, and remove stale/empty/duplicate fingerprint rows.

**Prevention**: Add regression tests with injected stale keys in state and assert compaction happens after one ingest run.

### Common Mistake: Force reprocess always scans full subtitle manifest

**Symptom**: Operators only need to reprocess one session or one subtitle file, but command rescans all subtitle assets and increases runtime cost.

**Cause**: CLI and service do not expose scoped filter inputs for subtitle-signal ingestion.

**Fix**: Add filterable ingest inputs (`session_ids`, `subtitle_paths`) and CLI flags (`--session-id/--session-ids`, `--subtitle-path/--subtitle-paths`) so reprocess can target only needed assets.

**Prevention**: Add parser + service tests that assert only matching assets emit signals under filter constraints.

### Common Mistake: Filtered reprocess has no match but looks like silent success

**Symptom**: Operator passes session/path filter flags and sees no new signals, but cannot tell whether it was true no-op or filter mismatch.

**Cause**: Filtered ingest path exits with zero output but has no explicit "no matched assets" telemetry.

**Fix**: Emit explicit `no assets matched filters` log with normalized filter inputs and keep zero-result summary log.

**Prevention**: Add regression test for unmatched filter run that asserts the explicit no-match log and zero emitted signals.

### Common Mistake: Filtered ingest lacks baseline context for operators

**Symptom**: Logs show either processed rows or no-match message, but operators still cannot judge filter selectivity because total manifest size is unknown.

**Cause**: Filtered ingest path logs outcomes without reporting `total_assets` vs `matched_assets`.

**Fix**: Emit filter summary telemetry (`total_assets`, `matched_assets`) for every filtered run before processing.

**Prevention**: Add tests that assert summary log presence for both partial-match and zero-match filtered runs.

### Common Mistake: Ingest summary hides why matched assets produced no output

**Symptom**: Run finishes with `processed_subtitles=0 emitted_signals=0`, but operators cannot tell whether assets were already processed or missing on disk.

**Cause**: End-of-run summary omits skip-category counters.

**Fix**: Include skip counters in summary telemetry (`skipped_already_processed`, `skipped_missing_subtitle`) alongside `matched_assets`.

**Prevention**: Add regression test that mixes already-processed and missing-file assets and assert both skip counters in summary log.

### Common Mistake: Bare `read_text` / `write_text` on durable state files breaks under non-UTF-8 OS locales

**Symptom**: Recorder fails to start with `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb7` when reading `data/tmp/orchestrator-state.json`. Re-running does not recover; the recorder loop is permanently broken until the file is deleted.

**Cause**: `OrchestratorStateStore.load` and `save` used bare `Path.read_text()` / `Path.write_text()`. On Windows zh-CN hosts, Python falls back to the platform locale (CP936/GBK) for both ends, so a state snapshot containing a Chinese `streamer_name` was silently written as GBK. Every other consumer (`recorder/service.py`, integration tests) passed an explicit `encoding="utf-8"` and could no longer decode the file.

**Fix**: Always pass `encoding="utf-8"` to both `read_text` and `write_text` for durable state JSON and event JSONL files. For backward compatibility with already-corrupted files, `OrchestratorStateStore.load` reads bytes and falls back to GBK when UTF-8 decode fails; the next `save` rewrites the file as UTF-8 so the fallback path is exercised at most once per legacy file.

**Prevention**: Mirror the established pattern used by every other state store (`recorder`, `exporter`, `recovery`, `subtitles`, `segmenter`, `windows_agent`) — explicit UTF-8 on both ends. Add round-trip unit tests with non-ASCII (Chinese) `streamer_name` payloads, and add an explicit auto-heal regression that simulates a legacy GBK-encoded file.

### Common Mistake: Lumping HTTP 403 into the generic 4xx bucket hides cookie-expiration signal

**Symptom**: Recorder ffmpeg failure on a 1080P+ stream URL after the platform cookie quietly expired manifests as a generic "Source rejected the request (HTTP 4xx). Refresh stream URL/session prerequisites before rerun." manual recovery hint, indistinguishable from a 404 "stream really gone" failure. Operators rerun probes / refresh stream URL without realizing they need to refresh `ARL_DOUYIN_COOKIE` / `ARL_BILIBILI_SESSDATA`.

**Cause**: `classify_failure_reason` originally lumped `401/403/404/410/server returned 4*` into the same `reason_code="http_4xx"` under `failure_category="http_4xx_non_retryable"`. The probe-side `cookie_expired_for_<platform>` audit channel existed but the recorder never wrote into it, so 403 failures from a stale cookie produced no cookie-specific signal at all.

**Fix**: Split 403 out as a distinct `reason_code="http_403_forbidden"` (still under the same non-retryable category — retry semantics are unchanged) and have the recorder emit a `cookie_expired_for_<platform>` audit row alongside `ffmpeg_record_failed` when the classifier returns the 403 sub-code AND the operator opted into cookie-based auth for that platform (`DouyinSettings.cookie` or matching `BilibiliSettings.sessdata` non-empty). Orchestrator routes the recorder-side cookie row to the audit log without advancing the per-job monotonic watermark so the accompanying ffmpeg failure event is still applied.

**Prevention**: When a single category (`http_4xx_non_retryable`) covers heterogeneous causes (auth/cookie vs gone vs server config), split the reason_code rather than the category. Test the classifier on every status code marker independently and assert that the high-confidence cookie-suspicion signal only fires when the operator actually configured a cookie env var — a 403 with no cookie configured is a server denial, not an expiration. For Bilibili, never emit the cookie-expiration signal from ffmpeg 403 alone; run a same-room probe and require `code=-101` classification first.

### Common Mistake: Treating Bilibili ffmpeg 403 as SESSDATA expiry without probing

**Symptom**: A Bilibili recording job fails with HTTP 403, operators are told to replace `ARL_BILIBILI_SESSDATA`, but rerunning `getRoomPlayInfo` with the same SESSDATA still returns a fresh direct-stream URL.

**Cause**: Bilibili direct stream URLs are short-lived signed URLs. The signed CDN URL can expire independently of the account cookie, so ffmpeg 403 is ambiguous until the room API is probed again.

**Fix**: On Bilibili direct-stream ffmpeg 403, run a same-room `BilibiliRoomProbe`. Emit `cookie_expired_for_bilibili` only when `classify_cookie_state(snapshot) == expired` (`api_error:code=-101` or `playinfo_error:api_error:code=-101`). If the probe is fresh and returns a direct stream URL, emit `stream_url_expired_for_bilibili` and retry ffmpeg once with the refreshed URL and headers. If the probe is fresh but cannot return a direct stream URL, emit `stream_url_expired_for_bilibili` with `reason=refresh_failed:*` and continue the normal fallback/manual path.

**Prevention**: Keep recorder tests for all three branches: fresh probe refreshes and retries once, expired SESSDATA emits only the cookie event, and fresh/no-direct-stream emits only the stream URL diagnostic. Status should surface `cookie_expired_for_bilibili` as action-required and `stream_url_expired_for_bilibili` as a degraded diagnostic.

### Common Mistake: Extracting a `subprocess.run` call to a helper breaks tests that patch it at the original module

**Symptom**: Refactor extracts `subprocess.run(...)` from a service module into a shared helper. Existing tests that do `patch("arl.<service>.service.subprocess.run", side_effect=...)` start raising `AttributeError: module arl.<service>.service has no attribute 'subprocess'` (when the import is removed) or silently no-op (when the patch resolves but the actual call now lives in the helper module). The "byte-identical refactor" claim breaks at test time even though behavior is unchanged.

**Cause**: `patch("a.b.subprocess.run", ...)` resolves the attribute path: it looks up `a.b`, then `subprocess` on it, then sets `run` on whatever module-object that resolves to. After the extraction:
- If `import subprocess` was removed from `a.b`, the attribute lookup fails entirely.
- If the import is kept (because some other use remains, or by accident), the patch sets `run` on the global `subprocess` module — which the helper also sees, so the patch coincidentally works through the helper too. This is the same module object across all importers.

**Fix**: When the original module legitimately needs `subprocess` for a separate reason (an unrelated probe, a fallback path), keep the import. When the original module no longer uses `subprocess` itself but tests patch through it, deliberately preserve `import subprocess  # noqa: F401 — kept as patch shim for tests` so the patch target stays resolvable. Patches still take effect because Python module objects are global singletons (`sys.modules['subprocess']`). Both paths are demonstrated in `src/arl/recorder/service.py` (subprocess still used by X11 probe) and `src/arl/exporter/service.py` (subprocess kept as shim only).

**Prevention**: After any refactor that moves a module-level dependency (subprocess, requests, datetime.now, etc.) into a shared helper, grep callers for `patch("<original_module>.<dep>` to find affected tests. Decide deliberately per call site: update the patch target to the new module, or keep the import as a shim. Document the choice with a `noqa` comment naming the test contract. Add a regression test that asserts the patch target still resolves (importing the module and accessing the attribute) so a future cleanup that drops "unused" imports doesn't silently re-break the tests.
