from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


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


class RecordingChunk(BaseModel):
    path: str
    started_at_seconds: float
    ended_at_seconds: float
    duration_seconds: float
    index: int


class RecordingChunkManifest(BaseModel):
    session_id: str
    source_type: SourceType
    path: str
    started_at: datetime
    ended_at: datetime | None = None
    chunks: list[RecordingChunk]
    created_at: datetime


class MediaSpan(BaseModel):
    path: str
    source_start_seconds: float
    source_end_seconds: float
    local_start_seconds: float
    local_end_seconds: float


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


class KdaEventCue(BaseModel):
    """Synthetic KDA kill/death cue detected by the highlight planner OCR pass.

    Persisted on the highlight plan so downstream stages (edit-planner SFX
    alignment, zoom triggers, BGM switch points) consume the same events the
    planner protected. ``text`` keeps the ``kda_change kills=a->b deaths=c->d
    previous_at=... current_at=...`` shape existing cue parsers expect.
    """

    started_at_seconds: float
    ended_at_seconds: float
    text: str


class HighlightPlanAsset(BaseModel):
    session_id: str
    match_index: int
    source_boundary_start_seconds: float
    source_boundary_end_seconds: float
    windows: list[HighlightClipWindow]
    kda_events: list[KdaEventCue] = Field(default_factory=list)
    # Condensed planner duration accounting (additive; None on legacy assets
    # and non-condensed plans). budget_seconds is the plan-duration cap derived
    # from target_duration_seconds; budget_exception_reason records why a plan
    # was allowed to stay above budget after shrinking bottomed out.
    target_duration_seconds: float | None = None
    budget_seconds: float | None = None
    budget_exception_reason: str | None = None
    created_at: datetime


class TimelineVideoTransform(BaseModel):
    kind: str = "none"
    scale: float = 1.0
    x_anchor: float = 0.5
    y_anchor: float = 0.5
    target: str | None = None
    ease_in_seconds: float = 0.4
    ease_out_seconds: float = 0.4

    @model_validator(mode="after")
    def _validate_transform(self) -> "TimelineVideoTransform":
        if self.kind not in {"none", "punch_in"}:
            raise ValueError("transform kind must be 'none' or 'punch_in'")
        if not 0.0 <= self.x_anchor <= 1.0:
            raise ValueError("transform x_anchor must be between 0.0 and 1.0")
        if not 0.0 <= self.y_anchor <= 1.0:
            raise ValueError("transform y_anchor must be between 0.0 and 1.0")
        if self.kind == "punch_in" and not 1.0 < self.scale <= 1.5:
            raise ValueError("punch_in scale must be greater than 1.0 and at most 1.5")
        if self.target is not None:
            self.target = self.target.strip().lower() or None
        self.ease_in_seconds = min(1.0, max(0.0, self.ease_in_seconds))
        self.ease_out_seconds = min(1.0, max(0.0, self.ease_out_seconds))
        return self


class TimelineSegment(BaseModel):
    role: str
    source_path: str | None = None
    source_start_seconds: float = 0.0
    source_end_seconds: float = 0.0
    transform: TimelineVideoTransform | None = None
    reason: str
    text: str | None = None
    duration_seconds: float | None = None


class AudioBed(BaseModel):
    source_path: str
    timeline_start_seconds: float = 0.0
    timeline_end_seconds: float | None = None
    gain_db: float = -28.0
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
