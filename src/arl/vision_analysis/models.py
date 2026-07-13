from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class VisionReading(BaseModel):
    reading_id: str
    detector: str
    at_seconds: float
    confidence: float
    payload: dict[str, Any] = Field(default_factory=dict)
    provenance: str = "coarse"


class VisionEvent(BaseModel):
    event_id: str
    kind: str
    started_at_seconds: float
    ended_at_seconds: float
    observed_at_seconds: float
    confidence: float
    evidence_reading_ids: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class VisionDetectorHealth(BaseModel):
    detector: str
    status: str = "ok"
    detail: str | None = None
    invocations: int = 0
    accepted_readings: int = 0


class VisionAnalysisMetrics(BaseModel):
    coarse_decoded_frames: int = 0
    refined_decoded_frames: int = 0
    refinement_candidate_count: int = 0
    refinement_range_count: int = 0
    refinement_source_seconds: float = 0.0
    refinement_cap_seconds: float = 0.0
    refinement_cap_exhausted: bool = False
    cache_hit: bool = False
    cache_reason: str = "miss"
    wall_time_seconds: float = 0.0


class VisionAnalysisAsset(BaseModel):
    session_id: str
    recording_path: str
    source_duration_seconds: float
    input_fingerprint: str
    config_fingerprint: str
    schema_version: int
    layout_profile: str
    status: str
    detector_health: list[VisionDetectorHealth] = Field(default_factory=list)
    readings: list[VisionReading] = Field(default_factory=list)
    events: list[VisionEvent] = Field(default_factory=list)
    metrics: VisionAnalysisMetrics = Field(default_factory=VisionAnalysisMetrics)
    created_at: datetime


class VisionAnalysisStateFile(BaseModel):
    processed_fingerprint_by_session: dict[str, str] = Field(default_factory=dict)


class VisionShadowProposal(BaseModel):
    kind: str
    started_at_seconds: float | None = None
    ended_at_seconds: float | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence_event_ids: list[str] = Field(default_factory=list)


class VisionAnalysisShadowReport(BaseModel):
    session_id: str
    input_fingerprint: str
    proposals: list[VisionShadowProposal] = Field(default_factory=list)
    accepted_event_count: int = 0
    rejected_reason: str | None = None
    created_at: datetime
