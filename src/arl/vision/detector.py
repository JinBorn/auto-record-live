from __future__ import annotations

from pathlib import Path

from ..config import VisionSettings
from .frame_sampler import sample_frame_window, sample_frames
from .match_stitcher import stitch_matches, stitch_scene_readings
from .models import MatchSegment, SceneReading, TimerReading
from .scene_classifier import classify_scene
from .timer_ocr import read_timer


class VisionMatchDetector:
    """Orchestrates vision-based match detection."""

    def __init__(self, settings: VisionSettings):
        self.settings = settings

    def detect(self, video_path: Path) -> list[MatchSegment]:
        """Detect match segments from video file.

        Uses a two-pass adaptive strategy:

        1. **Coarse pass** — sample the full video at the configured
           interval (default 20 s), classify scenes, read timers, and
           stitch segments.

        2. **Refinement pass** — for segments still missing a start
           boundary after the coarse pass, re-sample a narrow window
           around the segment start at a finer interval (default 5 s)
           to catch loading screens that are shorter than the coarse
           sample interval.

        Args:
            video_path: Path to raw recording video

        Returns:
            List of MatchSegment with completeness analysis
        """
        # ── Coarse pass ──────────────────────────────────────────
        frames = sample_frames(
            video_path,
            interval_seconds=self.settings.frame_sample_interval_seconds,
        )

        readings: list[TimerReading] = []
        scene_readings: list[SceneReading] = []
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

        segments = stitch_scene_readings(
            scene_readings,
            match_start_threshold_seconds=self.settings.match_start_threshold_seconds,
            min_match_duration_seconds=self.settings.min_match_duration_seconds,
            min_complete_timer_seconds=self.settings.min_complete_timer_seconds,
            timer_readings=readings,
        )
        if not segments:
            segments = stitch_matches(
                readings,
                match_start_threshold_seconds=self.settings.match_start_threshold_seconds,
                lobby_gap_threshold_seconds=self.settings.lobby_gap_threshold_seconds,
            )
            return segments

        # ── Refinement pass ──────────────────────────────────────
        segments = self._refine_segment_starts(video_path, segments)
        segments = self._refine_segment_ends(video_path, segments)

        return segments

    def detect_from_readings(
        self,
        *,
        timer_readings: list[TimerReading],
        scene_readings: list[SceneReading],
    ) -> list[MatchSegment]:
        """Build match segments from durable shared-analysis evidence.

        Local adaptive refinement remains owned by ``detect`` during rollout;
        callers fall back to that path when persisted coarse evidence does not
        yield complete segments.
        """
        segments = stitch_scene_readings(
            scene_readings,
            match_start_threshold_seconds=self.settings.match_start_threshold_seconds,
            min_match_duration_seconds=self.settings.min_match_duration_seconds,
            min_complete_timer_seconds=self.settings.min_complete_timer_seconds,
            timer_readings=timer_readings,
        )
        if segments:
            return segments
        return stitch_matches(
            timer_readings,
            match_start_threshold_seconds=self.settings.match_start_threshold_seconds,
            lobby_gap_threshold_seconds=self.settings.lobby_gap_threshold_seconds,
        )

    # ── refinement ───────────────────────────────────────────────

    def _refine_segment_starts(
        self,
        video_path: Path,
        segments: list[MatchSegment],
    ) -> list[MatchSegment]:
        """Re-sample start regions of incomplete segments at fine interval.

        Loading screens between games are typically 30–120 s.  A 20 s
        coarse interval can land on either side of the loading screen and
        miss it entirely.  This pass re-samples a narrow window around
        each segment that is still ``incomplete_no_start``, looking for
        "loading" frames the coarse pass skipped.
        """
        refine_interval = self.settings.match_start_refine_interval_seconds
        lookback = self.settings.match_start_refine_lookback_seconds

        for seg in segments:
            if seg.is_complete or seg.reason != "incomplete_no_start":
                continue

            # Define the search window: look back *lookback* seconds
            # before the current segment start and up to the first
            # in-game frame timestamp (or 60 s forward, whichever).
            window_start = max(0.0, seg.start_seconds - lookback)
            window_end = seg.start_seconds + 60.0
            if window_start >= window_end:
                continue

            fine_frames = sample_frame_window(
                video_path,
                window_start,
                window_end,
                interval_seconds=refine_interval,
            )
            if not fine_frames:
                continue

            # Classify the fine-grained frames.
            fine_scenes: list[SceneReading] = []
            fine_timers: list[TimerReading] = []
            for ts, frame in fine_frames:
                fine_scenes.append(classify_scene(frame, ts))
                fine_timers.append(
                    read_timer(
                        frame,
                        ts,
                        crop_region=self.settings.timer_crop_region,
                        detector=self.settings.timer_ocr_detector,
                    )
                )

            # Find the last "loading" frame that precedes an "in_game"
            # frame whose timer shows an early-game value.  This
            # confirms it's a real loading screen, not a death overlay.
            found_loading_at = self._find_real_loading(
                fine_scenes,
                fine_timers,
                seg.start_seconds,
            )
            if found_loading_at is None:
                continue

            # Update the segment: shift start back to the loading
            # frame and mark it complete (incomplete_no_start implies
            # a natural end was already detected).
            seg.start_seconds = found_loading_at
            seg.is_complete = True
            seg.confidence = min(0.95, seg.confidence + 0.35)
            seg.reason = "complete"

        return segments

    def _refine_segment_ends(
        self,
        video_path: Path,
        segments: list[MatchSegment],
    ) -> list[MatchSegment]:
        """Re-sample complete segment tails to avoid post-game client bleed."""
        refine_interval = self.settings.match_start_refine_interval_seconds
        lookback = min(90.0, self.settings.match_start_refine_lookback_seconds)

        for seg in segments:
            if not seg.is_complete:
                continue
            window_start = max(seg.start_seconds, seg.end_seconds - lookback)
            window_end = seg.end_seconds
            if window_start >= window_end:
                continue

            fine_frames = sample_frame_window(
                video_path,
                window_start,
                window_end,
                interval_seconds=refine_interval,
            )
            if not fine_frames:
                continue

            fine_scenes = [classify_scene(frame, ts) for ts, frame in fine_frames]
            refined_end = self._find_trailing_non_game_start(
                fine_scenes,
                current_end=seg.end_seconds,
            )
            if refined_end is None or refined_end >= seg.end_seconds:
                continue
            refined_end = min(seg.end_seconds, refined_end + refine_interval)
            if refined_end - seg.start_seconds < self.settings.min_match_duration_seconds:
                continue
            seg.end_seconds = refined_end

        return segments

    @staticmethod
    def _find_real_loading(
        scenes: list[SceneReading],
        timers: list[TimerReading],
        current_start: float,
    ) -> float | None:
        """Find the last "loading" frame that is a real game start.

        A loading frame is considered *real* when the first in-game frame
        that follows it (and precedes *current_start*) carries a low
        game-time value (≤ 180 s).

        Returns the timestamp of the loading frame, or *None*.
        """
        from .match_stitcher import _parse_timer

        # Build sorted lists keyed by timestamp.
        timer_by_ts: dict[float, float] = {}
        for tr in timers:
            if tr.game_time_text:
                gt = _parse_timer(tr.game_time_text)
                if gt > 0:
                    timer_by_ts[tr.timestamp_seconds] = gt

        sorted_scenes = sorted(scenes, key=lambda s: s.timestamp_seconds)
        loading_candidates: list[float] = [
            s.timestamp_seconds for s in sorted_scenes if s.scene == "loading"
        ]
        if not loading_candidates:
            return None

        # For each candidate, check the first in_game frame after it
        # (but still before the current segment start).
        for ld_ts in reversed(loading_candidates):
            # Find the next in_game frame after this loading.
            next_in_game_ts: float | None = None
            for s in sorted_scenes:
                if s.scene == "in_game" and s.timestamp_seconds > ld_ts:
                    next_in_game_ts = s.timestamp_seconds
                    break
            if next_in_game_ts is None:
                continue
            if next_in_game_ts >= current_start:
                # This loading is too close to / after the existing start.
                continue

            # Validate with timer: is the game-time at next_in_game low?
            game_time = timer_by_ts.get(next_in_game_ts)
            if game_time is not None and game_time <= 180.0:
                return ld_ts

        return None

    @staticmethod
    def _find_trailing_non_game_start(
        scenes: list[SceneReading],
        *,
        current_end: float,
    ) -> float | None:
        sorted_scenes = [
            scene
            for scene in sorted(scenes, key=lambda item: item.timestamp_seconds)
            if scene.timestamp_seconds <= current_end + 0.001
        ]
        if not sorted_scenes:
            return None

        last_in_game_index: int | None = None
        for index, scene in enumerate(sorted_scenes):
            if scene.scene == "in_game":
                last_in_game_index = index
        if last_in_game_index is None:
            return None

        for scene in sorted_scenes[last_in_game_index + 1 :]:
            if scene.scene != "in_game":
                return scene.timestamp_seconds
        return None
