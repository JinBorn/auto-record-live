from __future__ import annotations

import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from arl.config import BilibiliSettings, DouyinSettings
from arl.shared.contracts import LiveState
from arl.windows_agent.cookie_health import (
    build_cookie_health_probes,
    CookieHealthReport,
    CookieHealthRow,
    load_cookie_health_live_room_keys,
    run_cookie_health,
)
from arl.windows_agent.models import AgentSnapshot, AgentStateFile
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


class CookieHealthProbeSelectionTests(unittest.TestCase):
    def test_same_platform_credential_uses_first_representative_room(self) -> None:
        platforms = [
            BilibiliSettings(
                room_url="https://live.bilibili.com/111",
                streamer_name="bili-a",
                sessdata="same-sessdata",
            ),
            BilibiliSettings(
                room_url="https://live.bilibili.com/222",
                streamer_name="bili-b",
                sessdata="same-sessdata",
            ),
            DouyinSettings(
                room_url="https://live.douyin.com/333",
                streamer_name="douyin-a",
                cookie="same-cookie",
            ),
            DouyinSettings(
                room_url="https://live.douyin.com/444",
                streamer_name="douyin-b",
                cookie="same-cookie",
            ),
        ]

        with patch("arl.windows_agent.cookie_health.build_probe") as mocked_build:
            mocked_build.side_effect = lambda platform: _ScriptedProbe(
                platform=platform.type,
                snapshot=_snapshot(
                    platform=platform.type,
                    state=LiveState.LIVE,
                    reason="ok",
                ),
                cookie_state=CookieState.FRESH,
            )
            probes = build_cookie_health_probes(platforms)

        self.assertEqual(len(probes), 2)
        selected_platforms = [call.args[0] for call in mocked_build.call_args_list]
        self.assertEqual(
            [(platform.type, platform.room_url) for platform in selected_platforms],
            [
                ("bilibili", "https://live.bilibili.com/111"),
                ("douyin", "https://live.douyin.com/333"),
            ],
        )

    def test_live_room_in_same_credential_group_is_preferred(self) -> None:
        platforms = [
            BilibiliSettings(
                room_url="https://live.bilibili.com/111",
                streamer_name="bili-offline",
                sessdata="same-sessdata",
            ),
            BilibiliSettings(
                room_url="https://live.bilibili.com/222",
                streamer_name="bili-live",
                sessdata="same-sessdata",
            ),
        ]

        with patch("arl.windows_agent.cookie_health.build_probe") as mocked_build:
            mocked_build.side_effect = lambda platform: _ScriptedProbe(
                platform=platform.type,
                snapshot=_snapshot(
                    platform=platform.type,
                    state=LiveState.LIVE,
                    reason="ok",
                ),
                cookie_state=CookieState.FRESH,
            )
            probes = build_cookie_health_probes(
                platforms,
                live_room_keys={("bilibili", "https://live.bilibili.com/222")},
            )

        self.assertEqual(len(probes), 1)
        selected_platform = mocked_build.call_args.args[0]
        self.assertEqual(selected_platform.room_url, "https://live.bilibili.com/222")

    def test_same_platform_different_credentials_are_checked_separately(self) -> None:
        platforms = [
            BilibiliSettings(
                room_url="https://live.bilibili.com/111",
                sessdata="sessdata-a",
            ),
            BilibiliSettings(
                room_url="https://live.bilibili.com/222",
                sessdata="sessdata-b",
            ),
        ]

        with patch("arl.windows_agent.cookie_health.build_probe") as mocked_build:
            mocked_build.side_effect = lambda platform: _ScriptedProbe(
                platform=platform.type,
                snapshot=_snapshot(
                    platform=platform.type,
                    state=LiveState.LIVE,
                    reason="ok",
                ),
                cookie_state=CookieState.FRESH,
            )
            probes = build_cookie_health_probes(platforms)

        self.assertEqual(len(probes), 2)
        selected_platforms = [call.args[0] for call in mocked_build.call_args_list]
        self.assertEqual(
            [platform.room_url for platform in selected_platforms],
            [
                "https://live.bilibili.com/111",
                "https://live.bilibili.com/222",
            ],
        )


class CookieHealthLiveRoomStateTests(unittest.TestCase):
    def test_load_live_room_keys_from_windows_agent_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "windows-agent-state.json"
            state = AgentStateFile()
            state.set(
                _snapshot(
                    platform="bilibili",
                    state=LiveState.LIVE,
                    reason="api_live_with_stream_url",
                )
            )
            state.set(
                AgentSnapshot(
                    state=LiveState.OFFLINE,
                    streamer_name="douyin-streamer",
                    room_url="https://live.example.com/douyin-offline",
                    reason="not_live",
                    detected_at=_NOW,
                    platform="douyin",
                )
            )
            state_path.write_text(
                state.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )

            keys = load_cookie_health_live_room_keys(state_path)

        self.assertEqual(keys, {("bilibili", "https://live.example.com/bilibili")})

    def test_missing_live_room_state_returns_empty_set(self) -> None:
        self.assertEqual(
            load_cookie_health_live_room_keys(Path("missing-windows-agent-state.json")),
            set(),
        )


if __name__ == "__main__":
    unittest.main()
