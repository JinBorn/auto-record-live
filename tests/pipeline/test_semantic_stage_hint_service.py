from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import RecordingSettings, SegmenterSettings, Settings, StorageSettings
from arl.segmenter.models import MatchStageHint, MatchStageSignal
from arl.segmenter.semantic_hints import SemanticStageHintService
from arl.shared.contracts import MatchStage, RecordingAsset, SourceType, SubtitleAsset
from arl.shared.jsonl_store import append_model, load_models


class SemanticStageHintServiceTest(unittest.TestCase):
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
        self.match_stage_signals_path = self.temp_root / "match-stage-signals.jsonl"
        self.subtitle_assets_path = self.temp_root / "subtitle-assets.jsonl"

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

    def _append_signal(
        self,
        session_id: str,
        text: str,
        *,
        at_seconds: float | None = None,
        detected_at: datetime | None = None,
    ) -> None:
        append_model(
            self.match_stage_signals_path,
            MatchStageSignal(
                session_id=session_id,
                text=text,
                at_seconds=at_seconds,
                detected_at=detected_at,
            ),
        )

    def _append_subtitle_asset(self, session_id: str, match_index: int, subtitle_path: Path) -> None:
        append_model(
            self.subtitle_assets_path,
            SubtitleAsset(
                session_id=session_id,
                match_index=match_index,
                path=str(subtitle_path),
                format="srt",
            ),
        )

    def test_semantic_stage_hints_emit_stage_sequence_by_cycles(self) -> None:
        self._append_asset(
            "session-semantic-001",
            datetime(2026, 4, 26, 18, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 18, 40, tzinfo=timezone.utc),
        )

        SemanticStageHintService(self.settings).run()
        hints = self._load_hints("session-semantic-001")

        expected = [
            (MatchStage.CHAMPION_SELECT, 0.0),
            (MatchStage.LOADING, 45.0),
            (MatchStage.IN_GAME, 75.0),
            (MatchStage.POST_GAME, 1175.0),
            (MatchStage.CHAMPION_SELECT, 1200.0),
            (MatchStage.LOADING, 1245.0),
            (MatchStage.IN_GAME, 1275.0),
            (MatchStage.POST_GAME, 2375.0),
        ]
        self.assertEqual(len(hints), len(expected))
        self.assertEqual([(hint.stage, hint.at_seconds) for hint in hints], expected)

    def test_semantic_stage_hints_idempotent_on_repeated_runs(self) -> None:
        self._append_asset(
            "session-semantic-002",
            datetime(2026, 4, 26, 19, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 19, 31, tzinfo=timezone.utc),
        )

        service = SemanticStageHintService(self.settings)
        service.run()
        service.run()

        hints = self._load_hints("session-semantic-002")
        self.assertEqual(len(hints), 8)

    def test_semantic_stage_hints_skip_sessions_with_existing_hints(self) -> None:
        self._append_asset(
            "session-semantic-003",
            datetime(2026, 4, 26, 20, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 20, 35, tzinfo=timezone.utc),
        )
        append_model(
            self.match_stage_hints_path,
            MatchStageHint(
                session_id="session-semantic-003",
                stage=MatchStage.IN_GAME,
                at_seconds=120.0,
            ),
        )

        SemanticStageHintService(self.settings).run()
        hints = self._load_hints("session-semantic-003")

        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0].at_seconds, 120.0)

    def test_semantic_stage_hints_short_cycle_keeps_in_game_inside_duration(self) -> None:
        self._append_asset(
            "session-semantic-004",
            datetime(2026, 4, 26, 21, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 21, 1, tzinfo=timezone.utc),
        )

        SemanticStageHintService(self.settings).run()
        hints = self._load_hints("session-semantic-004")
        in_game_hints = [hint for hint in hints if hint.stage == MatchStage.IN_GAME]

        self.assertEqual(len(in_game_hints), 1)
        self.assertIsNotNone(in_game_hints[0].at_seconds)
        self.assertLess(in_game_hints[0].at_seconds, 60.0)

    def test_semantic_stage_hints_prefer_signal_driven_sequence_when_available(self) -> None:
        self._append_asset(
            "session-semantic-005",
            datetime(2026, 4, 26, 22, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 22, 35, tzinfo=timezone.utc),
        )
        self._append_signal("session-semantic-005", "champion select ready", at_seconds=15.0)
        self._append_signal("session-semantic-005", "loading game resources", at_seconds=52.0)
        self._append_signal("session-semantic-005", "in game scoreboard", at_seconds=97.0)
        self._append_signal("session-semantic-005", "victory game over", at_seconds=1950.0)

        SemanticStageHintService(self.settings).run()
        hints = self._load_hints("session-semantic-005")

        self.assertEqual(
            [(hint.stage, hint.at_seconds) for hint in hints],
            [
                (MatchStage.CHAMPION_SELECT, 15.0),
                (MatchStage.LOADING, 52.0),
                (MatchStage.IN_GAME, 97.0),
                (MatchStage.POST_GAME, 1950.0),
            ],
        )

    def test_semantic_stage_hints_support_chinese_signals(self) -> None:
        self._append_asset(
            "session-semantic-009",
            datetime(2026, 4, 27, 2, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 27, 2, 35, tzinfo=timezone.utc),
        )
        self._append_signal("session-semantic-009", "进入英雄选择，开始BP阶段。", at_seconds=12.0)
        self._append_signal("session-semantic-009", "正在加载中，准备就绪。", at_seconds=40.0)
        self._append_signal("session-semantic-009", "对局中发生击杀并推塔。", at_seconds=88.0)
        self._append_signal("session-semantic-009", "本局胜利，进入结算界面。", at_seconds=1820.0)

        SemanticStageHintService(self.settings).run()
        hints = self._load_hints("session-semantic-009")

        self.assertEqual(
            [(hint.stage, hint.at_seconds) for hint in hints],
            [
                (MatchStage.CHAMPION_SELECT, 12.0),
                (MatchStage.LOADING, 40.0),
                (MatchStage.IN_GAME, 88.0),
                (MatchStage.POST_GAME, 1820.0),
            ],
        )

    def test_semantic_stage_hints_fallback_to_template_when_signals_have_no_in_game(self) -> None:
        self._append_asset(
            "session-semantic-006",
            datetime(2026, 4, 26, 23, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 23, 31, tzinfo=timezone.utc),
        )
        self._append_signal("session-semantic-006", "champion select window", at_seconds=10.0)
        self._append_signal("session-semantic-006", "loading complete", at_seconds=50.0)
        self._append_signal("session-semantic-006", "post game summary", at_seconds=1800.0)

        SemanticStageHintService(self.settings).run()
        hints = self._load_hints("session-semantic-006")

        self.assertEqual(len(hints), 8)
        self.assertEqual(hints[0].stage, MatchStage.CHAMPION_SELECT)
        self.assertEqual(hints[2].stage, MatchStage.IN_GAME)

    def test_semantic_stage_hints_use_detected_at_signals_and_ignore_out_of_range(self) -> None:
        started_at = datetime(2026, 4, 27, 0, 0, tzinfo=timezone.utc)
        self._append_asset(
            "session-semantic-007",
            started_at,
            datetime(2026, 4, 27, 0, 20, tzinfo=timezone.utc),
        )
        self._append_signal(
            "session-semantic-007",
            "champion select",
            detected_at=datetime(2026, 4, 27, 0, 0, 20, tzinfo=timezone.utc),
        )
        self._append_signal(
            "session-semantic-007",
            "in game scoreboard",
            detected_at=datetime(2026, 4, 27, 0, 2, 0, tzinfo=timezone.utc),
        )
        self._append_signal(
            "session-semantic-007",
            "victory game over",
            detected_at=datetime(2026, 4, 27, 0, 30, tzinfo=timezone.utc),
        )

        SemanticStageHintService(self.settings).run()
        hints = self._load_hints("session-semantic-007")

        self.assertEqual(
            [(hint.stage, hint.at_seconds) for hint in hints],
            [
                (MatchStage.CHAMPION_SELECT, 20.0),
                (MatchStage.IN_GAME, 120.0),
            ],
        )

    def test_semantic_stage_hints_auto_ingest_signals_from_subtitles(self) -> None:
        self._append_asset(
            "session-semantic-008",
            datetime(2026, 4, 27, 1, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 27, 1, 35, tzinfo=timezone.utc),
        )
        subtitle_path = self.settings.storage.processed_dir / "session-semantic-008" / "match-01.srt"
        subtitle_path.parent.mkdir(parents=True, exist_ok=True)
        subtitle_path.write_text(
            (
                "1\n"
                "00:00:10,000 --> 00:00:15,000\n"
                "Champion select draft.\n\n"
                "2\n"
                "00:00:45,000 --> 00:00:49,000\n"
                "Game loading.\n\n"
                "3\n"
                "00:01:30,000 --> 00:01:40,000\n"
                "In game scoreboard.\n\n"
                "4\n"
                "00:30:00,000 --> 00:30:06,000\n"
                "Victory game over.\n"
            ),
            encoding="utf-8",
        )
        self._append_subtitle_asset("session-semantic-008", 1, subtitle_path)

        SemanticStageHintService(self.settings).run()
        hints = self._load_hints("session-semantic-008")

        self.assertEqual(
            [(hint.stage, hint.at_seconds) for hint in hints],
            [
                (MatchStage.CHAMPION_SELECT, 10.0),
                (MatchStage.LOADING, 45.0),
                (MatchStage.IN_GAME, 90.0),
                (MatchStage.POST_GAME, 1800.0),
            ],
        )

    def test_semantic_stage_hints_use_external_stage_keyword_overrides(self) -> None:
        self._append_asset(
            "session-semantic-010",
            datetime(2026, 4, 27, 3, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 27, 3, 40, tzinfo=timezone.utc),
        )
        self._append_signal("session-semantic-010", "draft room opened", at_seconds=10.0)
        self._append_signal("session-semantic-010", "game ready now", at_seconds=42.0)
        self._append_signal("session-semantic-010", "laning phase started", at_seconds=95.0)
        self._append_signal("session-semantic-010", "final whistle", at_seconds=1860.0)

        keyword_path = self.temp_root / "stage-keywords-custom.json"
        keyword_path.write_text(
            json.dumps(
                {
                    "champion_select": ["draft room"],
                    "loading": ["game ready"],
                    "in_game": ["laning phase"],
                    "post_game": ["final whistle"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        settings = self.settings.model_copy(
            deep=True,
            update={"segmenter": SegmenterSettings(stage_keywords_path=keyword_path)},
        )
        SemanticStageHintService(settings).run()
        hints = [
            hint
            for hint in load_models(self.match_stage_hints_path, MatchStageHint)
            if hint.session_id == "session-semantic-010"
        ]

        self.assertEqual(
            [(hint.stage, hint.at_seconds) for hint in hints],
            [
                (MatchStage.CHAMPION_SELECT, 10.0),
                (MatchStage.LOADING, 42.0),
                (MatchStage.IN_GAME, 95.0),
                (MatchStage.POST_GAME, 1860.0),
            ],
        )


if __name__ == "__main__":
    unittest.main()
