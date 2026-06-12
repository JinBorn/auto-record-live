from __future__ import annotations

from pathlib import Path

from ..config import VisionSettings
from .frame_sampler import sample_frames
from .match_stitcher import stitch_matches
from .models import MatchSegment
from .timer_ocr import read_timer


class VisionMatchDetector:
    """Orchestrates vision-based match detection."""

    def __init__(self, settings: VisionSettings):
        self.settings = settings

    def detect(self, video_path: Path) -> list[MatchSegment]:
        """Detect match segments from video file.

        Args:
            video_path: Path to raw recording video

        Returns:
            List of MatchSegment with completeness analysis
        """
        frames = sample_frames(
            video_path,
            interval_seconds=self.settings.frame_sample_interval_seconds,
        )

        readings = [
            read_timer(
                frame,
                timestamp,
                crop_region=self.settings.timer_crop_region,
                detector=self.settings.timer_ocr_detector,
            )
            for timestamp, frame in frames
        ]

        segments = stitch_matches(
            readings,
            match_start_threshold_seconds=self.settings.match_start_threshold_seconds,
            lobby_gap_threshold_seconds=self.settings.lobby_gap_threshold_seconds,
        )

        return segments
