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


def _live_started_line(
    *,
    platform: str,
    streamer_name: str,
    room_url: str,
    detected_at: str,
    stream_url: str | None = None,
    stream_headers: dict[str, str] | None = None,
    source_type: str | None = "direct_stream",
) -> str:
    payload = {
        "event_type": "live_started",
        "snapshot": {
            "state": "live",
            "streamer_name": streamer_name,
            "room_url": room_url,
            "platform": platform,
            "source_type": source_type,
            "stream_url": stream_url,
            "stream_headers": stream_headers or {},
            "reason": "test",
            "detected_at": detected_at,
        },
    }
    return json.dumps(payload, ensure_ascii=False)


class OrchestratorMultiPlatformTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.event_log = root / "windows-agent-events.jsonl"
        self.event_log.parent.mkdir(parents=True, exist_ok=True)
        self.event_log.touch()
        self.settings = Settings(
            douyin=DouyinSettings(),
            orchestrator=OrchestratorSettings(
                state_file=root / "orchestrator-state.json",
                agent_event_log_path=self.event_log,
                recorder_event_log_path=root / "recorder-events.jsonl",
                audit_log_path=root / "orchestrator-events.jsonl",
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _append_event(self, line: str) -> None:
        with self.event_log.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def test_bilibili_live_started_creates_session_with_platform_and_headers(self) -> None:
        self._append_event(
            _live_started_line(
                platform="bilibili",
                streamer_name="bili-streamer",
                room_url="https://live.bilibili.com/12345",
                detected_at="2026-05-06T01:00:00+00:00",
                stream_url="https://cn-pull.example.com/live/abc.flv?token=x",
                stream_headers={
                    "Referer": "https://live.bilibili.com",
                    "User-Agent": "Mozilla/5.0",
                },
            )
        )

        OrchestratorService(self.settings).run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.settings.orchestrator.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.sessions), 1)
        session = state.sessions[0]
        self.assertEqual(session.platform, "bilibili")
        self.assertEqual(session.stream_headers["Referer"], "https://live.bilibili.com")
        self.assertEqual(session.status, SessionStatus.LIVE)

        self.assertEqual(len(state.recording_jobs), 1)
        job = state.recording_jobs[0]
        self.assertEqual(job.platform, "bilibili")
        self.assertEqual(job.stream_headers["User-Agent"], "Mozilla/5.0")
        self.assertEqual(job.status, RecordingJobStatus.QUEUED)

    def test_cross_platform_live_started_runs_concurrently(self) -> None:
        # Multi-platform deployments (e.g. ARL_PLATFORMS=douyin,bilibili) must
        # let both platforms' live sessions coexist. A live_started on platform
        # B must NOT supersede platform A's active session — each platform has
        # its own active_session_id_by_platform[platform] entry.
        self._append_event(
            _live_started_line(
                platform="douyin",
                streamer_name="streamer-a",
                room_url="https://live.douyin.com/room",
                detected_at="2026-05-06T01:00:00+00:00",
                stream_url="https://example.invalid/douyin.m3u8",
            )
        )
        OrchestratorService(self.settings).run_once()

        self._append_event(
            _live_started_line(
                platform="bilibili",
                streamer_name="streamer-b",
                room_url="https://live.bilibili.com/12345",
                detected_at="2026-05-06T01:01:00+00:00",
                stream_url="https://cn-pull.example.com/live/abc.flv?token=x",
                stream_headers={
                    "Referer": "https://live.bilibili.com",
                    "User-Agent": "Mozilla/5.0",
                },
            )
        )
        OrchestratorService(self.settings).run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.settings.orchestrator.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.sessions), 2)
        douyin_session, bilibili_session = state.sessions

        # Both stay LIVE — no supersede across platforms.
        self.assertEqual(douyin_session.platform, "douyin")
        self.assertEqual(douyin_session.status, SessionStatus.LIVE)
        self.assertIsNone(douyin_session.stop_reason)

        self.assertEqual(bilibili_session.platform, "bilibili")
        self.assertEqual(bilibili_session.status, SessionStatus.LIVE)
        self.assertIsNone(bilibili_session.stop_reason)

        # Active id maps are keyed by platform + room so same-platform rooms
        # can coexist.
        self.assertEqual(
            state.active_session_id_by_platform["douyin:https://live.douyin.com/room"],
            douyin_session.session_id,
        )
        self.assertEqual(
            state.active_session_id_by_platform["bilibili:https://live.bilibili.com/12345"],
            bilibili_session.session_id,
        )

        # Both recording jobs queued, both reachable via active stream keys.
        self.assertEqual(len(state.recording_jobs), 2)
        for job in state.recording_jobs:
            session = next(
                session for session in state.sessions if session.session_id == job.session_id
            )
            self.assertEqual(
                state.active_recording_job_id_by_platform[
                    f"{job.platform}:{session.room_url}"
                ],
                job.job_id,
            )

    def test_same_platform_different_rooms_run_concurrently(self) -> None:
        # Production monitoring can track multiple rooms on the same platform.
        # A live_started for Douyin room B must not supersede room A.
        self._append_event(
            _live_started_line(
                platform="douyin",
                streamer_name="streamer-a",
                room_url="https://live.douyin.com/room-old",
                detected_at="2026-05-06T01:00:00+00:00",
                stream_url="https://example.invalid/douyin-old.m3u8",
            )
        )
        OrchestratorService(self.settings).run_once()

        self._append_event(
            _live_started_line(
                platform="douyin",
                streamer_name="streamer-a",
                room_url="https://live.douyin.com/room-new",
                detected_at="2026-05-06T01:01:00+00:00",
                stream_url="https://example.invalid/douyin-new.m3u8",
            )
        )
        OrchestratorService(self.settings).run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.settings.orchestrator.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.sessions), 2)
        first_session, second_session = state.sessions

        self.assertEqual(first_session.room_url, "https://live.douyin.com/room-old")
        self.assertEqual(first_session.status, SessionStatus.LIVE)
        self.assertIsNone(first_session.stop_reason)

        self.assertEqual(second_session.room_url, "https://live.douyin.com/room-new")
        self.assertEqual(second_session.status, SessionStatus.LIVE)

        self.assertEqual(
            state.active_session_id_by_platform["douyin:https://live.douyin.com/room-old"],
            first_session.session_id,
        )
        self.assertEqual(
            state.active_session_id_by_platform["douyin:https://live.douyin.com/room-new"],
            second_session.session_id,
        )

    def test_same_platform_duplicate_live_started_does_not_supersede(self) -> None:
        # A second live_started for the SAME (platform, room_url) is treated
        # as a duplicate (job/session enrichment), not a supersede.
        first_line = _live_started_line(
            platform="bilibili",
            streamer_name="bili-streamer",
            room_url="https://live.bilibili.com/12345",
            detected_at="2026-05-06T01:00:00+00:00",
            stream_url=None,
            source_type="browser_capture",
            stream_headers={"Referer": "https://live.bilibili.com"},
        )
        second_line = _live_started_line(
            platform="bilibili",
            streamer_name="bili-streamer",
            room_url="https://live.bilibili.com/12345",
            detected_at="2026-05-06T01:00:30+00:00",
            stream_url="https://cn-pull.example.com/live/abc.flv?token=x",
            stream_headers={
                "Referer": "https://live.bilibili.com",
                "User-Agent": "Mozilla/5.0",
            },
        )
        self._append_event(first_line)
        OrchestratorService(self.settings).run_once()
        self._append_event(second_line)
        OrchestratorService(self.settings).run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.settings.orchestrator.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.sessions), 1)
        session = state.sessions[0]
        self.assertEqual(session.status, SessionStatus.LIVE)
        self.assertEqual(
            session.stream_url, "https://cn-pull.example.com/live/abc.flv?token=x"
        )
        self.assertEqual(session.stream_headers["User-Agent"], "Mozilla/5.0")


if __name__ == "__main__":
    unittest.main()
