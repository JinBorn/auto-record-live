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


if __name__ == "__main__":
    unittest.main()
