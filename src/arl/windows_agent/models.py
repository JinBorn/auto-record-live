from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from arl.shared.contracts import LiveState, SourceType


class AgentSnapshot(BaseModel):
    state: LiveState
    streamer_name: str
    room_url: str
    source_type: SourceType | None = None
    stream_url: str | None = None
    stream_headers: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None
    detected_at: datetime
    platform: str = "douyin"


class AgentStateFile(BaseModel):
    """Persisted state for the windows-agent loop.

    ``last_snapshots`` is the canonical multi-platform map keyed by
    ``"<platform>:<room_url>"``. ``last_snapshot`` is kept as a backward
    compatibility shim for state files written by the single-platform
    (Douyin-only) version of this stage; on load it is converted into a
    single-entry ``last_snapshots`` map and dropped on next save.
    """

    last_snapshots: dict[str, AgentSnapshot] = Field(default_factory=dict)
    # Legacy single-platform snapshot field kept only to read older state files.
    # Excluded from serialization so new saves use the dict shape exclusively.
    last_snapshot: AgentSnapshot | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _migrate_legacy_single_snapshot(self) -> "AgentStateFile":
        if self.last_snapshot is not None and not self.last_snapshots:
            self.last_snapshots = {_state_key_for(self.last_snapshot): self.last_snapshot}
            self.last_snapshot = None
        return self

    def get(self, platform: str, room_url: str) -> AgentSnapshot | None:
        return self.last_snapshots.get(_state_key(platform, room_url))

    def set(self, snapshot: AgentSnapshot) -> None:
        self.last_snapshots[_state_key_for(snapshot)] = snapshot


class AgentEvent(BaseModel):
    event_type: str
    snapshot: AgentSnapshot


def _state_key(platform: str, room_url: str) -> str:
    return f"{platform}:{room_url}"


def _state_key_for(snapshot: AgentSnapshot) -> str:
    return _state_key(snapshot.platform, snapshot.room_url)
