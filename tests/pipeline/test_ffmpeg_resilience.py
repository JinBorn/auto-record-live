from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from arl.config import (
    BilibiliSettings,
    DouyinSettings,
    ExportSettings,
    OrchestratorSettings,
    RecordingSettings,
    Settings,
    StorageSettings,
    SubtitleSettings,
)
from arl.exporter.models import ExporterStateFile
from arl.exporter.service import ExporterService
from arl.orchestrator.models import (
    OrchestratorStateFile,
    RecordingJobRecord,
    RecordingJobStatus,
    SessionRecord,
    SessionStatus,
)
from arl.orchestrator.service import OrchestratorService
from arl.recorder.models import RecorderStateFile
from arl.recovery.service import RecoveryService
from arl.recorder.service import (
    RecordingBuildOutcome,
    RecordingWorkResult,
    RecorderService,
)
from arl.shared.contracts import (
    ExportAsset,
    HighlightClipWindow,
    HighlightPlanAsset,
    LiveState,
    MatchBoundary,
    RecordingAsset,
    SourceType,
    SubtitleAsset,
)
from arl.shared.jsonl_store import append_model
from arl.windows_agent.models import AgentSnapshot
from arl.windows_agent.platform_probe import CookieState


class FfmpegResilienceTest(unittest.TestCase):
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
            recording=RecordingSettings(
                enable_ffmpeg=True,
                ffmpeg_max_retries=2,
                direct_stream_timeout_seconds=5,
                auto_retry_max_attempts=0,
            ),
            subtitles=SubtitleSettings(enabled=True),
            export=ExportSettings(
                enable_ffmpeg=True,
                ffmpeg_max_retries=1,
                ffmpeg_timeout_seconds=10,
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_recorder_retries_ffmpeg_then_falls_back(self) -> None:
        started_at = datetime(2026, 4, 25, 1, 0, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 1, 10, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-r",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-r",
                    session_id="session-r",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

        # auto_retry_max_attempts=0 (from setUp) → transient yield short-circuits
        # straight to fallback_placeholder without scheduling a cross-run retry.
        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["ffmpeg"]),
        ) as mocked_run:
            RecorderService(self.settings).run()

        self.assertEqual(mocked_run.call_count, 1)
        assets_path = self.temp_root / "recording-assets.jsonl"
        payload = json.loads(assets_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertTrue(payload["path"].endswith("recording-source.txt"))
        self.assertTrue(Path(payload["path"]).exists())
        audit_path = self.temp_root / "recorder-events.jsonl"
        audit_payloads = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(
            [item["event_type"] for item in audit_payloads],
            [
                "ffmpeg_record_failed",
                "ffmpeg_fallback_placeholder",
            ],
        )
        self.assertEqual(audit_payloads[0]["attempt"], 1)
        self.assertEqual(audit_payloads[0]["max_attempts"], 3)
        self.assertEqual(
            audit_payloads[0]["decision"], "attempt_failed_yield_to_next_probe"
        )

    def test_recorder_audit_writes_to_orchestrator_configured_path(self) -> None:
        started_at = datetime(2026, 4, 25, 1, 12, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 1, 18, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-custom-log",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-custom-log",
                    session_id="session-custom-log",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        custom_recorder_event_log = self.temp_root / "events" / "recorder-events-custom.jsonl"
        self.settings.orchestrator.recorder_event_log_path = custom_recorder_event_log

        with patch("arl.recorder.service.shutil.which", return_value=None):
            RecorderService(self.settings).run()

        self.assertTrue(custom_recorder_event_log.exists())
        payloads = [
            json.loads(line)
            for line in custom_recorder_event_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(payloads[0]["event_type"], "ffmpeg_skipped")
        self.assertEqual(payloads[0]["job_id"], "job-custom-log")
        self.assertEqual(payloads[0]["reason"], "missing_binary")
        self.assertFalse((self.temp_root / "recorder-events.jsonl").exists())

    def test_recorder_schedules_retry_for_retryable_failures_until_exhausted(self) -> None:
        started_at = datetime(2026, 4, 25, 1, 20, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 1, 30, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-retry",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-retry",
                    session_id="session-retry",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        self.settings.recording.auto_retry_max_attempts = 2

        # Yield-on-transient means each run produces one ffmpeg call. To exercise
        # the cross-run retry-exhaustion path without waiting on the 1/5/15/60s
        # backoff, pin the schedule to zero so the next run is immediately
        # eligible.
        ffmpeg_error = subprocess.CalledProcessError(
            1,
            ["ffmpeg"],
            stderr="exit_status:1",
        )
        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.subprocess.run",
            side_effect=ffmpeg_error,
        ) as mocked_run, patch.object(
            RecorderService,
            "_next_eligible_after_yield",
            staticmethod(lambda attempt: timedelta(0)),
        ):
            RecorderService(self.settings).run()
            RecorderService(self.settings).run()
            RecorderService(self.settings).run()

        # One ffmpeg call per run with new yield-on-transient semantics.
        self.assertEqual(mocked_run.call_count, 3)
        assets_path = self.temp_root / "recording-assets.jsonl"
        payload = json.loads(assets_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertTrue(payload["path"].endswith("recording-source.txt"))
        self.assertTrue(Path(payload["path"]).exists())

        state_payload = json.loads((self.temp_root / "recorder-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state_payload["processed_job_ids"], ["job-retry"])
        self.assertEqual(state_payload["retry_attempts_by_job_id"], {})

        audit_payloads = [
            json.loads(line)
            for line in (self.temp_root / "recorder-events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(
            [item["event_type"] for item in audit_payloads if item["event_type"] == "recording_retry_scheduled"],
            ["recording_retry_scheduled", "recording_retry_scheduled"],
        )
        self.assertEqual(
            [item["event_type"] for item in audit_payloads if item["event_type"] == "recording_retry_exhausted"],
            ["recording_retry_exhausted"],
        )

    def test_recorder_does_not_schedule_cross_run_retry_for_http_4xx_failures(self) -> None:
        started_at = datetime(2026, 4, 25, 1, 35, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 1, 40, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-no-retry-4xx",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-no-retry-4xx",
                    session_id="session-no-retry-4xx",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        self.settings.recording.auto_retry_max_attempts = 3

        ffmpeg_error = subprocess.CalledProcessError(
            1,
            ["ffmpeg"],
            stderr="[https @ 0xdeadbeef] Server returned 404 Not Found",
        )
        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.subprocess.run",
            side_effect=ffmpeg_error,
        ) as mocked_run:
            RecorderService(self.settings).run()

        self.assertEqual(mocked_run.call_count, 1)

        assets_path = self.temp_root / "recording-assets.jsonl"
        payload = json.loads(assets_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertTrue(payload["path"].endswith("recording-source.txt"))
        self.assertTrue(Path(payload["path"]).exists())

        recorder_state_payload = json.loads(
            (self.temp_root / "recorder-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(recorder_state_payload["processed_job_ids"], ["job-no-retry-4xx"])
        self.assertEqual(recorder_state_payload["retry_attempts_by_job_id"], {})

        audit_payloads = [
            json.loads(line)
            for line in (self.temp_root / "recorder-events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        event_types = [item["event_type"] for item in audit_payloads]
        self.assertEqual(
            event_types,
            [
                "ffmpeg_record_failed",
                "ffmpeg_fallback_placeholder",
            ],
        )
        self.assertFalse(any(event_type == "recording_retry_scheduled" for event_type in event_types))
        self.assertFalse(any(event_type == "recording_retry_exhausted" for event_type in event_types))

    def test_recorder_keeps_retry_path_for_http_503_failures(self) -> None:
        started_at = datetime(2026, 4, 25, 1, 42, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 1, 47, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-retry-503",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-retry-503",
                    session_id="session-retry-503",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        self.settings.recording.auto_retry_max_attempts = 2

        ffmpeg_error = subprocess.CalledProcessError(
            1,
            ["ffmpeg"],
            stderr="[https @ 0xbeefcafe] Server returned 503 Service Unavailable",
        )
        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.subprocess.run",
            side_effect=ffmpeg_error,
        ) as mocked_run:
            RecorderService(self.settings).run()

        # Yield-on-transient: a single ffmpeg attempt then schedule a cross-run
        # retry rather than burning multiple in-run attempts on the same URL.
        self.assertEqual(mocked_run.call_count, 1)
        self.assertFalse((self.temp_root / "recording-assets.jsonl").exists())

        recorder_state_payload = json.loads(
            (self.temp_root / "recorder-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(recorder_state_payload["processed_job_ids"], [])
        self.assertEqual(recorder_state_payload["retry_attempts_by_job_id"], {"job-retry-503": 1})
        self.assertIn("job-retry-503", recorder_state_payload["next_eligible_at_by_job_id"])

        audit_payloads = [
            json.loads(line)
            for line in (self.temp_root / "recorder-events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        event_types = [item["event_type"] for item in audit_payloads]
        self.assertEqual(
            event_types,
            [
                "ffmpeg_record_failed",
                "recording_retry_scheduled",
            ],
        )
        self.assertEqual(
            audit_payloads[0]["decision"], "attempt_failed_yield_to_next_probe"
        )
        self.assertFalse(any(event_type == "ffmpeg_fallback_placeholder" for event_type in event_types))

    def test_recorder_uses_browser_capture_ffmpeg_when_input_configured(self) -> None:
        started_at = datetime(2026, 4, 25, 2, 0, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 2, 5, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-bc",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-bc",
                    session_id="session-bc",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

        self.settings.recording.browser_capture_input = ":99.0"
        self.settings.recording.browser_capture_timeout_seconds = 3

        def _fake_ffmpeg(*args, **kwargs):
            command = args[0]
            Path(command[-1]).write_text("fake video bytes", encoding="utf-8")
            return subprocess.CompletedProcess(args=command, returncode=0)

        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.subprocess.run",
            side_effect=_fake_ffmpeg,
        ) as mocked_run:
            RecorderService(self.settings).run()

        self.assertEqual(mocked_run.call_count, 1)
        assets_path = self.temp_root / "recording-assets.jsonl"
        payload = json.loads(assets_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertTrue(payload["path"].endswith("recording-source.mp4"))
        self.assertTrue(Path(payload["path"]).exists())
        audit_path = self.temp_root / "recorder-events.jsonl"
        audit_payload = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(audit_payload["event_type"], "ffmpeg_record_succeeded")
        self.assertEqual(audit_payload["source_type"], "browser_capture")

    def test_recorder_skips_failed_job_and_marks_manual_recovery_once(self) -> None:
        started_at = datetime(2026, 4, 25, 2, 20, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 2, 25, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-manual",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-manual",
                    session_id="session-manual",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.FAILED,
                    created_at=started_at,
                    ended_at=ended_at,
                    stop_reason="missing_binary",
                    failure_category="environment",
                    recoverable=False,
                    recovery_hint="Install ffmpeg and verify PATH on the runtime host.",
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

        RecorderService(self.settings).run()
        RecorderService(self.settings).run()

        self.assertFalse((self.temp_root / "recording-assets.jsonl").exists())
        recorder_state = json.loads((self.temp_root / "recorder-state.json").read_text(encoding="utf-8"))
        self.assertEqual(recorder_state["manual_required_job_ids"], ["job-manual"])

        audit_payloads = [
            json.loads(line)
            for line in (self.temp_root / "recorder-events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        manual_events = [
            item for item in audit_payloads if item["event_type"] == "recording_manual_recovery_required"
        ]
        self.assertEqual(len(manual_events), 1)
        self.assertEqual(manual_events[0]["job_id"], "job-manual")
        self.assertIn("missing_binary", manual_events[0]["reason"])
        recovery_action_payloads = [
            json.loads(line)
            for line in (
                self.temp_root / "recorder-recovery-actions.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(recovery_action_payloads), 1)
        self.assertEqual(recovery_action_payloads[0]["job_id"], "job-manual")
        self.assertEqual(recovery_action_payloads[0]["action_type"], "inspect_failure_logs")
        self.assertEqual(
            recovery_action_payloads[0]["failure_category"],
            "unknown_unclassified_non_retryable",
        )
        self.assertFalse(recovery_action_payloads[0]["recoverable"])
        self.assertGreater(len(recovery_action_payloads[0]["steps"]), 0)

    def test_recorder_failed_job_still_marks_manual_recovery_when_previously_processed(self) -> None:
        started_at = datetime(2026, 4, 25, 2, 26, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 2, 29, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-failed-processed",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-failed-processed",
                    session_id="session-failed-processed",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.FAILED,
                    created_at=started_at,
                    ended_at=ended_at,
                    stop_reason="missing_binary",
                    failure_category="environment",
                    recoverable=False,
                    recovery_hint="Install ffmpeg and verify PATH on the runtime host.",
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        (self.temp_root / "recorder-state.json").write_text(
            json.dumps(
                {
                    "processed_job_ids": ["job-failed-processed"],
                    "retry_attempts_by_job_id": {"job-failed-processed": 2},
                    "manual_required_job_ids": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        RecorderService(self.settings).run()
        RecorderService(self.settings).run()

        recorder_state = json.loads((self.temp_root / "recorder-state.json").read_text(encoding="utf-8"))
        self.assertEqual(recorder_state["processed_job_ids"], ["job-failed-processed"])
        self.assertEqual(recorder_state["retry_attempts_by_job_id"], {})
        self.assertEqual(recorder_state["manual_required_job_ids"], ["job-failed-processed"])
        audit_payloads = [
            json.loads(line)
            for line in (self.temp_root / "recorder-events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        manual_events = [
            item for item in audit_payloads if item["event_type"] == "recording_manual_recovery_required"
        ]
        self.assertEqual(len(manual_events), 1)
        recovery_action_payloads = [
            json.loads(line)
            for line in (
                self.temp_root / "recorder-recovery-actions.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(recovery_action_payloads), 1)

    def test_recorder_manual_recovery_action_infers_prerequisite_from_http_404_reason(self) -> None:
        started_at = datetime(2026, 4, 25, 2, 27, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 2, 30, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-manual-infer-404",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-manual-infer-404",
                    session_id="session-manual-infer-404",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.FAILED,
                    created_at=started_at,
                    ended_at=ended_at,
                    stop_reason="[https @ 0xdeadbeef] Server returned 404 Not Found",
                    failure_category=None,
                    recoverable=None,
                    recovery_hint=None,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

        RecorderService(self.settings).run()

        recovery_action_payloads = [
            json.loads(line)
            for line in (
                self.temp_root / "recorder-recovery-actions.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(recovery_action_payloads), 1)
        self.assertEqual(
            recovery_action_payloads[0]["action_type"],
            "restore_source_prerequisites",
        )
        self.assertEqual(
            recovery_action_payloads[0]["failure_category"],
            "http_4xx_non_retryable",
        )

    def test_recorder_manual_recovery_action_falls_back_to_inspect_for_unknown_reason(self) -> None:
        started_at = datetime(2026, 4, 25, 2, 28, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 2, 31, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-manual-infer-unknown",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-manual-infer-unknown",
                    session_id="session-manual-infer-unknown",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.FAILED,
                    created_at=started_at,
                    ended_at=ended_at,
                    stop_reason="opaque_failure_signature_xyz",
                    failure_category=None,
                    recoverable=None,
                    recovery_hint=None,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

        RecorderService(self.settings).run()

        recovery_action_payloads = [
            json.loads(line)
            for line in (
                self.temp_root / "recorder-recovery-actions.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(recovery_action_payloads), 1)
        self.assertEqual(
            recovery_action_payloads[0]["action_type"],
            "inspect_failure_logs",
        )
        self.assertEqual(
            recovery_action_payloads[0]["failure_category"],
            "unknown_unclassified_non_retryable",
        )

    def test_processed_job_transitions_to_failed_then_manual_recovery_is_emitted(self) -> None:
        started_at = datetime(2026, 4, 25, 2, 28, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 2, 33, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-transition",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-transition",
                    session_id="session-transition",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

        with patch("arl.recorder.service.shutil.which", return_value=None):
            RecorderService(self.settings).run()

        OrchestratorService(self.settings).run_once()
        RecorderService(self.settings).run()

        updated_state = OrchestratorStateFile.model_validate_json(
            self.orchestrator_state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(updated_state.recording_jobs[0].status, RecordingJobStatus.FAILED)
        recorder_state = json.loads((self.temp_root / "recorder-state.json").read_text(encoding="utf-8"))
        self.assertEqual(recorder_state["processed_job_ids"], ["job-transition"])
        self.assertEqual(recorder_state["manual_required_job_ids"], ["job-transition"])
        audit_payloads = [
            json.loads(line)
            for line in (self.temp_root / "recorder-events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        manual_events = [
            item for item in audit_payloads if item["event_type"] == "recording_manual_recovery_required"
        ]
        self.assertEqual(len(manual_events), 1)
        self.assertEqual(manual_events[0]["job_id"], "job-transition")
        recovery_action_payloads = [
            json.loads(line)
            for line in (
                self.temp_root / "recorder-recovery-actions.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(recovery_action_payloads), 1)
        self.assertEqual(recovery_action_payloads[0]["job_id"], "job-transition")

    def test_recorder_retrying_job_reopens_even_when_previously_processed(self) -> None:
        started_at = datetime(2026, 4, 25, 2, 31, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-reopen",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=started_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-reopen",
                    session_id="session-reopen",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.RETRYING,
                    created_at=started_at,
                    ended_at=None,
                    stop_reason="operator-fixed",
                    failure_category="environment",
                    recoverable=False,
                    recovery_hint="Install ffmpeg and verify PATH on the runtime host.",
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        (self.temp_root / "recorder-state.json").write_text(
            json.dumps(
                {
                    "processed_job_ids": ["job-reopen"],
                    "retry_attempts_by_job_id": {"job-reopen": 2},
                    "manual_required_job_ids": ["job-reopen"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.settings.recording.enable_ffmpeg = False

        RecorderService(self.settings).run()

        assets_path = self.temp_root / "recording-assets.jsonl"
        self.assertTrue(assets_path.exists())
        asset_payloads = [
            json.loads(line) for line in assets_path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        self.assertEqual(len(asset_payloads), 1)
        recorder_state = json.loads((self.temp_root / "recorder-state.json").read_text(encoding="utf-8"))
        self.assertEqual(recorder_state["processed_job_ids"], ["job-reopen"])
        self.assertEqual(recorder_state["manual_required_job_ids"], [])
        self.assertEqual(recorder_state["retry_attempts_by_job_id"], {})

    def test_manual_recovery_resolved_requeues_and_orchestrator_reopens_job(self) -> None:
        started_at = datetime(2026, 4, 25, 2, 30, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 2, 35, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-manual-requeue",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-manual-requeue",
                    session_id="session-manual-requeue",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.FAILED,
                    created_at=started_at,
                    ended_at=ended_at,
                    stop_reason="missing_binary",
                    failure_category="environment",
                    recoverable=False,
                    recovery_hint="Install ffmpeg and verify PATH on the runtime host.",
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

        RecorderService(self.settings).run()

        recovery_service = RecoveryService(self.settings)
        recovery_service.run()
        self.assertEqual(
            recovery_service.mark_resolved("job-manual-requeue", "operator-fixed"),
            1,
        )

        OrchestratorService(self.settings).run_once()

        updated_state = OrchestratorStateFile.model_validate_json(
            self.orchestrator_state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(updated_state.recording_jobs[0].status, RecordingJobStatus.RETRYING)
        self.assertEqual(updated_state.recording_jobs[0].stop_reason, "operator-fixed")
        self.assertIsNone(updated_state.recording_jobs[0].ended_at)
        self.assertEqual(
            updated_state.active_recording_job_id_by_platform[
                "douyin:https://live.douyin.com/room"
            ],
            "job-manual-requeue",
        )

        recorder_event_payloads = [
            json.loads(line)
            for line in (self.temp_root / "recorder-events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(
            any(
                item["event_type"] == "recording_retry_scheduled"
                and item["job_id"] == "job-manual-requeue"
                and item.get("reason") == "operator-fixed"
                for item in recorder_event_payloads
            )
        )

    def test_recorder_browser_capture_auto_windows_defaults_to_gdigrab(self) -> None:
        started_at = datetime(2026, 4, 25, 2, 10, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 2, 15, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-bc-win",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-bc-win",
                    session_id="session-bc-win",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

        self.settings.recording.browser_capture_input = ""
        self.settings.recording.browser_capture_format = "auto"
        self.settings.recording.browser_capture_timeout_seconds = 3

        captured_commands: list[list[str]] = []

        def _fake_ffmpeg(*args, **kwargs):
            command = args[0]
            captured_commands.append(command)
            Path(command[-1]).write_text("fake video bytes", encoding="utf-8")
            return subprocess.CompletedProcess(args=command, returncode=0)

        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.sys.platform",
            "win32",
        ), patch("arl.recorder.service.subprocess.run", side_effect=_fake_ffmpeg):
            RecorderService(self.settings).run()

        self.assertEqual(len(captured_commands), 1)
        command = captured_commands[0]
        self.assertIn("gdigrab", command)
        self.assertIn("desktop", command)

    def test_recorder_browser_capture_auto_macos_defaults_to_avfoundation(self) -> None:
        started_at = datetime(2026, 4, 25, 2, 16, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 2, 19, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-bc-mac",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-bc-mac",
                    session_id="session-bc-mac",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

        self.settings.recording.browser_capture_input = ""
        self.settings.recording.browser_capture_format = "auto"
        self.settings.recording.browser_capture_timeout_seconds = 3

        captured_commands: list[list[str]] = []

        def _fake_ffmpeg(*args, **kwargs):
            command = args[0]
            captured_commands.append(command)
            Path(command[-1]).write_text("fake video bytes", encoding="utf-8")
            return subprocess.CompletedProcess(args=command, returncode=0)

        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.sys.platform",
            "darwin",
        ), patch("arl.recorder.service.subprocess.run", side_effect=_fake_ffmpeg):
            RecorderService(self.settings).run()

        self.assertEqual(len(captured_commands), 1)
        command = captured_commands[0]
        self.assertIn("avfoundation", command)
        self.assertIn("default:none", command)

    def test_recorder_unsupported_browser_capture_format_falls_back_to_platform_default(self) -> None:
        started_at = datetime(2026, 4, 25, 2, 19, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 2, 20, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-bc-format-fallback",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-bc-format-fallback",
                    session_id="session-bc-format-fallback",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

        self.settings.recording.browser_capture_input = ""
        self.settings.recording.browser_capture_format = "invalid-format"
        self.settings.recording.browser_capture_timeout_seconds = 3

        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.sys.platform",
            "linux",
        ), patch("arl.recorder.service.os.getenv", return_value=""), patch(
            "arl.recorder.service.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["ffmpeg"]),
        ):
            RecorderService(self.settings).run()

        audit_payloads = [
            json.loads(line)
            for line in (self.temp_root / "recorder-events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        skipped_events = [item for item in audit_payloads if item["event_type"] == "ffmpeg_skipped"]
        self.assertEqual(len(skipped_events), 1)
        self.assertEqual(skipped_events[0]["reason"], "missing_browser_capture_input")

    def test_recorder_skips_auto_x11grab_when_display_unavailable(self) -> None:
        started_at = datetime(2026, 4, 25, 2, 20, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 2, 25, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-bc-linux",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-bc-linux",
                    session_id="session-bc-linux",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

        self.settings.recording.enable_ffmpeg = True
        self.settings.recording.browser_capture_input = ""
        self.settings.recording.browser_capture_format = "x11grab"

        probe_failure = subprocess.CalledProcessError(
            1,
            ["ffmpeg"],
            stderr="[x11grab] Cannot open display :0, error 1.\nError opening input files: Input/output error",
        )
        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.os.getenv",
            return_value=":0",
        ), patch("arl.recorder.service.subprocess.run", side_effect=probe_failure):
            RecorderService(self.settings).run()

        audit_payloads = [
            json.loads(line)
            for line in (self.temp_root / "recorder-events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        skipped_events = [item for item in audit_payloads if item["event_type"] == "ffmpeg_skipped"]
        self.assertEqual(len(skipped_events), 1)
        self.assertIn("unavailable_browser_capture_display", skipped_events[0]["reason"])

    def test_recorder_auto_x11grab_uses_fallback_display_candidate(self) -> None:
        started_at = datetime(2026, 4, 25, 2, 30, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 2, 35, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-bc-linux-fallback",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-bc-linux-fallback",
                    session_id="session-bc-linux-fallback",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

        self.settings.recording.enable_ffmpeg = True
        self.settings.recording.browser_capture_input = ""
        self.settings.recording.browser_capture_format = "x11grab"
        self.settings.recording.browser_capture_timeout_seconds = 3

        captured_commands: list[list[str]] = []
        probe_attempt_inputs: list[str] = []

        def _fake_run(*args, **kwargs):
            command = args[0]
            if "-f" in command and "null" in command and command[-1] == "-":
                input_value = command[command.index("-i") + 1]
                probe_attempt_inputs.append(input_value)
                if input_value == ":0":
                    raise subprocess.CalledProcessError(
                        1,
                        command,
                        stderr=(
                            "[x11grab] Cannot open display :0, error 1.\n"
                            "Error opening input files: Input/output error"
                        ),
                    )
                return subprocess.CompletedProcess(args=command, returncode=0)

            captured_commands.append(command)
            Path(command[-1]).write_text("fake video bytes", encoding="utf-8")
            return subprocess.CompletedProcess(args=command, returncode=0)

        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.os.getenv",
            return_value=":0",
        ), patch("arl.recorder.service.subprocess.run", side_effect=_fake_run):
            RecorderService(self.settings).run()

        self.assertEqual(probe_attempt_inputs, [":0", ":0.0"])
        self.assertEqual(len(captured_commands), 1)
        command = captured_commands[0]
        self.assertIn("x11grab", command)
        self.assertIn(":0.0", command)

    def test_x11_probe_result_is_cached_per_input_in_single_run(self) -> None:
        started_at = datetime(2026, 4, 25, 2, 31, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 25, 2, 36, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-bc-cache-a",
                    streamer_name="streamer-a",
                    room_url="https://live.douyin.com/room/a",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                ),
                SessionRecord(
                    session_id="session-bc-cache-b",
                    streamer_name="streamer-b",
                    room_url="https://live.douyin.com/room/b",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                    ended_at=ended_at,
                ),
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-bc-cache-a",
                    session_id="session-bc-cache-a",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                ),
                RecordingJobRecord(
                    job_id="job-bc-cache-b",
                    session_id="session-bc-cache-b",
                    source_type=SourceType.BROWSER_CAPTURE,
                    stream_url=None,
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                    ended_at=ended_at,
                ),
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

        self.settings.recording.enable_ffmpeg = True
        self.settings.recording.browser_capture_input = ""
        self.settings.recording.browser_capture_format = "x11grab"
        self.settings.recording.browser_capture_timeout_seconds = 3

        probe_call_count = 0

        def _fake_run(*args, **kwargs):
            nonlocal probe_call_count
            command = args[0]
            if "-f" in command and "null" in command and command[-1] == "-":
                probe_call_count += 1
                return subprocess.CompletedProcess(args=command, returncode=0)
            Path(command[-1]).write_text("fake video bytes", encoding="utf-8")
            return subprocess.CompletedProcess(args=command, returncode=0)

        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.os.getenv",
            return_value=":0",
        ), patch("arl.recorder.service.subprocess.run", side_effect=_fake_run):
            RecorderService(self.settings).run()

        self.assertEqual(probe_call_count, 1)

    def test_exporter_retries_ffmpeg_then_falls_back(self) -> None:
        boundary = MatchBoundary(
            session_id="session-e",
            match_index=1,
            started_at_seconds=0.0,
            ended_at_seconds=60.0,
            confidence=0.9,
        )
        subtitle_file = self.processed_root / "session-e" / "match-01.srt"
        subtitle_file.parent.mkdir(parents=True, exist_ok=True)
        subtitle_file.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
            encoding="utf-8",
        )
        subtitle = SubtitleAsset(
            session_id="session-e",
            match_index=1,
            path=str(subtitle_file),
            format="srt",
        )
        recording_file = self.raw_root / "session-e" / "recording-source.mp4"
        recording_file.parent.mkdir(parents=True, exist_ok=True)
        recording_file.write_text("not-a-real-video", encoding="utf-8")
        recording = RecordingAsset(
            session_id="session-e",
            source_type=SourceType.DIRECT_STREAM,
            path=str(recording_file),
            started_at=datetime(2026, 4, 25, 1, 0, tzinfo=timezone.utc),
            ended_at=datetime(2026, 4, 25, 1, 10, tzinfo=timezone.utc),
        )

        append_model(self.temp_root / "match-boundaries.jsonl", boundary)
        append_model(self.temp_root / "subtitle-assets.jsonl", subtitle)
        append_model(self.temp_root / "recording-assets.jsonl", recording)

        with patch("arl.exporter.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["ffmpeg"]),
        ) as mocked_run:
            ExporterService(self.settings).run()

        self.assertEqual(mocked_run.call_count, 2)
        self.assertFalse((self.temp_root / "export-assets.jsonl").exists())
        state = ExporterStateFile.model_validate_json(
            (self.temp_root / "exporter-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state.deferred_match_keys, ["session-e:1"])


class RecorderHardeningTest(unittest.TestCase):
    """R1-R5 coverage for recorder ffmpeg failure production hardening."""

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
            recording=RecordingSettings(
                enable_ffmpeg=True,
                ffmpeg_max_retries=2,
                direct_stream_timeout_seconds=5,
                auto_retry_max_attempts=2,
                session_retry_budget=3,
                stderr_retain_count=4,
            ),
            subtitles=SubtitleSettings(enabled=True),
            export=ExportSettings(enable_ffmpeg=False),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ----- helpers -----

    def _seed_state(
        self,
        *,
        session_id: str = "session-hd",
        job_id: str = "job-hd",
        extra_jobs: list[tuple[str, str, RecordingJobStatus]] | None = None,
    ) -> OrchestratorStateFile:
        started_at = datetime(2026, 5, 11, 1, 0, tzinfo=timezone.utc)
        ended_at = datetime(2026, 5, 11, 1, 10, tzinfo=timezone.utc)
        sessions = [
            SessionRecord(
                session_id=session_id,
                streamer_name="streamer-a",
                room_url="https://live.douyin.com/room",
                source_type=SourceType.DIRECT_STREAM,
                stream_url="https://example.invalid/live.m3u8",
                status=SessionStatus.STOPPED,
                started_at=started_at,
                ended_at=ended_at,
            )
        ]
        jobs = [
            RecordingJobRecord(
                job_id=job_id,
                session_id=session_id,
                source_type=SourceType.DIRECT_STREAM,
                stream_url="https://example.invalid/live.m3u8",
                status=RecordingJobStatus.STOPPED,
                created_at=started_at,
                ended_at=ended_at,
            )
        ]
        for extra_session_id, extra_job_id, extra_status in extra_jobs or []:
            if extra_session_id not in {item.session_id for item in sessions}:
                sessions.append(
                    SessionRecord(
                        session_id=extra_session_id,
                        streamer_name="streamer-b",
                        room_url="https://live.douyin.com/other",
                        source_type=SourceType.DIRECT_STREAM,
                        stream_url="https://example.invalid/other.m3u8",
                        status=SessionStatus.STOPPED,
                        started_at=started_at,
                        ended_at=ended_at,
                    )
                )
            jobs.append(
                RecordingJobRecord(
                    job_id=extra_job_id,
                    session_id=extra_session_id,
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/other.m3u8",
                    status=extra_status,
                    created_at=started_at,
                    ended_at=ended_at,
                )
            )
        state = OrchestratorStateFile(sessions=sessions, recording_jobs=jobs)
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return state

    def _audit_payloads(self) -> list[dict]:
        path = self.temp_root / "recorder-events.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _run_with_ffmpeg_error(self, error: BaseException) -> int:
        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.subprocess.run",
            side_effect=error,
        ) as mocked_run:
            RecorderService(self.settings).run()
        return mocked_run.call_count

    # ----- R1: yield-on-transient -----

    def test_long_direct_stream_capture_reserves_finalize_headroom(self) -> None:
        self._seed_state(job_id="job-headroom")
        self.settings.recording.direct_stream_timeout_seconds = 7200
        self.settings.recording.direct_stream_finalize_headroom_seconds = 60
        self.settings.recording.validate_actual_resolution = False

        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["ffmpeg"], returncode=0),
        ) as mocked_run:
            RecorderService(self.settings).run()

        self.assertEqual(mocked_run.call_count, 1)
        command = list(mocked_run.call_args[0][0])
        self.assertEqual(command[command.index("-t") + 1], "7140")

    def test_recorder_runs_multiple_jobs_with_bounded_parallelism(self) -> None:
        self._seed_state(
            job_id="job-parallel-a",
            extra_jobs=[
                ("session-parallel-b", "job-parallel-b", RecordingJobStatus.STOPPED),
            ],
        )
        self.settings.recording.max_concurrent_jobs = 2
        self.settings.recording.validate_actual_resolution = False

        lock = threading.Lock()
        direct_started = 0
        both_started = threading.Event()

        def _fake_run(command, **kwargs):
            nonlocal direct_started
            output_path = Path(command[-1])
            if output_path.name == "recording-source.mp4":
                with lock:
                    direct_started += 1
                    if direct_started == 2:
                        both_started.set()
                self.assertTrue(
                    both_started.wait(timeout=2),
                    "expected two ffmpeg recording commands to overlap",
                )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text("fake recording", encoding="utf-8")
                return subprocess.CompletedProcess(args=command, returncode=0)
            if output_path.name == "recording-source.remux.mp4":
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text("fake remux", encoding="utf-8")
                return subprocess.CompletedProcess(args=command, returncode=0)
            raise AssertionError(f"unexpected command: {command}")

        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.subprocess.run", side_effect=_fake_run
        ):
            RecorderService(self.settings).run()

        self.assertEqual(direct_started, 2)
        assets = [
            json.loads(line)
            for line in (self.temp_root / "recording-assets.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(len(assets), 2)
        self.assertEqual(
            {Path(asset["path"]).name for asset in assets},
            {"recording-source.mp4"},
        )

    def test_interrupted_parallel_run_persists_completed_outcomes(self) -> None:
        self._seed_state(
            session_id="session-interrupt-a",
            job_id="job-interrupt-a",
            extra_jobs=[
                ("session-interrupt-b", "job-interrupt-b", RecordingJobStatus.STOPPED),
            ],
        )
        self.settings.recording.max_concurrent_jobs = 2

        first_done = threading.Event()
        release_second = threading.Event()

        def _fake_build(
            service: RecorderService,
            item,
        ) -> RecordingWorkResult:
            output_path = (
                service.settings.storage.raw_dir
                / item.session.session_id
                / "recording-source.mp4"
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if item.job.job_id == "job-interrupt-a":
                output_path.write_text("completed recording", encoding="utf-8")
                first_done.set()
                return RecordingWorkResult(
                    item=item,
                    outcome=RecordingBuildOutcome(output_path=output_path),
                )

            self.assertTrue(first_done.wait(timeout=2))
            release_second.wait(timeout=2)
            output_path.write_text("late recording", encoding="utf-8")
            return RecordingWorkResult(
                item=item,
                outcome=RecordingBuildOutcome(output_path=output_path),
            )

        def _interrupting_as_completed(futures):
            self.assertTrue(first_done.wait(timeout=2))
            raise KeyboardInterrupt

        try:
            with patch(
                "arl.recorder.service.RecorderService._build_recording_work_item",
                new=_fake_build,
            ), patch(
                "arl.recorder.service.as_completed",
                side_effect=_interrupting_as_completed,
            ), patch(
                "arl.recorder.service._RECORDING_INTERRUPT_DRAIN_TIMEOUT_SECONDS",
                0.05,
            ), patch.object(
                RecorderService,
                "_remux_direct_stream_recording",
                return_value=None,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    RecorderService(self.settings).run()
        finally:
            release_second.set()

        assets = [
            json.loads(line)
            for line in (self.temp_root / "recording-assets.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["session_id"], "session-interrupt-a")

        recorder_state = json.loads(
            (self.temp_root / "recorder-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(recorder_state["processed_job_ids"], ["job-interrupt-a"])

    def test_direct_stream_failure_salvages_probeable_partial_mp4_as_asset(self) -> None:
        self._seed_state(job_id="job-partial-salvage")

        def _which(binary: str) -> str | None:
            if binary in {"ffmpeg", "ffprobe"}:
                return f"/usr/bin/{binary}"
            return None

        def _fake_run(command, **kwargs):
            if command[0].endswith("ffmpeg"):
                output_path = Path(command[-1])
                output_path.parent.mkdir(parents=True, exist_ok=True)
                if output_path.name == "recording-source.mp4":
                    output_path.write_text("probeable partial mp4", encoding="utf-8")
                    raise subprocess.CalledProcessError(
                        1,
                        command,
                        stderr="[http] Error reading HTTP response: Error number -10054 occurred",
                    )
                if output_path.name == "recording-source.remux.mp4":
                    output_path.write_text("remuxed mp4", encoding="utf-8")
                    return subprocess.CompletedProcess(args=command, returncode=0)
            if command[0].endswith("ffprobe"):
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "streams": [
                                {
                                    "width": 1920,
                                    "height": 1080,
                                    "bit_rate": "6000000",
                                }
                            ]
                        }
                    ),
                )
            raise AssertionError(f"unexpected command: {command}")

        with patch("arl.recorder.service.shutil.which", side_effect=_which), patch(
            "arl.recorder.service.subprocess.run", side_effect=_fake_run
        ):
            RecorderService(self.settings).run()

        assets = [
            json.loads(line)
            for line in (self.temp_root / "recording-assets.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(len(assets), 1)
        self.assertEqual(Path(assets[0]["path"]).name, "recording-source.mp4")
        self.assertFalse((self.raw_root / "session-hd" / "recording-source.txt").exists())

        event_types = [item["event_type"] for item in self._audit_payloads()]
        self.assertIn("ffmpeg_record_failed", event_types)
        self.assertIn("ffmpeg_record_succeeded", event_types)
        self.assertNotIn("ffmpeg_fallback_placeholder", event_types)

    def test_r1_yield_on_http_5xx_runs_ffmpeg_once_with_yield_decision(self) -> None:
        self._seed_state(job_id="job-5xx")
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Server returned 503 Service Unavailable"
        )
        call_count = self._run_with_ffmpeg_error(error)
        self.assertEqual(call_count, 1)
        fail_events = [
            item for item in self._audit_payloads() if item["event_type"] == "ffmpeg_record_failed"
        ]
        self.assertEqual(len(fail_events), 1)
        self.assertEqual(fail_events[0]["decision"], "attempt_failed_yield_to_next_probe")
        self.assertEqual(fail_events[0]["failure_category"], "http_5xx_retryable")

    def test_r1_yield_on_network_timeout(self) -> None:
        self._seed_state(job_id="job-timeout")
        error = subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=5)
        call_count = self._run_with_ffmpeg_error(error)
        self.assertEqual(call_count, 1)
        fail_events = [
            item for item in self._audit_payloads() if item["event_type"] == "ffmpeg_record_failed"
        ]
        self.assertEqual(len(fail_events), 1)
        self.assertEqual(fail_events[0]["decision"], "attempt_failed_yield_to_next_probe")
        self.assertEqual(fail_events[0]["failure_category"], "network_timeout_retryable")

    def test_r1_yield_on_ffmpeg_process_error(self) -> None:
        self._seed_state(job_id="job-process")
        error = subprocess.CalledProcessError(1, ["ffmpeg"])  # no stderr → exit_status:1
        call_count = self._run_with_ffmpeg_error(error)
        self.assertEqual(call_count, 1)
        fail_events = [
            item for item in self._audit_payloads() if item["event_type"] == "ffmpeg_record_failed"
        ]
        self.assertEqual(len(fail_events), 1)
        self.assertEqual(fail_events[0]["decision"], "attempt_failed_yield_to_next_probe")
        self.assertEqual(
            fail_events[0]["failure_category"], "ffmpeg_process_error_retryable"
        )

    def test_r1_non_retryable_http_4xx_uses_attempt_failed_decision(self) -> None:
        self._seed_state(job_id="job-4xx")
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Server returned 404 Not Found"
        )
        call_count = self._run_with_ffmpeg_error(error)
        self.assertEqual(call_count, 1)
        fail_events = [
            item for item in self._audit_payloads() if item["event_type"] == "ffmpeg_record_failed"
        ]
        self.assertEqual(len(fail_events), 1)
        self.assertEqual(fail_events[0]["decision"], "attempt_failed")
        self.assertEqual(fail_events[0]["failure_category"], "http_4xx_non_retryable")

    # ----- R2 + R3: stderr excerpt + log file -----

    def test_r2_stderr_excerpt_and_log_path_present_on_failure(self) -> None:
        self._seed_state(job_id="job-stderr")
        long_stderr = "\n".join(
            [f"line-{idx:02d} long stderr content " + "x" * 30 for idx in range(40)]
        )
        error = subprocess.CalledProcessError(1, ["ffmpeg"], stderr=long_stderr)
        self._run_with_ffmpeg_error(error)
        fail_events = [
            item for item in self._audit_payloads() if item["event_type"] == "ffmpeg_record_failed"
        ]
        self.assertEqual(len(fail_events), 1)
        excerpt = fail_events[0]["stderr_excerpt"]
        self.assertIsNotNone(excerpt)
        self.assertLessEqual(len(excerpt), 4096)
        self.assertIn("line-00", excerpt)
        self.assertIn("line-39", excerpt)
        log_path = fail_events[0]["stderr_log_path"]
        self.assertIsNotNone(log_path)
        log_file = Path(log_path)
        self.assertTrue(log_file.exists(), f"expected log at {log_path}")
        self.assertIn(long_stderr, log_file.read_text(encoding="utf-8"))

    def test_r2_success_audit_has_no_stderr_fields(self) -> None:
        self._seed_state(job_id="job-success")
        # Make subprocess.run succeed by patching it to no-op.
        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.subprocess.run", return_value=None
        ):
            # Pre-create output file so the recorder picks it up as success.
            output_dir = self.raw_root / "session-hd"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "recording-source.mp4").write_text("ok", encoding="utf-8")
            RecorderService(self.settings).run()
        success_events = [
            item
            for item in self._audit_payloads()
            if item["event_type"] == "ffmpeg_record_succeeded"
        ]
        self.assertEqual(len(success_events), 1)
        self.assertIsNone(success_events[0].get("stderr_excerpt"))
        self.assertIsNone(success_events[0].get("stderr_log_path"))

    def test_quality_gate_rejects_actual_resolution_below_1080p(self) -> None:
        self._seed_state(job_id="job-quality-low")

        def _which(binary: str) -> str | None:
            if binary in {"ffmpeg", "ffprobe"}:
                return f"/usr/bin/{binary}"
            return None

        def _fake_run(command, **kwargs):
            if command[0].endswith("ffmpeg"):
                output_path = Path(command[-1])
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text("fake 720p video", encoding="utf-8")
                return subprocess.CompletedProcess(args=command, returncode=0)
            if command[0].endswith("ffprobe"):
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "streams": [
                                {
                                    "width": 1280,
                                    "height": 720,
                                    "bit_rate": "3800000",
                                }
                            ]
                        }
                    ),
                )
            raise AssertionError(f"unexpected command: {command}")

        with patch("arl.recorder.service.shutil.which", side_effect=_which), patch(
            "arl.recorder.service.subprocess.run", side_effect=_fake_run
        ):
            RecorderService(self.settings).run()

        recording_file = self.raw_root / "session-hd" / "recording-source.mp4"
        self.assertFalse(recording_file.exists())
        self.assertFalse((self.temp_root / "recording-assets.jsonl").exists())
        events = self._audit_payloads()
        quality_events = [
            item
            for item in events
            if item["event_type"] == "quality_below_actual_resolution"
        ]
        self.assertEqual(len(quality_events), 1)
        event = quality_events[0]
        self.assertEqual(event["observed_width"], 1280)
        self.assertEqual(event["observed_height"], 720)
        self.assertEqual(event["observed_bitrate_kbps"], 3800)
        self.assertEqual(event["min_required_height"], 1080)
        self.assertEqual(event["failure_category"], "quality_unusable_non_retryable")
        self.assertEqual(event["reason_code"], "quality_below_actual_resolution")
        self.assertFalse(any(item["event_type"] == "ffmpeg_record_succeeded" for item in events))

    def test_quality_gate_accepts_actual_resolution_at_1080p(self) -> None:
        self._seed_state(job_id="job-quality-pass")

        def _which(binary: str) -> str | None:
            if binary in {"ffmpeg", "ffprobe"}:
                return f"/usr/bin/{binary}"
            return None

        def _fake_run(command, **kwargs):
            if command[0].endswith("ffmpeg"):
                output_path = Path(command[-1])
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text("fake 1080p video", encoding="utf-8")
                return subprocess.CompletedProcess(args=command, returncode=0)
            if command[0].endswith("ffprobe"):
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "streams": [
                                {
                                    "width": 1920,
                                    "height": 1080,
                                    "bit_rate": "6200000",
                                }
                            ]
                        }
                    ),
                )
            raise AssertionError(f"unexpected command: {command}")

        with patch("arl.recorder.service.shutil.which", side_effect=_which), patch(
            "arl.recorder.service.subprocess.run", side_effect=_fake_run
        ):
            RecorderService(self.settings).run()

        recording_file = self.raw_root / "session-hd" / "recording-source.mp4"
        self.assertTrue(recording_file.exists())
        payload = json.loads(
            (self.temp_root / "recording-assets.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        self.assertTrue(payload["path"].endswith("recording-source.mp4"))
        events = self._audit_payloads()
        self.assertTrue(any(item["event_type"] == "ffmpeg_record_succeeded" for item in events))
        self.assertFalse(
            any(item["event_type"] == "quality_below_actual_resolution" for item in events)
        )

    def test_live_session_recording_asset_uses_attempt_window(self) -> None:
        started_at = datetime(2026, 5, 11, 1, 0, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-live-asset",
                    streamer_name="streamer-live",
                    room_url="https://live.douyin.com/live",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.LIVE,
                    started_at=started_at,
                    ended_at=None,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-live-asset",
                    session_id="session-live-asset",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.QUEUED,
                    created_at=started_at,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        self.settings.recording.validate_actual_resolution = False

        def _fake_run(command, **kwargs):
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("fake video", encoding="utf-8")
            return subprocess.CompletedProcess(args=command, returncode=0)

        with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.recorder.service.subprocess.run", side_effect=_fake_run
        ):
            RecorderService(self.settings).run()

        payload = json.loads(
            (self.temp_root / "recording-assets.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        self.assertNotEqual(payload["started_at"], started_at.isoformat())
        self.assertIsNotNone(payload["ended_at"])

    def test_direct_stream_success_remuxes_fragmented_mp4_to_faststart(self) -> None:
        self._seed_state(job_id="job-remux")
        commands: list[list[str]] = []
        remux_saw_durable_success = False

        def _which(binary: str) -> str | None:
            if binary in {"ffmpeg", "ffprobe"}:
                return f"/usr/bin/{binary}"
            return None

        def _fake_run(command, **kwargs):
            nonlocal remux_saw_durable_success
            commands.append(list(command))
            if command[0].endswith("ffprobe"):
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps({"streams": [{"width": 1920, "height": 1080}]}),
                )
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if output_path.name == "recording-source.mp4":
                output_path.write_text("fragmented", encoding="utf-8")
            elif output_path.name == "recording-source.remux.mp4":
                asset_rows = (self.temp_root / "recording-assets.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
                self.assertEqual(len(asset_rows), 1)
                asset_payload = json.loads(asset_rows[0])
                self.assertTrue(asset_payload["path"].endswith("recording-source.mp4"))
                recorder_state = json.loads(
                    (self.temp_root / "recorder-state.json").read_text(encoding="utf-8")
                )
                self.assertEqual(recorder_state["processed_job_ids"], ["job-remux"])
                remux_saw_durable_success = True
                output_path.write_text("faststart", encoding="utf-8")
            else:
                raise AssertionError(f"unexpected output path: {output_path}")
            return subprocess.CompletedProcess(args=command, returncode=0)

        with patch("arl.recorder.service.shutil.which", side_effect=_which), patch(
            "arl.recorder.service.subprocess.run", side_effect=_fake_run
        ):
            RecorderService(self.settings).run()

        recording_file = self.raw_root / "session-hd" / "recording-source.mp4"
        self.assertEqual(recording_file.read_text(encoding="utf-8"), "faststart")
        self.assertFalse((self.raw_root / "session-hd" / "recording-source.remux.mp4").exists())
        self.assertTrue(remux_saw_durable_success)
        remux_commands = [
            command
            for command in commands
            if command[0].endswith("ffmpeg")
            and command[-1].endswith("recording-source.remux.mp4")
        ]
        self.assertEqual(len(remux_commands), 1)
        remux_command = remux_commands[0]
        self.assertIn("-map", remux_command)
        self.assertIn("0", remux_command)
        self.assertIn("-c", remux_command)
        self.assertIn("copy", remux_command)
        self.assertIn("-movflags", remux_command)
        self.assertEqual(remux_command[remux_command.index("-movflags") + 1], "+faststart")
        self.assertTrue(
            any(item["event_type"] == "ffmpeg_record_succeeded" for item in self._audit_payloads())
        )

    def test_direct_stream_remux_failure_keeps_original_recording(self) -> None:
        self._seed_state(job_id="job-remux-fail")

        def _which(binary: str) -> str | None:
            if binary in {"ffmpeg", "ffprobe"}:
                return f"/usr/bin/{binary}"
            return None

        def _fake_run(command, **kwargs):
            if command[0].endswith("ffprobe"):
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps({"streams": [{"width": 1920, "height": 1080}]}),
                )
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if output_path.name == "recording-source.mp4":
                output_path.write_text("fragmented", encoding="utf-8")
                return subprocess.CompletedProcess(args=command, returncode=0)
            if output_path.name == "recording-source.remux.mp4":
                raise subprocess.CalledProcessError(
                    1,
                    command,
                    stderr="muxer failed",
                )
            raise AssertionError(f"unexpected output path: {output_path}")

        with patch("arl.recorder.service.shutil.which", side_effect=_which), patch(
            "arl.recorder.service.subprocess.run", side_effect=_fake_run
        ):
            RecorderService(self.settings).run()

        recording_file = self.raw_root / "session-hd" / "recording-source.mp4"
        self.assertEqual(recording_file.read_text(encoding="utf-8"), "fragmented")
        self.assertFalse((self.raw_root / "session-hd" / "recording-source.remux.mp4").exists())
        self.assertTrue(
            any(item["event_type"] == "ffmpeg_record_succeeded" for item in self._audit_payloads())
        )

    def test_r3_stderr_log_rotation_keeps_only_retain_count_newest(self) -> None:
        stderr_dir = self.temp_root / "recorder-stderr"
        stderr_dir.mkdir(parents=True, exist_ok=True)
        retain = self.settings.recording.stderr_retain_count  # 4
        seeded = retain + 5
        files = []
        base = datetime(2026, 5, 11, 0, 0, tzinfo=timezone.utc).timestamp()
        for idx in range(seeded):
            entry = stderr_dir / f"old-{idx:02d}.log"
            entry.write_text(f"file-{idx}", encoding="utf-8")
            mtime = base + idx
            os.utime(entry, (mtime, mtime))
            files.append((entry, mtime))

        self._seed_state(job_id="job-noop")
        # shutil.which returns None so no ffmpeg invocation; rotation still fires at run() top.
        with patch("arl.recorder.service.shutil.which", return_value=None):
            RecorderService(self.settings).run()

        remaining = sorted(stderr_dir.iterdir(), key=lambda p: p.name)
        self.assertEqual(len(remaining), retain)
        # The newest `retain` files are old-05 through old-08 (by mtime).
        remaining_names = {entry.name for entry in remaining}
        expected_newest = {
            f"old-{idx:02d}.log" for idx in range(seeded - retain, seeded)
        }
        self.assertEqual(remaining_names, expected_newest)

    # ----- R4: backoff schedule -----

    def test_r4_first_yield_sets_next_eligible_at_one_second_out(self) -> None:
        self._seed_state(job_id="job-backoff")
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Server returned 503 Service Unavailable"
        )
        before = datetime.now(timezone.utc)
        self._run_with_ffmpeg_error(error)
        after = datetime.now(timezone.utc)
        state_payload = json.loads(
            (self.temp_root / "recorder-state.json").read_text(encoding="utf-8")
        )
        eligible_at_str = state_payload["next_eligible_at_by_job_id"]["job-backoff"]
        eligible_at = datetime.fromisoformat(eligible_at_str)
        self.assertGreaterEqual(eligible_at, before + timedelta(milliseconds=900))
        self.assertLessEqual(eligible_at, after + timedelta(seconds=1, milliseconds=200))

    def test_r4_schedule_progresses_one_five_fifteen_sixty(self) -> None:
        deltas = [
            RecorderService._next_eligible_after_yield(attempt) for attempt in (1, 2, 3, 4, 5)
        ]
        self.assertEqual(
            deltas,
            [
                timedelta(seconds=1),
                timedelta(seconds=5),
                timedelta(seconds=15),
                timedelta(seconds=60),
                timedelta(seconds=60),
            ],
        )

    def test_r4_deferred_job_skips_ffmpeg_within_window(self) -> None:
        self._seed_state(job_id="job-defer")
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Server returned 503 Service Unavailable"
        )
        # First run yields and writes next_eligible_at = now + 1s.
        first_count = self._run_with_ffmpeg_error(error)
        self.assertEqual(first_count, 1)
        # Immediate second run — should defer.
        second_count = self._run_with_ffmpeg_error(error)
        self.assertEqual(second_count, 0)
        # Eligibility entry preserved across the deferred run.
        state_payload = json.loads(
            (self.temp_root / "recorder-state.json").read_text(encoding="utf-8")
        )
        self.assertIn("job-defer", state_payload["next_eligible_at_by_job_id"])

    # ----- R5: session retry budget -----

    def test_r5_session_retry_counter_increments_on_each_yield(self) -> None:
        self._seed_state(job_id="job-counter")
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Server returned 503 Service Unavailable"
        )
        with patch.object(
            RecorderService,
            "_next_eligible_after_yield",
            staticmethod(lambda attempt: timedelta(0)),
        ):
            with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
                "arl.recorder.service.subprocess.run", side_effect=error
            ):
                RecorderService(self.settings).run()
                state_after_one = json.loads(
                    (self.temp_root / "recorder-state.json").read_text(encoding="utf-8")
                )
                self.assertEqual(state_after_one["retries_by_session_id"], {"session-hd": 1})
                RecorderService(self.settings).run()
                state_after_two = json.loads(
                    (self.temp_root / "recorder-state.json").read_text(encoding="utf-8")
                )
                self.assertEqual(state_after_two["retries_by_session_id"], {"session-hd": 2})

    def test_r5_budget_boundary_emits_budget_exceeded_event_and_resets(self) -> None:
        # session_retry_budget=3 from setUp; need 3 yields to trip.
        self._seed_state(job_id="job-budget")
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Server returned 503 Service Unavailable"
        )
        # Configure auto_retry_max_attempts high enough that we hit the
        # session budget before the per-job retry budget gives up.
        self.settings.recording.auto_retry_max_attempts = 20
        with patch.object(
            RecorderService,
            "_next_eligible_after_yield",
            staticmethod(lambda attempt: timedelta(0)),
        ):
            with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
                "arl.recorder.service.subprocess.run", side_effect=error
            ):
                RecorderService(self.settings).run()
                RecorderService(self.settings).run()
                RecorderService(self.settings).run()
        budget_events = [
            item
            for item in self._audit_payloads()
            if item["event_type"] == "recording_session_retry_budget_exceeded"
        ]
        self.assertEqual(len(budget_events), 1)
        self.assertEqual(budget_events[0]["decision"], "manual_required")
        self.assertEqual(
            budget_events[0]["failure_category"], "unknown_unclassified_non_retryable"
        )
        self.assertEqual(
            budget_events[0]["reason_detail"], "session_retry_budget_exceeded:3"
        )
        state_after = json.loads(
            (self.temp_root / "recorder-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state_after["retries_by_session_id"], {"session-hd": 0})

    def test_r5_cross_session_isolation(self) -> None:
        # Two sessions; only one accumulates yields.
        self._seed_state(
            session_id="session-a",
            job_id="job-a",
            extra_jobs=[("session-b", "job-b", RecordingJobStatus.STOPPED)],
        )
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Server returned 503 Service Unavailable"
        )
        with patch.object(
            RecorderService,
            "_next_eligible_after_yield",
            staticmethod(lambda attempt: timedelta(0)),
        ):
            with patch("arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
                "arl.recorder.service.subprocess.run", side_effect=error
            ):
                RecorderService(self.settings).run()

        state_after = json.loads(
            (self.temp_root / "recorder-state.json").read_text(encoding="utf-8")
        )
        # Both sessions yielded once.
        self.assertEqual(
            state_after["retries_by_session_id"],
            {"session-a": 1, "session-b": 1},
        )

    # ----- backwards compat + orchestrator routing -----

    def test_recorder_state_loads_when_new_dict_fields_absent(self) -> None:
        legacy_state = {
            "processed_job_ids": ["job-x"],
            "retry_attempts_by_job_id": {"job-x": 1},
            "manual_required_job_ids": [],
        }
        state_path = self.temp_root / "recorder-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(legacy_state), encoding="utf-8")
        loaded = RecorderStateFile.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(loaded.next_eligible_at_by_job_id, {})
        self.assertEqual(loaded.retries_by_session_id, {})

    def test_orchestrator_routes_recording_session_retry_budget_exceeded(self) -> None:
        started_at = datetime(2026, 5, 11, 2, 0, tzinfo=timezone.utc)
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id="session-budget",
                    streamer_name="streamer-z",
                    room_url="https://live.douyin.com/z",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/z.m3u8",
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="job-budget",
                    session_id="session-budget",
                    source_type=SourceType.DIRECT_STREAM,
                    stream_url="https://example.invalid/z.m3u8",
                    status=RecordingJobStatus.RETRYING,
                    created_at=started_at,
                )
            ],
            active_recording_job_id_by_platform={"douyin": "job-budget"},
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )

        recorder_audit_path = self.temp_root / "recorder-events.jsonl"
        recorder_audit_path.parent.mkdir(parents=True, exist_ok=True)
        budget_event = {
            "event_type": "recording_session_retry_budget_exceeded",
            "session_id": "session-budget",
            "job_id": "job-budget",
            "source_type": "direct_stream",
            "decision": "manual_required",
            "failure_category": "unknown_unclassified_non_retryable",
            "is_retryable": False,
            "reason_code": "unknown_unclassified",
            "reason_detail": "session_retry_budget_exceeded:3",
            "reason": "session_retry_budget_exceeded:3",
            "created_at": started_at.isoformat(),
        }
        recorder_audit_path.write_text(json.dumps(budget_event) + "\n", encoding="utf-8")

        OrchestratorService(self.settings).run_once()

        result_state = json.loads(self.orchestrator_state_path.read_text(encoding="utf-8"))
        job = result_state["recording_jobs"][0]
        self.assertEqual(job["status"], "failed")
        self.assertIn("session_retry_budget_exceeded", job["stop_reason"])
        self.assertEqual(
            job["failure_category"], "unknown_unclassified_non_retryable"
        )
        orch_audits = [
            json.loads(line)
            for line in (self.temp_root / "orchestrator-events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        orch_event_types = [item["event_type"] for item in orch_audits]
        self.assertIn("recording_job_failed", orch_event_types)


class RecorderCookieExpiredEmitTest(unittest.TestCase):
    """Recorder-side cookie_expired_for_<platform> gating: emit only on 403 AND
    when the operator opted into cookie-based auth for that platform."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.temp_root = root / "tmp"
        self.raw_root = root / "raw"
        self.processed_root = root / "processed"
        self.export_root = root / "exports"
        self.orchestrator_state_path = self.temp_root / "orchestrator-state.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ----- helpers -----

    def _settings(
        self,
        *,
        douyin_cookie: str = "",
        bilibili_sessdata: str = "",
        bilibili_room_url: str = "https://live.bilibili.com/12345",
        include_bilibili: bool = False,
    ) -> Settings:
        douyin = DouyinSettings(cookie=douyin_cookie)
        platforms: list = [douyin]
        if include_bilibili:
            platforms.append(
                BilibiliSettings(
                    room_url=bilibili_room_url,
                    streamer_name="bili-streamer",
                    sessdata=bilibili_sessdata,
                )
            )
        return Settings(
            douyin=douyin,
            platforms=platforms,
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
            recording=RecordingSettings(
                enable_ffmpeg=True,
                ffmpeg_max_retries=2,
                direct_stream_timeout_seconds=5,
                auto_retry_max_attempts=0,  # avoid cross-run retry noise for 4xx
                session_retry_budget=3,
                stderr_retain_count=4,
            ),
            subtitles=SubtitleSettings(enabled=True),
            export=ExportSettings(enable_ffmpeg=False),
        )

    def _seed_state(
        self,
        *,
        platform: str,
        session_id: str,
        job_id: str,
        source_type: SourceType = SourceType.DIRECT_STREAM,
        room_url: str | None = None,
    ) -> None:
        started_at = datetime(2026, 5, 12, 1, 0, tzinfo=timezone.utc)
        resolved_room_url = room_url or f"https://live.{platform}.example/room"
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id=session_id,
                    streamer_name="streamer-x",
                    room_url=resolved_room_url,
                    platform=platform,
                    source_type=source_type,
                    stream_url="https://example.invalid/live.m3u8",
                    status=SessionStatus.STOPPED,
                    started_at=started_at,
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id=job_id,
                    session_id=session_id,
                    platform=platform,
                    source_type=source_type,
                    stream_url="https://example.invalid/live.m3u8",
                    status=RecordingJobStatus.STOPPED,
                    created_at=started_at,
                )
            ],
        )
        self.orchestrator_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.orchestrator_state_path.write_text(
            state.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )

    def _audit_payloads(self) -> list[dict]:
        path = self.temp_root / "recorder-events.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _run_with_ffmpeg_error(
        self, settings: Settings, error: BaseException
    ) -> int:
        with patch(
            "arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"
        ), patch(
            "arl.recorder.service.subprocess.run", side_effect=error
        ) as mocked_run:
            RecorderService(settings).run()
        return mocked_run.call_count

    # ----- emit cases -----

    def test_403_with_douyin_cookie_configured_emits_cookie_expired(self) -> None:
        settings = self._settings(douyin_cookie="sessionid=abc")
        self._seed_state(platform="douyin", session_id="s-1", job_id="j-1")
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="[https] HTTP error 403 Forbidden"
        )
        self._run_with_ffmpeg_error(settings, error)
        events = self._audit_payloads()
        event_types = [item["event_type"] for item in events]
        self.assertIn("ffmpeg_record_failed", event_types)
        self.assertIn("cookie_expired_for_douyin", event_types)
        cookie_event = next(
            item for item in events if item["event_type"] == "cookie_expired_for_douyin"
        )
        # cookie_expired carries no decision/failure_category — it's informational.
        self.assertIsNone(cookie_event.get("decision"))
        self.assertIsNone(cookie_event.get("failure_category"))
        self.assertEqual(cookie_event["session_id"], "s-1")
        self.assertEqual(cookie_event["job_id"], "j-1")
        self.assertIn("403", cookie_event["reason"])

    def test_403_with_douyin_cookie_empty_skips_cookie_expired(self) -> None:
        # Operator never opted into cookie-based auth → 403 stays a plain
        # generic 4xx failure, no cookie suspicion signal.
        settings = self._settings(douyin_cookie="")
        self._seed_state(platform="douyin", session_id="s-2", job_id="j-2")
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Server returned 403 Forbidden"
        )
        self._run_with_ffmpeg_error(settings, error)
        events = self._audit_payloads()
        event_types = [item["event_type"] for item in events]
        self.assertIn("ffmpeg_record_failed", event_types)
        self.assertNotIn("cookie_expired_for_douyin", event_types)

    def test_403_with_bilibili_fresh_probe_refreshes_stream_url(self) -> None:
        settings = self._settings(
            bilibili_sessdata="sessdata-token", include_bilibili=True
        )
        self._seed_state(
            platform="bilibili",
            session_id="s-3",
            job_id="j-3",
            room_url="https://live.bilibili.com/12345",
        )
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="HTTP 403 Forbidden"
        )
        refreshed_snapshot = AgentSnapshot(
            state=LiveState.LIVE,
            streamer_name="bili-streamer",
            room_url="https://live.bilibili.com/12345",
            source_type=SourceType.DIRECT_STREAM,
            stream_url="https://refreshed.example/live.m3u8?token=new",
            stream_headers={"Referer": "https://live.bilibili.com"},
            reason="api_live_with_stream_url",
            detected_at=datetime(2026, 5, 12, 1, 1, tzinfo=timezone.utc),
            platform="bilibili",
        )
        with patch(
            "arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"
        ), patch(
            "arl.recorder.service.BilibiliRoomProbe"
        ) as mocked_probe_class, patch(
            "arl.recorder.service.subprocess.run",
            side_effect=[
                error,
                subprocess.CompletedProcess(args=["ffmpeg"], returncode=0),
            ],
        ) as mocked_run:
            probe = mocked_probe_class.return_value
            probe.detect.return_value = refreshed_snapshot
            probe.classify_cookie_state.return_value = CookieState.FRESH
            RecorderService(settings).run()

        self.assertEqual(mocked_run.call_count, 2)
        second_command = list(mocked_run.call_args_list[1].args[0])
        self.assertEqual(
            second_command[second_command.index("-i") + 1],
            "https://refreshed.example/live.m3u8?token=new",
        )
        events = self._audit_payloads()
        event_types = [item["event_type"] for item in events]
        self.assertEqual(
            event_types,
            [
                "ffmpeg_record_failed",
                "stream_url_expired_for_bilibili",
                "ffmpeg_record_succeeded",
            ],
        )
        self.assertNotIn("cookie_expired_for_bilibili", event_types)
        stream_event = next(
            item
            for item in events
            if item["event_type"] == "stream_url_expired_for_bilibili"
        )
        self.assertEqual(stream_event["reason"], "refreshed_stream_url_after_403")

    def test_403_with_bilibili_sessdata_expired_emits_cookie_expired(self) -> None:
        settings = self._settings(
            bilibili_sessdata="sessdata-token", include_bilibili=True
        )
        self._seed_state(
            platform="bilibili",
            session_id="s-3-expired",
            job_id="j-3-expired",
            room_url="https://live.bilibili.com/12345",
        )
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="HTTP 403 Forbidden"
        )
        expired_snapshot = AgentSnapshot(
            state=LiveState.OFFLINE,
            streamer_name="bili-streamer",
            room_url="https://live.bilibili.com/12345",
            reason="api_error:code=-101:account_not_logged_in",
            detected_at=datetime(2026, 5, 12, 1, 1, tzinfo=timezone.utc),
            platform="bilibili",
        )
        with patch(
            "arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"
        ), patch(
            "arl.recorder.service.BilibiliRoomProbe"
        ) as mocked_probe_class, patch(
            "arl.recorder.service.subprocess.run", side_effect=error
        ) as mocked_run:
            probe = mocked_probe_class.return_value
            probe.detect.return_value = expired_snapshot
            probe.classify_cookie_state.return_value = CookieState.EXPIRED
            RecorderService(settings).run()

        self.assertEqual(mocked_run.call_count, 1)
        events = self._audit_payloads()
        event_types = [item["event_type"] for item in events]
        self.assertIn("ffmpeg_record_failed", event_types)
        self.assertIn("cookie_expired_for_bilibili", event_types)
        self.assertNotIn("stream_url_expired_for_bilibili", event_types)
        cookie_event = next(
            item
            for item in events
            if item["event_type"] == "cookie_expired_for_bilibili"
        )
        self.assertIn("sessdata_expired", cookie_event["reason"])

    def test_403_with_bilibili_refresh_failure_emits_stream_url_event(self) -> None:
        settings = self._settings(
            bilibili_sessdata="sessdata-token", include_bilibili=True
        )
        self._seed_state(
            platform="bilibili",
            session_id="s-3-refresh-failed",
            job_id="j-3-refresh-failed",
            room_url="https://live.bilibili.com/12345",
        )
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="HTTP 403 Forbidden"
        )
        no_direct_snapshot = AgentSnapshot(
            state=LiveState.LIVE,
            streamer_name="bili-streamer",
            room_url="https://live.bilibili.com/12345",
            source_type=SourceType.BROWSER_CAPTURE,
            stream_url=None,
            reason="stream_url_missing",
            detected_at=datetime(2026, 5, 12, 1, 1, tzinfo=timezone.utc),
            platform="bilibili",
        )
        with patch(
            "arl.recorder.service.shutil.which", return_value="/usr/bin/ffmpeg"
        ), patch(
            "arl.recorder.service.BilibiliRoomProbe"
        ) as mocked_probe_class, patch(
            "arl.recorder.service.subprocess.run", side_effect=error
        ) as mocked_run:
            probe = mocked_probe_class.return_value
            probe.detect.return_value = no_direct_snapshot
            probe.classify_cookie_state.return_value = CookieState.FRESH
            RecorderService(settings).run()

        self.assertEqual(mocked_run.call_count, 1)
        events = self._audit_payloads()
        event_types = [item["event_type"] for item in events]
        self.assertIn("ffmpeg_record_failed", event_types)
        self.assertIn("stream_url_expired_for_bilibili", event_types)
        self.assertNotIn("cookie_expired_for_bilibili", event_types)
        stream_event = next(
            item
            for item in events
            if item["event_type"] == "stream_url_expired_for_bilibili"
        )
        self.assertEqual(stream_event["reason"], "refresh_failed:stream_url_missing")

    def test_403_with_bilibili_sessdata_empty_skips_cookie_expired(self) -> None:
        settings = self._settings(bilibili_sessdata="", include_bilibili=True)
        self._seed_state(platform="bilibili", session_id="s-4", job_id="j-4")
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Server returned 403"
        )
        self._run_with_ffmpeg_error(settings, error)
        events = self._audit_payloads()
        event_types = [item["event_type"] for item in events]
        self.assertNotIn("cookie_expired_for_bilibili", event_types)

    def test_404_with_douyin_cookie_configured_skips_cookie_expired(self) -> None:
        # 404 is the "stream really gone" signal, not a cookie suspect — must
        # NOT trigger cookie_expired even when cookie is configured.
        settings = self._settings(douyin_cookie="sessionid=abc")
        self._seed_state(platform="douyin", session_id="s-5", job_id="j-5")
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Server returned 404 Not Found"
        )
        self._run_with_ffmpeg_error(settings, error)
        events = self._audit_payloads()
        event_types = [item["event_type"] for item in events]
        self.assertIn("ffmpeg_record_failed", event_types)
        self.assertNotIn("cookie_expired_for_douyin", event_types)

    def test_503_with_douyin_cookie_configured_skips_cookie_expired(self) -> None:
        # Transient 5xx is unrelated to cookie state.
        settings = self._settings(douyin_cookie="sessionid=abc")
        self._seed_state(platform="douyin", session_id="s-6", job_id="j-6")
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Server returned 503 Service Unavailable"
        )
        self._run_with_ffmpeg_error(settings, error)
        events = self._audit_payloads()
        event_types = [item["event_type"] for item in events]
        self.assertIn("ffmpeg_record_failed", event_types)
        self.assertNotIn("cookie_expired_for_douyin", event_types)

    def test_browser_capture_403_with_douyin_cookie_emits_cookie_expired(self) -> None:
        # The browser-capture ffmpeg path goes through a different inner method
        # but the cookie-gating helper is shared. A configured cookie should
        # still produce the signal if ffmpeg returns 403 in that path.
        settings_overrides = self._settings(douyin_cookie="sessionid=abc")
        # Override source_type to BROWSER_CAPTURE and ensure the recorder picks
        # the browser-capture branch by providing a configured capture input.
        new_settings = settings_overrides.model_copy(deep=True)
        new_settings.recording.browser_capture_format = "gdigrab"
        new_settings.recording.browser_capture_input = "desktop"
        self._seed_state(
            platform="douyin",
            session_id="s-7",
            job_id="j-7",
            source_type=SourceType.BROWSER_CAPTURE,
        )
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="HTTP 403 Forbidden"
        )
        self._run_with_ffmpeg_error(new_settings, error)
        events = self._audit_payloads()
        event_types = [item["event_type"] for item in events]
        self.assertIn("ffmpeg_record_failed", event_types)
        self.assertIn("cookie_expired_for_douyin", event_types)


class ExporterFfmpegAuditTest(unittest.TestCase):
    """Coverage for exporter ffmpeg failure observability parity with recorder.

    Recorder already emits canonical decision fields + stderr_excerpt +
    stderr_log_path on every ffmpeg failure. PR2 of the shared-ffmpeg-helper
    task extends the same shape to exporter via exporter-events.jsonl +
    exporter-stderr/<session>_match<idx>-<attempt>.log.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.temp_root = root / "tmp"
        self.raw_root = root / "raw"
        self.processed_root = root / "processed"
        self.export_root = root / "exports"
        self.settings = Settings(
            douyin=DouyinSettings(event_log_path=self.temp_root / "windows-agent-events.jsonl"),
            storage=StorageSettings(
                raw_dir=self.raw_root,
                processed_dir=self.processed_root,
                export_dir=self.export_root,
                temp_dir=self.temp_root,
            ),
            orchestrator=OrchestratorSettings(
                state_file=self.temp_root / "orchestrator-state.json",
                agent_event_log_path=self.temp_root / "windows-agent-events.jsonl",
                recorder_event_log_path=self.temp_root / "recorder-events.jsonl",
                audit_log_path=self.temp_root / "orchestrator-events.jsonl",
            ),
            recording=RecordingSettings(enable_ffmpeg=False),
            subtitles=SubtitleSettings(enabled=True),
            export=ExportSettings(
                enable_ffmpeg=True,
                ffmpeg_max_retries=1,
                ffmpeg_timeout_seconds=10,
                stderr_retain_count=4,
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ----- helpers -----

    def _seed_export_inputs(
        self,
        *,
        session_id: str = "session-e",
        match_index: int = 1,
        platform: str = "douyin",
        placeholder_subtitle: bool = False,
        boundary_ended_at_seconds: float = 60.0,
        boundary_confidence: float = 0.9,
        boundary_is_complete: bool = True,
        boundary_reason: str | None = None,
    ) -> None:
        started_at = datetime(2026, 5, 12, 1, 0, tzinfo=timezone.utc)
        ended_at = datetime(2026, 5, 12, 1, 10, tzinfo=timezone.utc)
        boundary = MatchBoundary(
            session_id=session_id,
            match_index=match_index,
            started_at_seconds=0.0,
            ended_at_seconds=boundary_ended_at_seconds,
            confidence=boundary_confidence,
            is_complete=boundary_is_complete,
            reason=boundary_reason,
        )
        subtitle_file = self.processed_root / session_id / f"match-{match_index:02d}.srt"
        subtitle_file.parent.mkdir(parents=True, exist_ok=True)
        if placeholder_subtitle:
            subtitle_text = (
                "1\n"
                "00:00:00,000 --> 00:00:03,000\n"
                "Placeholder subtitle generated by local pipeline.\n"
            )
        else:
            subtitle_text = "1\n00:00:00,000 --> 00:00:01,000\nhello\n"
        subtitle_file.write_text(subtitle_text, encoding="utf-8")
        subtitle = SubtitleAsset(
            session_id=session_id,
            match_index=match_index,
            path=str(subtitle_file),
            format="srt",
        )
        recording_file = self.raw_root / session_id / "recording-source.mp4"
        recording_file.parent.mkdir(parents=True, exist_ok=True)
        recording_file.write_text("not-a-real-video", encoding="utf-8")
        recording = RecordingAsset(
            session_id=session_id,
            source_type=SourceType.DIRECT_STREAM,
            path=str(recording_file),
            started_at=started_at,
            ended_at=ended_at,
        )
        append_model(self.temp_root / "match-boundaries.jsonl", boundary)
        append_model(self.temp_root / "subtitle-assets.jsonl", subtitle)
        append_model(self.temp_root / "recording-assets.jsonl", recording)
        self.settings.orchestrator.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings.orchestrator.state_file.write_text(
            OrchestratorStateFile(
                sessions=[
                    SessionRecord(
                        session_id=session_id,
                        streamer_name=f"{platform}-streamer",
                        room_url=f"https://live.example/{platform}",
                        platform=platform,
                        source_type=SourceType.DIRECT_STREAM,
                        stream_url=f"https://media.example/{platform}.m3u8",
                        status=SessionStatus.STOPPED,
                        started_at=started_at,
                        ended_at=ended_at,
                    )
                ],
                recording_jobs=[
                    RecordingJobRecord(
                        job_id=f"job-{session_id}",
                        session_id=session_id,
                        platform=platform,
                        source_type=SourceType.DIRECT_STREAM,
                        stream_url=f"https://media.example/{platform}.m3u8",
                        status=RecordingJobStatus.STOPPED,
                        created_at=started_at,
                        ended_at=ended_at,
                    )
                ],
            ).model_dump_json(indent=2)
            + "\n",
            encoding="utf-8",
        )

    def _audit_payloads(self) -> list[dict]:
        path = self.temp_root / "exporter-events.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _fake_successful_export_run(self, command, **kwargs):
        if command[0].endswith("ffprobe"):
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "width": 1920,
                                "height": 1080,
                            }
                        ],
                        "format": {"duration": "60.0", "size": "12345"},
                    }
                ),
            )
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("fake exported video", encoding="utf-8")
        return subprocess.CompletedProcess(args=command, returncode=0)

    def _which_ffmpeg_and_ffprobe(self, binary: str) -> str | None:
        if binary in {"ffmpeg", "ffprobe"}:
            return f"/usr/bin/{binary}"
        return None

    # ----- tests -----

    def test_low_confidence_full_recording_boundary_is_deferred(self) -> None:
        self._seed_export_inputs(
            session_id="session-low-confidence-full",
            boundary_ended_at_seconds=600.0,
            boundary_confidence=0.5,
        )

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(self.settings).run()

        self.assertEqual(mocked_run.call_count, 0)
        self.assertFalse((self.temp_root / "export-assets.jsonl").exists())
        self.assertFalse(
            (
                self.export_root
                / "douyin"
                / "session-low-confidence-full_match01.mp4"
            ).exists()
        )
        self.assertFalse(
            (
                self.export_root
                / "douyin"
                / "session-low-confidence-full_match01.txt"
            ).exists()
        )

    def test_incomplete_match_boundary_is_not_exported(self) -> None:
        self._seed_export_inputs(
            session_id="session-incomplete",
            boundary_confidence=0.95,
            boundary_is_complete=False,
            boundary_reason="incomplete_no_end",
        )

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(self.settings).run()

        self.assertEqual(mocked_run.call_count, 0)
        self.assertFalse((self.temp_root / "export-assets.jsonl").exists())
        self.assertFalse(
            (self.export_root / "douyin" / "session-incomplete_match01.mp4").exists()
        )

    def test_failure_emits_canonical_audit_with_stderr_excerpt_and_log_path(self) -> None:
        self._seed_export_inputs()
        # Helper extracts the LAST stderr line as the failure reason, so the
        # 4xx marker must land on the last line for classification to fire.
        long_stderr = (
            "\n".join([f"trace-line-{idx:02d}" for idx in range(30)])
            + "\nServer returned 404 Not Found"
        )
        error = subprocess.CalledProcessError(1, ["ffmpeg"], stderr=long_stderr)
        with patch("arl.exporter.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=error,
        ):
            ExporterService(self.settings).run()

        payloads = self._audit_payloads()
        failed_rows = [item for item in payloads if item["event_type"] == "ffmpeg_export_failed"]
        # Non-retryable (http_4xx) short-circuits the attempt loop; see
        # 05-13-exporter-ffmpeg-failure-alignment.
        self.assertEqual(len(failed_rows), 1)
        row = failed_rows[0]
        self.assertEqual(row["session_id"], "session-e")
        self.assertEqual(row["match_index"], 1)
        self.assertEqual(row["attempt"], 1)
        self.assertEqual(row["max_attempts"], 2)
        self.assertEqual(row["decision"], "attempt_failed")
        self.assertEqual(row["failure_category"], "http_4xx_non_retryable")
        self.assertFalse(row["is_retryable"])
        self.assertEqual(row["reason_code"], "http_4xx")
        self.assertIn("404", row["reason_detail"])
        self.assertIsNotNone(row["stderr_excerpt"])
        self.assertIn("404", row["stderr_excerpt"])
        self.assertIsNotNone(row["stderr_log_path"])
        self.assertTrue(Path(row["stderr_log_path"]).exists())
        self.assertTrue(row["stderr_log_path"].endswith("session-e_match01-1.log"))

        placeholder_rows = [
            item
            for item in payloads
            if item["event_type"] == "ffmpeg_export_fallback_placeholder"
        ]
        self.assertEqual(len(placeholder_rows), 1)
        self.assertEqual(placeholder_rows[0]["decision"], "fallback_placeholder")
        # placeholder row inherits the last failure's classification so operators
        # can see what root-caused the exhaustion.
        self.assertEqual(placeholder_rows[0]["failure_category"], "http_4xx_non_retryable")
        self.assertEqual(placeholder_rows[0]["reason_code"], "http_4xx")

        self.assertFalse((self.temp_root / "export-assets.jsonl").exists())
        state = ExporterStateFile.model_validate_json(
            (self.temp_root / "exporter-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state.deferred_match_keys, ["session-e:1"])

    def test_non_retryable_short_circuits_with_max_retries_five(self) -> None:
        new_settings = self.settings.model_copy(deep=True)
        new_settings.export.ffmpeg_max_retries = 5
        self._seed_export_inputs()
        error = subprocess.CalledProcessError(
            1,
            ["ffmpeg"],
            stderr="Server returned 404 Not Found",
        )
        with patch("arl.exporter.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=error,
        ) as mocked_run:
            ExporterService(new_settings).run()

        self.assertEqual(mocked_run.call_count, 1)
        payloads = self._audit_payloads()
        failed_rows = [
            item for item in payloads if item["event_type"] == "ffmpeg_export_failed"
        ]
        placeholder_rows = [
            item
            for item in payloads
            if item["event_type"] == "ffmpeg_export_fallback_placeholder"
        ]
        self.assertEqual(len(failed_rows), 1)
        self.assertEqual(len(placeholder_rows), 1)
        self.assertEqual(failed_rows[0]["attempt"], 1)
        self.assertEqual(failed_rows[0]["max_attempts"], 6)
        self.assertFalse(failed_rows[0]["is_retryable"])
        self.assertEqual(placeholder_rows[0]["reason_code"], "http_4xx")

    def test_success_emits_succeeded_audit_without_stderr_fields(self) -> None:
        self._seed_export_inputs()
        def _which(binary: str) -> str | None:
            if binary in {"ffmpeg", "ffprobe"}:
                return f"/usr/bin/{binary}"
            return None

        def _fake_run(command, **kwargs):
            if command[0].endswith("ffprobe"):
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "streams": [
                                {
                                    "codec_type": "video",
                                    "width": 1920,
                                    "height": 1080,
                                }
                            ],
                            "format": {"duration": "60.0", "size": "12345"},
                        }
                    ),
                )
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("fake exported video", encoding="utf-8")
            return subprocess.CompletedProcess(args=command, returncode=0)

        with patch("arl.exporter.service.shutil.which", side_effect=_which), patch(
            "arl.exporter.service.subprocess.run", side_effect=_fake_run
        ) as mocked_run:
            ExporterService(self.settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        subtitle_path = self.processed_root / "session-e" / "match-01.srt"
        self.assertNotIn("-vf", command)
        self.assertIn("-to", command)
        self.assertEqual(command[command.index("-to") + 1], "60.0")
        self.assertIn(str(subtitle_path.resolve()), command)
        self.assertIn("-c:v", command)
        self.assertEqual(command[command.index("-c:v") + 1], "copy")
        self.assertIn("-c:a", command)
        self.assertEqual(command[command.index("-c:a") + 1], "copy")
        self.assertIn("-c:s", command)
        self.assertEqual(command[command.index("-c:s") + 1], "mov_text")
        payloads = self._audit_payloads()
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["event_type"], "ffmpeg_export_succeeded")
        self.assertEqual(payloads[0]["session_id"], "session-e")
        self.assertEqual(payloads[0]["match_index"], 1)
        self.assertIsNone(payloads[0]["stderr_excerpt"])
        self.assertIsNone(payloads[0]["stderr_log_path"])
        # Success row carries no canonical decision fields (mirrors ffmpeg_record_succeeded).
        self.assertIsNone(payloads[0]["decision"])
        self.assertIsNone(payloads[0]["failure_category"])

    def test_h265_export_with_subtitles_uses_libx265(self) -> None:
        self._seed_export_inputs()
        settings = self.settings.model_copy(deep=True)
        settings.export.ffmpeg_video_codec = "h265"

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        self.assertNotIn("-vf", command)
        self.assertIn("-c:v", command)
        self.assertEqual(command[command.index("-c:v") + 1], "libx265")
        self.assertIn("-tag:v", command)
        self.assertEqual(command[command.index("-tag:v") + 1], "hvc1")

    def test_export_can_burn_subtitles_when_enabled(self) -> None:
        self._seed_export_inputs()
        settings = self.settings.model_copy(deep=True)
        settings.export.burn_subtitles = True

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        subtitle_path = (
            self.processed_root / "session-e" / "match-01.srt"
        ).resolve().as_posix().replace(":", "\\:")
        self.assertIn("-vf", command)
        self.assertEqual(command[command.index("-vf") + 1], f"subtitles='{subtitle_path}'")
        self.assertIn("-c:v", command)
        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertIn("-crf", command)
        self.assertEqual(command[command.index("-crf") + 1], "18")
        self.assertIn("-c:a", command)
        self.assertEqual(command[command.index("-c:a") + 1], "copy")

    def test_export_can_burn_ass_subtitles_when_enabled(self) -> None:
        self._seed_export_inputs()
        settings = self.settings.model_copy(deep=True)
        settings.export.burn_subtitles = True
        settings.export.use_ass_subtitles = True
        settings.export.ass_font_name = "Microsoft YaHei"
        settings.export.ass_font_size = 42
        settings.export.ass_margin_v = 18
        settings.export.ass_outline = 3

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        ass_path = (self.processed_root / "session-e" / "match-01.ass").resolve()
        expected_filter_path = ass_path.as_posix().replace(":", "\\:")
        self.assertIn("-vf", command)
        self.assertEqual(
            command[command.index("-vf") + 1],
            f"subtitles='{expected_filter_path}'",
        )

        ass_text = ass_path.read_text(encoding="utf-8")
        self.assertIn("[V4+ Styles]", ass_text)
        self.assertIn(
            "Style: Default,Microsoft YaHei,42,"
            "&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
            "0,0,0,0,100,100,0,0,1,3,0,2,20,20,18,1",
            ass_text,
        )
        self.assertIn(
            "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello",
            ass_text,
        )

    def test_export_can_disable_subtitle_burn_in(self) -> None:
        self._seed_export_inputs()
        settings = self.settings.model_copy(deep=True)
        settings.export.burn_subtitles = False

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        self.assertNotIn("-vf", command)
        self.assertIn("-map", command)
        self.assertEqual(command[command.index("-map") + 1], "0:v?")
        self.assertIn("-c:v", command)
        self.assertEqual(command[command.index("-c:v") + 1], "copy")
        self.assertIn("-c:s", command)
        self.assertEqual(command[command.index("-c:s") + 1], "mov_text")

    def test_highlight_plan_export_uses_select_filters(self) -> None:
        self._seed_export_inputs(boundary_ended_at_seconds=120.0)
        settings = self.settings.model_copy(deep=True)
        settings.export.use_highlight_plans = True
        settings.export.burn_subtitles = True
        append_model(
            self.temp_root / "highlight-plans.jsonl",
            HighlightPlanAsset(
                session_id="session-e",
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=120.0,
                windows=[
                    HighlightClipWindow(
                        started_at_seconds=0.0,
                        ended_at_seconds=30.0,
                        reason="narration",
                    ),
                    HighlightClipWindow(
                        started_at_seconds=90.0,
                        ended_at_seconds=120.0,
                        reason="highlight_keyword",
                    ),
                ],
                created_at=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
            ),
        )

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        self.assertIn("-t", command)
        self.assertEqual(command[command.index("-t") + 1], "120.0")
        self.assertNotIn("-to", command)
        video_filter = command[command.index("-vf") + 1]
        audio_filter = command[command.index("-af") + 1]
        self.assertIn("subtitles=", video_filter)
        self.assertIn("select='between(t,0.000,30.000)+between(t,90.000,120.000)'", video_filter)
        self.assertIn("setpts=N/FRAME_RATE/TB", video_filter)
        self.assertIn("aselect='between(t,0.000,30.000)+between(t,90.000,120.000)'", audio_filter)
        self.assertIn("asetpts=N/SR/TB", audio_filter)

    def test_highlight_plan_export_can_disable_subtitle_burn_in(self) -> None:
        self._seed_export_inputs(boundary_ended_at_seconds=120.0)
        settings = self.settings.model_copy(deep=True)
        settings.export.use_highlight_plans = True
        settings.export.burn_subtitles = False
        append_model(
            self.temp_root / "highlight-plans.jsonl",
            HighlightPlanAsset(
                session_id="session-e",
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=120.0,
                windows=[
                    HighlightClipWindow(
                        started_at_seconds=0.0,
                        ended_at_seconds=30.0,
                        reason="narration",
                    ),
                    HighlightClipWindow(
                        started_at_seconds=90.0,
                        ended_at_seconds=120.0,
                        reason="highlight_keyword",
                    ),
                ],
                created_at=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
            ),
        )

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        video_filter = command[command.index("-vf") + 1]
        self.assertNotIn("subtitles=", video_filter)
        self.assertIn("select='between(t,0.000,30.000)+between(t,90.000,120.000)'", video_filter)
        self.assertIn("setpts=N/FRAME_RATE/TB", video_filter)

    def test_highlight_plan_is_ignored_by_default_for_full_match_export(self) -> None:
        self._seed_export_inputs(boundary_ended_at_seconds=120.0)
        append_model(
            self.temp_root / "highlight-plans.jsonl",
            HighlightPlanAsset(
                session_id="session-e",
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=120.0,
                windows=[
                    HighlightClipWindow(
                        started_at_seconds=0.0,
                        ended_at_seconds=30.0,
                        reason="narration",
                    ),
                    HighlightClipWindow(
                        started_at_seconds=90.0,
                        ended_at_seconds=120.0,
                        reason="highlight_keyword",
                    ),
                ],
                created_at=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
            ),
        )

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(self.settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        self.assertIn("-to", command)
        self.assertEqual(command[command.index("-to") + 1], "120.0")
        self.assertNotIn("-t", command)
        self.assertNotIn("-vf", command)
        self.assertNotIn("-af", command)

    def test_visual_only_highlight_plan_is_ignored(self) -> None:
        self._seed_export_inputs(boundary_ended_at_seconds=120.0)
        settings = self.settings.model_copy(deep=True)
        settings.export.use_highlight_plans = True
        append_model(
            self.temp_root / "highlight-plans.jsonl",
            HighlightPlanAsset(
                session_id="session-e",
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=120.0,
                windows=[
                    HighlightClipWindow(
                        started_at_seconds=0.0,
                        ended_at_seconds=30.0,
                        reason="condensed_visual_activity",
                    )
                ],
                created_at=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
            ),
        )

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        self.assertIn("-to", command)
        self.assertEqual(command[command.index("-to") + 1], "120.0")
        self.assertNotIn("-vf", command)
        self.assertNotIn("-af", command)

    def test_incomplete_condensed_highlight_plan_is_ignored(self) -> None:
        self._seed_export_inputs(boundary_ended_at_seconds=120.0)
        settings = self.settings.model_copy(deep=True)
        settings.export.use_highlight_plans = True
        append_model(
            self.temp_root / "highlight-plans.jsonl",
            HighlightPlanAsset(
                session_id="session-e",
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=120.0,
                windows=[
                    HighlightClipWindow(
                        started_at_seconds=45.0,
                        ended_at_seconds=90.0,
                        reason="condensed_key_event",
                    )
                ],
                created_at=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
            ),
        )

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        self.assertIn("-to", command)
        self.assertEqual(command[command.index("-to") + 1], "120.0")
        self.assertNotIn("-vf", command)
        self.assertNotIn("-af", command)

    def test_full_span_highlight_plan_is_ignored(self) -> None:
        self._seed_export_inputs(boundary_ended_at_seconds=120.0)
        settings = self.settings.model_copy(deep=True)
        settings.export.use_highlight_plans = True
        append_model(
            self.temp_root / "highlight-plans.jsonl",
            HighlightPlanAsset(
                session_id="session-e",
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=120.0,
                windows=[
                    HighlightClipWindow(
                        started_at_seconds=0.0,
                        ended_at_seconds=120.0,
                        reason="condensed_match_context",
                    )
                ],
                created_at=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
            ),
        )

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        self.assertIn("-to", command)
        self.assertEqual(command[command.index("-to") + 1], "120.0")
        self.assertNotIn("-vf", command)
        self.assertNotIn("-af", command)

    def test_export_path_uses_platform_subdirectory(self) -> None:
        self._seed_export_inputs(platform="bilibili")

        def _which(binary: str) -> str | None:
            if binary in {"ffmpeg", "ffprobe"}:
                return f"/usr/bin/{binary}"
            return None

        def _fake_run(command, **kwargs):
            if command[0].endswith("ffprobe"):
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "streams": [
                                {
                                    "codec_type": "video",
                                    "width": 1920,
                                    "height": 1080,
                                }
                            ],
                            "format": {"duration": "60.0", "size": "12345"},
                        }
                    ),
                )
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("fake exported video", encoding="utf-8")
            return subprocess.CompletedProcess(args=command, returncode=0)

        with patch("arl.exporter.service.shutil.which", side_effect=_which), patch(
            "arl.exporter.service.subprocess.run", side_effect=_fake_run
        ):
            ExporterService(self.settings).run()

        export_row = json.loads(
            (self.temp_root / "export-assets.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        expected_path = self.export_root / "bilibili" / "session-e_match01.mp4"
        self.assertEqual(Path(export_row["path"]), expected_path)
        self.assertTrue(expected_path.exists())
        self.assertFalse((self.export_root / "session-e_match01.mp4").exists())

    def test_export_path_uses_selected_recording_platform_state(self) -> None:
        self._seed_export_inputs(session_id="session-selected", platform="douyin")
        self.settings.orchestrator.state_file.write_text(
            OrchestratorStateFile().model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        selected_state_path = (
            self.temp_root
            / "selected-recordings"
            / "run-20260606"
            / "orchestrator-state.json"
        )
        selected_state_path.parent.mkdir(parents=True, exist_ok=True)
        started_at = datetime(2026, 5, 12, 1, 0, tzinfo=timezone.utc)
        selected_state_path.write_text(
            OrchestratorStateFile(
                sessions=[
                    SessionRecord(
                        session_id="session-selected",
                        streamer_name="bilibili-streamer",
                        room_url="https://live.bilibili.com/1",
                        platform="bilibili",
                        source_type=SourceType.DIRECT_STREAM,
                        stream_url="https://media.example/bilibili.m3u8",
                        status=SessionStatus.STOPPED,
                        started_at=started_at,
                        ended_at=started_at,
                    )
                ],
                recording_jobs=[
                    RecordingJobRecord(
                        job_id="job-session-selected",
                        session_id="session-selected",
                        platform="bilibili",
                        source_type=SourceType.DIRECT_STREAM,
                        stream_url="https://media.example/bilibili.m3u8",
                        status=RecordingJobStatus.STOPPED,
                        created_at=started_at,
                        ended_at=started_at,
                    )
                ],
            ).model_dump_json(indent=2)
            + "\n",
            encoding="utf-8",
        )

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ):
            ExporterService(self.settings).run()

        export_row = json.loads(
            (self.temp_root / "export-assets.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        expected_path = (
            self.export_root / "bilibili" / "session-selected_match01.mp4"
        )
        self.assertEqual(Path(export_row["path"]), expected_path)
        self.assertTrue(expected_path.exists())
        self.assertFalse(
            (self.export_root / "unknown" / "session-selected_match01.mp4").exists()
        )

    def test_ffmpeg_fallback_removes_partial_mp4(self) -> None:
        self._seed_export_inputs()

        def _fake_timeout(command, **kwargs):
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("partial mp4 shell", encoding="utf-8")
            raise subprocess.TimeoutExpired(command, timeout=10)

        with patch("arl.exporter.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=_fake_timeout,
        ):
            ExporterService(self.settings).run()

        partial_path = self.export_root / "douyin" / "session-e_match01.mp4"
        self.assertFalse(partial_path.exists())
        self.assertFalse((self.temp_root / "export-assets.jsonl").exists())
        state = ExporterStateFile.model_validate_json(
            (self.temp_root / "exporter-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state.deferred_match_keys, ["session-e:1"])

    def test_placeholder_subtitle_uses_stream_copy_export(self) -> None:
        self._seed_export_inputs(placeholder_subtitle=True)
        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(self.settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        self.assertNotIn("-vf", command)
        self.assertIn("-map", command)
        self.assertEqual(command[command.index("-map") + 1], "0")
        self.assertIn("-c", command)
        self.assertEqual(command[command.index("-c") + 1], "copy")
        self.assertIn("-movflags", command)
        self.assertEqual(command[command.index("-movflags") + 1], "+faststart")

    def test_placeholder_subtitle_does_not_generate_ass_when_burn_enabled(self) -> None:
        self._seed_export_inputs(placeholder_subtitle=True)
        settings = self.settings.model_copy(deep=True)
        settings.export.burn_subtitles = True
        settings.export.use_ass_subtitles = True

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        self.assertNotIn("-vf", command)
        self.assertFalse((self.processed_root / "session-e" / "match-01.ass").exists())

    def test_h265_placeholder_subtitle_transcodes_instead_of_stream_copy(self) -> None:
        self._seed_export_inputs(placeholder_subtitle=True)
        settings = self.settings.model_copy(deep=True)
        settings.export.ffmpeg_video_codec = "h265"

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        self.assertNotIn("-vf", command)
        self.assertNotIn("-map", command)
        self.assertIn("-c:v", command)
        self.assertEqual(command[command.index("-c:v") + 1], "libx265")
        self.assertIn("-tag:v", command)
        self.assertEqual(command[command.index("-tag:v") + 1], "hvc1")

    def test_force_reprocess_can_target_one_existing_export(self) -> None:
        self._seed_export_inputs(session_id="session-force", match_index=1)
        self._seed_export_inputs(session_id="session-force", match_index=2)
        existing_path_1 = self.export_root / "douyin" / "session-force_match01.txt"
        existing_path_2 = self.export_root / "douyin" / "session-force_match02.txt"
        existing_path_1.parent.mkdir(parents=True, exist_ok=True)
        existing_path_1.write_text("existing export 1", encoding="utf-8")
        existing_path_2.write_text("existing export 2", encoding="utf-8")
        append_model(
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id="session-force",
                match_index=1,
                path=str(existing_path_1),
                subtitle_path=str(self.processed_root / "session-force" / "match-01.srt"),
                created_at=datetime(2026, 5, 12, 1, 0, tzinfo=timezone.utc),
            ),
        )
        append_model(
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id="session-force",
                match_index=2,
                path=str(existing_path_2),
                subtitle_path=str(self.processed_root / "session-force" / "match-02.srt"),
                created_at=datetime(2026, 5, 12, 1, 0, tzinfo=timezone.utc),
            ),
        )
        (self.temp_root / "exporter-state.json").write_text(
            ExporterStateFile(
                processed_match_keys=["session-force:1", "session-force:2"]
            ).model_dump_json(indent=2)
            + "\n",
            encoding="utf-8",
        )

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(self.settings).run(
                session_ids={"session-force"},
                match_indices={2},
                force_reprocess=True,
            )

        ffmpeg_commands = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ]
        self.assertEqual(len(ffmpeg_commands), 1)
        self.assertTrue(str(ffmpeg_commands[0][-1]).endswith("session-force_match02.mp4"))
        export_rows = [
            json.loads(line)
            for line in (self.temp_root / "export-assets.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        self.assertEqual(len(export_rows), 3)
        self.assertTrue(export_rows[-1]["path"].endswith("session-force_match02.mp4"))

    def test_success_exit_with_empty_video_output_falls_back(self) -> None:
        self._seed_export_inputs()

        def _which(binary: str) -> str | None:
            if binary in {"ffmpeg", "ffprobe"}:
                return f"/usr/bin/{binary}"
            return None

        def _fake_run(command, **kwargs):
            if command[0].endswith("ffprobe"):
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps({"streams": [], "format": {"size": "262"}}),
                )
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("empty mp4 shell", encoding="utf-8")
            return subprocess.CompletedProcess(args=command, returncode=0)

        with patch("arl.exporter.service.shutil.which", side_effect=_which), patch(
            "arl.exporter.service.subprocess.run", side_effect=_fake_run
        ):
            ExporterService(self.settings).run()

        payloads = self._audit_payloads()
        self.assertEqual(
            [item["event_type"] for item in payloads],
            ["ffmpeg_export_failed", "ffmpeg_export_fallback_placeholder"],
        )
        self.assertEqual(
            payloads[0]["reason_detail"],
            "ffmpeg_output_missing_video_stream",
        )
        self.assertEqual(payloads[0]["reason_code"], "unknown_unclassified")
        self.assertFalse((self.temp_root / "export-assets.jsonl").exists())
        state = ExporterStateFile.model_validate_json(
            (self.temp_root / "exporter-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state.deferred_match_keys, ["session-e:1"])
        self.assertFalse((self.export_root / "douyin" / "session-e_match01.mp4").exists())

    def test_oserror_failure_audit_handles_missing_stderr_gracefully(self) -> None:
        self._seed_export_inputs()
        with patch("arl.exporter.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=OSError("missing binary"),
        ):
            ExporterService(self.settings).run()

        payloads = self._audit_payloads()
        failed_rows = [item for item in payloads if item["event_type"] == "ffmpeg_export_failed"]
        self.assertTrue(failed_rows)
        for row in failed_rows:
            self.assertIsNone(row["stderr_excerpt"])
            self.assertIsNone(row["stderr_log_path"])
            self.assertEqual(row["reason_code"], "unknown_unclassified")

    def test_rotation_keeps_only_retain_count_newest_in_exporter_stderr(self) -> None:
        import os as _os

        stderr_dir = self.temp_root / "exporter-stderr"
        stderr_dir.mkdir(parents=True, exist_ok=True)
        retain = self.settings.export.stderr_retain_count  # 4
        seeded = retain + 5
        base = datetime(2026, 5, 11, 0, 0, tzinfo=timezone.utc).timestamp()
        for idx in range(seeded):
            entry = stderr_dir / f"old-{idx:02d}.log"
            entry.write_text(f"file-{idx}", encoding="utf-8")
            mtime = base + idx
            _os.utime(entry, (mtime, mtime))

        # shutil.which returns None → no ffmpeg invocation, but rotation still
        # fires at run() top.
        with patch("arl.exporter.service.shutil.which", return_value=None):
            ExporterService(self.settings).run()

        remaining = sorted(stderr_dir.iterdir(), key=lambda p: p.name)
        self.assertEqual(len(remaining), retain)
        expected_newest = {f"old-{idx:02d}.log" for idx in range(seeded - retain, seeded)}
        self.assertEqual({entry.name for entry in remaining}, expected_newest)

    def test_attempt_count_one_when_max_retries_zero(self) -> None:
        # ffmpeg_max_retries=0 → attempts=1 → exactly one failed row, then
        # fallback. Guards against off-by-one in the attempts calculation.
        new_settings = self.settings.model_copy(deep=True)
        new_settings.export.ffmpeg_max_retries = 0
        self._seed_export_inputs(match_index=2)
        error = subprocess.CalledProcessError(1, ["ffmpeg"], stderr="Server returned 503")
        with patch("arl.exporter.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=error,
        ):
            ExporterService(new_settings).run()

        payloads = self._audit_payloads()
        failed_rows = [item for item in payloads if item["event_type"] == "ffmpeg_export_failed"]
        self.assertEqual(len(failed_rows), 1)
        self.assertEqual(failed_rows[0]["attempt"], 1)
        self.assertEqual(failed_rows[0]["max_attempts"], 1)
        # 5xx is_retryable=True survives in the audit even though exporter's
        # retry policy doesn't act on it (PRD D4: out of scope).
        self.assertTrue(failed_rows[0]["is_retryable"])
        self.assertEqual(failed_rows[0]["reason_code"], "http_5xx")


class ExporterAttemptBackoffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.temp_root = root / "tmp"
        self.raw_root = root / "raw"
        self.processed_root = root / "processed"
        self.export_root = root / "exports"
        self.settings = Settings(
            douyin=DouyinSettings(event_log_path=self.temp_root / "windows-agent-events.jsonl"),
            storage=StorageSettings(
                raw_dir=self.raw_root,
                processed_dir=self.processed_root,
                export_dir=self.export_root,
                temp_dir=self.temp_root,
            ),
            orchestrator=OrchestratorSettings(
                state_file=self.temp_root / "orchestrator-state.json",
                agent_event_log_path=self.temp_root / "windows-agent-events.jsonl",
                recorder_event_log_path=self.temp_root / "recorder-events.jsonl",
                audit_log_path=self.temp_root / "orchestrator-events.jsonl",
            ),
            recording=RecordingSettings(enable_ffmpeg=False),
            subtitles=SubtitleSettings(enabled=True),
            export=ExportSettings(
                enable_ffmpeg=True,
                ffmpeg_timeout_seconds=10,
                ffmpeg_max_retries=0,
                backoff_initial_seconds=2.0,
                backoff_max_seconds=8.0,
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_export_inputs(self) -> None:
        boundary = MatchBoundary(
            session_id="session-backoff",
            match_index=1,
            started_at_seconds=0.0,
            ended_at_seconds=60.0,
            confidence=0.9,
        )
        subtitle_file = self.processed_root / "session-backoff" / "match-01.srt"
        subtitle_file.parent.mkdir(parents=True, exist_ok=True)
        subtitle_file.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
            encoding="utf-8",
        )
        recording_file = self.raw_root / "session-backoff" / "recording-source.mp4"
        recording_file.parent.mkdir(parents=True, exist_ok=True)
        recording_file.write_text("not-a-real-video", encoding="utf-8")
        append_model(self.temp_root / "match-boundaries.jsonl", boundary)
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id="session-backoff",
                match_index=1,
                path=str(subtitle_file),
                format="srt",
            ),
        )
        append_model(
            self.temp_root / "recording-assets.jsonl",
            RecordingAsset(
                session_id="session-backoff",
                source_type=SourceType.DIRECT_STREAM,
                path=str(recording_file),
                started_at=datetime(2026, 5, 12, 1, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 5, 12, 1, 10, tzinfo=timezone.utc),
            ),
        )

    def _run_export_with_error(
        self,
        settings: Settings,
        error: BaseException,
    ) -> list[float]:
        self._seed_export_inputs()
        sleep_calls: list[float] = []
        with patch("arl.exporter.service.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=error,
        ), patch("arl.exporter.service.time.sleep", side_effect=sleep_calls.append):
            ExporterService(settings).run()
        return sleep_calls

    def test_no_sleep_before_first_attempt(self) -> None:
        error = subprocess.CalledProcessError(1, ["ffmpeg"], stderr="exit_status:1")
        sleep_calls = self._run_export_with_error(self.settings, error)
        self.assertEqual(sleep_calls, [])

    def test_sleep_between_two_retryable_attempts(self) -> None:
        settings = self.settings.model_copy(deep=True)
        settings.export.ffmpeg_max_retries = 1
        error = subprocess.CalledProcessError(1, ["ffmpeg"], stderr="exit_status:1")
        sleep_calls = self._run_export_with_error(settings, error)
        self.assertEqual(sleep_calls, [2.0])

    def test_sleep_doubles_and_caps_across_three_attempts(self) -> None:
        settings = self.settings.model_copy(deep=True)
        settings.export.ffmpeg_max_retries = 2
        error = subprocess.CalledProcessError(1, ["ffmpeg"], stderr="exit_status:1")
        sleep_calls = self._run_export_with_error(settings, error)
        self.assertEqual(sleep_calls, [2.0, 4.0])

    def test_sleep_caps_at_max(self) -> None:
        settings = self.settings.model_copy(deep=True)
        settings.export.ffmpeg_max_retries = 4
        error = subprocess.CalledProcessError(1, ["ffmpeg"], stderr="exit_status:1")
        sleep_calls = self._run_export_with_error(settings, error)
        self.assertEqual(sleep_calls, [2.0, 4.0, 8.0, 8.0])

    def test_no_sleep_after_non_retryable_short_circuit(self) -> None:
        settings = self.settings.model_copy(deep=True)
        settings.export.ffmpeg_max_retries = 3
        error = subprocess.CalledProcessError(
            1,
            ["ffmpeg"],
            stderr="Server returned 404 Not Found",
        )
        sleep_calls = self._run_export_with_error(settings, error)
        self.assertEqual(sleep_calls, [])

    def test_config_overrides_backoff_schedule(self) -> None:
        settings = self.settings.model_copy(deep=True)
        settings.export.ffmpeg_max_retries = 2
        settings.export.backoff_initial_seconds = 1.0
        settings.export.backoff_max_seconds = 3.0
        error = subprocess.CalledProcessError(1, ["ffmpeg"], stderr="exit_status:1")
        sleep_calls = self._run_export_with_error(settings, error)
        self.assertEqual(sleep_calls, [1.0, 2.0])


class ExporterBatchBudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.temp_root = root / "tmp"
        self.raw_root = root / "raw"
        self.processed_root = root / "processed"
        self.export_root = root / "exports"
        self.settings = Settings(
            douyin=DouyinSettings(event_log_path=self.temp_root / "windows-agent-events.jsonl"),
            storage=StorageSettings(
                raw_dir=self.raw_root,
                processed_dir=self.processed_root,
                export_dir=self.export_root,
                temp_dir=self.temp_root,
            ),
            orchestrator=OrchestratorSettings(
                state_file=self.temp_root / "orchestrator-state.json",
                agent_event_log_path=self.temp_root / "windows-agent-events.jsonl",
                recorder_event_log_path=self.temp_root / "recorder-events.jsonl",
                audit_log_path=self.temp_root / "orchestrator-events.jsonl",
            ),
            recording=RecordingSettings(enable_ffmpeg=False),
            subtitles=SubtitleSettings(enabled=True),
            export=ExportSettings(
                enable_ffmpeg=True,
                ffmpeg_timeout_seconds=10,
                ffmpeg_max_retries=0,
                batch_fallback_budget=3,
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_export_inputs(self, count: int) -> None:
        session_id = "session-batch"
        recording_file = self.raw_root / session_id / "recording-source.mp4"
        recording_file.parent.mkdir(parents=True, exist_ok=True)
        recording_file.write_text("not-a-real-video", encoding="utf-8")
        append_model(
            self.temp_root / "recording-assets.jsonl",
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.DIRECT_STREAM,
                path=str(recording_file),
                started_at=datetime(2026, 5, 12, 1, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 5, 12, 1, 10, tzinfo=timezone.utc),
            ),
        )
        for match_index in range(1, count + 1):
            boundary = MatchBoundary(
                session_id=session_id,
                match_index=match_index,
                started_at_seconds=float((match_index - 1) * 60),
                ended_at_seconds=float(match_index * 60),
                confidence=0.9,
            )
            subtitle_file = self.processed_root / session_id / f"match-{match_index:02d}.srt"
            subtitle_file.parent.mkdir(parents=True, exist_ok=True)
            subtitle_file.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
                encoding="utf-8",
            )
            append_model(self.temp_root / "match-boundaries.jsonl", boundary)
            append_model(
                self.temp_root / "subtitle-assets.jsonl",
                SubtitleAsset(
                    session_id=session_id,
                    match_index=match_index,
                    path=str(subtitle_file),
                    format="srt",
                ),
            )

    def _audit_payloads(self) -> list[dict]:
        path = self.temp_root / "exporter-events.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _processed_keys(self) -> list[str]:
        path = self.temp_root / "exporter-state.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))["processed_match_keys"]

    def _deferred_keys(self) -> list[str]:
        path = self.temp_root / "exporter-state.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8")).get("deferred_match_keys", [])

    def _run_with_failure_pattern(
        self,
        *,
        count: int,
        failing_matches: set[int],
        settings: Settings | None = None,
    ) -> None:
        self._seed_export_inputs(count)

        def _fake_run(command, **kwargs):
            if "-show_entries" in command:
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "streams": [
                                {
                                    "codec_type": "video",
                                    "width": 1920,
                                    "height": 1080,
                                }
                            ],
                            "format": {"duration": "60.0", "size": "12345"},
                        }
                    ),
                )
            output_name = Path(command[-1]).name
            match_index = int(output_name.split("_match", 1)[1].split(".", 1)[0])
            if match_index in failing_matches:
                raise subprocess.CalledProcessError(
                    1,
                    ["ffmpeg"],
                    stderr="exit_status:1",
                )
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("fake exported video", encoding="utf-8")
            return subprocess.CompletedProcess(args=command, returncode=0)

        def _which(binary: str) -> str | None:
            if binary in {"ffmpeg", "ffprobe"}:
                return f"/usr/bin/{binary}"
            return None

        with patch("arl.exporter.service.shutil.which", side_effect=_which), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=_fake_run,
        ), patch("arl.exporter.service.time.sleep", return_value=None):
            ExporterService(settings or self.settings).run()

    def test_isolated_fallback_does_not_trip_budget(self) -> None:
        self._run_with_failure_pattern(count=5, failing_matches={3})
        events = self._audit_payloads()
        self.assertFalse(
            any(item["event_type"] == "ffmpeg_export_batch_aborted" for item in events)
        )
        self.assertEqual(
            len(
                [
                    item
                    for item in events
                    if item["event_type"] == "ffmpeg_export_fallback_placeholder"
                ]
            ),
            1,
        )
        self.assertEqual(len(self._processed_keys()), 4)
        self.assertEqual(self._deferred_keys(), ["session-batch:3"])

    def test_three_consecutive_fallbacks_trip_default_budget(self) -> None:
        self._run_with_failure_pattern(count=10, failing_matches=set(range(1, 11)))
        events = self._audit_payloads()
        fallback_rows = [
            item for item in events if item["event_type"] == "ffmpeg_export_fallback_placeholder"
        ]
        abort_rows = [
            item for item in events if item["event_type"] == "ffmpeg_export_batch_aborted"
        ]
        self.assertEqual(len(fallback_rows), 3)
        self.assertEqual(len(abort_rows), 1)
        self.assertEqual(abort_rows[0]["consecutive_fallbacks"], 3)
        self.assertEqual(abort_rows[0]["remaining_matches"], 7)
        self.assertEqual(self._processed_keys(), [])
        self.assertEqual(
            self._deferred_keys(),
            ["session-batch:1", "session-batch:2", "session-batch:3"],
        )

    def test_success_between_fallbacks_resets_counter(self) -> None:
        self._run_with_failure_pattern(count=6, failing_matches={1, 2, 4, 5, 6})
        events = self._audit_payloads()
        abort_rows = [
            item for item in events if item["event_type"] == "ffmpeg_export_batch_aborted"
        ]
        self.assertEqual(len(abort_rows), 1)
        self.assertEqual(abort_rows[0]["match_index"], 6)
        self.assertEqual(abort_rows[0]["remaining_matches"], 0)

    def test_batch_aborted_inherits_last_failure_classification(self) -> None:
        self._run_with_failure_pattern(count=10, failing_matches=set(range(1, 11)))
        abort_row = [
            item
            for item in self._audit_payloads()
            if item["event_type"] == "ffmpeg_export_batch_aborted"
        ][0]
        self.assertEqual(abort_row["decision"], "batch_aborted")
        self.assertEqual(abort_row["failure_category"], "ffmpeg_process_error_retryable")
        self.assertTrue(abort_row["is_retryable"])
        self.assertEqual(abort_row["reason_code"], "ffmpeg_process_error")
        self.assertEqual(abort_row["consecutive_fallbacks"], 3)
        self.assertEqual(abort_row["remaining_matches"], 7)

    def test_config_overrides_budget_threshold(self) -> None:
        settings = self.settings.model_copy(deep=True)
        settings.export.batch_fallback_budget = 2
        self._run_with_failure_pattern(
            count=5,
            failing_matches=set(range(1, 6)),
            settings=settings,
        )
        events = self._audit_payloads()
        fallback_rows = [
            item for item in events if item["event_type"] == "ffmpeg_export_fallback_placeholder"
        ]
        abort_row = [
            item for item in events if item["event_type"] == "ffmpeg_export_batch_aborted"
        ][0]
        self.assertEqual(len(fallback_rows), 2)
        self.assertEqual(abort_row["consecutive_fallbacks"], 2)
        self.assertEqual(abort_row["remaining_matches"], 3)

    def test_intentional_placeholder_does_not_count(self) -> None:
        settings = self.settings.model_copy(deep=True)
        settings.export.enable_ffmpeg = False
        self._seed_export_inputs(10)
        ExporterService(settings).run()
        events = self._audit_payloads()
        self.assertFalse(
            any(item["event_type"] == "ffmpeg_export_batch_aborted" for item in events)
        )
        self.assertEqual(len(self._processed_keys()), 0)
        self.assertEqual(len(self._deferred_keys()), 10)


if __name__ == "__main__":
    unittest.main()

