from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from arl.config import (
    DouyinSettings,
    ExportSettings,
    OrchestratorSettings,
    RecordingSettings,
    Settings,
    StorageSettings,
    SubtitleSettings,
)
from arl.orchestrator.models import (
    OrchestratorStateFile,
    RecordingJobRecord,
    RecordingJobStatus,
    SessionRecord,
    SessionStatus,
)
from arl.recorder.service import RecorderService
from arl.shared.contracts import SourceType


class RecorderFfmpegHeaderInjectionTests(unittest.TestCase):
    """Verify the ffmpeg command construction uses -user_agent + -headers when
    a recording job carries stream_headers (B 站 path), and is unchanged when
    the dict is empty (Douyin regression).
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.temp_root = root / "tmp"
        self.raw_root = root / "raw"
        self.orchestrator_state_path = self.temp_root / "orchestrator-state.json"

        self.settings = Settings(
            douyin=DouyinSettings(),
            storage=StorageSettings(
                raw_dir=self.raw_root,
                processed_dir=root / "processed",
                export_dir=root / "exports",
                temp_dir=self.temp_root,
            ),
            orchestrator=OrchestratorSettings(
                state_file=self.orchestrator_state_path,
                agent_event_log_path=self.temp_root / "windows-agent-events.jsonl",
                recorder_event_log_path=self.temp_root / "recorder-events.jsonl",
                audit_log_path=self.temp_root / "orchestrator-events.jsonl",
            ),
            recording=RecordingSettings(
                enable_ffmpeg=True,
                ffmpeg_max_retries=0,
                direct_stream_timeout_seconds=5,
                auto_retry_max_attempts=0,
            ),
            subtitles=SubtitleSettings(enabled=False),
            export=ExportSettings(),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_state(self, *, stream_headers: dict[str, str], platform: str) -> None:
        started_at = datetime(2026, 5, 6, 1, 0, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-h",
                    streamer_name="bili-streamer",
                    room_url="https://live.bilibili.com/12345",
                    platform=platform,
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://cn-pull.example.com/live/abc.flv?token=xyz",
                    stream_headers=stream_headers,
                    status=SessionStatus.LIVE,
                    started_at=started_at,
                ),
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-h",
                    session_id="session-h",
                    platform=platform,
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://cn-pull.example.com/live/abc.flv?token=xyz",
                    stream_headers=stream_headers,
                    status=RecordingJobStatus.QUEUED,
                    created_at=started_at,
                ),
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

    def _captured_command(self, mocked_run) -> list[str]:
        self.assertEqual(mocked_run.call_count, 1)
        args, _kwargs = mocked_run.call_args
        return list(args[0])

    def test_bilibili_stream_headers_become_user_agent_and_headers_flags(self) -> None:
        bilibili_headers = {
            "Referer": "https://live.bilibili.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124",
        }
        self._write_state(stream_headers=bilibili_headers, platform="bilibili")

        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["ffmpeg"], returncode=0),
        ) as mocked_run:
            RecorderService(self.settings).run()

        command = self._captured_command(mocked_run)

        # User-Agent rides on -user_agent (single value, not in -headers).
        self.assertIn("-user_agent", command)
        ua_index = command.index("-user_agent")
        self.assertEqual(command[ua_index + 1], bilibili_headers["User-Agent"])

        # Other headers (Referer here) ride on -headers as CRLF-joined lines.
        self.assertIn("-headers", command)
        headers_index = command.index("-headers")
        self.assertEqual(
            command[headers_index + 1],
            "Referer: https://live.bilibili.com",
        )

        # The header flags must precede -i so ffmpeg applies them to the input.
        i_index = command.index("-i")
        self.assertLess(ua_index, i_index)
        self.assertLess(headers_index, i_index)

    def test_empty_stream_headers_keep_command_unchanged_for_douyin(self) -> None:
        self._write_state(stream_headers={}, platform="douyin")

        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["ffmpeg"], returncode=0),
        ) as mocked_run:
            RecorderService(self.settings).run()

        command = self._captured_command(mocked_run)
        self.assertNotIn("-user_agent", command)
        self.assertNotIn("-headers", command)
        # Still has the input + copy + output structure.
        self.assertIn("-i", command)
        self.assertIn("-c", command)
        self.assertIn("copy", command)

    def test_user_agent_lookup_is_case_insensitive(self) -> None:
        # If a future probe writes lowercase keys, -user_agent should still
        # be split out cleanly.
        self._write_state(
            stream_headers={"referer": "https://live.bilibili.com", "user-agent": "ua-lc"},
            platform="bilibili",
        )

        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["ffmpeg"], returncode=0),
        ) as mocked_run:
            RecorderService(self.settings).run()

        command = self._captured_command(mocked_run)
        self.assertIn("-user_agent", command)
        ua_index = command.index("-user_agent")
        self.assertEqual(command[ua_index + 1], "ua-lc")
        self.assertIn("-headers", command)
        headers_index = command.index("-headers")
        self.assertEqual(command[headers_index + 1], "referer: https://live.bilibili.com")

    def test_recording_succeeds_writes_asset_with_bilibili_path(self) -> None:
        bilibili_headers = {
            "Referer": "https://live.bilibili.com",
            "User-Agent": "test-ua",
        }
        self._write_state(stream_headers=bilibili_headers, platform="bilibili")

        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["ffmpeg"], returncode=0),
        ):
            RecorderService(self.settings).run()

        assets_path = self.temp_root / "recording-assets.jsonl"
        payload = json.loads(assets_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(payload["session_id"], "session-h")
        self.assertEqual(payload["source_type"], SourceType.DIRECT_STREAM.value)


if __name__ == "__main__":
    unittest.main()
