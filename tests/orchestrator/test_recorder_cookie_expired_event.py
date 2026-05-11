from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import DouyinSettings, OrchestratorSettings, Settings
from arl.orchestrator.models import (
    OrchestratorStateFile,
    RecordingJobRecord,
    RecordingJobStatus,
    SessionRecord,
    SessionStatus,
)
from arl.orchestrator.service import OrchestratorService
from arl.shared.contracts import SourceType


class OrchestratorRecorderCookieExpiredEventTest(unittest.TestCase):
    """Orchestrator routes recorder-side cookie_expired_for_<platform> to
    audit-only and never mutates session/job state. The watermark must not
    advance, otherwise subsequent ffmpeg_record_failed events for the same job
    would be skipped as stale."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.temp_root = root / "tmp"
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.temp_root / "orchestrator-state.json"
        self.settings = Settings(
            douyin=DouyinSettings(),
            orchestrator=OrchestratorSettings(
                state_file=self.state_path,
                agent_event_log_path=self.temp_root / "windows-agent-events.jsonl",
                recorder_event_log_path=self.temp_root / "recorder-events.jsonl",
                audit_log_path=self.temp_root / "orchestrator-events.jsonl",
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_state(self, *, platform: str = "douyin") -> None:
        started_at = datetime(2026, 5, 12, 2, 0, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-rec",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/x",
                    platform=platform,
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.LIVE,
                    started_at=started_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-rec",
                    session_id="session-rec",
                    platform=platform,
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.QUEUED,
                    created_at=started_at,
                )
            ],
            active_session_id_by_platform={platform: "session-rec"},
            active_recording_job_id_by_platform={platform: "job-rec"},
        )
        self.state_path.write_text(
            state.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )

    def _orch_audits(self) -> list[dict]:
        path = self.temp_root / "orchestrator-events.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _write_recorder_events(self, payloads: list[dict]) -> None:
        path = self.temp_root / "recorder-events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(item) for item in payloads) + "\n",
            encoding="utf-8",
        )

    # ----- routing -----

    def test_cookie_expired_recorder_event_writes_audit_only(self) -> None:
        self._seed_state(platform="douyin")
        created_at = datetime(2026, 5, 12, 2, 5, tzinfo=timezone.utc)
        self._write_recorder_events(
            [
                {
                    "event_type": "cookie_expired_for_douyin",
                    "session_id": "session-rec",
                    "job_id": "job-rec",
                    "source_type": "direct_stream",
                    "reason": "HTTP 403 Forbidden",
                    "created_at": created_at.isoformat(),
                }
            ]
        )

        OrchestratorService(self.settings).run_once()

        audits = self._orch_audits()
        event_types = [item["event_type"] for item in audits]
        self.assertIn("cookie_expired_for_douyin", event_types)
        cookie_audit = next(
            item for item in audits if item["event_type"] == "cookie_expired_for_douyin"
        )
        self.assertEqual(cookie_audit["session_id"], "session-rec")
        self.assertEqual(cookie_audit["job_id"], "job-rec")
        self.assertIn("platform=douyin", cookie_audit["message"])
        self.assertIn("403", cookie_audit["message"])

        # Job state must NOT have transitioned.
        result = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(result["recording_jobs"][0]["status"], "queued")
        self.assertIsNone(result["recording_jobs"][0].get("failure_category"))

        # Must NOT be treated as an unknown event (no recorder_event_ignored).
        self.assertNotIn("recorder_event_ignored", event_types)

    def test_cookie_expired_does_not_advance_watermark(self) -> None:
        # Watermark advance would block a later ffmpeg_record_failed at the
        # same created_at as stale. The cookie_expired branch must early-return
        # before _mark_recorder_event_applied.
        self._seed_state(platform="douyin")
        at = datetime(2026, 5, 12, 2, 10, tzinfo=timezone.utc).isoformat()
        self._write_recorder_events(
            [
                {
                    "event_type": "cookie_expired_for_douyin",
                    "session_id": "session-rec",
                    "job_id": "job-rec",
                    "source_type": "direct_stream",
                    "reason": "HTTP 403 Forbidden",
                    "created_at": at,
                },
                {
                    "event_type": "ffmpeg_record_failed",
                    "session_id": "session-rec",
                    "job_id": "job-rec",
                    "source_type": "direct_stream",
                    "decision": "attempt_failed",
                    "failure_category": "http_4xx_non_retryable",
                    "is_retryable": False,
                    "reason_code": "http_403_forbidden",
                    "reason_detail": "HTTP 403 Forbidden",
                    "reason": "HTTP 403 Forbidden",
                    "attempt": 1,
                    "max_attempts": 1,
                    "created_at": at,
                },
            ]
        )

        OrchestratorService(self.settings).run_once()

        audits = self._orch_audits()
        event_types = [item["event_type"] for item in audits]
        self.assertIn("cookie_expired_for_douyin", event_types)
        # ffmpeg_record_failed must still drive job to a terminal state, not be
        # skipped as stale (which would emit recorder_event_stale_ignored).
        self.assertIn("recording_job_attempt_failed_terminal", event_types)
        self.assertNotIn("recorder_event_stale_ignored", event_types)

        result = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(result["recording_jobs"][0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
