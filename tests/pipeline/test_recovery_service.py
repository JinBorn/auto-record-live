from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import DouyinSettings, OrchestratorSettings, Settings, StorageSettings
from arl.recovery.service import RecoveryService
from arl.recorder.models import RecorderRecoveryAction
from arl.shared.contracts import SourceType
from arl.shared.jsonl_store import append_model


class RecoveryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.temp_root = root / "tmp"
        self.settings = Settings(
            douyin=DouyinSettings(event_log_path=self.temp_root / "windows-agent-events.jsonl"),
            storage=StorageSettings(temp_dir=self.temp_root),
            orchestrator=OrchestratorSettings(
                recorder_event_log_path=self.temp_root / "recorder-events.jsonl",
                state_file=self.temp_root / "orchestrator-state.json",
                agent_event_log_path=self.temp_root / "windows-agent-events.jsonl",
                audit_log_path=self.temp_root / "orchestrator-events.jsonl",
            ),
        )
        self.actions_path = self.temp_root / "recorder-recovery-actions.jsonl"
        self.events_path = self.temp_root / "recovery-events.jsonl"
        self.archive_path = self.temp_root / "recovery-events-archive.jsonl"
        self.state_path = self.temp_root / "recovery-state.json"
        self.recorder_events_path = self.temp_root / "recorder-events.jsonl"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_recovery_service_dispatches_actions_once(self) -> None:
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="install_or_fix_runtime_dependencies",
                session_id="session-a",
                job_id="job-a",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="environment",
                recoverable=False,
                stop_reason="missing_binary",
                recovery_hint="Install ffmpeg and verify PATH on the runtime host.",
                steps=["Install ffmpeg on runtime host."],
                created_at=datetime(2026, 4, 25, 8, 0, tzinfo=timezone.utc),
            ),
        )
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="fix_recorder_configuration",
                session_id="session-b",
                job_id="job-b",
                source_type=SourceType.BROWSER_CAPTURE,
                failure_category="configuration",
                recoverable=False,
                stop_reason="invalid argument",
                recovery_hint="Check ffmpeg input format/device arguments.",
                steps=["Fix capture format and input arguments."],
                created_at=datetime(2026, 4, 25, 8, 5, tzinfo=timezone.utc),
            ),
        )

        service = RecoveryService(self.settings)
        service.run()
        service.run()

        event_payloads = [
            json.loads(line)
            for line in self.events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(event_payloads), 2)
        self.assertEqual(
            [item["event_type"] for item in event_payloads],
            ["manual_recovery_action_dispatched", "manual_recovery_action_dispatched"],
        )
        self.assertEqual(event_payloads[0]["job_id"], "job-a")
        self.assertEqual(event_payloads[1]["job_id"], "job-b")
        self.assertEqual(event_payloads[0]["status"], "pending")
        self.assertIn("Install ffmpeg", event_payloads[0]["message"])

        state_payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(len(state_payload["processed_action_keys"]), 2)
        self.assertEqual(len(state_payload["status_by_action_key"]), 2)
        self.assertTrue(all(value == "pending" for value in state_payload["status_by_action_key"].values()))

    def test_recovery_service_uses_hint_when_steps_missing(self) -> None:
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="inspect_failure_logs",
                session_id="session-c",
                job_id="job-c",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="unknown",
                recoverable=None,
                stop_reason="subprocess_error:RuntimeError",
                recovery_hint="Inspect recorder-events.jsonl and ffmpeg stderr.",
                steps=[],
                created_at=datetime(2026, 4, 25, 9, 0, tzinfo=timezone.utc),
            ),
        )

        RecoveryService(self.settings).run()
        event_payload = json.loads(self.events_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(event_payload["job_id"], "job-c")
        self.assertIn("Inspect recorder-events.jsonl", event_payload["message"])

    def test_recovery_service_can_mark_pending_actions_resolved_or_failed(self) -> None:
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="inspect_failure_logs",
                session_id="session-r",
                job_id="job-r",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="unknown",
                recoverable=None,
                stop_reason="subprocess_error",
                recovery_hint=None,
                steps=["Inspect logs."],
                created_at=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
            ),
        )
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="fix_recorder_configuration",
                session_id="session-f",
                job_id="job-f",
                source_type=SourceType.BROWSER_CAPTURE,
                failure_category="configuration",
                recoverable=False,
                stop_reason="invalid argument",
                recovery_hint="Fix capture parameters.",
                steps=["Fix capture parameters."],
                created_at=datetime(2026, 4, 25, 10, 5, tzinfo=timezone.utc),
            ),
        )

        service = RecoveryService(self.settings)
        service.run()
        self.assertEqual(service.mark_resolved("job-r", "resolved by operator"), 1)
        self.assertEqual(service.mark_failed("job-f", "still blocked"), 1)
        self.assertEqual(service.mark_resolved("job-r"), 0)
        self.assertEqual(service.mark_failed("job-unknown"), 0)

        event_payloads = [
            json.loads(line)
            for line in self.events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(event_payloads), 4)
        self.assertEqual(
            [item["event_type"] for item in event_payloads],
            [
                "manual_recovery_action_dispatched",
                "manual_recovery_action_dispatched",
                "manual_recovery_action_resolved",
                "manual_recovery_action_failed",
            ],
        )
        self.assertEqual(event_payloads[2]["status"], "resolved")
        self.assertEqual(event_payloads[2]["message"], "resolved by operator")
        self.assertEqual(event_payloads[3]["status"], "failed")
        self.assertEqual(event_payloads[3]["message"], "still blocked")
        recorder_event_payloads = []
        if self.recorder_events_path.exists():
            recorder_event_payloads = [
                json.loads(line)
                for line in self.recorder_events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self.assertEqual(len(recorder_event_payloads), 1)
        self.assertEqual(recorder_event_payloads[0]["event_type"], "recording_retry_scheduled")
        self.assertEqual(recorder_event_payloads[0]["job_id"], "job-r")
        self.assertEqual(recorder_event_payloads[0]["reason"], "resolved by operator")

        state_payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertTrue(any(value == "resolved" for value in state_payload["status_by_action_key"].values()))
        self.assertTrue(any(value == "failed" for value in state_payload["status_by_action_key"].values()))

    def test_recovery_service_can_list_pending_and_mark_by_action_key(self) -> None:
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="inspect_failure_logs",
                session_id="session-k",
                job_id="job-k",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="unknown",
                recoverable=None,
                stop_reason="subprocess_error",
                recovery_hint="Inspect logs.",
                steps=["Inspect logs."],
                created_at=datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc),
            ),
        )
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="restore_source_prerequisites",
                session_id="session-k",
                job_id="job-k",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="prerequisite",
                recoverable=True,
                stop_reason="missing_stream_url",
                recovery_hint="Restore stream URL.",
                steps=["Restore stream URL."],
                created_at=datetime(2026, 4, 25, 11, 5, tzinfo=timezone.utc),
            ),
        )

        service = RecoveryService(self.settings)
        service.run()
        pending = service.list_pending_actions()
        self.assertEqual(len(pending), 2)
        first_key = pending[0]["action_key"]
        self.assertEqual(service.mark_action_resolved(first_key, "done"), 1)
        self.assertEqual(service.mark_action_resolved(first_key), 0)
        self.assertEqual(service.mark_action_failed("missing-action-key"), 0)

        pending_after = service.list_pending_actions()
        self.assertEqual(len(pending_after), 1)
        self.assertNotEqual(pending_after[0]["action_key"], first_key)
        second_key = pending_after[0]["action_key"]
        self.assertEqual(service.mark_action_resolved(second_key, "done-2"), 1)
        recorder_event_payloads = []
        if self.recorder_events_path.exists():
            recorder_event_payloads = [
                json.loads(line)
                for line in self.recorder_events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self.assertEqual(len(recorder_event_payloads), 1)
        self.assertEqual(recorder_event_payloads[0]["event_type"], "recording_retry_scheduled")
        self.assertEqual(recorder_event_payloads[0]["job_id"], "job-k")
        self.assertEqual(recorder_event_payloads[0]["reason"], "done-2")

    def test_recovery_service_does_not_requeue_when_action_failed_for_same_job(self) -> None:
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="inspect_failure_logs",
                session_id="session-mix",
                job_id="job-mix",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="unknown",
                recoverable=None,
                stop_reason="subprocess_error",
                recovery_hint="Inspect logs.",
                steps=["Inspect logs."],
                created_at=datetime(2026, 4, 25, 11, 30, tzinfo=timezone.utc),
            ),
        )
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="restore_source_prerequisites",
                session_id="session-mix",
                job_id="job-mix",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="prerequisite",
                recoverable=True,
                stop_reason="missing_stream_url",
                recovery_hint="Restore stream URL.",
                steps=["Restore stream URL."],
                created_at=datetime(2026, 4, 25, 11, 31, tzinfo=timezone.utc),
            ),
        )

        service = RecoveryService(self.settings)
        service.run()
        pending = service.list_pending_actions()
        self.assertEqual(len(pending), 2)

        self.assertEqual(service.mark_action_failed(pending[0]["action_key"], "blocked"), 1)
        self.assertEqual(service.mark_action_resolved(pending[1]["action_key"], "partial-fixed"), 1)

        recorder_event_payloads = []
        if self.recorder_events_path.exists():
            recorder_event_payloads = [
                json.loads(line)
                for line in self.recorder_events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self.assertEqual(recorder_event_payloads, [])
        summary = service.summary()
        self.assertEqual(summary["actions_pending"], 0)
        self.assertEqual(summary["actions_resolved"], 1)
        self.assertEqual(summary["actions_failed"], 1)

    def test_recovery_service_mark_resolved_job_does_not_requeue_when_failed_exists(self) -> None:
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="inspect_failure_logs",
                session_id="session-mix-job",
                job_id="job-mix-job",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="unknown",
                recoverable=None,
                stop_reason="subprocess_error",
                recovery_hint="Inspect logs.",
                steps=["Inspect logs."],
                created_at=datetime(2026, 4, 25, 11, 40, tzinfo=timezone.utc),
            ),
        )
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="restore_source_prerequisites",
                session_id="session-mix-job",
                job_id="job-mix-job",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="prerequisite",
                recoverable=True,
                stop_reason="missing_stream_url",
                recovery_hint="Restore stream URL.",
                steps=["Restore stream URL."],
                created_at=datetime(2026, 4, 25, 11, 41, tzinfo=timezone.utc),
            ),
        )

        service = RecoveryService(self.settings)
        service.run()
        pending = service.list_pending_actions()
        self.assertEqual(len(pending), 2)

        self.assertEqual(service.mark_action_failed(pending[0]["action_key"], "blocked"), 1)
        self.assertEqual(service.mark_resolved("job-mix-job", "resolve-rest"), 1)

        recorder_event_payloads = []
        if self.recorder_events_path.exists():
            recorder_event_payloads = [
                json.loads(line)
                for line in self.recorder_events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self.assertEqual(recorder_event_payloads, [])
        summary = service.summary()
        self.assertEqual(summary["actions_pending"], 0)
        self.assertEqual(summary["actions_resolved"], 1)
        self.assertEqual(summary["actions_failed"], 1)

    def test_recovery_service_requeues_when_latest_cycle_resolved_supersedes_old_failed(self) -> None:
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="inspect_failure_logs",
                session_id="session-cycle",
                job_id="job-cycle",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="unknown",
                recoverable=None,
                stop_reason="subprocess_error:first",
                recovery_hint="Inspect logs.",
                steps=["Inspect logs."],
                created_at=datetime(2026, 4, 25, 12, 30, tzinfo=timezone.utc),
            ),
        )

        service = RecoveryService(self.settings)
        service.run()
        self.assertEqual(service.mark_failed("job-cycle", "first-cycle-failed"), 1)

        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="inspect_failure_logs",
                session_id="session-cycle",
                job_id="job-cycle",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="unknown",
                recoverable=None,
                stop_reason="subprocess_error:second",
                recovery_hint="Inspect logs again.",
                steps=["Inspect logs again."],
                created_at=datetime(2026, 4, 25, 12, 40, tzinfo=timezone.utc),
            ),
        )
        service.run()

        self.assertEqual(service.mark_resolved("job-cycle", "second-cycle-resolved"), 1)

        recorder_event_payloads = []
        if self.recorder_events_path.exists():
            recorder_event_payloads = [
                json.loads(line)
                for line in self.recorder_events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self.assertEqual(len(recorder_event_payloads), 1)
        self.assertEqual(recorder_event_payloads[0]["event_type"], "recording_retry_scheduled")
        self.assertEqual(recorder_event_payloads[0]["job_id"], "job-cycle")
        self.assertEqual(recorder_event_payloads[0]["reason"], "second-cycle-resolved")

    def test_recovery_service_requeues_with_same_timestamp_when_later_action_resolved(self) -> None:
        same_time = datetime(2026, 4, 25, 12, 50, tzinfo=timezone.utc)
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="inspect_failure_logs",
                session_id="session-same-ts",
                job_id="job-same-ts",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="unknown",
                recoverable=None,
                stop_reason="subprocess_error:first",
                recovery_hint="Inspect logs.",
                steps=["Inspect logs."],
                created_at=same_time,
            ),
        )

        service = RecoveryService(self.settings)
        service.run()
        self.assertEqual(service.mark_failed("job-same-ts", "first-failed"), 1)

        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="inspect_failure_logs",
                session_id="session-same-ts",
                job_id="job-same-ts",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="unknown",
                recoverable=None,
                stop_reason="subprocess_error:second",
                recovery_hint="Inspect logs again.",
                steps=["Inspect logs again."],
                created_at=same_time,
            ),
        )
        service.run()
        self.assertEqual(service.mark_resolved("job-same-ts", "second-resolved"), 1)

        recorder_event_payloads = []
        if self.recorder_events_path.exists():
            recorder_event_payloads = [
                json.loads(line)
                for line in self.recorder_events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self.assertEqual(len(recorder_event_payloads), 1)
        self.assertEqual(recorder_event_payloads[0]["event_type"], "recording_retry_scheduled")
        self.assertEqual(recorder_event_payloads[0]["job_id"], "job-same-ts")
        self.assertEqual(recorder_event_payloads[0]["reason"], "second-resolved")

    def test_recovery_service_accepts_legacy_action_key_for_backward_compat(self) -> None:
        created_at = datetime(2026, 4, 25, 12, 55, tzinfo=timezone.utc)
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="inspect_failure_logs",
                session_id="session-legacy",
                job_id="job-legacy",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="unknown",
                recoverable=None,
                stop_reason="subprocess_error",
                recovery_hint="Inspect logs.",
                steps=["Inspect logs."],
                created_at=created_at,
            ),
        )

        service = RecoveryService(self.settings)
        service.run()

        legacy_key = (
            "session-legacy:job-legacy:"
            f"inspect_failure_logs:{created_at.isoformat()}"
        )
        self.assertEqual(service.mark_action_resolved(legacy_key, "legacy-resolved"), 1)

        recorder_event_payloads = []
        if self.recorder_events_path.exists():
            recorder_event_payloads = [
                json.loads(line)
                for line in self.recorder_events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self.assertEqual(len(recorder_event_payloads), 1)
        self.assertEqual(recorder_event_payloads[0]["event_type"], "recording_retry_scheduled")
        self.assertEqual(recorder_event_payloads[0]["job_id"], "job-legacy")
        self.assertEqual(recorder_event_payloads[0]["reason"], "legacy-resolved")

    def test_recovery_service_legacy_action_key_prefers_latest_when_collided(self) -> None:
        same_time = datetime(2026, 4, 25, 12, 57, tzinfo=timezone.utc)
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="inspect_failure_logs",
                session_id="session-legacy-collision",
                job_id="job-legacy-collision",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="unknown",
                recoverable=None,
                stop_reason="subprocess_error:first",
                recovery_hint="Inspect logs.",
                steps=["Inspect logs."],
                created_at=same_time,
            ),
        )

        service = RecoveryService(self.settings)
        service.run()
        self.assertEqual(service.mark_failed("job-legacy-collision", "first-failed"), 1)

        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="inspect_failure_logs",
                session_id="session-legacy-collision",
                job_id="job-legacy-collision",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="unknown",
                recoverable=None,
                stop_reason="subprocess_error:second",
                recovery_hint="Inspect logs again.",
                steps=["Inspect logs again."],
                created_at=same_time,
            ),
        )
        service.run()

        legacy_key = (
            "session-legacy-collision:job-legacy-collision:"
            f"inspect_failure_logs:{same_time.isoformat()}"
        )
        self.assertEqual(service.mark_action_resolved(legacy_key, "legacy-collision-resolved"), 1)

        recorder_event_payloads = []
        if self.recorder_events_path.exists():
            recorder_event_payloads = [
                json.loads(line)
                for line in self.recorder_events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self.assertEqual(len(recorder_event_payloads), 1)
        self.assertEqual(recorder_event_payloads[0]["event_type"], "recording_retry_scheduled")
        self.assertEqual(recorder_event_payloads[0]["job_id"], "job-legacy-collision")
        self.assertEqual(recorder_event_payloads[0]["reason"], "legacy-collision-resolved")

    def test_recovery_service_summary_and_batch_job_updates(self) -> None:
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="inspect_failure_logs",
                session_id="session-s",
                job_id="job-a",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="unknown",
                recoverable=None,
                stop_reason="subprocess_error",
                recovery_hint="Inspect logs.",
                steps=["Inspect logs."],
                created_at=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
            ),
        )
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="fix_recorder_configuration",
                session_id="session-s",
                job_id="job-a",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="configuration",
                recoverable=False,
                stop_reason="invalid argument",
                recovery_hint="Fix recorder config.",
                steps=["Fix config."],
                created_at=datetime(2026, 4, 25, 12, 1, tzinfo=timezone.utc),
            ),
        )
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="restore_source_prerequisites",
                session_id="session-s",
                job_id="job-b",
                source_type=SourceType.BROWSER_CAPTURE,
                failure_category="prerequisite",
                recoverable=True,
                stop_reason="missing_stream_url",
                recovery_hint="Restore stream URL.",
                steps=["Restore stream URL."],
                created_at=datetime(2026, 4, 25, 12, 2, tzinfo=timezone.utc),
            ),
        )

        service = RecoveryService(self.settings)
        service.run()

        batch_resolved = service.mark_jobs_resolved(["job-a", "job-missing"], "batch resolve")
        self.assertEqual(batch_resolved["total_updated"], 2)
        self.assertEqual(batch_resolved["updated_by_job"]["job-a"], 2)
        self.assertEqual(batch_resolved["updated_by_job"]["job-missing"], 0)

        batch_failed = service.mark_jobs_failed(["job-b"], "batch fail")
        self.assertEqual(batch_failed["total_updated"], 1)
        self.assertEqual(batch_failed["updated_by_job"]["job-b"], 1)

        summary = service.summary()
        self.assertEqual(summary["actions_total"], 3)
        self.assertEqual(summary["actions_dispatched"], 3)
        self.assertEqual(summary["actions_pending"], 0)
        self.assertEqual(summary["actions_resolved"], 2)
        self.assertEqual(summary["actions_failed"], 1)
        self.assertEqual(summary["actions_undispatched"], 0)
        self.assertEqual(summary["by_action_type"]["inspect_failure_logs"]["resolved"], 1)
        self.assertEqual(summary["by_action_type"]["fix_recorder_configuration"]["resolved"], 1)
        self.assertEqual(summary["by_action_type"]["restore_source_prerequisites"]["failed"], 1)
        event_payloads = [
            json.loads(line)
            for line in self.events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        batch_terminal_events = [
            item
            for item in event_payloads
            if item["event_type"] in {"manual_recovery_action_resolved", "manual_recovery_action_failed"}
        ]
        self.assertEqual(len(batch_terminal_events), 3)
        self.assertTrue(all(item.get("action_key") for item in batch_terminal_events))
        recorder_event_payloads = [
            json.loads(line)
            for line in self.recorder_events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(recorder_event_payloads), 1)
        self.assertEqual(recorder_event_payloads[0]["event_type"], "recording_retry_scheduled")
        self.assertEqual(recorder_event_payloads[0]["job_id"], "job-a")
        self.assertEqual(recorder_event_payloads[0]["reason"], "batch resolve")

    def test_recovery_service_maintenance_archives_terminal_and_compacts_actions(self) -> None:
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="inspect_failure_logs",
                session_id="session-m",
                job_id="job-m",
                source_type=SourceType.DIRECT_STREAM,
                failure_category="unknown",
                recoverable=None,
                stop_reason="subprocess_error",
                recovery_hint="Inspect logs.",
                steps=["Inspect logs."],
                created_at=datetime(2026, 4, 25, 13, 0, tzinfo=timezone.utc),
            ),
        )
        append_model(
            self.actions_path,
            RecorderRecoveryAction(
                action_type="restore_source_prerequisites",
                session_id="session-m",
                job_id="job-n",
                source_type=SourceType.BROWSER_CAPTURE,
                failure_category="prerequisite",
                recoverable=True,
                stop_reason="missing_stream_url",
                recovery_hint="Restore stream URL.",
                steps=["Restore stream URL."],
                created_at=datetime(2026, 4, 25, 13, 5, tzinfo=timezone.utc),
            ),
        )

        service = RecoveryService(self.settings)
        service.run()
        self.assertEqual(service.mark_resolved("job-m"), 1)
        self.assertEqual(service.mark_failed("job-n"), 1)

        maintenance = service.maintain()
        self.assertEqual(maintenance["removed_actions"], 2)
        self.assertEqual(maintenance["kept_actions"], 0)
        self.assertEqual(maintenance["kept_state_keys"], 0)
        self.assertEqual(maintenance["kept_events"], 0)
        self.assertGreaterEqual(maintenance["archived_events"], 4)

        actions_lines = [
            line for line in self.actions_path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        self.assertEqual(len(actions_lines), 0)
        current_event_lines = [
            line for line in self.events_path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        self.assertEqual(len(current_event_lines), 0)
        archived_event_lines = [
            line for line in self.archive_path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        self.assertGreaterEqual(len(archived_event_lines), 4)
        state_payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state_payload["processed_action_keys"], [])
        self.assertEqual(state_payload["status_by_action_key"], {})


if __name__ == "__main__":
    unittest.main()
