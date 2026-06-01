from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from arl.config import MaintenanceSettings, OrchestratorSettings, Settings, StorageSettings
from arl.maintenance.service import MaintenanceService
from arl.orchestrator.models import OrchestratorStateFile


class MaintenanceServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.temp_root = root / "tmp"
        self.settings = Settings(
            storage=StorageSettings(
                raw_dir=root / "raw",
                processed_dir=root / "processed",
                export_dir=root / "exports",
                temp_dir=self.temp_root,
            ),
            orchestrator=OrchestratorSettings(
                state_file=self.temp_root / "orchestrator-state.json",
                agent_event_log_path=self.temp_root / "windows-agent-events.jsonl",
                recorder_event_log_path=self.temp_root / "recorder-events.jsonl",
                audit_log_path=self.temp_root / "orchestrator-events.jsonl",
            ),
            maintenance=MaintenanceSettings(
                max_jsonl_bytes=100,
                keep_recent_lines=2,
                launcher_log_retain_count=2,
                archive_dir=self.temp_root / "archive",
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_archives_consumed_orchestrator_inputs_and_resets_cursors(self) -> None:
        consumed = self._agent_event_line("2026-06-01T01:00:00+00:00")
        unconsumed = self._agent_event_line("2026-06-01T01:01:00+00:00")
        self.settings.orchestrator.agent_event_log_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self.settings.orchestrator.agent_event_log_path.write_text(
            consumed + unconsumed,
            encoding="utf-8",
        )
        with self.settings.orchestrator.agent_event_log_path.open(
            "r",
            encoding="utf-8",
        ) as handle:
            handle.readline()
            cursor_offset = handle.tell()
        state = OrchestratorStateFile(cursor_offset=cursor_offset)
        self.settings.orchestrator.state_file.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

        result = MaintenanceService(self.settings).run_once().as_dict()

        updated = OrchestratorStateFile.model_validate_json(
            self.settings.orchestrator.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(updated.cursor_offset, 0)
        self.assertEqual(
            self.settings.orchestrator.agent_event_log_path.read_text(encoding="utf-8"),
            unconsumed,
        )
        self.assertIn("windows-agent-events.jsonl", result["consumed_logs_archived"])
        archives = list((self.temp_root / "archive").glob("windows-agent-events.jsonl.*.archive"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_text(encoding="utf-8"), consumed)

    def test_archives_old_audit_lines_and_keeps_recent_tail(self) -> None:
        path = self.settings.orchestrator.audit_log_path
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [json.dumps({"event_type": f"e{i}"}) + "\n" for i in range(5)]
        path.write_text("".join(rows), encoding="utf-8")

        result = MaintenanceService(self.settings).run_once().as_dict()

        self.assertEqual(path.read_text(encoding="utf-8"), "".join(rows[-2:]))
        self.assertIn("orchestrator-events.jsonl", result["audit_logs_archived"])
        archives = list((self.temp_root / "archive").glob("orchestrator-events.jsonl.*.archive"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_text(encoding="utf-8"), "".join(rows[:-2]))

    def test_rotates_launcher_logs_by_mtime(self) -> None:
        log_dir = self.temp_root / "launcher-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        for index in range(5):
            path = log_dir / f"log-{index}.log"
            path.write_text(str(index), encoding="utf-8")
            mtime = 1_700_000_000 + index
            path.touch()
            os.utime(path, (mtime, mtime))

        result = MaintenanceService(self.settings).run_once().as_dict()

        self.assertEqual(result["launcher_logs_removed"], 3)
        self.assertEqual(
            sorted(path.name for path in log_dir.glob("*.log")),
            ["log-3.log", "log-4.log"],
        )

    def _agent_event_line(self, detected_at: str) -> str:
        payload = {
            "event_type": "live_started",
            "snapshot": {
                "state": "live",
                "streamer_name": "streamer",
                "room_url": "https://live.douyin.com/1",
                "source_type": "direct_stream",
                "stream_url": "https://example.invalid/live.m3u8",
                "reason": "test",
                "detected_at": detected_at,
                "platform": "douyin",
            },
        }
        return json.dumps(payload, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    unittest.main()
