from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from arl.config import EditingSettings, Settings, StorageSettings
from arl.editing.models import EditPlannerStateFile
from arl.editing.audio import (
    BgmLibraryTrack,
    BgmSelectionContext,
    SourceMusicDetection,
    select_bgm_tracks,
)
from arl.editing.service import EditingPlannerService
from arl.shared.contracts import (
    AudioBed,
    EditPlanAsset,
    HighlightClipWindow,
    HighlightPlanAsset,
    MatchBoundary,
    RecordingAsset,
    RecordingChunk,
    RecordingChunkManifest,
    SourceType,
    SubtitleAsset,
    TimelineSegment,
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

    def test_planner_writes_teasers_before_condensed_main_windows(self) -> None:
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
                ("main", 0.0, 30.0),
                ("main", 60.0, 70.0),
                ("main", 120.0, 135.0),
                ("main", 300.0, 325.0),
                ("main", 570.0, 600.0),
            ],
        )
        self.assertEqual(plan.audio_beds, [])
        self.assertEqual(plan.sound_effects, [])
        self.assertTrue(all(segment.transform is None for segment in plan.timeline))
        state = EditPlannerStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [f"{session_id}:1"])

    def test_zoom_enabled_marks_high_signal_segments_with_budget(self) -> None:
        session_id = "session-edit-zoom"
        self.settings.editing.zoom_enabled = True
        self.settings.editing.zoom_target = "custom"
        self.settings.editing.zoom_scale = 1.25
        self.settings.editing.zoom_x_anchor = 0.4
        self.settings.editing.zoom_y_anchor = 0.35
        self.settings.editing.zoom_max_segments = 1
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
                    reason="highlight_keyword",
                ),
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 1)
        first_teaser = plans[0].timeline[0]
        second_teaser = plans[0].timeline[1]
        first_main = plans[0].timeline[2]
        self.assertIsNotNone(first_teaser.transform)
        assert first_teaser.transform is not None
        self.assertEqual(first_teaser.transform.kind, "punch_in")
        self.assertEqual(first_teaser.transform.scale, 1.25)
        self.assertEqual(first_teaser.transform.x_anchor, 0.4)
        self.assertEqual(first_teaser.transform.y_anchor, 0.35)
        self.assertEqual(first_teaser.transform.target, "custom")
        self.assertIsNone(second_teaser.transform)
        self.assertIsNone(first_main.transform)

    def test_planner_omits_teaser_for_generic_condensed_key_events(self) -> None:
        session_id = "session-edit-no-teaser"
        self.settings.editing.zoom_enabled = True
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=120.0,
                    ended_at_seconds=150.0,
                    reason="condensed_key_event",
                )
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertTrue(all(segment.role != "teaser" for segment in plan.timeline))
        key_segments = [
            segment for segment in plan.timeline if segment.reason == "condensed_key_event"
        ]
        self.assertEqual(len(key_segments), 1)
        self.assertEqual(key_segments[0].source_start_seconds, 120.0)
        self.assertEqual(key_segments[0].source_end_seconds, 150.0)
        self.assertIsNotNone(key_segments[0].transform)
        assert key_segments[0].transform is not None
        self.assertEqual(key_segments[0].transform.kind, "punch_in")
        self.assertEqual(key_segments[0].transform.target, "chat")
        self.assertTrue(
            all(
                segment.transform is None
                for segment in plan.timeline
                if segment is not key_segments[0]
            )
        )
        self.assertEqual(plan.sound_effects, [])
        self.assertTrue(any(segment.source_start_seconds == 0.0 for segment in plan.timeline))
        self.assertTrue(any(segment.source_end_seconds == 600.0 for segment in plan.timeline))

    def test_zoom_default_targets_bottom_left_chat_area(self) -> None:
        session_id = "session-edit-chat-zoom"
        self.settings.editing.zoom_enabled = True
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
        teaser = plans[0].timeline[0]
        self.assertIsNotNone(teaser.transform)
        assert teaser.transform is not None
        self.assertEqual(teaser.transform.target, "chat")
        self.assertEqual(teaser.transform.x_anchor, 0.0)
        self.assertEqual(teaser.transform.y_anchor, 1.0)

    def test_zoom_max_segments_zero_emits_no_transforms(self) -> None:
        session_id = "session-edit-zoom-zero"
        self.settings.editing.zoom_enabled = True
        self.settings.editing.zoom_max_segments = 0
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
        self.assertTrue(all(segment.transform is None for segment in plans[0].timeline))

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
        self.assertEqual(len(plan.sound_effects), 2)
        self.assertEqual(
            [(hit.at_seconds, hit.reason) for hit in plan.sound_effects],
            [(0.0, "highlight_keyword"), (70.0, "highlight_keyword")],
        )
        for hit in plan.sound_effects:
            self.assertEqual(hit.source_path, str(sfx_path))
            self.assertEqual(hit.gain_db, -9.0)

    def test_audio_mixing_uses_generated_default_assets_when_paths_are_unset(self) -> None:
        session_id = "session-edit-default-audio"
        self.settings.editing.audio_mixing_enabled = True
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=80.0,
                    reason="condensed_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=200.0,
                    reason="highlight_keyword",
                ),
                HighlightClipWindow(
                    started_at_seconds=570.0,
                    ended_at_seconds=600.0,
                    reason="condensed_match_context",
                ),
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(len(plan.audio_beds), 2)
        self.assertTrue(plan.audio_beds[0].source_path.endswith("bgm-playful.wav"))
        self.assertTrue(plan.audio_beds[1].source_path.endswith("bgm-climax.wav"))
        self.assertEqual(plan.audio_beds[0].timeline_start_seconds, 45.0)
        self.assertIsNotNone(plan.audio_beds[0].timeline_end_seconds)
        self.assertEqual(
            plan.audio_beds[1].timeline_start_seconds,
            plan.audio_beds[0].timeline_end_seconds,
        )
        self.assertGreater(plan.audio_beds[1].timeline_start_seconds, 45.0)
        self.assertEqual(plan.audio_beds[1].timeline_end_seconds, None)
        self.assertEqual(plan.audio_beds[0].reason, "background_music_playful")
        self.assertEqual(plan.audio_beds[1].reason, "background_music_climax")
        self.assertEqual(len(plan.sound_effects), 2)
        self.assertEqual(
            [(hit.at_seconds, hit.reason) for hit in plan.sound_effects],
            [(0.0, "highlight_keyword"), (125.0, "highlight_keyword")],
        )
        self.assertTrue(plan.sound_effects[0].source_path.endswith("wow.wav"))
        for audio_path in [
            Path(plan.audio_beds[0].source_path),
            Path(plan.audio_beds[1].source_path),
            Path(plan.sound_effects[0].source_path),
        ]:
            self.assertTrue(audio_path.exists())
            self.assertEqual(audio_path.read_bytes()[:4], b"RIFF")

    def test_audio_mixing_selects_bgm_from_library_by_context(self) -> None:
        session_id = "session-edit-bgm-library"
        self.settings.editing.audio_mixing_enabled = True
        library_path, early_path, climax_path = self._write_bgm_library()
        self.settings.editing.bgm_library_path = library_path
        self.settings.editing.bgm_gain_db = -28.0
        self._append_subtitle(
            session_id,
            "1\n00:00:02,000 --> 00:00:04,000\n"
            "电刀AP机器人 这个套路清线很快\n\n"
            "2\n00:01:40,000 --> 00:01:42,000\n"
            "这波团战直接开起来\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=200.0,
                    reason="highlight_keyword",
                )
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual([bed.source_path for bed in plan.audio_beds], [
            str(early_path),
            str(climax_path),
        ])
        self.assertEqual([bed.gain_db for bed in plan.audio_beds], [-28.0, -28.0])
        self.assertEqual(plan.audio_beds[0].reason, "background_music_library")
        self.assertEqual(plan.audio_beds[1].reason, "background_music_library_climax")

    def test_bgm_library_tie_break_uses_selection_key_for_variety(self) -> None:
        tracks = [
            BgmLibraryTrack(
                path=self.temp_root / f"hype-{index}.wav",
                tags=("hype",),
                phase="climax",
                energy=5,
            )
            for index in range(4)
        ]

        selected_names = {
            select_bgm_tracks(
                tracks,
                BgmSelectionContext(
                    tags=("hype",),
                    highlight_reasons=("condensed_key_event",),
                    rendered_duration_seconds=60.0,
                    selection_key=f"session-edit:{index}",
                ),
            )[0].path.name
            for index in range(12)
        }

        self.assertGreater(len(selected_names), 1)
        self.assertTrue(selected_names <= {track.path.name for track in tracks})

    def test_audio_mixing_selects_bgm_from_chinese_library_aliases(self) -> None:
        session_id = "session-edit-bgm-library-cn"
        self.settings.editing.audio_mixing_enabled = True
        library_path, early_path, climax_path = self._write_chinese_bgm_library()
        self.settings.editing.bgm_library_path = library_path
        self._append_subtitle(
            session_id,
            "1\n00:00:02,000 --> 00:00:04,000\n"
            "电刀AP机器人 这个套路清线很快\n\n"
            "2\n00:01:40,000 --> 00:01:42,000\n"
            "这波团战直接开起来\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=200.0,
                    reason="highlight_keyword",
                )
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 1)
        self.assertEqual([bed.source_path for bed in plans[0].audio_beds], [
            str(early_path),
            str(climax_path),
        ])

    def test_bgm_library_logs_load_diagnostics_and_no_match_context(self) -> None:
        session_id = "session-edit-bgm-library-diagnostics"
        self.settings.editing.audio_mixing_enabled = True
        library_dir = self.temp_root / "bgm-library-diagnostics"
        library_dir.mkdir(parents=True, exist_ok=True)
        valid_path = library_dir / "tutorial-only.wav"
        valid_path.write_text("audio", encoding="utf-8")
        library_path = library_dir / "library.json"
        library_path.write_text(
            json.dumps(
                {
                    "tracks": [
                        "bad-entry",
                        {"path": ""},
                        {"path": "missing.wav", "tags": ["robot"]},
                        {
                            "path": valid_path.name,
                            "tags": ["tutorial"],
                            "phase": "custom",
                            "mood": "custom",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.settings.editing.bgm_library_path = library_path
        self._append_subtitle(
            session_id,
            "1\n00:00:02,000 --> 00:00:04,000\n"
            "robot trick fight\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=200.0,
                    reason="highlight_keyword",
                )
            ],
            duration=600.0,
        )

        output = io.StringIO()
        with redirect_stdout(output):
            EditingPlannerService(self.settings).run()

        logs = output.getvalue()
        self.assertIn("bgm library loaded", logs)
        self.assertIn("tracks=1", logs)
        self.assertIn("total_items=4", logs)
        self.assertIn("skipped_non_object=1", logs)
        self.assertIn("skipped_missing_path=1", logs)
        self.assertIn("skipped_missing_file=1", logs)
        self.assertIn("bgm library had no match", logs)
        self.assertIn("tags=robot,chill,hype", logs)

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 1)
        self.assertTrue(plans[0].audio_beds[0].source_path.endswith("bgm-playful.wav"))

    def test_bgm_library_change_replans_existing_default_bgm_plan(self) -> None:
        session_id = "session-edit-bgm-library-replan"
        self.settings.editing.audio_mixing_enabled = True
        library_path, early_path, climax_path = self._write_bgm_library()
        self.settings.editing.bgm_library_path = library_path
        self._append_subtitle(
            session_id,
            "1\n00:00:02,000 --> 00:00:04,000\n"
            "机器人套路开团\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=200.0,
                    reason="highlight_keyword",
                )
            ],
            duration=600.0,
        )
        append_model(
            self.edit_plans_path,
            EditPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=600.0,
                timeline=[
                    TimelineSegment(
                        role="main",
                        source_start_seconds=0.0,
                        source_end_seconds=30.0,
                        reason="condensed_match_context",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=100.0,
                        source_end_seconds=200.0,
                        reason="highlight_keyword",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=570.0,
                        source_end_seconds=600.0,
                        reason="condensed_match_context",
                    ),
                ],
                audio_beds=[
                    AudioBed(
                        source_path=str(self.temp_root / "editing-audio" / "bgm-playful.wav"),
                        reason="background_music_playful",
                    )
                ],
                created_at=datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc),
            ),
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            EditPlannerStateFile(
                processed_match_keys=[f"{session_id}:1"],
            ).model_dump_json(indent=2)
            + "\n",
            encoding="utf-8",
        )

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 2)
        self.assertEqual(
            [bed.source_path for bed in plans[-1].audio_beds],
            [str(early_path), str(climax_path)],
        )

    def test_audio_timing_keeps_bgm_after_teaser(self) -> None:
        session_id = "session-edit-bgm-teaser-replan"
        self.settings.editing.audio_mixing_enabled = True
        library_path, early_path, climax_path = self._write_bgm_library()
        self.settings.editing.bgm_library_path = library_path
        self._append_subtitle(
            session_id,
            "1\n00:00:02,000 --> 00:00:04,000\n"
            "robot trick fight\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=145.0,
                    reason="highlight_keyword",
                )
            ],
            duration=600.0,
        )
        append_model(
            self.edit_plans_path,
            EditPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=600.0,
                timeline=[
                    TimelineSegment(
                        role="teaser",
                        source_start_seconds=100.0,
                        source_end_seconds=145.0,
                        reason="highlight_keyword",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=0.0,
                        source_end_seconds=30.0,
                        reason="condensed_match_context",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=100.0,
                        source_end_seconds=145.0,
                        reason="highlight_keyword",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=570.0,
                        source_end_seconds=600.0,
                        reason="condensed_match_context",
                    ),
                ],
                audio_beds=[
                    AudioBed(
                        source_path=str(early_path),
                        timeline_start_seconds=45.0,
                        timeline_end_seconds=97.5,
                        gain_db=self.settings.editing.bgm_gain_db,
                        loop=True,
                        reason="background_music_library",
                    ),
                    AudioBed(
                        source_path=str(climax_path),
                        timeline_start_seconds=97.5,
                        timeline_end_seconds=None,
                        gain_db=self.settings.editing.bgm_gain_db,
                        loop=True,
                        reason="background_music_library_climax",
                    ),
                ],
                created_at=datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc),
            ),
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            EditPlannerStateFile(
                processed_match_keys=[f"{session_id}:1"],
            ).model_dump_json(indent=2)
            + "\n",
            encoding="utf-8",
        )

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 2)
        latest = plans[-1]
        self.assertTrue(latest.audio_beds)
        self.assertTrue(
            all(Path(bed.source_path) in {early_path, climax_path} for bed in latest.audio_beds)
        )
        self.assertEqual(latest.audio_beds[0].timeline_start_seconds, 45.0)
        if len(latest.audio_beds) > 1:
            self.assertEqual(
                latest.audio_beds[0].timeline_end_seconds,
                latest.audio_beds[1].timeline_start_seconds,
            )
            self.assertGreater(latest.audio_beds[1].timeline_start_seconds, 45.0)

    def test_audio_mixing_marks_main_key_event_without_fallback_teaser(self) -> None:
        session_id = "session-edit-main-sfx"
        self.settings.editing.audio_mixing_enabled = True
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=120.0,
                    ended_at_seconds=150.0,
                    reason="condensed_key_event",
                )
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertTrue(all(segment.role != "teaser" for segment in plan.timeline))
        key_segments = [
            segment for segment in plan.timeline if segment.reason == "condensed_key_event"
        ]
        self.assertEqual(len(key_segments), 1)
        self.assertEqual(len(plan.sound_effects), 1)
        self.assertTrue(
            all(hit.source_path.endswith("wow.wav") for hit in plan.sound_effects)
        )
        self.assertEqual(
            [hit.at_seconds for hit in plan.sound_effects],
            [30.0],
        )
        self.assertTrue(
            all(hit.reason == "condensed_key_event" for hit in plan.sound_effects)
        )

    def test_audio_mixing_skips_bgm_when_source_already_has_music(self) -> None:
        session_id = "session-edit-source-music"
        self.settings.editing.audio_mixing_enabled = True
        recording_path = self._append_recording(session_id)
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=130.0,
                    reason="highlight_keyword",
                )
            ],
            duration=600.0,
        )
        seen: dict[str, object] = {}

        def _detector(
            source_path: Path,
            *,
            start_seconds: float,
            end_seconds: float,
        ) -> SourceMusicDetection:
            seen["source_path"] = source_path
            seen["start_seconds"] = start_seconds
            seen["end_seconds"] = end_seconds
            return SourceMusicDetection(
                has_music=True,
                confidence=0.91,
                reason="persistent_music_like_audio",
            )

        EditingPlannerService(
            self.settings,
            source_bgm_detector=_detector,
        ).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(seen["source_path"], recording_path)
        self.assertEqual(seen["start_seconds"], 0.0)
        self.assertEqual(seen["end_seconds"], 600.0)
        self.assertEqual(plan.audio_beds, [])
        self.assertEqual(len(plan.sound_effects), 2)
        self.assertTrue(all(hit.source_path.endswith("wow.wav") for hit in plan.sound_effects))

    def test_source_music_detection_resolves_chunked_recording_spans(self) -> None:
        session_id = "session-edit-source-music-chunked"
        self.settings.editing.audio_mixing_enabled = True
        first_chunk, second_chunk = self._append_chunked_recording(session_id)
        self._append_boundary(session_id, duration=20.0)
        append_model(
            self.highlight_plans_path,
            HighlightPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=20.0,
                windows=[
                    HighlightClipWindow(
                        started_at_seconds=0.0,
                        ended_at_seconds=5.0,
                        reason="condensed_match_context",
                    ),
                    HighlightClipWindow(
                        started_at_seconds=8.0,
                        ended_at_seconds=12.0,
                        reason="highlight_keyword",
                    ),
                    HighlightClipWindow(
                        started_at_seconds=15.0,
                        ended_at_seconds=20.0,
                        reason="condensed_match_context",
                    ),
                ],
                created_at=datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc),
            ),
        )
        seen: list[tuple[Path, float, float]] = []

        def _detector(
            source_path: Path,
            *,
            start_seconds: float,
            end_seconds: float,
        ) -> SourceMusicDetection:
            seen.append((source_path, start_seconds, end_seconds))
            return SourceMusicDetection(
                has_music=True,
                confidence=0.91,
                reason="persistent_music_like_audio",
            )

        EditingPlannerService(
            self.settings,
            source_bgm_detector=_detector,
        ).run()

        self.assertEqual(
            seen,
            [
                (first_chunk, 0.0, 10.0),
                (second_chunk, 0.0, 10.0),
            ],
        )
        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].audio_beds, [])

    def test_existing_bgm_plan_is_replanned_when_source_music_is_detected(self) -> None:
        session_id = "session-edit-source-music-replan"
        self.settings.editing.audio_mixing_enabled = True
        self._append_recording(session_id)
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=130.0,
                    reason="highlight_keyword",
                )
            ],
            duration=600.0,
        )
        append_model(
            self.edit_plans_path,
            EditPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=600.0,
                timeline=[
                    TimelineSegment(
                        role="main",
                        source_start_seconds=0.0,
                        source_end_seconds=30.0,
                        reason="condensed_match_context",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=100.0,
                        source_end_seconds=130.0,
                        reason="highlight_keyword",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=570.0,
                        source_end_seconds=600.0,
                        reason="condensed_match_context",
                    ),
                ],
                audio_beds=[
                    AudioBed(
                        source_path=str(self.temp_root / "audio" / "old-bgm.wav"),
                        reason="background_music_playful",
                    )
                ],
                created_at=datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc),
            ),
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            EditPlannerStateFile(
                processed_match_keys=[f"{session_id}:1"],
            ).model_dump_json(indent=2)
            + "\n",
            encoding="utf-8",
        )

        EditingPlannerService(
            self.settings,
            source_bgm_detector=lambda *args, **kwargs: SourceMusicDetection(
                has_music=True,
                confidence=0.9,
                reason="persistent_music_like_audio",
            ),
        ).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 2)
        self.assertEqual(plans[-1].audio_beds, [])
        self.assertEqual(len(plans[-1].sound_effects), 2)

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
        self.assertEqual(len(plans[0].timeline), 4)
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

    def test_legacy_full_main_edit_plan_is_replanned_without_force(self) -> None:
        session_id = "session-edit-legacy-main"
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
        append_model(
            self.edit_plans_path,
            EditPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=600.0,
                timeline=[
                    TimelineSegment(
                        role="teaser",
                        source_start_seconds=100.0,
                        source_end_seconds=110.0,
                        reason="highlight_keyword",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=0.0,
                        source_end_seconds=600.0,
                        reason="full_validated_match",
                    ),
                ],
                created_at=datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc),
            ),
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            EditPlannerStateFile(
                processed_match_keys=[f"{session_id}:1"],
            ).model_dump_json(indent=2)
            + "\n",
            encoding="utf-8",
        )

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 2)
        latest = plans[-1]
        self.assertTrue(
            all(segment.reason != "full_validated_match" for segment in latest.timeline)
        )
        self.assertGreater(
            sum(1 for segment in latest.timeline if segment.role == "main"),
            1,
        )

    def test_legacy_main_without_zoom_is_replanned_when_zoom_enabled(self) -> None:
        session_id = "session-edit-legacy-main-zoom"
        self.settings.editing.zoom_enabled = True
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=120.0,
                    ended_at_seconds=150.0,
                    reason="condensed_key_event",
                )
            ],
            duration=600.0,
        )
        append_model(
            self.edit_plans_path,
            EditPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=600.0,
                timeline=[
                    TimelineSegment(
                        role="main",
                        source_start_seconds=0.0,
                        source_end_seconds=30.0,
                        reason="condensed_match_context",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=120.0,
                        source_end_seconds=150.0,
                        reason="condensed_key_event",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=570.0,
                        source_end_seconds=600.0,
                        reason="condensed_match_context",
                    ),
                ],
                created_at=datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc),
            ),
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            EditPlannerStateFile(
                processed_match_keys=[f"{session_id}:1"],
            ).model_dump_json(indent=2)
            + "\n",
            encoding="utf-8",
        )

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 2)
        latest = plans[-1]
        self.assertTrue(all(segment.role != "teaser" for segment in latest.timeline))
        latest_key_segments = [
            segment for segment in latest.timeline if segment.reason == "condensed_key_event"
        ]
        self.assertEqual(len(latest_key_segments), 1)
        self.assertIsNotNone(latest_key_segments[0].transform)
        assert latest_key_segments[0].transform is not None
        self.assertEqual(latest_key_segments[0].transform.kind, "punch_in")
        self.assertTrue(
            all(
                segment.transform is None
                for segment in latest.timeline
                if segment is not latest_key_segments[0]
            )
        )

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
        include_edges: bool = True,
    ) -> None:
        persisted_windows = list(windows)
        if include_edges:
            persisted_windows = [
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=min(30.0, duration / 2.0),
                    reason="condensed_match_context",
                ),
                *persisted_windows,
                HighlightClipWindow(
                    started_at_seconds=max(0.0, duration - 30.0),
                    ended_at_seconds=duration,
                    reason="condensed_match_context",
                ),
            ]
        append_model(
            self.highlight_plans_path,
            HighlightPlanAsset(
                session_id=session_id,
                match_index=match_index,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=duration,
                windows=persisted_windows,
                created_at=datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc),
            ),
        )

    def _append_recording(self, session_id: str) -> Path:
        recording_path = self.temp_root / "raw" / session_id / "recording-source.mp4"
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_text("recording", encoding="utf-8")
        append_model(
            self.temp_root / "recording-assets.jsonl",
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.DIRECT_STREAM,
                path=str(recording_path),
                started_at=datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 6, 26, 12, 30, tzinfo=timezone.utc),
            ),
        )
        return recording_path

    def _append_chunked_recording(self, session_id: str) -> tuple[Path, Path]:
        raw_dir = self.temp_root / "raw" / session_id
        chunk_dir = raw_dir / "chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        first_chunk = chunk_dir / "recording-00000.mp4"
        second_chunk = chunk_dir / "recording-00001.mp4"
        first_chunk.write_text("chunk 0", encoding="utf-8")
        second_chunk.write_text("chunk 1", encoding="utf-8")
        manifest_path = raw_dir / "recording-chunks.json"
        manifest = RecordingChunkManifest(
            session_id=session_id,
            source_type=SourceType.DIRECT_STREAM,
            path=str(manifest_path),
            started_at=datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc),
            ended_at=datetime(2026, 6, 26, 12, 30, tzinfo=timezone.utc),
            chunks=[
                RecordingChunk(
                    path="chunks/recording-00000.mp4",
                    started_at_seconds=0.0,
                    ended_at_seconds=10.0,
                    duration_seconds=10.0,
                    index=0,
                ),
                RecordingChunk(
                    path="chunks/recording-00001.mp4",
                    started_at_seconds=10.0,
                    ended_at_seconds=20.0,
                    duration_seconds=10.0,
                    index=1,
                ),
            ],
            created_at=datetime(2026, 6, 26, 12, 31, tzinfo=timezone.utc),
        )
        manifest_path.write_text(
            manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        append_model(
            self.temp_root / "recording-assets.jsonl",
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.DIRECT_STREAM,
                path=str(manifest_path),
                started_at=datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 6, 26, 12, 30, tzinfo=timezone.utc),
            ),
        )
        return first_chunk, second_chunk

    def _append_subtitle(self, session_id: str, content: str) -> Path:
        subtitle_path = self.temp_root / "processed" / session_id / "match-01.srt"
        subtitle_path.parent.mkdir(parents=True, exist_ok=True)
        subtitle_path.write_text(content, encoding="utf-8")
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        return subtitle_path

    def _write_bgm_library(self) -> tuple[Path, Path, Path]:
        library_dir = self.temp_root / "bgm-library"
        library_dir.mkdir(parents=True, exist_ok=True)
        early_path = library_dir / "robot-playful.wav"
        climax_path = library_dir / "robot-hype.wav"
        generic_path = library_dir / "generic.wav"
        for path in (early_path, climax_path, generic_path):
            path.write_text("audio", encoding="utf-8")
        library_path = library_dir / "library.json"
        library_path.write_text(
            json.dumps(
                {
                    "tracks": [
                        {
                            "path": early_path.name,
                            "tags": ["robot", "trick", "statikk"],
                            "phase": "early",
                            "energy": 2,
                        },
                        {
                            "path": climax_path.name,
                            "tags": ["robot", "hype"],
                            "phase": "climax",
                            "energy": 5,
                        },
                        {
                            "path": generic_path.name,
                            "tags": ["chill"],
                            "phase": "early",
                            "energy": 1,
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return library_path, early_path, climax_path

    def _write_chinese_bgm_library(self) -> tuple[Path, Path, Path]:
        library_dir = self.temp_root / "bgm-library-cn"
        library_dir.mkdir(parents=True, exist_ok=True)
        early_path = library_dir / "robot-cn-playful.wav"
        climax_path = library_dir / "robot-cn-hype.wav"
        generic_path = library_dir / "generic-cn.wav"
        for path in (early_path, climax_path, generic_path):
            path.write_text("audio", encoding="utf-8")
        library_path = library_dir / "library.json"
        library_path.write_text(
            json.dumps(
                {
                    "tracks": [
                        {
                            "path": early_path.name,
                            "tags": ["机器人", "套路", "电刀"],
                            "phase": "前期",
                            "mood": "俏皮",
                            "energy": 2,
                        },
                        {
                            "path": climax_path.name,
                            "tags": ["机器人", "团战"],
                            "phase": "高潮",
                            "mood": "燃",
                            "energy": 5,
                        },
                        {
                            "path": generic_path.name,
                            "tags": ["轻松"],
                            "phase": "通用",
                            "mood": "轻松",
                            "energy": 1,
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return library_path, early_path, climax_path


if __name__ == "__main__":
    unittest.main()
