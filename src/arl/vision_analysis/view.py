from __future__ import annotations

from pathlib import Path

from arl.shared.jsonl_store import load_models
from arl.vision.models import SceneReading, TimerReading

from .models import VisionAnalysisAsset, VisionEvent


class VisionAnalysisView:
    def __init__(self, asset: VisionAnalysisAsset) -> None:
        self.asset = asset

    @classmethod
    def latest_for_session(cls, path: Path, session_id: str) -> "VisionAnalysisView | None":
        latest = None
        for asset in load_models(path, VisionAnalysisAsset):
            if asset.session_id == session_id:
                latest = asset
        return cls(latest) if latest is not None else None

    def detector_usable(self, detector: str) -> bool:
        for health in self.asset.detector_health:
            if health.detector == detector:
                if health.status != "ok" or health.accepted_readings <= 0:
                    return False
                readings = [
                    item for item in self.asset.readings if item.detector == detector
                ]
                if detector == "timer":
                    return any(item.payload.get("game_time_text") for item in readings)
                if detector == "kda":
                    return any(
                        item.payload.get("kills") is not None
                        and item.payload.get("deaths") is not None
                        and item.payload.get("assists") is not None
                        for item in readings
                    )
                return bool(readings)
        return False

    def timer_readings(self) -> list[TimerReading]:
        return [
            TimerReading(
                timestamp_seconds=item.at_seconds,
                game_time_text=item.payload.get("game_time_text"),
                confidence=item.confidence,
            )
            for item in self.asset.readings
            if item.detector == "timer" and item.provenance == "coarse"
        ]

    def scene_readings(self) -> list[SceneReading]:
        return [
            SceneReading(
                timestamp_seconds=item.at_seconds,
                scene=item.payload.get("scene", "other"),
                confidence=item.confidence,
            )
            for item in self.asset.readings
            if item.detector == "scene" and item.provenance == "coarse"
        ]

    def events(self, kind: str) -> list[VisionEvent]:
        return sorted(
            (item for item in self.asset.events if item.kind == kind),
            key=lambda item: item.observed_at_seconds,
        )
