from __future__ import annotations

from pathlib import Path

from ..config import VisionSettings
from .frame_sampler import sample_frames
from .match_stitcher import stitch_matches, stitch_scene_readings
from .models import MatchSegment
from .scene_classifier import classify_scene
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

        readings = []
        scene_readings = []
        for timestamp, frame in frames:
            scene_readings.append(classify_scene(frame, timestamp))
            readings.append(
                read_timer(
                    frame,
                    timestamp,
                    crop_region=self.settings.timer_crop_region,
                    detector=self.settings.timer_ocr_detector,
                )
            )

        scene_segments = stitch_scene_readings(
            scene_readings,
            match_start_threshold_seconds=self.settings.match_start_threshold_seconds,
        )
        if scene_segments:
            return scene_segments

        segments = stitch_matches(
            readings,
            match_start_threshold_seconds=self.settings.match_start_threshold_seconds,
            lobby_gap_threshold_seconds=self.settings.lobby_gap_threshold_seconds,
        )

        return segments
