from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


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
    enable_ffmpeg: bool = False
    ffmpeg_max_retries: int = 1
    auto_retry_max_attempts: int = 2
    browser_capture_input: str = ""
    browser_capture_format: str = "auto"
    browser_capture_resolution: str = "1920x1080"
    browser_capture_fps: int = 30
    browser_capture_timeout_seconds: int = 20


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


class SegmenterSettings(BaseModel):
    stage_keywords_path: Path | None = None


class ExportSettings(BaseModel):
    enable_ffmpeg: bool = False
    ffmpeg_preset: str = "veryfast"
    ffmpeg_crf: int = 23
    ffmpeg_timeout_seconds: int = 120
    ffmpeg_max_retries: int = 1


class Settings(BaseModel):
    douyin: DouyinSettings = Field(default_factory=DouyinSettings)
    windows_agent: WindowsAgentSettings = Field(default_factory=WindowsAgentSettings)
    platforms: list[PlatformSettings] = Field(default_factory=list)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    recording: RecordingSettings = Field(default_factory=RecordingSettings)
    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    segmenter: SegmenterSettings = Field(default_factory=SegmenterSettings)
    subtitles: SubtitleSettings = Field(default_factory=SubtitleSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)

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


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return raw != "0"


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
    )


_PLATFORM_LOADERS: dict[str, Callable[[], PlatformSettings]] = {
    "douyin": _load_douyin_settings,
}


def _load_platforms(default_douyin: DouyinSettings) -> list[PlatformSettings]:
    raw = os.getenv("ARL_PLATFORMS", "").strip()
    if not raw:
        # Backward compat: a deployment that never set ARL_PLATFORMS keeps
        # running the single-platform douyin loop derived from the legacy
        # ARL_DOUYIN_* env vars.
        return [default_douyin]

    platforms: list[PlatformSettings] = []
    seen: set[str] = set()
    for token in raw.split(","):
        platform_type = token.strip().lower()
        if not platform_type or platform_type in seen:
            continue
        seen.add(platform_type)
        loader = _PLATFORM_LOADERS.get(platform_type)
        if loader is None:
            raise ValueError(
                f"unknown ARL_PLATFORMS entry: {platform_type!r}; "
                f"registered={sorted(_PLATFORM_LOADERS)}"
            )
        if platform_type == "douyin":
            platforms.append(default_douyin)
        else:
            platforms.append(loader())
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
        ),
        segmenter=SegmenterSettings(stage_keywords_path=stage_keywords_path),
        recording=RecordingSettings(
            preferred_resolution=os.getenv("ARL_RECORDING_PREFERRED_RESOLUTION", "1080p"),
            segment_minutes=int(os.getenv("ARL_RECORDING_SEGMENT_MINUTES", "30")),
            direct_stream_timeout_seconds=int(
                os.getenv("ARL_DIRECT_STREAM_TIMEOUT_SECONDS", "20")
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
        ),
        export=ExportSettings(
            enable_ffmpeg=os.getenv("ARL_EXPORT_ENABLE_FFMPEG", "0") == "1",
            ffmpeg_preset=os.getenv("ARL_EXPORT_FFMPEG_PRESET", "veryfast"),
            ffmpeg_crf=int(os.getenv("ARL_EXPORT_FFMPEG_CRF", "23")),
            ffmpeg_timeout_seconds=max(
                10, int(os.getenv("ARL_EXPORT_FFMPEG_TIMEOUT_SECONDS", "120"))
            ),
            ffmpeg_max_retries=max(
                0, int(os.getenv("ARL_EXPORT_FFMPEG_MAX_RETRIES", "1"))
            ),
        ),
    )
