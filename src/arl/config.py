from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


DEFAULT_ASR_PREPROCESS_AUDIO_FILTER = (
    "highpass=f=80,lowpass=f=7800,afftdn=nf=-25,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)


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


class SegmenterSettings(BaseModel):
    stage_keywords_path: Path | None = None
    template_fallback_enabled: bool = False


class HighlightSettings(BaseModel):
    enabled: bool = True
    cue_padding_seconds: float = 6.0
    highlight_padding_seconds: float = 22.0
    merge_gap_seconds: float = 10.0
    keep_edge_seconds: float = 30.0
    min_boundary_duration_seconds: float = 600.0
    min_reduction_seconds: float = 120.0
    min_retained_seconds: float = 480.0
    min_retained_fraction: float = 0.55
    max_windows: int = 8


class ExportSettings(BaseModel):
    enable_ffmpeg: bool = False
    ffmpeg_video_codec: str = "auto"
    burn_subtitles: bool = True
    ffmpeg_preset: str = "veryfast"
    ffmpeg_crf: int = 23
    ffmpeg_timeout_seconds: int = 120
    ffmpeg_max_retries: int = 1
    stderr_retain_count: int = 200
    backoff_initial_seconds: float = 2.0
    backoff_max_seconds: float = 8.0
    batch_fallback_budget: int = 3

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
        return self


class MaintenanceSettings(BaseModel):
    max_jsonl_bytes: int = 50 * 1024 * 1024
    keep_recent_lines: int = 5000
    launcher_log_retain_count: int = 20
    archive_dir: Path = Path("data/tmp/archive")


class Settings(BaseModel):
    douyin: DouyinSettings = Field(default_factory=DouyinSettings)
    windows_agent: WindowsAgentSettings = Field(default_factory=WindowsAgentSettings)
    platforms: list[PlatformSettings] = Field(default_factory=list)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    recording: RecordingSettings = Field(default_factory=RecordingSettings)
    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    segmenter: SegmenterSettings = Field(default_factory=SegmenterSettings)
    highlights: HighlightSettings = Field(default_factory=HighlightSettings)
    subtitles: SubtitleSettings = Field(default_factory=SubtitleSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
    maintenance: MaintenanceSettings = Field(default_factory=MaintenanceSettings)

    @model_validator(mode="after")
    def _default_platforms_from_douyin(self) -> "Settings":
        if not self.platforms:
            self.platforms = [self.douyin]
        return self


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


def _env_csv(key: str) -> list[str]:
    raw = os.getenv(key, "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _pick_indexed(values: list[str], index: int, default: str = "") -> str:
    if index < len(values):
        return values[index]
    return default


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

    return Settings(
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
        ),
        segmenter=SegmenterSettings(
            stage_keywords_path=stage_keywords_path,
            template_fallback_enabled=_env_bool(
                "ARL_SEGMENTER_TEMPLATE_FALLBACK_ENABLED",
                False,
            ),
        ),
        highlights=HighlightSettings(
            enabled=_env_bool("ARL_HIGHLIGHT_PLANNER_ENABLED", True),
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
        ),
        recording=RecordingSettings(
            preferred_resolution=os.getenv("ARL_RECORDING_PREFERRED_RESOLUTION", "1080p"),
            segment_minutes=int(os.getenv("ARL_RECORDING_SEGMENT_MINUTES", "30")),
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
            burn_subtitles=_env_bool("ARL_EXPORT_BURN_SUBTITLES", True),
            ffmpeg_preset=os.getenv("ARL_EXPORT_FFMPEG_PRESET", "veryfast"),
            ffmpeg_crf=int(os.getenv("ARL_EXPORT_FFMPEG_CRF", "23")),
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
    )
