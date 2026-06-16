from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


FrameScene = Literal["in_game", "loading", "other"]


@dataclass
class TimerReading:
    timestamp_seconds: float
    game_time_text: str | None
    confidence: float


@dataclass
class SceneReading:
    timestamp_seconds: float
    scene: FrameScene
    confidence: float


@dataclass
class MatchSegment:
    start_seconds: float
    end_seconds: float
    timer_trace: list[tuple[float, str]]
    is_complete: bool
    confidence: float
    reason: str
