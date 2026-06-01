# End-to-end smoke test for full pipeline

## Goal

Add automated end-to-end test coverage from Windows agent through exporter, in a single pytest file under `tests/e2e/`. The test suite locks the orchestration glue between all stages so future refactors (e.g. event sourcing for orchestrator, cookie auto-refresh in agent) have a guard rail.

## User Value

Operators / contributors get:

1. **Pre-merge confidence**: one `pytest tests/e2e/ -v` proves the agent → orchestrator → recorder → segmenter → subtitles → exporter handoff didn't break.
2. **Documented happy paths**: each test case becomes a code-level spec for "what success looks like" for a given user journey.
3. **Cheaper bug repro**: any failure path that lacks coverage today can be added as a new case in this file.

## Confirmed Facts (from code inspection)

- `tests/pipeline/test_post_live_pipeline.py` already chains recorder → segmenter → subtitles → exporter, but **seeds orchestrator state directly** — the agent → orchestrator coupling is uncovered.
- `WindowsAgentService.__init__` builds probes via `build_probes(settings.platforms)` (`src/arl/windows_agent/service.py:17`). Tests can monkey `service.probes = [FakeProbe(...)]` to inject deterministic snapshots without patching `build_probes`.
- `SubtitleService._load_whisper_model()` gracefully degrades when faster-whisper is unavailable OR the recording path is a `.txt` placeholder (`src/arl/subtitles/service.py:232-248` + `_TRANSCRIBE_SUFFIXES`). **No subtitle mocking needed** — placeholder SRT is produced automatically.
- `ExporterService` consumes `MatchBoundary` + `SubtitleAsset` + `RecordingAsset` from JSONL manifests in `temp_dir`; using a temp `temp_dir` per test gives full isolation.
- 300 pytest baseline (post Session 29). Adding 5 cases brings the total to ~305.

## In-Scope Test Cases (Q3-a, decided 2026-05-14)

| # | Case name | Journey | What it locks |
|---|-----------|---------|---------------|
| 1 | `test_golden_path_single_platform` | Douyin: live_started → recorder mock-success → 1 boundary → 1 subtitle → 1 export mock-success | full-chain success schema; `processed_match_keys` advances by 1 in exporter |
| 2 | `test_cookie_expired_probe_emits_dual_audit` | Bilibili snapshot with `cookie_state=EXPIRED` → agent emits `live_started` + `cookie_expired_for_bilibili`; orchestrator routes both to `orchestrator-events.jsonl` without falling into ignored_unknown_event_type | dual-source cookie audit; orchestrator routing of supplementary events |
| 3 | `test_recorder_transient_failure_schedules_retry` | Live snapshot → recorder ffmpeg mock raises `CalledProcessError` with retryable stderr ("connection reset") → one `ffmpeg_record_failed` with `decision="attempt_failed_yield_to_next_probe"` + `recording_retry_scheduled` + `next_eligible_at_by_job_id` populated | yield-on-transient + per-job backoff state |
| 4 | `test_exporter_ffmpeg_failure_falls_back_to_placeholder` | Golden path through subtitles, exporter ffmpeg mock raises CalledProcessError (ffmpeg_process_error) → 2 `ffmpeg_export_failed` rows + 1 `ffmpeg_export_fallback_placeholder` + `.txt` export asset on disk | exporter audit parity + placeholder fallback path |
| 5 | `test_dual_platform_concurrent_isolation` | `platforms=[douyin, bilibili]` both LIVE → agent emits 2 `live_started` events in order; orchestrator creates 2 sessions + 2 jobs with distinct session_id / platform; recorder processes both | per-platform isolation; agent polling order matches `ARL_PLATFORMS` config order |

## Out of Scope

- Real ffmpeg invocation (decision Q1-b: mock `subprocess.run`).
- Real Playwright / HTTP API calls (probes are fully faked).
- Real faster-whisper transcription (graceful degradation already handles missing model).
- Live stop mid-recording (covered by existing `test_post_live_pipeline.py` state seeding).
- Recorder-side cookie_expired (already covered by `RecorderCookieExpiredEmitTest` in `test_ffmpeg_resilience.py`).
- CI integration — repo has no CI today; tests just need to pass under local `pytest -q`.

## Requirements

- **R1 (test layout)**: New top-level dir `tests/e2e/` with `__init__.py`, `_helpers.py`, and `test_full_pipeline.py`.
- **R2 (fake probe)**: `_helpers.py` exposes `FakeProbe(platform_name, snapshots: list[AgentSnapshot], cookie_states: list[CookieState] | None = None)`. `detect()` pops one snapshot per call (raises if exhausted). `classify_cookie_state()` returns the matching cookie state or `CookieState.FRESH` by default.
- **R3 (snapshot factories)**: Helper functions `make_live_snapshot(platform, stream_url=..., stream_headers=..., source_type=...)` and `make_offline_snapshot(platform, reason=...)` to keep cases readable.
- **R4 (subprocess mocking)**: Each case patches `arl.shared.ffmpeg_runner.subprocess.run` (the helper that recorder + exporter both call); default behavior is `return_value=None` (success), override per-case to raise.
- **R5 (settings sandboxing)**: Each test case constructs a fresh `Settings` with `tempfile.TemporaryDirectory()` for all storage paths, mirroring `test_post_live_pipeline.py`'s pattern.
- **R6 (assertions schema)**: Each case asserts:
  - JSONL line counts at specific event-log paths
  - audit row schema fields (decision / failure_category / reason_code present and correct)
  - state file content (sessions/jobs/processed_match_keys/next_eligible_at_by_job_id)
  - file existence on disk for recording/subtitle/export assets

## Acceptance Criteria

- [ ] `pytest tests/e2e/ -v` → 5 cases all green
- [ ] `pytest -q` total = baseline 300 + 5 = 305
- [ ] No real ffmpeg binary required (no `shutil.which("ffmpeg")` skip)
- [ ] No real faster-whisper model required (test passes whether or not it's pip-installed)
- [ ] Each case under 2 s wall-clock
- [ ] FakeProbe + snapshot factories are re-usable for future e2e cases without changes

## Decisions

- ~~Scope (Q1-a)~~ — medium (separate file + multiple cases) — 2026-05-13.
- ~~ffmpeg approach (Q1-b)~~ — **mock subprocess.run**, no real ffmpeg, no real live stream — revised 2026-05-14.
- ~~Test cases (Q3-a)~~ — 5 cases as listed — 2026-05-14.
- ~~Test directory (Q3-b)~~ — `tests/e2e/` — 2026-05-14.
- ~~Probe injection (Q3-c)~~ — `tests/e2e/_helpers.py` with `FakeProbe` + factories — 2026-05-14.

## Open Questions

All blockers resolved.

## Notes

This is a **lightweight task** — tests + helpers only, no production code changes. PRD + light `implement.md` (no separate `design.md`) is sufficient.
