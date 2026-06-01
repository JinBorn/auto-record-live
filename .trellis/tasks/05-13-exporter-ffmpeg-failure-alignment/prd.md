# Exporter ffmpeg failure handling alignment with recorder

## Goal

Bring exporter's ffmpeg failure handling closer to recorder's four-layer defense by adding the layers exporter currently lacks — without copying parts that don't apply to a local-file pipeline.

Today (after 05-13 Session 29 helper extraction):

- ✅ Shared `run_ffmpeg_attempt` does subprocess + classify + stderr excerpt + log dump.
- ✅ Exporter audit JSONL (`data/tmp/exporter-events.jsonl`) carries the canonical `decision` / `failure_category` / `is_retryable` / `reason_code` / `reason_detail` + `stderr_excerpt` + `stderr_log_path`.
- ✅ `ffmpeg_export_failed` + `ffmpeg_export_fallback_placeholder` are registered in `CORE_DECISION_EVENT_TYPES`.
- ❌ Exporter loop is a flat `for attempt in range(...)` — no inter-attempt sleep, retries fire back-to-back even when the failure is transient.
- ❌ No cross-run state — a match that fell back to placeholder in run N is re-attempted from scratch in run N+1, with no memory.
- ❌ No batch budget — if ffmpeg crashes match 1, exporter still tries match 2..N and writes N placeholders.

## User Value

Operators running an overnight batch export (e.g. 10 matches from a 4-hour session) who hit a host-wide resource event (OOM, GPU contention, ffmpeg binary regression) should:

1. See one canonical "we stopped the batch because the host looks broken" audit row instead of 10 placeholder rows.
2. Have a cross-run signal that lets the next `arl exporter` invocation skip recently-failed matches until they're worth retrying again, instead of immediately re-burning the in-run retry budget.
3. (Optional) Get per-attempt backoff so transient pressure (a 30 s CPU spike) doesn't cause both attempts to land inside the same spike.

## Known Spec Tension

`.trellis/spec/backend/orchestration-contracts.md:415` currently says:

> `ARL_EXPORT_FFMPEG_MAX_RETRIES` ... — in-run retry count for exporter ffmpeg; **exporter does NOT yield-on-transient (recorder-only behavior)**, so this is a straight retry loop.

This was deliberate: recorder yields because the next probe refreshes a stale stream URL. Exporter's input is a local file that doesn't change between attempts, so yield-on-transient has no upstream to wait for.

The recorder→exporter mapping is therefore **not 1:1**. Per-attempt sleep, cross-run defer, and batch budget can still be useful (they respond to host-level transient pressure, not URL staleness), but the PRD must justify each one rather than copying the recorder behavior wholesale.

## Confirmed Facts (from code inspection)

- `ExporterService.run()` iterates `boundaries`, calls `_write_export` → `_write_export_with_ffmpeg` for each match (`src/arl/exporter/service.py:42-73`).
- In-run retry is `for attempt in range(1, attempts + 1)` with no `time.sleep` between attempts; `attempts = export.ffmpeg_max_retries + 1` (default 2).
- On all attempts failing, `_write_placeholder_export` writes `<session>_match<idx>.txt`, emits `ffmpeg_export_fallback_placeholder`, returns (`src/arl/exporter/service.py:128-223`).
- `ExporterStateFile` currently has only `processed_match_keys: list[str]` — no failure memory, no eligibility map (`src/arl/exporter/models.py:10-11`).
- Recorder reference: `next_eligible_at_by_job_id`, `retries_by_session_id`, `session_retry_budget` live in `RecorderStateFile` (cross-run); backoff schedule is `1s/5s/15s/60s` (`src/arl/recorder/service.py:657-665`).
- Canonical failure registry: 5 categories, 6 reason codes in `src/arl/shared/failure_contracts.py`. Retryable: 5xx, network_timeout, ffmpeg_process_error. Non-retryable: 4xx, 403, unknown.

## In-Scope Failure Scenarios (decided 2026-05-13)

User confirmed **B + C only**:

- **Scenario B (host short-term pressure)**: per-attempt backoff so a 30 s CPU/disk spike doesn't take both attempts.
- **Scenario C (host broken / batch-wide)**: batch budget so 10 matches in a row failing → one "batch aborted" audit row, not 10 placeholders.

Explicitly out:

- Cross-run defer ("scenario D") — blocked by the prior `processed_match_keys` design treating placeholder as terminal. Would require splitting that key set into "success" vs "placeholder" first; that's a separate task.
- Scenario A (single corrupt match) — current placeholder behavior is correct.

## Decisions (Q2 + Q3, 2026-05-13)

**Batch budget (Q2)**:

- Counter unit = **match-level fallback** (one `ffmpeg_export_fallback_placeholder` increments the counter by 1).
- Counter shape = **consecutive** fallbacks; one success resets to 0.
- Counter lifetime = **in-memory, per `ExporterService.run()` invocation**; not persisted.
- Default threshold = **3** (env `ARL_EXPORTER_BATCH_FALLBACK_BUDGET`, min 1).
- On trip: emit one `ffmpeg_export_batch_aborted` audit row, `break` the boundaries loop. Remaining matches get **no** placeholder, **no** export-asset, **no** `processed_match_keys` entry — they get reprocessed on the next `arl exporter` invocation.
- Aborted audit row carries `decision`, `failure_category`, `reason_code`, `reason_detail` (inherited from the last fallback), plus `consecutive_fallbacks=<threshold>` and `remaining_matches=<int>`.

**Per-attempt backoff (Q3)**:

- Schedule = **2s, 8s** (`ARL_EXPORTER_BACKOFF_INITIAL_SECONDS` default 2, `ARL_EXPORTER_BACKOFF_MAX_SECONDS` default 8). Doubling between attempts, capped at max.
- Gate = **retryable only**. `is_retryable=False` failures `break` the attempt loop immediately, no sleep, jump to fallback path. (This is the "non-retryable short-circuit" — new behavior.)
- Sleep is monkeypatchable in tests; the helper accepts an injected `_sleep` callable or patches `time.sleep` in the module.

**Existing test breakage acknowledged**:

- `tests/pipeline/test_ffmpeg_resilience.py` `test_ffmpeg_failed_emits_audit_with_stderr_log` (around line 2197) asserts `len(failed_rows) == 2` for a 404-classified failure. New short-circuit behavior makes this `len(failed_rows) == 1`. Test gets updated in the same PR that ships the short-circuit.

## Requirements

- **R1 (non-retryable short-circuit)**: When `outcome.classification.is_retryable is False`, exporter's in-run loop `break`s after the first failed attempt — no further attempts, no sleep, fallback path runs.
- **R2 (per-attempt backoff, B)**: Between two retryable failed attempts of the same match, exporter sleeps `min(initial * 2^(attempt-1), max)` seconds. No sleep before attempt 1; no sleep after the final attempt.
- **R3 (batch budget, C)**: `ExporterService.run()` maintains an in-memory `consecutive_fallbacks` counter. Each `ffmpeg_export_fallback_placeholder` increments it; each success resets it. On reaching `ARL_EXPORTER_BATCH_FALLBACK_BUDGET` (default 3), emit `ffmpeg_export_batch_aborted` and break the boundaries loop.
- **R4 (config surface)**: New env vars: `ARL_EXPORTER_BACKOFF_INITIAL_SECONDS=2`, `ARL_EXPORTER_BACKOFF_MAX_SECONDS=8`, `ARL_EXPORTER_BATCH_FALLBACK_BUDGET=3`. New fields on `ExportSettings`.
- **R5 (audit registry)**: `ffmpeg_export_batch_aborted` added to `CORE_DECISION_EVENT_TYPES`; decision string = `"batch_aborted"` (pending Q4 confirmation); `ExporterAuditEvent` gains optional `consecutive_fallbacks: int | None` and `remaining_matches: int | None` fields.
- **R6 (spec update)**: Rewrite `orchestration-contracts.md:415` ("exporter does NOT yield-on-transient" — keep the no-yield part, add the new layers); extend validation matrix with two new rows for batch-aborted + non-retryable short-circuit; add the env vars to the env-keys block. Add Common Mistake to `quality-guidelines.md`: "count consecutive match-level fallbacks for batch budget, not failed attempts".
- **R7 (README)**: Extend the "ffmpeg 失败排查（exporter）" section with the new env vars + the `ffmpeg_export_batch_aborted` event semantics.
- **R8 (tests)**: New `ExporterAttemptBackoffTest` + `ExporterBatchBudgetTest` classes. Update one existing test assertion.

## Acceptance Criteria

- [ ] pytest baseline 300 → ≥ 315 green; existing `ExporterFfmpegAuditTest` updated test passes alongside new tests.
- [ ] 10-match batch with 1 isolated 4xx fallback → 9 successes + 1 placeholder, **no** `batch_aborted` (counter resets on success).
- [ ] 10-match batch where match 1, 2, 3 all fall back → 3 placeholders + 1 `batch_aborted` audit row + 7 matches left untouched (not in `processed_match_keys`).
- [ ] Per-attempt sleep delay observable via monkeypatched `time.sleep` recording arg list = `[2.0, 8.0]` for a 3-attempt retryable run.
- [ ] Non-retryable failure with `ffmpeg_max_retries=5` produces exactly 1 `ffmpeg_export_failed` row + 1 `ffmpeg_export_fallback_placeholder` row (no extra attempts).
- [ ] `orchestration-contracts.md:415` updated; validation matrix grows by 2 rows; `quality-guidelines.md` gains the Common Mistake.

## Out of Scope (proposed)

- Yielding within a single batch to wait for an external upstream — exporter has no upstream to yield to.
- Orchestrator integration — exporter audit is already grep-only and orchestrator does not consume it (`README.md:235-237`); keep that boundary.
- Removing the placeholder fallback — it's a deliberate "don't block the pipeline" property.

## Open Questions

1. ~~Failure scenario scope~~ — **decided B+C** (2026-05-13).
2. ~~Batch budget semantics~~ — **decided** consecutive / match-level / in-memory / threshold=3 (2026-05-13).
3. ~~Per-attempt backoff schedule and gating~~ — **decided** 2s/8s / retryable-only / non-retryable short-circuit (2026-05-13).
4. ~~PR breakdown + decision-string naming~~ — **decided** 3-PR / `"batch_aborted"` (2026-05-13).

All blockers cleared. Next: `design.md` + `implement.md`.

## Notes

- Lightweight PRD-only task is **not** appropriate — this touches state schema (new state file fields), config (new env vars), contracts (spec rewrite of line 415), and tests across multiple modules. Will add `design.md` and `implement.md` after PRD converges.
