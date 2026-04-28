from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


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


class DouyinSettings(BaseModel):
    room_url: str = ""
    streamer_name: str = ""
    persistent_profile_dir: str = "data/tmp/chrome-profile"
    allow_browser_capture_fallback: bool = True
    poll_interval_seconds: int = 30
    state_file: Path = Path("data/tmp/windows-agent-state.json")
    event_log_path: Path = Path("data/tmp/windows-agent-events.jsonl")
    playwright_script: Path = Path("scripts/probe_douyin_room.mjs")
    playwright_timeout_ms: int = 20000
    use_playwright_probe: bool = True


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
    storage: StorageSettings = Field(default_factory=StorageSettings)
    recording: RecordingSettings = Field(default_factory=RecordingSettings)
    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    segmenter: SegmenterSettings = Field(default_factory=SegmenterSettings)
    subtitles: SubtitleSettings = Field(default_factory=SubtitleSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)


def load_settings() -> Settings:
    _load_dotenv(Path(".env"))

    room_url = os.getenv("ARL_DOUYIN_ROOM_URL", "")
    streamer_name = os.getenv("ARL_STREAMER_NAME", "")
    stage_keywords_path_raw = os.getenv("ARL_STAGE_KEYWORDS_PATH", "").strip()
    stage_keywords_path = Path(stage_keywords_path_raw) if stage_keywords_path_raw else None

    return Settings(
        douyin=DouyinSettings(
            room_url=room_url,
            streamer_name=streamer_name,
            persistent_profile_dir=os.getenv(
                "ARL_DOUYIN_PROFILE_DIR",
                "data/tmp/chrome-profile",
            ),
            allow_browser_capture_fallback=os.getenv(
                "ARL_ALLOW_BROWSER_CAPTURE_FALLBACK",
                "1",
            )
            != "0",
            poll_interval_seconds=int(
                os.getenv("ARL_DOUYIN_POLL_INTERVAL_SECONDS", "30")
            ),
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
