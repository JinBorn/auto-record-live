from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from arl.config import Settings
from arl.vision.models import KdaReading, SceneReading, TimerReading
from arl.vision_analysis.builtin_detectors import (
    KdaVisionDetector,
    MatchResultVisionDetector,
    RespawnVisionDetector,
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
                # Anti-flicker: confirmation waits for range-end coverage, so
                # the sweep must reach the end of the [10, 20] range.
                KdaReading(19.0, 1, 0, 0, 0.9),
            ]
        )
        with patch(
            "arl.vision_analysis.builtin_detectors.read_kda",
            side_effect=lambda *args, **kwargs: next(refined),
        ):
            detector.begin_refinement_range(10.0, 20.0)
            refined_outputs = []
            for at_seconds in (10.0, 15.0, 16.0, 16.1, 16.2, 19.0):
                refined_outputs.append(
                    detector.analyze(object(), at_seconds, provenance="refined")
                )

        events = detector.finalize().events
        self.assertTrue(detector.refinement_range_complete())
        self.assertTrue(all(not output.readings for output in refined_outputs))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].observed_at_seconds, 16.0)
        self.assertEqual(events[0].attributes["current_kills"], 1)

    def test_kda_adapter_rejects_flickered_refinement_run(self) -> None:
        settings = Settings()
        settings.highlights.condensed_kda_frame_refinement_enabled = True
        detector = KdaVisionDetector(settings)
        coarse = iter(
            [
                KdaReading(10.0, 6, 2, 2, 0.9),
                KdaReading(20.0, 8, 2, 2, 0.9),
            ]
        )
        with patch(
            "arl.vision_analysis.builtin_detectors.read_kda",
            side_effect=lambda *args, **kwargs: next(coarse),
        ):
            detector.analyze(object(), 10.0, provenance="coarse")
            change = detector.analyze(object(), 20.0, provenance="coarse")
        self.assertEqual(len(change.refinement_requests), 1)

        # Three consecutive misread "8" frames would have confirmed a false
        # transition under the old early-return rule; the true "6" frames
        # afterwards expose the run as flicker.
        refined = iter(
            [
                KdaReading(10.0, 6, 2, 2, 0.9),
                KdaReading(14.0, 8, 2, 2, 0.9),
                KdaReading(14.4, 8, 2, 2, 0.9),
                KdaReading(14.8, 8, 2, 2, 0.9),
                KdaReading(16.0, 6, 2, 2, 0.9),
                KdaReading(19.0, 6, 2, 2, 0.9),
            ]
        )
        with patch(
            "arl.vision_analysis.builtin_detectors.read_kda",
            side_effect=lambda *args, **kwargs: next(refined),
        ):
            detector.begin_refinement_range(10.0, 20.0)
            for at_seconds in (10.0, 14.0, 14.4, 14.8, 16.0, 19.0):
                detector.analyze(object(), at_seconds, provenance="refined")

        events = detector.finalize().events
        self.assertEqual(events, [])
        self.assertFalse(detector.refinement_range_complete())

    def test_kda_adapter_streaming_confirmation_waits_for_range_end(self) -> None:
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
            detector.analyze(object(), 20.0, provenance="coarse")

        refined = iter(
            [
                KdaReading(10.0, 0, 0, 0, 0.9),
                KdaReading(15.0, 1, 0, 0, 0.9),
                KdaReading(15.4, 1, 0, 0, 0.9),
                KdaReading(15.8, 1, 0, 0, 0.9),
                KdaReading(19.0, 1, 0, 0, 0.9),
            ]
        )
        with patch(
            "arl.vision_analysis.builtin_detectors.read_kda",
            side_effect=lambda *args, **kwargs: next(refined),
        ):
            detector.begin_refinement_range(10.0, 20.0)
            for at_seconds in (10.0, 15.0, 15.4, 15.8):
                detector.analyze(object(), at_seconds, provenance="refined")
            # Stable run exists, but the sweep has not reached the range end:
            # the transition must stay unconfirmed.
            self.assertFalse(detector.refinement_range_complete())
            detector.analyze(object(), 19.0, provenance="refined")

        self.assertTrue(detector.refinement_range_complete())
        events = detector.finalize().events
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].observed_at_seconds, 15.0)

    def test_kda_adapter_finalize_confirms_partial_coverage_run(self) -> None:
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
            detector.analyze(object(), 20.0, provenance="coarse")

        # Refinement cap exhausted mid-range: streaming never confirms, but
        # finalize() accepts the stable non-reverting run on partial coverage.
        refined = iter(
            [
                KdaReading(10.0, 0, 0, 0, 0.9),
                KdaReading(15.0, 1, 0, 0, 0.9),
                KdaReading(15.4, 1, 0, 0, 0.9),
                KdaReading(15.8, 1, 0, 0, 0.9),
            ]
        )
        with patch(
            "arl.vision_analysis.builtin_detectors.read_kda",
            side_effect=lambda *args, **kwargs: next(refined),
        ):
            detector.begin_refinement_range(10.0, 20.0)
            for at_seconds in (10.0, 15.0, 15.4, 15.8):
                detector.analyze(object(), at_seconds, provenance="refined")

        self.assertFalse(detector.refinement_range_complete())
        events = detector.finalize().events
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].observed_at_seconds, 15.0)

    def test_kda_adapter_ignores_counter_regression_without_rebaselining(self) -> None:
        settings = Settings()
        settings.highlights.condensed_kda_frame_refinement_enabled = False
        detector = KdaVisionDetector(settings)
        coarse = iter(
            [
                KdaReading(10.0, 2, 1, 1, 0.9),
                KdaReading(20.0, 1, 0, 1, 0.9),
                KdaReading(30.0, 3, 1, 1, 0.9),
            ]
        )
        with patch(
            "arl.vision_analysis.builtin_detectors.read_kda",
            side_effect=lambda *args, **kwargs: next(coarse),
        ):
            detector.analyze(object(), 10.0, provenance="coarse")
            detector.analyze(object(), 20.0, provenance="coarse")
            detector.analyze(object(), 30.0, provenance="coarse")

        events = detector.finalize().events
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].attributes["previous_kills"], 2)
        self.assertEqual(events[0].attributes["current_kills"], 3)
        self.assertEqual(events[0].attributes["previous_deaths"], 1)
        self.assertEqual(events[0].attributes["current_deaths"], 1)

    def test_kda_adapter_rebaselines_after_stable_zero_new_match_reset(self) -> None:
        settings = Settings()
        settings.highlights.condensed_kda_frame_refinement_enabled = False
        detector = KdaVisionDetector(settings)
        coarse = iter(
            [
                KdaReading(10.0, 5, 2, 3, 0.9),
                KdaReading(20.0, 0, 0, 0, 0.9),
                KdaReading(30.0, 0, 0, 0, 0.9),
                KdaReading(40.0, 0, 0, 0, 0.9),
                KdaReading(50.0, 1, 0, 0, 0.9),
            ]
        )
        with patch(
            "arl.vision_analysis.builtin_detectors.read_kda",
            side_effect=lambda *args, **kwargs: next(coarse),
        ):
            for at_seconds in (10.0, 20.0, 30.0, 40.0, 50.0):
                detector.analyze(object(), at_seconds, provenance="coarse")

        events = detector.finalize().events
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].attributes["previous_kills"], 0)
        self.assertEqual(events[0].attributes["current_kills"], 1)

    def test_respawn_requires_monotonic_multiple_readings(self) -> None:
        settings = Settings()
        detector = RespawnVisionDetector(settings)
        observations = iter([(30, 0.8), (29, 0.8), (28, 0.8)])
        with (
            patch(
                "arl.vision_analysis.builtin_detectors.read_respawn_countdown",
                side_effect=lambda *args, **kwargs: next(observations),
            ),
            patch(
                "arl.vision_analysis.builtin_detectors.looks_like_player_dead",
                side_effect=[True, True, True, False, False, False],
            ),
        ):
            for at_seconds in (100.0, 101.0, 102.0, 103.0, 104.0, 105.0):
                detector.analyze(object(), at_seconds, provenance="refined")

        events = detector.finalize().events
        self.assertTrue(detector.refinement_range_complete())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "death_respawn_state")
        self.assertEqual(events[0].attributes["first_countdown"], 30)
        self.assertEqual(events[0].attributes["proposed_respawn_at"], 103.0)

    def test_respawn_rejects_single_or_increasing_read(self) -> None:
        settings = Settings()
        detector = RespawnVisionDetector(settings)
        observations = iter([(20, 0.8), (25, 0.8)])
        with (
            patch(
                "arl.vision_analysis.builtin_detectors.read_respawn_countdown",
                side_effect=lambda *args, **kwargs: next(observations),
            ),
            patch(
                "arl.vision_analysis.builtin_detectors.looks_like_player_dead",
                return_value=True,
            ),
        ):
            detector.analyze(object(), 100.0, provenance="coarse")
            detector.analyze(object(), 101.0, provenance="coarse")

        self.assertEqual(detector.finalize().events, [])

    def test_respawn_does_not_merge_distant_repeated_digits(self) -> None:
        settings = Settings()
        detector = RespawnVisionDetector(settings)
        observations = iter([(1, 0.8), (1, 0.8), (1, 0.8)])
        with (
            patch(
                "arl.vision_analysis.builtin_detectors.read_respawn_countdown",
                side_effect=lambda *args, **kwargs: next(observations),
            ),
            patch(
                "arl.vision_analysis.builtin_detectors.looks_like_player_dead",
                return_value=True,
            ),
        ):
            for at_seconds in (10.0, 1000.0, 5000.0):
                detector.analyze(object(), at_seconds, provenance="coarse")

        self.assertEqual(detector.finalize().events, [])

    def test_respawn_ignores_digits_when_frame_is_not_death_like(self) -> None:
        detector = RespawnVisionDetector(Settings())
        with (
            patch(
                "arl.vision_analysis.builtin_detectors.looks_like_player_dead",
                return_value=False,
            ),
            patch(
                "arl.vision_analysis.builtin_detectors.read_respawn_countdown"
            ) as read_countdown,
        ):
            output = detector.analyze(object(), 100.0, provenance="coarse")

        read_countdown.assert_not_called()
        self.assertFalse(output.readings[0].payload["death_like"])

    def test_match_result_requires_two_confirming_reads(self) -> None:
        settings = Settings()
        detector = MatchResultVisionDetector(settings)
        results = iter([("victory", 0.9), ("victory", 0.9)])
        with patch(
            "arl.vision_analysis.builtin_detectors.read_match_result",
            side_effect=lambda *args, **kwargs: next(results),
        ):
            first = detector.analyze(object(), 500.0, provenance="coarse")
            detector.analyze(object(), 501.0, provenance="refined")

        self.assertEqual(len(first.refinement_requests), 1)
        events = detector.finalize().events
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].attributes["result"], "victory")

    def test_match_result_rejects_ambiguous_reads(self) -> None:
        settings = Settings()
        detector = MatchResultVisionDetector(settings)
        results = iter([("victory", 0.9), ("defeat", 0.9)])
        with patch(
            "arl.vision_analysis.builtin_detectors.read_match_result",
            side_effect=lambda *args, **kwargs: next(results),
        ):
            detector.analyze(object(), 500.0, provenance="coarse")
            detector.analyze(object(), 501.0, provenance="refined")

        self.assertEqual(detector.finalize().events, [])
