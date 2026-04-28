from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from arl.cli import main
from arl.config import Settings, StorageSettings
from arl.segmenter.models import MatchStageSignal
from arl.shared.contracts import SubtitleAsset
from arl.shared.jsonl_store import append_model, load_models


class CliStageSignalsFromSubtitlesE2ETest(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_srt(self, session_id: str, match_index: int, content: str) -> Path:
        subtitle_path = self.processed_root / session_id / f"match-{match_index:02d}.srt"
        subtitle_path.parent.mkdir(parents=True, exist_ok=True)
        subtitle_path.write_text(content, encoding="utf-8")
        return subtitle_path

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

    def _run_cli(self, *args: str) -> int:
        with patch.object(sys, "argv", ["arl", *args]), patch(
            "arl.cli.load_settings",
            return_value=self.settings,
        ):
            return main()

    def test_cli_filter_and_force_reprocess_work_in_real_pipeline(self) -> None:
        subtitle_keep = self._write_srt(
            "session-cli-e2e-keep",
            1,
            (
                "1\n"
                "00:00:20,000 --> 00:00:24,000\n"
                "In game scoreboard.\n"
            ),
        )
        subtitle_other = self._write_srt(
            "session-cli-e2e-other",
            1,
            (
                "1\n"
                "00:00:25,000 --> 00:00:29,000\n"
                "In game scoreboard.\n"
            ),
        )
        self._append_subtitle_asset("session-cli-e2e-keep", 1, subtitle_keep)
        self._append_subtitle_asset("session-cli-e2e-other", 1, subtitle_other)

        self.assertEqual(
            self._run_cli(
                "stage-signals-from-subtitles",
                "--session-id",
                "session-cli-e2e-keep",
            ),
            0,
        )
        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].session_id, "session-cli-e2e-keep")
        self.assertEqual(signals[0].at_seconds, 20.0)

        subtitle_keep.write_text(
            (
                "1\n"
                "00:00:05,000 --> 00:00:08,000\n"
                "Game loading now.\n\n"
                "2\n"
                "00:00:20,000 --> 00:00:24,000\n"
                "In game scoreboard.\n"
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            self._run_cli(
                "stage-signals-from-subtitles",
                "--session-id",
                "session-cli-e2e-keep",
                "--force-reprocess",
            ),
            0,
        )
        signals = load_models(self.signals_path, MatchStageSignal)
        keep_signals = [
            signal for signal in signals if signal.session_id == "session-cli-e2e-keep"
        ]
        self.assertEqual(len(keep_signals), 2)
        self.assertEqual(sorted(signal.at_seconds for signal in keep_signals), [5.0, 20.0])
        self.assertFalse(any(signal.session_id == "session-cli-e2e-other" for signal in signals))

    def test_cli_filter_intersection_with_session_ids_and_subtitle_paths(self) -> None:
        subtitle_a = self._write_srt(
            "session-cli-e2e-a",
            1,
            (
                "1\n"
                "00:00:11,000 --> 00:00:14,000\n"
                "In game scoreboard.\n"
            ),
        )
        subtitle_b = self._write_srt(
            "session-cli-e2e-b",
            1,
            (
                "1\n"
                "00:00:12,000 --> 00:00:15,000\n"
                "In game scoreboard.\n"
            ),
        )
        subtitle_c = self._write_srt(
            "session-cli-e2e-c",
            1,
            (
                "1\n"
                "00:00:13,000 --> 00:00:16,000\n"
                "In game scoreboard.\n"
            ),
        )
        self._append_subtitle_asset("session-cli-e2e-a", 1, subtitle_a)
        self._append_subtitle_asset("session-cli-e2e-b", 1, subtitle_b)
        self._append_subtitle_asset("session-cli-e2e-c", 1, subtitle_c)

        self.assertEqual(
            self._run_cli(
                "stage-signals-from-subtitles",
                "--session-ids",
                "session-cli-e2e-a,session-cli-e2e-b",
                "--subtitle-paths",
                f"{subtitle_b},{subtitle_c}",
            ),
            0,
        )
        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].session_id, "session-cli-e2e-b")

    def test_cli_filter_intersection_includes_match_index(self) -> None:
        subtitle_a1 = self._write_srt(
            "session-cli-e2e-mi-a",
            1,
            (
                "1\n"
                "00:00:10,000 --> 00:00:12,000\n"
                "In game scoreboard.\n"
            ),
        )
        subtitle_a2 = self._write_srt(
            "session-cli-e2e-mi-a",
            2,
            (
                "1\n"
                "00:00:13,000 --> 00:00:16,000\n"
                "In game scoreboard.\n"
            ),
        )
        subtitle_b2 = self._write_srt(
            "session-cli-e2e-mi-b",
            2,
            (
                "1\n"
                "00:00:17,000 --> 00:00:19,000\n"
                "In game scoreboard.\n"
            ),
        )
        self._append_subtitle_asset("session-cli-e2e-mi-a", 1, subtitle_a1)
        self._append_subtitle_asset("session-cli-e2e-mi-a", 2, subtitle_a2)
        self._append_subtitle_asset("session-cli-e2e-mi-b", 2, subtitle_b2)

        self.assertEqual(
            self._run_cli(
                "stage-signals-from-subtitles",
                "--session-ids",
                "session-cli-e2e-mi-a,session-cli-e2e-mi-b",
                "--subtitle-paths",
                f"{subtitle_a1},{subtitle_a2},{subtitle_b2}",
                "--match-index",
                "2",
            ),
            0,
        )
        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 2)
        self.assertEqual(
            sorted((signal.session_id, signal.at_seconds) for signal in signals),
            [("session-cli-e2e-mi-a", 13.0), ("session-cli-e2e-mi-b", 17.0)],
        )

    def test_cli_logs_and_summarizes_when_filters_match_no_assets(self) -> None:
        subtitle = self._write_srt(
            "session-cli-e2e-no-match",
            1,
            (
                "1\n"
                "00:00:10,000 --> 00:00:12,000\n"
                "In game scoreboard.\n"
            ),
        )
        self._append_subtitle_asset("session-cli-e2e-no-match", 1, subtitle)

        output = StringIO()
        with redirect_stdout(output):
            exit_code = self._run_cli(
                "stage-signals-from-subtitles",
                "--session-id",
                "session-cli-e2e-not-exist",
                "--match-index",
                "9",
            )

        self.assertEqual(exit_code, 0)
        logs = output.getvalue()
        self.assertIn(
            "stage-signals-from-subtitles filter summary total_assets=1 matched_assets=0",
            logs,
        )
        self.assertIn("stage-signals-from-subtitles no assets matched filters", logs)
        self.assertIn("match_indices=9", logs)
        self.assertIn(
            "processed_subtitles=0 emitted_signals=0 matched_assets=0 "
            "skipped_already_processed=0 skipped_missing_subtitle=0",
            logs,
        )
        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(signals, [])

    def test_cli_force_reprocess_with_intersection_filter_does_not_duplicate(self) -> None:
        subtitle_a = self._write_srt(
            "session-cli-e2e-dedupe-a",
            1,
            (
                "1\n"
                "00:00:08,000 --> 00:00:11,000\n"
                "In game scoreboard.\n"
            ),
        )
        subtitle_b = self._write_srt(
            "session-cli-e2e-dedupe-b",
            1,
            (
                "1\n"
                "00:00:09,000 --> 00:00:12,000\n"
                "In game scoreboard.\n"
            ),
        )
        self._append_subtitle_asset("session-cli-e2e-dedupe-a", 1, subtitle_a)
        self._append_subtitle_asset("session-cli-e2e-dedupe-b", 1, subtitle_b)

        common_args = (
            "stage-signals-from-subtitles",
            "--session-ids",
            "session-cli-e2e-dedupe-a,session-cli-e2e-dedupe-b",
            "--subtitle-paths",
            f"{subtitle_b},{self.processed_root / 'session-cli-e2e-miss' / 'match-01.srt'}",
        )
        self.assertEqual(self._run_cli(*common_args), 0)
        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].session_id, "session-cli-e2e-dedupe-b")

        self.assertEqual(self._run_cli(*common_args, "--force-reprocess"), 0)
        signals = load_models(self.signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].session_id, "session-cli-e2e-dedupe-b")


if __name__ == "__main__":
    unittest.main()
