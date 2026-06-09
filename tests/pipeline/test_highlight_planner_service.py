from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import HighlightSettings, Settings, StorageSettings
from arl.highlights.models import HighlightPlannerStateFile
from arl.highlights.service import HighlightPlannerService
from arl.shared.contracts import (
    HighlightClipWindow,
    HighlightPlanAsset,
    MatchBoundary,
    SubtitleAsset,
)
from arl.shared.jsonl_store import append_model, load_models


class HighlightPlannerServiceTest(unittest.TestCase):
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
            highlights=HighlightSettings(
                cue_padding_seconds=10.0,
                highlight_padding_seconds=20.0,
                merge_gap_seconds=40.0,
                keep_edge_seconds=20.0,
                min_boundary_duration_seconds=120.0,
                min_reduction_seconds=60.0,
                min_retained_seconds=120.0,
                min_retained_fraction=0.2,
                max_windows=6,
            ),
        )
        self.boundaries_path = self.temp_root / "match-boundaries.jsonl"
        self.subtitles_path = self.temp_root / "subtitle-assets.jsonl"
        self.plans_path = self.temp_root / "highlight-plans.jsonl"
        self.state_path = self.temp_root / "highlight-planner-state.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_planner_generates_windows_from_narration_and_highlight_keywords(self) -> None:
        session_id = "session-highlight-001"
        subtitle_path = self._write_srt(
            session_id,
            "\n".join(
                [
                    "1",
                    "00:01:40,000 --> 00:01:45,000",
                    "normal jungle pathing narration",
                    "",
                    "2",
                    "00:04:10,000 --> 00:04:14,000",
                    "dragon fight double kill",
                    "",
                    "3",
                    "00:08:20,000 --> 00:08:24,000",
                    "we can push the base now",
                    "",
                    "4",
                    "00:13:40,000 --> 00:13:44,000",
                    "nexus explodes game over",
                    "",
                ]
            )
            + "\n",
        )
        self._append_boundary(session_id, duration=900.0)
        self._append_subtitle(session_id, subtitle_path)

        service = HighlightPlannerService(self.settings)
        service.run()
        service.run()

        plans = load_models(self.plans_path, HighlightPlanAsset)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.session_id, session_id)
        self.assertEqual(plan.match_index, 1)
        self.assertEqual(plan.source_boundary_start_seconds, 0.0)
        self.assertEqual(plan.source_boundary_end_seconds, 900.0)
        self.assertEqual(
            [(window.started_at_seconds, window.ended_at_seconds) for window in plan.windows],
            [
                (0.0, 20.0),
                (90.0, 115.0),
                (230.0, 274.0),
                (480.0, 524.0),
                (800.0, 900.0),
            ],
        )
        self.assertIn("highlight_keyword", [window.reason for window in plan.windows])
        state = HighlightPlannerStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [f"{session_id}:1"])

    def test_planner_omits_plan_when_reduction_is_not_meaningful(self) -> None:
        session_id = "session-highlight-no-reduction"
        subtitle_path = self._write_srt(
            session_id,
            "\n".join(
                [
                    "1",
                    "00:00:30,000 --> 00:00:34,000",
                    "opening commentary",
                    "",
                    "2",
                    "00:01:15,000 --> 00:01:20,000",
                    "more commentary",
                    "",
                    "3",
                    "00:02:05,000 --> 00:02:09,000",
                    "still talking",
                    "",
                    "4",
                    "00:03:00,000 --> 00:03:04,000",
                    "ending commentary",
                    "",
                ]
            )
            + "\n",
        )
        self._append_boundary(session_id, duration=220.0)
        self._append_subtitle(session_id, subtitle_path)

        HighlightPlannerService(self.settings).run()

        self.assertEqual(load_models(self.plans_path, HighlightPlanAsset), [])
        state = HighlightPlannerStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [])

    def test_planner_skips_missing_subtitle_without_marking_processed(self) -> None:
        session_id = "session-highlight-missing-subtitle"
        self._append_boundary(session_id, duration=900.0)
        self._append_subtitle(session_id, Path(self.temp_dir.name) / "missing.srt")

        HighlightPlannerService(self.settings).run()

        self.assertEqual(load_models(self.plans_path, HighlightPlanAsset), [])
        state = HighlightPlannerStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [])

    def test_planner_filters_by_session_ids(self) -> None:
        for session_id in ["session-highlight-filter-a", "session-highlight-filter-b"]:
            subtitle_path = self._write_srt(
                session_id,
                "\n".join(
                    [
                        "1",
                        "00:01:40,000 --> 00:01:45,000",
                        "teamfight kill",
                        "",
                        "2",
                        "00:04:10,000 --> 00:04:14,000",
                        "dragon fight",
                        "",
                        "3",
                        "00:08:20,000 --> 00:08:24,000",
                        "tower push",
                        "",
                        "4",
                        "00:12:20,000 --> 00:12:24,000",
                        "base fight",
                        "",
                    ]
                )
                + "\n",
            )
            self._append_boundary(session_id, duration=900.0)
            self._append_subtitle(session_id, subtitle_path)

        HighlightPlannerService(self.settings).run(session_ids={"session-highlight-filter-b"})

        plans = load_models(self.plans_path, HighlightPlanAsset)
        self.assertEqual([plan.session_id for plan in plans], ["session-highlight-filter-b"])

    def test_planner_replans_when_existing_plan_boundary_is_stale(self) -> None:
        session_id = "session-highlight-stale-plan"
        subtitle_path = self._write_srt(
            session_id,
            "\n".join(
                [
                    "1",
                    "00:01:40,000 --> 00:01:45,000",
                    "teamfight kill",
                    "",
                    "2",
                    "00:04:10,000 --> 00:04:14,000",
                    "dragon fight",
                    "",
                    "3",
                    "00:08:20,000 --> 00:08:24,000",
                    "tower push",
                    "",
                    "4",
                    "00:12:20,000 --> 00:12:24,000",
                    "base fight",
                    "",
                ]
            )
            + "\n",
        )
        self._append_boundary(session_id, duration=900.0)
        self._append_subtitle(session_id, subtitle_path)
        append_model(
            self.plans_path,
            HighlightPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=1200.0,
                windows=[
                    HighlightClipWindow(
                        started_at_seconds=0.0,
                        ended_at_seconds=1200.0,
                        reason="stale",
                    )
                ],
                created_at=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
            ),
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            HighlightPlannerStateFile(
                processed_match_keys=[f"{session_id}:1"]
            ).model_dump_json(indent=2)
            + "\n",
            encoding="utf-8",
        )

        HighlightPlannerService(self.settings).run()

        plans = load_models(self.plans_path, HighlightPlanAsset)
        self.assertEqual(len(plans), 2)
        self.assertEqual(plans[-1].source_boundary_end_seconds, 900.0)
        state = HighlightPlannerStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [f"{session_id}:1"])

    def _append_boundary(self, session_id: str, *, duration: float) -> None:
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=duration,
                confidence=0.8,
            ),
        )

    def _append_subtitle(self, session_id: str, subtitle_path: Path) -> None:
        append_model(
            self.subtitles_path,
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )

    def _write_srt(self, session_id: str, text: str) -> Path:
        path = self.settings.storage.processed_dir / session_id / "match-01.srt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
