from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from arl.shared.contracts import LiveState, SourceType
from arl.windows_agent.models import AgentSnapshot
from arl.windows_agent.platform_probe import PlatformProbe
from arl.windows_agent.service import WindowsAgentService
from arl.windows_agent.state_store import WindowsAgentStateStore


class _CrashingProbe(PlatformProbe):
    platform_name: ClassVar[str] = "crashing"

    def detect(self) -> AgentSnapshot:
        raise RuntimeError("simulated probe failure")


class _HealthyProbe(PlatformProbe):
    platform_name: ClassVar[str] = "healthy"

    def __init__(self, room_url: str = "https://live.example.com/42") -> None:
        self._room_url = room_url

    def detect(self) -> AgentSnapshot:
        return AgentSnapshot(
            state=LiveState.LIVE,
            streamer_name="streamer-healthy",
            room_url=self._room_url,
            source_type=SourceType.DIRECT_STREAM,
            stream_url="https://cdn.example/live.m3u8",
            reason="page_marker_detected",
            detected_at=datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc),
            platform=self.platform_name,
        )


class WindowsAgentServiceIsolationTests(unittest.TestCase):
    """Per-platform isolation: one probe crashing must not stop the loop nor
    prevent the other probes from logging snapshots, persisting state, or
    emitting events.
    """

    def _make_service(
        self,
        *,
        probes: list[PlatformProbe],
        state_path: Path,
        event_log_path: Path,
    ) -> WindowsAgentService:
        # Bypass __init__ so we don't build probes from settings and don't
        # require a fully-loaded Settings object for this isolation contract.
        service = WindowsAgentService.__new__(WindowsAgentService)
        service.settings = None  # type: ignore[assignment]
        service.probes = probes
        service.state_store = WindowsAgentStateStore(
            state_path=state_path,
            event_log_path=event_log_path,
        )
        return service

    def test_crashing_probe_does_not_block_healthy_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "agent-state.json"
            event_log_path = root / "windows-agent-events.jsonl"

            probes: list[PlatformProbe] = [_CrashingProbe(), _HealthyProbe()]
            service = self._make_service(
                probes=probes,
                state_path=state_path,
                event_log_path=event_log_path,
            )

            captured = io.StringIO()
            with redirect_stdout(captured):
                # Must not raise.
                service.run_once()

            log_output = captured.getvalue()

            # 1. Crash for the failing probe is logged with the platform name
            #    and exception class.
            self.assertIn("platform=crashing", log_output)
            self.assertIn("crashed", log_output)
            self.assertIn("RuntimeError", log_output)

            # 2. The healthy probe still emitted its snapshot/event.
            self.assertIn("platform=healthy", log_output)
            self.assertIn("emitted event=live_started", log_output)

            # 3. State file persisted only the healthy snapshot.
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(list(saved["last_snapshots"].keys()), [
                "healthy:https://live.example.com/42",
            ])

            # 4. Event log has exactly one line for the healthy probe.
            event_lines = [
                line
                for line in event_log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(event_lines), 1)
            event = json.loads(event_lines[0])
            self.assertEqual(event["event_type"], "live_started")
            self.assertEqual(event["snapshot"]["platform"], "healthy")
            self.assertEqual(event["snapshot"]["state"], LiveState.LIVE.value)


if __name__ == "__main__":
    unittest.main()
