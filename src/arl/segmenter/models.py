from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from arl.shared.contracts import MatchStage


class SegmenterStateFile(BaseModel):
    processed_asset_keys: list[str] = Field(default_factory=list)


class MatchStageHint(BaseModel):
    session_id: str
    stage: MatchStage
    at_seconds: float | None = None
    detected_at: datetime | None = None


class MatchStageSignal(BaseModel):
    session_id: str
    text: str
    source: str = "manual"
    at_seconds: float | None = None
    detected_at: datetime | None = None


class StageSignalIngestStateFile(BaseModel):
    processed_subtitle_keys: list[str] = Field(default_factory=list)
    emitted_signal_fingerprints_by_subtitle_key: dict[str, list[str]] = Field(
        default_factory=dict
    )
