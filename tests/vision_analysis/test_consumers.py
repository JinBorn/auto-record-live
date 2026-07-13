from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from arl.config import Settings
from arl.highlights.service import HighlightPlannerService
from arl.segmenter.service import SegmenterService
from arl.shared.contracts import MatchBoundary, RecordingAsset, SourceType
from arl.shared.jsonl_store import append_model
from arl.vision.models import MatchSegment
from arl.vision_analysis.models import (
    VisionAnalysisAsset,
    VisionAnalysisMetrics,
    VisionDetectorHealth,
    VisionEvent,
    VisionReading,
)


class VisionAnalysisConsumerTests(TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = Settings()
        self.settings.storage.temp_dir = self.root / "tmp"
        self.video = self.root / "recording.mp4"
        self.video.write_bytes(b"video")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _append_asset(
        self,
        *,
        session_id: str,
        readings: list[VisionReading],
        events: list[VisionEvent],
        detectors: list[str],
    ) -> None:
        append_model(
            self.settings.storage.temp_dir / "vision-analysis-assets.jsonl",
            VisionAnalysisAsset(
                session_id=session_id,
                recording_path=str(self.video),
                source_duration_seconds=900.0,
                input_fingerprint="input",
                config_fingerprint="config",
                schema_version=1,
                layout_profile="lol_zh_1080p_v1",
                status="ok",
                detector_health=[
                    VisionDetectorHealth(
                        detector=name,
                        status="ok",
                        invocations=1,
                        accepted_readings=max(
                            1, sum(item.detector == name for item in readings)
                        ),
                    )
                    for name in detectors
                ],
                readings=readings,
                events=events,
                metrics=VisionAnalysisMetrics(),
                created_at=datetime.now(timezone.utc),
            ),
        )

    def test_highlight_planner_maps_persisted_kda_event_without_scanning(self) -> None:
        session_id = "session-kda-asset"
        self._append_asset(
            session_id=session_id,
            readings=[
                VisionReading(
                    reading_id="kda-1",
                    detector="kda",
                    at_seconds=90.0,
                    confidence=0.9,
                    payload={"kills": 0, "deaths": 0, "assists": 0},
                )
            ],
            events=[
                VisionEvent(
                    event_id="event-1",
                    kind="kda_change",
                    started_at_seconds=90.0,
                    ended_at_seconds=100.0,
                    observed_at_seconds=100.0,
                    confidence=0.9,
                    evidence_reading_ids=["kda-1"],
                    attributes={
                        "previous_kills": 0,
                        "current_kills": 1,
                        "previous_deaths": 0,
                        "current_deaths": 0,
                        "previous_assists": 0,
                        "current_assists": 0,
                    },
                )
            ],
            detectors=["kda"],
        )
        boundary = MatchBoundary(
            session_id=session_id,
            match_index=1,
            started_at_seconds=0.0,
            ended_at_seconds=900.0,
            confidence=0.9,
        )
        recording = RecordingAsset(
            session_id=session_id,
            source_type=SourceType.DIRECT_STREAM,
            path=str(self.video),
            started_at=datetime.now(timezone.utc),
        )
        with patch.object(
            HighlightPlannerService,
            "_sample_kda_frames",
            side_effect=AssertionError("legacy scan should not run"),
        ):
            cues = HighlightPlannerService(self.settings)._detect_kda_event_cues(
                recording=recording,
                boundary=boundary,
                duration=900.0,
            )

        self.assertEqual(len(cues), 1)
        self.assertIn("kills=0->1", cues[0].text)
        self.assertIn("current_at=100.000", cues[0].text)

    def test_segmenter_prefers_complete_shared_timer_scene_evidence(self) -> None:
        session_id = "session-timer-asset"
        self._append_asset(
            session_id=session_id,
            readings=[
                VisionReading(
                    reading_id="timer-1",
                    detector="timer",
                    at_seconds=20.0,
                    confidence=0.9,
                    payload={"game_time_text": "00:30"},
                ),
                VisionReading(
                    reading_id="scene-1",
                    detector="scene",
                    at_seconds=20.0,
                    confidence=0.9,
                    payload={"scene": "in_game"},
                ),
            ],
            events=[],
            detectors=["timer", "scene"],
        )
        segment = MatchSegment(
            start_seconds=0.0,
            end_seconds=900.0,
            timer_trace=[],
            is_complete=True,
            confidence=0.95,
            reason="complete",
        )
        service = SegmenterService(self.settings)
        recording = RecordingAsset(
            session_id=session_id,
            source_type=SourceType.DIRECT_STREAM,
            path=str(self.video),
            started_at=datetime.now(timezone.utc),
        )
        with patch("arl.vision.VisionMatchDetector") as detector_cls:
            detector_cls.return_value.detect_from_readings.return_value = [segment]
            boundaries = service._detect_matches_visually(recording, 900.0)

        detector_cls.return_value.detect.assert_not_called()
        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0].reason, "complete")
