from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import tempfile

import cv2
import numpy as np

from arl.vision.frame_sampler import sample_frames


def test_sample_frames_synthetic_video():
    """Test frame sampling from a synthetic video."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        video_path = tmpdir_path / "test_video.mp4"

        fps = 30.0
        duration_seconds = 60
        width, height = 640, 480

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(video_path), fourcc, fps, (width, height))

        total_frames = int(fps * duration_seconds)
        for i in range(total_frames):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            frame[:, :] = (i % 255, (i * 2) % 255, (i * 3) % 255)
            writer.write(frame)

        writer.release()

        frames = sample_frames(video_path, interval_seconds=20.0)

        assert len(frames) == 3
        assert frames[0][0] == 0.0
        assert frames[1][0] == 20.0
        assert frames[2][0] == 40.0

        for timestamp, frame in frames:
            assert frame.shape == (height, width, 3)


if __name__ == "__main__":
    test_sample_frames_synthetic_video()
    print("All frame sampler tests passed!")
