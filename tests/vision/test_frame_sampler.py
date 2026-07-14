from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import tempfile

import cv2
import numpy as np

from arl.vision.frame_sampler import (
    iter_every_frame_window,
    iter_frame_window,
    sample_frame_window,
    sample_frames,
)


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


def test_sample_frame_window_bounded():
    """Test windowed sampling returns only frames within the requested range."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        video_path = tmpdir_path / "test_video.mp4"

        fps = 30.0
        duration_seconds = 120
        width, height = 640, 480

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(video_path), fourcc, fps, (width, height))
        for i in range(int(fps * duration_seconds)):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            frame[:, :] = (i % 255, (i * 2) % 255, (i * 3) % 255)
            writer.write(frame)
        writer.release()

        # Request a 20 s window inside the video.
        frames = sample_frame_window(
            video_path,
            start_seconds=50.0,
            end_seconds=70.0,
            interval_seconds=5.0,
        )

        assert len(frames) == 5  # 50, 55, 60, 65, 70
        for ts, _ in frames:
            assert 50.0 <= ts <= 70.0, f"timestamp {ts} outside window"


def test_sample_frame_window_clamped():
    """Test windowed sampling clamps to video bounds."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        video_path = tmpdir_path / "test_video.mp4"

        fps = 30.0
        duration_seconds = 10
        width, height = 640, 480

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(video_path), fourcc, fps, (width, height))
        for i in range(int(fps * duration_seconds)):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            writer.write(frame)
        writer.release()

        # Window entirely before video start → empty.
        frames = sample_frame_window(
            video_path,
            start_seconds=-30.0,
            end_seconds=-10.0,
            interval_seconds=5.0,
        )
        assert len(frames) == 0

        # Window extending past video end → clamped.
        frames = sample_frame_window(
            video_path,
            start_seconds=5.0,
            end_seconds=30.0,
            interval_seconds=5.0,
        )
        for ts, _ in frames:
            assert ts <= 10.0


def test_streaming_samplers_are_lazy_and_bounded():
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / "stream.mp4"
        fps = 10.0
        writer = cv2.VideoWriter(
            str(video_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (160, 90),
        )
        for index in range(30):
            writer.write(np.full((90, 160, 3), index, dtype=np.uint8))
        writer.release()

        coarse = iter_frame_window(
            video_path,
            start_seconds=0.5,
            end_seconds=2.5,
            interval_seconds=1.0,
        )
        assert not isinstance(coarse, list)
        assert [round(timestamp, 1) for timestamp, _ in coarse] == [0.5, 1.5, 2.5]

        refined = iter_every_frame_window(video_path, 1.0, 1.2)
        assert not isinstance(refined, list)
        timestamps = [round(timestamp, 1) for timestamp, _ in refined]
        assert timestamps == [1.0, 1.1, 1.2]


if __name__ == "__main__":
    test_sample_frames_synthetic_video()
    test_sample_frame_window_bounded()
    test_sample_frame_window_clamped()
    print("All frame sampler tests passed!")
