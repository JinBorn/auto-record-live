from __future__ import annotations

import hashlib
from typing import Any

from arl.config import Settings
from arl.vision.kda_ocr import read_kda
from arl.vision.scene_classifier import classify_scene
from arl.vision.timer_ocr import read_timer

from .detectors import DetectorOutput, RefinementRequest
from .models import VisionEvent, VisionReading


def _reading_id(detector: str, at_seconds: float, provenance: str) -> str:
    raw = f"{detector}:{at_seconds:.6f}:{provenance}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class TimerVisionDetector:
    name = "timer"
    version = "1"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.coarse_interval_seconds = settings.vision.frame_sample_interval_seconds

    def analyze(self, frame: Any, at_seconds: float, *, provenance: str) -> DetectorOutput:
        reading = read_timer(
            frame,
            at_seconds,
            crop_region=self.settings.vision.timer_crop_region,
            detector=self.settings.vision.timer_ocr_detector,
        )
        return DetectorOutput(
            readings=[
                VisionReading(
                    reading_id=_reading_id(self.name, at_seconds, provenance),
                    detector=self.name,
                    at_seconds=at_seconds,
                    confidence=reading.confidence,
                    payload={"game_time_text": reading.game_time_text},
                    provenance=provenance,
                )
            ]
        )


class SceneVisionDetector:
    name = "scene"
    version = "1"

    def __init__(self, settings: Settings) -> None:
        self.coarse_interval_seconds = settings.vision.frame_sample_interval_seconds

    def analyze(self, frame: Any, at_seconds: float, *, provenance: str) -> DetectorOutput:
        reading = classify_scene(frame, at_seconds)
        return DetectorOutput(
            readings=[
                VisionReading(
                    reading_id=_reading_id(self.name, at_seconds, provenance),
                    detector=self.name,
                    at_seconds=at_seconds,
                    confidence=reading.confidence,
                    payload={"scene": reading.scene},
                    provenance=provenance,
                )
            ]
        )


class KdaVisionDetector:
    name = "kda"
    version = "1"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.coarse_interval_seconds = (
            settings.highlights.condensed_kda_sample_interval_seconds
        )
        self._coarse_values: list[tuple[float, int, int, int, str]] = []
        self._refined_values: list[tuple[float, int, int, int, str]] = []
        self._transitions: list[
            tuple[tuple[float, int, int, int, str], tuple[float, int, int, int, str]]
        ] = []

    def reset(self) -> None:
        self._coarse_values.clear()
        self._refined_values.clear()
        self._transitions.clear()

    def analyze(self, frame: Any, at_seconds: float, *, provenance: str) -> DetectorOutput:
        reading = read_kda(
            frame,
            at_seconds,
            crop_region=self.settings.highlights.condensed_kda_crop_region,
        )
        result = VisionReading(
            reading_id=_reading_id(self.name, at_seconds, provenance),
            detector=self.name,
            at_seconds=at_seconds,
            confidence=reading.confidence,
            payload={
                "kills": reading.kills,
                "deaths": reading.deaths,
                "assists": reading.assists,
            },
            provenance=provenance,
        )
        value = self._usable_value(result)
        requests: list[RefinementRequest] = []
        if value is not None:
            target = self._refined_values if provenance == "refined" else self._coarse_values
            if provenance == "coarse" and target:
                previous = target[-1]
                if self._valid_transition(previous, value):
                    self._transitions.append((previous, value))
                    if self.settings.highlights.condensed_kda_frame_refinement_enabled:
                        requests.append(
                            RefinementRequest(
                                detector=self.name,
                                started_at_seconds=previous[0],
                                ended_at_seconds=value[0],
                            )
                        )
            target.append(value)
        return DetectorOutput(readings=[result], refinement_requests=requests)

    def finalize(self) -> DetectorOutput:
        events: list[VisionEvent] = []
        for previous, current in self._transitions:
            observed_at = current[0]
            if self.settings.highlights.condensed_kda_frame_refinement_enabled:
                refined = self._stable_refined_timestamp(previous, current)
                if refined is None:
                    continue
                observed_at = refined
            previous_ts, previous_kills, previous_deaths, previous_assists, previous_id = previous
            _, current_kills, current_deaths, current_assists, current_id = current
            events.append(
                VisionEvent(
                    event_id=hashlib.sha256(
                        f"kda_change:{previous_id}:{current_id}:{observed_at:.6f}".encode()
                    ).hexdigest()[:24],
                    kind="kda_change",
                    started_at_seconds=previous_ts,
                    ended_at_seconds=observed_at,
                    observed_at_seconds=observed_at,
                    confidence=0.9,
                    evidence_reading_ids=[previous_id, current_id],
                    attributes={
                        "previous_kills": previous_kills,
                        "current_kills": current_kills,
                        "previous_deaths": previous_deaths,
                        "current_deaths": current_deaths,
                        "previous_assists": previous_assists,
                        "current_assists": current_assists,
                    },
                )
            )
        return DetectorOutput(events=events)

    def _usable_value(
        self, reading: VisionReading
    ) -> tuple[float, int, int, int, str] | None:
        payload = reading.payload
        values = (payload.get("kills"), payload.get("deaths"), payload.get("assists"))
        if (
            None in values
            or reading.confidence < self.settings.highlights.condensed_kda_min_confidence
        ):
            return None
        return (
            reading.at_seconds,
            int(values[0]),
            int(values[1]),
            int(values[2]),
            reading.reading_id,
        )

    def _valid_transition(
        self,
        previous: tuple[float, int, int, int, str],
        current: tuple[float, int, int, int, str],
    ) -> bool:
        gap = current[0] - previous[0]
        deltas = tuple(current[index] - previous[index] for index in range(1, 4))
        return (
            gap > 0
            and gap <= self.settings.highlights.condensed_kda_max_reading_gap_seconds
            and all(delta >= 0 for delta in deltas)
            and deltas[0] + deltas[1] > 0
            and deltas[0] + deltas[1]
            <= self.settings.highlights.condensed_kda_max_event_delta
        )

    def _stable_refined_timestamp(
        self,
        previous: tuple[float, int, int, int, str],
        current: tuple[float, int, int, int, str],
    ) -> float | None:
        baseline = previous[1:4]
        target = current[1:4]
        values = sorted(
            (item for item in self._refined_values if previous[0] <= item[0] <= current[0]),
            key=lambda item: item[0],
        )
        saw_baseline = False
        consecutive = 0
        first_target_at: float | None = None
        for item in values:
            value = item[1:4]
            if value == baseline:
                saw_baseline = True
                consecutive = 0
                first_target_at = None
            elif value == target and saw_baseline:
                if consecutive == 0:
                    first_target_at = item[0]
                consecutive += 1
                if consecutive >= 3:
                    return first_target_at
            else:
                consecutive = 0
                first_target_at = None
        return None


def build_builtin_detectors(settings: Settings) -> list[object]:
    detectors: list[object] = [TimerVisionDetector(settings), SceneVisionDetector(settings)]
    if settings.highlights.condensed_kda_event_detection_enabled:
        detectors.append(KdaVisionDetector(settings))
    return detectors
