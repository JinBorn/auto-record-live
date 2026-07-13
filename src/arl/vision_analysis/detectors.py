from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import VisionEvent, VisionReading


@dataclass(frozen=True)
class RefinementRequest:
    detector: str
    started_at_seconds: float
    ended_at_seconds: float


@dataclass
class DetectorOutput:
    readings: list[VisionReading] = field(default_factory=list)
    events: list[VisionEvent] = field(default_factory=list)
    refinement_requests: list[RefinementRequest] = field(default_factory=list)


class VisionDetector(Protocol):
    name: str
    version: str
    coarse_interval_seconds: float

    def analyze(
        self,
        frame: Any,
        at_seconds: float,
        *,
        provenance: str,
    ) -> DetectorOutput: ...


class FinalizingVisionDetector(VisionDetector, Protocol):
    def finalize(self) -> DetectorOutput: ...
