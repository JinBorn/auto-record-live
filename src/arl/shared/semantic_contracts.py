from __future__ import annotations

from pydantic import BaseModel, Field


class SemanticCandidateDecisionView(BaseModel):
    candidate_id: str
    importance_score: float = 0.0
    story_relevance_score: float = 0.0
    emotion_score: float = 0.0
    instructional_score: float = 0.0
    outcome_clarity_score: float = 0.0
    recommendation: str = "keep"


class SemanticSfxRecommendationView(BaseModel):
    candidate_id: str
    category: str = "none"
    confidence: float = 0.0
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str = ""


class SemanticResultView(BaseModel):
    candidate_decisions: list[SemanticCandidateDecisionView] = Field(
        default_factory=list
    )
    sfx_recommendations: list[SemanticSfxRecommendationView] = Field(
        default_factory=list
    )


class SemanticAssetView(BaseModel):
    """Read-only cross-stage view of the persisted copywriter semantic asset."""

    session_id: str
    match_index: int
    result: SemanticResultView
