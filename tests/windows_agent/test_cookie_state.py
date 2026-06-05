from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import ClassVar

from arl.config import BilibiliSettings, DouyinSettings
from arl.shared.contracts import LiveState, SourceType
from arl.windows_agent.bilibili_probe import BilibiliRoomProbe
from arl.windows_agent.models import AgentSnapshot
from arl.windows_agent.platform_probe import CookieState, PlatformProbe
from arl.windows_agent.probe import DouyinRoomProbe


_NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)


def _live_snapshot(platform: str, *, reason: str | None = "live") -> AgentSnapshot:
    return AgentSnapshot(
        state=LiveState.LIVE,
        streamer_name=f"{platform}-streamer",
        room_url=f"https://live.example.com/{platform}",
        source_type=SourceType.DIRECT_STREAM,
        stream_url="https://cdn.example/live.m3u8",
        reason=reason,
        detected_at=_NOW,
        platform=platform,
    )


def _offline_snapshot(platform: str, *, reason: str | None) -> AgentSnapshot:
    return AgentSnapshot(
        state=LiveState.OFFLINE,
        streamer_name=f"{platform}-streamer",
        room_url=f"https://live.example.com/{platform}",
        reason=reason,
        detected_at=_NOW,
        platform=platform,
    )


class _BareProbe(PlatformProbe):
    """Concrete probe that doesn't override classify_cookie_state.

    Used to assert the base implementation's safe default.
    """

    platform_name: ClassVar[str] = "bare"

    def detect(self) -> AgentSnapshot:
        return _live_snapshot(self.platform_name)


class PlatformProbeBaseDefaultTests(unittest.TestCase):
    def test_default_returns_not_configured_for_any_snapshot(self) -> None:
        probe = _BareProbe()
        live = _live_snapshot("bare")
        offline = _offline_snapshot("bare", reason="not_live")
        self.assertEqual(probe.classify_cookie_state(live), CookieState.NOT_CONFIGURED)
        self.assertEqual(probe.classify_cookie_state(offline), CookieState.NOT_CONFIGURED)


class BilibiliCookieStateTests(unittest.TestCase):
    def _probe(self, *, sessdata: str = "") -> BilibiliRoomProbe:
        return BilibiliRoomProbe(
            BilibiliSettings(
                room_url="https://live.bilibili.com/12345",
                streamer_name="bili-streamer",
                sessdata=sessdata,
            )
        )

    def test_sessdata_set_and_code_minus_101_reason_returns_expired(self) -> None:
        probe = self._probe(sessdata="abc")
        snapshot = _offline_snapshot(
            "bilibili",
            reason="api_error:code=-101:账号未登录",
        )
        self.assertEqual(probe.classify_cookie_state(snapshot), CookieState.EXPIRED)

    def test_sessdata_set_and_playinfo_code_minus_101_returns_expired(self) -> None:
        probe = self._probe(sessdata="abc")
        snapshot = _live_snapshot(
            "bilibili",
            reason="playinfo_error:api_error:code=-101:account_not_logged_in",
        )
        self.assertEqual(probe.classify_cookie_state(snapshot), CookieState.EXPIRED)

    def test_sessdata_set_and_live_returns_fresh(self) -> None:
        probe = self._probe(sessdata="abc")
        snapshot = _live_snapshot("bilibili", reason="api_live_with_stream_url")
        self.assertEqual(probe.classify_cookie_state(snapshot), CookieState.FRESH)

    def test_sessdata_set_and_unrelated_offline_reason_returns_fresh(self) -> None:
        # Streamer offline with a non-cookie reason — cookie itself is fine.
        probe = self._probe(sessdata="abc")
        snapshot = _offline_snapshot("bilibili", reason="not_live")
        self.assertEqual(probe.classify_cookie_state(snapshot), CookieState.FRESH)

    def test_sessdata_set_and_quality_gate_failure_returns_fresh(self) -> None:
        # Bilibili quality gate (qn-based) has its own reason taxonomy and is
        # NOT a cookie-expiration signal; only code=-101 is.
        probe = self._probe(sessdata="abc")
        snapshot = _offline_snapshot(
            "bilibili",
            reason="quality_below_min_qn:250<400",
        )
        self.assertEqual(probe.classify_cookie_state(snapshot), CookieState.FRESH)

    def test_no_sessdata_and_code_minus_101_returns_not_configured(self) -> None:
        # Even though API returned -101, the user never authenticated, so
        # it is meaningless to call this "expired".
        probe = self._probe(sessdata="")
        snapshot = _offline_snapshot(
            "bilibili",
            reason="api_error:code=-101:账号未登录",
        )
        self.assertEqual(probe.classify_cookie_state(snapshot), CookieState.NOT_CONFIGURED)

    def test_no_sessdata_and_live_returns_not_configured(self) -> None:
        probe = self._probe(sessdata="")
        snapshot = _live_snapshot("bilibili", reason="api_live_with_stream_url")
        self.assertEqual(probe.classify_cookie_state(snapshot), CookieState.NOT_CONFIGURED)


class DouyinCookieStateTests(unittest.TestCase):
    def _probe(self, *, cookie: str = "", min_quality_tier: str = "uhd") -> DouyinRoomProbe:
        return DouyinRoomProbe(
            DouyinSettings(
                room_url="https://live.douyin.com/abc",
                streamer_name="douyin-streamer",
                cookie=cookie,
                min_quality_tier=min_quality_tier,
            )
        )

    def test_cookie_set_and_hd_baseline_rejection_returns_expired(self) -> None:
        # ARL_DOUYIN_COOKIE expired -> page falls back to anonymous _hd
        # (720p60) signed URLs only -> strict gate rejects with hd<uhd.
        probe = self._probe(cookie="sessionid=abc; uid=42")
        snapshot = _offline_snapshot(
            "douyin",
            reason="quality_below_min_tier:hd<uhd",
        )
        self.assertEqual(probe.classify_cookie_state(snapshot), CookieState.EXPIRED)

    def test_cookie_set_and_live_returns_fresh(self) -> None:
        probe = self._probe(cookie="sessionid=abc; uid=42")
        snapshot = _live_snapshot("douyin", reason="page_marker_detected")
        self.assertEqual(probe.classify_cookie_state(snapshot), CookieState.FRESH)

    def test_cookie_set_and_sub_baseline_rejection_returns_fresh(self) -> None:
        # quality_below_min_tier:sd<uhd is below the anonymous baseline,
        # which means cookie expiration is not the high-confidence cause
        # (could be streamer bandwidth issue, upstream CDN, etc.).
        probe = self._probe(cookie="sessionid=abc; uid=42")
        snapshot = _offline_snapshot(
            "douyin",
            reason="quality_below_min_tier:sd<uhd",
        )
        self.assertEqual(probe.classify_cookie_state(snapshot), CookieState.FRESH)

    def test_cookie_set_and_tier_unknown_returns_fresh(self) -> None:
        probe = self._probe(cookie="sessionid=abc; uid=42")
        snapshot = _offline_snapshot(
            "douyin",
            reason="quality_tier_unknown:min_required=uhd",
        )
        self.assertEqual(probe.classify_cookie_state(snapshot), CookieState.FRESH)

    def test_no_cookie_and_hd_rejection_returns_not_configured(self) -> None:
        probe = self._probe(cookie="")
        snapshot = _offline_snapshot(
            "douyin",
            reason="quality_below_min_tier:hd<uhd",
        )
        self.assertEqual(probe.classify_cookie_state(snapshot), CookieState.NOT_CONFIGURED)

    def test_no_cookie_and_live_returns_not_configured(self) -> None:
        probe = self._probe(cookie="")
        snapshot = _live_snapshot("douyin", reason="page_marker_detected")
        self.assertEqual(probe.classify_cookie_state(snapshot), CookieState.NOT_CONFIGURED)


if __name__ == "__main__":
    unittest.main()
