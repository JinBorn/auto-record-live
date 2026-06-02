from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from typing import ClassVar
from unittest.mock import patch

from arl.cli import build_parser, main
from arl.shared.contracts import LiveState, SourceType
from arl.windows_agent.models import AgentSnapshot
from arl.windows_agent.platform_probe import PlatformProbe


_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)


class _ScriptedProbe(PlatformProbe):
    platform_name: ClassVar[str] = "scripted"

    def __init__(self, snapshot: AgentSnapshot) -> None:
        self.platform_name = snapshot.platform  # type: ignore[misc]
        self._snapshot = snapshot

    def detect(self) -> AgentSnapshot:
        return self._snapshot


def _live_snapshot(platform: str) -> AgentSnapshot:
    return AgentSnapshot(
        state=LiveState.LIVE,
        streamer_name=f"{platform}-streamer",
        room_url=f"https://live.example.com/{platform}",
        source_type=SourceType.DIRECT_STREAM,
        stream_url=f"https://cdn.example/{platform}.m3u8",
        reason="api_live_with_stream_url",
        detected_at=_NOW,
        platform=platform,
    )


class LiveStatusCliParserTest(unittest.TestCase):
    def test_live_status_command_parses(self) -> None:
        args = build_parser().parse_args(["live-status"])
        self.assertEqual(args.command, "live-status")
        self.assertFalse(args.json)

    def test_live_status_json_flag_parses(self) -> None:
        args = build_parser().parse_args(["live-status", "--json"])
        self.assertEqual(args.command, "live-status")
        self.assertTrue(args.json)


class LiveStatusCliRunTest(unittest.TestCase):
    def _run(self, argv_tail: list[str], probes: list[PlatformProbe]) -> tuple[int, str]:
        argv = [sys.argv[0], *argv_tail]
        captured = io.StringIO()
        with patch("arl.cli.build_probes", return_value=probes), patch.object(
            sys, "argv", argv
        ), redirect_stdout(captured):
            exit_code = main()
        return exit_code, captured.getvalue()

    def test_live_status_text_output_lists_configured_room(self) -> None:
        exit_code, output = self._run(
            ["live-status"],
            [_ScriptedProbe(_live_snapshot("bilibili"))],
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("platform=bilibili", output)
        self.assertIn("state=live", output)
        self.assertIn("streamer_name=bilibili-streamer", output)
        self.assertIn("room_url=https://live.example.com/bilibili", output)
        self.assertIn("summary=live_status total=1 live=1 offline=0 error=0", output)

    def test_live_status_json_output_lists_configured_room(self) -> None:
        exit_code, output = self._run(
            ["live-status", "--json"],
            [_ScriptedProbe(_live_snapshot("douyin"))],
        )

        self.assertEqual(exit_code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["summary"]["total"], 1)
        self.assertEqual(payload["summary"]["live"], 1)
        self.assertEqual(payload["rooms"][0]["platform"], "douyin")
        self.assertEqual(payload["rooms"][0]["state"], "live")


if __name__ == "__main__":
    unittest.main()
