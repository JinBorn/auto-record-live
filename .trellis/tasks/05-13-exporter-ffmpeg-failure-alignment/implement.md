# Implement: Exporter ffmpeg failure handling alignment with recorder

3 PRs, each independently mergeable. Run pytest after every PR.

## PR1 — Non-retryable short-circuit

**Goal**: exporter's in-run attempt loop breaks immediately on `is_retryable=False`.

### Files

- `src/arl/exporter/service.py` — `_write_export_with_ffmpeg`: after `_append_audit("ffmpeg_export_failed", ...)`, add `if not fd.is_retryable: break`. (~3 lines added.)
- `tests/pipeline/test_ffmpeg_resilience.py` — `ExporterFfmpegAuditTest.test_ffmpeg_failed_emits_audit_with_stderr_log` (around line 2197):
  - Change `self.assertEqual(len(failed_rows), 2)` → `self.assertEqual(len(failed_rows), 1)`
  - Drop the `for idx, row in enumerate(...)` loop's expectation of `attempt=2`; keep the one-row assertion intact
  - Add a comment referencing this PRD: `# Non-retryable (http_4xx) short-circuits the attempt loop — see 05-13-exporter-ffmpeg-failure-alignment.`
- New test in same class: `test_non_retryable_short_circuits_with_max_retries_five`
  - Set `settings.export.ffmpeg_max_retries = 5`
  - Patch `subprocess.run` with a 4xx-classified stderr
  - Assert exactly 1 `ffmpeg_export_failed` + 1 `ffmpeg_export_fallback_placeholder`
- `.trellis/spec/backend/quality-guidelines.md` — add Common Mistake "Retrying exporter ffmpeg on non-retryable failures".

### Validation

```powershell
.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_ffmpeg_resilience.py -k "ExporterFfmpegAudit or short_circuit" -v
.\.venv\Scripts\python.exe -m pytest -q  # full sweep; expect 300 + 1 = 301 green
```

### Commit

```
feat(exporter): short-circuit attempt loop on non-retryable ffmpeg failures
```

---

## PR2 — Per-attempt exponential backoff (retryable only)

**Depends on**: PR1 merged.

### Files

- `src/arl/config.py`:
  - `ExportSettings` gains `backoff_initial_seconds: float = 2.0` and `backoff_max_seconds: float = 8.0`.
  - `load_settings()` reads `ARL_EXPORTER_BACKOFF_INITIAL_SECONDS` (default `"2"`) and `ARL_EXPORTER_BACKOFF_MAX_SECONDS` (default `"8"`) via `float(os.getenv(...))`.
- `src/arl/exporter/service.py`:
  - `import time` at top.
  - Add `_backoff_seconds(self, attempt: int) -> float` returning `min(initial * (2 ** (attempt - 1)), max)`.
  - In `_write_export_with_ffmpeg`, after the retryable branch (post-PR1 short-circuit), and before the next loop iteration: `if attempt < attempts: time.sleep(self._backoff_seconds(attempt))`. (~5 lines added.)
- New test class `ExporterAttemptBackoffTest(unittest.TestCase)` in `tests/pipeline/test_ffmpeg_resilience.py`:
  - `test_no_sleep_before_first_attempt` — fresh run, patch `time.sleep` to record, retryable failure, single attempt → recorded calls is empty.
  - `test_sleep_between_two_retryable_attempts` — `ffmpeg_max_retries=1`, ffmpeg_process_error stderr, recorded sleeps == `[2.0]`.
  - `test_sleep_doubles_and_caps_across_three_attempts` — `ffmpeg_max_retries=2`, recorded sleeps == `[2.0, 4.0]`.
  - `test_sleep_caps_at_max` — `ffmpeg_max_retries=4`, recorded sleeps == `[2.0, 4.0, 8.0, 8.0]`.
  - `test_no_sleep_after_non_retryable_short_circuit` — 4xx stderr, `ffmpeg_max_retries=3`, recorded sleeps is empty.
  - `test_env_overrides_backoff_schedule` — set initial=1, max=3, retries=2, recorded sleeps == `[1.0, 2.0]`.
- `README.md` — extend exporter env table with the two new vars; one bullet in the troubleshooting section.

### Validation

```powershell
.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_ffmpeg_resilience.py::ExporterAttemptBackoffTest -v
.\.venv\Scripts\python.exe -m pytest -q  # expect 301 + 6 = 307 green
```

### Commit

```
feat(exporter): exponential backoff between retryable ffmpeg attempts
```

---

## PR3 — Batch fallback budget + spec/README catch-up

**Depends on**: PR2 merged.

### Files

- `src/arl/shared/failure_contracts.py` — add `"ffmpeg_export_batch_aborted"` to `CORE_DECISION_EVENT_TYPES`.
- `src/arl/exporter/models.py` — `ExporterAuditEvent` gains `consecutive_fallbacks: int | None = None` and `remaining_matches: int | None = None`.
- `src/arl/config.py` — `ExportSettings` gains `batch_fallback_budget: int = 3`; `load_settings()` reads `ARL_EXPORTER_BATCH_FALLBACK_BUDGET` with `max(1, int(...))` lower bound.
- `src/arl/exporter/service.py`:
  - Refactor `_write_export` to return `tuple[Path, bool]` (`path`, `was_placeholder`). The non-ffmpeg intentional-placeholder branch returns `False`; only the ffmpeg-fallback path returns `True`.
  - `_write_export_with_ffmpeg`: stash `self._last_failure_classification` + `self._last_failure_reason` on every failed attempt (overwritten each iteration; final value is the cause of the fallback).
  - `run()`: initialize `consecutive_fallbacks = 0` and `self._last_failure_classification = None / self._last_failure_reason = None` at top.
  - Inside the boundaries `for index, boundary in enumerate(boundaries)` loop, after `processed_match_keys.append(key)`:
    - If `was_placeholder` and the fallback came from ffmpeg (i.e. `self._last_failure_classification is not None`): increment `consecutive_fallbacks`; if `>= budget`, emit `ffmpeg_export_batch_aborted` with the stashed classification + `remaining_matches = len(boundaries) - index - 1`, then `break`.
    - Else: `consecutive_fallbacks = 0`.
- New test class `ExporterBatchBudgetTest(unittest.TestCase)`:
  - `test_isolated_fallback_does_not_trip_budget` — 5 boundaries; ffmpeg fails on match 3 (process_error), succeeds otherwise → no `batch_aborted` row, 4 successes + 1 placeholder.
  - `test_three_consecutive_fallbacks_trip_default_budget` — 10 boundaries; ffmpeg always fails (process_error) → exactly 3 fallback rows + 1 `batch_aborted` row; matches 4..10 NOT in `processed_match_keys`.
  - `test_success_between_fallbacks_resets_counter` — pattern fail/fail/success/fail/fail/fail (6 boundaries) → no abort row after first two; abort row after the trailing 3-in-a-row.
  - `test_batch_aborted_inherits_last_failure_classification` — assert `failure_category=ffmpeg_process_error_retryable`, `reason_code=ffmpeg_process_error`, `decision=batch_aborted`, `consecutive_fallbacks=3`, `remaining_matches=7`.
  - `test_env_overrides_budget_threshold` — `ARL_EXPORTER_BATCH_FALLBACK_BUDGET=2`; 2-in-a-row trips immediately.
  - `test_intentional_placeholder_does_not_count` — `enable_ffmpeg=False`; 10 boundaries → 10 intentional placeholders, no `batch_aborted`.
- `.trellis/spec/backend/orchestration-contracts.md` — line 415 rewrite + 3 new env keys + 2 new validation-matrix rows (see design.md).
- `.trellis/spec/backend/quality-guidelines.md` — second Common Mistake "Counting attempts, not match fallbacks, in batch budget".
- `README.md` — extend the exporter troubleshooting section with batch-abort grep recipe.

### Validation

```powershell
.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_ffmpeg_resilience.py::ExporterBatchBudgetTest -v
.\.venv\Scripts\python.exe -m pytest -q  # expect 307 + 6 = 313 green
```

### Commit

```
feat(exporter): batch fallback budget + ffmpeg_export_batch_aborted contract
```

---

## End-to-end verification after all 3 PRs

1. **Manual smoke (optional)** — synthesize a 3-boundary scenario where ffmpeg is forced to fail by pointing the recording asset at a corrupt file; run `arl exporter`; grep:
   ```powershell
   Get-Content data/tmp/exporter-events.jsonl | Select-String ffmpeg_export_batch_aborted
   ```
   Expect one row with `consecutive_fallbacks=3` and `remaining_matches=0`.

2. **pytest total** — final `pytest -q` should be **313** passing, up from baseline **300** (PR1 +1, PR2 +6, PR3 +6).

3. **Spec lint (manual eyeball)** — verify `orchestration-contracts.md:415` no longer claims "straight retry loop" and that the validation matrix grew by exactly 2 rows.

## Risky files / rollback points

- `src/arl/exporter/service.py` — every PR touches this. If any PR misbehaves in prod, revert the corresponding commit (commits are linear, no force-pushes).
- `src/arl/shared/failure_contracts.py` — PR3 only touches the `CORE_DECISION_EVENT_TYPES` set; trivially revertable.
- `src/arl/exporter/models.py` — PR3 only adds optional fields; revert is safe (existing event readers default-skip missing fields).
- `.trellis/spec/backend/orchestration-contracts.md` — contract changes co-shipped with code in PR3. Reverting PR3 also reverts the doc, keeping contract + behavior in sync.

## Follow-ups (out of scope for this task)

- **Placeholder-to-success re-export** (scenario D from the brainstorm) — would require splitting `processed_match_keys` into success vs placeholder sets; separate PRD.
- **Operator dashboard for batch aborts** — currently grep-only; could expose via `arl recovery --summary` in a future task.
- **Per-match retry of placeholder-only matches** — a CLI like `arl exporter --retry-placeholders` that re-attempts only matches in the "placeholder" set above. Depends on the split-keys refactor.
