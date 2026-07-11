from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from arl.config import EditingSettings, Settings, StorageSettings
from arl.copywriter.models import CopywriterSemanticAsset, LlmCopywritingResult
from arl.editing.models import EditPlannerStateFile
from arl.editing.audio import (
    BgmLibraryTrack,
    BgmSelectionContext,
    SourceMusicDetection,
    SourceMusicSpan,
    select_bgm_tracks,
)
from arl.editing.service import EditingPlannerService
from arl.shared.contracts import (
    AudioBed,
    EditPlanAsset,
    HighlightClipWindow,
    HighlightPlanAsset,
    KdaEventCue,
    MatchBoundary,
    RecordingAsset,
    RecordingChunk,
    RecordingChunkManifest,
    SourceType,
    SubtitleAsset,
    TimelineSegment,
    TimelineVideoTransform,
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
                sfx_library_path=root / "sfx" / "library.json",
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
                ("teaser", 300.0, 320.0),
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

    def test_teaser_prefers_high_signal_subtitle_over_earlier_highlight(self) -> None:
        session_id = "session-edit-teaser-score"
        self._append_boundary(session_id, duration=600.0)
        self._append_subtitle(
            session_id,
            "1\n00:01:20,000 --> 00:01:25,000\n普通补刀先发育\n\n"
            "2\n00:05:00,000 --> 00:05:05,000\n"
            "上单电刀AP机器人 清线快伤害高 单杀打开局面\n",
        )
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=80.0,
                    ended_at_seconds=95.0,
                    reason="highlight_keyword",
                ),
                HighlightClipWindow(
                    started_at_seconds=300.0,
                    ended_at_seconds=315.0,
                    reason="highlight_keyword",
                ),
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        teaser_segments = [
            segment for segment in plan.timeline if segment.role == "teaser"
        ]
        self.assertEqual(
            [
                (segment.source_start_seconds, segment.source_end_seconds)
                for segment in teaser_segments
            ],
            [(300.0, 315.0), (80.0, 85.0)],
        )

    def test_semantic_teaser_recommendation_overrides_fallback_teaser(self) -> None:
        session_id = "session-edit-llm-teaser"
        self.settings.editing.zoom_enabled = True
        self.settings.editing.zoom_mode = "legacy"
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=80.0,
                    ended_at_seconds=95.0,
                    reason="highlight_keyword",
                ),
                HighlightClipWindow(
                    started_at_seconds=300.0,
                    ended_at_seconds=330.0,
                    reason="highlight_keyword",
                ),
            ],
            duration=600.0,
            kda_events=[
                KdaEventCue(
                    started_at_seconds=304.0,
                    ended_at_seconds=312.0,
                    text=(
                        "kda_change kills=1->2 deaths=0->0 "
                        "previous_at=305.000 current_at=310.000"
                    ),
                )
            ],
        )
        self._append_semantic_asset(
            session_id,
            teaser_start=305.0,
            teaser_end=313.0,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        teaser = [segment for segment in plan.timeline if segment.role == "teaser"]
        self.assertEqual(len(teaser), 1)
        self.assertEqual(teaser[0].source_start_seconds, 305.0)
        self.assertEqual(teaser[0].source_end_seconds, 313.0)
        self.assertEqual(teaser[0].reason, "llm_teaser")
        self.assertIsNotNone(teaser[0].transform)

    def test_unanchored_semantic_teaser_is_rejected_and_omission_recorded(self) -> None:
        session_id = "session-edit-llm-unanchored-teaser"
        self.settings.editing.teaser_fallback_enabled = False
        self.settings.editing.teaser_candidate_reasons = ("highlight_keyword",)
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=300.0,
                    ended_at_seconds=330.0,
                    reason="condensed_key_event",
                ),
            ],
            duration=600.0,
        )
        self._append_semantic_asset(
            session_id,
            teaser_start=305.0,
            teaser_end=313.0,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        teaser = [segment for segment in plan.timeline if segment.role == "teaser"]
        self.assertEqual(teaser, [])
        self.assertEqual(plan.teaser_omitted_reason, "no_high_confidence_teaser")

    def test_semantic_teaser_on_highlight_keyword_window_is_accepted(self) -> None:
        session_id = "session-edit-llm-highlight-keyword-teaser"
        self.settings.editing.teaser_fallback_enabled = False
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=300.0,
                    ended_at_seconds=330.0,
                    reason="highlight_keyword",
                ),
            ],
            duration=600.0,
        )
        self._append_semantic_asset(
            session_id,
            teaser_start=305.0,
            teaser_end=313.0,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        teaser = [segment for segment in plan.timeline if segment.role == "teaser"]
        self.assertEqual(len(teaser), 1)
        self.assertEqual(teaser[0].source_start_seconds, 305.0)
        self.assertEqual(teaser[0].source_end_seconds, 313.0)
        self.assertEqual(teaser[0].reason, "llm_teaser")
        self.assertIsNone(plan.teaser_omitted_reason)

    def test_active_no_strong_story_omits_teaser_with_reason(self) -> None:
        session_id = "session-edit-no-strong-story"
        self.settings.llm.story_analysis_enabled = True
        self.settings.llm.story_shadow_mode = False
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=300.0,
                    ended_at_seconds=330.0,
                    reason="highlight_keyword",
                ),
            ],
            duration=600.0,
        )
        self._append_semantic_asset(
            session_id,
            teaser_start=305.0,
            teaser_end=313.0,
            story_status="no_strong_story",
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(
            [segment for segment in plan.timeline if segment.role == "teaser"],
            [],
        )
        self.assertEqual(plan.teaser_omitted_reason, "no_strong_story")

    def test_story_shadow_asset_is_not_used_by_edit_planner(self) -> None:
        self.settings.llm.story_analysis_enabled = True
        self.settings.llm.story_shadow_mode = True
        service = EditingPlannerService(self.settings)
        asset = CopywriterSemanticAsset(
            session_id="session-edit-shadow",
            match_index=1,
            source_subtitle_path="match-01.srt",
            provider="test",
            model="test",
            prompt_fingerprint="prompt",
            input_fingerprint="input",
            result=LlmCopywritingResult(
                title_candidates=["标题一", "标题二", "标题三"],
                recommended_title="标题一",
                cover_lines=["影子故事", "仅供比较"],
                summary="影子摘要。",
                description="影子描述。",
                tags=["英雄联盟", "直播切片", "影子", "比较", "测试"],
                story_status="no_strong_story",
            ),
            status="generated",
            created_at=datetime.now(timezone.utc),
        )

        self.assertIsNone(service._semantic_asset_for_editing(asset))

    def test_invalid_semantic_teaser_recommendation_falls_back(self) -> None:
        session_id = "session-edit-llm-invalid-teaser"
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=80.0,
                    ended_at_seconds=95.0,
                    reason="highlight_keyword",
                )
            ],
            duration=600.0,
        )
        self._append_semantic_asset(
            session_id,
            teaser_start=300.0,
            teaser_end=310.0,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        teaser = [segment for segment in plan.timeline if segment.role == "teaser"]
        self.assertEqual(
            [(segment.source_start_seconds, segment.source_end_seconds, segment.reason) for segment in teaser],
            [(80.0, 95.0, "highlight_keyword")],
        )

    def test_black_card_transition_uses_semantic_hook_line(self) -> None:
        session_id = "session-edit-transition-hook"
        self.settings.editing.transition_mode = "black_card"
        self.settings.editing.transition_duration_seconds = 1.5
        self.settings.editing.transition_text = "Back to match start"
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=30.0,
                    reason="condensed_match_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=300.0,
                    ended_at_seconds=312.0,
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
        self._append_semantic_asset(
            session_id,
            teaser_start=300.0,
            teaser_end=312.0,
            hook_line="Hook from LLM",
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(
            [segment.role for segment in plan.timeline[:3]],
            ["teaser", "transition", "main"],
        )
        transition = plan.timeline[1]
        self.assertEqual(transition.reason, "transition_black_card")
        self.assertEqual(transition.duration_seconds, 1.5)
        self.assertEqual(transition.text, "Hook from LLM")
        self.assertEqual(transition.source_start_seconds, 0.0)
        self.assertEqual(transition.source_end_seconds, 0.0)

    def test_transition_mode_none_preserves_no_card_shape(self) -> None:
        session_id = "session-edit-transition-none"
        self.settings.editing.transition_mode = "none"
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=30.0,
                    reason="condensed_match_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=300.0,
                    ended_at_seconds=312.0,
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

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertNotIn("transition", [segment.role for segment in plan.timeline])

    def test_dynamic_teaser_budget_caps_total_duration(self) -> None:
        session_id = "session-edit-dynamic-teaser-budget"
        self.settings.editing.teaser_max_segments = 3
        self.settings.editing.teaser_max_total_seconds = 90.0
        self.settings.editing.teaser_budget_min_seconds = 20.0
        self.settings.editing.teaser_budget_max_seconds = 90.0
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=180.0,
                    reason="condensed_match_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=220.0,
                    ended_at_seconds=260.0,
                    reason="highlight_keyword",
                ),
                HighlightClipWindow(
                    started_at_seconds=320.0,
                    ended_at_seconds=360.0,
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

        teaser_segments = [
            segment
            for segment in load_models(self.edit_plans_path, EditPlanAsset)[0].timeline
            if segment.role == "teaser"
        ]
        total = sum(
            segment.source_end_seconds - segment.source_start_seconds
            for segment in teaser_segments
        )
        self.assertLessEqual(total, 29.0)
        self.assertEqual(
            [(segment.source_start_seconds, segment.source_end_seconds) for segment in teaser_segments],
            [(220.0, 249.0)],
        )

    def test_zoom_enabled_marks_high_signal_segments_with_budget(self) -> None:
        session_id = "session-edit-zoom"
        self.settings.editing.zoom_enabled = True
        self.settings.editing.zoom_mode = "legacy"
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
        self.assertEqual(first_teaser.transform.ease_in_seconds, 0.0)
        self.assertEqual(first_teaser.transform.ease_out_seconds, 0.0)
        self.assertIsNone(second_teaser.transform)
        self.assertIsNone(first_main.transform)

    def test_planner_emits_fallback_teaser_for_generic_condensed_key_events(self) -> None:
        session_id = "session-edit-fallback-teaser"
        self.settings.editing.zoom_enabled = True
        self.settings.editing.zoom_mode = "legacy"
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
        self.assertEqual(plan.timeline[0].role, "teaser")
        self.assertEqual(plan.timeline[0].reason, "teaser_fallback_top_scored")
        self.assertEqual(plan.timeline[0].source_start_seconds, 120.0)
        self.assertEqual(plan.timeline[0].source_end_seconds, 140.0)
        self.assertIsNotNone(plan.timeline[0].transform)
        key_segments = [
            segment for segment in plan.timeline if segment.reason == "condensed_key_event"
        ]
        self.assertEqual(len(key_segments), 1)
        self.assertEqual(key_segments[0].source_start_seconds, 120.0)
        self.assertEqual(key_segments[0].source_end_seconds, 150.0)
        self.assertIsNone(key_segments[0].transform)
        self.assertTrue(
            all(
                segment.transform is None
                for segment in plan.timeline
                if segment is not plan.timeline[0]
            )
        )
        self.assertEqual(plan.sound_effects, [])
        self.assertTrue(any(segment.source_start_seconds == 0.0 for segment in plan.timeline))
        self.assertTrue(any(segment.source_end_seconds == 600.0 for segment in plan.timeline))

    def test_zoom_splits_long_main_segment_into_short_closeup(self) -> None:
        session_id = "session-edit-long-main-zoom"
        self.settings.editing.zoom_fallback_enabled = True
        self.settings.editing.zoom_enabled = True
        self.settings.editing.teaser_max_segments = 0
        self._append_boundary(session_id, duration=700.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=30.0,
                    reason="condensed_match_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=220.0,
                    reason="condensed_key_event",
                ),
                HighlightClipWindow(
                    started_at_seconds=670.0,
                    ended_at_seconds=700.0,
                    reason="condensed_match_context",
                ),
            ],
            duration=700.0,
        )

        EditingPlannerService(self.settings).run()

        plans = load_models(self.edit_plans_path, EditPlanAsset)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        key_segments = [
            segment for segment in plan.timeline if segment.reason == "condensed_key_event"
        ]
        self.assertEqual(
            round(
                sum(
                    segment.source_end_seconds - segment.source_start_seconds
                    for segment in key_segments
                ),
                3,
            ),
            120.0,
        )
        transformed = [
            segment for segment in key_segments if segment.transform is not None
        ]
        self.assertEqual(len(transformed), 1)
        closeup = transformed[0]
        self.assertEqual(
            [
                (segment.source_start_seconds, segment.source_end_seconds)
                for segment in key_segments
            ],
            [(100.0, 157.0), (157.0, 163.0), (163.0, 220.0)],
        )
        self.assertLessEqual(
            closeup.source_end_seconds - closeup.source_start_seconds,
            self.settings.editing.zoom_closeup_seconds,
        )
        assert closeup.transform is not None
        self.assertEqual(closeup.transform.target, "chat")
        self.assertEqual(closeup.transform.ease_in_seconds, 0.4)

    def test_zoom_default_targets_bottom_left_chat_area(self) -> None:
        session_id = "session-edit-chat-zoom"
        self.settings.editing.zoom_fallback_enabled = True
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
        transformed = [
            segment for segment in plans[0].timeline if segment.transform is not None
        ]
        self.assertEqual(len(transformed), 1)
        transform = transformed[0].transform
        assert transform is not None
        self.assertEqual(transform.target, "chat")
        self.assertEqual(transform.x_anchor, 0.0)
        self.assertEqual(transform.y_anchor, 1.0)
        self.assertLessEqual(
            transformed[0].source_end_seconds - transformed[0].source_start_seconds,
            self.settings.editing.zoom_closeup_seconds,
        )

    def test_zoom_kda_kill_splits_main_segment_with_center_target(self) -> None:
        session_id = "session-edit-kda-zoom"
        self.settings.editing.zoom_enabled = True
        self.settings.editing.teaser_max_segments = 0
        self._append_subtitle(
            session_id,
            "1\n00:02:04,000 --> 00:02:06,000\n"
            "kda_change kills=2->3 deaths=0->0 previous_at=110.000 current_at=125.000\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=150.0,
                    reason="condensed_key_event",
                )
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        key_segments = [
            segment for segment in plan.timeline if segment.reason == "condensed_key_event"
        ]
        self.assertEqual(
            round(
                sum(
                    segment.source_end_seconds - segment.source_start_seconds
                    for segment in key_segments
                ),
                3,
            ),
            50.0,
        )
        transformed = [
            segment for segment in key_segments if segment.transform is not None
        ]
        self.assertEqual(len(transformed), 1)
        closeup = transformed[0]
        self.assertLessEqual(
            closeup.source_end_seconds - closeup.source_start_seconds,
            self.settings.editing.zoom_closeup_seconds,
        )
        self.assertLessEqual(closeup.source_start_seconds, 125.0)
        self.assertGreaterEqual(closeup.source_end_seconds, 125.0)
        transform = closeup.transform
        assert transform is not None
        self.assertEqual(transform.target, "center")
        self.assertEqual(transform.x_anchor, 0.5)
        self.assertEqual(transform.y_anchor, 0.5)
        self.assertEqual(transform.ease_in_seconds, 0.4)

    def test_zoom_chat_burst_uses_injected_sampler_and_chat_target(self) -> None:
        session_id = "session-edit-chat-burst-zoom"
        self.settings.editing.zoom_enabled = True
        self.settings.editing.teaser_max_segments = 0
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
        frame_a = np.zeros((100, 100, 3), dtype=np.uint8)
        frame_b = frame_a.copy()
        frame_b[55:95, 0:36] = 255
        sampler_calls: list[tuple[Path, float, float, float]] = []

        def _sampler(
            path: Path,
            start_seconds: float,
            end_seconds: float,
            *,
            interval_seconds: float,
        ) -> list[tuple[float, np.ndarray]]:
            sampler_calls.append((path, start_seconds, end_seconds, interval_seconds))
            return [(100.0, frame_a), (105.0, frame_b), (110.0, frame_b)]

        EditingPlannerService(
            self.settings,
            chat_frame_sampler=_sampler,
        ).run()

        self.assertEqual(len(sampler_calls), 1)
        _path, start_seconds, end_seconds, interval_seconds = sampler_calls[0]
        self.assertEqual(start_seconds, 100.0)
        self.assertEqual(end_seconds, 130.0)
        self.assertEqual(interval_seconds, 0.5)
        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        transformed = [
            segment for segment in plan.timeline if segment.transform is not None
        ]
        self.assertEqual(len(transformed), 1)
        closeup = transformed[0]
        self.assertLessEqual(closeup.source_start_seconds, 105.0)
        self.assertGreaterEqual(closeup.source_end_seconds, 105.0)
        transform = closeup.transform
        assert transform is not None
        self.assertEqual(transform.target, "chat")
        self.assertEqual(transform.x_anchor, 0.0)
        self.assertEqual(transform.y_anchor, 1.0)

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
        sfx_path = self.temp_root / "audio" / "coin.wav"
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
            [(0.0, "highlight_keyword"), (65.0, "highlight_keyword")],
        )
        for hit in plan.sound_effects:
            self.assertEqual(hit.source_path, str(sfx_path))
            self.assertEqual(hit.gain_db, -9.0)

    def test_audio_mixing_does_not_emit_sfx_for_tactical_windows(self) -> None:
        session_id = "session-edit-tactical-sfx"
        sfx_path = self.temp_root / "audio" / "coin.wav"
        sfx_path.parent.mkdir(parents=True, exist_ok=True)
        sfx_path.write_text("fake sfx", encoding="utf-8")
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.sfx_path = sfx_path
        self.settings.editing.teaser_fallback_enabled = False
        self.settings.editing.teaser_candidate_reasons = ("highlight_keyword",)
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=120.0,
                    ended_at_seconds=140.0,
                    reason="condensed_tactical",
                )
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(plan.sound_effects, [])

    def test_audio_mixing_aligns_sfx_to_kda_kill_timestamp(self) -> None:
        session_id = "session-edit-kda-sfx"
        sfx_path = self.temp_root / "audio" / "coin.wav"
        sfx_path.parent.mkdir(parents=True, exist_ok=True)
        sfx_path.write_text("fake sfx", encoding="utf-8")
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.sfx_path = sfx_path
        self.settings.editing.teaser_max_segments = 0
        self._append_subtitle(
            session_id,
            "1\n00:02:04,000 --> 00:02:06,000\n"
            "kda_change kills=2->3 deaths=0->0 previous_at=110.000 current_at=125.000\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=150.0,
                    reason="condensed_key_event",
                )
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(len(plan.sound_effects), 1)
        self.assertEqual(plan.sound_effects[0].source_path, str(sfx_path))
        self.assertEqual(plan.sound_effects[0].at_seconds, 55.0)
        self.assertEqual(plan.sound_effects[0].reason, "condensed_key_event")

    def test_audio_mixing_aligns_sfx_to_highlight_plan_kda_events(self) -> None:
        """Real pipelines never write kda_change lines into SRT files; the
        planner's OCR events arrive via HighlightPlanAsset.kda_events and must
        drive SFX alignment the same way."""
        session_id = "session-edit-plan-kda-sfx"
        sfx_path = self.temp_root / "audio" / "coin.wav"
        sfx_path.parent.mkdir(parents=True, exist_ok=True)
        sfx_path.write_text("fake sfx", encoding="utf-8")
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.sfx_path = sfx_path
        self.settings.editing.teaser_max_segments = 0
        self._append_subtitle(
            session_id,
            "1\n00:02:04,000 --> 00:02:06,000\nordinary speech line\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=150.0,
                    reason="condensed_key_event",
                )
            ],
            duration=600.0,
            kda_events=[
                KdaEventCue(
                    started_at_seconds=110.0,
                    ended_at_seconds=130.0,
                    text=(
                        "kda_change kills=2->3 deaths=0->0 "
                        "previous_at=110.000 current_at=125.000"
                    ),
                )
            ],
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(len(plan.sound_effects), 1)
        self.assertEqual(plan.sound_effects[0].source_path, str(sfx_path))
        self.assertEqual(plan.sound_effects[0].at_seconds, 55.0)
        self.assertEqual(plan.sound_effects[0].reason, "condensed_key_event")

    def test_audio_mixing_does_not_emit_sfx_for_death_only_kda(self) -> None:
        session_id = "session-edit-death-only-sfx"
        sfx_path = self.temp_root / "audio" / "coin.wav"
        sfx_path.parent.mkdir(parents=True, exist_ok=True)
        sfx_path.write_text("fake sfx", encoding="utf-8")
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.sfx_path = sfx_path
        self.settings.editing.teaser_max_segments = 0
        self._append_subtitle(
            session_id,
            "1\n00:02:04,000 --> 00:02:06,000\n"
            "kda_change kills=2->2 deaths=0->1 previous_at=110.000 current_at=125.000\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=150.0,
                    reason="condensed_key_event",
                )
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(plan.sound_effects, [])

    def test_audio_mixing_does_not_map_sfx_for_kda_in_trimmed_gap(self) -> None:
        session_id = "session-edit-gap-kda-sfx"
        sfx_path = self.temp_root / "audio" / "coin.wav"
        sfx_path.parent.mkdir(parents=True, exist_ok=True)
        sfx_path.write_text("fake sfx", encoding="utf-8")
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.sfx_path = sfx_path
        self.settings.editing.teaser_max_segments = 0
        self._append_subtitle(
            session_id,
            "1\n00:05:00,000 --> 00:05:02,000\n"
            "kda_change kills=2->3 deaths=0->0 previous_at=280.000 current_at=300.000\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(session_id, windows=[], duration=600.0)

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(plan.sound_effects, [])

    def test_audio_mixing_selects_multikill_sfx_variant(self) -> None:
        session_id = "session-edit-multikill-sfx"
        library_path, kill_path, multi_path = self._write_sfx_library()
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.sfx_library_path = library_path
        self.settings.editing.teaser_max_segments = 0
        self._append_subtitle(
            session_id,
            "1\n00:02:04,000 --> 00:02:06,000\n"
            "kda_change kills=2->4 deaths=0->0 previous_at=110.000 current_at=125.000\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=150.0,
                    reason="condensed_key_event",
                )
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(len(plan.sound_effects), 1)
        self.assertEqual(plan.sound_effects[0].source_path, str(multi_path))
        self.assertNotEqual(plan.sound_effects[0].source_path, str(kill_path))
        self.assertEqual(plan.sound_effects[0].gain_db, -7.0)

    def test_audio_mixing_falls_back_to_kill_coin_when_multikill_track_missing(self) -> None:
        session_id = "session-edit-multikill-fallback-sfx"
        library_dir = self.temp_root / "sfx-library-no-multi"
        library_dir.mkdir(parents=True, exist_ok=True)
        kill_path = library_dir / "coin.wav"
        kill_path.write_text("fake kill", encoding="utf-8")
        library_path = library_dir / "library.json"
        library_path.write_text(
            json.dumps(
                {"tracks": [{"category": "kill_coin", "path": kill_path.name}]}
            ),
            encoding="utf-8",
        )
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.sfx_library_path = library_path
        self.settings.editing.teaser_max_segments = 0
        self._append_subtitle(
            session_id,
            "1\n00:02:04,000 --> 00:02:06,000\n"
            "kda_change kills=2->4 deaths=0->0 previous_at=110.000 current_at=125.000\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=150.0,
                    reason="condensed_key_event",
                )
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(len(plan.sound_effects), 1)
        self.assertEqual(plan.sound_effects[0].source_path, str(kill_path))

    def test_audio_mixing_emits_teaser_impact_from_library(self) -> None:
        session_id = "session-edit-teaser-impact-sfx"
        library_dir = self.temp_root / "sfx-library-teaser-impact"
        library_dir.mkdir(parents=True, exist_ok=True)
        impact_path = library_dir / "impact.wav"
        impact_path.write_text("fake impact", encoding="utf-8")
        library_path = library_dir / "library.json"
        library_path.write_text(
            json.dumps(
                {
                    "tracks": [
                        {
                            "category": "teaser_impact",
                            "path": impact_path.name,
                            "gain_db": -2.0,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.sfx_library_path = library_path
        self._append_subtitle(
            session_id,
            "1\n00:02:04,000 --> 00:02:06,000\n"
            "很好玩真的超神了\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=150.0,
                    reason="highlight_keyword",
                )
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(plan.timeline[0].role, "teaser")
        impact_hits = [
            hit for hit in plan.sound_effects if hit.reason == "teaser_impact"
        ]
        self.assertEqual(len(impact_hits), 1)
        self.assertEqual(impact_hits[0].source_path, str(impact_path))
        self.assertEqual(impact_hits[0].at_seconds, 0.0)
        self.assertEqual(impact_hits[0].gain_db, -2.0)

    def test_audio_mixing_skips_teaser_impact_without_library_track(self) -> None:
        session_id = "session-edit-teaser-no-impact-sfx"
        library_path, _kill_path, _multi_path = self._write_sfx_library()
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.sfx_library_path = library_path
        self._append_subtitle(
            session_id,
            "1\n00:02:04,000 --> 00:02:06,000\n"
            "很好玩真的超神了\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=150.0,
                    reason="highlight_keyword",
                )
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(plan.timeline[0].role, "teaser")
        self.assertEqual(
            [hit for hit in plan.sound_effects if hit.reason == "teaser_impact"],
            [],
        )

    def test_audio_mixing_maps_repeated_kda_event_to_first_rendered_occurrence(self) -> None:
        session_id = "session-edit-teaser-main-kda-sfx"
        sfx_path = self.temp_root / "audio" / "coin.wav"
        sfx_path.parent.mkdir(parents=True, exist_ok=True)
        sfx_path.write_text("fake sfx", encoding="utf-8")
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.sfx_path = sfx_path
        self._append_subtitle(
            session_id,
            "1\n00:02:04,000 --> 00:02:06,000\n"
            "kda_change kills=2->3 deaths=0->0 previous_at=100.000 current_at=110.000\n",
        )
        self._append_boundary(session_id, duration=600.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=150.0,
                    reason="highlight_keyword",
                )
            ],
            duration=600.0,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(len(plan.sound_effects), 1)
        self.assertEqual(plan.sound_effects[0].at_seconds, 10.0)
        self.assertEqual(plan.timeline[0].role, "teaser")

    def test_audio_mixing_uses_generated_default_bgm_when_paths_are_unset(self) -> None:
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
        self.assertEqual(plan.audio_beds[0].timeline_start_seconds, 21.0)
        self.assertIsNotNone(plan.audio_beds[0].timeline_end_seconds)
        assert plan.audio_beds[0].timeline_end_seconds is not None
        self.assertGreater(
            plan.audio_beds[0].timeline_end_seconds,
            plan.audio_beds[1].timeline_start_seconds,
        )
        self.assertAlmostEqual(
            plan.audio_beds[0].timeline_end_seconds
            - plan.audio_beds[1].timeline_start_seconds,
            self.settings.editing.bgm_crossfade_seconds,
        )
        self.assertGreater(plan.audio_beds[1].timeline_start_seconds, 21.0)
        self.assertEqual(plan.audio_beds[1].timeline_end_seconds, None)
        self.assertEqual(plan.audio_beds[0].reason, "background_music_playful")
        self.assertEqual(plan.audio_beds[1].reason, "background_music_climax")
        self.assertEqual(len(plan.sound_effects), 2)
        self.assertTrue(
            all(hit.source_path.endswith("coin.wav") for hit in plan.sound_effects)
        )
        self.assertEqual(
            [hit.reason for hit in plan.sound_effects],
            ["highlight_keyword", "highlight_keyword"],
        )
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

    def test_bgm_library_tie_break_offsets_adjacent_match_indices(self) -> None:
        tracks = [
            BgmLibraryTrack(
                path=self.temp_root / f"hype-{index}.wav",
                tags=("hype",),
                phase="climax",
                energy=5,
            )
            for index in range(4)
        ]

        selected_names = [
            select_bgm_tracks(
                tracks,
                BgmSelectionContext(
                    tags=("hype",),
                    highlight_reasons=("condensed_key_event",),
                    rendered_duration_seconds=60.0,
                    selection_key=f"session-edit:{match_index}:hype",
                ),
            )[0].path.name
            for match_index in range(4)
        ]

        self.assertEqual(len(set(selected_names)), len(selected_names))

    def test_bgm_library_prefers_early_phase_before_climax(self) -> None:
        early_path = self.temp_root / "early.wav"
        hype_path = self.temp_root / "hype.wav"
        tracks = [
            BgmLibraryTrack(
                path=early_path,
                tags=("hype", "chill"),
                phase="laning",
                mood="chill",
                energy=2,
            ),
            BgmLibraryTrack(
                path=hype_path,
                tags=("hype", "chill", "funny"),
                phase="climax",
                mood="hype",
                energy=5,
            ),
        ]

        selected = select_bgm_tracks(
            tracks,
            BgmSelectionContext(
                tags=("hype", "chill", "funny"),
                highlight_reasons=("condensed_key_event", "condensed_match_context"),
                rendered_duration_seconds=600.0,
                selection_key="session-edit:2:hype,chill,funny",
            ),
        )

        self.assertEqual([track.path for track in selected], [early_path, hype_path])

    def test_long_library_bgm_uses_three_content_aware_crossfaded_phases(self) -> None:
        session_id = "session-edit-bgm-three-phase"
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.teaser_max_segments = 0
        library_path, laning_path, momentum_path, climax_path = (
            self._write_three_phase_bgm_library()
        )
        self.settings.editing.bgm_library_path = library_path
        self._append_subtitle(
            session_id,
            "1\n00:00:02,000 --> 00:00:04,000\n"
            "robot tactical fight\n",
        )
        self._append_boundary(session_id, duration=900.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=350.0,
                    reason="condensed_match_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=360.0,
                    ended_at_seconds=760.0,
                    reason="condensed_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=770.0,
                    ended_at_seconds=900.0,
                    reason="condensed_match_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=320.0,
                    ended_at_seconds=340.0,
                    reason="condensed_tactical",
                ),
                HighlightClipWindow(
                    started_at_seconds=700.0,
                    ended_at_seconds=720.0,
                    reason="highlight_keyword",
                ),
            ],
            duration=900.0,
            include_edges=False,
        )

        EditingPlannerService(self.settings).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(
            [bed.source_path for bed in plan.audio_beds],
            [str(laning_path), str(momentum_path), str(climax_path)],
        )
        self.assertEqual(
            [bed.reason for bed in plan.audio_beds],
            [
                "background_music_library",
                "background_music_library_momentum",
                "background_music_library_climax",
            ],
        )
        self.assertEqual(
            [
                (bed.timeline_start_seconds, bed.timeline_end_seconds)
                for bed in plan.audio_beds
            ],
            [(0.0, 331.0), (329.0, 701.0), (699.0, None)],
        )

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
                        timeline_start_seconds=20.0,
                        timeline_end_seconds=85.0,
                        gain_db=self.settings.editing.bgm_gain_db,
                        loop=True,
                        reason="background_music_library",
                    ),
                    AudioBed(
                        source_path=str(climax_path),
                        timeline_start_seconds=85.0,
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
        self.assertEqual(latest.audio_beds[0].timeline_start_seconds, 20.0)
        if len(latest.audio_beds) > 1:
            assert latest.audio_beds[0].timeline_end_seconds is not None
            self.assertGreater(
                latest.audio_beds[0].timeline_end_seconds,
                latest.audio_beds[1].timeline_start_seconds,
            )
            self.assertAlmostEqual(
                latest.audio_beds[0].timeline_end_seconds
                - latest.audio_beds[1].timeline_start_seconds,
                self.settings.editing.bgm_crossfade_seconds,
            )
            self.assertGreater(latest.audio_beds[1].timeline_start_seconds, 20.0)

    def test_audio_mixing_marks_main_key_event_without_fallback_teaser(self) -> None:
        session_id = "session-edit-main-sfx"
        sfx_path = self.temp_root / "audio" / "configured-sfx.wav"
        sfx_path.parent.mkdir(parents=True, exist_ok=True)
        sfx_path.write_text("fake sfx", encoding="utf-8")
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.sfx_path = sfx_path
        self.settings.editing.teaser_fallback_enabled = False
        self.settings.editing.teaser_candidate_reasons = ("highlight_keyword",)
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
        self.assertEqual(plan.sound_effects[0].source_path, str(sfx_path))
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
        self.assertTrue(
            all(hit.source_path.endswith("coin.wav") for hit in plan.sound_effects)
        )

    def test_source_music_spans_split_bgm_only_in_detected_regions(self) -> None:
        session_id = "session-edit-source-music-spans"
        bgm_path = self.temp_root / "audio" / "bgm.mp3"
        bgm_path.parent.mkdir(parents=True, exist_ok=True)
        bgm_path.write_text("fake bgm", encoding="utf-8")
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.bgm_path = bgm_path
        self.settings.editing.teaser_max_segments = 0
        self._append_recording(session_id)
        self._append_boundary(session_id, duration=120.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=60.0,
                    reason="condensed_match_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=90.0,
                    ended_at_seconds=120.0,
                    reason="condensed_match_context",
                ),
            ],
            duration=120.0,
            include_edges=False,
        )

        EditingPlannerService(
            self.settings,
            source_bgm_detector=lambda *args, **kwargs: SourceMusicDetection(
                has_music=True,
                confidence=0.9,
                reason="sampled_music_like_audio",
                music_spans=(SourceMusicSpan(40.0, 50.0, 0.9),),
                coverage_ratio=0.083,
            ),
        ).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(len(plan.audio_beds), 2)
        self.assertEqual(
            [
                (bed.timeline_start_seconds, bed.timeline_end_seconds)
                for bed in plan.audio_beds
            ],
            [(0.0, 38.0), (52.0, None)],
        )

    def test_source_music_majority_rendered_coverage_skips_bgm(self) -> None:
        session_id = "session-edit-source-music-majority"
        bgm_path = self.temp_root / "audio" / "bgm.mp3"
        bgm_path.parent.mkdir(parents=True, exist_ok=True)
        bgm_path.write_text("fake bgm", encoding="utf-8")
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.bgm_path = bgm_path
        self.settings.editing.teaser_max_segments = 0
        self.settings.editing.bgm_source_music_padding_seconds = 0.0
        self._append_recording(session_id)
        self._append_boundary(session_id, duration=120.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=60.0,
                    reason="condensed_match_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=90.0,
                    ended_at_seconds=120.0,
                    reason="condensed_match_context",
                ),
            ],
            duration=120.0,
            include_edges=False,
        )

        EditingPlannerService(
            self.settings,
            source_bgm_detector=lambda *args, **kwargs: SourceMusicDetection(
                has_music=True,
                confidence=0.9,
                reason="sampled_music_like_audio",
                music_spans=(SourceMusicSpan(0.0, 120.0, 0.9),),
                coverage_ratio=1.0,
            ),
        ).run()

        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(plan.audio_beds, [])

    def test_chunked_source_music_spans_translate_local_detector_spans(self) -> None:
        session_id = "session-edit-source-music-chunked-spans"
        bgm_path = self.temp_root / "audio" / "bgm.mp3"
        bgm_path.parent.mkdir(parents=True, exist_ok=True)
        bgm_path.write_text("fake bgm", encoding="utf-8")
        self.settings.editing.audio_mixing_enabled = True
        self.settings.editing.bgm_path = bgm_path
        self.settings.editing.teaser_max_segments = 0
        self.settings.editing.bgm_source_music_padding_seconds = 0.0
        first_chunk, second_chunk = self._append_chunked_recording(session_id)
        self._append_boundary(session_id, duration=20.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=8.0,
                    reason="condensed_match_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=12.0,
                    ended_at_seconds=20.0,
                    reason="condensed_match_context",
                ),
            ],
            duration=20.0,
            include_edges=False,
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
                confidence=0.9,
                reason="sampled_music_like_audio",
                music_spans=(SourceMusicSpan(4.0, 6.0, 0.9),),
                coverage_ratio=0.2,
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
        plan = load_models(self.edit_plans_path, EditPlanAsset)[0]
        self.assertEqual(
            [
                (bed.timeline_start_seconds, bed.timeline_end_seconds)
                for bed in plan.audio_beds
            ],
            [(0.0, 4.0), (6.0, 10.0), (12.0, None)],
        )

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
        self.assertTrue(
            all(hit.source_path.endswith("coin.wav") for hit in plans[-1].sound_effects)
        )

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
        self.settings.editing.zoom_fallback_enabled = True
        self.settings.editing.zoom_enabled = True
        self.settings.editing.teaser_fallback_enabled = False
        self.settings.editing.teaser_candidate_reasons = ("highlight_keyword",)
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
        self.assertEqual(
            round(
                sum(
                    segment.source_end_seconds - segment.source_start_seconds
                    for segment in latest_key_segments
                ),
                3,
            ),
            30.0,
        )
        transformed_key_segments = [
            segment
            for segment in latest_key_segments
            if segment.transform is not None
        ]
        self.assertEqual(len(transformed_key_segments), 1)
        self.assertLessEqual(
            transformed_key_segments[0].source_end_seconds
            - transformed_key_segments[0].source_start_seconds,
            self.settings.editing.zoom_closeup_seconds,
        )
        assert transformed_key_segments[0].transform is not None
        self.assertEqual(transformed_key_segments[0].transform.kind, "punch_in")
        self.assertTrue(
            all(
                segment.transform is None
                for segment in latest.timeline
                if segment is not transformed_key_segments[0]
            )
        )

    def test_legacy_long_zoom_plan_is_replanned_without_long_zoom(self) -> None:
        session_id = "session-edit-legacy-long-zoom"
        self.settings.editing.zoom_fallback_enabled = True
        self.settings.editing.zoom_enabled = True
        self.settings.editing.zoom_max_duration_seconds = 30.0
        self.settings.editing.teaser_fallback_enabled = False
        self.settings.editing.teaser_candidate_reasons = ("highlight_keyword",)
        self._append_boundary(session_id, duration=700.0)
        self._append_highlight_plan(
            session_id,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=30.0,
                    reason="condensed_match_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=100.0,
                    ended_at_seconds=220.0,
                    reason="condensed_key_event",
                ),
                HighlightClipWindow(
                    started_at_seconds=670.0,
                    ended_at_seconds=700.0,
                    reason="condensed_match_context",
                ),
            ],
            duration=700.0,
        )
        append_model(
            self.edit_plans_path,
            EditPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=700.0,
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
                        source_end_seconds=220.0,
                        reason="condensed_key_event",
                        transform=TimelineVideoTransform(
                            kind="punch_in",
                            scale=1.2,
                            x_anchor=0.0,
                            y_anchor=1.0,
                            target="chat",
                        ),
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=670.0,
                        source_end_seconds=700.0,
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
        transformed_segments = [
            segment for segment in latest.timeline if segment.transform is not None
        ]
        self.assertEqual(len(transformed_segments), 1)
        self.assertLessEqual(
            transformed_segments[0].source_end_seconds
            - transformed_segments[0].source_start_seconds,
            self.settings.editing.zoom_closeup_seconds,
        )
        self.assertFalse(
            any(
                segment.source_start_seconds == 100.0
                and segment.source_end_seconds == 220.0
                for segment in latest.timeline
            )
        )
        self.assertEqual(
            round(
                sum(
                    segment.source_end_seconds - segment.source_start_seconds
                    for segment in latest.timeline
                    if segment.reason == "condensed_key_event"
                ),
                3,
            ),
            120.0,
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
        kda_events: list[KdaEventCue] | None = None,
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
                kda_events=kda_events or [],
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

    def _append_semantic_asset(
        self,
        session_id: str,
        *,
        teaser_start: float,
        teaser_end: float,
        hook_line: str = "神钩开团，团战逆转",
        story_status: str = "legacy",
    ) -> None:
        append_model(
            self.temp_root / "copywriter-semantic-assets.jsonl",
            CopywriterSemanticAsset(
                session_id=session_id,
                match_index=1,
                source_subtitle_path=str(
                    self.temp_root / "processed" / session_id / "match-01.srt"
                ),
                provider="fake",
                model="fake-model",
                prompt_fingerprint="prompt",
                input_fingerprint=f"input-{session_id}",
                result=LlmCopywritingResult(
                    title_candidates=["神钩开团", "团战逆转", "上分名场面"],
                    recommended_title="神钩开团",
                    cover_lines=["神钩开团", "团战逆转"],
                    summary="一次关键开团带动整局节奏。",
                    description="关键团战打出优势，适合作为发布切片。",
                    tags=["英雄联盟", "直播切片", "神钩", "团战", "上分"],
                    hook_line=hook_line,
                    story_status=story_status,
                    teaser_recommendations=[
                        {
                            "source_start_seconds": teaser_start,
                            "source_end_seconds": teaser_end,
                            "hook_reason": "关键开团瞬间",
                        }
                    ],
                ),
                token_usage={"total_tokens": 42},
                status="generated",
                created_at=datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc),
            ),
        )

    def _write_three_phase_bgm_library(self) -> tuple[Path, Path, Path, Path]:
        library_dir = self.temp_root / "bgm-library-three"
        library_dir.mkdir(parents=True, exist_ok=True)
        laning_path = library_dir / "robot-laning.wav"
        momentum_path = library_dir / "robot-momentum.wav"
        climax_path = library_dir / "robot-climax.wav"
        for path in (laning_path, momentum_path, climax_path):
            path.write_text("audio", encoding="utf-8")
        library_path = library_dir / "library.json"
        library_path.write_text(
            json.dumps(
                {
                    "tracks": [
                        {
                            "path": laning_path.name,
                            "tags": ["robot"],
                            "phase": "laning",
                            "mood": "playful",
                            "energy": 2,
                        },
                        {
                            "path": momentum_path.name,
                            "tags": ["robot", "tactical"],
                            "phase": "momentum",
                            "mood": "tactical",
                            "energy": 3,
                        },
                        {
                            "path": climax_path.name,
                            "tags": ["robot", "hype"],
                            "phase": "climax",
                            "mood": "hype",
                            "energy": 5,
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        return library_path, laning_path, momentum_path, climax_path

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

    def _write_sfx_library(self) -> tuple[Path, Path, Path]:
        library_dir = self.temp_root / "sfx-library"
        library_dir.mkdir(parents=True, exist_ok=True)
        kill_path = library_dir / "coin.wav"
        multi_path = library_dir / "multi.wav"
        kill_path.write_text("fake kill", encoding="utf-8")
        multi_path.write_text("fake multi", encoding="utf-8")
        library_path = library_dir / "library.json"
        library_path.write_text(
            json.dumps(
                {
                    "tracks": [
                        {
                            "category": "kill_coin",
                            "path": kill_path.name,
                        },
                        {
                            "category": "multi_kill",
                            "path": multi_path.name,
                            "gain_db": -7.0,
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        return library_path, kill_path, multi_path

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
