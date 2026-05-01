from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from arl.config import SegmenterSettings, Settings, StorageSettings
from arl.segmenter.models import MatchStageSignal, StageSignalIngestStateFile
from arl.segmenter.signals_from_subtitles import StageSignalFromSubtitlesService
from arl.shared.contracts import SubtitleAsset
from arl.shared.jsonl_store import append_model, load_models


class StageSignalFromSubtitlesServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.temp_root = root / "tmp"
        self.processed_root = root / "processed"
        self.settings = Settings(
            storage=StorageSettings(
                raw_dir=root / "raw",
                processed_dir=self.processed_root,
                export_dir=root / "exports",
                temp_dir=self.temp_root,
            )
        )
        self.subtitle_assets_path = self.temp_root / "subtitle-assets.jsonl"
        self.signals_path = self.temp_root / "match-stage-signals.jsonl"
        self.state_path = self.temp_root / "stage-signal-ingest-state.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

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

    def _write_srt(self, session_id: str, match_index: int, content: str) -> Path:
        subtitle_path = self.processed_root / session_id / f"match-{match_index:02d}.srt"
        subtitle_path.parent.mkdir(parents=True, exist_ok=True)
        subtitle_path.write_text(content, encoding="utf-8")
        return subtitle_path

    def test_extracts_first_stage_signals_from_srt(self) -> None:
        subtitle_path = self._write_srt(
            "session-signal-srt-001",
            1,
            (
                "1\n"
                "00:00:05,000 --> 00:00:07,000\n"
                "Champion select draft starts.\n\n"
                "2\n"
                "00:00:20,000 --> 00:00:23,000\n"
                "Game loading now.\n\n"
                "3\n"
                "00:01:10,000 --> 00:01:14,000\n"
                "In game scoreboard updated.\n\n"
                "4\n"
                "00:10:40,000 --> 00:10:44,000\n"
                "Victory game over.\n\n"
                "5\n"
                "00:10:50,000 --> 00:10:54,000\n"
                "Another in game mention should be ignored as duplicate stage.\n"
            ),
        )
        self._append_subtitle_asset("session-signal-srt-001", 1, subtitle_path)

        StageSignalFromSubtitlesService(self.settings).run()
        signals = load_models(self.signals_path, MatchStageSignal)

        self.assertEqual(len(signals), 4)
        self.assertEqual([signal.source for signal in signals], ["subtitles_srt"] * 4)
        self.assertEqual([signal.at_seconds for signal in signals], [5.0, 20.0, 70.0, 640.0])
        self.assertIn("Champion select", signals[0].text)
        self.assertIn("loading", signals[1].text.lower())
        self.assertIn("scoreboard", signals[2].text.lower())
        self.assertIn("victory", signals[3].text.lower())

    def test_idempotent_on_repeated_runs(self) -> None:
        subtitle_path = self._write_srt(
            "session-signal-srt-002",
            2,
            (
                "1\n"
                "00:00:01,000 --> 00:00:03,000\n"
                "In game scoreboard.\n"
            ),
        )
        self._append_subtitle_asset("session-signal-srt-002", 2, subtitle_path)
        service = StageSignalFromSubtitlesService(self.settings)
        service.run()
        service.run()

        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 1)

    def test_force_reprocess_does_not_duplicate_existing_signals(self) -> None:
        subtitle_path = self._write_srt(
            "session-signal-srt-008",
            8,
            (
                "1\n"
                "00:00:03,000 --> 00:00:06,000\n"
                "Champion select starts.\n\n"
                "2\n"
                "00:00:20,000 --> 00:00:24,000\n"
                "In game scoreboard.\n"
            ),
        )
        self._append_subtitle_asset("session-signal-srt-008", 8, subtitle_path)
        service = StageSignalFromSubtitlesService(self.settings)
        service.run()
        service.run(force_reprocess=True)

        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 2)
        self.assertEqual([signal.at_seconds for signal in signals], [3.0, 20.0])

    def test_filter_by_session_id_only_processes_selected_assets(self) -> None:
        subtitle_a = self._write_srt(
            "session-signal-srt-filter-a",
            1,
            (
                "1\n"
                "00:00:03,000 --> 00:00:06,000\n"
                "In game scoreboard.\n"
            ),
        )
        subtitle_b = self._write_srt(
            "session-signal-srt-filter-b",
            1,
            (
                "1\n"
                "00:00:05,000 --> 00:00:08,000\n"
                "In game scoreboard.\n"
            ),
        )
        self._append_subtitle_asset("session-signal-srt-filter-a", 1, subtitle_a)
        self._append_subtitle_asset("session-signal-srt-filter-b", 1, subtitle_b)

        output = io.StringIO()
        with redirect_stdout(output):
            StageSignalFromSubtitlesService(self.settings).run(
                session_ids={"session-signal-srt-filter-b"},
            )
        signals = load_models(self.signals_path, MatchStageSignal)

        self.assertIn(
            "stage-signals-from-subtitles filter summary total_assets=2 matched_assets=1",
            output.getvalue(),
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].session_id, "session-signal-srt-filter-b")

    def test_filter_by_subtitle_path_only_processes_selected_assets(self) -> None:
        subtitle_a = self._write_srt(
            "session-signal-srt-path-a",
            1,
            (
                "1\n"
                "00:00:03,000 --> 00:00:06,000\n"
                "In game scoreboard.\n"
            ),
        )
        subtitle_b = self._write_srt(
            "session-signal-srt-path-b",
            1,
            (
                "1\n"
                "00:00:05,000 --> 00:00:08,000\n"
                "In game scoreboard.\n"
            ),
        )
        self._append_subtitle_asset("session-signal-srt-path-a", 1, subtitle_a)
        self._append_subtitle_asset("session-signal-srt-path-b", 1, subtitle_b)

        StageSignalFromSubtitlesService(self.settings).run(
            subtitle_paths={subtitle_a},
        )
        signals = load_models(self.signals_path, MatchStageSignal)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].session_id, "session-signal-srt-path-a")

    def test_filter_by_match_index_only_processes_selected_assets(self) -> None:
        subtitle_1 = self._write_srt(
            "session-signal-srt-match-index",
            1,
            (
                "1\n"
                "00:00:03,000 --> 00:00:06,000\n"
                "In game scoreboard.\n"
            ),
        )
        subtitle_2 = self._write_srt(
            "session-signal-srt-match-index",
            2,
            (
                "1\n"
                "00:00:07,000 --> 00:00:10,000\n"
                "In game scoreboard.\n"
            ),
        )
        self._append_subtitle_asset("session-signal-srt-match-index", 1, subtitle_1)
        self._append_subtitle_asset("session-signal-srt-match-index", 2, subtitle_2)

        StageSignalFromSubtitlesService(self.settings).run(
            match_indices={2},
        )
        signals = load_models(self.signals_path, MatchStageSignal)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].at_seconds, 7.0)

    def test_filter_intersection_includes_match_indices_dimension(self) -> None:
        subtitle_a1 = self._write_srt(
            "session-signal-srt-cross-a",
            1,
            (
                "1\n"
                "00:00:03,000 --> 00:00:06,000\n"
                "In game scoreboard.\n"
            ),
        )
        subtitle_a2 = self._write_srt(
            "session-signal-srt-cross-a",
            2,
            (
                "1\n"
                "00:00:05,000 --> 00:00:08,000\n"
                "In game scoreboard.\n"
            ),
        )
        subtitle_b2 = self._write_srt(
            "session-signal-srt-cross-b",
            2,
            (
                "1\n"
                "00:00:09,000 --> 00:00:12,000\n"
                "In game scoreboard.\n"
            ),
        )
        self._append_subtitle_asset("session-signal-srt-cross-a", 1, subtitle_a1)
        self._append_subtitle_asset("session-signal-srt-cross-a", 2, subtitle_a2)
        self._append_subtitle_asset("session-signal-srt-cross-b", 2, subtitle_b2)

        StageSignalFromSubtitlesService(self.settings).run(
            session_ids={"session-signal-srt-cross-a", "session-signal-srt-cross-b"},
            subtitle_paths={subtitle_a2, subtitle_b2},
            match_indices={2},
        )
        signals = load_models(self.signals_path, MatchStageSignal)

        self.assertEqual(len(signals), 2)
        self.assertEqual(
            sorted((signal.session_id, signal.at_seconds) for signal in signals),
            [
                ("session-signal-srt-cross-a", 5.0),
                ("session-signal-srt-cross-b", 9.0),
            ],
        )

    def test_logs_and_exits_when_filters_match_no_assets(self) -> None:
        subtitle_path = self._write_srt(
            "session-signal-srt-no-match",
            1,
            (
                "1\n"
                "00:00:03,000 --> 00:00:06,000\n"
                "In game scoreboard.\n"
            ),
        )
        self._append_subtitle_asset("session-signal-srt-no-match", 1, subtitle_path)

        output = io.StringIO()
        with redirect_stdout(output):
            StageSignalFromSubtitlesService(self.settings).run(
                session_ids={"session-not-exists"},
                match_indices={99},
            )

        self.assertIn(
            "stage-signals-from-subtitles filter summary total_assets=1 matched_assets=0",
            output.getvalue(),
        )
        self.assertIn("stage-signals-from-subtitles no assets matched filters", output.getvalue())
        self.assertIn("match_indices=99", output.getvalue())
        self.assertIn(
            "processed_subtitles=0 emitted_signals=0 matched_assets=0 "
            "skipped_already_processed=0 skipped_missing_subtitle=0",
            output.getvalue(),
        )
        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(signals, [])

    def test_summary_logs_include_skip_counters(self) -> None:
        subtitle_ok = self._write_srt(
            "session-signal-srt-summary-ok",
            1,
            (
                "1\n"
                "00:00:03,000 --> 00:00:06,000\n"
                "In game scoreboard.\n"
            ),
        )
        subtitle_missing = self.processed_root / "session-signal-srt-summary-miss" / "match-01.srt"
        self._append_subtitle_asset("session-signal-srt-summary-ok", 1, subtitle_ok)
        self._append_subtitle_asset("session-signal-srt-summary-miss", 1, subtitle_missing)
        service = StageSignalFromSubtitlesService(self.settings)
        service.run()

        output = io.StringIO()
        with redirect_stdout(output):
            service.run()
        logs = output.getvalue()

        self.assertIn(
            "matched_assets=2 skipped_already_processed=1 skipped_missing_subtitle=1",
            logs,
        )

    def test_force_reprocess_emits_new_stage_when_subtitle_content_changes(self) -> None:
        session_id = "session-signal-srt-009"
        subtitle_path = self._write_srt(
            session_id,
            9,
            (
                "1\n"
                "00:00:04,000 --> 00:00:07,000\n"
                "Champion select starts.\n\n"
                "2\n"
                "00:00:30,000 --> 00:00:34,000\n"
                "In game scoreboard.\n"
            ),
        )
        self._append_subtitle_asset(session_id, 9, subtitle_path)
        service = StageSignalFromSubtitlesService(self.settings)
        service.run()

        subtitle_path.write_text(
            (
                "1\n"
                "00:00:04,000 --> 00:00:07,000\n"
                "Champion select starts.\n\n"
                "2\n"
                "00:00:16,000 --> 00:00:19,000\n"
                "Game loading now.\n\n"
                "3\n"
                "00:00:30,000 --> 00:00:34,000\n"
                "In game scoreboard.\n"
            ),
            encoding="utf-8",
        )
        service.run(force_reprocess=True)

        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 3)
        self.assertEqual([signal.at_seconds for signal in signals], [4.0, 30.0, 16.0])
        self.assertIn("loading", signals[-1].text.lower())

    def test_extracts_chinese_stage_signals_from_srt(self) -> None:
        subtitle_path = self._write_srt(
            "session-signal-srt-004",
            4,
            (
                "1\n"
                "00:00:06,000 --> 00:00:09,000\n"
                "进入英雄选择，开始BP阶段。\n\n"
                "2\n"
                "00:00:18,000 --> 00:00:20,000\n"
                "正在加载中，准备就绪。\n\n"
                "3\n"
                "00:01:05,000 --> 00:01:10,000\n"
                "对局中发生击杀并推塔。\n\n"
                "4\n"
                "00:09:30,000 --> 00:09:36,000\n"
                "本局胜利，进入结算界面。\n"
            ),
        )
        self._append_subtitle_asset("session-signal-srt-004", 4, subtitle_path)

        StageSignalFromSubtitlesService(self.settings).run()
        signals = load_models(self.signals_path, MatchStageSignal)

        self.assertEqual(len(signals), 4)
        self.assertEqual([signal.source for signal in signals], ["subtitles_srt"] * 4)
        self.assertEqual([signal.at_seconds for signal in signals], [6.0, 18.0, 65.0, 570.0])
        self.assertIn("英雄选择", signals[0].text)
        self.assertIn("加载", signals[1].text)
        self.assertIn("击杀", signals[2].text)
        self.assertIn("胜利", signals[3].text)

    def test_extracts_signals_when_timestamp_uses_dot_separator(self) -> None:
        subtitle_path = self._write_srt(
            "session-signal-srt-dot-ts",
            11,
            (
                "1\n"
                "00:00:06.500 --> 00:00:09.200\n"
                "Champion select draft starts.\n\n"
                "2\n"
                "00:00:18.250 --> 00:00:20.900\n"
                "Game loading now.\n\n"
                "3\n"
                "00:01:05.125 --> 00:01:10.000\n"
                "In game scoreboard updated.\n"
            ),
        )
        self._append_subtitle_asset("session-signal-srt-dot-ts", 11, subtitle_path)

        StageSignalFromSubtitlesService(self.settings).run()
        signals = load_models(self.signals_path, MatchStageSignal)

        self.assertEqual(len(signals), 3)
        self.assertEqual([signal.at_seconds for signal in signals], [6.5, 18.25, 65.125])

    def test_placeholder_or_unmatched_subtitle_text_emits_no_signals_but_marks_processed(self) -> None:
        subtitle_path = self._write_srt(
            "session-signal-srt-003",
            3,
            (
                "1\n"
                "00:00:00,000 --> 00:00:03,000\n"
                "Placeholder subtitle generated by local pipeline.\n"
            ),
        )
        self._append_subtitle_asset("session-signal-srt-003", 3, subtitle_path)

        StageSignalFromSubtitlesService(self.settings).run()
        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 0)

        state = StageSignalIngestStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(len(state.processed_subtitle_keys), 1)
        self.assertEqual(state.emitted_signal_fingerprints_by_subtitle_key, {})

    def test_missing_subtitle_path_skips_without_marking_processed(self) -> None:
        missing_path = self.processed_root / "session-signal-srt-005" / "match-05.srt"
        self._append_subtitle_asset("session-signal-srt-005", 5, missing_path)

        StageSignalFromSubtitlesService(self.settings).run()
        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 0)

        state = StageSignalIngestStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_subtitle_keys, [])

    def test_compacts_stale_ingest_state_entries_not_in_current_subtitle_assets(self) -> None:
        subtitle_path = self._write_srt(
            "session-signal-srt-010",
            10,
            (
                "1\n"
                "00:00:05,000 --> 00:00:08,000\n"
                "In game scoreboard.\n"
            ),
        )
        self._append_subtitle_asset("session-signal-srt-010", 10, subtitle_path)
        stale_key = "stale-session:99:/tmp/stale.srt"
        state = StageSignalIngestStateFile(
            processed_subtitle_keys=[stale_key],
            emitted_signal_fingerprints_by_subtitle_key={
                stale_key: ["old-fingerprint", "old-fingerprint"],
                "another-stale-key": [""],
            },
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

        StageSignalFromSubtitlesService(self.settings).run()
        compacted = StageSignalIngestStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )

        expected_key = f"session-signal-srt-010:10:{subtitle_path}"
        self.assertEqual(compacted.processed_subtitle_keys, [expected_key])
        self.assertEqual(
            sorted(compacted.emitted_signal_fingerprints_by_subtitle_key.keys()),
            [expected_key],
        )

    def test_uses_external_stage_keyword_overrides(self) -> None:
        subtitle_path = self._write_srt(
            "session-signal-srt-006",
            6,
            (
                "1\n"
                "00:00:03,000 --> 00:00:06,000\n"
                "Draft room opened.\n\n"
                "2\n"
                "00:00:14,000 --> 00:00:18,000\n"
                "Game ready now.\n\n"
                "3\n"
                "00:01:05,000 --> 00:01:10,000\n"
                "Laning phase started.\n\n"
                "4\n"
                "00:10:00,000 --> 00:10:05,000\n"
                "Final whistle.\n"
            ),
        )
        self._append_subtitle_asset("session-signal-srt-006", 6, subtitle_path)

        keyword_path = self.temp_root / "stage-keywords.json"
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
            update={
                "segmenter": SegmenterSettings(stage_keywords_path=keyword_path),
            },
        )

        StageSignalFromSubtitlesService(settings).run()
        signals = load_models(self.signals_path, MatchStageSignal)

        self.assertEqual(len(signals), 4)
        self.assertEqual([signal.at_seconds for signal in signals], [3.0, 14.0, 65.0, 600.0])

    def test_invalid_stage_keyword_override_logs_and_falls_back_without_crash(self) -> None:
        subtitle_path = self._write_srt(
            "session-signal-srt-007",
            7,
            (
                "1\n"
                "00:00:03,000 --> 00:00:06,000\n"
                "In game scoreboard update.\n"
            ),
        )
        self._append_subtitle_asset("session-signal-srt-007", 7, subtitle_path)
        bad_keyword_path = self.temp_root / "bad-stage-keywords.json"
        bad_keyword_path.write_text("{broken-json", encoding="utf-8")
        settings = self.settings.model_copy(
            deep=True,
            update={
                "segmenter": SegmenterSettings(stage_keywords_path=bad_keyword_path),
            },
        )

        output = io.StringIO()
        with redirect_stdout(output):
            StageSignalFromSubtitlesService(settings).run()

        self.assertIn("stage-keywords override invalid json", output.getvalue())
        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].at_seconds, 3.0)


if __name__ == "__main__":
    unittest.main()
