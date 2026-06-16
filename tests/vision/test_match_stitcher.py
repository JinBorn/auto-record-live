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


if __name__ == "__main__":
    test_complete_match()
    test_incomplete_no_start()
    test_incomplete_no_end()
    test_multiple_matches()
    test_scene_stitching_splits_multi_game_recording()
    print("All tests passed!")
