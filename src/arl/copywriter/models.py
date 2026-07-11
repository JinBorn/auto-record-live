from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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


class CoverCandidate(BaseModel):
    path: str
    rank: int
    source_timestamp_seconds: float = 0.0
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    published_path: str | None = None


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
    cover_candidates: list[CoverCandidate] = Field(default_factory=list)
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


class CandidateSemanticDecision(BaseModel):
    candidate_id: str
    importance_score: float = 0.0
    story_relevance_score: float = 0.0
    emotion_score: float = 0.0
    instructional_score: float = 0.0
    outcome_clarity_score: float = 0.0
    recommendation: Literal["keep", "shorten", "drop"] = "keep"
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _accept_common_llm_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if "evidence_refs" not in normalized and "evidence_ids" in normalized:
            normalized["evidence_refs"] = normalized.get("evidence_ids")
        score = normalized.get("score")
        if isinstance(score, (int, float)):
            normalized.setdefault("importance_score", score)
            normalized.setdefault("story_relevance_score", score)
        return normalized

    @model_validator(mode="after")
    def _normalize(self) -> "CandidateSemanticDecision":
        self.candidate_id = self.candidate_id.strip()
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        for field_name in (
            "importance_score",
            "story_relevance_score",
            "emotion_score",
            "instructional_score",
            "outcome_clarity_score",
        ):
            setattr(self, field_name, min(1.0, max(0.0, getattr(self, field_name))))
        self.reason = self.reason.strip()
        self.evidence_refs = _clean_text_list(self.evidence_refs, max_items=20)
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
    schema_version: int = 1
    story_status: Literal["legacy", "strong_story", "no_strong_story"] = "legacy"
    primary_angle: str | None = None
    story_reason: str | None = None
    story_event_ids: list[str] = Field(default_factory=list)
    narrative_summary: str | None = None
    candidate_decisions: list[CandidateSemanticDecision] = Field(default_factory=list)
    teaser_candidate_ids: list[str] = Field(default_factory=list)
    claim_evidence: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _accept_common_story_shapes(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        raw_claims = normalized.get("claim_evidence")
        if isinstance(raw_claims, list):
            claims: dict[str, list[str]] = {}
            for item in raw_claims:
                if not isinstance(item, dict):
                    continue
                claim = str(item.get("claim", "")).strip()
                evidence = item.get("evidence", item.get("evidence_refs", []))
                refs: list[str] = []
                if isinstance(evidence, list):
                    for ref in evidence:
                        if isinstance(ref, dict):
                            raw_ref = ref.get("source", ref.get("evidence_id", ""))
                        else:
                            raw_ref = ref
                        text = str(raw_ref).strip()
                        if text:
                            refs.append(text)
                if claim:
                    claims[claim] = refs
            normalized["claim_evidence"] = claims
        return normalized

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
        if _compact_length(self.recommended_title) > 45:
            raise ValueError("recommended_title must be <=45 compact chars")
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
        self.schema_version = max(1, self.schema_version)
        self.primary_angle = (self.primary_angle or "").strip() or None
        self.story_reason = (self.story_reason or "").strip() or None
        self.narrative_summary = (self.narrative_summary or "").strip() or None
        self.story_event_ids = _clean_text_list(self.story_event_ids, max_items=20)
        self.teaser_candidate_ids = _clean_text_list(
            self.teaser_candidate_ids,
            max_items=3,
        )
        if self.story_status == "strong_story" and not self.primary_angle:
            raise ValueError("strong_story requires primary_angle")
        if self.story_status == "no_strong_story":
            self.primary_angle = None
            self.story_event_ids = []
            self.teaser_candidate_ids = []
            self.teaser_recommendations = []
        self.claim_evidence = {
            str(claim).strip(): _clean_text_list(refs, max_items=20)
            for claim, refs in self.claim_evidence.items()
            if str(claim).strip()
        }
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


class SemanticShadowCandidate(BaseModel):
    candidate_id: str
    started_at_seconds: float
    ended_at_seconds: float
    reason: str
    recommendation: Literal["keep", "shorten", "drop", "unscored"] = "unscored"
    semantic_score: float = 0.0
    decision_reason: str = ""


class SemanticShadowReport(BaseModel):
    session_id: str
    match_index: int
    input_fingerprint: str
    story_status: Literal["strong_story", "no_strong_story"]
    primary_angle: str | None = None
    current_total_seconds: float = 0.0
    proposed_keep_seconds: float = 0.0
    proposed_drop_seconds: float = 0.0
    candidates: list[SemanticShadowCandidate] = Field(default_factory=list)
    recommended_title: str
    cover_lines: list[str] = Field(default_factory=list)
    teaser_candidate_ids: list[str] = Field(default_factory=list)
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
