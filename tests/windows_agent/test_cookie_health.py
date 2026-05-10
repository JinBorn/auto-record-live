from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import ClassVar

from arl.shared.contracts import LiveState
from arl.windows_agent.cookie_health import (
    CookieHealthReport,
    CookieHealthRow,
    run_cookie_health,
)
from arl.windows_agent.models import AgentSnapshot
from arl.windows_agent.platform_probe import CookieState, PlatformProbe


_NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)


class _ScriptedProbe(PlatformProbe):
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

    platform_name: ClassVar[str] = "scripted"

    def detect(self) -> AgentSnapshot:
        return self._snapshot

    def classify_cookie_state(self, snapshot: AgentSnapshot) -> CookieState:
        return self._cookie_state


class _CrashingProbe(PlatformProbe):
    platform_name: ClassVar[str] = "crashing"

    def detect(self) -> AgentSnapshot:
        raise RuntimeError("simulated detect failure")


def _snapshot(*, platform: str, state: LiveState, reason: str | None) -> AgentSnapshot:
    return AgentSnapshot(
        state=state,
        streamer_name=f"{platform}-streamer",
        room_url=f"https://live.example.com/{platform}",
        reason=reason,
        detected_at=_NOW,
        platform=platform,
    )


class CookieHealthReportTests(unittest.TestCase):
    def test_all_fresh_returns_exit_zero(self) -> None:
        probes = [
            _ScriptedProbe(
                platform="bilibili",
                snapshot=_snapshot(
                    platform="bilibili",
                    state=LiveState.LIVE,
                    reason="api_live_with_stream_url",
                ),
                cookie_state=CookieState.FRESH,
            ),
            _ScriptedProbe(
                platform="douyin",
                snapshot=_snapshot(
                    platform="douyin",
                    state=LiveState.LIVE,
                    reason="page_marker_detected",
                ),
                cookie_state=CookieState.FRESH,
            ),
        ]
        report = run_cookie_health(probes)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual([row.status for row in report.rows], ["fresh", "fresh"])

    def test_any_expired_returns_exit_one(self) -> None:
        probes = [
            _ScriptedProbe(
                platform="bilibili",
                snapshot=_snapshot(
                    platform="bilibili",
                    state=LiveState.LIVE,
                    reason="api_live_with_stream_url",
                ),
                cookie_state=CookieState.FRESH,
            ),
            _ScriptedProbe(
                platform="douyin",
                snapshot=_snapshot(
                    platform="douyin",
                    state=LiveState.OFFLINE,
                    reason="quality_below_min_tier:hd<uhd",
                ),
                cookie_state=CookieState.EXPIRED,
            ),
        ]
        report = run_cookie_health(probes)
        self.assertEqual(report.exit_code, 1)
        statuses = [row.status for row in report.rows]
        self.assertIn("expired", statuses)
        self.assertIn("fresh", statuses)

    def test_not_configured_does_not_set_failure_exit(self) -> None:
        probes = [
            _ScriptedProbe(
                platform="douyin",
                snapshot=_snapshot(
                    platform="douyin",
                    state=LiveState.OFFLINE,
                    reason="not_live",
                ),
                cookie_state=CookieState.NOT_CONFIGURED,
            ),
        ]
        report = run_cookie_health(probes)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.rows[0].status, "not_configured")

    def test_probe_error_is_reported_but_does_not_set_failure_exit(self) -> None:
        report = run_cookie_health([_CrashingProbe()])
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(len(report.rows), 1)
        self.assertEqual(report.rows[0].platform, "crashing")
        self.assertEqual(report.rows[0].status, "error")
        self.assertIn("RuntimeError", report.rows[0].detail)

    def test_empty_probe_list_returns_no_rows_exit_zero(self) -> None:
        report = run_cookie_health([])
        self.assertEqual(report, CookieHealthReport(rows=[], exit_code=0))

    def test_row_detail_carries_snapshot_reason(self) -> None:
        probes = [
            _ScriptedProbe(
                platform="bilibili",
                snapshot=_snapshot(
                    platform="bilibili",
                    state=LiveState.OFFLINE,
                    reason="api_error:code=-101:账号未登录",
                ),
                cookie_state=CookieState.EXPIRED,
            ),
        ]
        report = run_cookie_health(probes)
        self.assertEqual(report.rows[0].detail, "api_error:code=-101:账号未登录")

    def test_row_detail_falls_back_to_n_a_when_reason_missing(self) -> None:
        probes = [
            _ScriptedProbe(
                platform="bilibili",
                snapshot=_snapshot(
                    platform="bilibili",
                    state=LiveState.LIVE,
                    reason=None,
                ),
                cookie_state=CookieState.FRESH,
            ),
        ]
        report = run_cookie_health(probes)
        self.assertEqual(
            report.rows[0],
            CookieHealthRow(platform="bilibili", status="fresh", detail="n/a"),
        )


if __name__ == "__main__":
    unittest.main()
