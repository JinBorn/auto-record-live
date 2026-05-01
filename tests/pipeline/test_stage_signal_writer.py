from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import Settings, StorageSettings
from arl.segmenter.models import MatchStageSignal
from arl.segmenter.signals import StageSignalWriter
from arl.shared.jsonl_store import load_models


class StageSignalWriterTest(unittest.TestCase):
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
        self.signals_path = self.temp_root / "match-stage-signals.jsonl"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_append_with_at_seconds(self) -> None:
        writer = StageSignalWriter(self.settings)
        writer.append(
            session_id="session-signal-001",
            text="champion select started",
            at_seconds=32.0,
        )

        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].session_id, "session-signal-001")
        self.assertEqual(signals[0].text, "champion select started")
        self.assertEqual(signals[0].source, "manual")
        self.assertEqual(signals[0].at_seconds, 32.0)
        self.assertIsNone(signals[0].detected_at)

    def test_append_with_detected_at(self) -> None:
        writer = StageSignalWriter(self.settings)
        detected_at = datetime(2026, 4, 26, 23, 10, tzinfo=timezone.utc)
        writer.append(
            session_id="session-signal-002",
            text="game over victory",
            source="ocr",
            detected_at=detected_at,
        )

        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].session_id, "session-signal-002")
        self.assertEqual(signals[0].source, "ocr")
        self.assertEqual(signals[0].detected_at, detected_at)
        self.assertIsNone(signals[0].at_seconds)

    def test_append_requires_one_timestamp_source(self) -> None:
        writer = StageSignalWriter(self.settings)
        with self.assertRaises(ValueError):
            writer.append(
                session_id="session-signal-003",
                text="loading screen",
            )


if __name__ == "__main__":
    unittest.main()
