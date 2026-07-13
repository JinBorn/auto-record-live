from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from arl.config import Settings
from arl.vision.models import KdaReading, SceneReading, TimerReading
from arl.vision_analysis.builtin_detectors import (
    KdaVisionDetector,
    SceneVisionDetector,
    TimerVisionDetector,
)


class BuiltinDetectorTests(TestCase):
    def test_timer_and_scene_adapters_preserve_readings(self) -> None:
        settings = Settings()
        with (
            patch(
                "arl.vision_analysis.builtin_detectors.read_timer",
                return_value=TimerReading(20.0, "03:15", 0.9),
            ),
            patch(
                "arl.vision_analysis.builtin_detectors.classify_scene",
                return_value=SceneReading(20.0, "in_game", 0.8),
            ),
        ):
            timer = TimerVisionDetector(settings).analyze(object(), 20.0, provenance="coarse")
            scene = SceneVisionDetector(settings).analyze(object(), 20.0, provenance="coarse")

        self.assertEqual(timer.readings[0].payload["game_time_text"], "03:15")
        self.assertEqual(scene.readings[0].payload["scene"], "in_game")

    def test_kda_adapter_refines_to_first_stable_target_frame(self) -> None:
        settings = Settings()
        settings.highlights.condensed_kda_frame_refinement_enabled = True
        detector = KdaVisionDetector(settings)
        coarse = iter(
            [
                KdaReading(10.0, 0, 0, 0, 0.9),
                KdaReading(20.0, 1, 0, 0, 0.9),
            ]
        )
        with patch(
            "arl.vision_analysis.builtin_detectors.read_kda",
            side_effect=lambda *args, **kwargs: next(coarse),
        ):
            detector.analyze(object(), 10.0, provenance="coarse")
            change = detector.analyze(object(), 20.0, provenance="coarse")
        self.assertEqual(len(change.refinement_requests), 1)

        refined = iter(
            [
                KdaReading(10.0, 0, 0, 0, 0.9),
                KdaReading(15.0, 0, 0, 0, 0.9),
                KdaReading(16.0, 1, 0, 0, 0.9),
                KdaReading(16.1, 1, 0, 0, 0.9),
                KdaReading(16.2, 1, 0, 0, 0.9),
            ]
        )
        with patch(
            "arl.vision_analysis.builtin_detectors.read_kda",
            side_effect=lambda *args, **kwargs: next(refined),
        ):
            for at_seconds in (10.0, 15.0, 16.0, 16.1, 16.2):
                detector.analyze(object(), at_seconds, provenance="refined")

        events = detector.finalize().events
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].observed_at_seconds, 16.0)
        self.assertEqual(events[0].attributes["current_kills"], 1)
