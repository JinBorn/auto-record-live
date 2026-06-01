from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from arl.config import BilibiliSettings, DouyinSettings, Settings, load_settings


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


class LoadSettingsBilibiliTests(unittest.TestCase):
    def test_arl_platforms_with_bilibili_loads_bilibili_settings_from_env(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_PLATFORMS"] = "bilibili"
            os.environ["ARL_BILIBILI_ROOM_URL"] = "https://live.bilibili.com/12345"
            os.environ["ARL_BILIBILI_STREAMER_NAME"] = "bili-streamer"
            settings = load_settings()

        self.assertEqual(len(settings.platforms), 1)
        platform = settings.platforms[0]
        self.assertIsInstance(platform, BilibiliSettings)
        self.assertEqual(platform.type, "bilibili")
        self.assertEqual(platform.room_url, "https://live.bilibili.com/12345")
        self.assertEqual(platform.streamer_name, "bili-streamer")

    def test_arl_platforms_dual_loads_both_in_listed_order(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_PLATFORMS"] = "douyin,bilibili"
            os.environ["ARL_DOUYIN_ROOM_URL"] = "https://live.douyin.com/123"
            os.environ["ARL_BILIBILI_ROOM_URL"] = "https://live.bilibili.com/12345"
            settings = load_settings()

        self.assertEqual(len(settings.platforms), 2)
        self.assertIsInstance(settings.platforms[0], DouyinSettings)
        self.assertIsInstance(settings.platforms[1], BilibiliSettings)
        # Reverse order also works.
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_PLATFORMS"] = "bilibili,douyin"
            os.environ["ARL_DOUYIN_ROOM_URL"] = "https://live.douyin.com/123"
            os.environ["ARL_BILIBILI_ROOM_URL"] = "https://live.bilibili.com/12345"
            settings = load_settings()
        self.assertIsInstance(settings.platforms[0], BilibiliSettings)
        self.assertIsInstance(settings.platforms[1], DouyinSettings)

    def test_quality_gate_envs_load_into_platform_settings(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_PLATFORMS"] = "douyin,bilibili"
            os.environ["ARL_DOUYIN_ROOM_URL"] = "https://live.douyin.com/123"
            os.environ["ARL_BILIBILI_ROOM_URL"] = "https://live.bilibili.com/12345"
            os.environ["ARL_DOUYIN_MIN_QUALITY_TIER"] = "origin"
            os.environ["ARL_BILIBILI_MIN_STREAM_QN"] = "10000"
            os.environ["ARL_BILIBILI_MIN_STREAM_BITRATE_KBPS"] = "6000"
            settings = load_settings()

        douyin = settings.platforms[0]
        bilibili = settings.platforms[1]
        self.assertIsInstance(douyin, DouyinSettings)
        self.assertIsInstance(bilibili, BilibiliSettings)
        self.assertEqual(douyin.min_quality_tier, "origin")
        self.assertEqual(bilibili.min_stream_qn, 10000)
        self.assertEqual(bilibili.min_stream_bitrate_kbps, 6000)

    def test_recording_actual_resolution_gate_envs_load(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_RECORDING_VALIDATE_ACTUAL_RESOLUTION"] = "0"
            os.environ["ARL_RECORDING_MIN_ACTUAL_RESOLUTION_HEIGHT"] = "720"
            os.environ["ARL_RECORDING_ACTUAL_RESOLUTION_PROBE_TIMEOUT_SECONDS"] = "3"
            settings = load_settings()

        self.assertFalse(settings.recording.validate_actual_resolution)
        self.assertEqual(settings.recording.min_actual_resolution_height, 720)
        self.assertEqual(
            settings.recording.actual_resolution_probe_timeout_seconds,
            3,
        )

    def test_exporter_backoff_envs_load(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_EXPORTER_BACKOFF_INITIAL_SECONDS"] = "1.5"
            os.environ["ARL_EXPORTER_BACKOFF_MAX_SECONDS"] = "6"
            os.environ["ARL_EXPORTER_BATCH_FALLBACK_BUDGET"] = "2"
            settings = load_settings()

        self.assertEqual(settings.export.backoff_initial_seconds, 1.5)
        self.assertEqual(settings.export.backoff_max_seconds, 6.0)
        self.assertEqual(settings.export.batch_fallback_budget, 2)

    def test_subtitle_model_cache_env_loads(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_WHISPER_MODEL_CACHE_DIR"] = "data/tmp/custom-whisper-cache"
            os.environ["ARL_WHISPER_MIN_LANGUAGE_PROBABILITY"] = "0.7"
            settings = load_settings()

        self.assertEqual(
            settings.subtitles.model_cache_dir,
            Path("data/tmp/custom-whisper-cache"),
        )
        self.assertEqual(settings.subtitles.min_language_probability, 0.7)


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
