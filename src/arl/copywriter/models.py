from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


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
    streamer_name: str | None = None
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
    published_package_dir: str | None = None
    published_video_path: str | None = None
    published_cover_path: str | None = None
    published_metadata_path: str | None = None
    status: str
    created_at: datetime


class TeaserRecommendation(BaseModel):
    source_start_seconds: float
    source_end_seconds: float
    hook_reason: str

    @model_validator(mode="after")
    def _validate_window(self) -> "TeaserRecommendation":
        self.source_start_seconds = max(0.0, self.source_start_seconds)
        self.source_end_seconds = max(0.0, self.source_end_seconds)
        if self.source_end_seconds <= self.source_start_seconds:
            raise ValueError("teaser recommendation end must be after start")
        self.hook_reason = self.hook_reason.strip()
        if not self.hook_reason:
            raise ValueError("teaser recommendation hook_reason is required")
        return self


class LlmCopywritingResult(BaseModel):
    title_candidates: list[str]
    recommended_title: str
    cover_lines: list[str]
    summary: str
    description: str
    tags: list[str]
    hook_line: str | None = None
    teaser_recommendations: list[TeaserRecommendation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize_and_validate(self) -> "LlmCopywritingResult":
        self.title_candidates = _clean_text_list(self.title_candidates, max_items=3)
        if len(self.title_candidates) != 3:
            raise ValueError("LLM copywriting result requires exactly 3 title candidates")
        self.recommended_title = self.recommended_title.strip()
        if self.recommended_title not in self.title_candidates:
            self.title_candidates = _clean_text_list(
                [self.recommended_title, *self.title_candidates],
                max_items=3,
            )
        if not self.recommended_title:
            raise ValueError("recommended_title is required")
        if _compact_length(self.recommended_title) > 30:
            raise ValueError("recommended_title must be <=30 compact chars")
        self.cover_lines = _clean_text_list(self.cover_lines, max_items=4)
        if not 2 <= len(self.cover_lines) <= 4:
            raise ValueError("cover_lines must contain 2-4 lines")
        if any(_compact_length(line) > 10 for line in self.cover_lines):
            raise ValueError("each cover line must be <=10 compact chars")
        self.summary = self.summary.strip()
        if _compact_length(self.summary) > 96:
            raise ValueError("summary must be <=96 compact chars")
        self.description = self.description.strip()
        if not self.description:
            raise ValueError("description is required")
        self.tags = _clean_text_list(self.tags, max_items=8)
        if not 5 <= len(self.tags) <= 8:
            raise ValueError("tags must contain 5-8 items")
        if self.hook_line is not None:
            self.hook_line = self.hook_line.strip() or None
        self.teaser_recommendations = self.teaser_recommendations[:3]
        return self


class CopywriterSemanticAsset(BaseModel):
    session_id: str
    match_index: int
    source_subtitle_path: str
    source_highlight_plan_path: str | None = None
    provider: str
    model: str
    prompt_fingerprint: str
    input_fingerprint: str
    result: LlmCopywritingResult
    token_usage: dict[str, int] = Field(default_factory=dict)
    status: str
    created_at: datetime


def _clean_text_list(values: list[Any], *, max_items: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        cleaned.append(text)
        seen.add(text)
        if len(cleaned) >= max_items:
            break
    return cleaned


def _compact_length(value: str) -> int:
    return len("".join(value.split()))
