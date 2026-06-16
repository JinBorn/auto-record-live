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

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    frames: list[tuple[float, np.ndarray]] = []
    timestamp = 0.0

    while timestamp <= duration_seconds:
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

    cap.release()
    return frames
