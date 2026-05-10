from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arl.config import DouyinSettings
from arl.shared.contracts import LiveState, SourceType
from arl.windows_agent.probe import DouyinRoomProbe


class DouyinRoomProbePlaywrightTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        script_path = root / "probe.mjs"
        script_path.write_text("// stub playwright script\n", encoding="utf-8")

        self.settings = DouyinSettings(
            room_url="https://live.douyin.com/room",
            streamer_name="streamer-a",
            playwright_script=script_path,
            use_playwright_probe=True,
            min_quality_tier="hd",
        )
        self.probe = DouyinRoomProbe(self.settings)
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_playwright_live_payload_with_stream_url_maps_to_direct_stream(self) -> None:
        payload = (
            '{"ok":true,"state":"live","sourceType":"direct_stream",'
            '"streamUrl":"https://cdn.example/live_hd.m3u8","reason":"page_marker_detected"}\n'
        )
        result = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout=payload,
            stderr="",
        )

        with patch("arl.windows_agent.probe.subprocess.run", return_value=result):
            snapshot = self.probe._probe_with_playwright(
                room_url=self.settings.room_url,
                streamer_name=self.settings.streamer_name,
                now=self.now,
            )

        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.DIRECT_STREAM)
        self.assertEqual(snapshot.stream_url, "https://cdn.example/live_hd.m3u8")
        self.assertEqual(snapshot.platform, "douyin")

    def test_playwright_live_payload_without_stream_url_keeps_browser_capture(self) -> None:
        payload = (
            '{"ok":true,"state":"live","sourceType":"browser_capture",'
            '"streamUrl":null,"reason":"page_marker_detected"}\n'
        )
        result = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout=payload,
            stderr="",
        )

        with patch("arl.windows_agent.probe.subprocess.run", return_value=result):
            snapshot = self.probe._probe_with_playwright(
                room_url=self.settings.room_url,
                streamer_name=self.settings.streamer_name,
                now=self.now,
            )

        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.BROWSER_CAPTURE)
        self.assertIsNone(snapshot.stream_url)

    def test_playwright_payload_is_parsed_when_logs_precede_json_line(self) -> None:
        payload = (
            "[probe] opening browser context\n"
            "[probe] room loaded\n"
            '{"ok":true,"state":"live","sourceType":"direct_stream",'
            '"streamUrl":"https://cdn.example/live_hd.m3u8","reason":"page_marker_detected"}\n'
        )
        result = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout=payload,
            stderr="",
        )

        with patch("arl.windows_agent.probe.subprocess.run", return_value=result):
            snapshot = self.probe._probe_with_playwright(
                room_url=self.settings.room_url,
                streamer_name=self.settings.streamer_name,
                now=self.now,
            )

        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.DIRECT_STREAM)
        self.assertEqual(snapshot.stream_url, "https://cdn.example/live_hd.m3u8")

    def test_playwright_none_stdout_is_handled_without_attribute_error(self) -> None:
        result = subprocess.CompletedProcess(
            args=["node"],
            returncode=1,
            stdout=None,
            stderr=None,
        )

        with patch("arl.windows_agent.probe.subprocess.run", return_value=result):
            snapshot = self.probe._probe_with_playwright(
                room_url=self.settings.room_url,
                streamer_name=self.settings.streamer_name,
                now=self.now,
            )

        self.assertEqual(snapshot.state, LiveState.OFFLINE)
        self.assertTrue((snapshot.reason or "").startswith("playwright_error:returncode:1"))

    def test_playwright_subprocess_uses_utf8_replace_decoding(self) -> None:
        result = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout='{"ok":false,"error":"probe_failed"}',
            stderr="",
        )
        with patch("arl.windows_agent.probe.subprocess.run", return_value=result) as run_mock:
            self.probe._probe_with_playwright(
                room_url=self.settings.room_url,
                streamer_name=self.settings.streamer_name,
                now=self.now,
            )

        _, kwargs = run_mock.call_args
        self.assertEqual(kwargs.get("encoding"), "utf-8")
        self.assertEqual(kwargs.get("errors"), "replace")

    def test_playwright_invalid_source_type_with_stream_url_falls_back_to_direct_stream(self) -> None:
        payload = (
            '{"ok":true,"state":"live","sourceType":"unexpected_type",'
            '"streamUrl":"https://cdn.example/live_hd.m3u8","reason":"page_marker_detected"}\n'
        )
        result = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout=payload,
            stderr="",
        )

        with patch("arl.windows_agent.probe.subprocess.run", return_value=result):
            snapshot = self.probe._probe_with_playwright(
                room_url=self.settings.room_url,
                streamer_name=self.settings.streamer_name,
                now=self.now,
            )

        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.DIRECT_STREAM)
        self.assertEqual(snapshot.stream_url, "https://cdn.example/live_hd.m3u8")

    def test_playwright_direct_stream_without_stream_url_downgrades_to_browser_capture(self) -> None:
        payload = (
            '{"ok":true,"state":"live","sourceType":"direct_stream",'
            '"streamUrl":null,"reason":"page_marker_detected"}\n'
        )
        result = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout=payload,
            stderr="",
        )

        with patch("arl.windows_agent.probe.subprocess.run", return_value=result):
            snapshot = self.probe._probe_with_playwright(
                room_url=self.settings.room_url,
                streamer_name=self.settings.streamer_name,
                now=self.now,
            )

        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.BROWSER_CAPTURE)
        self.assertIsNone(snapshot.stream_url)

    def test_playwright_direct_stream_below_quality_gate_is_unavailable(self) -> None:
        payload = (
            '{"ok":true,"state":"live","sourceType":"direct_stream",'
            '"streamUrl":"https://cdn.example/live/room_hd.m3u8?sign=abc",'
            '"reason":"page_marker_detected"}\n'
        )
        result = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout=payload,
            stderr="",
        )

        settings = self.settings.model_copy(update={"min_quality_tier": "uhd"})
        probe = DouyinRoomProbe(settings)
        with patch("arl.windows_agent.probe.subprocess.run", return_value=result):
            snapshot = probe._probe_with_playwright(
                room_url=settings.room_url,
                streamer_name=settings.streamer_name,
                now=self.now,
            )

        self.assertEqual(snapshot.state, LiveState.OFFLINE)
        self.assertIsNone(snapshot.source_type)
        self.assertIsNone(snapshot.stream_url)
        self.assertEqual(snapshot.reason, "quality_below_min_tier:hd<uhd")

    def test_detect_falls_back_to_http_when_playwright_fails(self) -> None:
        failing_payload = '{"ok":false,"error":"browser_crashed"}\n'
        playwright_result = subprocess.CompletedProcess(
            args=["node"],
            returncode=1,
            stdout=failing_payload,
            stderr="browser crashed",
        )
        http_response = SimpleNamespace(
            status_code=200,
            text=(
                '<html><script>'
                '"hls_pull_url":"https:\\/\\/pull.example.com\\/live\\/abc_hd.m3u8?token=1&sign=hls"'
                "</script></html>"
            ),
        )
        with (
            patch("arl.windows_agent.probe.subprocess.run", return_value=playwright_result),
            patch("arl.windows_agent.probe.httpx.get", return_value=http_response),
        ):
            snapshot = self.probe.detect()

        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.DIRECT_STREAM)
        self.assertEqual(snapshot.reason, "stream_url_detected_http")
        self.assertEqual(snapshot.stream_url, "https://pull.example.com/live/abc_hd.m3u8?token=1&sign=hls")

    def test_detect_http_live_marker_uses_direct_stream_when_available(self) -> None:
        settings = self.settings.model_copy(update={"use_playwright_probe": False})
        probe = DouyinRoomProbe(settings)
        http_response = SimpleNamespace(
            status_code=200,
            text=(
                '<html><body>鐩存挱涓?script>'
                '"stream_url":"https%3A%2F%2Fpull.example.com%2Flive%2Froom_hd.m3u8%3Ftoken%3D1%26sign%3Dxyz"'
                "</script></body></html>"
            ),
        )
        with patch("arl.windows_agent.probe.httpx.get", return_value=http_response):
            snapshot = probe.detect()

        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.DIRECT_STREAM)
        self.assertEqual(snapshot.stream_url, "https://pull.example.com/live/room_hd.m3u8?token=1&sign=xyz")

    def test_detect_http_percent_encoded_direct_url_without_markers_is_still_live(self) -> None:
        settings = self.settings.model_copy(update={"use_playwright_probe": False})
        probe = DouyinRoomProbe(settings)
        http_response = SimpleNamespace(
            status_code=200,
            text=(
                '<html><body><script>'
                '"https%3A%2F%2Fpull.example.com%2Flive%2Fencoded-room_hd.m3u8%3Ftoken%3D1%26sign%3Dabc"'
                "</script></body></html>"
            ),
        )
        with patch("arl.windows_agent.probe.httpx.get", return_value=http_response):
            snapshot = probe.detect()

        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.DIRECT_STREAM)
        self.assertEqual(snapshot.reason, "stream_url_detected_http")
        self.assertEqual(
            snapshot.stream_url,
            "https://pull.example.com/live/encoded-room_hd.m3u8?token=1&sign=abc",
        )

    def test_detect_http_multilayer_percent_encoded_and_x_escaped_stream_url(self) -> None:
        settings = self.settings.model_copy(update={"use_playwright_probe": False})
        probe = DouyinRoomProbe(settings)
        http_response = SimpleNamespace(
            status_code=200,
            text=(
                '<html><body><script>'
                '"stream_url":"\\x68\\x74\\x74\\x70\\x73%253A%252F%252Fpull.example.com%252Flive%252Fdeep-room_hd.m3u8%253Ftoken%253D1%2526sign%253Dabc"'
                "</script></body></html>"
            ),
        )
        with patch("arl.windows_agent.probe.httpx.get", return_value=http_response):
            snapshot = probe.detect()

        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.DIRECT_STREAM)
        self.assertEqual(snapshot.reason, "stream_url_detected_http")
        self.assertEqual(
            snapshot.stream_url,
            "https://pull.example.com/live/deep-room_hd.m3u8?token=1&sign=abc",
        )

    def test_detect_http_ignores_static_assets_without_live_markers(self) -> None:
        settings = self.settings.model_copy(update={"use_playwright_probe": False})
        probe = DouyinRoomProbe(settings)
        http_response = SimpleNamespace(
            status_code=200,
            text=(
                '<html><body>'
                '<script src="https://cdn.example.com/app.js"></script>'
                '<img src="https://cdn.example.com/live.png" />'
                "</body></html>"
            ),
        )
        with patch("arl.windows_agent.probe.httpx.get", return_value=http_response):
            snapshot = probe.detect()

        self.assertEqual(snapshot.state, LiveState.OFFLINE)
        self.assertIsNone(snapshot.source_type)
        self.assertIsNone(snapshot.stream_url)
        self.assertEqual(snapshot.reason, "live_state_unknown")

class DouyinRoomProbeStreamUrlScoreTests(unittest.TestCase):
    """_stream_url_score must rank Douyin quality tiers explicitly so the
    recorder gets the highest available variant. LoL streams expose 6+ tiers
    in the page (origin/uhd/hd/sd/md/ld) and without explicit ranking we end
    up with arbitrary 720p instead of 1080p60.
    """

    def test_quality_tier_score_strictly_descending(self) -> None:
        url_origin = "http://pull-hls.douyincdn.com/game/stream-1_origin.m3u8?t=x"
        url_uhd = "http://pull-hls.douyincdn.com/game/stream-1_uhd.m3u8?t=x"
        url_hd = "http://pull-hls.douyincdn.com/game/stream-1_hd.m3u8?t=x"
        url_sd = "http://pull-hls.douyincdn.com/game/stream-1_sd.m3u8?t=x"
        url_md = "http://pull-hls.douyincdn.com/game/stream-1_md/playlist.m3u8?t=x"
        url_ld = "http://pull-hls.douyincdn.com/game/stream-1_ld.m3u8?t=x"

        scores = [DouyinRoomProbe._stream_url_score(u) for u in (
            url_origin, url_uhd, url_hd, url_sd, url_md, url_ld,
        )]
        # Strictly descending: origin > uhd > hd > sd > md > ld.
        self.assertEqual(scores, sorted(scores, reverse=True))
        for a, b in zip(scores, scores[1:]):
            self.assertGreater(a, b)

    def test_origin_wins_against_lower_tiers_in_candidate_set(self) -> None:
        candidates = {
            "http://pull-hls.douyincdn.com/game/stream-1_sd.m3u8?t=x",
            "http://pull-hls.douyincdn.com/game/stream-1_origin.m3u8?t=y",
            "http://pull-hls.douyincdn.com/game/stream-1_md/playlist.m3u8?t=z",
        }
        best = max(candidates, key=DouyinRoomProbe._stream_url_score)
        self.assertIn("_origin.m3u8", best)

    def test_query_string_substrings_do_not_false_match_tier(self) -> None:
        # Real Douyin URLs sign with tokens that may contain literal "_sd" /
        # "_md" / "_hd" inside ?keeptime=...&wsSecret=...; those must not
        # boost the score.
        plain_url = "http://pull-hls.douyincdn.com/game/stream-1.m3u8"
        polluted_query_url = (
            "http://pull-hls.douyincdn.com/game/stream-1.m3u8"
            "?token=fake_origin_uhd_hd_sd_md_ld"
        )
        self.assertEqual(
            DouyinRoomProbe._stream_url_score(plain_url),
            DouyinRoomProbe._stream_url_score(polluted_query_url),
        )

    def test_origin_hls_suffix_matches_origin_tier(self) -> None:
        # Douyin sometimes serves "_origin_hls.flv" 鈥?the leading "_origin"
        # should still be detected even though the path continues.
        url = "http://pull-flv.douyincdn.com/game/stream-1_origin_hls.flv?t=x"
        # Score must include the origin-tier bonus (1000), not just the flv (40)
        # + pull (8) + stream (6) + live (4) + hls (10) base = 68.
        self.assertGreater(DouyinRoomProbe._stream_url_score(url), 1000)


class DouyinRoomProbeQualityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._settings = DouyinSettings(
            room_url="https://live.douyin.com/room",
            streamer_name="streamer-gate",
            use_playwright_probe=False,
            min_quality_tier="uhd",
        )

    def test_quality_tier_below_threshold_is_unavailable(self) -> None:
        probe = DouyinRoomProbe(self._settings)
        http_response = SimpleNamespace(
            status_code=200,
            text=(
                '<html><body>閻╁瓨鎸辨稉?script>'
                '"stream_url":"https%3A%2F%2Fpull.example.com%2Flive%2Froom_hd.m3u8%3Ftoken%3D1%26sign%3Dxyz"'
                "</script></body></html>"
            ),
        )
        with patch("arl.windows_agent.probe.httpx.get", return_value=http_response):
            snapshot = probe.detect()

        self.assertEqual(snapshot.state, LiveState.OFFLINE)
        self.assertTrue((snapshot.reason or "").startswith("quality_below_min_tier:"))

    def test_quality_tier_meets_threshold_is_live(self) -> None:
        probe = DouyinRoomProbe(self._settings)
        http_response = SimpleNamespace(
            status_code=200,
            text=(
                '<html><body>閻╁瓨鎸辨稉?script>'
                '"stream_url":"https%3A%2F%2Fpull.example.com%2Flive%2Froom_uhd.m3u8%3Ftoken%3D1%26sign%3Dxyz"'
                "</script></body></html>"
            ),
        )
        with patch("arl.windows_agent.probe.httpx.get", return_value=http_response):
            snapshot = probe.detect()

        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.DIRECT_STREAM)

    def test_unknown_tier_is_unavailable(self) -> None:
        probe = DouyinRoomProbe(self._settings)
        http_response = SimpleNamespace(
            status_code=200,
            text=(
                '<html><body><script>'
                '"stream_url":"https%3A%2F%2Fpull.example.com%2Flive%2Froom.m3u8%3Ftoken%3D1%26sign%3Dxyz"'
                "</script></body></html>"
            ),
        )
        with patch("arl.windows_agent.probe.httpx.get", return_value=http_response):
            snapshot = probe.detect()

        self.assertEqual(snapshot.state, LiveState.OFFLINE)
        self.assertTrue((snapshot.reason or "").startswith("quality_tier_unknown:"))


class DouyinRoomProbeCookieInjectionTests(unittest.TestCase):
    """PR6.B 鈥?ARL_DOUYIN_COOKIE injection across all three pipelines:
    Playwright subprocess --cookie arg, httpx HTTP-fallback Cookie header,
    and stream_headers() so ffmpeg gets the same cookie via -headers.

    Empty cookie must keep PR5 behavior byte-identical: no --cookie arg,
    no Cookie header, and stream_headers() returns {} (so the orchestration
    contract's "Douyin emits {} when no cookie configured" stays true).
    """

    _COOKIE = "fake-cookie-for-testing=val1; sid=val2"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        script_path = root / "probe.mjs"
        script_path.write_text("// stub\n", encoding="utf-8")
        self._script_path = script_path

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _settings(self, *, cookie: str, use_playwright: bool = True) -> DouyinSettings:
        return DouyinSettings(
            room_url="https://live.douyin.com/room",
            streamer_name="streamer-cookie",
            playwright_script=self._script_path,
            use_playwright_probe=use_playwright,
            cookie=cookie,
        )

    def test_cookie_appended_to_playwright_command_when_set(self) -> None:
        probe = DouyinRoomProbe(self._settings(cookie=self._COOKIE))
        result = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout='{"ok":true,"state":"offline","reason":"page_marker_detected"}\n',
            stderr="",
        )
        with patch(
            "arl.windows_agent.probe.subprocess.run",
            return_value=result,
        ) as mock_run:
            probe._probe_with_playwright(
                room_url=probe.settings.room_url,
                streamer_name=probe.settings.streamer_name,
                now=datetime.now(timezone.utc),
            )

        command_args = mock_run.call_args.args[0]
        self.assertIn("--cookie", command_args)
        cookie_idx = command_args.index("--cookie")
        self.assertEqual(command_args[cookie_idx + 1], self._COOKIE)
        # Empty-cookie path must NOT append --cookie at all (defends against
        # passing the literal empty string and confusing the .mjs script).
        probe_no_cookie = DouyinRoomProbe(self._settings(cookie=""))
        with patch(
            "arl.windows_agent.probe.subprocess.run",
            return_value=result,
        ) as mock_run_empty:
            probe_no_cookie._probe_with_playwright(
                room_url=probe_no_cookie.settings.room_url,
                streamer_name=probe_no_cookie.settings.streamer_name,
                now=datetime.now(timezone.utc),
            )
        self.assertNotIn("--cookie", mock_run_empty.call_args.args[0])

    def test_cookie_injected_into_http_fallback_headers(self) -> None:
        # use_playwright=True + a forced playwright failure to drive detect()
        # into the httpx fallback path; verify the Cookie header lands on the
        # httpx.get call.
        probe = DouyinRoomProbe(self._settings(cookie=self._COOKIE))
        playwright_fail = subprocess.CompletedProcess(
            args=["node"],
            returncode=1,
            stdout='{"ok":false,"error":"browser_crashed"}\n',
            stderr="",
        )
        http_response = SimpleNamespace(
            status_code=200,
            text='<html><body>鏆傛湭寮€鎾?/body></html>',
        )
        with (
            patch(
                "arl.windows_agent.probe.subprocess.run",
                return_value=playwright_fail,
            ),
            patch(
                "arl.windows_agent.probe.httpx.get",
                return_value=http_response,
            ) as mock_get,
        ):
            probe.detect()

        headers = mock_get.call_args.kwargs["headers"]
        self.assertEqual(headers["cookie"], self._COOKIE)
        # User-Agent must remain so the Douyin CDN doesn't reject the
        # request as a non-browser client.
        self.assertIn("Mozilla/5.0", headers["user-agent"])

    def test_stream_headers_include_cookie_when_set_and_empty_when_not(self) -> None:
        with_cookie = DouyinRoomProbe(self._settings(cookie=self._COOKIE))
        self.assertEqual(with_cookie.stream_headers(), {"Cookie": self._COOKIE})
        without_cookie = DouyinRoomProbe(self._settings(cookie=""))
        self.assertEqual(without_cookie.stream_headers(), {})

    def test_uhd_url_with_backslash_unicode_escape_is_extracted_signed(self) -> None:
        # Regression: Douyin's HTML JSON-encodes `&` as `&`. Earlier the
        # _URL_PATTERN char class excluded backslash, truncating the URL at
        # `&` and dropping the `sign=` segment 鈥?making every signed _uhd
        # URL look unsigned and get rejected. With backslash allowed in the
        # match and _normalize_stream_url decoding `&` 鈫?`&` afterward,
        # the signed _uhd candidate must reach the score function and beat
        # the lower _hd tier.
        html = (
            '<script>"data":{"uhd":'
            '{"main":{"flv":"http://pull-flv-q13.douyincdn.com/thirdgame/'
            'stream-1_uhd.flv?expire=1\\u0026sign=abcdef\\u0026t=1"}},'
            '"hd":'
            '{"main":{"flv":"http://pull-flv-q13.douyincdn.com/thirdgame/'
            'stream-1_hd.flv?expire=1\\u0026sign=fedcba\\u0026t=1"}}}'
            "</script>"
        )
        candidates = DouyinRoomProbe._extract_stream_url_candidates(html)
        self.assertTrue(
            any("_uhd.flv" in u and "sign=abcdef" in u for u in candidates),
            f"signed _uhd URL must survive extraction; got {candidates!r}",
        )
        picked = DouyinRoomProbe._extract_stream_url(html)
        self.assertIsNotNone(picked)
        self.assertIn("_uhd.flv", picked)
        self.assertIn("sign=abcdef", picked)


if __name__ == "__main__":
    unittest.main()
