from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import EditingSettings, Settings, StorageSettings
from arl.editing.models import EditPlannerStateFile
from arl.editing.service import EditingPlannerService
from arl.shared.contracts import (
    EditPlanAsset,
    HighlightClipWindow,
    HighlightPlanAsset,
    MatchBoundary,
)
from arl.shared.jsonl_store import append_model, load_models


class EditingPlannerServiceTest(unittest.TestCase):
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
            editing=EditingSettings(
                enabled=True,
                teaser_max_segments=2,
                teaser_max_total_seconds=45.0,
                teaser_min_segment_seconds=3.0,
            ),
        )
        self.boundaries_path = self.temp_root / "match-boundaries.jsonl"
        self.highlight_plans_path = self.temp_root / "highlight-plans.jsonl"
        self.edit_plans_path = self.temp_root / "edit-plans.jsonl"
        self.state_path = self.temp_root / "editing-state.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_planner_writes_teasers_before_full_main(self) -> None:
        session_id = "session-edit-001"
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=120.0,
                    ended_at_seconds=135.0,
                    reason="condensed_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=300.0,
                    ended_at_seconds=325.0,
                    reason="highlight_keyword",
                ),
                HighlightClipWindow(
                    started_at_seconds=60.0,
                    ended_at_seconds=70.0,
                    reason="narration",
                ),
            ],
            duration=600.0,
        )

        service = EditingPlannerService(self.settings)
        service.run()
        service.run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.session_id, session_id)
        self.assertEqual(
            [(segment.role, segment.source_start_seconds, segment.source_end_seconds) for segment in plan.timeline],
            [
                ("teaser", 300.0, 325.0),
                ("teaser", 120.0, 135.0),
                ("main", 0.0, 600.0),
            ],
        )
        self.assertEqual(plan.audio_beds, [])
        self.assertEqual(plan.sound_effects, [])
        state = EditPlannerStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [f"{session_id}:1"])

    def test_audio_instructions_emit_only_for_existing_configured_assets(self) -> None:
        session_id = "session-edit-audio"
        bgm_path = self.temp_root / "audio" / "bgm.mp3"
        sfx_path = self.temp_root / "audio" / "wow.wav"
        bgm_path.parent.mkdir(parents=True, exist_ok=True)
        bgm_path.write_text("fake bgm", encoding="utf-8")
        sfx_path.write_text("fake sfx", encoding="utf-8")
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.bgm_path = bgm_path
        self.settings.editing.bgm_gain_db = -30.0
        self.settings.editing.sfx_path = sfx_path
        self.settings.editing.sfx_gain_db = -9.0
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=300.0,
                    ended_at_seconds=325.0,
                    reason="highlight_keyword",
                ),
                HighlightClipWindow(
                    started_at_seconds=120.0,
                    ended_at_seconds=135.0,
                    reason="condensed_context",
                ),
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(len(plan.audio_beds), 1)
        self.assertEqual(plan.audio_beds[0].source_path, str(bgm_path))
        self.assertEqual(plan.audio_beds[0].gain_db, -30.0)
        self.assertTrue(plan.audio_beds[0].loop)
        self.assertEqual(len(plan.sound_effects), 1)
        self.assertEqual(plan.sound_effects[0].source_path, str(sfx_path))
        self.assertEqual(plan.sound_effects[0].at_seconds, 0.0)
        self.assertEqual(plan.sound_effects[0].gain_db, -9.0)
        self.assertEqual(plan.sound_effects[0].reason, "highlight_keyword")

    def test_missing_audio_assets_preserve_base_edit_plan(self) -> None:
        session_id = "session-edit-missing-audio"
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.bgm_path = self.temp_root / "audio" / "missing-bgm.mp3"
        self.settings.editing.sfx_path = self.temp_root / "audio" / "missing-sfx.wav"
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=110.0,
                    reason="highlight_keyword",
                )
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 1)
        self.assertEqual(len(plans[0].timeline), 2)
        self.assertEqual(plans[0].audio_beds, [])
        self.assertEqual(plans[0].sound_effects, [])

    def test_missing_highlight_plan_does_not_mark_processed(self) -> None:
        session_id = "session-edit-missing-highlight"
        self._append_boundary(session_id, duration=600.0)

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(plans, [])
        state = EditPlannerStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [])

    def test_force_reprocess_appends_replacement_plan(self) -> None:
        session_id = "session-edit-force"
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=110.0,
                    reason="highlight_keyword",
                )
            ],
            duration=600.0,
        )
        service = EditingPlannerService(self.settings)

        service.run()
        service.run(force_reprocess=True)

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 2)
        state = EditPlannerStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [f"{session_id}:1"])

    def test_filters_by_session_and_match_index(self) -> None:
        for session_id, match_index in [
            ("session-edit-filter-a", 1),
            ("session-edit-filter-b", 1),
            ("session-edit-filter-b", 2),
        ]:
            self._append_boundary(session_id, match_index=match_index, duration=600.0)
            self._append_highlight_plan(
                session_id,
                match_index=match_index,
                windows=[
                    HighlightClipWindow(
                        started_at_seconds=100.0,
                        ended_at_seconds=110.0,
                        reason="highlight_keyword",
                    )
                ],
                duration=600.0,
            )

        EditingPlannerService(self.settings).run(
            session_ids={"session-edit-filter-b"},
            match_indices={2},
        )

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(
            [(plan.session_id, plan.match_index) for plan in plans],
            [("session-edit-filter-b", 2)],
        )

    def test_stale_highlight_plan_does_not_mark_processed(self) -> None:
        session_id = "session-edit-stale-highlight"
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=110.0,
                    reason="highlight_keyword",
                )
            ],
            duration=500.0,
        )

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(plans, [])
        state = EditPlannerStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [])

    def _append_boundary(
        self,
        session_id: str,
        *,
        match_index: int = 1,
        duration: float,
        confidence: float = 0.9,
        is_complete: bool = True,
    ) -> None:
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id=session_id,
                match_index=match_index,
                started_at_seconds=0.0,
                ended_at_seconds=duration,
                confidence=confidence,
                is_complete=is_complete,
            ),
        )

    def _append_highlight_plan(
        self,
        session_id: str,
        *,
        match_index: int = 1,
        windows: list[HighlightClipWindow],
        duration: float,
    ) -> None:
        append_model(
            self.highlight_plans_path,
            HighlightPlanAsset(
                session_id=session_id,
                match_index=match_index,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=duration,
                windows=windows,
                created_at=datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc),
            ),
        )


if __name__ == "__main__":
    unittest.main()
