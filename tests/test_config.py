from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from arl.config import (
    BilibiliSettings,
    DouyinSettings,
    Settings,
    apply_publish_preset,
    load_settings,
)


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

    def test_quality_report_threshold_envs_load(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_QUALITY_REPORT_SUBTITLE_ACTIVE_RATIO_MIN"] = "0.7"
            os.environ["ARL_QUALITY_REPORT_LONG_NO_SUBTITLE_GAP_MIN_SECONDS"] = "12"
            os.environ["ARL_QUALITY_REPORT_MAX_SOURCE_GAP_SECONDS"] = "30"
            os.environ["ARL_QUALITY_REPORT_TEASER_MIN_SEGMENTS"] = "0"
            os.environ["ARL_QUALITY_REPORT_TEASER_MAX_SEGMENTS"] = "2"
            os.environ["ARL_QUALITY_REPORT_SFX_MAX_HITS"] = "4"
            os.environ["ARL_QUALITY_REPORT_ZOOM_MIN_SEGMENTS"] = "0"
            os.environ["ARL_QUALITY_REPORT_ZOOM_MAX_SEGMENTS"] = "3"
            os.environ["ARL_QUALITY_REPORT_TOP_NO_SUBTITLE_GAPS"] = "8"
            settings = load_settings()

        self.assertEqual(settings.quality_report.subtitle_active_ratio_min, 0.7)
        self.assertEqual(
            settings.quality_report.long_no_subtitle_gap_min_seconds,
            12.0,
        )
        self.assertEqual(settings.quality_report.max_source_gap_seconds, 30.0)
        self.assertEqual(settings.quality_report.teaser_min_segments, 0)
        self.assertEqual(settings.quality_report.teaser_max_segments, 2)
        self.assertEqual(settings.quality_report.sfx_max_hits, 4)
        self.assertEqual(settings.quality_report.zoom_min_segments, 0)
        self.assertEqual(settings.quality_report.zoom_max_segments, 3)
        self.assertEqual(settings.quality_report.top_no_subtitle_gaps, 8)

    def test_segmented_recording_envs_load(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_RECORDING_SEGMENTED_ENABLED"] = "1"
            os.environ["ARL_RECORDING_SEGMENTED_CHUNK_SECONDS"] = "600"
            settings = load_settings()

        self.assertTrue(settings.recording.segmented_recording_enabled)
        self.assertEqual(settings.recording.segmented_chunk_seconds, 600)

        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_RECORDING_SEGMENTED_CHUNK_SECONDS"] = "0"
            settings = load_settings()

        self.assertFalse(settings.recording.segmented_recording_enabled)
        self.assertEqual(settings.recording.segmented_chunk_seconds, 1)

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
        self.assertEqual(settings.export.ass_font_size, 32)
        self.assertEqual(settings.export.ass_margin_v, 110)
        self.assertEqual(settings.export.ass_max_chars_per_line, 18)
        self.assertEqual(settings.export.ass_max_lines, 2)
        self.assertFalse(settings.export.use_edit_plans)
        self.assertFalse(settings.export.use_highlight_plans)
        self.assertFalse(settings.export.use_hardware_encoding)
        self.assertFalse(settings.export.audio_loudnorm_enabled)
        self.assertEqual(
            settings.export.audio_loudnorm_filter,
            "loudnorm=I=-16:TP=-1.5:LRA=11",
        )
        self.assertEqual(settings.highlights.mode, "highlight")
        self.assertIsNone(settings.highlights.condensed_start_edge_seconds)
        self.assertFalse(settings.editing.enabled)
        self.assertFalse(settings.editing.zoom_enabled)
        self.assertFalse(settings.editing.audio_mixing_enabled)
        self.assertEqual(settings.editing.bgm_gain_db, -28.0)
        self.assertEqual(settings.editing.bgm_multi_phase_min_seconds, 600.0)
        self.assertEqual(settings.editing.bgm_switch_min_gap_seconds, 60.0)
        self.assertEqual(settings.editing.bgm_crossfade_seconds, 2.0)
        self.assertEqual(settings.editing.bgm_source_music_padding_seconds, 2.0)
        self.assertEqual(settings.editing.bgm_source_music_majority_threshold, 0.60)
        self.assertFalse(settings.llm.enabled)
        self.assertEqual(settings.llm.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(settings.llm.model, "deepseek-chat")

    def test_llm_envs_load_and_clamp(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_LLM_ENABLED"] = "1"
            os.environ["ARL_LLM_BASE_URL"] = " https://example.test/v1/ "
            os.environ["ARL_LLM_API_KEY"] = " secret "
            os.environ["ARL_LLM_MODEL"] = " custom-model "
            os.environ["ARL_LLM_TIMEOUT_SECONDS"] = "0"
            os.environ["ARL_LLM_MAX_RETRIES"] = "-1"
            os.environ["ARL_LLM_MAX_INPUT_CUES"] = "4"
            os.environ["ARL_LLM_TEMPERATURE"] = "3"
            settings = load_settings()

        self.assertTrue(settings.llm.enabled)
        self.assertEqual(settings.llm.base_url, "https://example.test/v1")
        self.assertEqual(settings.llm.api_key, "secret")
        self.assertEqual(settings.llm.model, "custom-model")
        self.assertEqual(settings.llm.timeout_seconds, 1.0)
        self.assertEqual(settings.llm.max_retries, 0)
        self.assertEqual(settings.llm.max_input_cues, 20)
        self.assertEqual(settings.llm.temperature, 1.5)

    def test_postprocess_publish_preset_env_enables_publish_pipeline(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_POSTPROCESS_PRESET"] = "publish"
            settings = load_settings()

        self.assertTrue(settings.highlights.enabled)
        self.assertEqual(settings.highlights.mode, "condensed")
        self.assertEqual(settings.highlights.keep_edge_seconds, 10.0)
        self.assertEqual(settings.highlights.condensed_start_edge_seconds, 1.0)
        self.assertTrue(settings.editing.enabled)
        self.assertTrue(settings.editing.zoom_enabled)
        self.assertEqual(settings.editing.zoom_target, "chat")
        self.assertEqual(settings.editing.zoom_mode, "closeup")
        self.assertEqual(settings.editing.zoom_max_segments, 3)
        self.assertTrue(settings.editing.audio_mixing_enabled)
        self.assertEqual(settings.editing.bgm_library_path, Path("data/bgm/library.json"))
        self.assertTrue(settings.export.enable_ffmpeg)
        self.assertTrue(settings.export.burn_subtitles)
        self.assertTrue(settings.export.use_ass_subtitles)
        self.assertTrue(settings.export.use_edit_plans)
        self.assertTrue(settings.export.use_highlight_plans)
        self.assertEqual(settings.export.ffmpeg_video_codec, "h264")
        self.assertEqual(settings.export.ffmpeg_bitrate, "8000k")
        self.assertEqual(settings.export.ffmpeg_max_bitrate, "10000k")
        self.assertTrue(settings.export.audio_loudnorm_enabled)
        self.assertEqual(settings.subtitles.model_size, "medium")
        self.assertFalse(settings.llm.enabled)

    def test_postprocess_publish_preset_bool_env_enables_publish_pipeline(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_POSTPROCESS_PUBLISH_PRESET"] = "1"
            settings = load_settings()

        self.assertEqual(settings.highlights.mode, "condensed")
        self.assertTrue(settings.editing.enabled)
        self.assertTrue(settings.export.use_edit_plans)

    def test_publish_preset_preserves_explicit_transition_mode_env(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_POSTPROCESS_PRESET"] = "publish"
            os.environ["ARL_EDIT_TRANSITION_MODE"] = "none"
            settings = load_settings()

        self.assertEqual(settings.editing.transition_mode, "none")

    def test_apply_publish_preset_does_not_mutate_source_settings(self) -> None:
        settings = Settings()

        published = apply_publish_preset(settings)

        self.assertEqual(settings.highlights.mode, "highlight")
        self.assertFalse(settings.editing.enabled)
        self.assertFalse(settings.export.use_edit_plans)
        self.assertEqual(published.highlights.mode, "condensed")
        self.assertEqual(published.highlights.keep_edge_seconds, 10.0)
        self.assertEqual(published.highlights.condensed_start_edge_seconds, 1.0)
        self.assertTrue(published.editing.enabled)
        self.assertEqual(settings.editing.zoom_max_segments, 1)
        self.assertEqual(published.editing.zoom_max_segments, 3)
        self.assertEqual(
            published.editing.bgm_library_path,
            Path("data/bgm/library.json"),
        )
        self.assertEqual(published.editing.transition_mode, "black_card")
        self.assertTrue(published.export.use_edit_plans)
        self.assertEqual(published.export.ffmpeg_video_codec, "h264")
        self.assertEqual(published.export.ffmpeg_bitrate, "8000k")
        self.assertEqual(published.export.ffmpeg_max_bitrate, "10000k")
        self.assertTrue(published.export.audio_loudnorm_enabled)
        self.assertEqual(settings.subtitles.model_size, "small")
        self.assertEqual(published.subtitles.model_size, "medium")

    def test_publish_preset_preserves_explicit_zoom_max_segments_env(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_POSTPROCESS_PRESET"] = "publish"
            os.environ["ARL_EDIT_ZOOM_MAX_SEGMENTS"] = "2"
            settings = load_settings()

        self.assertTrue(settings.editing.zoom_enabled)
        self.assertEqual(settings.editing.zoom_max_segments, 2)

    def test_apply_publish_preset_keeps_cpu_subtitle_model_small(self) -> None:
        settings = Settings(subtitles={"device": "cpu"})

        published = apply_publish_preset(settings)

        self.assertEqual(published.subtitles.model_size, "small")

    def test_publish_preset_preserves_explicit_whisper_model_env(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_POSTPROCESS_PRESET"] = "publish"
            os.environ["ARL_WHISPER_MODEL_SIZE"] = "small"
            settings = load_settings()

        self.assertEqual(settings.subtitles.model_size, "small")

    def test_apply_publish_preset_raises_low_existing_bitrate_settings(self) -> None:
        settings = Settings(
            export={
                "ffmpeg_bitrate": "4000k",
                "ffmpeg_max_bitrate": "5000k",
            },
        )

        published = apply_publish_preset(settings)

        self.assertEqual(published.export.ffmpeg_bitrate, "8000k")
        self.assertEqual(published.export.ffmpeg_max_bitrate, "10000k")

    def test_apply_publish_preset_preserves_higher_existing_bitrate_settings(self) -> None:
        settings = Settings(
            export={
                "ffmpeg_bitrate": "12M",
                "ffmpeg_max_bitrate": "15000k",
            },
        )

        published = apply_publish_preset(settings)

        self.assertEqual(published.export.ffmpeg_bitrate, "12M")
        self.assertEqual(published.export.ffmpeg_max_bitrate, "15000k")

    def test_apply_publish_preset_preserves_explicit_bgm_inputs(self) -> None:
        settings = Settings(
            editing={
                "bgm_library_path": Path("C:/audio/custom-library.json"),
                "bgm_path": Path("C:/audio/manual.mp3"),
            }
        )

        published = apply_publish_preset(settings)

        self.assertEqual(
            published.editing.bgm_library_path,
            Path("C:/audio/custom-library.json"),
        )
        self.assertEqual(published.editing.bgm_path, Path("C:/audio/manual.mp3"))

    def test_export_audio_loudnorm_envs_load(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_EXPORT_AUDIO_LOUDNORM_ENABLED"] = "1"
            os.environ["ARL_EXPORT_AUDIO_LOUDNORM_FILTER"] = (
                "loudnorm=I=-14:TP=-1:LRA=9"
            )
            settings = load_settings()

        self.assertTrue(settings.export.audio_loudnorm_enabled)
        self.assertEqual(
            settings.export.audio_loudnorm_filter,
            "loudnorm=I=-14:TP=-1:LRA=9",
        )

    def test_export_audio_loudnorm_empty_filter_uses_default(self) -> None:
        settings = Settings(
            export={"audio_loudnorm_filter": "   "},
        )

        self.assertEqual(
            settings.export.audio_loudnorm_filter,
            "loudnorm=I=-16:TP=-1.5:LRA=11",
        )

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
            os.environ["ARL_EXPORT_ASS_MAX_CHARS_PER_LINE"] = "12"
            os.environ["ARL_EXPORT_ASS_MAX_LINES"] = "1"
            settings = load_settings()

        self.assertTrue(settings.export.use_ass_subtitles)
        self.assertEqual(settings.export.ass_font_name, "Microsoft YaHei")
        self.assertEqual(settings.export.ass_font_size, 42)
        self.assertEqual(settings.export.ass_margin_v, 18)
        self.assertEqual(settings.export.ass_outline, 3)
        self.assertEqual(settings.export.ass_max_chars_per_line, 12)
        self.assertEqual(settings.export.ass_max_lines, 1)

    def test_export_ass_subtitle_numeric_envs_clamp(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_EXPORT_ASS_FONT_SIZE"] = "0"
            os.environ["ARL_EXPORT_ASS_MARGIN_V"] = "-1"
            os.environ["ARL_EXPORT_ASS_OUTLINE"] = "-2"
            os.environ["ARL_EXPORT_ASS_MAX_CHARS_PER_LINE"] = "0"
            os.environ["ARL_EXPORT_ASS_MAX_LINES"] = "0"
            settings = load_settings()

        self.assertEqual(settings.export.ass_font_size, 1)
        self.assertEqual(settings.export.ass_margin_v, 0)
        self.assertEqual(settings.export.ass_outline, 0)
        self.assertEqual(settings.export.ass_max_chars_per_line, 1)
        self.assertEqual(settings.export.ass_max_lines, 1)

    def test_export_optional_processing_envs_load(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_EXPORT_USE_EDIT_PLANS"] = "1"
            os.environ["ARL_EXPORT_USE_HIGHLIGHT_PLANS"] = "1"
            os.environ["ARL_EXPORT_USE_HARDWARE_ENCODING"] = "1"
            settings = load_settings()

        self.assertTrue(settings.export.use_edit_plans)
        self.assertTrue(settings.export.use_highlight_plans)
        self.assertTrue(settings.export.use_hardware_encoding)

    def test_edit_planner_envs_load(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_EDIT_PLANNER_ENABLED"] = "1"
            os.environ["ARL_EDIT_TEASER_MAX_SEGMENTS"] = "0"
            os.environ["ARL_EDIT_TEASER_MAX_TOTAL_SECONDS"] = "0"
            os.environ["ARL_EDIT_TEASER_MIN_SEGMENT_SECONDS"] = "0"
            os.environ["ARL_EDIT_TEASER_DYNAMIC_BUDGET_ENABLED"] = "0"
            os.environ["ARL_EDIT_TEASER_BUDGET_FRACTION_MIN"] = "0.15"
            os.environ["ARL_EDIT_TEASER_BUDGET_FRACTION_MAX"] = "0.10"
            os.environ["ARL_EDIT_TEASER_BUDGET_MIN_SECONDS"] = "0"
            os.environ["ARL_EDIT_TEASER_BUDGET_MAX_SECONDS"] = "5"
            os.environ["ARL_EDIT_TEASER_CANDIDATE_REASONS"] = (
                "highlight_keyword,condensed_key_event,highlight_keyword"
            )
            os.environ["ARL_EDIT_TEASER_FALLBACK_ENABLED"] = "0"
            os.environ["ARL_EDIT_TRANSITION_MODE"] = "black-card"
            os.environ["ARL_EDIT_TRANSITION_DURATION_SECONDS"] = "20"
            os.environ["ARL_EDIT_TRANSITION_TEXT"] = "  Back now  "
            os.environ["ARL_EDIT_TRANSITION_SFX_PATH"] = "C:/audio/whoosh.wav"
            os.environ["ARL_EDIT_TRANSITION_SFX_GAIN_DB"] = "12"
            settings = load_settings()

        self.assertTrue(settings.editing.enabled)
        self.assertEqual(settings.editing.teaser_max_segments, 1)
        self.assertEqual(settings.editing.teaser_max_total_seconds, 1.0)
        self.assertEqual(settings.editing.teaser_min_segment_seconds, 0.1)
        self.assertFalse(settings.editing.teaser_dynamic_budget_enabled)
        self.assertEqual(settings.editing.teaser_budget_fraction_min, 0.15)
        self.assertEqual(settings.editing.teaser_budget_fraction_max, 0.15)
        self.assertEqual(settings.editing.teaser_budget_min_seconds, 0.1)
        self.assertEqual(settings.editing.teaser_budget_max_seconds, 5.0)
        self.assertEqual(
            settings.editing.teaser_candidate_reasons,
            ("highlight_keyword", "condensed_key_event"),
        )
        self.assertFalse(settings.editing.teaser_fallback_enabled)
        self.assertEqual(settings.editing.transition_mode, "black_card")
        self.assertEqual(settings.editing.transition_duration_seconds, 10.0)
        self.assertEqual(settings.editing.transition_text, "Back now")
        self.assertEqual(settings.editing.transition_sfx_path, Path("C:/audio/whoosh.wav"))
        self.assertEqual(settings.editing.transition_sfx_gain_db, 6.0)

    def test_edit_audio_mixing_envs_load_and_clamp(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_EDIT_AUDIO_MIXING_ENABLED"] = "1"
            os.environ["ARL_EDIT_SKIP_BGM_WHEN_SOURCE_HAS_MUSIC"] = "0"
            os.environ["ARL_EDIT_BGM_LIBRARY_PATH"] = "C:/audio/library.json"
            os.environ["ARL_EDIT_BGM_PATH"] = "C:/audio/bgm.mp3"
            os.environ["ARL_EDIT_BGM_GAIN_DB"] = "3"
            os.environ["ARL_EDIT_BGM_MULTI_PHASE_MIN_SECONDS"] = "300"
            os.environ["ARL_EDIT_BGM_SWITCH_MIN_GAP_SECONDS"] = "45"
            os.environ["ARL_EDIT_BGM_CROSSFADE_SECONDS"] = "1.5"
            os.environ["ARL_EDIT_BGM_SOURCE_MUSIC_PADDING_SECONDS"] = "4"
            os.environ["ARL_EDIT_BGM_SOURCE_MUSIC_MAJORITY_THRESHOLD"] = "0.75"
            os.environ["ARL_EDIT_SFX_PATH"] = "C:/audio/wow.wav"
            os.environ["ARL_EDIT_SFX_GAIN_DB"] = "12"
            os.environ["ARL_EDIT_SFX_LIBRARY_PATH"] = "C:/audio/sfx-library.json"
            os.environ["ARL_EDIT_SFX_TIMING_OFFSET_SECONDS"] = "-0.25"
            os.environ["ARL_EDIT_SFX_MIN_INTERVAL_SECONDS"] = "15"
            os.environ["ARL_EDIT_SFX_MAX_HITS"] = "8"
            os.environ["ARL_EDIT_SFX_KDA_ALIGNMENT_ENABLED"] = "0"
            os.environ["ARL_EDIT_SFX_MULTIKILL_WINDOW_SECONDS"] = "12"
            settings = load_settings()

        self.assertTrue(settings.editing.audio_mixing_enabled)
        self.assertFalse(settings.editing.skip_bgm_when_source_has_music)
        self.assertEqual(settings.editing.bgm_library_path, Path("C:/audio/library.json"))
        self.assertEqual(settings.editing.bgm_path, Path("C:/audio/bgm.mp3"))
        self.assertEqual(settings.editing.bgm_gain_db, 0.0)
        self.assertEqual(settings.editing.bgm_multi_phase_min_seconds, 300.0)
        self.assertEqual(settings.editing.bgm_switch_min_gap_seconds, 45.0)
        self.assertEqual(settings.editing.bgm_crossfade_seconds, 1.5)
        self.assertEqual(settings.editing.bgm_source_music_padding_seconds, 4.0)
        self.assertEqual(settings.editing.bgm_source_music_majority_threshold, 0.75)
        self.assertEqual(settings.editing.sfx_path, Path("C:/audio/wow.wav"))
        self.assertEqual(settings.editing.sfx_gain_db, 6.0)
        self.assertEqual(
            settings.editing.sfx_library_path,
            Path("C:/audio/sfx-library.json"),
        )
        self.assertEqual(settings.editing.sfx_timing_offset_seconds, -0.25)
        self.assertEqual(settings.editing.sfx_min_interval_seconds, 15.0)
        self.assertEqual(settings.editing.sfx_max_hits, 8)
        self.assertFalse(settings.editing.sfx_kda_alignment_enabled)
        self.assertEqual(settings.editing.sfx_multikill_window_seconds, 12.0)

        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_EDIT_SFX_LIBRARY_PATH"] = ""
            os.environ["ARL_EDIT_BGM_GAIN_DB"] = "-90"
            os.environ["ARL_EDIT_BGM_MULTI_PHASE_MIN_SECONDS"] = "-1"
            os.environ["ARL_EDIT_BGM_SWITCH_MIN_GAP_SECONDS"] = "-1"
            os.environ["ARL_EDIT_BGM_CROSSFADE_SECONDS"] = "9"
            os.environ["ARL_EDIT_BGM_SOURCE_MUSIC_PADDING_SECONDS"] = "-1"
            os.environ["ARL_EDIT_BGM_SOURCE_MUSIC_MAJORITY_THRESHOLD"] = "2"
            os.environ["ARL_EDIT_SFX_GAIN_DB"] = "-90"
            os.environ["ARL_EDIT_SFX_MIN_INTERVAL_SECONDS"] = "-5"
            os.environ["ARL_EDIT_SFX_MAX_HITS"] = "-1"
            os.environ["ARL_EDIT_SFX_MULTIKILL_WINDOW_SECONDS"] = "-1"
            settings = load_settings()

        self.assertFalse(settings.editing.audio_mixing_enabled)
        self.assertTrue(settings.editing.skip_bgm_when_source_has_music)
        self.assertIsNone(settings.editing.bgm_library_path)
        self.assertIsNone(settings.editing.bgm_path)
        self.assertEqual(settings.editing.bgm_gain_db, -60.0)
        self.assertEqual(settings.editing.bgm_multi_phase_min_seconds, 0.0)
        self.assertEqual(settings.editing.bgm_switch_min_gap_seconds, 0.0)
        self.assertEqual(settings.editing.bgm_crossfade_seconds, 2.0)
        self.assertEqual(settings.editing.bgm_source_music_padding_seconds, 0.0)
        self.assertEqual(settings.editing.bgm_source_music_majority_threshold, 1.0)
        self.assertIsNone(settings.editing.sfx_path)
        self.assertEqual(settings.editing.sfx_gain_db, -60.0)
        self.assertEqual(settings.editing.sfx_library_path, Path("data/sfx/library.json"))
        self.assertEqual(settings.editing.sfx_min_interval_seconds, 0.0)
        self.assertEqual(settings.editing.sfx_max_hits, 0)
        self.assertTrue(settings.editing.sfx_kda_alignment_enabled)
        self.assertEqual(settings.editing.sfx_multikill_window_seconds, 0.0)

    def test_edit_zoom_envs_load_and_clamp(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_EDIT_ZOOM_ENABLED"] = "1"
            os.environ["ARL_EDIT_ZOOM_TARGET"] = "custom"
            os.environ["ARL_EDIT_ZOOM_SCALE"] = "2"
            os.environ["ARL_EDIT_ZOOM_X_ANCHOR"] = "-0.5"
            os.environ["ARL_EDIT_ZOOM_Y_ANCHOR"] = "1.5"
            os.environ["ARL_EDIT_ZOOM_MAX_SEGMENTS"] = "-1"
            os.environ["ARL_EDIT_ZOOM_MAX_DURATION_SECONDS"] = "0"
            os.environ["ARL_EDIT_ZOOM_MODE"] = "static"
            os.environ["ARL_EDIT_ZOOM_CLOSEUP_SECONDS"] = "99"
            os.environ["ARL_EDIT_ZOOM_EASE_SECONDS"] = "-1"
            os.environ["ARL_EDIT_ZOOM_MIN_INTERVAL_SECONDS"] = "-5"
            os.environ["ARL_EDIT_ZOOM_CHAT_BURST_ENABLED"] = "0"
            os.environ["ARL_EDIT_ZOOM_CHAT_BURST_SAMPLE_INTERVAL_SECONDS"] = "0"
            os.environ["ARL_EDIT_ZOOM_CHAT_BURST_THRESHOLD"] = "2"
            settings = load_settings()

        self.assertTrue(settings.editing.zoom_enabled)
        self.assertEqual(settings.editing.zoom_target, "custom")
        self.assertEqual(settings.editing.zoom_scale, 1.5)
        self.assertEqual(settings.editing.zoom_x_anchor, 0.0)
        self.assertEqual(settings.editing.zoom_y_anchor, 1.0)
        self.assertEqual(settings.editing.zoom_max_segments, 0)
        self.assertEqual(settings.editing.zoom_max_duration_seconds, 1.0)
        self.assertEqual(settings.editing.zoom_mode, "legacy")
        self.assertEqual(settings.editing.zoom_closeup_seconds, 8.0)
        self.assertEqual(settings.editing.zoom_ease_seconds, 0.0)
        self.assertEqual(settings.editing.zoom_min_interval_seconds, 0.0)
        self.assertFalse(settings.editing.zoom_chat_burst_enabled)
        self.assertEqual(settings.editing.zoom_chat_burst_sample_interval_seconds, 0.1)
        self.assertEqual(settings.editing.zoom_chat_burst_threshold, 1.0)

        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            settings = load_settings()

        self.assertFalse(settings.editing.zoom_enabled)
        self.assertEqual(settings.editing.zoom_target, "chat")
        self.assertEqual(settings.editing.zoom_scale, 1.2)
        self.assertEqual(settings.editing.zoom_x_anchor, 0.5)
        self.assertEqual(settings.editing.zoom_y_anchor, 0.5)
        self.assertEqual(settings.editing.zoom_max_segments, 1)
        self.assertEqual(settings.editing.zoom_max_duration_seconds, 30.0)
        self.assertEqual(settings.editing.zoom_mode, "closeup")
        self.assertEqual(settings.editing.zoom_closeup_seconds, 6.0)
        self.assertEqual(settings.editing.zoom_ease_seconds, 0.4)
        self.assertEqual(settings.editing.zoom_min_interval_seconds, 25.0)
        self.assertTrue(settings.editing.zoom_chat_burst_enabled)
        self.assertEqual(settings.editing.zoom_chat_burst_sample_interval_seconds, 0.5)
        self.assertEqual(settings.editing.zoom_chat_burst_threshold, 0.08)

    def test_highlight_planner_envs_load(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_HIGHLIGHT_PLANNER_ENABLED"] = "0"
            os.environ["ARL_HIGHLIGHT_CUE_PADDING_SECONDS"] = "10.5"
            os.environ["ARL_HIGHLIGHT_KEYWORD_PADDING_SECONDS"] = "24"
            os.environ["ARL_HIGHLIGHT_MERGE_GAP_SECONDS"] = "60"
            os.environ["ARL_HIGHLIGHT_KEEP_EDGE_SECONDS"] = "20"
            os.environ["ARL_HIGHLIGHT_CONDENSED_START_EDGE_SECONDS"] = "2.5"
            os.environ["ARL_HIGHLIGHT_MIN_BOUNDARY_DURATION_SECONDS"] = "300"
            os.environ["ARL_HIGHLIGHT_MIN_REDUCTION_SECONDS"] = "45"
            os.environ["ARL_HIGHLIGHT_MIN_RETAINED_SECONDS"] = "180"
            os.environ["ARL_HIGHLIGHT_MIN_RETAINED_FRACTION"] = "0.4"
            os.environ["ARL_HIGHLIGHT_MAX_WINDOWS"] = "5"
            os.environ["ARL_HIGHLIGHT_CONDENSED_TARGET_DURATION_RANGE"] = "8,19"
            os.environ[
                "ARL_HIGHLIGHT_CONDENSED_HIGH_DENSITY_DURATION_RANGE"
            ] = "15,19"
            os.environ[
                "ARL_HIGHLIGHT_CONDENSED_MID_DENSITY_DURATION_RANGE"
            ] = "10,15"
            os.environ[
                "ARL_HIGHLIGHT_CONDENSED_LOW_DENSITY_DURATION_RANGE"
            ] = "7,10"
            os.environ["ARL_HIGHLIGHT_CONDENSED_VISUAL_SAMPLE_INTERVAL_SECONDS"] = "30"
            os.environ["ARL_HIGHLIGHT_CONDENSED_BORING_GAP_THRESHOLD_SECONDS"] = "42"
            os.environ["ARL_HIGHLIGHT_CONDENSED_COMPOSITE_TRIM_ENABLED"] = "0"
            os.environ["ARL_HIGHLIGHT_CONDENSED_INTERNAL_GAP_TRIM_SECONDS"] = "13"
            os.environ["ARL_HIGHLIGHT_CONDENSED_INTERNAL_GAP_KEEP_SECONDS"] = "2"
            os.environ["ARL_HIGHLIGHT_CONDENSED_CONTINUITY_BRIDGE_SECONDS"] = "6"
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
        self.assertEqual(settings.highlights.condensed_start_edge_seconds, 2.5)
        self.assertEqual(settings.highlights.min_boundary_duration_seconds, 300.0)
        self.assertEqual(settings.highlights.min_reduction_seconds, 45.0)
        self.assertEqual(settings.highlights.min_retained_seconds, 180.0)
        self.assertEqual(settings.highlights.min_retained_fraction, 0.4)
        self.assertEqual(settings.highlights.max_windows, 5)
        self.assertEqual(settings.highlights.condensed_target_duration_range, (8, 19))
        self.assertEqual(
            settings.highlights.condensed_high_density_duration_range,
            (15, 19),
        )
        self.assertEqual(
            settings.highlights.condensed_mid_density_duration_range,
            (10, 15),
        )
        self.assertEqual(
            settings.highlights.condensed_low_density_duration_range,
            (7, 10),
        )
        self.assertEqual(settings.highlights.condensed_visual_sample_interval_seconds, 30.0)
        self.assertEqual(settings.highlights.condensed_boring_gap_threshold_seconds, 42.0)
        self.assertFalse(settings.highlights.condensed_composite_trim_enabled)
        self.assertEqual(settings.highlights.condensed_internal_gap_trim_seconds, 13.0)
        self.assertEqual(settings.highlights.condensed_internal_gap_keep_seconds, 2.0)
        self.assertEqual(settings.highlights.condensed_continuity_bridge_seconds, 6.0)
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

    def test_highlight_condensed_defaults_use_dynamic_publish_range(self) -> None:
        with _ARLEnvIsolation(), patch("arl.config._load_dotenv"):
            settings = load_settings()

        self.assertEqual(settings.highlights.condensed_target_duration_range, (7, 20))
        self.assertEqual(
            settings.highlights.condensed_high_density_duration_range,
            (16, 20),
        )
        self.assertEqual(
            settings.highlights.condensed_mid_density_duration_range,
            (10, 16),
        )
        self.assertEqual(
            settings.highlights.condensed_low_density_duration_range,
            (7, 11),
        )
        self.assertEqual(settings.highlights.condensed_kda_kill_preroll_seconds, 15.0)
        self.assertEqual(settings.highlights.condensed_kda_death_preroll_seconds, 30.0)
        self.assertEqual(settings.highlights.condensed_kda_postroll_seconds, 5.0)
        self.assertEqual(settings.highlights.condensed_continuity_bridge_seconds, 3.0)

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
            os.environ["ARL_ASR_INITIAL_PROMPT_PATH"] = "data/asr/custom-prompt.txt"
            os.environ["ARL_ASR_INITIAL_PROMPT_MAX_CHARS"] = "-5"
            os.environ["ARL_ASR_TERM_FIXES_PATH"] = "data/asr/custom-fixes.json"
            os.environ["ARL_ASR_OPENCC_ENABLED"] = "0"
            os.environ["ARL_WHISPER_BEAM_SIZE"] = "0"
            os.environ["ARL_WHISPER_VAD_FILTER"] = "0"
            os.environ["ARL_WHISPER_VAD_MIN_SILENCE_DURATION_MS"] = "-1"
            os.environ["ARL_WHISPER_VAD_SPEECH_PAD_MS"] = "-2"
            os.environ["ARL_ASR_DISPLAY_SMOOTHING_ENABLED"] = "0"
            os.environ["ARL_ASR_DISPLAY_MIN_DURATION_SECONDS"] = "-1"
            os.environ["ARL_ASR_DISPLAY_TRAILING_HOLD_SECONDS"] = "-2"
            os.environ["ARL_ASR_DISPLAY_MAX_GAP_FILL_SECONDS"] = "-3"
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
        self.assertEqual(
            settings.subtitles.initial_prompt_path,
            Path("data/asr/custom-prompt.txt"),
        )
        self.assertEqual(settings.subtitles.initial_prompt_max_chars, 0)
        self.assertEqual(settings.subtitles.term_fixes_path, Path("data/asr/custom-fixes.json"))
        self.assertFalse(settings.subtitles.opencc_enabled)
        self.assertEqual(settings.subtitles.beam_size, 1)
        self.assertFalse(settings.subtitles.vad_filter)
        self.assertEqual(settings.subtitles.vad_min_silence_duration_ms, 0)
        self.assertEqual(settings.subtitles.vad_speech_pad_ms, 0)
        self.assertFalse(settings.subtitles.display_smoothing_enabled)
        self.assertEqual(settings.subtitles.display_min_duration_seconds, 0.0)
        self.assertEqual(settings.subtitles.display_trailing_hold_seconds, 0.0)
        self.assertEqual(settings.subtitles.display_max_gap_fill_seconds, 0.0)

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
