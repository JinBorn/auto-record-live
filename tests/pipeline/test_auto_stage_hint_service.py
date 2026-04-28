from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import RecordingSettings, Settings, StorageSettings
from arl.segmenter.auto_hints import AutoStageHintService
from arl.segmenter.models import MatchStageHint
from arl.shared.contracts import MatchStage, RecordingAsset, SourceType
from arl.shared.jsonl_store import append_model, load_models


class AutoStageHintServiceTest(unittest.TestCase):
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
            ),
            recording=RecordingSettings(segment_minutes=20),
        )
        self.recording_assets_path = self.temp_root / "recording-assets.jsonl"
        self.match_stage_hints_path = self.temp_root / "match-stage-hints.jsonl"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _append_asset(
        self,
        session_id: str,
        started_at: datetime,
        ended_at: datetime | None,
    ) -> None:
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.BROWSER_CAPTURE,
                path=f"/tmp/{session_id}.mp4",
                started_at=started_at,
                ended_at=ended_at,
            ),
        )

    def _load_hints(self, session_id: str) -> list[MatchStageHint]:
        return [
            hint
            for hint in load_models(self.match_stage_hints_path, MatchStageHint)
            if hint.session_id == session_id
        ]

    def test_auto_stage_hints_emits_interval_based_in_game_hints(self) -> None:
        self._append_asset(
            "session-auto-001",
            datetime(2026, 4, 26, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 10, 55, tzinfo=timezone.utc),
        )

        AutoStageHintService(self.settings).run()

        hints = self._load_hints("session-auto-001")
        self.assertEqual([hint.stage for hint in hints], [MatchStage.IN_GAME] * 3)
        self.assertEqual([hint.at_seconds for hint in hints], [0.0, 1200.0, 2400.0])

    def test_auto_stage_hints_is_idempotent_on_repeated_runs(self) -> None:
        self._append_asset(
            "session-auto-002",
            datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 12, 40, tzinfo=timezone.utc),
        )
        service = AutoStageHintService(self.settings)
        service.run()
        service.run()

        hints = self._load_hints("session-auto-002")
        self.assertEqual([hint.at_seconds for hint in hints], [0.0, 1200.0])

    def test_auto_stage_hints_skips_session_with_existing_in_game_hint(self) -> None:
        self._append_asset(
            "session-auto-003",
            datetime(2026, 4, 26, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 15, 0, tzinfo=timezone.utc),
        )
        append_model(
            self.match_stage_hints_path,
            MatchStageHint(
                session_id="session-auto-003",
                stage=MatchStage.IN_GAME,
                at_seconds=150.0,
            ),
        )

        AutoStageHintService(self.settings).run()
        hints = self._load_hints("session-auto-003")

        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0].at_seconds, 150.0)

    def test_auto_stage_hints_ignores_non_in_game_existing_hints_for_seeding(self) -> None:
        self._append_asset(
            "session-auto-004",
            datetime(2026, 4, 26, 16, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 16, 31, tzinfo=timezone.utc),
        )
        append_model(
            self.match_stage_hints_path,
            MatchStageHint(
                session_id="session-auto-004",
                stage=MatchStage.LOADING,
                at_seconds=80.0,
            ),
        )

        AutoStageHintService(self.settings).run()
        hints = self._load_hints("session-auto-004")
        in_game_hints = [hint for hint in hints if hint.stage == MatchStage.IN_GAME]

        self.assertEqual([hint.at_seconds for hint in in_game_hints], [0.0, 1200.0])


if __name__ == "__main__":
    unittest.main()
