from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


DEFAULT_ASR_PREPROCESS_AUDIO_FILTER = (
    "highpass=f=80,lowpass=f=7800,afftdn=nf=-25,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)

DEFAULT_EXPORT_AUDIO_LOUDNORM_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"
DEFAULT_PUBLISH_BGM_LIBRARY_PATH = Path("data/bgm/library.json")
DEFAULT_SFX_LIBRARY_PATH = Path("data/sfx/library.json")


def _load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        os.environ[key] = value


class PlatformSettings(BaseModel):
    """Shared fields for any platform probe.

    Concrete platforms subclass this to add platform-specific fields (for
    example ``DouyinSettings`` adds Playwright probe wiring). The ``type``
    field is the registry key that ``build_probes`` uses to map config to a
    probe class.
    """

    type: str
    room_url: str = ""
    streamer_name: str = ""


class DouyinSettings(PlatformSettings):
    type: str = "douyin"
    persistent_profile_dir: str = "data/tmp/chrome-profile"
    allow_browser_capture_fallback: bool = True
    playwright_script: Path = Path("scripts/probe_douyin_room.mjs")
    playwright_timeout_ms: int = 20000
    use_playwright_probe: bool = True
    playwright_headless: bool = True
    # Optional Douyin cookie header value, e.g. "k1=v1; k2=v2; ...". When
    # non-empty, DouyinRoomProbe forwards it to the Playwright subprocess
    # (--cookie), the httpx fallback (Cookie request header), and the
    # AgentSnapshot.stream_headers dict so ffmpeg can replay the same cookie
    # against signed CDN URLs. Without it the page DOM only exposes _hd
    # (720p60) signed leaf URLs; the higher _uhd / _origin tiers stay
    # unsigned and unrecordable.
    cookie: str = ""
    # Minimum accepted Douyin quality tier for direct-stream availability.
    # Default "uhd" enforces 1080p-grade streams only.
    min_quality_tier: str = "uhd"


class BilibiliSettings(PlatformSettings):
    type: str = "bilibili"
    # Inherits room_url + streamer_name from PlatformSettings.
    # Pure HTTP API route per research/bilibili-live-detection.md — no
    # Playwright fields. If a future PR adds a Playwright fallback, extend
    # this model then; do not pre-add fields BilibiliRoomProbe ignores.
    # Optional SESSDATA cookie value (raw, no "SESSDATA=" prefix). When
    # non-empty, BilibiliRoomProbe injects it into both API requests and
    # ffmpeg stream headers so the response unlocks qn>=400 (1080P 蓝光);
    # anonymous calls are capped at qn=250 (720p).
    sessdata: str = ""
    # Minimum accepted current_qn for direct-stream availability.
    # Default 400 enforces 1080p baseline.
    min_stream_qn: int = 400
    # Optional minimum bitrate gate (kbps). Applied only when codec bitrate
    # metadata is present in playinfo payload.
    min_stream_bitrate_kbps: int = 4500


class WindowsAgentSettings(BaseModel):
    """Loop-level config for the windows-agent process.

    One ``state_file`` / ``event_log_path`` / ``poll_interval_seconds`` is
    shared across every probe in ``Settings.platforms`` because the agent loop
    is single-process and writes one jsonl event stream.
    """

    state_file: Path = Path("data/tmp/windows-agent-state.json")
    event_log_path: Path = Path("data/tmp/windows-agent-events.jsonl")
    poll_interval_seconds: int = 30


class StorageSettings(BaseModel):
    raw_dir: Path = Path("data/raw")
    processed_dir: Path = Path("data/processed")
    export_dir: Path = Path("data/exports")
    temp_dir: Path = Path("data/tmp")


class RecordingSettings(BaseModel):
    preferred_resolution: str = "1080p"
    segment_minutes: int = 30
    segmented_recording_enabled: bool = False
    segmented_chunk_seconds: int = 900
    direct_stream_timeout_seconds: int = 20
    direct_stream_finalize_headroom_seconds: int = 60
    max_concurrent_jobs: int = 1
    enable_ffmpeg: bool = False
    ffmpeg_max_retries: int = 1
    auto_retry_max_attempts: int = 2
    browser_capture_input: str = ""
    browser_capture_format: str = "auto"
    browser_capture_resolution: str = "1920x1080"
    browser_capture_fps: int = 30
    browser_capture_timeout_seconds: int = 20
    session_retry_budget: int = 8
    stderr_retain_count: int = 200
    validate_actual_resolution: bool = True
    min_actual_resolution_height: int = 1080
    actual_resolution_probe_timeout_seconds: int = 10


class OrchestratorSettings(BaseModel):
    poll_interval_seconds: int = 5
    agent_event_log_path: Path = Path("data/tmp/windows-agent-events.jsonl")
    recorder_event_log_path: Path = Path("data/tmp/recorder-events.jsonl")
    state_file: Path = Path("data/tmp/orchestrator-state.json")
    audit_log_path: Path = Path("data/tmp/orchestrator-events.jsonl")
    auto_create_recording_job: bool = True


class SubtitleSettings(BaseModel):
    enabled: bool = True
    provider: str = "faster-whisper"
    model_size: str = "small"
    model_size_explicit: bool = Field(default=False, exclude=True)
    language: str = "zh"
    model_cache_dir: Path = Path("data/tmp/whisper-models")
    min_language_probability: float = 0.5
    device: str = "auto"
    compute_type: str = "auto"
    cuda_compute_type: str = "auto"
    cpu_compute_type: str = "int8"
    preprocess_audio: bool = False
    preprocess_audio_filter: str = DEFAULT_ASR_PREPROCESS_AUDIO_FILTER
    preprocess_timeout_seconds: int = 120
    initial_prompt_path: Path | None = Path("data/asr/initial-prompt.txt")
    initial_prompt_max_chars: int = 1200
    term_fixes_path: Path | None = Path("data/asr/term-fixes.json")
    opencc_enabled: bool = True
    beam_size: int = 5
    vad_filter: bool = True
    vad_min_silence_duration_ms: int = 300
    vad_speech_pad_ms: int = 80
    display_smoothing_enabled: bool = True
    display_min_duration_seconds: float = 0.0
    display_trailing_hold_seconds: float = 0.15
    display_max_gap_fill_seconds: float = 0.0


class SegmenterSettings(BaseModel):
    stage_keywords_path: Path | None = None
    template_fallback_enabled: bool = False


class VisionSettings(BaseModel):
    match_detection_enabled: bool = True
    frame_sample_interval_seconds: float = 20.0
    timer_ocr_detector: str = "auto"
    timer_crop_region: tuple[int, int, int, int] = (1770, 5, 150, 50)
    match_start_threshold_seconds: float = 120.0
    lobby_gap_threshold_seconds: float = 40.0
    min_match_duration_seconds: float = 360.0  # 6 minutes minimum
    min_complete_timer_seconds: float = 900.0  # 15 minutes minimum in-game timer
    # Adaptive refinement: when a segment is missing a start boundary
    # after the coarse pass, re-sample a narrow window at this finer
    # interval (seconds) to catch loading screens that are shorter than
    # the coarse sample interval.
    match_start_refine_interval_seconds: float = 5.0
    # How far back (seconds) before the segment start to search for a
    # missed loading screen during refinement.
    match_start_refine_lookback_seconds: float = 120.0


class HighlightSettings(BaseModel):
    enabled: bool = True
    mode: str = "highlight"  # "highlight" | "condensed" | "disabled"

    # === highlight模式参数（现有，保持不变）===
    cue_padding_seconds: float = 6.0
    highlight_padding_seconds: float = 22.0
    merge_gap_seconds: float = 10.0
    keep_edge_seconds: float = 30.0
    condensed_start_edge_seconds: float | None = None
    min_boundary_duration_seconds: float = 600.0
    min_reduction_seconds: float = 120.0
    min_retained_seconds: float = 480.0
    min_retained_fraction: float = 0.55
    max_windows: int = 8

    # === condensed模式参数（新增）===
    # 内容密度权重
    condensed_weight_highlight_events: float = 0.5
    condensed_weight_narration: float = 0.25
    condensed_weight_visual: float = 0.15
    condensed_weight_baseline: float = 0.1

    # 目标时长映射
    condensed_target_duration_range: tuple[int, int] = (7, 20)  # 分钟
    condensed_high_density_threshold: float = 0.8
    condensed_low_density_threshold: float = 0.5
    condensed_high_density_duration_range: tuple[int, int] = (16, 20)
    condensed_mid_density_duration_range: tuple[int, int] = (10, 16)
    condensed_low_density_duration_range: tuple[int, int] = (7, 11)

    # 窗口生成参数
    condensed_context_padding_seconds: float = 5.0
    condensed_merge_gap_seconds: float = 8.0
    condensed_min_window_duration_seconds: float = 3.0
    condensed_silent_gap_threshold_seconds: float = 60.0
    condensed_boring_gap_threshold_seconds: float = 45.0
    condensed_composite_trim_enabled: bool = True
    condensed_internal_gap_trim_seconds: float = 8.0
    condensed_internal_gap_keep_seconds: float = 3.0
    condensed_continuity_bridge_seconds: float = 3.0
    condensed_action_resolution_tail_seconds: float = 40.0
    condensed_action_resolution_gap_seconds: float = 8.0
    condensed_combat_continuity_enabled: bool = True
    condensed_combat_sample_interval_seconds: float = 2.0
    condensed_combat_enter_activity_threshold: float = 0.055
    condensed_combat_release_activity_threshold: float = 0.025
    condensed_combat_lookaround_seconds: float = 30.0
    condensed_combat_release_samples: int = 3
    condensed_combat_safety_cap_seconds: float = 180.0

    # 优先级权重
    condensed_priority_key_event: float = 1.0
    condensed_priority_tactical: float = 0.7
    condensed_priority_narration: float = 0.4

    # 低价值对话过滤
    condensed_low_value_min_length: int = 3
    condensed_low_value_similarity_threshold: float = 0.8
    condensed_low_value_repeat_window_seconds: float = 30.0

    # 视觉分析
    condensed_use_visual_analysis: bool = True
    condensed_visual_sample_interval_seconds: float = 10.0
    condensed_visual_weight_scene_change: float = 0.5
    condensed_visual_weight_minimap: float = 0.3
    condensed_visual_weight_edge_density: float = 0.2

    # KDA-based event preservation for condensed exports.
    condensed_kda_event_detection_enabled: bool = True
    condensed_kda_frame_refinement_enabled: bool = False
    condensed_kda_crop_region: tuple[int, int, int, int] = (1665, 0, 85, 32)
    condensed_kda_sample_interval_seconds: float = 10.0
    condensed_kda_min_confidence: float = 0.4
    condensed_kda_max_reading_gap_seconds: float = 120.0
    condensed_kda_max_event_delta: int = 8
    condensed_kda_kill_preroll_seconds: float = 15.0
    condensed_kda_death_preroll_seconds: float = 30.0
    condensed_kda_postroll_seconds: float = 5.0
    condensed_kda_post_death_kill_suppression_seconds: float = 0.0
    condensed_kda_death_wait_trim_seconds: float = 120.0
    condensed_kda_death_silent_gap_trim_seconds: float = 10.0
    condensed_kda_death_silent_trim_lookback_seconds: float = 30.0
    condensed_kda_death_reaction_tail_seconds: float = 3.0

    # Final-stage duration budget shrinking for condensed plans. When the
    # post-restore/bridge fixpoint still exceeds the duration budget, trim the
    # lowest-value window spans (KDA cue spans stay fully protected, cuts snap
    # to speech-safe boundaries) until the plan fits or bottoms out.
    condensed_budget_shrink_enabled: bool = True
    condensed_budget_trim_step_seconds: float = 15.0
    condensed_budget_max_speech_extension_seconds: float = 3.0

    # 用户自定义术语
    custom_tactical_keywords: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_mode(self) -> "HighlightSettings":
        """Validate mode field is one of the allowed values."""
        if self.mode not in {"highlight", "condensed", "disabled"}:
            raise ValueError(
                f"highlights.mode must be one of 'highlight', 'condensed', or 'disabled', got '{self.mode}'"
            )
        return self


class EditingSettings(BaseModel):
    enabled: bool = False
    teaser_max_segments: int = 2
    teaser_max_total_seconds: float = 45.0
    teaser_min_segment_seconds: float = 3.0
    teaser_dynamic_budget_enabled: bool = True
    teaser_budget_fraction_min: float = 0.08
    teaser_budget_fraction_max: float = 0.12
    teaser_budget_min_seconds: float = 20.0
    teaser_budget_max_seconds: float = 90.0
    teaser_candidate_reasons: tuple[str, ...] = (
        "highlight_keyword",
        "condensed_key_event",
    )
    teaser_fallback_enabled: bool = True
    transition_mode: str = "none"
    transition_duration_seconds: float = 1.25
    transition_text: str = "Back to match start"
    transition_sfx_path: Path | None = None
    transition_sfx_gain_db: float = -12.0
    teaser_impact_sfx_gain_db: float = -10.0
    zoom_enabled: bool = False
    zoom_target: str = "chat"
    zoom_scale: float = 1.2
    zoom_x_anchor: float = 0.5
    zoom_y_anchor: float = 0.5
    zoom_max_segments: int = 1
    zoom_max_duration_seconds: float = 30.0
    zoom_mode: str = "closeup"
    zoom_closeup_seconds: float = 6.0
    zoom_ease_seconds: float = 0.4
    zoom_min_interval_seconds: float = 25.0
    zoom_chat_burst_enabled: bool = True
    zoom_chat_burst_sample_interval_seconds: float = 0.5
    zoom_chat_burst_threshold: float = 0.08
    audio_mixing_enabled: bool = False
    skip_bgm_when_source_has_music: bool = True
    zoom_fallback_enabled: bool = False
    bgm_library_path: Path | None = None
    bgm_path: Path | None = None
    bgm_gain_db: float = -28.0
    bgm_multi_phase_min_seconds: float = 600.0
    bgm_switch_min_gap_seconds: float = 60.0
    bgm_crossfade_seconds: float = 2.0
    bgm_source_music_padding_seconds: float = 2.0
    bgm_source_music_majority_threshold: float = 0.35
    sfx_path: Path | None = None
    sfx_gain_db: float = -12.0
    sfx_library_path: Path | None = DEFAULT_SFX_LIBRARY_PATH
    sfx_timing_offset_seconds: float = 0.0
    sfx_min_interval_seconds: float = 20.0
    sfx_max_hits: int = 6
    sfx_kda_alignment_enabled: bool = True
    sfx_multikill_window_seconds: float = 8.0

    @model_validator(mode="after")
    def _normalize_editing_settings(self) -> "EditingSettings":
        aliases = {
            "chat": "chat",
            "game_chat": "chat",
            "bottom_left": "chat",
            "bottom-left": "chat",
            "center": "center",
            "centre": "center",
            "custom": "custom",
        }
        raw = self.zoom_target.strip().lower()
        if raw not in aliases:
            raise ValueError("zoom_target must be one of chat, center, or custom")
        self.zoom_target = aliases[raw]
        zoom_mode_aliases = {
            "closeup": "closeup",
            "close-up": "closeup",
            "close_up": "closeup",
            "split": "closeup",
            "legacy": "legacy",
            "segment": "legacy",
            "static": "legacy",
        }
        zoom_mode = self.zoom_mode.strip().lower()
        if zoom_mode not in zoom_mode_aliases:
            raise ValueError("zoom_mode must be one of closeup or legacy")
        self.zoom_mode = zoom_mode_aliases[zoom_mode]
        transition_aliases = {
            "none": "none",
            "off": "none",
            "disabled": "none",
            "black_card": "black_card",
            "black-card": "black_card",
            "card": "black_card",
            "crossfade": "crossfade",
            "cross-fade": "crossfade",
        }
        transition_mode = self.transition_mode.strip().lower()
        if transition_mode not in transition_aliases:
            raise ValueError("transition_mode must be one of none, black_card, or crossfade")
        self.transition_mode = transition_aliases[transition_mode]
        if self.teaser_budget_fraction_min < 0.0:
            self.teaser_budget_fraction_min = 0.0
        if self.teaser_budget_fraction_max < self.teaser_budget_fraction_min:
            self.teaser_budget_fraction_max = self.teaser_budget_fraction_min
        self.teaser_budget_min_seconds = max(0.1, self.teaser_budget_min_seconds)
        self.teaser_budget_max_seconds = max(
            self.teaser_budget_min_seconds,
            self.teaser_budget_max_seconds,
        )
        self.transition_duration_seconds = min(
            10.0,
            max(0.1, self.transition_duration_seconds),
        )
        self.transition_text = self.transition_text.strip() or "Back to match start"
        candidate_reasons = tuple(
            dict.fromkeys(
                reason.strip()
                for reason in self.teaser_candidate_reasons
                if reason.strip()
            )
        )
        self.teaser_candidate_reasons = candidate_reasons or ("highlight_keyword",)
        self.bgm_multi_phase_min_seconds = max(0.0, self.bgm_multi_phase_min_seconds)
        self.bgm_switch_min_gap_seconds = max(0.0, self.bgm_switch_min_gap_seconds)
        self.bgm_crossfade_seconds = min(2.0, max(1.0, self.bgm_crossfade_seconds))
        self.bgm_source_music_padding_seconds = max(
            0.0,
            self.bgm_source_music_padding_seconds,
        )
        self.bgm_source_music_majority_threshold = min(
            1.0,
            max(0.0, self.bgm_source_music_majority_threshold),
        )
        self.sfx_min_interval_seconds = max(0.0, self.sfx_min_interval_seconds)
        self.sfx_max_hits = max(0, self.sfx_max_hits)
        self.sfx_multikill_window_seconds = max(
            0.0,
            self.sfx_multikill_window_seconds,
        )
        self.zoom_closeup_seconds = min(8.0, max(3.0, self.zoom_closeup_seconds))
        self.zoom_ease_seconds = min(1.0, max(0.0, self.zoom_ease_seconds))
        self.zoom_min_interval_seconds = max(0.0, self.zoom_min_interval_seconds)
        self.zoom_chat_burst_sample_interval_seconds = max(
            0.1,
            self.zoom_chat_burst_sample_interval_seconds,
        )
        self.zoom_chat_burst_threshold = min(
            1.0,
            max(0.0, self.zoom_chat_burst_threshold),
        )
        return self


class ExportSettings(BaseModel):
    enable_ffmpeg: bool = False
    ffmpeg_video_codec: str = "auto"
    burn_subtitles: bool = False
    use_ass_subtitles: bool = False
    ass_font_name: str = "SimHei"
    ass_font_size: int = 32
    ass_margin_v: int = 110
    ass_outline: int = 2
    ass_max_chars_per_line: int = 18
    ass_max_lines: int = 2
    ffmpeg_preset: str = "slow"
    ffmpeg_crf: int = 18
    ffmpeg_bitrate: str | None = None
    ffmpeg_max_bitrate: str | None = None
    ffmpeg_timeout_seconds: int = 120
    ffmpeg_max_retries: int = 1
    stderr_retain_count: int = 200
    backoff_initial_seconds: float = 2.0
    backoff_max_seconds: float = 8.0
    batch_fallback_budget: int = 3
    use_edit_plans: bool = False
    use_highlight_plans: bool = False
    use_hardware_encoding: bool = False
    audio_loudnorm_enabled: bool = False
    audio_loudnorm_filter: str = DEFAULT_EXPORT_AUDIO_LOUDNORM_FILTER

    @model_validator(mode="after")
    def _normalize_ffmpeg_video_codec(self) -> "ExportSettings":
        aliases = {
            "": "auto",
            "auto": "auto",
            "copy": "copy",
            "h264": "h264",
            "avc": "h264",
            "x264": "h264",
            "libx264": "h264",
            "h265": "h265",
            "hevc": "h265",
            "x265": "h265",
            "libx265": "h265",
        }
        raw = self.ffmpeg_video_codec.strip().lower()
        if raw not in aliases:
            raise ValueError(
                "ffmpeg_video_codec must be one of auto, copy, h264, h265, or hevc"
            )
        self.ffmpeg_video_codec = aliases[raw]
        self.ass_font_name = self.ass_font_name.strip() or "SimHei"
        self.ass_font_size = max(1, self.ass_font_size)
        self.ass_margin_v = max(0, self.ass_margin_v)
        self.ass_outline = max(0, self.ass_outline)
        self.ass_max_chars_per_line = max(1, self.ass_max_chars_per_line)
        self.ass_max_lines = max(1, self.ass_max_lines)
        self.audio_loudnorm_filter = (
            self.audio_loudnorm_filter.strip()
            or DEFAULT_EXPORT_AUDIO_LOUDNORM_FILTER
        )
        return self


class MaintenanceSettings(BaseModel):
    max_jsonl_bytes: int = 50 * 1024 * 1024
    keep_recent_lines: int = 5000
    launcher_log_retain_count: int = 20
    archive_dir: Path = Path("data/tmp/archive")


class QualityReportSettings(BaseModel):
    subtitle_active_ratio_min: float = 0.40
    long_no_subtitle_gap_min_seconds: float = 8.0
    max_source_gap_seconds: float = 45.0
    teaser_min_segments: int = 1
    teaser_max_segments: int = 3
    sfx_max_hits: int = 6
    zoom_min_segments: int = 1
    zoom_max_segments: int = 4
    top_no_subtitle_gaps: int = 5
    duration_budget_enforced: bool = True

    @model_validator(mode="after")
    def _normalize_thresholds(self) -> "QualityReportSettings":
        self.subtitle_active_ratio_min = min(
            1.0,
            max(0.0, self.subtitle_active_ratio_min),
        )
        self.long_no_subtitle_gap_min_seconds = max(
            0.0,
            self.long_no_subtitle_gap_min_seconds,
        )
        self.max_source_gap_seconds = max(0.0, self.max_source_gap_seconds)
        self.teaser_min_segments = max(0, self.teaser_min_segments)
        self.teaser_max_segments = max(
            self.teaser_min_segments,
            self.teaser_max_segments,
        )
        self.sfx_max_hits = max(0, self.sfx_max_hits)
        self.zoom_min_segments = max(0, self.zoom_min_segments)
        self.zoom_max_segments = max(
            self.zoom_min_segments,
            self.zoom_max_segments,
        )
        self.top_no_subtitle_gaps = max(1, self.top_no_subtitle_gaps)
        return self


class CopywriterSettings(BaseModel):
    cover_max_candidates: int = 1

    @model_validator(mode="after")
    def _normalize(self) -> "CopywriterSettings":
        self.cover_max_candidates = max(1, self.cover_max_candidates)
        return self


class LlmSettings(BaseModel):
    enabled: bool = False
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    timeout_seconds: float = 30.0
    max_retries: int = 2
    max_input_cues: int = 160
    temperature: float = 0.4
    story_analysis_enabled: bool = False
    story_shadow_mode: bool = True
    semantic_weight: float = 0.25
    semantic_schema_version: int = 2
    semantic_sfx_enabled: bool = True
    semantic_sfx_shadow_mode: bool = True
    semantic_sfx_min_confidence: float = 0.80
    semantic_sfx_max_hits: int = 2
    semantic_sfx_max_per_category: int = 1
    semantic_sfx_min_spacing_seconds: float = 8.0
    semantic_sfx_max_candidates: int = 20

    @model_validator(mode="after")
    def _normalize(self) -> "LlmSettings":
        self.base_url = self.base_url.strip().rstrip("/")
        self.api_key = self.api_key.strip()
        self.model = self.model.strip() or "deepseek-chat"
        self.timeout_seconds = max(1.0, self.timeout_seconds)
        self.max_retries = max(0, self.max_retries)
        self.max_input_cues = max(20, self.max_input_cues)
        self.temperature = min(1.5, max(0.0, self.temperature))
        self.semantic_weight = min(1.0, max(0.0, self.semantic_weight))
        self.semantic_schema_version = max(1, self.semantic_schema_version)
        self.semantic_sfx_min_confidence = min(
            1.0,
            max(0.0, self.semantic_sfx_min_confidence),
        )
        self.semantic_sfx_max_hits = max(0, self.semantic_sfx_max_hits)
        self.semantic_sfx_max_per_category = max(
            1,
            self.semantic_sfx_max_per_category,
        )
        self.semantic_sfx_min_spacing_seconds = max(
            0.0,
            self.semantic_sfx_min_spacing_seconds,
        )
        self.semantic_sfx_max_candidates = max(1, self.semantic_sfx_max_candidates)
        return self


class Settings(BaseModel):
    douyin: DouyinSettings = Field(default_factory=DouyinSettings)
    windows_agent: WindowsAgentSettings = Field(default_factory=WindowsAgentSettings)
    platforms: list[PlatformSettings] = Field(default_factory=list)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    recording: RecordingSettings = Field(default_factory=RecordingSettings)
    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    segmenter: SegmenterSettings = Field(default_factory=SegmenterSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)
    highlights: HighlightSettings = Field(default_factory=HighlightSettings)
    editing: EditingSettings = Field(default_factory=EditingSettings)
    subtitles: SubtitleSettings = Field(default_factory=SubtitleSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
    maintenance: MaintenanceSettings = Field(default_factory=MaintenanceSettings)
    quality_report: QualityReportSettings = Field(default_factory=QualityReportSettings)
    copywriter: CopywriterSettings = Field(default_factory=CopywriterSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)

    @model_validator(mode="after")
    def _default_platforms_from_douyin(self) -> "Settings":
        if not self.platforms:
            self.platforms = [self.douyin]
        return self


def apply_publish_preset(settings: Settings) -> Settings:
    """Enable the full publish-edit pipeline on top of existing settings."""

    ffmpeg_bitrate = _publish_min_bitrate(settings.export.ffmpeg_bitrate, "8000k")
    ffmpeg_max_bitrate = _publish_min_bitrate(
        settings.export.ffmpeg_max_bitrate,
        "10000k",
    )
    bgm_library_path = settings.editing.bgm_library_path
    if bgm_library_path is None and settings.editing.bgm_path is None:
        bgm_library_path = DEFAULT_PUBLISH_BGM_LIBRARY_PATH
    subtitles = settings.subtitles
    if (
        not subtitles.model_size_explicit
        and subtitles.device in {"auto", "cuda"}
        and subtitles.model_size == "small"
    ):
        subtitles = subtitles.model_copy(update={"model_size": "medium"})
    transition_mode = settings.editing.transition_mode
    if (
        transition_mode == "none"
        and os.getenv("ARL_EDIT_TRANSITION_MODE", "").strip() == ""
    ):
        transition_mode = "black_card"
    zoom_max_segments = settings.editing.zoom_max_segments
    if os.getenv("ARL_EDIT_ZOOM_MAX_SEGMENTS", "").strip() == "":
        zoom_max_segments = max(zoom_max_segments, 3)
    # Publish refines KDA changes to their first stable video frame, so no
    # guessed timing offset is needed. Explicit operator overrides still win.
    sfx_timing_offset_seconds = settings.editing.sfx_timing_offset_seconds
    if os.getenv("ARL_EDIT_SFX_TIMING_OFFSET_SECONDS", "").strip() == "":
        sfx_timing_offset_seconds = 0.0
    sfx_gain_db = settings.editing.sfx_gain_db
    if os.getenv("ARL_EDIT_SFX_GAIN_DB", "").strip() == "":
        sfx_gain_db = -7.0

    return settings.model_copy(
        deep=True,
        update={
            "highlights": settings.highlights.model_copy(
                update={
                    "enabled": True,
                    "mode": "condensed",
                    "keep_edge_seconds": min(
                        settings.highlights.keep_edge_seconds,
                        10.0,
                    ),
                    "condensed_start_edge_seconds": min(
                        (
                            settings.highlights.condensed_start_edge_seconds
                            if settings.highlights.condensed_start_edge_seconds
                            is not None
                            else settings.highlights.keep_edge_seconds
                        ),
                        1.0,
                    ),
                    "condensed_kda_frame_refinement_enabled": True,
                }
            ),
            "editing": settings.editing.model_copy(
                update={
                    "enabled": True,
                    "zoom_enabled": True,
                    "zoom_max_segments": zoom_max_segments,
                    "audio_mixing_enabled": True,
                    "bgm_library_path": bgm_library_path,
                    "transition_mode": transition_mode,
                    "sfx_timing_offset_seconds": sfx_timing_offset_seconds,
                    "sfx_gain_db": sfx_gain_db,
                }
            ),
            "export": settings.export.model_copy(
                update={
                    "enable_ffmpeg": True,
                    "burn_subtitles": True,
                    "use_ass_subtitles": True,
                    "use_edit_plans": True,
                    "use_highlight_plans": True,
                    "ffmpeg_video_codec": (
                        "h264"
                        if settings.export.ffmpeg_video_codec == "auto"
                        else settings.export.ffmpeg_video_codec
                    ),
                    "ffmpeg_bitrate": ffmpeg_bitrate,
                    "ffmpeg_max_bitrate": ffmpeg_max_bitrate,
                    "audio_loudnorm_enabled": True,
                }
            ),
            "subtitles": subtitles,
        },
    )


def _publish_min_bitrate(current: str | None, minimum: str) -> str:
    if current is None:
        return minimum
    current_kbps = _bitrate_to_kbps(current)
    minimum_kbps = _bitrate_to_kbps(minimum)
    if current_kbps is None or minimum_kbps is None:
        return current
    if current_kbps < minimum_kbps:
        return minimum
    return current


def _bitrate_to_kbps(value: str) -> float | None:
    raw = value.strip().lower()
    if not raw:
        return None
    multiplier = 1.0
    number = raw
    if raw.endswith("kbps"):
        number = raw[:-4]
    elif raw.endswith("k"):
        number = raw[:-1]
    elif raw.endswith("mbps"):
        number = raw[:-4]
        multiplier = 1000.0
    elif raw.endswith("m"):
        number = raw[:-1]
        multiplier = 1000.0
    try:
        return float(number.strip()) * multiplier
    except ValueError:
        return None


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return raw != "0"


def _postprocess_publish_preset_enabled() -> bool:
    preset = os.getenv("ARL_POSTPROCESS_PRESET", "").strip().lower()
    if preset:
        return preset in {"publish", "publishing", "bilibili"}
    return _env_bool("ARL_POSTPROCESS_PUBLISH_PRESET", False)


def _env_csv(key: str) -> list[str]:
    raw = os.getenv(key, "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_int_tuple4(
    key: str,
    default: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    parts = [item.strip() for item in raw.split(",")]
    if len(parts) != 4:
        raise ValueError(f"{key} must contain four comma-separated integers")
    return (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))


def _env_minute_range(key: str, default: tuple[int, int]) -> tuple[int, int]:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    parts = [item.strip() for item in raw.split(",")]
    if len(parts) != 2:
        raise ValueError(f"{key} must contain two comma-separated integers")
    lower = max(1, int(parts[0]))
    upper = max(lower, int(parts[1]))
    return (lower, upper)


def _pick_indexed(values: list[str], index: int, default: str = "") -> str:
    if index < len(values):
        return values[index]
    return default


def _env_optional_path(key: str, default: Path | None = None) -> Path | None:
    raw = os.getenv(key, "").strip()
    return Path(raw) if raw else default


def _load_douyin_settings() -> DouyinSettings:
    return DouyinSettings(
        room_url=os.getenv("ARL_DOUYIN_ROOM_URL", ""),
        streamer_name=os.getenv("ARL_STREAMER_NAME", ""),
        persistent_profile_dir=os.getenv(
            "ARL_DOUYIN_PROFILE_DIR",
            "data/tmp/chrome-profile",
        ),
        allow_browser_capture_fallback=os.getenv(
            "ARL_ALLOW_BROWSER_CAPTURE_FALLBACK",
            "1",
        )
        != "0",
        playwright_script=Path(
            os.getenv(
                "ARL_DOUYIN_PLAYWRIGHT_SCRIPT",
                "scripts/probe_douyin_room.mjs",
            )
        ),
        playwright_timeout_ms=int(
            os.getenv("ARL_DOUYIN_PLAYWRIGHT_TIMEOUT_MS", "20000")
        ),
        use_playwright_probe=os.getenv("ARL_USE_PLAYWRIGHT_PROBE", "1") != "0",
        playwright_headless=_env_bool("ARL_DOUYIN_PLAYWRIGHT_HEADLESS", True),
        cookie=os.getenv("ARL_DOUYIN_COOKIE", ""),
        min_quality_tier=os.getenv("ARL_DOUYIN_MIN_QUALITY_TIER", "uhd"),
    )


def _load_douyin_settings_list() -> list[DouyinSettings]:
    room_urls = _env_csv("ARL_DOUYIN_ROOM_URLS")
    if not room_urls:
        room_urls = _env_csv("ARL_DOUYIN_ROOM_URL")
    if not room_urls:
        return [_load_douyin_settings()]

    streamer_names = _env_csv("ARL_DOUYIN_STREAMER_NAMES")
    if not streamer_names:
        streamer_names = _env_csv("ARL_STREAMER_NAME")
    legacy = _load_douyin_settings()
    settings: list[DouyinSettings] = []
    for index, room_url in enumerate(room_urls):
        settings.append(
            legacy.model_copy(
                update={
                    "room_url": room_url,
                    "streamer_name": _pick_indexed(
                        streamer_names,
                        index,
                        legacy.streamer_name,
                    ),
                }
            )
        )
    return settings


def _load_bilibili_settings() -> BilibiliSettings:
    return BilibiliSettings(
        room_url=os.getenv("ARL_BILIBILI_ROOM_URL", ""),
        streamer_name=os.getenv("ARL_BILIBILI_STREAMER_NAME", ""),
        sessdata=os.getenv("ARL_BILIBILI_SESSDATA", ""),
        min_stream_qn=_env_int("ARL_BILIBILI_MIN_STREAM_QN", 400),
        min_stream_bitrate_kbps=max(
            0,
            _env_int("ARL_BILIBILI_MIN_STREAM_BITRATE_KBPS", 4500),
        ),
    )


def _load_bilibili_settings_list() -> list[BilibiliSettings]:
    room_urls = _env_csv("ARL_BILIBILI_ROOM_URLS")
    if not room_urls:
        room_urls = _env_csv("ARL_BILIBILI_ROOM_URL")
    if not room_urls:
        return [_load_bilibili_settings()]

    streamer_names = _env_csv("ARL_BILIBILI_STREAMER_NAMES")
    if not streamer_names:
        streamer_names = _env_csv("ARL_BILIBILI_STREAMER_NAME")
    legacy = _load_bilibili_settings()
    settings: list[BilibiliSettings] = []
    for index, room_url in enumerate(room_urls):
        settings.append(
            legacy.model_copy(
                update={
                    "room_url": room_url,
                    "streamer_name": _pick_indexed(
                        streamer_names,
                        index,
                        legacy.streamer_name,
                    ),
                }
            )
        )
    return settings


_PLATFORM_LOADERS: dict[str, Callable[[], PlatformSettings]] = {
    "douyin": _load_douyin_settings,
    "bilibili": _load_bilibili_settings,
}

_PLATFORM_LIST_LOADERS: dict[str, Callable[[], list[PlatformSettings]]] = {
    "douyin": _load_douyin_settings_list,
    "bilibili": _load_bilibili_settings_list,
}


def _load_platforms(default_douyin: DouyinSettings) -> list[PlatformSettings]:
    raw = os.getenv("ARL_PLATFORMS", "").strip()
    if not raw:
        # Backward compat: a deployment that never set ARL_PLATFORMS keeps
        # running the single-platform douyin loop derived from the legacy
        # ARL_DOUYIN_* env vars.
        if _env_csv("ARL_DOUYIN_ROOM_URLS") or len(_env_csv("ARL_DOUYIN_ROOM_URL")) > 1:
            return _load_douyin_settings_list()
        return [default_douyin]

    platforms: list[PlatformSettings] = []
    seen: set[str] = set()
    for token in raw.split(","):
        platform_type = token.strip().lower()
        if not platform_type or platform_type in seen:
            continue
        seen.add(platform_type)
        loader = _PLATFORM_LIST_LOADERS.get(platform_type)
        if loader is None:
            raise ValueError(
                f"unknown ARL_PLATFORMS entry: {platform_type!r}; "
                f"registered={sorted(_PLATFORM_LOADERS)}"
            )
        if (
            platform_type == "douyin"
            and not _env_csv("ARL_DOUYIN_ROOM_URLS")
            and len(_env_csv("ARL_DOUYIN_ROOM_URL")) <= 1
        ):
            platforms.append(default_douyin)
        else:
            platforms.extend(loader())
    return platforms


def load_settings() -> Settings:
    _load_dotenv(Path(".env"))

    douyin_settings = _load_douyin_settings()
    platforms = _load_platforms(douyin_settings)

    stage_keywords_path_raw = os.getenv("ARL_STAGE_KEYWORDS_PATH", "").strip()
    stage_keywords_path = Path(stage_keywords_path_raw) if stage_keywords_path_raw else None
    condensed_target_duration_range = _env_minute_range(
        "ARL_HIGHLIGHT_CONDENSED_TARGET_DURATION_RANGE",
        (7, 20),
    )
    condensed_high_density_duration_range = _env_minute_range(
        "ARL_HIGHLIGHT_CONDENSED_HIGH_DENSITY_DURATION_RANGE",
        (16, 20),
    )
    condensed_mid_density_duration_range = _env_minute_range(
        "ARL_HIGHLIGHT_CONDENSED_MID_DENSITY_DURATION_RANGE",
        (10, 16),
    )
    condensed_low_density_duration_range = _env_minute_range(
        "ARL_HIGHLIGHT_CONDENSED_LOW_DENSITY_DURATION_RANGE",
        (7, 11),
    )

    settings = Settings(
        douyin=douyin_settings,
        platforms=platforms,
        windows_agent=WindowsAgentSettings(
            state_file=Path(
                os.getenv(
                    "ARL_WINDOWS_AGENT_STATE_FILE",
                    "data/tmp/windows-agent-state.json",
                )
            ),
            event_log_path=Path(
                os.getenv(
                    "ARL_WINDOWS_AGENT_EVENT_LOG",
                    "data/tmp/windows-agent-events.jsonl",
                )
            ),
            poll_interval_seconds=_env_int(
                "ARL_AGENT_POLL_INTERVAL_SECONDS",
                _env_int("ARL_DOUYIN_POLL_INTERVAL_SECONDS", 30),
            ),
        ),
        orchestrator=OrchestratorSettings(
            poll_interval_seconds=int(
                os.getenv("ARL_ORCHESTRATOR_POLL_INTERVAL_SECONDS", "5")
            ),
            agent_event_log_path=Path(
                os.getenv(
                    "ARL_ORCHESTRATOR_AGENT_EVENT_LOG",
                    "data/tmp/windows-agent-events.jsonl",
                )
            ),
            recorder_event_log_path=Path(
                os.getenv(
                    "ARL_ORCHESTRATOR_RECORDER_EVENT_LOG",
                    "data/tmp/recorder-events.jsonl",
                )
            ),
            state_file=Path(
                os.getenv(
                    "ARL_ORCHESTRATOR_STATE_FILE",
                    "data/tmp/orchestrator-state.json",
                )
            ),
            audit_log_path=Path(
                os.getenv(
                    "ARL_ORCHESTRATOR_AUDIT_LOG",
                    "data/tmp/orchestrator-events.jsonl",
                )
            ),
            auto_create_recording_job=os.getenv(
                "ARL_ORCHESTRATOR_AUTO_CREATE_RECORDING_JOB",
                "1",
            )
            != "0",
        ),
        subtitles=SubtitleSettings(
            enabled=os.getenv("ARL_SUBTITLES_ENABLED", "1") != "0",
            provider=os.getenv("ARL_SUBTITLE_PROVIDER", "faster-whisper"),
            model_size=os.getenv("ARL_WHISPER_MODEL_SIZE", "small"),
            model_size_explicit="ARL_WHISPER_MODEL_SIZE" in os.environ,
            language=os.getenv("ARL_SUBTITLE_LANGUAGE", "zh"),
            model_cache_dir=Path(
                os.getenv(
                    "ARL_WHISPER_MODEL_CACHE_DIR",
                    "data/tmp/whisper-models",
                )
            ),
            min_language_probability=min(
                1.0,
                max(
                    0.0,
                    float(os.getenv("ARL_WHISPER_MIN_LANGUAGE_PROBABILITY", "0.5")),
                ),
            ),
            device=os.getenv("ARL_WHISPER_DEVICE", "auto").strip().lower() or "auto",
            compute_type=(
                os.getenv("ARL_WHISPER_COMPUTE_TYPE", "auto").strip().lower() or "auto"
            ),
            cuda_compute_type=(
                os.getenv("ARL_WHISPER_CUDA_COMPUTE_TYPE", "auto").strip().lower()
                or "auto"
            ),
            cpu_compute_type=(
                os.getenv("ARL_WHISPER_CPU_COMPUTE_TYPE", "int8").strip().lower()
                or "int8"
            ),
            preprocess_audio=_env_bool("ARL_ASR_PREPROCESS_AUDIO", False),
            preprocess_audio_filter=os.getenv(
                "ARL_ASR_PREPROCESS_AUDIO_FILTER",
                DEFAULT_ASR_PREPROCESS_AUDIO_FILTER,
            ).strip()
            or DEFAULT_ASR_PREPROCESS_AUDIO_FILTER,
            preprocess_timeout_seconds=max(
                10,
                _env_int("ARL_ASR_PREPROCESS_TIMEOUT_SECONDS", 120),
            ),
            initial_prompt_path=_env_optional_path(
                "ARL_ASR_INITIAL_PROMPT_PATH",
                Path("data/asr/initial-prompt.txt"),
            ),
            initial_prompt_max_chars=max(
                0,
                _env_int("ARL_ASR_INITIAL_PROMPT_MAX_CHARS", 1200),
            ),
            term_fixes_path=_env_optional_path(
                "ARL_ASR_TERM_FIXES_PATH",
                Path("data/asr/term-fixes.json"),
            ),
            opencc_enabled=_env_bool("ARL_ASR_OPENCC_ENABLED", True),
            beam_size=max(1, _env_int("ARL_WHISPER_BEAM_SIZE", 5)),
            vad_filter=_env_bool("ARL_WHISPER_VAD_FILTER", True),
            vad_min_silence_duration_ms=max(
                0,
                _env_int("ARL_WHISPER_VAD_MIN_SILENCE_DURATION_MS", 300),
            ),
            vad_speech_pad_ms=max(
                0,
                _env_int("ARL_WHISPER_VAD_SPEECH_PAD_MS", 80),
            ),
            display_smoothing_enabled=_env_bool(
                "ARL_ASR_DISPLAY_SMOOTHING_ENABLED",
                True,
            ),
            display_min_duration_seconds=max(
                0.0,
                _env_float("ARL_ASR_DISPLAY_MIN_DURATION_SECONDS", 0.0),
            ),
            display_trailing_hold_seconds=max(
                0.0,
                _env_float("ARL_ASR_DISPLAY_TRAILING_HOLD_SECONDS", 0.15),
            ),
            display_max_gap_fill_seconds=max(
                0.0,
                _env_float("ARL_ASR_DISPLAY_MAX_GAP_FILL_SECONDS", 0.0),
            ),
        ),
        segmenter=SegmenterSettings(
            stage_keywords_path=stage_keywords_path,
            template_fallback_enabled=_env_bool(
                "ARL_SEGMENTER_TEMPLATE_FALLBACK_ENABLED",
                False,
            ),
        ),
        vision=VisionSettings(
            match_detection_enabled=_env_bool("ARL_VISION_MATCH_DETECTION_ENABLED", True),
            frame_sample_interval_seconds=_env_float(
                "ARL_VISION_FRAME_SAMPLE_INTERVAL_SECONDS", 20.0
            ),
            timer_ocr_detector=os.getenv("ARL_VISION_TIMER_OCR_DETECTOR", "auto"),
            match_start_threshold_seconds=_env_float(
                "ARL_VISION_MATCH_START_THRESHOLD_SECONDS", 120.0
            ),
            lobby_gap_threshold_seconds=_env_float(
                "ARL_VISION_LOBBY_GAP_THRESHOLD_SECONDS", 40.0
            ),
            min_match_duration_seconds=max(
                60.0,
                _env_float("ARL_VISION_MIN_MATCH_DURATION_SECONDS", 360.0),
            ),
            min_complete_timer_seconds=max(
                0.0,
                _env_float("ARL_VISION_MIN_COMPLETE_TIMER_SECONDS", 900.0),
            ),
            match_start_refine_interval_seconds=max(
                1.0,
                _env_float("ARL_VISION_MATCH_START_REFINE_INTERVAL_SECONDS", 5.0),
            ),
            match_start_refine_lookback_seconds=max(
                30.0,
                _env_float("ARL_VISION_MATCH_START_REFINE_LOOKBACK_SECONDS", 120.0),
            ),
        ),
        highlights=HighlightSettings(
            enabled=_env_bool("ARL_HIGHLIGHT_PLANNER_ENABLED", True),
            mode=os.getenv("ARL_HIGHLIGHT_MODE", "highlight"),
            cue_padding_seconds=max(
                0.0,
                _env_float("ARL_HIGHLIGHT_CUE_PADDING_SECONDS", 6.0),
            ),
            highlight_padding_seconds=max(
                0.0,
                _env_float("ARL_HIGHLIGHT_KEYWORD_PADDING_SECONDS", 22.0),
            ),
            merge_gap_seconds=max(
                0.0,
                _env_float("ARL_HIGHLIGHT_MERGE_GAP_SECONDS", 10.0),
            ),
            keep_edge_seconds=max(
                0.0,
                _env_float("ARL_HIGHLIGHT_KEEP_EDGE_SECONDS", 30.0),
            ),
            condensed_start_edge_seconds=(
                max(
                    0.0,
                    _env_float("ARL_HIGHLIGHT_CONDENSED_START_EDGE_SECONDS", 0.0),
                )
                if os.getenv("ARL_HIGHLIGHT_CONDENSED_START_EDGE_SECONDS", "").strip()
                else None
            ),
            min_boundary_duration_seconds=max(
                0.0,
                _env_float("ARL_HIGHLIGHT_MIN_BOUNDARY_DURATION_SECONDS", 600.0),
            ),
            min_reduction_seconds=max(
                0.0,
                _env_float("ARL_HIGHLIGHT_MIN_REDUCTION_SECONDS", 120.0),
            ),
            min_retained_seconds=max(
                0.0,
                _env_float("ARL_HIGHLIGHT_MIN_RETAINED_SECONDS", 480.0),
            ),
            min_retained_fraction=min(
                1.0,
                max(0.0, _env_float("ARL_HIGHLIGHT_MIN_RETAINED_FRACTION", 0.55)),
            ),
            max_windows=max(1, _env_int("ARL_HIGHLIGHT_MAX_WINDOWS", 8)),
            condensed_target_duration_range=condensed_target_duration_range,
            condensed_high_density_duration_range=condensed_high_density_duration_range,
            condensed_mid_density_duration_range=condensed_mid_density_duration_range,
            condensed_low_density_duration_range=condensed_low_density_duration_range,
            condensed_visual_sample_interval_seconds=max(
                1.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_VISUAL_SAMPLE_INTERVAL_SECONDS",
                    10.0,
                ),
            ),
            condensed_boring_gap_threshold_seconds=max(
                1.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_BORING_GAP_THRESHOLD_SECONDS",
                    45.0,
                ),
            ),
            condensed_composite_trim_enabled=_env_bool(
                "ARL_HIGHLIGHT_CONDENSED_COMPOSITE_TRIM_ENABLED",
                True,
            ),
            condensed_internal_gap_trim_seconds=max(
                0.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_INTERNAL_GAP_TRIM_SECONDS",
                    8.0,
                ),
            ),
            condensed_internal_gap_keep_seconds=max(
                0.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_INTERNAL_GAP_KEEP_SECONDS",
                    3.0,
                ),
            ),
            condensed_continuity_bridge_seconds=max(
                0.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_CONTINUITY_BRIDGE_SECONDS",
                    3.0,
                ),
            ),
            condensed_action_resolution_tail_seconds=max(
                0.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_ACTION_RESOLUTION_TAIL_SECONDS",
                    40.0,
                ),
            ),
            condensed_action_resolution_gap_seconds=max(
                0.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_ACTION_RESOLUTION_GAP_SECONDS",
                    8.0,
                ),
            ),
            condensed_combat_continuity_enabled=_env_bool(
                "ARL_HIGHLIGHT_CONDENSED_COMBAT_CONTINUITY_ENABLED",
                True,
            ),
            condensed_combat_sample_interval_seconds=max(
                0.5,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_COMBAT_SAMPLE_INTERVAL_SECONDS",
                    2.0,
                ),
            ),
            condensed_combat_enter_activity_threshold=min(
                1.0,
                max(
                    0.0,
                    _env_float(
                        "ARL_HIGHLIGHT_CONDENSED_COMBAT_ENTER_ACTIVITY_THRESHOLD",
                        0.055,
                    ),
                ),
            ),
            condensed_combat_release_activity_threshold=min(
                1.0,
                max(
                    0.0,
                    _env_float(
                        "ARL_HIGHLIGHT_CONDENSED_COMBAT_RELEASE_ACTIVITY_THRESHOLD",
                        0.025,
                    ),
                ),
            ),
            condensed_combat_lookaround_seconds=max(
                0.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_COMBAT_LOOKAROUND_SECONDS",
                    30.0,
                ),
            ),
            condensed_combat_release_samples=max(
                2,
                _env_int(
                    "ARL_HIGHLIGHT_CONDENSED_COMBAT_RELEASE_SAMPLES",
                    3,
                ),
            ),
            condensed_combat_safety_cap_seconds=max(
                30.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_COMBAT_SAFETY_CAP_SECONDS",
                    180.0,
                ),
            ),
            condensed_kda_event_detection_enabled=_env_bool(
                "ARL_HIGHLIGHT_CONDENSED_KDA_EVENT_DETECTION_ENABLED",
                True,
            ),
            condensed_kda_crop_region=_env_int_tuple4(
                "ARL_HIGHLIGHT_CONDENSED_KDA_CROP_REGION",
                (1665, 0, 85, 32),
            ),
            condensed_kda_sample_interval_seconds=max(
                1.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_KDA_SAMPLE_INTERVAL_SECONDS",
                    10.0,
                ),
            ),
            condensed_kda_min_confidence=min(
                1.0,
                max(
                    0.0,
                    _env_float("ARL_HIGHLIGHT_CONDENSED_KDA_MIN_CONFIDENCE", 0.4),
                ),
            ),
            condensed_kda_max_reading_gap_seconds=max(
                1.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_KDA_MAX_READING_GAP_SECONDS",
                    120.0,
                ),
            ),
            condensed_kda_max_event_delta=max(
                1,
                _env_int("ARL_HIGHLIGHT_CONDENSED_KDA_MAX_EVENT_DELTA", 8),
            ),
            condensed_kda_kill_preroll_seconds=max(
                0.0,
                _env_float("ARL_HIGHLIGHT_CONDENSED_KDA_KILL_PREROLL_SECONDS", 15.0),
            ),
            condensed_kda_death_preroll_seconds=max(
                0.0,
                _env_float("ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_PREROLL_SECONDS", 30.0),
            ),
            condensed_kda_postroll_seconds=max(
                0.0,
                _env_float("ARL_HIGHLIGHT_CONDENSED_KDA_POSTROLL_SECONDS", 5.0),
            ),
            condensed_kda_post_death_kill_suppression_seconds=max(
                0.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_KDA_POST_DEATH_KILL_SUPPRESSION_SECONDS",
                    0.0,
                ),
            ),
            condensed_kda_death_wait_trim_seconds=max(
                0.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_WAIT_TRIM_SECONDS",
                    120.0,
                ),
            ),
            condensed_kda_death_silent_gap_trim_seconds=max(
                0.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_SILENT_GAP_TRIM_SECONDS",
                    10.0,
                ),
            ),
            condensed_kda_death_silent_trim_lookback_seconds=max(
                0.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_SILENT_TRIM_LOOKBACK_SECONDS",
                    30.0,
                ),
            ),
            condensed_kda_death_reaction_tail_seconds=max(
                0.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_REACTION_TAIL_SECONDS",
                    3.0,
                ),
            ),
            condensed_budget_shrink_enabled=_env_bool(
                "ARL_HIGHLIGHT_CONDENSED_BUDGET_SHRINK_ENABLED",
                True,
            ),
            condensed_budget_trim_step_seconds=max(
                3.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_BUDGET_TRIM_STEP_SECONDS",
                    15.0,
                ),
            ),
            condensed_budget_max_speech_extension_seconds=max(
                0.0,
                _env_float(
                    "ARL_HIGHLIGHT_CONDENSED_BUDGET_MAX_SPEECH_EXTENSION_SECONDS",
                    3.0,
                ),
            ),
        ),
        editing=EditingSettings(
            enabled=_env_bool("ARL_EDIT_PLANNER_ENABLED", False),
            teaser_max_segments=max(1, _env_int("ARL_EDIT_TEASER_MAX_SEGMENTS", 2)),
            teaser_max_total_seconds=max(
                1.0,
                _env_float("ARL_EDIT_TEASER_MAX_TOTAL_SECONDS", 45.0),
            ),
            teaser_min_segment_seconds=max(
                0.1,
                _env_float("ARL_EDIT_TEASER_MIN_SEGMENT_SECONDS", 3.0),
            ),
            teaser_dynamic_budget_enabled=_env_bool(
                "ARL_EDIT_TEASER_DYNAMIC_BUDGET_ENABLED",
                True,
            ),
            teaser_budget_fraction_min=max(
                0.0,
                _env_float("ARL_EDIT_TEASER_BUDGET_FRACTION_MIN", 0.08),
            ),
            teaser_budget_fraction_max=max(
                0.0,
                _env_float("ARL_EDIT_TEASER_BUDGET_FRACTION_MAX", 0.12),
            ),
            teaser_budget_min_seconds=max(
                0.1,
                _env_float("ARL_EDIT_TEASER_BUDGET_MIN_SECONDS", 20.0),
            ),
            teaser_budget_max_seconds=max(
                0.1,
                _env_float("ARL_EDIT_TEASER_BUDGET_MAX_SECONDS", 90.0),
            ),
            teaser_candidate_reasons=tuple(
                _env_csv("ARL_EDIT_TEASER_CANDIDATE_REASONS")
                or ["highlight_keyword", "condensed_key_event"]
            ),
            teaser_fallback_enabled=_env_bool(
                "ARL_EDIT_TEASER_FALLBACK_ENABLED",
                True,
            ),
            transition_mode=os.getenv("ARL_EDIT_TRANSITION_MODE", "none"),
            transition_duration_seconds=max(
                0.1,
                _env_float("ARL_EDIT_TRANSITION_DURATION_SECONDS", 1.25),
            ),
            transition_text=os.getenv(
                "ARL_EDIT_TRANSITION_TEXT",
                "Back to match start",
            ),
            transition_sfx_path=_env_optional_path("ARL_EDIT_TRANSITION_SFX_PATH"),
            transition_sfx_gain_db=min(
                6.0,
                max(-60.0, _env_float("ARL_EDIT_TRANSITION_SFX_GAIN_DB", -12.0)),
            ),
            teaser_impact_sfx_gain_db=min(
                6.0,
                max(-60.0, _env_float("ARL_EDIT_TEASER_IMPACT_SFX_GAIN_DB", -10.0)),
            ),
            zoom_enabled=_env_bool("ARL_EDIT_ZOOM_ENABLED", False),
            zoom_target=os.getenv("ARL_EDIT_ZOOM_TARGET", "chat"),
            zoom_scale=min(
                1.5,
                max(1.0, _env_float("ARL_EDIT_ZOOM_SCALE", 1.2)),
            ),
            zoom_x_anchor=min(
                1.0,
                max(0.0, _env_float("ARL_EDIT_ZOOM_X_ANCHOR", 0.5)),
            ),
            zoom_y_anchor=min(
                1.0,
                max(0.0, _env_float("ARL_EDIT_ZOOM_Y_ANCHOR", 0.5)),
            ),
            zoom_max_segments=max(0, _env_int("ARL_EDIT_ZOOM_MAX_SEGMENTS", 1)),
            zoom_max_duration_seconds=max(
                1.0,
                _env_float("ARL_EDIT_ZOOM_MAX_DURATION_SECONDS", 30.0),
            ),
            zoom_mode=os.getenv("ARL_EDIT_ZOOM_MODE", "closeup"),
            zoom_closeup_seconds=min(
                8.0,
                max(3.0, _env_float("ARL_EDIT_ZOOM_CLOSEUP_SECONDS", 6.0)),
            ),
            zoom_ease_seconds=min(
                1.0,
                max(0.0, _env_float("ARL_EDIT_ZOOM_EASE_SECONDS", 0.4)),
            ),
            zoom_min_interval_seconds=max(
                0.0,
                _env_float("ARL_EDIT_ZOOM_MIN_INTERVAL_SECONDS", 25.0),
            ),
            zoom_chat_burst_enabled=_env_bool(
                "ARL_EDIT_ZOOM_CHAT_BURST_ENABLED",
                True,
            ),
            zoom_chat_burst_sample_interval_seconds=max(
                0.1,
                _env_float("ARL_EDIT_ZOOM_CHAT_BURST_SAMPLE_INTERVAL_SECONDS", 0.5),
            ),
            zoom_chat_burst_threshold=min(
                1.0,
                max(0.0, _env_float("ARL_EDIT_ZOOM_CHAT_BURST_THRESHOLD", 0.08)),
            ),
            zoom_fallback_enabled=_env_bool(
                "ARL_EDIT_ZOOM_FALLBACK_ENABLED",
                False,
            ),
            audio_mixing_enabled=_env_bool(
                "ARL_EDIT_AUDIO_MIXING_ENABLED",
                False,
            ),
            skip_bgm_when_source_has_music=_env_bool(
                "ARL_EDIT_SKIP_BGM_WHEN_SOURCE_HAS_MUSIC",
                True,
            ),
            bgm_library_path=_env_optional_path("ARL_EDIT_BGM_LIBRARY_PATH"),
            bgm_path=_env_optional_path("ARL_EDIT_BGM_PATH"),
            bgm_gain_db=min(
                0.0,
                max(-60.0, _env_float("ARL_EDIT_BGM_GAIN_DB", -28.0)),
            ),
            bgm_multi_phase_min_seconds=max(
                0.0,
                _env_float("ARL_EDIT_BGM_MULTI_PHASE_MIN_SECONDS", 600.0),
            ),
            bgm_switch_min_gap_seconds=max(
                0.0,
                _env_float("ARL_EDIT_BGM_SWITCH_MIN_GAP_SECONDS", 60.0),
            ),
            bgm_crossfade_seconds=min(
                2.0,
                max(1.0, _env_float("ARL_EDIT_BGM_CROSSFADE_SECONDS", 2.0)),
            ),
            bgm_source_music_padding_seconds=max(
                0.0,
                _env_float("ARL_EDIT_BGM_SOURCE_MUSIC_PADDING_SECONDS", 2.0),
            ),
            bgm_source_music_majority_threshold=min(
                1.0,
                max(
                    0.0,
                    _env_float("ARL_EDIT_BGM_SOURCE_MUSIC_MAJORITY_THRESHOLD", 0.35),
                ),
            ),
            sfx_path=_env_optional_path("ARL_EDIT_SFX_PATH"),
            sfx_gain_db=min(
                6.0,
                max(-60.0, _env_float("ARL_EDIT_SFX_GAIN_DB", -12.0)),
            ),
            sfx_library_path=_env_optional_path(
                "ARL_EDIT_SFX_LIBRARY_PATH",
                DEFAULT_SFX_LIBRARY_PATH,
            ),
            sfx_timing_offset_seconds=_env_float(
                "ARL_EDIT_SFX_TIMING_OFFSET_SECONDS",
                0.0,
            ),
            sfx_min_interval_seconds=max(
                0.0,
                _env_float("ARL_EDIT_SFX_MIN_INTERVAL_SECONDS", 20.0),
            ),
            sfx_max_hits=max(0, _env_int("ARL_EDIT_SFX_MAX_HITS", 6)),
            sfx_kda_alignment_enabled=_env_bool(
                "ARL_EDIT_SFX_KDA_ALIGNMENT_ENABLED",
                True,
            ),
            sfx_multikill_window_seconds=max(
                0.0,
                _env_float("ARL_EDIT_SFX_MULTIKILL_WINDOW_SECONDS", 8.0),
            ),
        ),
        recording=RecordingSettings(
            preferred_resolution=os.getenv("ARL_RECORDING_PREFERRED_RESOLUTION", "1080p"),
            segment_minutes=int(os.getenv("ARL_RECORDING_SEGMENT_MINUTES", "30")),
            segmented_recording_enabled=_env_bool(
                "ARL_RECORDING_SEGMENTED_ENABLED",
                False,
            ),
            segmented_chunk_seconds=max(
                1,
                _env_int("ARL_RECORDING_SEGMENTED_CHUNK_SECONDS", 900),
            ),
            direct_stream_timeout_seconds=int(
                os.getenv("ARL_DIRECT_STREAM_TIMEOUT_SECONDS", "20")
            ),
            direct_stream_finalize_headroom_seconds=max(
                0,
                int(os.getenv("ARL_RECORDING_FINALIZE_HEADROOM_SECONDS", "60")),
            ),
            max_concurrent_jobs=max(
                1,
                _env_int("ARL_RECORDER_MAX_CONCURRENT_JOBS", 1),
            ),
            enable_ffmpeg=os.getenv("ARL_RECORDING_ENABLE_FFMPEG", "0") == "1",
            ffmpeg_max_retries=max(
                0, int(os.getenv("ARL_RECORDING_FFMPEG_MAX_RETRIES", "1"))
            ),
            auto_retry_max_attempts=max(
                0, int(os.getenv("ARL_RECORDING_AUTO_RETRY_MAX_ATTEMPTS", "2"))
            ),
            browser_capture_input=os.getenv("ARL_BROWSER_CAPTURE_INPUT", ""),
            browser_capture_format=os.getenv(
                "ARL_BROWSER_CAPTURE_FORMAT",
                "auto",
            ),
            browser_capture_resolution=os.getenv(
                "ARL_BROWSER_CAPTURE_RESOLUTION",
                "1920x1080",
            ),
            browser_capture_fps=max(
                1, int(os.getenv("ARL_BROWSER_CAPTURE_FPS", "30"))
            ),
            browser_capture_timeout_seconds=max(
                1, int(os.getenv("ARL_BROWSER_CAPTURE_TIMEOUT_SECONDS", "20"))
            ),
            session_retry_budget=max(
                1, int(os.getenv("ARL_RECORDER_SESSION_RETRY_BUDGET", "8"))
            ),
            stderr_retain_count=max(
                0, int(os.getenv("ARL_RECORDER_STDERR_RETAIN_COUNT", "200"))
            ),
            validate_actual_resolution=_env_bool(
                "ARL_RECORDING_VALIDATE_ACTUAL_RESOLUTION",
                True,
            ),
            min_actual_resolution_height=max(
                1,
                _env_int("ARL_RECORDING_MIN_ACTUAL_RESOLUTION_HEIGHT", 1080),
            ),
            actual_resolution_probe_timeout_seconds=max(
                1,
                _env_int("ARL_RECORDING_ACTUAL_RESOLUTION_PROBE_TIMEOUT_SECONDS", 10),
            ),
        ),
        export=ExportSettings(
            enable_ffmpeg=os.getenv("ARL_EXPORT_ENABLE_FFMPEG", "0") == "1",
            ffmpeg_video_codec=os.getenv("ARL_EXPORT_FFMPEG_VIDEO_CODEC", "auto"),
            burn_subtitles=_env_bool("ARL_EXPORT_BURN_SUBTITLES", False),
            use_ass_subtitles=_env_bool("ARL_EXPORT_USE_ASS_SUBTITLES", False),
            ass_font_name=os.getenv("ARL_EXPORT_ASS_FONT_NAME", "SimHei"),
            ass_font_size=max(1, _env_int("ARL_EXPORT_ASS_FONT_SIZE", 32)),
            ass_margin_v=max(0, _env_int("ARL_EXPORT_ASS_MARGIN_V", 110)),
            ass_outline=max(0, _env_int("ARL_EXPORT_ASS_OUTLINE", 2)),
            ass_max_chars_per_line=max(
                1,
                _env_int("ARL_EXPORT_ASS_MAX_CHARS_PER_LINE", 18),
            ),
            ass_max_lines=max(1, _env_int("ARL_EXPORT_ASS_MAX_LINES", 2)),
            ffmpeg_preset=os.getenv("ARL_EXPORT_FFMPEG_PRESET", "slow"),
            ffmpeg_crf=int(os.getenv("ARL_EXPORT_FFMPEG_CRF", "18")),
            ffmpeg_bitrate=os.getenv("ARL_EXPORT_FFMPEG_BITRATE") or None,
            ffmpeg_max_bitrate=os.getenv("ARL_EXPORT_FFMPEG_MAX_BITRATE") or None,
            ffmpeg_timeout_seconds=max(
                10, int(os.getenv("ARL_EXPORT_FFMPEG_TIMEOUT_SECONDS", "120"))
            ),
            ffmpeg_max_retries=max(
                0, int(os.getenv("ARL_EXPORT_FFMPEG_MAX_RETRIES", "1"))
            ),
            stderr_retain_count=max(
                0, int(os.getenv("ARL_EXPORTER_STDERR_RETAIN_COUNT", "200"))
            ),
            backoff_initial_seconds=max(
                0.0,
                float(os.getenv("ARL_EXPORTER_BACKOFF_INITIAL_SECONDS", "2")),
            ),
            backoff_max_seconds=max(
                0.0,
                float(os.getenv("ARL_EXPORTER_BACKOFF_MAX_SECONDS", "8")),
            ),
            batch_fallback_budget=max(
                1,
                int(os.getenv("ARL_EXPORTER_BATCH_FALLBACK_BUDGET", "3")),
            ),
            use_edit_plans=_env_bool("ARL_EXPORT_USE_EDIT_PLANS", False),
            use_highlight_plans=_env_bool("ARL_EXPORT_USE_HIGHLIGHT_PLANS", False),
            use_hardware_encoding=_env_bool("ARL_EXPORT_USE_HARDWARE_ENCODING", False),
            audio_loudnorm_enabled=_env_bool(
                "ARL_EXPORT_AUDIO_LOUDNORM_ENABLED",
                False,
            ),
            audio_loudnorm_filter=os.getenv(
                "ARL_EXPORT_AUDIO_LOUDNORM_FILTER",
                DEFAULT_EXPORT_AUDIO_LOUDNORM_FILTER,
            )
            or DEFAULT_EXPORT_AUDIO_LOUDNORM_FILTER,
        ),
        maintenance=MaintenanceSettings(
            max_jsonl_bytes=max(
                1024,
                _env_int("ARL_MAINTENANCE_MAX_JSONL_BYTES", 50 * 1024 * 1024),
            ),
            keep_recent_lines=max(
                100,
                _env_int("ARL_MAINTENANCE_KEEP_RECENT_LINES", 5000),
            ),
            launcher_log_retain_count=max(
                0,
                _env_int("ARL_LAUNCHER_LOG_RETAIN_COUNT", 20),
            ),
            archive_dir=Path(
                os.getenv("ARL_MAINTENANCE_ARCHIVE_DIR", "data/tmp/archive")
            ),
        ),
        quality_report=QualityReportSettings(
            subtitle_active_ratio_min=_env_float(
                "ARL_QUALITY_REPORT_SUBTITLE_ACTIVE_RATIO_MIN",
                0.40,
            ),
            long_no_subtitle_gap_min_seconds=_env_float(
                "ARL_QUALITY_REPORT_LONG_NO_SUBTITLE_GAP_MIN_SECONDS",
                8.0,
            ),
            max_source_gap_seconds=_env_float(
                "ARL_QUALITY_REPORT_MAX_SOURCE_GAP_SECONDS",
                45.0,
            ),
            teaser_min_segments=_env_int(
                "ARL_QUALITY_REPORT_TEASER_MIN_SEGMENTS",
                1,
            ),
            teaser_max_segments=_env_int(
                "ARL_QUALITY_REPORT_TEASER_MAX_SEGMENTS",
                3,
            ),
            sfx_max_hits=_env_int("ARL_QUALITY_REPORT_SFX_MAX_HITS", 6),
            zoom_min_segments=_env_int(
                "ARL_QUALITY_REPORT_ZOOM_MIN_SEGMENTS",
                1,
            ),
            zoom_max_segments=_env_int(
                "ARL_QUALITY_REPORT_ZOOM_MAX_SEGMENTS",
                4,
            ),
            top_no_subtitle_gaps=_env_int(
                "ARL_QUALITY_REPORT_TOP_NO_SUBTITLE_GAPS",
                5,
            ),
            duration_budget_enforced=_env_bool(
                "ARL_QUALITY_REPORT_DURATION_BUDGET_ENFORCED",
                True,
            ),
        ),
        copywriter=CopywriterSettings(
            cover_max_candidates=max(
                1,
                _env_int("ARL_COPY_COVER_MAX_CANDIDATES", 1),
            ),
        ),
        llm=LlmSettings(
            enabled=_env_bool("ARL_LLM_ENABLED", False),
            base_url=os.getenv("ARL_LLM_BASE_URL", "https://api.deepseek.com/v1"),
            api_key=os.getenv("ARL_LLM_API_KEY", ""),
            model=os.getenv("ARL_LLM_MODEL", "deepseek-chat"),
            timeout_seconds=_env_float("ARL_LLM_TIMEOUT_SECONDS", 30.0),
            max_retries=_env_int("ARL_LLM_MAX_RETRIES", 2),
            max_input_cues=_env_int("ARL_LLM_MAX_INPUT_CUES", 160),
            temperature=_env_float("ARL_LLM_TEMPERATURE", 0.4),
            story_analysis_enabled=_env_bool(
                "ARL_LLM_STORY_ANALYSIS_ENABLED",
                False,
            ),
            story_shadow_mode=_env_bool("ARL_LLM_STORY_SHADOW_MODE", True),
            semantic_weight=_env_float("ARL_HIGHLIGHT_SEMANTIC_WEIGHT", 0.25),
            semantic_schema_version=_env_int("ARL_LLM_SEMANTIC_SCHEMA_VERSION", 2),
            semantic_sfx_enabled=_env_bool(
                "ARL_LLM_SEMANTIC_SFX_ENABLED",
                True,
            ),
            semantic_sfx_shadow_mode=_env_bool(
                "ARL_LLM_SEMANTIC_SFX_SHADOW_MODE",
                True,
            ),
            semantic_sfx_min_confidence=_env_float(
                "ARL_LLM_SEMANTIC_SFX_MIN_CONFIDENCE",
                0.80,
            ),
            semantic_sfx_max_hits=_env_int(
                "ARL_LLM_SEMANTIC_SFX_MAX_HITS",
                2,
            ),
            semantic_sfx_max_per_category=_env_int(
                "ARL_LLM_SEMANTIC_SFX_MAX_PER_CATEGORY",
                1,
            ),
            semantic_sfx_min_spacing_seconds=_env_float(
                "ARL_LLM_SEMANTIC_SFX_MIN_SPACING_SECONDS",
                8.0,
            ),
            semantic_sfx_max_candidates=_env_int(
                "ARL_LLM_SEMANTIC_SFX_MAX_CANDIDATES",
                20,
            ),
        ),
    )
    if _postprocess_publish_preset_enabled():
        settings = apply_publish_preset(settings)
    return settings
