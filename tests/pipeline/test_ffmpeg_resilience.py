from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
from arl.recorder.service import RecorderService
from arl.shared.contracts import MatchBoundary, RecordingAsset, SourceType, SubtitleAsset
from arl.shared.jsonl_store import append_model


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
        self.assertEqual(updated_state.active_recording_job_id_by_platform["douyin"], "job-manual-requeue")

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
        exports_path = self.temp_root / "export-assets.jsonl"
        payload = json.loads(exports_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertTrue(payload["path"].endswith("_match01.txt"))
        self.assertTrue(Path(payload["path"]).exists())


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


if __name__ == "__main__":
    unittest.main()
