from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from typing import ClassVar
from unittest.mock import patch

from arl.cli import build_parser, main
from arl.shared.contracts import LiveState
from arl.windows_agent.models import AgentSnapshot
from arl.windows_agent.platform_probe import CookieState, PlatformProbe


_NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)


class _ScriptedProbe(PlatformProbe):
    platform_name: ClassVar[str] = "scripted"

    def __init__(
        self,
        *,
        platform: str,
        snapshot: AgentSnapshot,
        cookie_state: CookieState,
    ) -> None:
        self.platform_name = platform  # type: ignore[misc]
        self._snapshot = snapshot
        self._cookie_state = cookie_state

    def detect(self) -> AgentSnapshot:
        return self._snapshot

    def classify_cookie_state(self, snapshot: AgentSnapshot) -> CookieState:
        return self._cookie_state


def _live_snapshot(platform: str) -> AgentSnapshot:
    return AgentSnapshot(
        state=LiveState.LIVE,
        streamer_name=f"{platform}-streamer",
        room_url=f"https://live.example.com/{platform}",
        reason="api_live_with_stream_url",
        detected_at=_NOW,
        platform=platform,
    )


def _expired_snapshot(platform: str) -> AgentSnapshot:
    return AgentSnapshot(
        state=LiveState.OFFLINE,
        streamer_name=f"{platform}-streamer",
        room_url=f"https://live.example.com/{platform}",
        reason="api_error:code=-101:账号未登录",
        detected_at=_NOW,
        platform=platform,
    )


class CookieHealthCliParserTest(unittest.TestCase):
    def test_cookie_health_command_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["cookie-health"])
        self.assertEqual(args.command, "cookie-health")


class CookieHealthCliRunTest(unittest.TestCase):
    """End-to-end CLI run: patch build_probes to inject scripted probes,
    call main(), capture stdout + exit code.
    """

    def _run(self, probes: list[PlatformProbe]) -> tuple[int, str]:
        argv = [sys.argv[0], "cookie-health"]
        captured = io.StringIO()
        live_room_keys = {("bilibili", "https://live.example.com/bilibili")}
        with patch(
            "arl.cli.load_cookie_health_live_room_keys",
            return_value=live_room_keys,
        ) as mocked_live_room_keys, patch(
            "arl.cli.build_cookie_health_probes",
            return_value=probes,
        ) as mocked_cookie_health_probes, patch(
            "arl.cli.build_probes"
        ) as mocked_build_probes, patch.object(
            sys,
            "argv",
            argv,
        ), redirect_stdout(captured):
            exit_code = main()
        mocked_live_room_keys.assert_called_once()
        self.assertEqual(
            mocked_cookie_health_probes.call_args.kwargs["live_room_keys"],
            live_room_keys,
        )
        mocked_build_probes.assert_not_called()
        return exit_code, captured.getvalue()

    def test_all_fresh_exits_zero_and_reports_status(self) -> None:
        probes = [
            _ScriptedProbe(
                platform="bilibili",
                snapshot=_live_snapshot("bilibili"),
                cookie_state=CookieState.FRESH,
            ),
        ]
        exit_code, output = self._run(probes)
        self.assertEqual(exit_code, 0)
        self.assertIn("platform=bilibili", output)
        self.assertIn("status=fresh", output)
        self.assertIn("summary=ok", output)

    def test_any_expired_exits_one_and_emits_hint(self) -> None:
        probes = [
            _ScriptedProbe(
                platform="bilibili",
                snapshot=_expired_snapshot("bilibili"),
                cookie_state=CookieState.EXPIRED,
            ),
        ]
        exit_code, output = self._run(probes)
        self.assertEqual(exit_code, 1)
        self.assertIn("status=expired", output)
        self.assertIn("summary=expired_cookie_detected", output)
        self.assertIn("ARL_BILIBILI_SESSDATA", output)

    def test_no_cookie_configured_exits_zero(self) -> None:
        probes = [
            _ScriptedProbe(
                platform="douyin",
                snapshot=_live_snapshot("douyin"),
                cookie_state=CookieState.NOT_CONFIGURED,
            ),
        ]
        exit_code, output = self._run(probes)
        self.assertEqual(exit_code, 0)
        self.assertIn("status=not_configured", output)
        self.assertIn("summary=ok", output)
        self.assertNotIn("hint=", output)


if __name__ == "__main__":
    unittest.main()
