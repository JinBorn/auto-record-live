from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import ClassVar

from arl.shared.contracts import LiveState, SourceType
from arl.windows_agent.live_status import run_live_status
from arl.windows_agent.models import AgentSnapshot
from arl.windows_agent.platform_probe import PlatformProbe


_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)


class _SnapshotProbe(PlatformProbe):
    platform_name: ClassVar[str] = "snapshot"

    def __init__(self, snapshot: AgentSnapshot) -> None:
        self.platform_name = snapshot.platform  # type: ignore[misc]
        self._snapshot = snapshot

    def detect(self) -> AgentSnapshot:
        return self._snapshot


class _CrashingProbe(PlatformProbe):
    platform_name: ClassVar[str] = "crashing"

    def __init__(self) -> None:
        self.settings = type(
            "Settings",
            (),
            {
                "room_url": "https://live.example.com/crash",
                "streamer_name": "crash-room",
            },
        )()

    def detect(self) -> AgentSnapshot:
        raise RuntimeError("simulated probe failure")


class LiveStatusServiceTests(unittest.TestCase):
    def test_run_live_status_reports_snapshot_rows_and_summary(self) -> None:
        live = AgentSnapshot(
            state=LiveState.LIVE,
            streamer_name="live-room",
            room_url="https://live.example.com/live",
            source_type=SourceType.DIRECT_STREAM,
            stream_url="https://cdn.example/live.m3u8",
            reason="api_live_with_stream_url",
            detected_at=_NOW,
            platform="bilibili",
        )
        offline = AgentSnapshot(
            state=LiveState.OFFLINE,
            streamer_name="offline-room",
            room_url="https://live.example.com/offline",
            reason="live_state_unknown",
            detected_at=_NOW,
            platform="douyin",
        )

        report = run_live_status([_SnapshotProbe(live), _SnapshotProbe(offline)])
        payload = report.as_dict()

        self.assertEqual(payload["summary"]["total"], 2)
        self.assertEqual(payload["summary"]["live"], 1)
        self.assertEqual(payload["summary"]["offline"], 1)
        self.assertEqual(payload["summary"]["error"], 0)
        self.assertEqual(payload["rooms"][0]["platform"], "bilibili")
        self.assertEqual(payload["rooms"][0]["state"], "live")
        self.assertEqual(payload["rooms"][0]["source_type"], "direct_stream")
        self.assertEqual(payload["rooms"][1]["state"], "offline")

    def test_run_live_status_isolates_probe_errors(self) -> None:
        report = run_live_status([_CrashingProbe()])
        payload = report.as_dict()

        self.assertEqual(payload["summary"]["total"], 1)
        self.assertEqual(payload["summary"]["error"], 1)
        self.assertEqual(payload["rooms"][0]["platform"], "crashing")
        self.assertEqual(payload["rooms"][0]["state"], "error")
        self.assertEqual(payload["rooms"][0]["room_url"], "https://live.example.com/crash")
        self.assertEqual(payload["rooms"][0]["reason"], "probe_error:RuntimeError")


if __name__ == "__main__":
    unittest.main()
