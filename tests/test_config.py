from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from arl.config import DouyinSettings, Settings, load_settings


class _ARLEnvIsolation:
    """Snapshot ``ARL_*`` env vars so each test gets a clean slate.

    ``load_settings()`` reads ``os.environ`` directly; without isolation the
    developer's local .env-derived vars would leak into the test process.
    """

    def __enter__(self) -> "_ARLEnvIsolation":
        self._snapshot = {k: v for k, v in os.environ.items() if k.startswith("ARL_")}
        for key in list(os.environ):
            if key.startswith("ARL_"):
                del os.environ[key]
        return self

    def __exit__(self, *exc: object) -> None:
        for key in list(os.environ):
            if key.startswith("ARL_"):
                del os.environ[key]
        os.environ.update(self._snapshot)


class LoadSettingsBackwardCompatTests(unittest.TestCase):
    """PR1 contract: deployments that never set ``ARL_PLATFORMS`` keep working
    by deriving a single-douyin platforms list from the legacy ``ARL_DOUYIN_*``
    env vars.
    """

    def test_no_arl_platforms_defaults_to_single_douyin_entry(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_DOUYIN_ROOM_URL"] = "https://live.douyin.com/123"
            os.environ["ARL_STREAMER_NAME"] = "streamer-a"
            settings = load_settings()

        self.assertEqual(len(settings.platforms), 1)
        platform = settings.platforms[0]
        self.assertIsInstance(platform, DouyinSettings)
        self.assertEqual(platform.type, "douyin")
        self.assertEqual(platform.room_url, "https://live.douyin.com/123")
        # Settings.douyin remains as the backward-compat anchor.
        self.assertEqual(settings.douyin.room_url, "https://live.douyin.com/123")

    def test_arl_platforms_explicit_douyin_resolves_to_douyin_settings(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_PLATFORMS"] = "douyin"
            os.environ["ARL_DOUYIN_ROOM_URL"] = "https://live.douyin.com/123"
            settings = load_settings()

        self.assertEqual(len(settings.platforms), 1)
        self.assertIsInstance(settings.platforms[0], DouyinSettings)

    def test_arl_platforms_dedups_repeated_entries(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_PLATFORMS"] = "douyin, douyin , DOUYIN"
            os.environ["ARL_DOUYIN_ROOM_URL"] = "https://live.douyin.com/123"
            settings = load_settings()

        self.assertEqual(len(settings.platforms), 1)

    def test_arl_platforms_unknown_value_raises_value_error_with_diagnostic(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_PLATFORMS"] = "not_a_real_platform"
            with self.assertRaises(ValueError) as ctx:
                load_settings()
        self.assertIn("not_a_real_platform", str(ctx.exception))


class SettingsValidatorTests(unittest.TestCase):
    def test_settings_with_empty_platforms_defaults_to_douyin(self) -> None:
        """``WindowsAgentService(settings)`` calls ``build_probes(settings.platforms)``
        — an empty list would silently produce a service with no probes. The
        ``_default_platforms_from_douyin`` validator prevents that.
        """
        settings = Settings(douyin=DouyinSettings(room_url="https://live.douyin.com/123"))
        self.assertEqual(len(settings.platforms), 1)
        self.assertIs(settings.platforms[0], settings.douyin)


if __name__ == "__main__":
    unittest.main()
