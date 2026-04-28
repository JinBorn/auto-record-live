from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import (
    DouyinSettings,
    ExportSettings,
    OrchestratorSettings,
    RecordingSettings,
    Settings,
    StorageSettings,
    SubtitleSettings,
)
from arl.exporter.service import ExporterService
from arl.orchestrator.models import (
    OrchestratorStateFile,
    RecordingJobRecord,
    RecordingJobStatus,
    SessionRecord,
    SessionStatus,
)
from arl.recorder.service import RecorderService
from arl.segmenter.service import SegmenterService
from arl.shared.contracts import SourceType
from arl.subtitles.service import SubtitleService


def _count_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


class PostLivePipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.temp_root = root / "tmp"
        self.raw_root = root / "raw"
        self.processed_root = root / "processed"
        self.export_root = root / "exports"
        self.orchestrator_state_path = self.temp_root / "orchestrator-state.json"

        self.settings = Settings(
            douyin=DouyinSettings(event_log_path=self.temp_root / "windows-agent-events.jsonl"),
            storage=StorageSettings(
                raw_dir=self.raw_root,
                processed_dir=self.processed_root,
                export_dir=self.export_root,
                temp_dir=self.temp_root,
            ),
            orchestrator=OrchestratorSettings(
                state_file=self.orchestrator_state_path,
                agent_event_log_path=self.temp_root / "windows-agent-events.jsonl",
                recorder_event_log_path=self.temp_root / "recorder-events.jsonl",
                audit_log_path=self.temp_root / "orchestrator-events.jsonl",
            ),
            recording=RecordingSettings(enable_ffmpeg=True),
            subtitles=SubtitleSettings(enabled=True),
            export=ExportSettings(enable_ffmpeg=True),
        )

        started_at = datetime(2026, 4, 25, 1, 0, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 1, 35, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-20260425010000-abcd1234",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                    stop_reason="test",
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="recording-20260425010000-beef0001",
                    session_id="session-20260425010000-abcd1234",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                    stop_reason="test",
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_post_live_pipeline_outputs_and_idempotency(self) -> None:
        RecorderService(self.settings).run()
        SegmenterService(self.settings).run()
        SubtitleService(self.settings).run()
        ExporterService(self.settings).run()

        recording_assets_path = self.temp_root / "recording-assets.jsonl"
        boundaries_path = self.temp_root / "match-boundaries.jsonl"
        subtitles_path = self.temp_root / "subtitle-assets.jsonl"
        exports_path = self.temp_root / "export-assets.jsonl"

        self.assertEqual(_count_jsonl_lines(recording_assets_path), 1)
        self.assertEqual(_count_jsonl_lines(boundaries_path), 1)
        self.assertEqual(_count_jsonl_lines(subtitles_path), 1)
        self.assertEqual(_count_jsonl_lines(exports_path), 1)

        subtitle_line = subtitles_path.read_text(encoding="utf-8").splitlines()[0]
        subtitle_payload = json.loads(subtitle_line)
        self.assertTrue(Path(subtitle_payload["path"]).exists())

        export_line = exports_path.read_text(encoding="utf-8").splitlines()[0]
        export_payload = json.loads(export_line)
        self.assertTrue(Path(export_payload["path"]).exists())

        RecorderService(self.settings).run()
        SegmenterService(self.settings).run()
        SubtitleService(self.settings).run()
        ExporterService(self.settings).run()

        self.assertEqual(_count_jsonl_lines(recording_assets_path), 1)
        self.assertEqual(_count_jsonl_lines(boundaries_path), 1)
        self.assertEqual(_count_jsonl_lines(subtitles_path), 1)
        self.assertEqual(_count_jsonl_lines(exports_path), 1)


if __name__ == "__main__":
    unittest.main()
