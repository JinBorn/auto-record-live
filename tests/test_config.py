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

    def test_arl_platforms_expands_multiple_rooms_per_platform(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_PLATFORMS"] = "douyin,bilibili"
            os.environ["ARL_DOUYIN_ROOM_URLS"] = (
                "https://live.douyin.com/111, https://live.douyin.com/222"
            )
            os.environ["ARL_DOUYIN_STREAMER_NAMES"] = "douyin-a,douyin-b"
            os.environ["ARL_BILIBILI_ROOM_URLS"] = (
                "https://live.bilibili.com/333, https://live.bilibili.com/444"
            )
            os.environ["ARL_BILIBILI_STREAMER_NAMES"] = "bili-a,bili-b"
            settings = load_settings()

        self.assertEqual(len(settings.platforms), 4)
        self.assertEqual(
            [(platform.type, platform.room_url, platform.streamer_name) for platform in settings.platforms],
            [
                ("douyin", "https://live.douyin.com/111", "douyin-a"),
                ("douyin", "https://live.douyin.com/222", "douyin-b"),
                ("bilibili", "https://live.bilibili.com/333", "bili-a"),
                ("bilibili", "https://live.bilibili.com/444", "bili-b"),
            ],
        )

    def test_douyin_room_urls_expand_when_platforms_omitted(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_DOUYIN_ROOM_URLS"] = (
                "https://live.douyin.com/111,https://live.douyin.com/222"
            )
            settings = load_settings()

        self.assertEqual(
            [platform.room_url for platform in settings.platforms],
            ["https://live.douyin.com/111", "https://live.douyin.com/222"],
        )

    def test_comma_separated_legacy_room_url_envs_expand_per_room(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_PLATFORMS"] = "douyin,bilibili"
            os.environ["ARL_DOUYIN_ROOM_URL"] = (
                "https://live.douyin.com/111, https://live.douyin.com/222"
            )
            os.environ["ARL_STREAMER_NAME"] = "douyin-a,douyin-b"
            os.environ["ARL_BILIBILI_ROOM_URL"] = (
                "https://live.bilibili.com/333, https://live.bilibili.com/444"
            )
            os.environ["ARL_BILIBILI_STREAMER_NAME"] = "bili-a,bili-b"
            settings = load_settings()

        self.assertEqual(
            [(platform.type, platform.room_url, platform.streamer_name) for platform in settings.platforms],
            [
                ("douyin", "https://live.douyin.com/111", "douyin-a"),
                ("douyin", "https://live.douyin.com/222", "douyin-b"),
                ("bilibili", "https://live.bilibili.com/333", "bili-a"),
                ("bilibili", "https://live.bilibili.com/444", "bili-b"),
            ],
        )

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

    def test_douyin_playwright_headless_env_loads(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_PLATFORMS"] = "douyin"
            os.environ["ARL_DOUYIN_ROOM_URL"] = "https://live.douyin.com/123"
            settings = load_settings()

        douyin = settings.platforms[0]
        self.assertIsInstance(douyin, DouyinSettings)
        self.assertTrue(douyin.playwright_headless)

        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_PLATFORMS"] = "douyin"
            os.environ["ARL_DOUYIN_ROOM_URL"] = "https://live.douyin.com/123"
            os.environ["ARL_DOUYIN_PLAYWRIGHT_HEADLESS"] = "0"
            settings = load_settings()

        douyin = settings.platforms[0]
        self.assertIsInstance(douyin, DouyinSettings)
        self.assertFalse(douyin.playwright_headless)

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

    def test_recorder_max_concurrent_jobs_env_loads_with_minimum_one(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_RECORDER_MAX_CONCURRENT_JOBS"] = "4"
            settings = load_settings()

        self.assertEqual(settings.recording.max_concurrent_jobs, 4)

        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_RECORDER_MAX_CONCURRENT_JOBS"] = "0"
            settings = load_settings()

        self.assertEqual(settings.recording.max_concurrent_jobs, 1)

    def test_recording_finalize_headroom_env_loads(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_RECORDING_FINALIZE_HEADROOM_SECONDS"] = "90"
            settings = load_settings()

        self.assertEqual(settings.recording.direct_stream_finalize_headroom_seconds, 90)

    def test_vision_min_match_duration_env_loads(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_VISION_MIN_MATCH_DURATION_SECONDS"] = "420"
            settings = load_settings()

        self.assertEqual(settings.vision.min_match_duration_seconds, 420.0)

    def test_vision_min_complete_timer_env_loads(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_VISION_MIN_COMPLETE_TIMER_SECONDS"] = "780"
            settings = load_settings()

        self.assertEqual(settings.vision.min_complete_timer_seconds, 780.0)

    def test_exporter_backoff_envs_load(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_EXPORTER_BACKOFF_INITIAL_SECONDS"] = "1.5"
            os.environ["ARL_EXPORTER_BACKOFF_MAX_SECONDS"] = "6"
            os.environ["ARL_EXPORTER_BATCH_FALLBACK_BUDGET"] = "2"
            settings = load_settings()

        self.assertEqual(settings.export.backoff_initial_seconds, 1.5)
        self.assertEqual(settings.export.backoff_max_seconds, 6.0)
        self.assertEqual(settings.export.batch_fallback_budget, 2)

    def test_export_ffmpeg_video_codec_env_loads(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_EXPORT_FFMPEG_VIDEO_CODEC"] = "hevc"
            settings = load_settings()

        self.assertEqual(settings.export.ffmpeg_video_codec, "h265")

    def test_export_quality_preserving_defaults(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            settings = load_settings()

        self.assertFalse(settings.export.burn_subtitles)
        self.assertEqual(settings.export.ffmpeg_crf, 18)
        self.assertEqual(settings.export.ffmpeg_preset, "slow")
        self.assertFalse(settings.export.use_highlight_plans)
        self.assertFalse(settings.export.use_hardware_encoding)

    def test_export_burn_subtitles_env_loads(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_EXPORT_BURN_SUBTITLES"] = "1"
            settings = load_settings()

        self.assertTrue(settings.export.burn_subtitles)

    def test_export_ass_subtitle_envs_load(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_EXPORT_USE_ASS_SUBTITLES"] = "1"
            os.environ["ARL_EXPORT_ASS_FONT_NAME"] = "Microsoft YaHei"
            os.environ["ARL_EXPORT_ASS_FONT_SIZE"] = "42"
            os.environ["ARL_EXPORT_ASS_MARGIN_V"] = "18"
            os.environ["ARL_EXPORT_ASS_OUTLINE"] = "3"
            settings = load_settings()

        self.assertTrue(settings.export.use_ass_subtitles)
        self.assertEqual(settings.export.ass_font_name, "Microsoft YaHei")
        self.assertEqual(settings.export.ass_font_size, 42)
        self.assertEqual(settings.export.ass_margin_v, 18)
        self.assertEqual(settings.export.ass_outline, 3)

    def test_export_ass_subtitle_numeric_envs_clamp(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_EXPORT_ASS_FONT_SIZE"] = "0"
            os.environ["ARL_EXPORT_ASS_MARGIN_V"] = "-1"
            os.environ["ARL_EXPORT_ASS_OUTLINE"] = "-2"
            settings = load_settings()

        self.assertEqual(settings.export.ass_font_size, 1)
        self.assertEqual(settings.export.ass_margin_v, 0)
        self.assertEqual(settings.export.ass_outline, 0)

    def test_export_optional_processing_envs_load(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_EXPORT_USE_HIGHLIGHT_PLANS"] = "1"
            os.environ["ARL_EXPORT_USE_HARDWARE_ENCODING"] = "1"
            settings = load_settings()

        self.assertTrue(settings.export.use_highlight_plans)
        self.assertTrue(settings.export.use_hardware_encoding)

    def test_highlight_planner_envs_load(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_HIGHLIGHT_PLANNER_ENABLED"] = "0"
            os.environ["ARL_HIGHLIGHT_CUE_PADDING_SECONDS"] = "10.5"
            os.environ["ARL_HIGHLIGHT_KEYWORD_PADDING_SECONDS"] = "24"
            os.environ["ARL_HIGHLIGHT_MERGE_GAP_SECONDS"] = "60"
            os.environ["ARL_HIGHLIGHT_KEEP_EDGE_SECONDS"] = "20"
            os.environ["ARL_HIGHLIGHT_MIN_BOUNDARY_DURATION_SECONDS"] = "300"
            os.environ["ARL_HIGHLIGHT_MIN_REDUCTION_SECONDS"] = "45"
            os.environ["ARL_HIGHLIGHT_MIN_RETAINED_SECONDS"] = "180"
            os.environ["ARL_HIGHLIGHT_MIN_RETAINED_FRACTION"] = "0.4"
            os.environ["ARL_HIGHLIGHT_MAX_WINDOWS"] = "5"
            os.environ["ARL_HIGHLIGHT_CONDENSED_VISUAL_SAMPLE_INTERVAL_SECONDS"] = "30"
            os.environ["ARL_HIGHLIGHT_CONDENSED_ACTION_RESOLUTION_TAIL_SECONDS"] = "31"
            os.environ["ARL_HIGHLIGHT_CONDENSED_ACTION_RESOLUTION_GAP_SECONDS"] = "9"
            os.environ["ARL_HIGHLIGHT_CONDENSED_KDA_EVENT_DETECTION_ENABLED"] = "0"
            os.environ["ARL_HIGHLIGHT_CONDENSED_KDA_CROP_REGION"] = "10,20,30,40"
            os.environ["ARL_HIGHLIGHT_CONDENSED_KDA_SAMPLE_INTERVAL_SECONDS"] = "3"
            os.environ["ARL_HIGHLIGHT_CONDENSED_KDA_MIN_CONFIDENCE"] = "0.7"
            os.environ["ARL_HIGHLIGHT_CONDENSED_KDA_MAX_READING_GAP_SECONDS"] = "12"
            os.environ["ARL_HIGHLIGHT_CONDENSED_KDA_MAX_EVENT_DELTA"] = "4"
            os.environ["ARL_HIGHLIGHT_CONDENSED_KDA_KILL_PREROLL_SECONDS"] = "20"
            os.environ["ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_PREROLL_SECONDS"] = "50"
            os.environ["ARL_HIGHLIGHT_CONDENSED_KDA_POSTROLL_SECONDS"] = "6"
            os.environ[
                "ARL_HIGHLIGHT_CONDENSED_KDA_POST_DEATH_KILL_SUPPRESSION_SECONDS"
            ] = "75"
            os.environ["ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_WAIT_TRIM_SECONDS"] = "95"
            os.environ["ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_SILENT_GAP_TRIM_SECONDS"] = "11"
            os.environ[
                "ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_SILENT_TRIM_LOOKBACK_SECONDS"
            ] = "25"
            os.environ["ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_REACTION_TAIL_SECONDS"] = "4"
            settings = load_settings()

        self.assertFalse(settings.highlights.enabled)
        self.assertEqual(settings.highlights.cue_padding_seconds, 10.5)
        self.assertEqual(settings.highlights.highlight_padding_seconds, 24.0)
        self.assertEqual(settings.highlights.merge_gap_seconds, 60.0)
        self.assertEqual(settings.highlights.keep_edge_seconds, 20.0)
        self.assertEqual(settings.highlights.min_boundary_duration_seconds, 300.0)
        self.assertEqual(settings.highlights.min_reduction_seconds, 45.0)
        self.assertEqual(settings.highlights.min_retained_seconds, 180.0)
        self.assertEqual(settings.highlights.min_retained_fraction, 0.4)
        self.assertEqual(settings.highlights.max_windows, 5)
        self.assertEqual(settings.highlights.condensed_visual_sample_interval_seconds, 30.0)
        self.assertEqual(settings.highlights.condensed_action_resolution_tail_seconds, 31.0)
        self.assertEqual(settings.highlights.condensed_action_resolution_gap_seconds, 9.0)
        self.assertFalse(settings.highlights.condensed_kda_event_detection_enabled)
        self.assertEqual(settings.highlights.condensed_kda_crop_region, (10, 20, 30, 40))
        self.assertEqual(settings.highlights.condensed_kda_sample_interval_seconds, 3.0)
        self.assertEqual(settings.highlights.condensed_kda_min_confidence, 0.7)
        self.assertEqual(settings.highlights.condensed_kda_max_reading_gap_seconds, 12.0)
        self.assertEqual(settings.highlights.condensed_kda_max_event_delta, 4)
        self.assertEqual(settings.highlights.condensed_kda_kill_preroll_seconds, 20.0)
        self.assertEqual(settings.highlights.condensed_kda_death_preroll_seconds, 50.0)
        self.assertEqual(settings.highlights.condensed_kda_postroll_seconds, 6.0)
        self.assertEqual(
            settings.highlights.condensed_kda_post_death_kill_suppression_seconds,
            75.0,
        )
        self.assertEqual(
            settings.highlights.condensed_kda_death_wait_trim_seconds,
            95.0,
        )
        self.assertEqual(
            settings.highlights.condensed_kda_death_silent_gap_trim_seconds,
            11.0,
        )
        self.assertEqual(
            settings.highlights.condensed_kda_death_silent_trim_lookback_seconds,
            25.0,
        )
        self.assertEqual(
            settings.highlights.condensed_kda_death_reaction_tail_seconds,
            4.0,
        )

    def test_maintenance_envs_load(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_MAINTENANCE_MAX_JSONL_BYTES"] = "2048"
            os.environ["ARL_MAINTENANCE_KEEP_RECENT_LINES"] = "123"
            os.environ["ARL_LAUNCHER_LOG_RETAIN_COUNT"] = "7"
            os.environ["ARL_MAINTENANCE_ARCHIVE_DIR"] = "data/tmp/custom-archive"
            settings = load_settings()

        self.assertEqual(settings.maintenance.max_jsonl_bytes, 2048)
        self.assertEqual(settings.maintenance.keep_recent_lines, 123)
        self.assertEqual(settings.maintenance.launcher_log_retain_count, 7)
        self.assertEqual(settings.maintenance.archive_dir, Path("data/tmp/custom-archive"))

    def test_subtitle_model_cache_env_loads(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_WHISPER_MODEL_CACHE_DIR"] = "data/tmp/custom-whisper-cache"
            os.environ["ARL_WHISPER_MIN_LANGUAGE_PROBABILITY"] = "0.7"
            os.environ["ARL_WHISPER_DEVICE"] = "CPU"
            os.environ["ARL_WHISPER_COMPUTE_TYPE"] = "AUTO"
            os.environ["ARL_WHISPER_CUDA_COMPUTE_TYPE"] = "INT8_FLOAT16"
            os.environ["ARL_WHISPER_CPU_COMPUTE_TYPE"] = "INT8"
            os.environ["ARL_ASR_PREPROCESS_AUDIO"] = "1"
            os.environ["ARL_ASR_PREPROCESS_AUDIO_FILTER"] = "highpass=f=120,loudnorm"
            os.environ["ARL_ASR_PREPROCESS_TIMEOUT_SECONDS"] = "45"
            settings = load_settings()

        self.assertEqual(
            settings.subtitles.model_cache_dir,
            Path("data/tmp/custom-whisper-cache"),
        )
        self.assertEqual(settings.subtitles.min_language_probability, 0.7)
        self.assertEqual(settings.subtitles.device, "cpu")
        self.assertEqual(settings.subtitles.compute_type, "auto")
        self.assertEqual(settings.subtitles.cuda_compute_type, "int8_float16")
        self.assertEqual(settings.subtitles.cpu_compute_type, "int8")
        self.assertTrue(settings.subtitles.preprocess_audio)
        self.assertEqual(settings.subtitles.preprocess_audio_filter, "highpass=f=120,loudnorm")
        self.assertEqual(settings.subtitles.preprocess_timeout_seconds, 45)

    def test_segmenter_template_fallback_env_loads(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            settings = load_settings()

        self.assertFalse(settings.segmenter.template_fallback_enabled)

        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_SEGMENTER_TEMPLATE_FALLBACK_ENABLED"] = "1"
            settings = load_settings()

        self.assertTrue(settings.segmenter.template_fallback_enabled)


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
