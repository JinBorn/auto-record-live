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

**Prevention**: Add tests for unsupported provider, missing ASR dependency path, and SRT output formatting from transcription entries.

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
