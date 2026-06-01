# Implement: End-to-end smoke test for full pipeline

2 PRs. PR1 establishes harness + golden path; PR2 adds the remaining 4 cases.

## PR1 — `tests/e2e/` skeleton + golden path case

### Files

- `tests/e2e/__init__.py` — empty.
- `tests/e2e/_helpers.py`:
  - `class FakeProbe(PlatformProbe)`: constructor takes `platform_name: str`, `snapshots: list[AgentSnapshot]`, optional `cookie_states: list[CookieState] | None`. `detect()` pops next snapshot; raises `RuntimeError("FakeProbe snapshots exhausted")` if empty. `classify_cookie_state(snapshot)` returns matching state or `CookieState.FRESH`. `stream_headers(snapshot)` returns `snapshot.stream_headers`.
  - `def make_live_snapshot(platform, *, stream_url=None, stream_headers=None, source_type=SourceType.DIRECT_STREAM, room_url=...)` → `AgentSnapshot` with `state=LiveState.LIVE`.
  - `def make_offline_snapshot(platform, *, reason="manual", room_url=...)` → `AgentSnapshot` with `state=LiveState.OFFLINE`.
  - `def build_sandboxed_settings(tmp_path: Path, platforms=("douyin",)) -> Settings` — returns a `Settings` instance with `temp_dir` / `raw_dir` / `processed_dir` / `export_dir` rooted at `tmp_path` and `enable_ffmpeg=True` on both recorder + exporter.
- `tests/e2e/test_full_pipeline.py`:
  - `class GoldenPathTest(unittest.TestCase)` with `setUp` / `tearDown` using `tempfile.TemporaryDirectory()`.
  - `test_golden_path_single_platform`:
    1. Build settings via helper; inject `FakeProbe("douyin", [make_live_snapshot("douyin", stream_url="https://stub")])` into `WindowsAgentService(settings).probes`.
    2. Run `WindowsAgentService.run_once()` → assert `windows-agent-events.jsonl` has 1 `live_started`.
    3. Run `OrchestratorService(settings).run(once=True)` → assert state has 1 session + 1 recording job.
    4. Patch `arl.shared.ffmpeg_runner.subprocess.run` with `return_value=None`; run `RecorderService(settings).run()` → assert `recording-assets.jsonl` has 1 line, `ffmpeg_record_succeeded` in `recorder-events.jsonl`.
    5. Mark session STOPPED (simulating live_stopped → orchestrator transition), re-run orchestrator once, run `SegmenterService(settings).run()` → assert `match-boundaries.jsonl` has 1 line.
    6. Run `SubtitleService(settings).run()` → assert `subtitle-assets.jsonl` has 1 line (placeholder SRT, since fake ffmpeg means no real video data).
    7. Patch `subprocess.run` (same target) with `return_value=None`; run `ExporterService(settings).run()` → assert `export-assets.jsonl` has 1 line, `ffmpeg_export_succeeded` in `exporter-events.jsonl`.

### Validation

```powershell
.\.venv\Scripts\python.exe -m pytest tests/e2e/ -v
.\.venv\Scripts\python.exe -m pytest -q  # 300 + 1 = 301
```

### Commit

```
test(e2e): full-pipeline harness + golden path case
```

---

## PR2 — Remaining 4 cases

**Depends on**: PR1 merged.

### Files

- `tests/e2e/test_full_pipeline.py` — add 4 new test classes (one per case) reusing the helpers from PR1:

  - `class CookieExpiredProbeTest`:
    - FakeProbe with one bilibili snapshot + `cookie_states=[CookieState.EXPIRED]`.
    - Run agent → orchestrator.
    - Assert `windows-agent-events.jsonl` has 2 lines: `live_started` then `cookie_expired_for_bilibili`.
    - Assert `orchestrator-events.jsonl` has both events routed (no `ignored_unknown_event_type`).

  - `class RecorderTransientRetryTest`:
    - FakeProbe with 1 douyin live snapshot.
    - Patch `arl.shared.ffmpeg_runner.subprocess.run` with `side_effect=CalledProcessError(1, [...], stderr="Connection reset by peer")`.
    - Run agent → orchestrator → recorder.
    - Assert `recorder-events.jsonl` has `ffmpeg_record_failed` with `decision="attempt_failed_yield_to_next_probe"`, `is_retryable=True`, `reason_code="network_timeout"` (or whatever `classify_failure_reason("connection reset")` returns).
    - Assert `recording_retry_scheduled` row present with `attempt=1`.
    - Assert `recorder-state.json` has `next_eligible_at_by_job_id[job_id]` set in the future.

  - `class ExporterFfmpegFailureTest`:
    - Reuse the golden setup, but patch the ffmpeg helper's `subprocess.run` to raise on every call (`exit_status:1` → ffmpeg_process_error_retryable, exporter retries to max then falls back).
    - Run full chain; assert exporter emitted 2 `ffmpeg_export_failed` + 1 `ffmpeg_export_fallback_placeholder`; assert `.txt` placeholder exists in export_dir.

  - `class DualPlatformConcurrencyTest`:
    - FakeProbe with douyin live + FakeProbe with bilibili live, in that order.
    - Run agent once → assert events in order: `live_started`(douyin), `live_started`(bilibili).
    - Run orchestrator once → assert state has 2 sessions, 2 jobs, distinct `session_id` / `platform` values.

### Validation

```powershell
.\.venv\Scripts\python.exe -m pytest tests/e2e/ -v  # 5 cases
.\.venv\Scripts\python.exe -m pytest -q  # 301 + 4 = 305
```

### Commit

```
test(e2e): cookie expired / recorder retry / exporter failure / dual-platform cases
```

---

## Risky files / rollback points

- All changes are inside `tests/e2e/`; reverting either PR is safe and removes only test coverage (no production behavior change).
- Helper signatures in `_helpers.py` may need iteration once PR2's 4 cases are wired; expect 1-2 small follow-ups within PR2.

## Verification after both PRs

- `pytest -q` total ≥ 305 (vs baseline 300).
- `pytest tests/e2e/ -v` finishes < 10 s wall-clock.
- No real ffmpeg or faster-whisper required.

## Follow-ups (out of scope)

- Adding cases for: live_stopped mid-recording, recording_session_retry_budget_exceeded escalation, cookie auto-refresh (task E once it exists).
- CI integration when the project adopts GitHub Actions / similar.
