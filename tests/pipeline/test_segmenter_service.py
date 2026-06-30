from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from arl.config import Settings, StorageSettings
from arl.segmenter.models import MatchStageHint
from arl.segmenter.service import SegmenterService
from arl.shared.contracts import MatchStage, RecordingAsset, SourceType
from arl.shared.jsonl_store import append_model
from arl.vision.models import MatchSegment


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


class SegmenterServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.temp_root = root / "tmp"
        self.settings = Settings(
            storage=StorageSettings(
                raw_dir=root / "raw",
                processed_dir=root / "processed",
                export_dir=root / "exports",
                temp_dir=self.temp_root,
            )
        )
        self.recording_assets_path = self.temp_root / "recording-assets.jsonl"
        self.match_stage_hints_path = self.temp_root / "match-stage-hints.jsonl"
        self.boundaries_path = self.temp_root / "match-boundaries.jsonl"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_segmenter_builds_multi_match_boundaries_from_in_game_hints(self) -> None:
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id="session-segment-001",
                source_type=SourceType.BROWSER_CAPTURE,
                path="/tmp/segment-001.mp4",
                started_at=datetime(2026, 4, 26, 10, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 26, 11, 0, tzinfo=timezone.utc),
            ),
        )
        append_model(
            self.match_stage_hints_path,
            MatchStageHint(
                session_id="session-segment-001",
                stage=MatchStage.CHAMPION_SELECT,
                at_seconds=30.0,
            ),
        )
        append_model(
            self.match_stage_hints_path,
            MatchStageHint(
                session_id="session-segment-001",
                stage=MatchStage.IN_GAME,
                at_seconds=120.0,
            ),
        )
        append_model(
            self.match_stage_hints_path,
            MatchStageHint(
                session_id="session-segment-001",
                stage=MatchStage.IN_GAME,
                at_seconds=1580.0,
            ),
        )
        append_model(
            self.match_stage_hints_path,
            MatchStageHint(
                session_id="session-segment-001",
                stage=MatchStage.IN_GAME,
                at_seconds=4600.0,
            ),
        )

        service = SegmenterService(self.settings)
        service.run()

        boundaries = _read_jsonl(self.boundaries_path)
        self.assertEqual(len(boundaries), 2)
        self.assertEqual(boundaries[0]["match_index"], 1)
        self.assertEqual(boundaries[0]["started_at_seconds"], 120.0)
        self.assertEqual(boundaries[0]["ended_at_seconds"], 1580.0)
        self.assertEqual(boundaries[0]["confidence"], 0.8)
        self.assertEqual(boundaries[1]["match_index"], 2)
        self.assertEqual(boundaries[1]["started_at_seconds"], 1580.0)
        self.assertEqual(boundaries[1]["ended_at_seconds"], 3600.0)
        self.assertEqual(boundaries[1]["confidence"], 0.8)

        service.run()
        boundaries = _read_jsonl(self.boundaries_path)
        self.assertEqual(len(boundaries), 2)

    def test_segmenter_uses_detected_at_when_at_seconds_missing(self) -> None:
        started_at = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id="session-segment-002",
                source_type=SourceType.BROWSER_CAPTURE,
                path="/tmp/segment-002.mp4",
                started_at=started_at,
                ended_at=datetime(2026, 4, 26, 12, 40, tzinfo=timezone.utc),
            ),
        )
        append_model(
            self.match_stage_hints_path,
            MatchStageHint(
                session_id="session-segment-002",
                stage=MatchStage.IN_GAME,
                detected_at=datetime(2026, 4, 26, 12, 3, tzinfo=timezone.utc),
            ),
        )
        append_model(
            self.match_stage_hints_path,
            MatchStageHint(
                session_id="session-segment-002",
                stage=MatchStage.IN_GAME,
                detected_at=datetime(2026, 4, 26, 12, 22, tzinfo=timezone.utc),
            ),
        )

        SegmenterService(self.settings).run()
        boundaries = _read_jsonl(self.boundaries_path)

        self.assertEqual(len(boundaries), 2)
        self.assertEqual(boundaries[0]["started_at_seconds"], 180.0)
        self.assertEqual(boundaries[0]["ended_at_seconds"], 1320.0)
        self.assertEqual(boundaries[1]["started_at_seconds"], 1320.0)
        self.assertEqual(boundaries[1]["ended_at_seconds"], 2400.0)

    def test_segmenter_force_reprocess_replaces_existing_boundaries(self) -> None:
        session_id = "session-segment-force"
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.BROWSER_CAPTURE,
                path="/tmp/segment-force.mp4",
                started_at=datetime(2026, 6, 9, 6, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 6, 9, 7, 0, tzinfo=timezone.utc),
            ),
        )
        append_model(
            self.match_stage_hints_path,
            MatchStageHint(
                session_id=session_id,
                stage=MatchStage.IN_GAME,
                at_seconds=0.0,
            ),
        )

        service = SegmenterService(self.settings)
        service.run(session_ids={session_id})
        boundaries = _read_jsonl(self.boundaries_path)
        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0]["ended_at_seconds"], 3600.0)

        append_model(
            self.match_stage_hints_path,
            MatchStageHint(
                session_id=session_id,
                stage=MatchStage.POST_GAME,
                at_seconds=1200.0,
            ),
        )
        service.run(session_ids={session_id})
        boundaries = _read_jsonl(self.boundaries_path)
        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0]["ended_at_seconds"], 3600.0)

        service.run(session_ids={session_id}, force_reprocess=True)
        boundaries = _read_jsonl(self.boundaries_path)
        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0]["ended_at_seconds"], 1200.0)

    def test_segmenter_ends_match_at_post_game_before_next_in_game(self) -> None:
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id="session-segment-post-game",
                source_type=SourceType.BROWSER_CAPTURE,
                path="/tmp/segment-post-game.mp4",
                started_at=datetime(2026, 6, 9, 6, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 6, 9, 7, 0, tzinfo=timezone.utc),
            ),
        )
        for stage, at_seconds in [
            (MatchStage.IN_GAME, 0.0),
            (MatchStage.POST_GAME, 1314.0),
            (MatchStage.IN_GAME, 1800.0),
        ]:
            append_model(
                self.match_stage_hints_path,
                MatchStageHint(
                    session_id="session-segment-post-game",
                    stage=stage,
                    at_seconds=at_seconds,
                ),
            )

        SegmenterService(self.settings).run()
        boundaries = _read_jsonl(self.boundaries_path)

        self.assertEqual(len(boundaries), 2)
        self.assertEqual(boundaries[0]["started_at_seconds"], 0.0)
        self.assertEqual(boundaries[0]["ended_at_seconds"], 1314.0)
        self.assertEqual(boundaries[1]["started_at_seconds"], 1800.0)
        self.assertEqual(boundaries[1]["ended_at_seconds"], 3600.0)

    def test_segmenter_applies_post_game_to_matching_in_game_window(self) -> None:
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id="session-segment-two-post-game",
                source_type=SourceType.BROWSER_CAPTURE,
                path="/tmp/segment-two-post-game.mp4",
                started_at=datetime(2026, 6, 9, 8, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 6, 9, 9, 30, tzinfo=timezone.utc),
            ),
        )
        for stage, at_seconds in [
            (MatchStage.IN_GAME, 60.0),
            (MatchStage.POST_GAME, 1500.0),
            (MatchStage.IN_GAME, 2100.0),
            (MatchStage.POST_GAME, 4200.0),
        ]:
            append_model(
                self.match_stage_hints_path,
                MatchStageHint(
                    session_id="session-segment-two-post-game",
                    stage=stage,
                    at_seconds=at_seconds,
                ),
            )

        SegmenterService(self.settings).run()
        boundaries = _read_jsonl(self.boundaries_path)

        self.assertEqual(len(boundaries), 2)
        self.assertEqual(boundaries[0]["started_at_seconds"], 60.0)
        self.assertEqual(boundaries[0]["ended_at_seconds"], 1500.0)
        self.assertEqual(boundaries[1]["started_at_seconds"], 2100.0)
        self.assertEqual(boundaries[1]["ended_at_seconds"], 4200.0)

    def test_segmenter_ignores_out_of_range_post_game_hints(self) -> None:
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id="session-segment-post-game-out-of-range",
                source_type=SourceType.BROWSER_CAPTURE,
                path="/tmp/segment-post-game-out-of-range.mp4",
                started_at=datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 6, 9, 11, 0, tzinfo=timezone.utc),
            ),
        )
        for stage, at_seconds in [
            (MatchStage.POST_GAME, 0.0),
            (MatchStage.POST_GAME, 100.0),
            (MatchStage.IN_GAME, 120.0),
            (MatchStage.IN_GAME, 1800.0),
            (MatchStage.POST_GAME, 3700.0),
        ]:
            append_model(
                self.match_stage_hints_path,
                MatchStageHint(
                    session_id="session-segment-post-game-out-of-range",
                    stage=stage,
                    at_seconds=at_seconds,
                ),
            )

        SegmenterService(self.settings).run()
        boundaries = _read_jsonl(self.boundaries_path)

        self.assertEqual(len(boundaries), 2)
        self.assertEqual(boundaries[0]["started_at_seconds"], 120.0)
        self.assertEqual(boundaries[0]["ended_at_seconds"], 1800.0)
        self.assertEqual(boundaries[1]["started_at_seconds"], 1800.0)
        self.assertEqual(boundaries[1]["ended_at_seconds"], 3600.0)

    def test_segmenter_falls_back_to_single_boundary_without_hints(self) -> None:
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id="session-segment-003",
                source_type=SourceType.BROWSER_CAPTURE,
                path="/tmp/segment-003.mp4",
                started_at=datetime(2026, 4, 26, 15, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 26, 15, 35, tzinfo=timezone.utc),
            ),
        )

        SegmenterService(self.settings).run()
        boundaries = _read_jsonl(self.boundaries_path)

        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0]["match_index"], 1)
        self.assertEqual(boundaries[0]["started_at_seconds"], 0.0)
        self.assertEqual(boundaries[0]["ended_at_seconds"], 2100.0)
        self.assertEqual(boundaries[0]["confidence"], 0.5)

    def test_segmenter_filters_by_session_ids(self) -> None:
        for session_id in ["session-segment-filter-a", "session-segment-filter-b"]:
            append_model(
                self.recording_assets_path,
                RecordingAsset(
                    session_id=session_id,
                    source_type=SourceType.BROWSER_CAPTURE,
                    path=f"/tmp/{session_id}.mp4",
                    started_at=datetime(2026, 4, 26, 15, 0, tzinfo=timezone.utc),
                    ended_at=datetime(2026, 4, 26, 15, 10, tzinfo=timezone.utc),
                ),
            )

        SegmenterService(self.settings).run(session_ids={"session-segment-filter-b"})
        boundaries = _read_jsonl(self.boundaries_path)

        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0]["session_id"], "session-segment-filter-b")

    def test_segmenter_preserves_sub_minute_recording_duration(self) -> None:
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id="session-segment-004",
                source_type=SourceType.BROWSER_CAPTURE,
                path="/tmp/segment-004.mp4",
                started_at=datetime(2026, 4, 26, 15, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 26, 15, 0, 30, tzinfo=timezone.utc),
            ),
        )

        SegmenterService(self.settings).run()
        boundaries = _read_jsonl(self.boundaries_path)

        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0]["started_at_seconds"], 0.0)
        self.assertEqual(boundaries[0]["ended_at_seconds"], 30.0)

    def test_segmenter_persists_vision_completeness_metadata(self) -> None:
        recording_path = self.temp_root / "recording-source.mp4"
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_text("fake media", encoding="utf-8")
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id="session-vision-completeness",
                source_type=SourceType.DIRECT_STREAM,
                path=str(recording_path),
                started_at=datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 6, 10, 12, 30, tzinfo=timezone.utc),
            ),
        )

        detector_segments = [
            MatchSegment(
                start_seconds=20.0,
                end_seconds=300.0,
                timer_trace=[],
                is_complete=False,
                confidence=0.4,
                reason="incomplete_no_end",
            ),
            MatchSegment(
                start_seconds=420.0,
                end_seconds=1500.0,
                timer_trace=[],
                is_complete=True,
                confidence=0.95,
                reason="complete",
            ),
        ]
        with patch("arl.vision.VisionMatchDetector") as detector_cls:
            detector_cls.return_value.detect.return_value = detector_segments
            SegmenterService(self.settings).run()

        boundaries = _read_jsonl(self.boundaries_path)
        self.assertEqual(len(boundaries), 2)
        self.assertFalse(boundaries[0]["is_complete"])
        self.assertEqual(boundaries[0]["reason"], "incomplete_no_end")
        self.assertTrue(boundaries[1]["is_complete"])
        self.assertEqual(boundaries[1]["reason"], "complete")


if __name__ == "__main__":
    unittest.main()
