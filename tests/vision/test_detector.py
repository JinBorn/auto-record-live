from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import tempfile

import cv2
import numpy as np

from arl.config import VisionSettings
from arl.vision.detector import VisionMatchDetector


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


if __name__ == "__main__":
    test_vision_match_detector_integration()
    print("Vision detector integration test passed!")
