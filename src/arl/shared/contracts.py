from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class SourceType(str, Enum):
    DIRECT_STREAM = "direct_stream"
    BROWSER_CAPTURE = "browser_capture"


class LiveState(str, Enum):
    OFFLINE = "offline"
    LIVE = "live"


class MatchStage(str, Enum):
    UNKNOWN = "unknown"
    CHAMPION_SELECT = "champion_select"
    LOADING = "loading"
    IN_GAME = "in_game"
    POST_GAME = "post_game"


class StreamDiscoveryResult(BaseModel):
    state: LiveState
    room_url: str
    streamer_name: str
    source_type: SourceType
    stream_url: str | None = None
    detected_at: datetime


class RecordingAsset(BaseModel):
    session_id: str
    source_type: SourceType
    path: str
    started_at: datetime
    ended_at: datetime | None = None


class MatchBoundary(BaseModel):
    session_id: str
    match_index: int
    started_at_seconds: float
    ended_at_seconds: float
    confidence: float


class SubtitleAsset(BaseModel):
    session_id: str
    match_index: int
    path: str
    format: str


class ExportAsset(BaseModel):
    session_id: str
    match_index: int
    path: str
    subtitle_path: str
    created_at: datetime
