from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CopywriterStateFile(BaseModel):
    processed_match_keys: list[str] = Field(default_factory=list)


class CopyDraft(BaseModel):
    session_id: str
    match_index: int
    source_subtitle_path: str
    source_export_path: str | None = None
    transcript_excerpt: list[str]
    title_candidates: list[str]
    recommended_title: str
    description: str
    tags: list[str]
    status: str
    created_at: datetime


class PublishingPackage(BaseModel):
    session_id: str
    match_index: int
    path: str | None = None
    source_subtitle_path: str
    source_export_path: str | None = None
    source_recording_path: str | None = None
    transcript_excerpt: list[str]
    evidence: list[str]
    title_candidates: list[str]
    recommended_title: str
    summary: str
    cover_lines: list[str]
    tags: list[str]
    cover_path: str | None = None
    status: str
    created_at: datetime
