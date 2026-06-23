from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def sample_frames(
    video_path: Path,
    interval_seconds: float = 20.0,
    output_dir: Path | None = None,
) -> list[tuple[float, np.ndarray]]:
    """Extract frames at regular intervals.

    Args:
        video_path: Path to video file
        interval_seconds: Time interval between samples
        output_dir: Optional directory to write debug PNGs

    Returns:
        List of (timestamp_seconds, bgr_frame) tuples
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_seconds = total_frames / fps if fps > 0 else 0

    result = _sample_range(cap, fps, 0.0, duration_seconds, interval_seconds, output_dir)
    cap.release()
    return result


def sample_frame_window(
    video_path: Path,
    start_seconds: float,
    end_seconds: float,
    *,
    interval_seconds: float = 5.0,
    output_dir: Path | None = None,
) -> list[tuple[float, np.ndarray]]:
    """Extract frames within a specific time window.

    Used for adaptive refinement: when the coarse pass misses a match-start
    boundary (loading screen shorter than the coarse interval), this
    re-samples a narrow window at a finer interval to catch it.

    Args:
        video_path: Path to video file
        start_seconds: Window start time in the recording
        end_seconds: Window end time in the recording
        interval_seconds: Time interval between samples (default finer: 5 s)
        output_dir: Optional directory to write debug PNGs

    Returns:
        List of (timestamp_seconds, bgr_frame) tuples within the window
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    video_duration = total_frames / fps if fps > 0 else 0

    clamped_start = max(0.0, start_seconds)
    clamped_end = min(video_duration, end_seconds)
    if clamped_start >= clamped_end:
        cap.release()
        return []

    result = _sample_range(cap, fps, clamped_start, clamped_end, interval_seconds, output_dir)
    cap.release()
    return result


def _sample_range(
    cap: cv2.VideoCapture,
    fps: float,
    range_start: float,
    range_end: float,
    interval_seconds: float,
    output_dir: Path | None = None,
) -> list[tuple[float, np.ndarray]]:
    """Sample frames from *range_start* to *range_end* at *interval_seconds*."""
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    frames: list[tuple[float, np.ndarray]] = []
    timestamp = range_start

    while timestamp <= range_end:
        frame_index = int(timestamp * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)

        ret, frame = cap.read()
        if not ret:
            break

        frames.append((timestamp, frame))

        if output_dir is not None:
            output_path = output_dir / f"frame_{int(timestamp):06d}.png"
            cv2.imwrite(str(output_path), frame)

        timestamp += interval_seconds

    return frames
