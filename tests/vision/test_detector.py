from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import tempfile

import cv2
import numpy as np

from arl.config import VisionSettings
from arl.vision.detector import VisionMatchDetector
from arl.vision.models import SceneReading, TimerReading


def test_vision_match_detector_integration():
    """Test end-to-end vision match detection."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        video_path = tmpdir_path / "test_video.mp4"

        fps = 30.0
        duration_seconds = 100
        width, height = 1920, 1080

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(video_path), fourcc, fps, (width, height))

        total_frames = int(fps * duration_seconds)
        for i in range(total_frames):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            writer.write(frame)

        writer.release()

        settings = VisionSettings(
            match_detection_enabled=True,
            frame_sample_interval_seconds=20.0,
            timer_ocr_detector="template",
        )

        detector = VisionMatchDetector(settings)
        segments = detector.detect(video_path)

        assert isinstance(segments, list)
        print(f"Detected {len(segments)} segments")


def test_find_real_loading_detects_valid_start():
    """Loading frame with early-game timer after it → real start."""
    scenes = [
        SceneReading(100.0, "other", 0.7),
        SceneReading(140.0, "loading", 0.9),
        SceneReading(155.0, "in_game", 0.9),
    ]
    timers = [
        TimerReading(155.0, "00:45", 0.9),  # Early game → valid
    ]

    result = VisionMatchDetector._find_real_loading(scenes, timers, 200.0)

    assert result == 140.0, f"Expected 140.0, got {result}"


def test_find_real_loading_rejects_mid_game():
    """Loading frame followed by mid-game timer → death screen, not real."""
    scenes = [
        SceneReading(100.0, "in_game", 0.9),
        SceneReading(120.0, "loading", 0.7),  # Death screen
        SceneReading(140.0, "in_game", 0.9),
    ]
    timers = [
        TimerReading(140.0, "11:30", 0.9),  # Mid-game → invalid
    ]

    result = VisionMatchDetector._find_real_loading(scenes, timers, 400.0)

    assert result is None, f"Expected None, got {result}"


def test_find_real_loading_skips_after_segment_start():
    """Loading frame after the current segment start → not relevant."""
    scenes = [
        SceneReading(500.0, "loading", 0.9),
        SceneReading(520.0, "in_game", 0.9),
    ]
    timers = [
        TimerReading(520.0, "00:30", 0.9),
    ]

    # Segment already starts at 490 → the loading at 500 is after it.
    result = VisionMatchDetector._find_real_loading(scenes, timers, 490.0)

    assert result is None, f"Expected None, got {result}"


def test_find_trailing_non_game_start_uses_first_tail_non_game_scene():
    scenes = [
        SceneReading(4000.0, "in_game", 0.9),
        SceneReading(4045.0, "in_game", 0.9),
        SceneReading(4050.0, "other", 0.7),
        SceneReading(4055.0, "loading", 0.9),
        SceneReading(4060.0, "other", 0.7),
    ]

    result = VisionMatchDetector._find_trailing_non_game_start(
        scenes,
        current_end=4060.0,
    )

    assert result == 4050.0


def test_find_trailing_non_game_start_ignores_middle_non_game_gaps():
    scenes = [
        SceneReading(100.0, "in_game", 0.9),
        SceneReading(105.0, "other", 0.7),
        SceneReading(110.0, "in_game", 0.9),
        SceneReading(115.0, "in_game", 0.9),
    ]

    result = VisionMatchDetector._find_trailing_non_game_start(
        scenes,
        current_end=115.0,
    )

    assert result is None


if __name__ == "__main__":
    test_vision_match_detector_integration()
    test_find_real_loading_detects_valid_start()
    test_find_real_loading_rejects_mid_game()
    test_find_real_loading_skips_after_segment_start()
    test_find_trailing_non_game_start_uses_first_tail_non_game_scene()
    test_find_trailing_non_game_start_ignores_middle_non_game_gaps()
    print("Vision detector integration test passed!")
