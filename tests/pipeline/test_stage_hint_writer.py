from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import Settings, StorageSettings
from arl.segmenter.hints import StageHintWriter
from arl.segmenter.models import MatchStageHint
from arl.shared.contracts import MatchStage
from arl.shared.jsonl_store import load_models


class StageHintWriterTest(unittest.TestCase):
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
        self.hints_path = self.temp_root / "match-stage-hints.jsonl"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_append_with_at_seconds(self) -> None:
        writer = StageHintWriter(self.settings)
        writer.append(
            session_id="session-hint-001",
            stage=MatchStage.IN_GAME,
            at_seconds=95.5,
        )

        hints = load_models(self.hints_path, MatchStageHint)
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0].session_id, "session-hint-001")
        self.assertEqual(hints[0].stage, MatchStage.IN_GAME)
        self.assertEqual(hints[0].at_seconds, 95.5)
        self.assertIsNone(hints[0].detected_at)

    def test_append_with_detected_at(self) -> None:
        writer = StageHintWriter(self.settings)
        detected_at = datetime(2026, 4, 26, 13, 30, tzinfo=timezone.utc)
        writer.append(
            session_id="session-hint-002",
            stage=MatchStage.POST_GAME,
            detected_at=detected_at,
        )

        hints = load_models(self.hints_path, MatchStageHint)
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0].session_id, "session-hint-002")
        self.assertEqual(hints[0].stage, MatchStage.POST_GAME)
        self.assertEqual(hints[0].detected_at, detected_at)
        self.assertIsNone(hints[0].at_seconds)

    def test_append_requires_one_timestamp_source(self) -> None:
        writer = StageHintWriter(self.settings)
        with self.assertRaises(ValueError):
            writer.append(
                session_id="session-hint-003",
                stage=MatchStage.LOADING,
            )


if __name__ == "__main__":
    unittest.main()
