from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TimerReading:
    timestamp_seconds: float
    game_time_text: str | None
    confidence: float


@dataclass
class MatchSegment:
    start_seconds: float
    end_seconds: float
    timer_trace: list[tuple[float, str]]
    is_complete: bool
    confidence: float
    reason: str
