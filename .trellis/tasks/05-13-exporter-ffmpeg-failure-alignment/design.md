# Design: Exporter ffmpeg failure handling alignment with recorder

## Architecture

Three additive layers on top of the existing `ExporterService.run()` boundaries loop. No new modules; all changes inside `src/arl/exporter/`.

```
ExporterService.run()
  ├── boundaries loop  ← (L3) batch budget counter wraps this
  │     └── _write_export_with_ffmpeg()
  │           └── for attempt in range(...)  ← (L1+L2) short-circuit + backoff
  │                 └── run_ffmpeg_attempt()  (existing shared helper, unchanged)
```

- **L1 (non-retryable short-circuit)**: inside the attempt loop, `break` immediately when `outcome.classification.is_retryable is False`. Pre-existing audit emission unchanged; only the `for` loop exits early.
- **L2 (per-attempt backoff)**: inside the attempt loop, after emitting the failed audit row, **and only when the failure is retryable AND another attempt remains**, sleep for `_backoff_seconds(attempt)`. Backed by a module-level `time.sleep` so tests monkeypatch it without injection plumbing.
- **L3 (batch budget)**: `run()` holds a local `consecutive_fallbacks` int; the boundaries `for` loop checks it after each match, emits `ffmpeg_export_batch_aborted`, and `break`s when the threshold is hit. Counter is local to the `run()` call frame — not on `ExporterStateFile`, not on `self`.

## Data flow / contracts

### New `ExportSettings` fields (`src/arl/config.py:140`)

```python
class ExportSettings(BaseModel):
    # ... existing fields ...
    backoff_initial_seconds: float = 2.0      # ARL_EXPORTER_BACKOFF_INITIAL_SECONDS
    backoff_max_seconds: float = 8.0          # ARL_EXPORTER_BACKOFF_MAX_SECONDS
    batch_fallback_budget: int = 3            # ARL_EXPORTER_BATCH_FALLBACK_BUDGET (min 1)
```

`load_settings()` reads each via `os.getenv` with the existing `max(...)` lower-bound idiom for `batch_fallback_budget`.

### New audit fields on `ExporterAuditEvent` (`src/arl/exporter/models.py:14`)

```python
class ExporterAuditEvent(BaseModel):
    # ... existing fields ...
    consecutive_fallbacks: int | None = None  # populated only on ffmpeg_export_batch_aborted
    remaining_matches: int | None = None      # populated only on ffmpeg_export_batch_aborted
```

Both default `None` so existing event types serialize unchanged.

### New entry in `CORE_DECISION_EVENT_TYPES` (`src/arl/shared/failure_contracts.py:36`)

Add `"ffmpeg_export_batch_aborted"` to the set. This event then **must** carry the canonical decision tuple (validator already enforces this).

### Decision strings

| Event | `decision` | Notes |
|-------|------------|-------|
| `ffmpeg_export_failed` (existing) | `"attempt_failed"` | unchanged |
| `ffmpeg_export_fallback_placeholder` (existing) | `"fallback_placeholder"` | unchanged |
| `ffmpeg_export_batch_aborted` (NEW) | `"batch_aborted"` | new canonical decision string |

`batch_aborted` is not added to a registry — there is no closed enum of `decision` strings in the codebase (only `failure_category` and `reason_code` are registry-enforced).

### Audit row example (batch_aborted)

```json
{
  "event_type": "ffmpeg_export_batch_aborted",
  "session_id": "session-X",
  "match_index": 3,
  "decision": "batch_aborted",
  "failure_category": "ffmpeg_process_error_retryable",
  "is_retryable": true,
  "reason_code": "ffmpeg_process_error",
  "reason_detail": "exit_status:1",
  "consecutive_fallbacks": 3,
  "remaining_matches": 7,
  "created_at": "2026-05-13T..."
}
```

The three classification fields are inherited from **the last fallback** (i.e., the one that triggered the trip). `match_index` is the last attempted match (the one whose fallback put the counter at threshold).

## Module-by-module changes

### `src/arl/exporter/service.py`

**`_write_export_with_ffmpeg()` — in-run loop rewrite** (lines 128-223):

```python
attempts = self.settings.export.ffmpeg_max_retries + 1
last_outcome = None
for attempt in range(1, attempts + 1):
    outcome = run_ffmpeg_attempt(...)
    if outcome.success:
        self._append_audit("ffmpeg_export_succeeded", ...)
        return output_path

    last_outcome = outcome
    fd = outcome.classification
    log("exporter", "ffmpeg export failed ...")
    self._append_audit("ffmpeg_export_failed", decision="attempt_failed", ...)

    # L1: non-retryable short-circuit
    if not fd.is_retryable:
        break
    # L2: per-attempt backoff before the next retryable attempt
    if attempt < attempts:
        time.sleep(self._backoff_seconds(attempt))

# fallback path unchanged (lines 205-223)
```

**New helper**:

```python
def _backoff_seconds(self, attempt: int) -> float:
    initial = self.settings.export.backoff_initial_seconds
    max_seconds = self.settings.export.backoff_max_seconds
    return min(initial * (2 ** (attempt - 1)), max_seconds)
```

Schedule for default (initial=2, max=8): attempt 1→2 sleep 2s, 2→3 sleep 4s, 3→4 sleep 8s, ≥4 stays 8s. With default `ffmpeg_max_retries=1` only the 2s slot is used.

**`run()` — boundaries loop wrap** (lines 27-73):

```python
def run(self) -> None:
    # ... existing setup ...
    consecutive_fallbacks = 0
    budget = self.settings.export.batch_fallback_budget

    for index, boundary in enumerate(boundaries):
        # ... existing skip / lookup checks ...

        result = self._write_export(boundary, subtitle, recording_asset)
        # _write_export now returns (path, was_placeholder: bool)
        output_path, was_placeholder = result

        # ... existing append_model + processed_match_keys.append ...

        if was_placeholder:
            consecutive_fallbacks += 1
            if consecutive_fallbacks >= budget:
                remaining = len(boundaries) - index - 1
                last_fd = self._last_failure_classification  # cached by _write_export_with_ffmpeg
                self._append_audit(
                    "ffmpeg_export_batch_aborted",
                    session_id=boundary.session_id,
                    match_index=boundary.match_index,
                    decision="batch_aborted",
                    failure_category=last_fd.failure_category,
                    is_retryable=last_fd.is_retryable,
                    reason_code=last_fd.reason_code,
                    reason_detail=self._last_failure_reason,
                    consecutive_fallbacks=consecutive_fallbacks,
                    remaining_matches=remaining,
                )
                log("exporter", f"batch aborted budget={budget} ...")
                break
        else:
            consecutive_fallbacks = 0
```

To carry the last failure's classification into the `batch_aborted` row, `_write_export_with_ffmpeg` stashes the last failure `FailureDecision` + `reason` on `self._last_failure_classification` / `self._last_failure_reason` (both reset at top of `run()`).

**`_write_export` return shape change**: returns `(Path, bool)` instead of `Path`. Two call sites:
- `_write_export_with_ffmpeg` returns `(output_path, False)` on success, `(placeholder_path, True)` on fallback.
- `_write_placeholder_export` path (the non-ffmpeg branch in `_write_export`) returns `(placeholder_path, True)` — wait, no. The non-ffmpeg branch is "ffmpeg disabled or prerequisite unmet" — that's not a fallback from a failed attempt, it's an intentional placeholder. **Should this count toward the budget?**

  **Decision (in scope of this PRD)**: NO. Only `_write_export_with_ffmpeg`'s fallback path increments the counter. A run with `enable_ffmpeg=False` shouldn't trip `batch_aborted` after 3 matches because that path never failed — it just opted out. → `_write_export` returns `(path, False)` for the intentional-placeholder branch; only the actual ffmpeg fallback path returns `(path, True)`.

### `src/arl/exporter/models.py`

Add two `int | None = None` fields to `ExporterAuditEvent`. No validator change needed (the existing validator only enforces the canonical decision tuple; new fields are observability metadata).

### `src/arl/shared/failure_contracts.py`

Add `"ffmpeg_export_batch_aborted"` to `CORE_DECISION_EVENT_TYPES`. No other change.

### `src/arl/config.py`

Add three fields to `ExportSettings`; wire env vars in `load_settings()`:

```python
export=ExportSettings(
    # ... existing ...
    backoff_initial_seconds=float(os.getenv("ARL_EXPORTER_BACKOFF_INITIAL_SECONDS", "2")),
    backoff_max_seconds=float(os.getenv("ARL_EXPORTER_BACKOFF_MAX_SECONDS", "8")),
    batch_fallback_budget=max(1, int(os.getenv("ARL_EXPORTER_BATCH_FALLBACK_BUDGET", "3"))),
),
```

### `.trellis/spec/backend/orchestration-contracts.md`

Two surgical edits:

1. **Line 415** — current text says "exporter does NOT yield-on-transient ... so this is a straight retry loop". Rewrite:

   > `ARL_EXPORT_FFMPEG_MAX_RETRIES` (int >= 0, default `1`) — in-run retry count for exporter ffmpeg. Exporter does NOT yield-on-transient (recorder-only behavior — exporter's input is a local file, no probe to wait for), but the in-run loop **does** short-circuit on non-retryable failures (4xx, unknown) and **does** sleep between retryable attempts via the exponential backoff governed by `ARL_EXPORTER_BACKOFF_INITIAL_SECONDS` / `ARL_EXPORTER_BACKOFF_MAX_SECONDS`.

2. **After line 416** — add the three new env vars to the env-keys block:

   - `ARL_EXPORTER_BACKOFF_INITIAL_SECONDS` (float, default `2.0`) — first inter-attempt sleep
   - `ARL_EXPORTER_BACKOFF_MAX_SECONDS` (float, default `8.0`) — backoff cap
   - `ARL_EXPORTER_BATCH_FALLBACK_BUDGET` (int >= 1, default `3`) — consecutive match-level fallbacks before exporter emits `ffmpeg_export_batch_aborted` and stops the boundaries loop

3. **Validation matrix** — add two rows mirroring recorder rows 638/643:

   | Exporter sees non-retryable ffmpeg failure | Emits exactly one `ffmpeg_export_failed` row + `ffmpeg_export_fallback_placeholder`; no further attempts; counter increments by 1 |
   | Consecutive `ARL_EXPORTER_BATCH_FALLBACK_BUDGET` match-level fallbacks reached | Emit one `ffmpeg_export_batch_aborted` with `consecutive_fallbacks` + `remaining_matches`; remaining matches in the boundaries loop are NOT processed and NOT added to `processed_match_keys` |

### `.trellis/spec/backend/quality-guidelines.md`

Add two Common Mistakes (placement: extend the existing "exporter ffmpeg patch-shim" section):

1. **"Counting attempts, not match fallbacks, in batch budget"** — explain the divergence: a 2-attempt success spike would otherwise burn the budget on a successful match.
2. **"Retrying exporter ffmpeg on non-retryable failures"** — recorder yields to next probe so retry makes sense there, but exporter has no upstream refresh; 4xx/unknown classification means the input is structurally broken — short-circuit.

### `README.md`

Extend the existing "ffmpeg 失败排查（exporter）" section (currently at line 230-237):

- Mention the three new env vars next to the existing `ARL_EXPORT_FFMPEG_MAX_RETRIES`.
- Add a bullet: "**Batch 早停**：连续 N 个 match fallback（默认 N=3）后，导出器会输出一行 `ffmpeg_export_batch_aborted` 并跳出本次 run；未处理的 match 留给下次 `arl exporter` 重试。grep `ffmpeg_export_batch_aborted` 是 "本批次是不是被主机问题搞挂了" 的一行答案。"

## Compatibility / migration

- `ExporterStateFile` schema unchanged → old state files load without migration.
- New `ExporterAuditEvent` fields default to `None` → readers that grep older event files don't break.
- Default values for the three new env vars match current behavior in the common case (default `ffmpeg_max_retries=1` only uses 1 backoff slot; default budget=3 only trips on truly batch-wide failures).
- Existing test `test_ffmpeg_failed_emits_audit_with_stderr_log` (PR1) needs assertion update from `len==2` to `len==1` for 4xx case. Other 50+ tests in `test_ffmpeg_resilience.py` unaffected (they test either recorder, success path, or `OSError`/`unknown` classifications).

## Trade-offs

- **In-memory budget vs persisted budget**: in-memory is simpler and matches "one run = one host-pressure window" semantics. Trade-off: if `arl exporter` is invoked in a tight loop (cron every minute), each invocation gets a fresh budget. Acceptable — operator running it tight is implicitly opting out of cross-run safety.
- **Counter resets on success**: catches "host is broken right now" but allows long batches with sparse failures. Trade-off: a flaky host (50% fail rate) won't trip the budget. We chose this over cumulative to avoid spurious aborts on long batches with a few genuinely-bad inputs.
- **Decision string `"batch_aborted"`**: deliberately not reusing recorder's `"manual_required"`. The semantics differ: recorder escalates a specific job to manual recovery; exporter leaves remaining matches eligible for the next run. Reusing `"manual_required"` would mislead operators.
- **No yield-on-transient**: keeps the prior architectural decision (no upstream to wait for) — this PRD adds layers next to that decision, not on top of it.

## Operational notes

- Logs to look for:
  - `exporter ffmpeg backoff sleeping seconds=2.0 attempt=1/2` (PR2)
  - `exporter batch aborted budget=3 consecutive_fallbacks=3 remaining=7` (PR3)
- Single-pass grep for "did this run hit batch trouble":
  ```
  grep ffmpeg_export_batch_aborted data/tmp/exporter-events.jsonl
  ```
- Rollback: revert PR3 alone disables the batch budget without affecting backoff or short-circuit. Revert PR2+PR3 disables both new behaviors; PR1 stays as a small standalone improvement.
