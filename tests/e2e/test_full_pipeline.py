from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arl.exporter.service import ExporterService
from arl.exporter.models import ExporterStateFile
from arl.orchestrator.models import OrchestratorStateFile
from arl.orchestrator.service import OrchestratorService
from arl.recorder.service import RecorderService
from arl.segmenter.service import SegmenterService
from arl.subtitles.service import SubtitleService
from arl.windows_agent.service import WindowsAgentService

from tests.e2e._helpers import (
    FakeProbe,
    build_sandboxed_settings,
    fake_successful_subprocess,
    jsonl_payloads,
    make_live_snapshot,
    make_offline_snapshot,
)


class GoldenPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = build_sandboxed_settings(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_golden_path_single_platform(self) -> None:
        agent = WindowsAgentService(self.settings)
        agent.probes = [
            FakeProbe(
                "douyin",
                [
                    make_live_snapshot("douyin"),
                    make_offline_snapshot("douyin"),
                ],
            )
        ]

        agent.run_once()
        agent_rows = jsonl_payloads(self.settings.windows_agent.event_log_path)
        self.assertEqual([row["event_type"] for row in agent_rows], ["live_started"])

        orchestrator = OrchestratorService(self.settings)
        orchestrator.run(once=True)
        state = OrchestratorStateFile.model_validate_json(
            self.settings.orchestrator.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.sessions), 1)
        self.assertEqual(len(state.recording_jobs), 1)

        agent.run_once()
        agent_rows = jsonl_payloads(self.settings.windows_agent.event_log_path)
        self.assertEqual(
            [row["event_type"] for row in agent_rows],
            ["live_started", "live_stopped"],
        )
        orchestrator.run(once=True)

        with patch("arl.recorder.service.shutil.which", return_value="ffmpeg"), patch(
            "arl.shared.ffmpeg_runner.subprocess.run",
            side_effect=fake_successful_subprocess,
        ):
            RecorderService(self.settings).run()
        recording_assets = jsonl_payloads(self.settings.storage.temp_dir / "recording-assets.jsonl")
        self.assertEqual(len(recording_assets), 1)
        self.assertTrue(Path(recording_assets[0]["path"]).exists())
        recorder_events = jsonl_payloads(self.settings.orchestrator.recorder_event_log_path)
        self.assertIn("ffmpeg_record_succeeded", [row["event_type"] for row in recorder_events])

        SegmenterService(self.settings).run()
        boundaries = jsonl_payloads(self.settings.storage.temp_dir / "match-boundaries.jsonl")
        self.assertEqual(len(boundaries), 1)

        SubtitleService(self.settings).run()
        subtitles = jsonl_payloads(self.settings.storage.temp_dir / "subtitle-assets.jsonl")
        self.assertEqual(len(subtitles), 1)
        self.assertTrue(Path(subtitles[0]["path"]).exists())

        with patch("arl.exporter.service.shutil.which", return_value="ffmpeg"), patch(
            "arl.shared.ffmpeg_runner.subprocess.run",
            side_effect=fake_successful_subprocess,
        ):
            ExporterService(self.settings).run()
        exports = jsonl_payloads(self.settings.storage.temp_dir / "export-assets.jsonl")
        self.assertEqual(len(exports), 1)
        self.assertTrue(Path(exports[0]["path"]).exists())
        exporter_events = jsonl_payloads(self.settings.storage.temp_dir / "exporter-events.jsonl")
        self.assertIn("ffmpeg_export_succeeded", [row["event_type"] for row in exporter_events])

        exporter_state = ExporterStateFile.model_validate_json(
            (self.settings.storage.temp_dir / "exporter-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(exporter_state.processed_match_keys, [f"{exports[0]['session_id']}:1"])


if __name__ == "__main__":
    unittest.main()
