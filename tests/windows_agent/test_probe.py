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
        )
        self.probe = DouyinRoomProbe(self.settings)
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_playwright_live_payload_with_stream_url_maps_to_direct_stream(self) -> None:
        payload = (
            '{"ok":true,"state":"live","sourceType":"direct_stream",'
            '"streamUrl":"https://cdn.example/live.m3u8","reason":"page_marker_detected"}\n'
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
        self.assertEqual(snapshot.stream_url, "https://cdn.example/live.m3u8")
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
            '"streamUrl":"https://cdn.example/live.m3u8","reason":"page_marker_detected"}\n'
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
        self.assertEqual(snapshot.stream_url, "https://cdn.example/live.m3u8")

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
            '"streamUrl":"https://cdn.example/live.m3u8","reason":"page_marker_detected"}\n'
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
        self.assertEqual(snapshot.stream_url, "https://cdn.example/live.m3u8")

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
                '"hls_pull_url":"https:\\/\\/pull.example.com\\/live\\/abc.m3u8?token=1"'
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
        self.assertEqual(snapshot.stream_url, "https://pull.example.com/live/abc.m3u8?token=1")

    def test_detect_http_live_marker_uses_direct_stream_when_available(self) -> None:
        settings = self.settings.model_copy(update={"use_playwright_probe": False})
        probe = DouyinRoomProbe(settings)
        http_response = SimpleNamespace(
            status_code=200,
            text=(
                '<html><body>直播中<script>'
                '"stream_url":"https%3A%2F%2Fpull.example.com%2Flive%2Froom.m3u8%3Ftoken%3D1"'
                "</script></body></html>"
            ),
        )
        with patch("arl.windows_agent.probe.httpx.get", return_value=http_response):
            snapshot = probe.detect()

        self.assertEqual(snapshot.state, LiveState.LIVE)
        self.assertEqual(snapshot.source_type, SourceType.DIRECT_STREAM)
        self.assertEqual(snapshot.reason, "page_marker_detected")
        self.assertEqual(snapshot.stream_url, "https://pull.example.com/live/room.m3u8?token=1")

    def test_detect_http_percent_encoded_direct_url_without_markers_is_still_live(self) -> None:
        settings = self.settings.model_copy(update={"use_playwright_probe": False})
        probe = DouyinRoomProbe(settings)
        http_response = SimpleNamespace(
            status_code=200,
            text=(
                '<html><body><script>'
                '"https%3A%2F%2Fpull.example.com%2Flive%2Fencoded-room.m3u8%3Ftoken%3D1"'
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
            "https://pull.example.com/live/encoded-room.m3u8?token=1",
        )

    def test_detect_http_multilayer_percent_encoded_and_x_escaped_stream_url(self) -> None:
        settings = self.settings.model_copy(update={"use_playwright_probe": False})
        probe = DouyinRoomProbe(settings)
        http_response = SimpleNamespace(
            status_code=200,
            text=(
                '<html><body><script>'
                '"stream_url":"\\x68\\x74\\x74\\x70\\x73%253A%252F%252Fpull.example.com%252Flive%252Fdeep-room.m3u8%253Ftoken%253D1"'
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
            "https://pull.example.com/live/deep-room.m3u8?token=1",
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


if __name__ == "__main__":
    unittest.main()
