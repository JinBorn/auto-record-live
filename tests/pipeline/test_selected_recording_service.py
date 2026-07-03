from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from arl.config import (
    BilibiliSettings,
    DouyinSettings,
    Settings,
    StorageSettings,
)
from arl.orchestrator.models import (
    OrchestratorStateFile,
    RecordingJobRecord,
    RecordingJobStatus,
    SessionRecord,
    SessionStatus,
)
from arl.selected_recording.service import SelectedRecordingService
from arl.shared.contracts import LiveState, SourceType
from arl.windows_agent.models import AgentSnapshot
from arl.windows_agent.platform_probe import PlatformProbe


_NOW = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)


class _StageCalls:
    calls: list[dict[str, object]] = []


def _stage_class(name: str):
    class _Stage:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def run(self, *args, **kwargs) -> None:
            _StageCalls.calls.append(
                {
                    "name": name,
                    "once": kwargs.get("once"),
                    "room_urls": [
                        platform.room_url for platform in self.settings.platforms
                    ],
                    "ffmpeg": self.settings.recording.enable_ffmpeg,
                    "max_concurrent_jobs": (
                        self.settings.recording.max_concurrent_jobs
                    ),
                    "agent_state_file": str(
                        self.settings.windows_agent.state_file
                    ),
                    "orchestrator_state_file": str(
                        self.settings.orchestrator.state_file
                    ),
                    "recorder_event_log": str(
                        self.settings.orchestrator.recorder_event_log_path
                    ),
                }
            )

    return _Stage


class _SnapshotProbe(PlatformProbe):
    platform_name: ClassVar[str] = "snapshot"

    def __init__(self, snapshot: AgentSnapshot) -> None:
        self.platform_name = snapshot.platform  # type: ignore[misc]
        self._snapshot = snapshot

    def detect(self) -> AgentSnapshot:
        return self._snapshot


def _snapshot(*, platform: str, room_url: str, live: bool) -> AgentSnapshot:
    return AgentSnapshot(
        state=LiveState.LIVE if live else LiveState.OFFLINE,
        streamer_name=f"{platform}-streamer",
        room_url=room_url,
        source_type=SourceType.DIRECT_STREAM if live else None,
        stream_url=f"https://cdn.example/{platform}.m3u8" if live else None,
        reason="api_live_with_stream_url" if live else "offline",
        detected_at=_NOW,
        platform=platform,
    )


class SelectedRecordingServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        _StageCalls.calls = []
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            storage=StorageSettings(temp_dir=root / "tmp"),
            platforms=[
                DouyinSettings(
                    room_url="https://live.douyin.com/111",
                    streamer_name="douyin-a",
                ),
                BilibiliSettings(
                    room_url="https://live.bilibili.com/222",
                    streamer_name="bili-b",
                ),
                DouyinSettings(
                    room_url="https://live.douyin.com/333",
                    streamer_name="douyin-c",
                ),
            ],
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_room_indices_select_only_requested_rooms_for_pipeline(self) -> None:
        with self._patch_stages():
            result = SelectedRecordingService(self.settings).run(
                room_indices=[2, 1, 2],
                max_concurrent_jobs=2,
            )

        self.assertEqual(
            [room.index for room in result.selected_rooms],
            [2, 1],
        )
        self.assertEqual(
            [call["name"] for call in _StageCalls.calls],
            ["agent", "orchestrator", "recorder", "orchestrator"],
        )
        for call in _StageCalls.calls:
            self.assertEqual(
                call["room_urls"],
                [
                    "https://live.bilibili.com/222",
                    "https://live.douyin.com/111",
                ],
            )
            self.assertTrue(call["ffmpeg"])
            self.assertEqual(call["max_concurrent_jobs"], 2)
            self.assertIn("selected-recordings", str(call["agent_state_file"]))
            self.assertIn("selected-recordings", str(call["orchestrator_state_file"]))
            self.assertIn("selected-recordings", str(call["recorder_event_log"]))
        self.assertIsNotNone(result.state_dir)

    def test_all_live_selects_only_currently_live_rooms(self) -> None:
        probes = [
            _SnapshotProbe(
                _snapshot(
                    platform="douyin",
                    room_url="https://live.douyin.com/111",
                    live=False,
                )
            ),
            _SnapshotProbe(
                _snapshot(
                    platform="bilibili",
                    room_url="https://live.bilibili.com/222",
                    live=True,
                )
            ),
            _SnapshotProbe(
                _snapshot(
                    platform="douyin",
                    room_url="https://live.douyin.com/333",
                    live=False,
                )
            ),
        ]

        with self._patch_stages(), patch(
            "arl.selected_recording.service.build_probes",
            return_value=probes,
        ):
            result = SelectedRecordingService(self.settings).run(all_live=True)

        self.assertEqual([room.index for room in result.selected_rooms], [2])
        self.assertEqual(
            _StageCalls.calls[0]["room_urls"],
            ["https://live.bilibili.com/222"],
        )

    def test_invalid_room_index_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid range is 1..3"):
            SelectedRecordingService(self.settings).run(room_indices=[4])

    def test_continues_with_new_cycle_when_successful_job_ends_while_live(self) -> None:
        live_after_success = OrchestratorStateFile(
            active_session_id_by_platform={
                "bilibili:https://live.bilibili.com/222": "session-live"
            },
            sessions=[
                SessionRecord(
                    session_id="session-live",
                    streamer_name="bili-b",
                    room_url="https://live.bilibili.com/222",
                    platform="bilibili",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://cdn.example/live.m3u8",
                    status=SessionStatus.LIVE,
                    started_at=_NOW,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-stopped",
                    session_id="session-live",
                    platform="bilibili",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://cdn.example/live.m3u8",
                    status=RecordingJobStatus.STOPPED,
                    created_at=_NOW,
                    ended_at=_NOW,
                )
            ],
        )
        offline_after_next_cycle = OrchestratorStateFile()

        with self._patch_stages(), patch(
            "arl.selected_recording.service.load_orchestrator_state",
            side_effect=[live_after_success, offline_after_next_cycle],
        ), patch("arl.selected_recording.service.time.sleep") as sleep:
            result = SelectedRecordingService(self.settings).run(room_indices=[2])

        self.assertEqual(
            [call["name"] for call in _StageCalls.calls],
            [
                "agent",
                "orchestrator",
                "recorder",
                "orchestrator",
                "agent",
                "orchestrator",
                "recorder",
                "orchestrator",
            ],
        )
        sleep.assert_called_once_with(
            self.settings.windows_agent.poll_interval_seconds
        )
        self.assertEqual(result.sessions, 1)
        self.assertEqual(result.recording_jobs_by_status, {"stopped": 1})
        self.assertEqual(len(result.state_dirs or []), 2)

    def test_does_not_continue_after_failed_live_cycle(self) -> None:
        failed_live_state = OrchestratorStateFile(
            active_session_id_by_platform={
                "bilibili:https://live.bilibili.com/222": "session-live"
            },
            sessions=[
                SessionRecord(
                    session_id="session-live",
                    streamer_name="bili-b",
                    room_url="https://live.bilibili.com/222",
                    platform="bilibili",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://cdn.example/live.m3u8",
                    status=SessionStatus.LIVE,
                    started_at=_NOW,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-failed",
                    session_id="session-live",
                    platform="bilibili",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://cdn.example/live.m3u8",
                    status=RecordingJobStatus.FAILED,
                    created_at=_NOW,
                    ended_at=_NOW,
                    stop_reason="http_403_forbidden",
                )
            ],
        )

        with self._patch_stages(), patch(
            "arl.selected_recording.service.load_orchestrator_state",
            return_value=failed_live_state,
        ), patch("arl.selected_recording.service.time.sleep") as sleep:
            result = SelectedRecordingService(self.settings).run(room_indices=[2])

        self.assertEqual(
            [call["name"] for call in _StageCalls.calls],
            ["agent", "orchestrator", "recorder", "orchestrator"],
        )
        sleep.assert_not_called()
        self.assertEqual(result.recording_jobs_by_status, {"failed": 1})

    def _patch_stages(self):
        return patch.multiple(
            "arl.selected_recording.service",
            WindowsAgentService=_stage_class("agent"),
            OrchestratorService=_stage_class("orchestrator"),
            RecorderService=_stage_class("recorder"),
        )


if __name__ == "__main__":
    unittest.main()
