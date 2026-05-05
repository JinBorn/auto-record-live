from __future__ import annotations

import unittest

from arl.config import DouyinSettings, PlatformSettings
from arl.windows_agent.probe import DouyinRoomProbe
from arl.windows_agent.registry import (
    PROBE_REGISTRY,
    UnknownPlatformError,
    build_probe,
    build_probes,
)


class RegistryTests(unittest.TestCase):
    def test_douyin_is_registered(self) -> None:
        self.assertIn("douyin", PROBE_REGISTRY)
        self.assertIs(PROBE_REGISTRY["douyin"], DouyinRoomProbe)

    def test_build_probes_empty_list_returns_empty_list(self) -> None:
        self.assertEqual(build_probes([]), [])

    def test_build_probes_returns_douyin_probe_with_platform_name(self) -> None:
        settings = DouyinSettings(
            room_url="https://live.douyin.com/123",
            streamer_name="streamer-a",
        )

        probes = build_probes([settings])

        self.assertEqual(len(probes), 1)
        self.assertIsInstance(probes[0], DouyinRoomProbe)
        self.assertEqual(probes[0].platform_name, "douyin")

    def test_build_probe_raises_unknown_platform_with_diagnostic_message(self) -> None:
        settings = PlatformSettings(type="not_a_real_platform")

        with self.assertRaises(UnknownPlatformError) as ctx:
            build_probe(settings)

        message = str(ctx.exception)
        self.assertIn("not_a_real_platform", message)
        self.assertIn("douyin", message)

    def test_unknown_platform_error_is_value_error(self) -> None:
        # Callers may catch ValueError generally; preserve that contract.
        self.assertTrue(issubclass(UnknownPlatformError, ValueError))


if __name__ == "__main__":
    unittest.main()
