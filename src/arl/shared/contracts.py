from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


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
    is_complete: bool = True
    reason: str | None = None


class HighlightClipWindow(BaseModel):
    started_at_seconds: float
    ended_at_seconds: float
    reason: str


class HighlightPlanAsset(BaseModel):
    session_id: str
    match_index: int
    source_boundary_start_seconds: float
    source_boundary_end_seconds: float
    windows: list[HighlightClipWindow]
    created_at: datetime


class TimelineVideoTransform(BaseModel):
    kind: str = "none"
    scale: float = 1.0
    x_anchor: float = 0.5
    y_anchor: float = 0.5


class TimelineSegment(BaseModel):
    role: str
    source_path: str | None = None
    source_start_seconds: float
    source_end_seconds: float
    transform: TimelineVideoTransform | None = None
    reason: str


class AudioBed(BaseModel):
    source_path: str
    timeline_start_seconds: float = 0.0
    timeline_end_seconds: float | None = None
    gain_db: float = -24.0
    loop: bool = True
    reason: str = "background_music"


class SoundEffectHit(BaseModel):
    source_path: str
    at_seconds: float
    gain_db: float = -12.0
    reason: str


class EditPlanAsset(BaseModel):
    session_id: str
    match_index: int
    source_boundary_start_seconds: float
    source_boundary_end_seconds: float
    timeline: list[TimelineSegment]
    audio_beds: list[AudioBed] = Field(default_factory=list)
    sound_effects: list[SoundEffectHit] = Field(default_factory=list)
    created_at: datetime


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


class CopyAsset(BaseModel):
    session_id: str
    match_index: int
    path: str
    title: str
    description: str
    tags: list[str]
    subtitle_path: str
    export_path: str | None = None
    created_at: datetime
