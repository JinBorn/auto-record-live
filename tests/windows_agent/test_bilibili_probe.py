from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from arl.config import BilibiliSettings
from arl.shared.contracts import LiveState, SourceType
from arl.windows_agent.bilibili_probe import BilibiliRoomProbe


def _ok_status_payload(live_status: int) -> dict:
    return {"code": 0, "message": "0", "data": {"live_status": live_status}}


def _ok_playinfo_payload() -> dict:
    # B 站 把 URL 拆成 host + base_url + extra；测试断言会把它们拼回来后等于
    # _expected_stream_url()，所以这里只定义 parts，不在 default-arg 里写
    # 一个先于变量定义的字面量字符串。
    host = "https://cn-pull.example.com"
    base_url = "/live/abc.flv"
    extra = "?token=xyz&expires=999"
    return {
        "code": 0,
        "message": "0",
        "data": {
            "playurl_info": {
                "playurl": {
                    "stream": [
                        {
                            "format": [
                                {
                                    "codec": [
                                        {
                                            "current_qn": 400,
                                            "bitrate": 6000,
                                            "base_url": base_url,
                                            "url_info": [
                                                {
                                                    "host": host,
                                                    "extra": extra,
                                                },
                                            ],
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                },
            },
        },
    }


def _expected_stream_url() -> str:
    return "https://cn-pull.example.com/live/abc.flv?token=xyz&expires=999"


def _http_response(status_code: int, payload: dict | str) -> SimpleNamespace:
    if isinstance(payload, dict):
        def _json() -> dict:
            return payload
    else:
        def _json() -> dict:
            raise ValueError(payload)
    return SimpleNamespace(status_code=status_code, json=_json)


class BilibiliRoomProbeBasicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = BilibiliSettings(
            room_url="https://live.bilibili.com/12345",
            streamer_name="bili-streamer",
        )
        self.probe = BilibiliRoomProbe(self.settings)

    def test_stream_headers_include_referer_and_user_agent(self) -> None:
        headers = self.probe.stream_headers()
        self.assertEqual(headers["Referer"], "https://live.bilibili.com")
        self.assertIn("Mozilla/5.0", headers["User-Agent"])

    def test_detect_live_status_one_with_stream_url_returns_direct_stream(self) -> None:
        responses = [
            _http_response(200, _ok_status_payload(1)),
            _http_response(200, _ok_playinfo_payload()),
        ]
        with patch(
            "arl.windows_agent.bilibili_probe.httpx.get",
            side_effect=responses,
        ):
            snapshot = self.probe.detect()

        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.platform, "bilibili")
        self.assertEqual(snapshot.source_type, SourceType.DIRECT_STREAM)
        self.assertEqual(snapshot.stream_url, _expected_stream_url())
        self.assertEqual(snapshot.stream_headers["Referer"], "https://live.bilibili.com")
        self.assertEqual(snapshot.reason, "api_live_with_stream_url")

    def test_detect_live_status_two_carousel_maps_to_offline_with_specific_reason(self) -> None:
        responses = [_http_response(200, _ok_status_payload(2))]
        with patch(
            "arl.windows_agent.bilibili_probe.httpx.get",
            side_effect=responses,
        ):
            snapshot = self.probe.detect()

        self.assertEqual(snapshot.state, LiveState.OFFLINE)
        self.assertEqual(snapshot.platform, "bilibili")
        self.assertEqual(snapshot.reason, "carousel_playback")
        self.assertIsNone(snapshot.source_type)
        self.assertIsNone(snapshot.stream_url)

    def test_detect_live_status_zero_offline(self) -> None:
        responses = [_http_response(200, _ok_status_payload(0))]
        with patch(
            "arl.windows_agent.bilibili_probe.httpx.get",
            side_effect=responses,
        ):
            snapshot = self.probe.detect()

        self.assertEqual(snapshot.state, LiveState.OFFLINE)
        self.assertEqual(snapshot.reason, "not_live")

    def test_detect_live_with_missing_stream_url_falls_back_to_browser_capture(self) -> None:
        empty_playinfo = {
            "code": 0,
            "data": {"playurl_info": {"playurl": {"stream": []}}},
        }
        responses = [
            _http_response(200, _ok_status_payload(1)),
            _http_response(200, empty_playinfo),
        ]
        with patch(
            "arl.windows_agent.bilibili_probe.httpx.get",
            side_effect=responses,
        ):
            snapshot = self.probe.detect()

        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.BROWSER_CAPTURE)
        self.assertIsNone(snapshot.stream_url)
        self.assertEqual(snapshot.reason, "stream_url_missing")
        # Even when DIRECT_STREAM degrades, headers must still flow through so
        # the recorder's browser_capture path can use them if it ever needs to.
        self.assertEqual(snapshot.stream_headers["Referer"], "https://live.bilibili.com")


class BilibiliRoomProbeErrorPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = BilibiliSettings(
            room_url="https://live.bilibili.com/12345",
            streamer_name="bili-streamer",
        )
        self.probe = BilibiliRoomProbe(self.settings)

    def test_no_room_url_configured_returns_offline(self) -> None:
        probe = BilibiliRoomProbe(BilibiliSettings(room_url="", streamer_name=""))
        snapshot = probe.detect()
        self.assertEqual(snapshot.state, LiveState.OFFLINE)
        self.assertEqual(snapshot.reason, "room_url_not_configured")

    def test_unparseable_room_url_returns_offline(self) -> None:
        probe = BilibiliRoomProbe(
            BilibiliSettings(
                room_url="https://example.com/not-a-bilibili-url",
                streamer_name="x",
            )
        )
        snapshot = probe.detect()
        self.assertEqual(snapshot.state, LiveState.OFFLINE)
        self.assertEqual(snapshot.reason, "room_id_not_parsed")

    def test_status_endpoint_http_error_returns_offline_without_raising(self) -> None:
        with patch(
            "arl.windows_agent.bilibili_probe.httpx.get",
            side_effect=httpx.ConnectError("boom"),
        ):
            snapshot = self.probe.detect()
        self.assertEqual(snapshot.state, LiveState.OFFLINE)
        self.assertTrue(snapshot.reason and snapshot.reason.startswith("http_error:"))

    def test_status_endpoint_4xx_returns_offline_with_status_reason(self) -> None:
        responses = [_http_response(403, {})]
        with patch(
            "arl.windows_agent.bilibili_probe.httpx.get",
            side_effect=responses,
        ):
            snapshot = self.probe.detect()
        self.assertEqual(snapshot.state, LiveState.OFFLINE)
        self.assertEqual(snapshot.reason, "http_status:403")

    def test_status_endpoint_negative_api_code_returns_offline(self) -> None:
        responses = [
            _http_response(200, {"code": -403, "message": "illegal access", "data": {}}),
        ]
        with patch(
            "arl.windows_agent.bilibili_probe.httpx.get",
            side_effect=responses,
        ):
            snapshot = self.probe.detect()
        self.assertEqual(snapshot.state, LiveState.OFFLINE)
        self.assertTrue(snapshot.reason and "api_error:code=-403" in snapshot.reason)

    def test_playinfo_endpoint_failure_keeps_live_with_browser_capture_fallback(self) -> None:
        responses = [
            _http_response(200, _ok_status_payload(1)),
            # Second call raises after we've already established LIVE.
        ]
        with patch(
            "arl.windows_agent.bilibili_probe.httpx.get",
            side_effect=[*responses, httpx.ReadTimeout("slow")],
        ):
            snapshot = self.probe.detect()
        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.BROWSER_CAPTURE)
        self.assertTrue(
            snapshot.reason and snapshot.reason.startswith("playinfo_http_error:")
        )


class BilibiliRoomProbeQnPriorityTests(unittest.TestCase):
    """_extract_stream_url must pick the codec entry with the highest
    current_qn. B 站 returns multiple qn variants in one response (qn=10000
    原画 / qn=400 蓝光 / qn=250 超清 / qn=150 高清); the legacy implementation
    returned the first one walked, which is typically the lowest variant.
    """

    @staticmethod
    def _multi_qn_payload() -> dict:
        return {
            "code": 0,
            "message": "0",
            "data": {
                "playurl_info": {
                    "playurl": {
                        "stream": [
                            {
                                "format": [
                                    {
                                        "codec": [
                                            {
                                                "current_qn": 250,
                                                "base_url": "/live/abc_qn250.flv",
                                                "url_info": [{"host": "https://qn250.example.com", "extra": "?t=250"}],
                                            },
                                            {
                                                "current_qn": 10000,
                                                "base_url": "/live/abc_qn10000.flv",
                                                "url_info": [{"host": "https://qn10000.example.com", "extra": "?t=10000"}],
                                            },
                                            {
                                                "current_qn": 400,
                                                "base_url": "/live/abc_qn400.flv",
                                                "url_info": [{"host": "https://qn400.example.com", "extra": "?t=400"}],
                                            },
                                        ],
                                    },
                                ],
                            },
                        ],
                    },
                },
            },
        }

    def test_picks_highest_current_qn_variant(self) -> None:
        url = BilibiliRoomProbe._extract_stream_url(self._multi_qn_payload())
        self.assertEqual(url, "https://qn10000.example.com/live/abc_qn10000.flv?t=10000")

    def test_missing_current_qn_treated_as_zero_so_others_win(self) -> None:
        payload = {
            "code": 0,
            "message": "0",
            "data": {
                "playurl_info": {
                    "playurl": {
                        "stream": [
                            {
                                "format": [
                                    {
                                        "codec": [
                                            {
                                                "base_url": "/live/no_qn.flv",
                                                "url_info": [{"host": "https://noqn.example.com", "extra": ""}],
                                            },
                                            {
                                                "current_qn": 150,
                                                "base_url": "/live/qn150.flv",
                                                "url_info": [{"host": "https://qn150.example.com", "extra": ""}],
                                            },
                                        ],
                                    },
                                ],
                            },
                        ],
                    },
                },
            },
        }
        url = BilibiliRoomProbe._extract_stream_url(payload)
        self.assertEqual(url, "https://qn150.example.com/live/qn150.flv")

    def test_bool_current_qn_does_not_sneak_through_as_one(self) -> None:
        payload = {
            "code": 0,
            "message": "0",
            "data": {
                "playurl_info": {
                    "playurl": {
                        "stream": [
                            {
                                "format": [
                                    {
                                        "codec": [
                                            {
                                                "current_qn": True,
                                                "base_url": "/live/bool.flv",
                                                "url_info": [{"host": "https://bool.example.com", "extra": ""}],
                                            },
                                            {
                                                "current_qn": 10,
                                                "base_url": "/live/qn10.flv",
                                                "url_info": [{"host": "https://qn10.example.com", "extra": ""}],
                                            },
                                        ],
                                    },
                                ],
                            },
                        ],
                    },
                },
            },
        }
        url = BilibiliRoomProbe._extract_stream_url(payload)
        self.assertEqual(url, "https://qn10.example.com/live/qn10.flv")

    def test_single_codec_without_current_qn_still_returns_url(self) -> None:
        url = BilibiliRoomProbe._extract_stream_url(_ok_playinfo_payload())
        self.assertEqual(url, _expected_stream_url())


class BilibiliRoomProbeQualityGateTests(unittest.TestCase):
    def _build_probe(
        self,
        *,
        min_qn: int = 400,
        min_bitrate_kbps: int = 4500,
    ) -> BilibiliRoomProbe:
        settings = BilibiliSettings(
            room_url="https://live.bilibili.com/12345",
            streamer_name="bili-streamer",
            min_stream_qn=min_qn,
            min_stream_bitrate_kbps=min_bitrate_kbps,
        )
        return BilibiliRoomProbe(settings)

    @staticmethod
    def _playinfo_payload(*, qn: int, bitrate_kbps: int | None = None) -> dict:
        codec_entry: dict[str, object] = {
            "current_qn": qn,
            "base_url": "/live/abc.flv",
            "url_info": [{"host": "https://cdn.example.com", "extra": "?token=xyz"}],
        }
        if bitrate_kbps is not None:
            codec_entry["bitrate"] = bitrate_kbps
        return {
            "code": 0,
            "message": "0",
            "data": {
                "playurl_info": {
                    "playurl": {
                        "stream": [
                            {"format": [{"codec": [codec_entry]}]},
                        ]
                    }
                }
            },
        }

    def test_detect_rejects_qn_below_threshold(self) -> None:
        probe = self._build_probe(min_qn=400)
        responses = [
            _http_response(200, _ok_status_payload(1)),
            _http_response(200, self._playinfo_payload(qn=250, bitrate_kbps=6000)),
        ]
        with patch("arl.windows_agent.bilibili_probe.httpx.get", side_effect=responses):
            snapshot = probe.detect()
        self.assertEqual(snapshot.state, LiveState.OFFLINE)
        self.assertTrue((snapshot.reason or "").startswith("quality_below_min_qn:"))

    def test_detect_rejects_bitrate_below_threshold_when_present(self) -> None:
        probe = self._build_probe(min_qn=400, min_bitrate_kbps=6000)
        responses = [
            _http_response(200, _ok_status_payload(1)),
            _http_response(200, self._playinfo_payload(qn=400, bitrate_kbps=2500)),
        ]
        with patch("arl.windows_agent.bilibili_probe.httpx.get", side_effect=responses):
            snapshot = probe.detect()
        self.assertEqual(snapshot.state, LiveState.OFFLINE)
        self.assertTrue((snapshot.reason or "").startswith("quality_below_min_bitrate:"))

    def test_detect_accepts_when_qn_and_bitrate_meet_threshold(self) -> None:
        probe = self._build_probe(min_qn=400, min_bitrate_kbps=4500)
        responses = [
            _http_response(200, _ok_status_payload(1)),
            _http_response(200, self._playinfo_payload(qn=400, bitrate_kbps=6000)),
        ]
        with patch("arl.windows_agent.bilibili_probe.httpx.get", side_effect=responses):
            snapshot = probe.detect()
        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.DIRECT_STREAM)
        self.assertEqual(snapshot.reason, "api_live_with_stream_url")

    def test_detect_accepts_when_bitrate_metadata_missing(self) -> None:
        probe = self._build_probe(min_qn=400, min_bitrate_kbps=9000)
        responses = [
            _http_response(200, _ok_status_payload(1)),
            _http_response(200, self._playinfo_payload(qn=400, bitrate_kbps=None)),
        ]
        with patch("arl.windows_agent.bilibili_probe.httpx.get", side_effect=responses):
            snapshot = probe.detect()
        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.DIRECT_STREAM)


class BilibiliRoomProbeCookieInjectionTests(unittest.TestCase):
    """PR6.A — SESSDATA cookie injection through stream_headers + _fetch_json.

    When ARL_BILIBILI_SESSDATA is set, the probe must:
    - emit Cookie: SESSDATA=<value> in stream_headers() so the recorder's
      _build_ffmpeg_header_args forwards it to ffmpeg.
    - send the same Cookie header on every _fetch_json call so the API
      returns qn>=400 (1080P 蓝光) variants instead of being capped at qn=250
      under anonymous access.

    Empty sessdata must be byte-identical to PR5 behavior — no Cookie key in
    either stream_headers() or _fetch_json call headers — to defend against
    accidentally sending "Cookie: SESSDATA=" with an empty value.
    """

    _SESSDATA = "fake-sessdata-for-testing"

    def _settings(self, sessdata: str) -> BilibiliSettings:
        return BilibiliSettings(
            room_url="https://live.bilibili.com/12345",
            streamer_name="bili-streamer",
            sessdata=sessdata,
        )

    def test_sessdata_injected_into_fetch_json_headers(self) -> None:
        probe = BilibiliRoomProbe(self._settings(self._SESSDATA))
        responses = [
            _http_response(200, _ok_status_payload(1)),
            _http_response(200, _ok_playinfo_payload()),
        ]
        with patch(
            "arl.windows_agent.bilibili_probe.httpx.get",
            side_effect=responses,
        ) as mock_get:
            probe.detect()

        self.assertEqual(mock_get.call_count, 2)
        for call in mock_get.call_args_list:
            headers = call.kwargs["headers"]
            self.assertEqual(
                headers["Cookie"],
                f"SESSDATA={self._SESSDATA}",
            )
            # PR5 headers must still be present alongside the new Cookie.
            self.assertEqual(headers["Referer"], "https://live.bilibili.com")
            self.assertIn("Mozilla/5.0", headers["User-Agent"])

    def test_stream_headers_include_cookie_when_sessdata_set(self) -> None:
        probe = BilibiliRoomProbe(self._settings(self._SESSDATA))
        headers = probe.stream_headers()
        self.assertEqual(headers["Cookie"], f"SESSDATA={self._SESSDATA}")
        self.assertEqual(headers["Referer"], "https://live.bilibili.com")
        self.assertIn("Mozilla/5.0", headers["User-Agent"])

    def test_empty_sessdata_keeps_pr5_behavior_byte_identical(self) -> None:
        probe = BilibiliRoomProbe(self._settings(""))

        headers = probe.stream_headers()
        self.assertNotIn("Cookie", headers)
        self.assertEqual(set(headers.keys()), {"Referer", "User-Agent"})

        responses = [
            _http_response(200, _ok_status_payload(1)),
            _http_response(200, _ok_playinfo_payload()),
        ]
        with patch(
            "arl.windows_agent.bilibili_probe.httpx.get",
            side_effect=responses,
        ) as mock_get:
            probe.detect()

        for call in mock_get.call_args_list:
            self.assertNotIn("Cookie", call.kwargs["headers"])


if __name__ == "__main__":
    unittest.main()
