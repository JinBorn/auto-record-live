from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from arl.config import DouyinSettings, OrchestratorSettings, Settings
from arl.orchestrator.models import OrchestratorStateFile
from arl.orchestrator.service import OrchestratorService


def _cookie_expired_event_line(*, platform: str, reason: str, detected_at: str) -> str:
    return json.dumps(
        {
            "event_type": f"cookie_expired_for_{platform}",
            "snapshot": {
                "state": "offline",
                "streamer_name": f"{platform}-streamer",
                "room_url": f"https://live.example.com/{platform}",
                "source_type": None,
                "stream_url": None,
                "reason": reason,
                "detected_at": detected_at,
                "platform": platform,
            },
        },
        ensure_ascii=False,
    )


def _live_started_line(*, platform: str, detected_at: str) -> str:
    return json.dumps(
        {
            "event_type": "live_started",
            "snapshot": {
                "state": "live",
                "streamer_name": f"{platform}-streamer",
                "room_url": f"https://live.example.com/{platform}",
                "source_type": "browser_capture",
                "stream_url": None,
                "reason": "page_marker_detected",
                "detected_at": detected_at,
                "platform": platform,
            },
        },
        ensure_ascii=False,
    )


def _read_audit_event_types(audit_log: Path) -> list[str]:
    if not audit_log.exists():
        return []
    return [
        json.loads(line)["event_type"]
        for line in audit_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class OrchestratorCookieExpiredEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.agent_event_log = root / "windows-agent-events.jsonl"
        self.recorder_event_log = root / "recorder-events.jsonl"
        self.state_file = root / "orchestrator-state.json"
        self.audit_log = root / "orchestrator-events.jsonl"

        settings = Settings(
            douyin=DouyinSettings(),
            orchestrator=OrchestratorSettings(
                agent_event_log_path=self.agent_event_log,
                recorder_event_log_path=self.recorder_event_log,
                state_file=self.state_file,
                audit_log_path=self.audit_log,
            ),
        )
        self.service = OrchestratorService(settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cookie_expired_event_is_audited_under_its_own_event_type(self) -> None:
        line = _cookie_expired_event_line(
            platform="bilibili",
            reason="api_error:code=-101:账号未登录",
            detected_at="2026-05-10T12:00:00Z",
        )
        self.agent_event_log.write_text(line + "\n", encoding="utf-8")

        self.service.run_once()

        audit_types = _read_audit_event_types(self.audit_log)
        self.assertIn("cookie_expired_for_bilibili", audit_types)
        self.assertNotIn("ignored_unknown_event_type", audit_types)

    def test_cookie_expired_event_does_not_create_session_or_job(self) -> None:
        line = _cookie_expired_event_line(
            platform="douyin",
            reason="quality_below_min_tier:hd<uhd",
            detected_at="2026-05-10T12:00:00Z",
        )
        self.agent_event_log.write_text(line + "\n", encoding="utf-8")

        self.service.run_once()

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(state.sessions, [])
        self.assertEqual(state.recording_jobs, [])
        self.assertNotIn("douyin", state.active_session_id_by_platform)
        self.assertNotIn("douyin", state.active_recording_job_id_by_platform)

    def test_cookie_expired_audit_payload_carries_platform_and_reason(self) -> None:
        line = _cookie_expired_event_line(
            platform="bilibili",
            reason="api_error:code=-101:账号未登录",
            detected_at="2026-05-10T12:00:00Z",
        )
        self.agent_event_log.write_text(line + "\n", encoding="utf-8")

        self.service.run_once()

        rows = [
            json.loads(line)
            for line in self.audit_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        cookie_rows = [r for r in rows if r["event_type"] == "cookie_expired_for_bilibili"]
        self.assertEqual(len(cookie_rows), 1)
        message = cookie_rows[0].get("message", "")
        self.assertIn("platform=bilibili", message)
        self.assertIn("streamer=bilibili-streamer", message)
        self.assertIn("reason=api_error:code=-101", message)

    def test_cookie_expired_event_for_unknown_platform_still_routes_audit(self) -> None:
        # The orchestrator dispatches by event_type prefix and does not
        # cross-check platform against PROBE_REGISTRY; that registration is
        # the agent's responsibility. We still want a stable audit row here
        # rather than ignored_unknown_event_type, so operator tooling can
        # surface even unexpected platforms.
        line = _cookie_expired_event_line(
            platform="xinghuo",
            reason="api_error:code=-101:test",
            detected_at="2026-05-10T12:00:00Z",
        )
        self.agent_event_log.write_text(line + "\n", encoding="utf-8")

        self.service.run_once()

        audit_types = _read_audit_event_types(self.audit_log)
        self.assertIn("cookie_expired_for_xinghuo", audit_types)
        self.assertNotIn("ignored_unknown_event_type", audit_types)

    def test_cookie_expired_alongside_live_stopped_runs_both_handlers(self) -> None:
        # The agent emits cookie_expired right after the underlying state
        # transition event. Orchestrator must process both rows in order.
        live = _live_started_line(
            platform="bilibili",
            detected_at="2026-05-10T11:00:00Z",
        )
        cookie = _cookie_expired_event_line(
            platform="bilibili",
            reason="api_error:code=-101:账号未登录",
            detected_at="2026-05-10T12:00:00Z",
        )
        self.agent_event_log.write_text(live + "\n" + cookie + "\n", encoding="utf-8")

        self.service.run_once()

        audit_types = _read_audit_event_types(self.audit_log)
        self.assertIn("session_started", audit_types)
        self.assertIn("cookie_expired_for_bilibili", audit_types)
        self.assertNotIn("ignored_unknown_event_type", audit_types)

        state = OrchestratorStateFile.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.sessions), 1)
        self.assertEqual(state.sessions[0].platform, "bilibili")


if __name__ == "__main__":
    unittest.main()
