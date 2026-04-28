from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from arl.shared.contracts import LiveState, SourceType


class AgentSnapshot(BaseModel):
    state: LiveState
    streamer_name: str
    room_url: str
    source_type: SourceType | None = None
    stream_url: str | None = None
    reason: str | None = None
    detected_at: datetime


class AgentStateFile(BaseModel):
    last_snapshot: AgentSnapshot | None = None


class AgentEvent(BaseModel):
    event_type: str
    snapshot: AgentSnapshot
