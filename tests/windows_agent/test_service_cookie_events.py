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
from arl.windows_agent.platform_probe import CookieState, PlatformProbe
from arl.windows_agent.service import WindowsAgentService
from arl.windows_agent.state_store import WindowsAgentStateStore


_NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)


class _ScriptedProbe(PlatformProbe):
    """Probe that yields a configured snapshot + cookie state per call."""

    platform_name: ClassVar[str] = "scripted"

    def __init__(
        self,
        *,
        snapshot: AgentSnapshot,
        cookie_state: CookieState,
        platform: str = "scripted",
    ) -> None:
        self._snapshot = snapshot
        self._cookie_state = cookie_state
        self.platform_name = platform  # type: ignore[misc]

    def detect(self) -> AgentSnapshot:
        return self._snapshot

    def classify_cookie_state(self, snapshot: AgentSnapshot) -> CookieState:
        return self._cookie_state


def _expired_offline_snapshot(platform: str) -> AgentSnapshot:
    return AgentSnapshot(
        state=LiveState.OFFLINE,
        streamer_name=f"{platform}-streamer",
        room_url=f"https://live.example.com/{platform}",
        reason="api_error:code=-101:账号未登录"
        if platform == "bilibili"
        else "quality_below_min_tier:hd<uhd",
        detected_at=_NOW,
        platform=platform,
    )


def _live_snapshot(platform: str) -> AgentSnapshot:
    return AgentSnapshot(
        state=LiveState.LIVE,
        streamer_name=f"{platform}-streamer",
        room_url=f"https://live.example.com/{platform}",
        source_type=SourceType.DIRECT_STREAM,
        stream_url="https://cdn.example/live.m3u8",
        reason="page_marker_detected",
        detected_at=_NOW,
        platform=platform,
    )


class WindowsAgentServiceCookieEventTests(unittest.TestCase):
    """Cookie-expiration events ride alongside the underlying live event,
    gated on snapshot transition (the same _has_changed dedup as live_*).
    """

    def _make_service(
        self,
        *,
        probes: list[PlatformProbe],
        state_path: Path,
        event_log_path: Path,
    ) -> WindowsAgentService:
        service = WindowsAgentService.__new__(WindowsAgentService)
        service.settings = None  # type: ignore[assignment]
        service.probes = probes
        service.state_store = WindowsAgentStateStore(
            state_path=state_path,
            event_log_path=event_log_path,
        )
        return service

    def _read_event_types(self, event_log_path: Path) -> list[str]:
        if not event_log_path.exists():
            return []
        return [
            json.loads(line)["event_type"]
            for line in event_log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_expired_state_emits_cookie_event_alongside_live_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "agent-state.json"
            event_log_path = root / "windows-agent-events.jsonl"

            probe = _ScriptedProbe(
                snapshot=_expired_offline_snapshot("bilibili"),
                cookie_state=CookieState.EXPIRED,
                platform="bilibili",
            )
            service = self._make_service(
                probes=[probe],
                state_path=state_path,
                event_log_path=event_log_path,
            )

            with redirect_stdout(io.StringIO()):
                service.run_once()

            event_types = self._read_event_types(event_log_path)
            self.assertEqual(event_types, ["live_stopped", "cookie_expired_for_bilibili"])

    def test_fresh_state_emits_only_live_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "agent-state.json"
            event_log_path = root / "windows-agent-events.jsonl"

            probe = _ScriptedProbe(
                snapshot=_live_snapshot("bilibili"),
                cookie_state=CookieState.FRESH,
                platform="bilibili",
            )
            service = self._make_service(
                probes=[probe],
                state_path=state_path,
                event_log_path=event_log_path,
            )

            with redirect_stdout(io.StringIO()):
                service.run_once()

            event_types = self._read_event_types(event_log_path)
            self.assertEqual(event_types, ["live_started"])

    def test_not_configured_with_expired_shape_does_not_emit_cookie_event(self) -> None:
        # Even if the snapshot reason matches the cookie-expiration shape,
        # a probe that classifies cookie state as not_configured (because
        # no cookie env var was set) must NOT trigger the cookie_expired
        # event. This is the "no false positive when no cookie configured"
        # acceptance criterion.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "agent-state.json"
            event_log_path = root / "windows-agent-events.jsonl"

            probe = _ScriptedProbe(
                snapshot=_expired_offline_snapshot("douyin"),
                cookie_state=CookieState.NOT_CONFIGURED,
                platform="douyin",
            )
            service = self._make_service(
                probes=[probe],
                state_path=state_path,
                event_log_path=event_log_path,
            )

            with redirect_stdout(io.StringIO()):
                service.run_once()

            event_types = self._read_event_types(event_log_path)
            self.assertEqual(event_types, ["live_stopped"])

    def test_persistent_expired_state_does_not_re_emit_on_unchanged_snapshot(self) -> None:
        # Per prd "frequency: state-transition only — reuse existing
        # _has_changed dedup". A persistently-expired cookie produces one
        # event on transition, not one per cycle.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "agent-state.json"
            event_log_path = root / "windows-agent-events.jsonl"

            probe = _ScriptedProbe(
                snapshot=_expired_offline_snapshot("bilibili"),
                cookie_state=CookieState.EXPIRED,
                platform="bilibili",
            )
            service = self._make_service(
                probes=[probe],
                state_path=state_path,
                event_log_path=event_log_path,
            )

            with redirect_stdout(io.StringIO()):
                service.run_once()
                service.run_once()  # same snapshot — no new events

            event_types = self._read_event_types(event_log_path)
            self.assertEqual(event_types, ["live_stopped", "cookie_expired_for_bilibili"])


if __name__ == "__main__":
    unittest.main()
