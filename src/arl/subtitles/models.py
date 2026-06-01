from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SubtitleStateFile(BaseModel):
    processed_match_keys: list[str] = Field(default_factory=list)


class SubtitleAuditEvent(BaseModel):
    event_type: str
    session_id: str
    match_index: int
    language: str | None = None
    language_probability: float | None = None
    reason: str | None = None
    reason_detail: str | None = None
    created_at: datetime
