from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from arl.config import (
    BilibiliSettings,
    DouyinSettings,
    ExportSettings,
    OrchestratorSettings,
    RecordingSettings,
    Settings,
    StorageSettings,
    SubtitleSettings,
    WindowsAgentSettings,
)
from arl.shared.contracts import LiveState, SourceType
from arl.windows_agent.models import AgentSnapshot
from arl.windows_agent.platform_probe import CookieState, PlatformProbe


class FakeProbe(PlatformProbe):
    def __init__(
        self,
        platform_name: str,
        snapshots: list[AgentSnapshot],
        cookie_states: list[CookieState] | None = None,
    ) -> None:
        self.platform_name = platform_name
        self._snapshots = list(snapshots)
        self._cookie_states = list(cookie_states or [])

    def detect(self) -> AgentSnapshot:
        if not self._snapshots:
            raise RuntimeError("FakeProbe snapshots exhausted")
        return self._snapshots.pop(0)

    def classify_cookie_state(self, snapshot: AgentSnapshot) -> CookieState:
        if not self._cookie_states:
            return CookieState.FRESH
        return self._cookie_states.pop(0)

    def stream_headers(self) -> dict[str, str]:
        if self._snapshots:
            return dict(self._snapshots[0].stream_headers)
        return {}


def make_live_snapshot(
    platform: str,
    *,
    stream_url: str | None = None,
    stream_headers: dict[str, str] | None = None,
    source_type: SourceType = SourceType.DIRECT_STREAM,
    room_url: str | None = None,
    reason: str | None = "stream_url_detected",
    detected_at: datetime | None = None,
) -> AgentSnapshot:
    return AgentSnapshot(
        state=LiveState.LIVE,
        streamer_name=f"{platform}-streamer",
        room_url=room_url or f"https://live.example/{platform}",
        source_type=source_type,
        stream_url=stream_url or f"https://media.example/{platform}.m3u8",
        stream_headers=dict(stream_headers or {}),
        reason=reason,
        detected_at=detected_at or datetime(2026, 5, 14, 1, 0, tzinfo=timezone.utc),
        platform=platform,
    )


def make_offline_snapshot(
    platform: str,
    *,
    reason: str = "manual_stop",
    room_url: str | None = None,
    detected_at: datetime | None = None,
) -> AgentSnapshot:
    return AgentSnapshot(
        state=LiveState.OFFLINE,
        streamer_name=f"{platform}-streamer",
        room_url=room_url or f"https://live.example/{platform}",
        source_type=None,
        stream_url=None,
        reason=reason,
        detected_at=detected_at or datetime(2026, 5, 14, 1, 30, tzinfo=timezone.utc),
        platform=platform,
    )


def build_sandboxed_settings(
    tmp_path: Path,
    platforms: tuple[str, ...] = ("douyin",),
) -> Settings:
    temp_dir = tmp_path / "tmp"
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    export_dir = tmp_path / "exports"

    douyin = DouyinSettings(
        room_url="https://live.example/douyin",
        streamer_name="douyin-streamer",
        event_log_path=temp_dir / "windows-agent-events.jsonl",
    )
    platform_settings = []
    for platform in platforms:
        if platform == "douyin":
            platform_settings.append(douyin)
        elif platform == "bilibili":
            platform_settings.append(
                BilibiliSettings(
                    room_url="https://live.example/bilibili",
                    streamer_name="bilibili-streamer",
                    sessdata="test-sessdata",
                )
            )
        else:
            raise ValueError(f"unsupported e2e platform: {platform}")

    return Settings(
        douyin=douyin,
        platforms=platform_settings,
        windows_agent=WindowsAgentSettings(
            state_file=temp_dir / "windows-agent-state.json",
            event_log_path=temp_dir / "windows-agent-events.jsonl",
        ),
        storage=StorageSettings(
            raw_dir=raw_dir,
            processed_dir=processed_dir,
            export_dir=export_dir,
            temp_dir=temp_dir,
        ),
        orchestrator=OrchestratorSettings(
            state_file=temp_dir / "orchestrator-state.json",
            agent_event_log_path=temp_dir / "windows-agent-events.jsonl",
            recorder_event_log_path=temp_dir / "recorder-events.jsonl",
            audit_log_path=temp_dir / "orchestrator-events.jsonl",
        ),
        recording=RecordingSettings(
            enable_ffmpeg=True,
            ffmpeg_max_retries=1,
            validate_actual_resolution=False,
        ),
        subtitles=SubtitleSettings(enabled=True, provider="placeholder"),
        export=ExportSettings(enable_ffmpeg=True, ffmpeg_max_retries=1),
    )


def jsonl_payloads(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def fake_successful_subprocess(command: list[str], **kwargs) -> None:
    output_path = Path(command[-1])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("fake media artifact\n", encoding="utf-8")
    return None
