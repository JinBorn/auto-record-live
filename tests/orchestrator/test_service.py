from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from arl.config import DouyinSettings, OrchestratorSettings, Settings
from arl.orchestrator.models import (
    OrchestratorStateFile,
    RecordingJobStatus,
    SessionStatus,
)
from arl.orchestrator.service import OrchestratorService


def _event_line(
    event_type: str,
    *,
    state: str,
    detected_at: str,
    source_type: str | None = "browser_capture",
    stream_url: str | None = None,
) -> str:
    payload = {
        "event_type": event_type,
        "snapshot": {
            "state": state,
            "streamer_name": "streamer-a",
            "room_url": "https://live.douyin.com/room",
            "source_type": source_type,
            "stream_url": stream_url,
            "reason": "test",
            "detected_at": detected_at,
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _recorder_event_line(
    event_type: str,
    *,
    session_id: str,
    job_id: str,
    created_at: str,
    reason: str | None = None,
    source_type: str = "direct_stream",
) -> str:
    canonical_defaults: dict[str, tuple[str, str, bool]] = {
        "recording_retry_scheduled": ("retry_scheduled", "ffmpeg_process_error_retryable", True),
        "ffmpeg_record_failed": ("attempt_failed", "ffmpeg_process_error_retryable", True),
        "ffmpeg_fallback_placeholder": (
            "fallback_placeholder",
            "unknown_unclassified_non_retryable",
            False,
        ),
        "recording_manual_recovery_required": (
            "manual_required",
            "unknown_unclassified_non_retryable",
            False,
        ),
    }
    payload = {
        "event_type": event_type,
        "session_id": session_id,
        "job_id": job_id,
        "source_type": source_type,
        "reason": reason,
        "attempt": 1,
        "max_attempts": 2,
        "created_at": created_at,
    }
    if event_type in canonical_defaults:
        decision, failure_category, is_retryable = canonical_defaults[event_type]
        reason_code = "ffmpeg_process_error"
        reason_text = (reason or "").lower()
        if "server returned 404" in reason_text:
            failure_category = "http_4xx_non_retryable"
            is_retryable = False
            reason_code = "http_4xx"
        elif "server returned 403" in reason_text:
            failure_category = "http_4xx_non_retryable"
            is_retryable = False
            reason_code = "http_4xx"
        elif "server returned 503" in reason_text:
            failure_category = "http_5xx_retryable"
            is_retryable = True
            reason_code = "http_5xx"
        elif "missing_binary" in reason_text:
            failure_category = "unknown_unclassified_non_retryable"
            is_retryable = False
            reason_code = "unknown_unclassified"
        payload.update(
            {
                "decision": decision,
                "failure_category": failure_category,
                "is_retryable": is_retryable,
                "reason_code": reason_code,
                "reason_detail": reason or "test_reason",
            }
        )
    return json.dumps(payload, ensure_ascii=False)


class OrchestratorServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.agent_event_log = root / "windows-agent-events.jsonl"
        self.recorder_event_log = root / "recorder-events.jsonl"
        self.state_file = root / "orchestrator-state.json"
        self.audit_log = root / "orchestrator-events.jsonl"

        settings = Settings(
            douyin=DouyinSettings(event_log_path=self.agent_event_log),
            orchestrator=OrchestratorSettings(
                agent_event_log_path=self.agent_event_log,
                recorder_event_log_path=self.recorder_event_log,
                state_file=self.state_file,
                audit_log_path=self.audit_log,
            ),
        )
        self.service = OrchestratorService(settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_start_then_stop_creates_closed_session_and_job(self) -> None:
        lines = [
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
            ),
            _event_line(
                "live_stopped",
                state="offline",
                detected_at="2026-04-24T02:00:00Z",
                source_type=None,
            ),
        ]
        self.agent_event_log.write_text("\n".join(lines) + "\n", encoding="utf-8")

        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.sessions), 1)
        self.assertEqual(len(state.recording_jobs), 1)
        self.assertIsNone(state.active_session_id)
        self.assertIsNone(state.active_recording_job_id)
        self.assertEqual(state.sessions[0].status, SessionStatus.STOPPED)
        self.assertIsNotNone(state.sessions[0].ended_at)
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.STOPPED)
        self.assertIsNotNone(state.recording_jobs[0].ended_at)

    def test_duplicate_live_started_does_not_create_second_session(self) -> None:
        lines = [
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
            ),
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:10Z",
            ),
        ]
        self.agent_event_log.write_text("\n".join(lines) + "\n", encoding="utf-8")

        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.sessions), 1)
        self.assertEqual(len(state.recording_jobs), 1)
        self.assertIsNotNone(state.active_session_id)
        self.assertIsNotNone(state.active_recording_job_id)
        self.assertEqual(state.sessions[0].status, SessionStatus.LIVE)
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.QUEUED)

    def test_duplicate_live_started_enriches_active_job_stream_url(self) -> None:
        lines = [
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="browser_capture",
                stream_url=None,
            ),
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:10Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live.m3u8",
            ),
        ]
        self.agent_event_log.write_text("\n".join(lines) + "\n", encoding="utf-8")

        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.sessions), 1)
        self.assertEqual(len(state.recording_jobs), 1)
        self.assertEqual(state.sessions[0].source_type.value, "direct_stream")
        self.assertEqual(state.sessions[0].stream_url, "https://cdn.example/live.m3u8")
        self.assertEqual(state.recording_jobs[0].source_type.value, "direct_stream")
        self.assertEqual(state.recording_jobs[0].stream_url, "https://cdn.example/live.m3u8")

    def test_duplicate_live_started_recreates_job_when_active_job_already_ended(self) -> None:
        lines = [
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live-1.m3u8",
            ),
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:01:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live-2.m3u8",
            ),
        ]
        self.agent_event_log.write_text("\n".join(lines) + "\n", encoding="utf-8")

        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.sessions), 1)
        self.assertEqual(len(state.recording_jobs), 1)
        first_job = state.recording_jobs[0]

        first_job.status = RecordingJobStatus.FAILED
        first_job.ended_at = first_job.created_at
        state.active_recording_job_id = None
        self.state_file.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

        with self.agent_event_log.open("a", encoding="utf-8") as handle:
            handle.write(
                _event_line(
                    "live_started",
                    state="live",
                    detected_at="2026-04-24T01:02:00Z",
                    source_type="direct_stream",
                    stream_url="https://cdn.example/live-3.m3u8",
                )
                + "\n"
            )

        self.service.run_once()

        updated = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(updated.sessions), 1)
        self.assertEqual(len(updated.recording_jobs), 2)
        self.assertEqual(updated.recording_jobs[-1].status, RecordingJobStatus.QUEUED)
        self.assertEqual(
            updated.recording_jobs[-1].stream_url,
            "https://cdn.example/live-3.m3u8",
        )
        self.assertEqual(updated.active_recording_job_id, updated.recording_jobs[-1].job_id)

    def test_new_streamer_live_started_replaces_stale_active_session(self) -> None:
        lines = [
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/streamer-a.m3u8",
            ),
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:01:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/streamer-b.m3u8",
            ).replace("streamer-a", "streamer-b").replace("/room", "/room-b"),
        ]
        self.agent_event_log.write_text("\n".join(lines) + "\n", encoding="utf-8")

        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.sessions), 2)
        self.assertEqual(len(state.recording_jobs), 2)
        self.assertEqual(state.sessions[0].status, SessionStatus.STOPPED)
        self.assertEqual(state.sessions[0].stop_reason, "superseded_by_new_live_started")
        self.assertEqual(state.sessions[1].status, SessionStatus.LIVE)
        self.assertEqual(state.sessions[1].streamer_name, "streamer-b")
        self.assertEqual(state.active_session_id, state.sessions[1].session_id)
        self.assertEqual(state.active_recording_job_id, state.recording_jobs[1].job_id)

    def test_cursor_supports_incremental_processing(self) -> None:
        self.agent_event_log.write_text(
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()

        with self.agent_event_log.open("a", encoding="utf-8") as handle:
            handle.write(
                _event_line(
                    "live_stopped",
                    state="offline",
                    detected_at="2026-04-24T01:10:00Z",
                    source_type=None,
                )
                + "\n"
            )
        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.sessions), 1)
        self.assertEqual(len(state.recording_jobs), 1)
        self.assertEqual(state.sessions[0].status, SessionStatus.STOPPED)
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.STOPPED)

    def test_recorder_retry_events_update_recording_job_status(self) -> None:
        self.agent_event_log.write_text(
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live.m3u8",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.recording_jobs), 1)
        job = state.recording_jobs[0]
        self.assertEqual(job.status, RecordingJobStatus.QUEUED)

        self.recorder_event_log.write_text(
            _recorder_event_line(
                "recording_retry_scheduled",
                session_id=job.session_id,
                job_id=job.job_id,
                reason="exit_status:1",
                created_at="2026-04-24T01:05:00Z",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.RETRYING)
        self.assertEqual(state.recording_jobs[0].stop_reason, "exit_status:1")
        self.assertIsNone(state.recording_jobs[0].ended_at)

        with self.recorder_event_log.open("a", encoding="utf-8") as handle:
            handle.write(
                _recorder_event_line(
                    "recording_retry_exhausted",
                    session_id=job.session_id,
                    job_id=job.job_id,
                    reason="exit_status:1",
                    created_at="2026-04-24T01:10:00Z",
                )
                + "\n"
            )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.FAILED)
        self.assertEqual(state.recording_jobs[0].stop_reason, "exit_status:1")
        self.assertIsNotNone(state.recording_jobs[0].ended_at)
        self.assertEqual(state.recording_jobs[0].failure_category, "ffmpeg_process_error_retryable")
        self.assertTrue(state.recording_jobs[0].recoverable)
        self.assertIsNotNone(state.recording_jobs[0].recovery_hint)
        self.assertIsNone(state.active_recording_job_id)

        with self.recorder_event_log.open("a", encoding="utf-8") as handle:
            handle.write(
                _recorder_event_line(
                    "recording_retry_scheduled",
                    session_id=job.session_id,
                    job_id=job.job_id,
                    reason="manual_recovery_resolved",
                    created_at="2026-04-24T01:15:00Z",
                )
                + "\n"
            )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.RETRYING)
        self.assertEqual(state.recording_jobs[0].stop_reason, "manual_recovery_resolved")
        self.assertIsNone(state.recording_jobs[0].ended_at)
        self.assertEqual(state.active_recording_job_id, job.job_id)

    def test_ffmpeg_success_event_closes_retrying_job(self) -> None:
        self.agent_event_log.write_text(
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live.m3u8",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        job = state.recording_jobs[0]

        self.recorder_event_log.write_text(
            "\n".join(
                [
                    _recorder_event_line(
                        "recording_retry_scheduled",
                        session_id=job.session_id,
                        job_id=job.job_id,
                        reason="exit_status:1",
                        created_at="2026-04-24T01:05:00Z",
                    ),
                    _recorder_event_line(
                        "ffmpeg_record_succeeded",
                        session_id=job.session_id,
                        job_id=job.job_id,
                        created_at="2026-04-24T01:08:00Z",
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.STOPPED)
        self.assertIsNone(state.recording_jobs[0].stop_reason)
        self.assertIsNotNone(state.recording_jobs[0].ended_at)
        self.assertIsNone(state.active_recording_job_id)
        self.assertIsNone(state.recording_jobs[0].failure_category)
        self.assertIsNone(state.recording_jobs[0].recoverable)
        self.assertIsNone(state.recording_jobs[0].recovery_hint)

    def test_stale_recorder_event_is_ignored_and_does_not_regress_status(self) -> None:
        self.agent_event_log.write_text(
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live.m3u8",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        job = state.recording_jobs[0]

        self.recorder_event_log.write_text(
            "\n".join(
                [
                    _recorder_event_line(
                        "recording_retry_exhausted",
                        session_id=job.session_id,
                        job_id=job.job_id,
                        reason="exit_status:1",
                        created_at="2026-04-24T01:10:00Z",
                    ),
                    _recorder_event_line(
                        "recording_retry_scheduled",
                        session_id=job.session_id,
                        job_id=job.job_id,
                        reason="exit_status:1",
                        created_at="2026-04-24T01:05:00Z",
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.FAILED)
        self.assertEqual(state.recording_jobs[0].stop_reason, "exit_status:1")

        audit_lines = [
            json.loads(line)
            for line in self.audit_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        stale_events = [
            line for line in audit_lines if line.get("event_type") == "recorder_event_stale_ignored"
        ]
        self.assertEqual(len(stale_events), 1)

    def test_duplicate_recorder_event_timestamp_is_ignored_idempotently(self) -> None:
        self.agent_event_log.write_text(
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live.m3u8",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        job = state.recording_jobs[0]

        self.recorder_event_log.write_text(
            "\n".join(
                [
                    _recorder_event_line(
                        "recording_retry_exhausted",
                        session_id=job.session_id,
                        job_id=job.job_id,
                        reason="terminal_failure",
                        created_at="2026-04-24T01:10:00Z",
                    ),
                    _recorder_event_line(
                        "recording_retry_scheduled",
                        session_id=job.session_id,
                        job_id=job.job_id,
                        reason="should_be_ignored",
                        created_at="2026-04-24T01:10:00Z",
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.FAILED)
        self.assertEqual(state.recording_jobs[0].stop_reason, "terminal_failure")

        audit_lines = [
            json.loads(line)
            for line in self.audit_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        stale_events = [
            line for line in audit_lines if line.get("event_type") == "recorder_event_stale_ignored"
        ]
        self.assertEqual(len(stale_events), 1)

    def test_unknown_recorder_event_does_not_block_older_known_event(self) -> None:
        self.agent_event_log.write_text(
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live.m3u8",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        job = state.recording_jobs[0]

        self.recorder_event_log.write_text(
            "\n".join(
                [
                    _recorder_event_line(
                        "recording_custom_future_event",
                        session_id=job.session_id,
                        job_id=job.job_id,
                        reason="unknown",
                        created_at="2026-04-24T01:10:00Z",
                    ),
                    _recorder_event_line(
                        "recording_retry_scheduled",
                        session_id=job.session_id,
                        job_id=job.job_id,
                        reason="retry_after_unknown",
                        created_at="2026-04-24T01:05:00Z",
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.RETRYING)
        self.assertEqual(state.recording_jobs[0].stop_reason, "retry_after_unknown")
        self.assertEqual(state.active_recording_job_id, job.job_id)

        audit_lines = [
            json.loads(line)
            for line in self.audit_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(
            any(line.get("event_type") == "recorder_event_ignored" for line in audit_lines)
        )
        stale_events = [
            line for line in audit_lines if line.get("event_type") == "recorder_event_stale_ignored"
        ]
        self.assertEqual(stale_events, [])

    def test_unknown_older_recorder_event_is_ignored_not_marked_stale(self) -> None:
        self.agent_event_log.write_text(
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live.m3u8",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        job = state.recording_jobs[0]

        self.recorder_event_log.write_text(
            "\n".join(
                [
                    _recorder_event_line(
                        "recording_retry_scheduled",
                        session_id=job.session_id,
                        job_id=job.job_id,
                        reason="known_event",
                        created_at="2026-04-24T01:10:00Z",
                    ),
                    _recorder_event_line(
                        "recording_custom_older_event",
                        session_id=job.session_id,
                        job_id=job.job_id,
                        reason="unknown_older",
                        created_at="2026-04-24T01:05:00Z",
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()

        updated_state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(updated_state.recording_jobs[0].status, RecordingJobStatus.RETRYING)
        self.assertEqual(updated_state.recording_jobs[0].stop_reason, "known_event")

        audit_lines = [
            json.loads(line)
            for line in self.audit_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        ignored_events = [
            line for line in audit_lines if line.get("event_type") == "recorder_event_ignored"
        ]
        stale_events = [
            line for line in audit_lines if line.get("event_type") == "recorder_event_stale_ignored"
        ]
        self.assertEqual(len(ignored_events), 1)
        self.assertEqual(ignored_events[0].get("message"), "event_type=recording_custom_older_event")
        self.assertEqual(stale_events, [])

    def test_non_recoverable_failure_classification_for_missing_binary(self) -> None:
        self.agent_event_log.write_text(
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live.m3u8",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        job = state.recording_jobs[0]

        self.recorder_event_log.write_text(
            _recorder_event_line(
                "ffmpeg_skipped",
                session_id=job.session_id,
                job_id=job.job_id,
                reason="missing_binary",
                created_at="2026-04-24T01:02:00Z",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.FAILED)
        self.assertEqual(
            state.recording_jobs[0].failure_category,
            "unknown_unclassified_non_retryable",
        )
        self.assertFalse(state.recording_jobs[0].recoverable)
        self.assertIn("inconclusive", state.recording_jobs[0].recovery_hint or "")

    def test_ffmpeg_record_failed_recoverable_keeps_job_retrying(self) -> None:
        self.agent_event_log.write_text(
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live.m3u8",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        job = state.recording_jobs[0]

        self.recorder_event_log.write_text(
            _recorder_event_line(
                "ffmpeg_record_failed",
                session_id=job.session_id,
                job_id=job.job_id,
                reason="exit_status:1",
                created_at="2026-04-24T01:03:00Z",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.RETRYING)
        self.assertEqual(state.recording_jobs[0].failure_category, "ffmpeg_process_error_retryable")
        self.assertTrue(state.recording_jobs[0].recoverable)
        self.assertEqual(state.active_recording_job_id, job.job_id)

        audit_lines = [
            json.loads(line)
            for line in self.audit_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(
            any(
                line.get("event_type") == "recording_job_attempt_failed_retrying"
                for line in audit_lines
            )
        )
        self.assertTrue(
            any(
                line.get("event_type") == "recording_job_recovery_retry_planned"
                for line in audit_lines
            )
        )

    def test_ffmpeg_record_failed_non_recoverable_marks_job_failed(self) -> None:
        self.agent_event_log.write_text(
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live.m3u8",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        job = state.recording_jobs[0]

        self.recorder_event_log.write_text(
            _recorder_event_line(
                "ffmpeg_record_failed",
                session_id=job.session_id,
                job_id=job.job_id,
                reason="missing_binary",
                created_at="2026-04-24T01:03:00Z",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.FAILED)
        self.assertEqual(
            state.recording_jobs[0].failure_category,
            "unknown_unclassified_non_retryable",
        )
        self.assertFalse(state.recording_jobs[0].recoverable)
        self.assertIsNotNone(state.recording_jobs[0].ended_at)
        self.assertIsNone(state.active_recording_job_id)

        audit_lines = [
            json.loads(line)
            for line in self.audit_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(
            any(
                line.get("event_type") == "recording_job_attempt_failed_terminal"
                for line in audit_lines
            )
        )
        self.assertTrue(
            any(
                line.get("event_type") == "recording_job_recovery_manual_required"
                for line in audit_lines
            )
        )

    def test_ffmpeg_record_failed_http_404_marks_job_failed(self) -> None:
        self.agent_event_log.write_text(
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live.m3u8",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        job = state.recording_jobs[0]

        self.recorder_event_log.write_text(
            _recorder_event_line(
                "ffmpeg_record_failed",
                session_id=job.session_id,
                job_id=job.job_id,
                reason="[https @ 0xdeadbeef] Server returned 404 Not Found",
                created_at="2026-04-24T01:03:00Z",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.FAILED)
        self.assertEqual(state.recording_jobs[0].failure_category, "http_4xx_non_retryable")
        self.assertFalse(state.recording_jobs[0].recoverable)
        self.assertIsNotNone(state.recording_jobs[0].ended_at)
        self.assertIsNone(state.active_recording_job_id)

    def test_http_4xx_failure_then_next_live_started_recreates_active_job(self) -> None:
        self.agent_event_log.write_text(
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live.m3u8",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        first_job = state.recording_jobs[0]

        self.recorder_event_log.write_text(
            _recorder_event_line(
                "ffmpeg_fallback_placeholder",
                session_id=first_job.session_id,
                job_id=first_job.job_id,
                reason="Error opening input files: Server returned 403 Forbidden (access denied)",
                created_at="2026-04-24T01:01:00Z",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()

        with self.agent_event_log.open("a", encoding="utf-8") as handle:
            handle.write(
                _event_line(
                    "live_started",
                    state="live",
                    detected_at="2026-04-24T01:02:00Z",
                    source_type="browser_capture",
                    stream_url=None,
                )
                + "\n"
            )
        self.service.run_once()

        updated = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(updated.sessions), 1)
        self.assertEqual(len(updated.recording_jobs), 2)
        self.assertEqual(updated.recording_jobs[-1].status, RecordingJobStatus.QUEUED)
        self.assertEqual(updated.recording_jobs[-1].source_type.value, "browser_capture")
        self.assertEqual(updated.active_recording_job_id, updated.recording_jobs[-1].job_id)

    def test_http_4xx_failure_locks_session_to_browser_capture(self) -> None:
        self.agent_event_log.write_text(
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live.m3u8",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        first_job = state.recording_jobs[0]

        self.recorder_event_log.write_text(
            _recorder_event_line(
                "ffmpeg_fallback_placeholder",
                session_id=first_job.session_id,
                job_id=first_job.job_id,
                reason="Error opening input files: Server returned 403 Forbidden (access denied)",
                created_at="2026-04-24T01:01:00Z",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()

        with self.agent_event_log.open("a", encoding="utf-8") as handle:
            handle.write(
                _event_line(
                    "live_started",
                    state="live",
                    detected_at="2026-04-24T01:02:00Z",
                    source_type="direct_stream",
                    stream_url="https://cdn.example/live-new.m3u8",
                )
                + "\n"
            )
        self.service.run_once()

        updated = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(updated.sessions[0].source_type.value, "browser_capture")
        self.assertIsNone(updated.sessions[0].stream_url)
        self.assertEqual(updated.recording_jobs[-1].source_type.value, "browser_capture")
        self.assertIsNone(updated.recording_jobs[-1].stream_url)
        self.assertEqual(updated.active_recording_job_id, updated.recording_jobs[-1].job_id)

    def test_ffmpeg_record_failed_http_503_keeps_job_retrying(self) -> None:
        self.agent_event_log.write_text(
            _event_line(
                "live_started",
                state="live",
                detected_at="2026-04-24T01:00:00Z",
                source_type="direct_stream",
                stream_url="https://cdn.example/live.m3u8",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()
        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        job = state.recording_jobs[0]

        self.recorder_event_log.write_text(
            _recorder_event_line(
                "ffmpeg_record_failed",
                session_id=job.session_id,
                job_id=job.job_id,
                reason="[https @ 0xbeef] Server returned 503 Service Unavailable",
                created_at="2026-04-24T01:04:00Z",
            )
            + "\n",
            encoding="utf-8",
        )
        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(state.recording_jobs[0].status, RecordingJobStatus.RETRYING)
        self.assertEqual(state.recording_jobs[0].failure_category, "http_5xx_retryable")
        self.assertTrue(state.recording_jobs[0].recoverable)
        self.assertEqual(state.active_recording_job_id, job.job_id)


if __name__ == "__main__":
    unittest.main()
