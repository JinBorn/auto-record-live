from __future__ import annotations

import hashlib
from typing import Any

from arl.config import Settings
from arl.vision.kda_ocr import read_kda
from arl.vision.scene_classifier import classify_scene
from arl.vision.timer_ocr import read_timer

from .detectors import DetectorOutput, RefinementRequest
from .models import VisionEvent, VisionReading
from .new_signal_ocr import (
    looks_like_player_dead,
    read_match_result,
    read_respawn_countdown,
)


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
    version = "3"
    refinement_interval_seconds = 0.0

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
        self._zero_reset_streak: list[tuple[float, int, int, int, str]] = []
        self._confirmed_transition_at: dict[tuple[str, str], float] = {}
        self._active_refinement_keys: set[tuple[str, str]] | None = None

    def reset(self) -> None:
        self._coarse_values.clear()
        self._refined_values.clear()
        self._transitions.clear()
        self._zero_reset_streak.clear()
        self._confirmed_transition_at.clear()
        self._active_refinement_keys = None

    def begin_refinement_range(self, start_seconds: float, end_seconds: float) -> None:
        self._active_refinement_keys = {
            (previous[4], current[4])
            for previous, current in self._transitions
            if previous[0] < end_seconds
            and current[0] > start_seconds
            and (previous[4], current[4]) not in self._confirmed_transition_at
        }

    def refinement_range_complete(self) -> bool:
        return self._active_refinement_keys == set()

    def analyze(self, frame: Any, at_seconds: float, *, provenance: str) -> DetectorOutput:
        if provenance == "refined" and self.refinement_range_complete():
            return DetectorOutput()
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
            if provenance == "refined":
                self._refined_values.append(value)
                if self._active_refinement_keys is not None:
                    for previous, current in self._transitions:
                        key = (previous[4], current[4])
                        if key not in self._active_refinement_keys:
                            continue
                        refined = self._stable_refined_timestamp(previous, current)
                        if refined is not None:
                            self._confirmed_transition_at[key] = refined
                            self._active_refinement_keys.remove(key)
            elif not self._coarse_values:
                self._coarse_values.append(value)
            else:
                previous = self._coarse_values[-1]
                action = self._coarse_value_action(previous, value)
                if action == "ignore" and value[1:4] == (0, 0, 0):
                    if (
                        self._zero_reset_streak
                        and value[0] - self._zero_reset_streak[-1][0]
                        > self.settings.highlights.condensed_kda_max_reading_gap_seconds
                    ):
                        self._zero_reset_streak.clear()
                    self._zero_reset_streak.append(value)
                    if len(self._zero_reset_streak) >= 3:
                        self._coarse_values.append(value)
                        self._zero_reset_streak.clear()
                else:
                    self._zero_reset_streak.clear()
                if action == "event":
                    self._transitions.append((previous, value))
                    if self.settings.highlights.condensed_kda_frame_refinement_enabled:
                        requests.append(
                            RefinementRequest(
                                detector=self.name,
                                started_at_seconds=previous[0],
                                ended_at_seconds=value[0],
                            )
                        )
                    if (
                        value[2] > previous[2]
                        and self.settings.vision_analysis.death_respawn_enabled
                    ):
                        requests.append(
                            RefinementRequest(
                                detector="respawn",
                                started_at_seconds=value[0],
                                ended_at_seconds=value[0] + 120.0,
                            )
                        )
                if action != "ignore":
                    self._coarse_values.append(value)
        return DetectorOutput(
            readings=[] if provenance == "refined" else [result],
            refinement_requests=requests,
        )

    def finalize(self) -> DetectorOutput:
        events: list[VisionEvent] = []
        for previous, current in self._transitions:
            observed_at = current[0]
            if self.settings.highlights.condensed_kda_frame_refinement_enabled:
                refined = self._confirmed_transition_at.get(
                    (previous[4], current[4])
                ) or self._stable_refined_timestamp(previous, current)
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

    def _coarse_value_action(
        self,
        previous: tuple[float, int, int, int, str],
        current: tuple[float, int, int, int, str],
    ) -> str:
        gap = current[0] - previous[0]
        deltas = tuple(current[index] - previous[index] for index in range(1, 4))
        if gap <= 0 or any(delta < 0 for delta in deltas):
            return "ignore"
        if (
            gap > self.settings.highlights.condensed_kda_max_reading_gap_seconds
            or deltas[0] + deltas[1]
            > self.settings.highlights.condensed_kda_max_event_delta
        ):
            return "baseline"
        if deltas[0] + deltas[1] > 0:
            return "event"
        return "baseline"

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


class RespawnVisionDetector:
    name = "respawn"
    version = "2"
    refinement_interval_seconds = 0.45

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.coarse_interval_seconds = (
            settings.vision_analysis.respawn_coarse_interval_seconds
        )
        self._observations: list[tuple[float, int, str]] = []
        self._death_states: list[tuple[float, bool, str]] = []
        self._last_refined_check: float | None = None

    def reset(self) -> None:
        self._observations.clear()
        self._death_states.clear()
        self._last_refined_check = None

    def begin_refinement_range(self, start_seconds: float, end_seconds: float) -> None:
        return None

    def refinement_range_complete(self) -> bool:
        death_start = self._first_stable_state(True, start_index=0)
        if death_start is None:
            return False
        return self._first_stable_state(
            False,
            start_index=death_start[0] + 3,
        ) is not None

    def analyze(self, frame: Any, at_seconds: float, *, provenance: str) -> DetectorOutput:
        if (
            provenance == "refined"
            and self._last_refined_check is not None
            and at_seconds - self._last_refined_check < 0.45
        ):
            return DetectorOutput()
        if provenance == "refined":
            self._last_refined_check = at_seconds
        try:
            death_like = looks_like_player_dead(frame)
        except (TypeError, ValueError):
            death_like = False
        if not death_like:
            reading = VisionReading(
                reading_id=_reading_id(self.name, at_seconds, provenance),
                detector=self.name,
                at_seconds=at_seconds,
                confidence=0.8 if provenance == "refined" else 0.0,
                payload={"seconds_remaining": None, "death_like": False},
                provenance=provenance,
            )
            if provenance == "refined":
                self._death_states.append((at_seconds, False, reading.reading_id))
            return DetectorOutput(readings=[reading])
        seconds, confidence = read_respawn_countdown(
            frame,
            self.settings.vision_analysis.respawn_crop_region,
        )
        reading = VisionReading(
            reading_id=_reading_id(self.name, at_seconds, provenance),
            detector=self.name,
            at_seconds=at_seconds,
            confidence=confidence,
            payload={"seconds_remaining": seconds, "death_like": True},
            provenance=provenance,
        )
        if seconds is not None and confidence >= 0.7:
            self._observations.append((at_seconds, seconds, reading.reading_id))
        if provenance == "refined":
            self._death_states.append((at_seconds, True, reading.reading_id))
        return DetectorOutput(readings=[reading])

    def finalize(self) -> DetectorOutput:
        sequences: list[list[tuple[float, int, str]]] = []
        for observation in sorted(self._observations):
            if not sequences:
                sequences.append([observation])
                continue
            previous = sequences[-1][-1]
            elapsed = observation[0] - previous[0]
            countdown_drop = previous[1] - observation[1]
            if (
                0.0 < elapsed <= 12.0
                and countdown_drop >= 0
                and abs(countdown_drop - elapsed) <= 4.0
            ):
                sequences[-1].append(observation)
            else:
                sequences.append([observation])
        accepted = max(sequences, key=len, default=[])
        if (
            len(accepted) < 3
            or len({item[1] for item in accepted}) < 3
            or accepted[0][1] - accepted[-1][1] < 2
        ):
            accepted = []

        death_start = self._first_stable_state(True, start_index=0)
        if death_start is None:
            return DetectorOutput()
        respawn = self._first_stable_state(False, start_index=death_start[0] + 3)
        if respawn is None:
            return DetectorOutput()
        start_state = self._death_states[death_start[0]]
        end_state = self._death_states[respawn[0]]
        attributes = {
            "proposed_respawn_at": end_state[0],
            "state_source": "death_screen_transition",
        }
        evidence_ids = [item[2] for item in self._death_states[death_start[0] : respawn[0] + 3]]
        if accepted:
            attributes.update(
                {
                    "first_countdown": accepted[0][1],
                    "last_countdown": accepted[-1][1],
                }
            )
            evidence_ids.extend(item[2] for item in accepted)
        return DetectorOutput(
            events=[
                VisionEvent(
                    event_id=hashlib.sha256(
                        f"death_state:{start_state[2]}:{end_state[2]}".encode()
                    ).hexdigest()[:24],
                    kind="death_respawn_state",
                    started_at_seconds=start_state[0],
                    ended_at_seconds=end_state[0],
                    observed_at_seconds=start_state[0],
                    confidence=0.9,
                    evidence_reading_ids=list(dict.fromkeys(evidence_ids)),
                    attributes=attributes,
                )
            ]
        )

    def _first_stable_state(
        self,
        value: bool,
        *,
        start_index: int,
    ) -> tuple[int, float] | None:
        consecutive = 0
        first_index = 0
        for index in range(start_index, len(self._death_states)):
            if self._death_states[index][1] == value:
                if consecutive == 0:
                    first_index = index
                consecutive += 1
                if consecutive >= 3:
                    return first_index, self._death_states[first_index][0]
            else:
                consecutive = 0
        return None


class MatchResultVisionDetector:
    name = "match_result"
    version = "2"
    refinement_interval_seconds = 0.5

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.coarse_interval_seconds = (
            settings.vision_analysis.match_result_coarse_interval_seconds
        )
        self._observations: list[tuple[float, str, str]] = []

    def reset(self) -> None:
        self._observations.clear()

    def analyze(self, frame: Any, at_seconds: float, *, provenance: str) -> DetectorOutput:
        result, confidence = read_match_result(
            frame,
            self.settings.vision_analysis.match_result_crop_region,
        )
        reading = VisionReading(
            reading_id=_reading_id(self.name, at_seconds, provenance),
            detector=self.name,
            at_seconds=at_seconds,
            confidence=confidence,
            payload={"result": result},
            provenance=provenance,
        )
        requests = []
        if result is not None and confidence >= 0.8:
            self._observations.append((at_seconds, result, reading.reading_id))
            if provenance == "coarse":
                requests.append(
                    RefinementRequest(
                        detector=self.name,
                        started_at_seconds=max(0.0, at_seconds - 3.0),
                        ended_at_seconds=at_seconds + 3.0,
                    )
                )
        return DetectorOutput(readings=[reading], refinement_requests=requests)

    def finalize(self) -> DetectorOutput:
        observations = sorted(self._observations)
        for index, first in enumerate(observations):
            matching = [
                item
                for item in observations[index:]
                if item[1] == first[1] and item[0] - first[0] <= 3.0
            ]
            if len(matching) < 2:
                continue
            return DetectorOutput(
                events=[
                    VisionEvent(
                        event_id=hashlib.sha256(
                            f"match_result:{first[1]}:{first[2]}".encode()
                        ).hexdigest()[:24],
                        kind="match_result",
                        started_at_seconds=first[0],
                        ended_at_seconds=matching[-1][0],
                        observed_at_seconds=first[0],
                        confidence=0.9,
                        evidence_reading_ids=[item[2] for item in matching],
                        attributes={"result": first[1]},
                    )
                ]
            )
        return DetectorOutput()


def build_builtin_detectors(settings: Settings) -> list[object]:
    detectors: list[object] = [TimerVisionDetector(settings), SceneVisionDetector(settings)]
    if settings.highlights.condensed_kda_event_detection_enabled:
        detectors.append(KdaVisionDetector(settings))
    if settings.vision_analysis.death_respawn_enabled:
        detectors.append(RespawnVisionDetector(settings))
    if settings.vision_analysis.match_result_enabled:
        detectors.append(MatchResultVisionDetector(settings))
    return detectors
