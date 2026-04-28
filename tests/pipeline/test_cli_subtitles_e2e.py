from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from arl.cli import main
from arl.config import Settings, StorageSettings, SubtitleSettings
from arl.shared.contracts import MatchBoundary
from arl.shared.jsonl_store import append_model


def _read_jsonl_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        rows.append(json.loads(stripped))
    return rows


class CliSubtitlesE2ETest(unittest.TestCase):
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
            ),
            subtitles=SubtitleSettings(enabled=True, provider="placeholder"),
        )
        self.boundaries_path = self.temp_root / "match-boundaries.jsonl"
        self.subtitle_assets_path = self.temp_root / "subtitle-assets.jsonl"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _append_boundary(
        self,
        session_id: str,
        match_index: int,
        *,
        started_at_seconds: float,
        ended_at_seconds: float,
    ) -> None:
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id=session_id,
                match_index=match_index,
                started_at_seconds=started_at_seconds,
                ended_at_seconds=ended_at_seconds,
                confidence=0.9,
            ),
        )

    def _run_cli(self, *args: str) -> int:
        with patch.object(sys, "argv", ["arl", *args]), patch(
            "arl.cli.load_settings",
            return_value=self.settings,
        ):
            return main()

    def test_cli_subtitles_filters_intersection_targets_only_selected_boundaries(self) -> None:
        self._append_boundary(
            "session-sub-e2e-a",
            1,
            started_at_seconds=0.0,
            ended_at_seconds=30.0,
        )
        self._append_boundary(
            "session-sub-e2e-a",
            2,
            started_at_seconds=30.0,
            ended_at_seconds=60.0,
        )
        self._append_boundary(
            "session-sub-e2e-b",
            2,
            started_at_seconds=0.0,
            ended_at_seconds=30.0,
        )

        exit_code = self._run_cli(
            "subtitles",
            "--session-ids",
            "session-sub-e2e-a,session-sub-e2e-b",
            "--match-index",
            "2",
        )
        self.assertEqual(exit_code, 0)

        subtitle_assets = _read_jsonl_rows(self.subtitle_assets_path)
        self.assertEqual(len(subtitle_assets), 2)
        self.assertEqual(
            sorted((row["session_id"], row["match_index"]) for row in subtitle_assets),
            [("session-sub-e2e-a", 2), ("session-sub-e2e-b", 2)],
        )

    def test_cli_subtitles_logs_no_match_observability_when_filters_miss(self) -> None:
        self._append_boundary(
            "session-sub-e2e-no-match",
            1,
            started_at_seconds=0.0,
            ended_at_seconds=30.0,
        )

        output = StringIO()
        with redirect_stdout(output):
            exit_code = self._run_cli(
                "subtitles",
                "--session-id",
                "session-sub-e2e-not-exists",
                "--match-index",
                "9",
            )
        self.assertEqual(exit_code, 0)

        logs = output.getvalue()
        self.assertIn("filters summary total_boundaries=1 matched_boundaries=0", logs)
        self.assertIn("no boundaries matched filters", logs)
        self.assertIn("processed_matches=0", logs)
        subtitle_assets = _read_jsonl_rows(self.subtitle_assets_path)
        self.assertEqual(subtitle_assets, [])


if __name__ == "__main__":
    unittest.main()
