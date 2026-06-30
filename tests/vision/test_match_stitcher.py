from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from arl.vision.match_stitcher import stitch_matches, stitch_scene_readings
from arl.vision.models import SceneReading, TimerReading


def test_complete_match():
    """Test detection of a complete match with start and end."""
    readings = [
        TimerReading(0.0, None, 0.0),
        TimerReading(20.0, "00:34", 0.9),
        TimerReading(40.0, "05:12", 0.9),
        TimerReading(60.0, "10:45", 0.9),
        TimerReading(80.0, "15:23", 0.9),
        TimerReading(100.0, "20:11", 0.9),
        TimerReading(120.0, None, 0.0),
        TimerReading(140.0, None, 0.0),
    ]

    segments = stitch_matches(readings)

    assert len(segments) == 1
    assert segments[0].is_complete is True
    assert segments[0].confidence == 0.95
    assert segments[0].reason == "complete"
    assert segments[0].start_seconds == 20.0
    assert segments[0].end_seconds == 100.0


def test_incomplete_no_start():
    """Test detection of incomplete match (recording joined mid-game)."""
    readings = [
        TimerReading(0.0, "23:45", 0.9),
        TimerReading(20.0, "28:12", 0.9),
        TimerReading(40.0, "32:50", 0.9),
        TimerReading(60.0, None, 0.0),
    ]

    segments = stitch_matches(readings)

    assert len(segments) == 1
    assert segments[0].is_complete is False
    assert segments[0].confidence == 0.3
    assert segments[0].reason == "incomplete_no_start"


def test_incomplete_no_end():
    """Test detection of incomplete match (recording ends mid-game)."""
    readings = [
        TimerReading(0.0, None, 0.0),
        TimerReading(20.0, "01:23", 0.9),
        TimerReading(40.0, "06:45", 0.9),
        TimerReading(60.0, "15:00", 0.9),
    ]

    segments = stitch_matches(readings)

    assert len(segments) == 1
    assert segments[0].is_complete is False
    assert segments[0].confidence == 0.4
    assert segments[0].reason == "incomplete_no_end"


def test_multiple_matches():
    """Test detection of multiple matches in one recording."""
    readings = [
        TimerReading(0.0, "15:30", 0.9),
        TimerReading(20.0, "20:12", 0.9),
        TimerReading(40.0, None, 0.0),
        TimerReading(60.0, None, 0.0),
        TimerReading(80.0, "00:45", 0.9),
        TimerReading(100.0, "05:20", 0.9),
        TimerReading(120.0, "10:15", 0.9),
        TimerReading(140.0, None, 0.0),
        TimerReading(160.0, None, 0.0),
        TimerReading(180.0, "00:30", 0.9),
        TimerReading(200.0, "03:15", 0.9),
    ]

    segments = stitch_matches(readings)

    assert len(segments) == 3
    assert segments[0].is_complete is False
    assert segments[1].is_complete is True
    assert segments[2].is_complete is False


def test_scene_stitching_splits_multi_game_recording():
    """Test scene-based splitting for multi-game recordings with trailing partial."""
    readings = [
        SceneReading(0.0, "loading", 0.9),
        SceneReading(20.0, "in_game", 0.9),
        SceneReading(1800.0, "in_game", 0.9),
        SceneReading(1860.0, "other", 0.7),
        SceneReading(2040.0, "other", 0.7),
        SceneReading(2220.0, "loading", 0.9),
        SceneReading(2280.0, "in_game", 0.9),
        SceneReading(3640.0, "in_game", 0.9),
        SceneReading(3660.0, "other", 0.7),
        SceneReading(3840.0, "other", 0.7),
        SceneReading(4020.0, "in_game", 0.9),
        SceneReading(4140.0, "in_game", 0.9),
    ]

    segments = stitch_scene_readings(readings)

    assert len(segments) == 3
    assert segments[0].is_complete is True
    assert segments[0].start_seconds == 0.0
    assert segments[0].end_seconds == 1860.0
    assert segments[1].is_complete is True
    assert segments[1].start_seconds == 2220.0
    assert segments[1].end_seconds == 3660.0
    assert segments[2].is_complete is False
    assert segments[2].reason == "incomplete_no_end"


def test_scene_stitching_merges_classifier_fragments_without_loading():
    """Test short classifier gaps can still be stitched within one match."""
    readings = [
        SceneReading(0.0, "loading", 0.9),
        SceneReading(20.0, "in_game", 0.9),
        SceneReading(80.0, "other", 0.7),
        SceneReading(420.0, "in_game", 0.9),
        SceneReading(470.0, "other", 0.7),
        SceneReading(820.0, "in_game", 0.9),
        SceneReading(900.0, "in_game", 0.9),
    ]

    segments = stitch_scene_readings(readings)

    assert len(segments) == 1
    assert segments[0].start_seconds == 0.0
    assert segments[0].end_seconds == 900.0
    assert segments[0].reason == "incomplete_no_end"


def test_scene_stitching_rejects_short_complete_match_by_default():
    """A very short loading-to-end span is not a publishable complete game."""
    readings = [
        SceneReading(60.0, "loading", 0.9),
        SceneReading(80.0, "in_game", 0.9),
        SceneReading(360.0, "in_game", 0.9),
        SceneReading(400.0, "other", 0.7),
    ]

    segments = stitch_scene_readings(readings)

    assert len(segments) == 1
    assert segments[0].is_complete is False
    assert segments[0].reason == "incomplete_too_short"


def test_scene_stitching_skips_abrupt_loading_as_death_screen():
    """Loading frame right after in_game without other frames → death screen.

    A "loading" frame that appears ≤ 90 s after the last in_game frame,
    with no intervening "other" frames, is treated as a death/respawn
    misclassification and does NOT split the match.
    """
    readings = [
        SceneReading(0.0, "loading", 0.9),
        SceneReading(20.0, "in_game", 0.9),
        SceneReading(40.0, "in_game", 0.9),
        SceneReading(60.0, "in_game", 0.9),
        # Death screen misclassified as "loading" — gap is just 20 s.
        SceneReading(80.0, "loading", 0.7),
        SceneReading(100.0, "in_game", 0.9),
        SceneReading(120.0, "in_game", 0.9),
        SceneReading(2000.0, "in_game", 0.9),
        SceneReading(2040.0, "other", 0.7),
    ]

    segments = stitch_scene_readings(readings)

    # Should be ONE match (not split at T=80).
    assert len(segments) == 1, f"Expected 1 segment, got {len(segments)}"
    assert segments[0].start_seconds == 0.0
    assert segments[0].end_seconds == 2040.0


def test_scene_stitching_uses_abrupt_loading_as_end_when_game_does_not_resume():
    readings = [
        SceneReading(0.0, "loading", 0.9),
        SceneReading(20.0, "in_game", 0.9),
        SceneReading(2000.0, "in_game", 0.9),
        SceneReading(2020.0, "loading", 0.9),
        SceneReading(2040.0, "other", 0.7),
        SceneReading(2060.0, "other", 0.7),
    ]

    segments = stitch_scene_readings(readings)

    assert len(segments) == 1
    assert segments[0].is_complete is True
    assert segments[0].start_seconds == 0.0
    assert segments[0].end_seconds == 2020.0


def test_timer_validation_upgrades_missed_loading_start():
    """When loading is missed but timer shows early game → upgrade to complete."""
    scene_readings = [
        SceneReading(1800.0, "in_game", 0.9),  # Game 2 start, no loading detected
        SceneReading(1860.0, "in_game", 0.9),
        SceneReading(3660.0, "in_game", 0.9),
        SceneReading(3700.0, "other", 0.7),
    ]
    timer_readings = [
        TimerReading(1800.0, "00:45", 0.9),  # Early game timer → valid start
        TimerReading(1860.0, "01:30", 0.9),
        TimerReading(3660.0, "24:10", 0.9),
    ]

    segments = stitch_scene_readings(
        scene_readings,
        match_start_threshold_seconds=120.0,
        min_match_duration_seconds=360.0,
        timer_readings=timer_readings,
    )

    assert len(segments) == 1
    assert segments[0].is_complete is True
    assert segments[0].reason == "complete"


def test_timer_validation_downgrades_death_screen_false_start():
    """When loading is falsely detected but timer shows mid-game → downgrade."""
    scene_readings = [
        SceneReading(0.0, "loading", 0.9),
        SceneReading(20.0, "in_game", 0.9),
        SceneReading(500.0, "in_game", 0.9),
        SceneReading(520.0, "loading", 0.7),  # Death screen (gap > 90s from in_game
        # but let the guard pass — use "other" frames to bypass the abrupt-loading
        # guard; we want to test the timer-downgrade path specifically).
        SceneReading(540.0, "in_game", 0.9),
        SceneReading(1800.0, "in_game", 0.9),
        SceneReading(1820.0, "other", 0.7),
    ]
    timer_readings = [
        TimerReading(20.0, "00:25", 0.9),
        TimerReading(540.0, "08:15", 0.9),  # Mid-game time → false start
        TimerReading(1800.0, "22:30", 0.9),
    ]

    segments = stitch_scene_readings(
        scene_readings,
        match_start_threshold_seconds=120.0,
        min_match_duration_seconds=360.0,
        timer_readings=timer_readings,
    )

    # The T=520 loading frame is more than 90 s from the last in_game at T=500,
    # so the abrupt-loading guard won't catch it.  But timer validation should
    # downgrade the second segment (which starts at T=520) because the timer
    # at T=540 reads 08:15 (> 120 s threshold).
    for seg in segments:
        if abs(seg.start_seconds - 520.0) < 1.0:
            assert seg.is_complete is False, (
                f"Segment starting at {seg.start_seconds:.0f}s should be downgraded, "
                f"got is_complete={seg.is_complete} reason={seg.reason}"
            )
            assert seg.reason == "incomplete_no_start"


def test_timer_validation_downgrades_complete_segment_that_ends_too_early():
    """A scene span that cuts away before end-game is not a complete match."""
    scene_readings = [
        SceneReading(0.0, "loading", 0.9),
        SceneReading(20.0, "in_game", 0.9),
        SceneReading(720.0, "in_game", 0.9),
        SceneReading(760.0, "other", 0.7),
    ]
    timer_readings = [
        TimerReading(20.0, "00:32", 0.9),
        TimerReading(720.0, "12:12", 0.9),
    ]

    segments = stitch_scene_readings(
        scene_readings,
        match_start_threshold_seconds=120.0,
        min_match_duration_seconds=360.0,
        min_complete_timer_seconds=900.0,
        timer_readings=timer_readings,
    )

    assert len(segments) == 1
    assert segments[0].is_complete is False
    assert segments[0].reason == "incomplete_timer_too_early"


if __name__ == "__main__":
    test_complete_match()
    test_incomplete_no_start()
    test_incomplete_no_end()
    test_multiple_matches()
    test_scene_stitching_splits_multi_game_recording()
    test_scene_stitching_merges_classifier_fragments_without_loading()
    test_scene_stitching_rejects_short_complete_match_by_default()
    test_scene_stitching_skips_abrupt_loading_as_death_screen()
    test_timer_validation_upgrades_missed_loading_start()
    test_timer_validation_downgrades_death_screen_false_start()
    test_timer_validation_downgrades_complete_segment_that_ends_too_early()
    print("All tests passed!")
