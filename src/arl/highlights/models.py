from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


class HighlightPlannerStateFile(BaseModel):
    processed_match_keys: list[str] = Field(default_factory=list)


@dataclass
class ClassifiedCue:
    """字幕分类结果，用于condensed模式窗口生成。"""

    started_at_seconds: float
    ended_at_seconds: float
    text: str
    category: str  # "key_event" | "tactical" | "narration" | "low_value"
    priority: float  # 1.0 / 0.7 / 0.4 / 0.0


@dataclass
class ContentDensityResult:
    """内容密度分析结果，用于condensed模式目标时长计算。"""

    highlight_event_density: float
    narration_density: float
    visual_activity: float
    content_density_score: float
    target_duration_seconds: float


@dataclass
class WindowDraft:
    """窗口优化过程中的草稿窗口，用于condensed模式Phase 1-4。"""

    started_at_seconds: float
    ended_at_seconds: float
    reason: str
    priority: float
