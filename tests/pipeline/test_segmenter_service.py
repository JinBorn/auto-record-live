from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import Settings, StorageSettings
from arl.segmenter.models import MatchStageHint
from arl.segmenter.service import SegmenterService
from arl.shared.contracts import MatchStage, RecordingAsset, SourceType
from arl.shared.jsonl_store import append_model


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


if __name__ == "__main__":
    unittest.main()
