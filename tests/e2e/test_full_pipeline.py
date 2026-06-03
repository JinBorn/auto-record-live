from __future__ import annotations

import tempfile
import unittest
from subprocess import CalledProcessError
from pathlib import Path
from unittest.mock import patch

from arl.copywriter.models import CopywriterStateFile
from arl.copywriter.service import CopywriterService
from arl.exporter.service import ExporterService
from arl.exporter.models import ExporterStateFile
from arl.orchestrator.models import OrchestratorStateFile
from arl.orchestrator.service import OrchestratorService
from arl.recorder.models import RecorderStateFile
from arl.recorder.service import RecorderService
from arl.segmenter.service import SegmenterService
from arl.subtitles.service import SubtitleService
from arl.windows_agent.platform_probe import CookieState
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

        CopywriterService(self.settings).run()
        copies = jsonl_payloads(self.settings.storage.temp_dir / "copy-assets.jsonl")
        self.assertEqual(len(copies), 1)
        self.assertTrue(Path(copies[0]["path"]).exists())

        exporter_state = ExporterStateFile.model_validate_json(
            (self.settings.storage.temp_dir / "exporter-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(exporter_state.processed_match_keys, [f"{exports[0]['session_id']}:1"])
        copywriter_state = CopywriterStateFile.model_validate_json(
            (self.settings.storage.temp_dir / "copywriter-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(copywriter_state.processed_match_keys, [f"{exports[0]['session_id']}:1"])


class CookieExpiredProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = build_sandboxed_settings(self.root, platforms=("bilibili",))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cookie_expired_probe_emits_dual_audit(self) -> None:
        agent = WindowsAgentService(self.settings)
        agent.probes = [
            FakeProbe(
                "bilibili",
                [
                    make_live_snapshot(
                        "bilibili",
                        reason="api_error:code=-101",
                    )
                ],
                cookie_states=[CookieState.EXPIRED],
            )
        ]

        agent.run_once()
        OrchestratorService(self.settings).run(once=True)

        agent_rows = jsonl_payloads(self.settings.windows_agent.event_log_path)
        self.assertEqual(
            [row["event_type"] for row in agent_rows],
            ["live_started", "cookie_expired_for_bilibili"],
        )

        audit_rows = jsonl_payloads(self.settings.orchestrator.audit_log_path)
        audit_types = [row["event_type"] for row in audit_rows]
        self.assertIn("session_started", audit_types)
        self.assertIn("cookie_expired_for_bilibili", audit_types)
        self.assertNotIn("ignored_unknown_event_type", audit_types)


class RecorderTransientRetryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = build_sandboxed_settings(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_recorder_transient_failure_schedules_retry(self) -> None:
        agent = WindowsAgentService(self.settings)
        agent.probes = [FakeProbe("douyin", [make_live_snapshot("douyin")])]
        agent.run_once()
        OrchestratorService(self.settings).run(once=True)

        failure = CalledProcessError(
            1,
            ["ffmpeg"],
            stderr="Connection reset by peer",
        )
        with patch("arl.recorder.service.shutil.which", return_value="ffmpeg"), patch(
            "arl.shared.ffmpeg_runner.subprocess.run",
            side_effect=failure,
        ):
            RecorderService(self.settings).run()

        recorder_events = jsonl_payloads(self.settings.orchestrator.recorder_event_log_path)
        failed_rows = [
            row for row in recorder_events if row["event_type"] == "ffmpeg_record_failed"
        ]
        retry_rows = [
            row for row in recorder_events if row["event_type"] == "recording_retry_scheduled"
        ]
        self.assertEqual(len(failed_rows), 1)
        self.assertEqual(failed_rows[0]["decision"], "attempt_failed_yield_to_next_probe")
        self.assertTrue(failed_rows[0]["is_retryable"])
        self.assertEqual(failed_rows[0]["reason_code"], "network_timeout")
        self.assertEqual(len(retry_rows), 1)
        self.assertEqual(retry_rows[0]["attempt"], 1)

        recorder_state = RecorderStateFile.model_validate_json(
            (self.settings.storage.temp_dir / "recorder-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn(failed_rows[0]["job_id"], recorder_state.next_eligible_at_by_job_id)
        self.assertEqual(jsonl_payloads(self.settings.storage.temp_dir / "recording-assets.jsonl"), [])


class ExporterFfmpegFailureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = build_sandboxed_settings(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_exporter_ffmpeg_failure_falls_back_to_placeholder(self) -> None:
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
        orchestrator = OrchestratorService(self.settings)
        orchestrator.run(once=True)
        agent.run_once()
        orchestrator.run(once=True)

        with patch("arl.recorder.service.shutil.which", return_value="ffmpeg"), patch(
            "arl.shared.ffmpeg_runner.subprocess.run",
            side_effect=fake_successful_subprocess,
        ):
            RecorderService(self.settings).run()
        SegmenterService(self.settings).run()
        SubtitleService(self.settings).run()

        failure = CalledProcessError(
            1,
            ["ffmpeg"],
            stderr="exit_status:1",
        )
        with patch("arl.exporter.service.shutil.which", return_value="ffmpeg"), patch(
            "arl.shared.ffmpeg_runner.subprocess.run",
            side_effect=failure,
        ), patch("arl.exporter.service.time.sleep", return_value=None):
            ExporterService(self.settings).run()

        exporter_events = jsonl_payloads(self.settings.storage.temp_dir / "exporter-events.jsonl")
        failed_rows = [
            row for row in exporter_events if row["event_type"] == "ffmpeg_export_failed"
        ]
        fallback_rows = [
            row
            for row in exporter_events
            if row["event_type"] == "ffmpeg_export_fallback_placeholder"
        ]
        self.assertEqual(len(failed_rows), 2)
        self.assertEqual(len(fallback_rows), 1)
        self.assertEqual(fallback_rows[0]["decision"], "fallback_placeholder")
        self.assertEqual(fallback_rows[0]["reason_code"], "ffmpeg_process_error")

        exports = jsonl_payloads(self.settings.storage.temp_dir / "export-assets.jsonl")
        self.assertEqual(len(exports), 1)
        self.assertTrue(exports[0]["path"].endswith(".txt"))
        self.assertTrue(Path(exports[0]["path"]).exists())


class DualPlatformConcurrencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = build_sandboxed_settings(
            self.root,
            platforms=("douyin", "bilibili"),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dual_platform_concurrent_isolation(self) -> None:
        agent = WindowsAgentService(self.settings)
        agent.probes = [
            FakeProbe("douyin", [make_live_snapshot("douyin")]),
            FakeProbe("bilibili", [make_live_snapshot("bilibili")]),
        ]

        agent.run_once()
        agent_rows = jsonl_payloads(self.settings.windows_agent.event_log_path)
        self.assertEqual([row["event_type"] for row in agent_rows], ["live_started", "live_started"])
        self.assertEqual(
            [row["snapshot"]["platform"] for row in agent_rows],
            ["douyin", "bilibili"],
        )

        OrchestratorService(self.settings).run(once=True)
        state = OrchestratorStateFile.model_validate_json(
            self.settings.orchestrator.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.sessions), 2)
        self.assertEqual(len(state.recording_jobs), 2)
        self.assertEqual(
            {session.platform for session in state.sessions},
            {"douyin", "bilibili"},
        )
        self.assertEqual(
            {job.platform for job in state.recording_jobs},
            {"douyin", "bilibili"},
        )
        self.assertEqual(len({session.session_id for session in state.sessions}), 2)


if __name__ == "__main__":
    unittest.main()
