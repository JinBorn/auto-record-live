from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from arl.config import HighlightSettings, Settings, StorageSettings
from arl.highlights.models import ClassifiedCue, HighlightPlannerStateFile
from arl.highlights.service import (
    HighlightPlannerService,
    _CombatActivitySample,
    _SrtCue,
)
from arl.shared.contracts import (
    HighlightClipWindow,
    HighlightPlanAsset,
    MatchBoundary,
    RecordingAsset,
    RecordingChunk,
    RecordingChunkManifest,
    SourceType,
    SubtitleAsset,
)
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.semantic_contracts import SemanticAssetView
from arl.shared.semantic_contracts import SemanticAssetView
from arl.vision.models import KdaReading


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

    def test_condensed_planner_requires_meaningful_subtitles(self) -> None:
        session_id = "session-condensed-placeholder"
        self.settings.highlights.mode = "condensed"
        subtitle_path = self._write_srt(
            session_id,
            "\n".join(
                [
                    "1",
                    "00:00:00,000 --> 00:00:02,000",
                    "Placeholder subtitle generated by local pipeline.",
                    "",
                ]
            )
            + "\n",
        )
        self._append_boundary(session_id, duration=900.0)
        self._append_subtitle(session_id, subtitle_path)

        HighlightPlannerService(self.settings).run()

        plans = load_models(self.plans_path, HighlightPlanAsset)
        self.assertEqual(plans, [])
        state = HighlightPlannerStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [])

    def test_planner_force_reprocess_appends_replacement_plan(self) -> None:
        session_id = "session-highlight-force"
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
        service.run(session_ids={session_id})
        service.run(session_ids={session_id}, force_reprocess=True)

        plans = load_models(self.plans_path, HighlightPlanAsset)
        self.assertEqual(len(plans), 2)
        self.assertEqual([plan.session_id for plan in plans], [session_id, session_id])

    def test_condensed_planner_preserves_kda_kill_events(self) -> None:
        session_id = "session-condensed-kda"
        self.settings.highlights.mode = "condensed"
        self.settings.highlights.condensed_use_visual_analysis = False
        self.settings.highlights.condensed_kda_sample_interval_seconds = 5.0
        subtitle_path = self._write_srt(
            session_id,
            "\n".join(
                [
                    "1",
                    "00:01:30,000 --> 00:01:34,000",
                    "lane narration before fight",
                    "",
                ]
            )
            + "\n",
        )
        video_path = self.settings.storage.raw_dir / session_id / "recording-source.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"fake-mp4")
        append_model(
            self.temp_root / "recording-assets.jsonl",
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.DIRECT_STREAM,
                path=str(video_path),
                started_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            ),
        )
        self._append_boundary(session_id, duration=900.0)
        self._append_subtitle(session_id, subtitle_path)

        readings = [
            KdaReading(95.0, 0, 0, 0, 0.9),
            KdaReading(100.0, 1, 0, 0, 0.9),
            KdaReading(105.0, 1, 0, 0, 0.9),
        ]
        with (
            patch(
                "arl.vision.frame_sampler.sample_frame_window",
                return_value=[(95.0, object()), (100.0, object()), (105.0, object())],
            ),
            patch("arl.vision.kda_ocr.read_kda", side_effect=readings),
        ):
            HighlightPlannerService(self.settings).run()

        plans = load_models(self.plans_path, HighlightPlanAsset)
        self.assertEqual(len(plans), 1)
        self.assertTrue(
            any(
                window.reason == "condensed_key_event"
                and window.started_at_seconds <= 95.0
                and window.ended_at_seconds >= 100.0
                for window in plans[0].windows
            )
        )
        self.assertEqual(len(plans[0].kda_events), 1)
        self.assertIn("kda_change kills=0->1", plans[0].kda_events[0].text)
        self.assertIn("current_at=100.000", plans[0].kda_events[0].text)

    def test_kda_event_detection_samples_chunked_recording_spans(self) -> None:
        service = HighlightPlannerService(self.settings)
        session_id = "session-condensed-kda-chunked"
        raw_dir = self.settings.storage.raw_dir / session_id
        chunk_dir = raw_dir / "chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        first_chunk = chunk_dir / "recording-00000.mp4"
        second_chunk = chunk_dir / "recording-00001.mp4"
        first_chunk.write_bytes(b"chunk-0")
        second_chunk.write_bytes(b"chunk-1")
        manifest_path = raw_dir / "recording-chunks.json"
        manifest = RecordingChunkManifest(
            session_id=session_id,
            source_type=SourceType.DIRECT_STREAM,
            path=str(manifest_path),
            started_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
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
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        manifest_path.write_text(
            manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        recording = RecordingAsset(
            session_id=session_id,
            source_type=SourceType.DIRECT_STREAM,
            path=str(manifest_path),
            started_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        boundary = MatchBoundary(
            session_id=session_id,
            match_index=1,
            started_at_seconds=8.0,
            ended_at_seconds=12.0,
            confidence=0.9,
        )
        sample_calls: list[tuple[Path, float, float]] = []
        read_timestamps: list[float] = []

        def _sample_frame_window(path, start_seconds, end_seconds, *, interval_seconds):
            sample_calls.append((Path(path), start_seconds, end_seconds))
            if Path(path) == first_chunk:
                return [(9.0, object())]
            return [(1.0, object())]

        def _read_kda(frame, timestamp_seconds, *, crop_region):
            read_timestamps.append(timestamp_seconds)
            if timestamp_seconds < 10.0:
                return KdaReading(timestamp_seconds, 0, 0, 0, 0.9)
            return KdaReading(timestamp_seconds, 1, 0, 0, 0.9)

        with (
            patch(
                "arl.vision.frame_sampler.sample_frame_window",
                side_effect=_sample_frame_window,
            ),
            patch("arl.vision.kda_ocr.read_kda", side_effect=_read_kda),
        ):
            cues = service._detect_kda_event_cues(
                recording=recording,
                boundary=boundary,
                duration=4.0,
            )

        self.assertEqual(
            sample_calls,
            [
                (first_chunk, 8.0, 10.0),
                (second_chunk, 0.0, 2.0),
            ],
        )
        self.assertEqual(read_timestamps, [9.0, 11.0])
        self.assertEqual(len(cues), 1)
        self.assertIn("current_at=3.000", cues[0].text)

    def test_kda_event_detection_preserves_long_gap_kill_death_change(self) -> None:
        service = HighlightPlannerService(self.settings)
        video_path = self.temp_root / "recording-source.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"fake-mp4")
        boundary = MatchBoundary(
            session_id="session-condensed-kda-long-gap",
            match_index=1,
            started_at_seconds=0.0,
            ended_at_seconds=300.0,
            confidence=0.8,
        )
        recording = RecordingAsset(
            session_id=boundary.session_id,
            source_type=SourceType.DIRECT_STREAM,
            path=str(video_path),
            started_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        readings = [
            KdaReading(95.0, 2, 0, 1, 0.9),
            KdaReading(100.0, 1, 0, 1, 0.9),
            KdaReading(190.0, 3, 1, 1, 0.9),
        ]

        with (
            patch(
                "arl.vision.frame_sampler.sample_frame_window",
                return_value=[(95.0, object()), (100.0, object()), (190.0, object())],
            ),
            patch("arl.vision.kda_ocr.read_kda", side_effect=readings),
        ):
            cues = service._detect_kda_event_cues(
                recording=recording,
                boundary=boundary,
                duration=300.0,
            )

        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].category, "key_event")
        self.assertEqual(cues[0].started_at_seconds, 65.0)
        self.assertEqual(cues[0].ended_at_seconds, 195.0)
        self.assertIn("kills=2->3", cues[0].text)
        self.assertIn("deaths=0->1", cues[0].text)

    def test_kda_event_detection_preserves_post_death_kill_changes(self) -> None:
        service = HighlightPlannerService(self.settings)
        video_path = self.temp_root / "recording-source.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"fake-mp4")
        boundary = MatchBoundary(
            session_id="session-condensed-kda-post-death",
            match_index=1,
            started_at_seconds=0.0,
            ended_at_seconds=300.0,
            confidence=0.8,
        )
        recording = RecordingAsset(
            session_id=boundary.session_id,
            source_type=SourceType.DIRECT_STREAM,
            path=str(video_path),
            started_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        readings = [
            KdaReading(90.0, 6, 1, 2, 0.9),
            KdaReading(100.0, 6, 2, 2, 0.9),
            KdaReading(150.0, 8, 2, 2, 0.9),
            KdaReading(220.0, 9, 2, 2, 0.9),
        ]

        with (
            patch(
                "arl.vision.frame_sampler.sample_frame_window",
                return_value=[
                    (90.0, object()),
                    (100.0, object()),
                    (150.0, object()),
                    (220.0, object()),
                ],
            ),
            patch("arl.vision.kda_ocr.read_kda", side_effect=readings),
        ):
            cues = service._detect_kda_event_cues(
                recording=recording,
                boundary=boundary,
                duration=300.0,
            )

        self.assertEqual(len(cues), 3)
        self.assertIn("deaths=1->2", cues[0].text)
        self.assertIn("kills=6->8", cues[1].text)
        self.assertIn("kills=8->9", cues[2].text)

    def test_trim_silent_kda_death_waits_splits_internal_subtitle_gap(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=485.0,
                ended_at_seconds=640.0,
                reason="condensed_key_event",
            )
        ]
        kda_cues = [
            ClassifiedCue(
                started_at_seconds=500.0,
                ended_at_seconds=635.0,
                text=(
                    "kda_change kills=2->3 deaths=0->1 "
                    "previous_at=560.000 current_at=630.000"
                ),
                category="key_event",
                priority=1.0,
            )
        ]
        speech_cues = [
            _SrtCue(592.6, 593.7, "哎呦,可惜"),
            _SrtCue(599.9, 602.9, "这不差一点点"),
            _SrtCue(621.1, 627.0, "然后比如说"),
        ]

        trimmed = service._trim_silent_kda_death_waits(
            windows,
            kda_event_cues=kda_cues,
            speech_cues=speech_cues,
            classified_cues=kda_cues,
        )

        self.assertEqual(
            [(item.started_at_seconds, item.ended_at_seconds) for item in trimmed],
            [(485.0, 605.9), (621.1, 640.0)],
        )

    def test_trim_silent_kda_death_waits_extends_death_reaction_tail(self) -> None:
        service = HighlightPlannerService(self.settings)
        self.settings.highlights.condensed_kda_death_silent_gap_trim_seconds = 999.0
        windows = [
            HighlightClipWindow(
                started_at_seconds=100.0,
                ended_at_seconds=160.0,
                reason="condensed_key_event",
            )
        ]
        kda_cues = [
            ClassifiedCue(
                started_at_seconds=120.0,
                ended_at_seconds=170.0,
                text=(
                    "kda_change kills=5->5 deaths=0->1 "
                    "previous_at=150.000 current_at=160.000"
                ),
                category="key_event",
                priority=1.0,
            )
        ]

        trimmed = service._trim_silent_kda_death_waits(
            windows,
            kda_event_cues=kda_cues,
            speech_cues=[],
            classified_cues=kda_cues,
        )

        self.assertEqual(len(trimmed), 1)
        self.assertEqual(trimmed[0].started_at_seconds, 100.0)
        self.assertEqual(trimmed[0].ended_at_seconds, 163.0)

    def test_trim_silent_kda_death_waits_preserves_observation_lead_in(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=100.0,
                ended_at_seconds=170.0,
                reason="condensed_key_event",
            )
        ]
        kda_cues = [
            ClassifiedCue(
                started_at_seconds=100.0,
                ended_at_seconds=165.0,
                text=(
                    "kda_change kills=5->5 deaths=0->1 "
                    "previous_at=150.000 current_at=160.000"
                ),
                category="key_event",
                priority=1.0,
            )
        ]
        speech_cues = [
            _SrtCue(120.0, 130.0, "fight setup"),
            _SrtCue(158.0, 159.0, "death reaction"),
        ]

        trimmed = service._trim_silent_kda_death_waits(
            windows,
            kda_event_cues=kda_cues,
            speech_cues=speech_cues,
            classified_cues=kda_cues,
        )

        self.assertEqual(
            [(item.started_at_seconds, item.ended_at_seconds) for item in trimmed],
            [(100.0, 170.0)],
        )

    def test_trim_post_death_waits_drops_context_and_shifts_later_key_window(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=1016.4,
                ended_at_seconds=1034.04,
                reason="condensed_context",
            ),
            HighlightClipWindow(
                started_at_seconds=1037.52,
                ended_at_seconds=1360.0,
                reason="condensed_key_event",
            ),
        ]
        kda_cues = [
            ClassifiedCue(
                started_at_seconds=920.0,
                ended_at_seconds=995.0,
                text=(
                    "kda_change kills=6->6 deaths=1->2 "
                    "previous_at=980.000 current_at=990.000"
                ),
                category="key_event",
                priority=1.0,
            )
        ]
        classified_cues = [
            ClassifiedCue(1021.4, 1025.2, "ordinary narration", "narration", 0.4),
            ClassifiedCue(1111.9, 1115.4, "dragon fight", "key_event", 1.0),
        ]

        trimmed = service._trim_post_death_low_value_waits(
            windows,
            kda_event_cues=kda_cues,
            classified_cues=classified_cues,
        )

        self.assertEqual(len(trimmed), 1)
        self.assertEqual(trimmed[0].started_at_seconds, 1106.9)
        self.assertEqual(trimmed[0].ended_at_seconds, 1360.0)
        self.assertEqual(trimmed[0].reason, "condensed_key_event")

    def test_protect_death_like_continuity_entries_extends_bridge_to_previous_end(self) -> None:
        service = HighlightPlannerService(self.settings)
        boundary = MatchBoundary(
            session_id="session-death-bridge",
            match_index=1,
            started_at_seconds=1000.0,
            ended_at_seconds=1300.0,
            confidence=0.9,
        )
        recording = RecordingAsset(
            session_id="session-death-bridge",
            source_type=SourceType.DIRECT_STREAM,
            path="recording.mp4",
            started_at=datetime.now(timezone.utc),
        )
        windows = [
            HighlightClipWindow(
                started_at_seconds=100.0,
                ended_at_seconds=120.0,
                reason="condensed_key_event",
            ),
            HighlightClipWindow(
                started_at_seconds=138.0,
                ended_at_seconds=160.0,
                reason="condensed_continuity",
            ),
        ]

        with patch.object(
            service,
            "_sample_boundary_frame",
            return_value=_death_like_frame(),
        ):
            protected = service._protect_death_like_continuity_entries(
                windows,
                boundary=boundary,
                recording=recording,
            )

        self.assertEqual(protected[1].started_at_seconds, 120.0)
        self.assertEqual(protected[1].ended_at_seconds, 160.0)

    def test_trim_post_death_waits_keeps_later_kda_kill_window(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=1037.52,
                ended_at_seconds=1360.0,
                reason="condensed_key_event",
            )
        ]
        kda_cues = [
            ClassifiedCue(
                started_at_seconds=920.0,
                ended_at_seconds=995.0,
                text=(
                    "kda_change kills=6->6 deaths=1->2 "
                    "previous_at=980.000 current_at=990.000"
                ),
                category="key_event",
                priority=1.0,
            ),
            ClassifiedCue(
                started_at_seconds=1010.0,
                ended_at_seconds=1050.0,
                text=(
                    "kda_change kills=6->8 deaths=2->2 "
                    "previous_at=1040.000 current_at=1045.000"
                ),
                category="key_event",
                priority=1.0,
            ),
        ]
        classified_cues = [
            *kda_cues,
            ClassifiedCue(1111.9, 1115.4, "dragon fight", "key_event", 1.0),
        ]

        trimmed = service._trim_post_death_low_value_waits(
            windows,
            kda_event_cues=kda_cues,
            classified_cues=classified_cues,
        )

        self.assertEqual(len(trimmed), 1)
        self.assertEqual(trimmed[0].started_at_seconds, 1037.52)
        self.assertEqual(trimmed[0].ended_at_seconds, 1360.0)

    def test_trim_low_value_internal_gaps_splits_long_no_signal_gap(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=0.0,
                ended_at_seconds=100.0,
                reason="condensed_key_event",
            )
        ]
        speech_cues = [
            _SrtCue(0.0, 10.0, "opening narration"),
            _SrtCue(90.0, 100.0, "ending narration"),
        ]

        trimmed = service._trim_low_value_internal_gaps(
            windows,
            speech_cues=speech_cues,
            kda_event_cues=[],
            classified_cues=[],
            match_duration_seconds=120.0,
        )

        self.assertEqual(
            [(item.started_at_seconds, item.ended_at_seconds) for item in trimmed],
            [(0.0, 13.0), (87.0, 100.0)],
        )

    def test_trim_low_value_internal_gaps_preserves_silent_visual_action(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=0.0,
                ended_at_seconds=100.0,
                reason="condensed_key_event",
            )
        ]
        speech_cues = [
            _SrtCue(0.0, 10.0, "opening narration"),
            _SrtCue(90.0, 100.0, "ending narration"),
        ]
        classified_cues = [
            ClassifiedCue(50.0, 60.0, "visual fight cluster", "key_event", 1.0)
        ]

        trimmed = service._trim_low_value_internal_gaps(
            windows,
            speech_cues=speech_cues,
            kda_event_cues=[],
            classified_cues=classified_cues,
            match_duration_seconds=120.0,
        )

        self.assertEqual(
            [(item.started_at_seconds, item.ended_at_seconds) for item in trimmed],
            [(0.0, 13.0), (47.0, 63.0), (87.0, 100.0)],
        )

    def test_trim_low_value_internal_gaps_preserves_kda_event_coverage(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=0.0,
                ended_at_seconds=100.0,
                reason="condensed_key_event",
            )
        ]
        kda_cues = [
            ClassifiedCue(
                40.0,
                70.0,
                "kda_change kills=0->1 deaths=0->0 previous_at=50.000 current_at=65.000",
                "key_event",
                1.0,
            )
        ]

        trimmed = service._trim_low_value_internal_gaps(
            windows,
            speech_cues=[],
            kda_event_cues=kda_cues,
            classified_cues=kda_cues,
            match_duration_seconds=120.0,
        )

        self.assertTrue(
            any(
                item.reason == "condensed_key_event"
                and item.started_at_seconds <= 40.0
                and item.ended_at_seconds >= 70.0
                for item in trimmed
            )
        )
        self.assertLess(
            sum(item.ended_at_seconds - item.started_at_seconds for item in trimmed),
            100.0,
        )

    def test_adaptive_combat_interval_extends_until_activity_releases(self) -> None:
        service = HighlightPlannerService(self.settings)
        cue = ClassifiedCue(50.0, 52.0, "teamfight starts", "key_event", 1.0)
        samples = [
            _CombatActivitySample(40.0, 0.01),
            _CombatActivitySample(42.0, 0.07),
            _CombatActivitySample(44.0, 0.04),
            _CombatActivitySample(46.0, 0.06),
            _CombatActivitySample(52.0, 0.08),
            _CombatActivitySample(54.0, 0.04),
            _CombatActivitySample(56.0, 0.03),
            _CombatActivitySample(58.0, 0.01),
            _CombatActivitySample(60.0, 0.01),
            _CombatActivitySample(62.0, 0.01),
        ]

        intervals = service._detect_combat_protected_intervals(
            classified_cues=[cue],
            kda_event_cues=[],
            match_duration_seconds=120.0,
            activity_samples=samples,
        )

        self.assertEqual(len(intervals), 1)
        self.assertLessEqual(intervals[0][0], 42.0)
        self.assertGreaterEqual(intervals[0][1], 56.0)
        self.assertLess(intervals[0][1], 62.0)

    def test_combat_continuity_does_not_protect_motion_without_combat_anchor(self) -> None:
        service = HighlightPlannerService(self.settings)

        intervals = service._detect_combat_protected_intervals(
            classified_cues=[
                ClassifiedCue(20.0, 22.0, "ordinary lane narration", "narration", 0.4)
            ],
            kda_event_cues=[],
            match_duration_seconds=120.0,
            activity_samples=[_CombatActivitySample(20.0, 0.2)],
        )

        self.assertEqual(intervals, [])

    def test_combat_interval_protects_silent_internal_fight(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [HighlightClipWindow(
            started_at_seconds=0.0,
            ended_at_seconds=100.0,
            reason="condensed_key_event",
        )]
        speech_cues = [
            _SrtCue(0.0, 10.0, "setup"),
            _SrtCue(90.0, 100.0, "resolution"),
        ]

        trimmed = service._trim_low_value_internal_gaps(
            windows,
            speech_cues=speech_cues,
            kda_event_cues=[],
            classified_cues=[],
            match_duration_seconds=120.0,
            combat_protected_intervals=[(20.0, 80.0)],
        )

        self.assertTrue(any(
            item.started_at_seconds <= 20.0 and item.ended_at_seconds >= 80.0
            for item in trimmed
        ))

    def test_extend_action_resolution_keeps_failed_gank_explanation(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=825.0,
                ended_at_seconds=880.0,
                reason="condensed_key_event",
            ),
            HighlightClipWindow(
                started_at_seconds=915.0,
                ended_at_seconds=993.0,
                reason="condensed_key_event",
            ),
        ]
        classified_cues = [
            ClassifiedCue(881.82, 883.50, "A一刀", "narration", 0.4),
            ClassifiedCue(884.34, 885.00, "A一下", "narration", 0.4),
            ClassifiedCue(885.00, 892.84, "那你往那走", "narration", 0.4),
            ClassifiedCue(
                897.28,
                900.62,
                "这已经使出浑身解数了",
                "narration",
                0.4,
            ),
            ClassifiedCue(902.96, 905.14, "那给他走了", "narration", 0.4),
            ClassifiedCue(907.26, 909.98, "回家喽", "narration", 0.4),
        ]

        extended = service._extend_action_resolution_windows(
            windows,
            classified_cues=classified_cues,
        )

        self.assertEqual(len(extended), 2)
        self.assertEqual(extended[0].started_at_seconds, 825.0)
        self.assertEqual(extended[0].ended_at_seconds, 909.98)
        self.assertEqual(extended[1].started_at_seconds, 915.0)

    def test_extend_action_resolution_stops_at_large_subtitle_gap(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=100.0,
                ended_at_seconds=120.0,
                reason="condensed_key_event",
            )
        ]
        classified_cues = [
            ClassifiedCue(132.0, 135.0, "late unrelated narration", "narration", 0.4)
        ]

        extended = service._extend_action_resolution_windows(
            windows,
            classified_cues=classified_cues,
        )

        self.assertEqual(len(extended), 1)
        self.assertEqual(extended[0].started_at_seconds, 100.0)
        self.assertEqual(extended[0].ended_at_seconds, 120.0)

    def test_protect_speech_boundaries_extends_cut_subtitle_cues(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=10.5,
                ended_at_seconds=30.0,
                reason="condensed_key_event",
            )
        ]
        speech_cues = [
            _SrtCue(10.0, 12.0, "speech already started"),
            _SrtCue(29.5, 32.0, "unfinished sentence"),
            _SrtCue(32.4, 34.0, "same thought continues"),
            _SrtCue(36.0, 38.0, "later unrelated speech"),
        ]

        protected = service._protect_speech_boundaries(
            windows,
            speech_cues=speech_cues,
            match_duration_seconds=40.0,
        )

        self.assertEqual(len(protected), 1)
        self.assertEqual(protected[0].started_at_seconds, 10.0)
        self.assertEqual(protected[0].ended_at_seconds, 34.0)

    def test_budget_speech_protection_finishes_sentence_beyond_old_cap(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=10.0,
                ended_at_seconds=20.0,
                reason="condensed_key_event",
            )
        ]
        speech_cues = [
            _SrtCue(19.5, 22.0, "我今天中午"),
            _SrtCue(22.2, 25.5, "还没有吃饭。"),
            _SrtCue(25.7, 29.0, "然后准备点外卖。"),
        ]

        protected = service._protect_speech_boundaries(
            windows,
            speech_cues=speech_cues,
            match_duration_seconds=40.0,
            max_extension_seconds=12.0,
        )

        self.assertEqual(protected[0].ended_at_seconds, 25.5)

    def test_budget_speech_protection_can_cut_between_adjacent_sentences(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=10.0,
                ended_at_seconds=20.0,
                reason="condensed_key_event",
            )
        ]
        speech_cues = [
            _SrtCue(19.5, 22.0, "第一句话说完了。"),
            _SrtCue(22.1, 26.0, "第二句话也在继续。"),
        ]

        protected = service._protect_speech_boundaries(
            windows,
            speech_cues=speech_cues,
            match_duration_seconds=40.0,
            max_extension_seconds=12.0,
        )

        self.assertEqual(protected[0].ended_at_seconds, 22.0)

    def test_budget_speech_protection_keeps_pathological_chain_bounded(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=10.0,
                ended_at_seconds=20.0,
                reason="condensed_key_event",
            )
        ]
        speech_cues = [
            _SrtCue(19.5, 23.0, "没有句号的连续讲话"),
            _SrtCue(23.1, 28.0, "仍然没有可用句界"),
        ]

        protected = service._protect_speech_boundaries(
            windows,
            speech_cues=speech_cues,
            match_duration_seconds=40.0,
            max_extension_seconds=4.0,
        )

        self.assertEqual(protected[0].ended_at_seconds, 24.0)

    def test_condensed_duration_budget_keeps_dense_content_short(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=0.0,
                ended_at_seconds=20.0,
                reason="condensed_match_context",
            ),
            HighlightClipWindow(
                started_at_seconds=80.0,
                ended_at_seconds=220.0,
                reason="condensed_key_event",
            ),
            HighlightClipWindow(
                started_at_seconds=500.0,
                ended_at_seconds=760.0,
                reason="condensed_key_event",
            ),
            HighlightClipWindow(
                started_at_seconds=880.0,
                ended_at_seconds=900.0,
                reason="condensed_match_context",
            ),
        ]
        classified_cues = [
            ClassifiedCue(100.0, 104.0, "opening kill", "key_event", 1.0),
            ClassifiedCue(520.0, 524.0, "dragon fight", "key_event", 1.0),
            ClassifiedCue(540.0, 544.0, "double kill", "key_event", 1.0),
            ClassifiedCue(560.0, 564.0, "tower dive", "key_event", 1.0),
            ClassifiedCue(700.0, 704.0, "rotation", "tactical", 0.7),
        ]

        budgeted = service._enforce_condensed_duration_budget(
            windows,
            classified_cues=classified_cues,
            target_duration_seconds=120.0,
            match_duration_seconds=900.0,
        )

        total = sum(
            window.ended_at_seconds - window.started_at_seconds
            for window in budgeted
        )
        self.assertLessEqual(total, 180.0)
        self.assertEqual(budgeted[0].started_at_seconds, 0.0)
        self.assertEqual(budgeted[-1].ended_at_seconds, 900.0)
        self.assertTrue(
            any(
                window.started_at_seconds <= 540.0 <= window.ended_at_seconds
                for window in budgeted
            )
        )

    def test_budget_shrink_converges_and_keeps_kda_coverage(self) -> None:
        service = HighlightPlannerService(self.settings)
        kda_cues = [
            ClassifiedCue(
                180.0,
                210.0,
                "kda_change kills=0->1 deaths=0->0 previous_at=195.000 current_at=205.000",
                "key_event",
                1.0,
            ),
            ClassifiedCue(
                700.0,
                730.0,
                "kda_change kills=1->2 deaths=0->0 previous_at=715.000 current_at=725.000",
                "key_event",
                1.0,
            ),
        ]
        windows = [
            HighlightClipWindow(
                started_at_seconds=100.0,
                ended_at_seconds=400.0,
                reason="condensed_key_event",
            ),
            HighlightClipWindow(
                started_at_seconds=600.0,
                ended_at_seconds=900.0,
                reason="condensed_key_event",
            ),
            HighlightClipWindow(
                started_at_seconds=1200.0,
                ended_at_seconds=1500.0,
                reason="condensed_context",
            ),
        ]
        speech_cues = [
            _SrtCue(float(base), float(base + 2), f"line {base}")
            for base in range(0, 1800, 60)
        ]

        shrunk, exception_reason = service._shrink_windows_to_budget(
            windows,
            kda_event_cues=kda_cues,
            speech_cues=speech_cues,
            classified_cues=list(kda_cues),
            target_duration_seconds=300.0,
            match_duration_seconds=1800.0,
        )

        total = sum(w.ended_at_seconds - w.started_at_seconds for w in shrunk)
        self.assertLessEqual(total, 376.0)
        self.assertIsNone(exception_reason)
        for cue in kda_cues:
            self.assertTrue(
                any(
                    w.started_at_seconds <= cue.started_at_seconds + 0.01
                    and w.ended_at_seconds >= cue.ended_at_seconds - 0.01
                    for w in shrunk
                ),
                f"KDA span {cue.started_at_seconds}-{cue.ended_at_seconds} uncovered",
            )
        ordered = sorted(shrunk, key=lambda w: w.started_at_seconds)
        for previous, current in zip(ordered, ordered[1:]):
            gap = current.started_at_seconds - previous.ended_at_seconds
            self.assertLessEqual(gap, 45.1)

    def test_budget_shrink_keeps_boundary_edge_anchors(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=0.0,
                ended_at_seconds=20.0,
                reason="condensed_context",
            ),
            HighlightClipWindow(
                started_at_seconds=300.0,
                ended_at_seconds=900.0,
                reason="condensed_key_event",
            ),
        ]
        speech_cues = [
            _SrtCue(float(base), float(base + 2), f"line {base}")
            for base in range(0, 900, 60)
        ]

        shrunk, _exception = service._shrink_windows_to_budget(
            windows,
            kda_event_cues=[],
            speech_cues=speech_cues,
            classified_cues=[],
            target_duration_seconds=120.0,
            match_duration_seconds=900.0,
        )

        # The edit planner requires windows anchored at both boundary edges.
        self.assertLessEqual(min(w.started_at_seconds for w in shrunk), 0.5)
        self.assertGreaterEqual(max(w.ended_at_seconds for w in shrunk), 899.5)

    def test_budget_shrink_is_noop_within_budget(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=0.0,
                ended_at_seconds=120.0,
                reason="condensed_key_event",
            ),
            HighlightClipWindow(
                started_at_seconds=150.0,
                ended_at_seconds=240.0,
                reason="condensed_context",
            ),
        ]

        shrunk, exception_reason = service._shrink_windows_to_budget(
            windows,
            kda_event_cues=[],
            speech_cues=[_SrtCue(10.0, 12.0, "line")],
            classified_cues=[],
            target_duration_seconds=300.0,
            match_duration_seconds=600.0,
        )

        self.assertEqual(shrunk, windows)
        self.assertIsNone(exception_reason)

    def test_budget_shrink_disabled_by_setting_keeps_windows(self) -> None:
        self.settings.highlights.condensed_budget_shrink_enabled = False
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=0.0,
                ended_at_seconds=500.0,
                reason="condensed_key_event",
            ),
            HighlightClipWindow(
                started_at_seconds=540.0,
                ended_at_seconds=900.0,
                reason="condensed_key_event",
            ),
        ]

        shrunk, exception_reason = service._shrink_windows_to_budget(
            windows,
            kda_event_cues=[],
            speech_cues=[_SrtCue(10.0, 12.0, "line")],
            classified_cues=[],
            target_duration_seconds=120.0,
            match_duration_seconds=900.0,
        )

        self.assertEqual(shrunk, windows)
        self.assertIsNone(exception_reason)

    def test_budget_shrink_records_exception_when_protected_floor_reached(self) -> None:
        service = HighlightPlannerService(self.settings)
        kda_cues = [
            ClassifiedCue(
                float(start),
                float(start + 60),
                (
                    "kda_change kills=0->1 deaths=0->0 "
                    f"previous_at={start + 20}.000 current_at={start + 40}.000"
                ),
                "key_event",
                1.0,
            )
            for start in range(100, 500, 80)
        ]
        windows = [
            HighlightClipWindow(
                started_at_seconds=90.0,
                ended_at_seconds=560.0,
                reason="condensed_key_event",
            ),
        ]

        shrunk, exception_reason = service._shrink_windows_to_budget(
            windows,
            kda_event_cues=kda_cues,
            speech_cues=[_SrtCue(10.0, 12.0, "line")],
            classified_cues=list(kda_cues),
            target_duration_seconds=60.0,
            match_duration_seconds=600.0,
        )

        self.assertIsNotNone(exception_reason)
        self.assertIn("protected content floor", exception_reason)
        for cue in kda_cues:
            self.assertTrue(
                any(
                    w.started_at_seconds <= cue.started_at_seconds + 0.01
                    and w.ended_at_seconds >= cue.ended_at_seconds - 0.01
                    for w in shrunk
                )
            )

    def test_budget_shrink_preserves_combat_interval_and_records_it(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [HighlightClipWindow(
            started_at_seconds=0.0,
            ended_at_seconds=500.0,
            reason="condensed_key_event",
        )]

        shrunk, exception_reason = service._shrink_windows_to_budget(
            windows,
            kda_event_cues=[],
            speech_cues=[],
            classified_cues=[
                ClassifiedCue(200.0, 202.0, "teamfight", "key_event", 1.0)
            ],
            target_duration_seconds=60.0,
            match_duration_seconds=600.0,
            combat_protected_intervals=[(100.0, 400.0)],
        )

        self.assertTrue(any(
            item.started_at_seconds <= 100.0 and item.ended_at_seconds >= 400.0
            for item in shrunk
        ))
        self.assertIsNotNone(exception_reason)
        self.assertIn("combat_protected=300s", exception_reason)

    def test_protect_speech_boundaries_caps_extension_in_shrink_mode(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=10.5,
                ended_at_seconds=30.0,
                reason="condensed_key_event",
            )
        ]
        speech_cues = [
            _SrtCue(10.0, 12.0, "speech already started"),
            _SrtCue(29.5, 32.0, "unfinished sentence"),
            _SrtCue(32.4, 34.0, "same thought continues"),
        ]

        protected = service._protect_speech_boundaries(
            windows,
            speech_cues=speech_cues,
            match_duration_seconds=40.0,
            max_extension_seconds=3.0,
        )

        self.assertEqual(len(protected), 1)
        self.assertEqual(protected[0].started_at_seconds, 10.0)
        # Uncapped protection would extend to 34.0; shrink mode caps the
        # extension at max_extension_seconds past the original end.
        self.assertEqual(protected[0].ended_at_seconds, 33.0)

    def test_restore_missing_kda_event_windows_requires_full_key_event_coverage(self) -> None:
        service = HighlightPlannerService(self.settings)
        windows = [
            HighlightClipWindow(
                started_at_seconds=0.0,
                ended_at_seconds=38.0,
                reason="condensed_match_context",
            ),
            HighlightClipWindow(
                started_at_seconds=83.0,
                ended_at_seconds=88.0,
                reason="condensed_continuity",
            ),
            HighlightClipWindow(
                started_at_seconds=127.0,
                ended_at_seconds=132.0,
                reason="condensed_continuity",
            ),
            HighlightClipWindow(
                started_at_seconds=177.0,
                ended_at_seconds=182.0,
                reason="condensed_continuity",
            ),
        ]
        kda_cues = [
            ClassifiedCue(
                70.0,
                185.0,
                "kda_change kills=0->1 deaths=0->1 previous_at=130.000 current_at=180.000",
                "key_event",
                1.0,
            )
        ]

        restored = service._restore_missing_kda_event_windows(
            windows,
            kda_event_cues=kda_cues,
            match_duration_seconds=300.0,
        )

        self.assertTrue(
            any(
                window.reason == "condensed_key_event"
                and window.started_at_seconds <= 70.0
                and window.ended_at_seconds >= 185.0
                for window in restored
            )
        )

    def test_condensed_helpers_clip_cues_and_windows_to_boundary_duration(self) -> None:
        service = HighlightPlannerService(self.settings)

        cues = service._clip_cues_to_duration(
            [
                _SrtCue(10.0, 20.0, "opening"),
                _SrtCue(170.0, 190.0, "tail narration"),
                _SrtCue(190.0, 200.0, "after boundary"),
            ],
            180.0,
        )
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[1].ended_at_seconds, 180.0)

        windows = service._clamp_highlight_windows(
            [
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=30.0,
                    reason="condensed_match_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=150.0,
                    ended_at_seconds=210.0,
                    reason="condensed_key_event",
                ),
                HighlightClipWindow(
                    started_at_seconds=160.0,
                    ended_at_seconds=175.0,
                    reason="condensed_continuity",
                ),
                HighlightClipWindow(
                    started_at_seconds=181.0,
                    ended_at_seconds=190.0,
                    reason="condensed_key_event",
                ),
            ],
            180.0,
        )
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[-1].reason, "condensed_key_event")
        self.assertEqual(windows[-1].ended_at_seconds, 180.0)

    def test_condensed_clamp_preserves_short_boundary_context_windows(self) -> None:
        service = HighlightPlannerService(self.settings)

        windows = service._clamp_highlight_windows(
            [
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=1.0,
                    reason="condensed_match_context",
                ),
                HighlightClipWindow(
                    started_at_seconds=10.0,
                    ended_at_seconds=11.0,
                    reason="condensed_continuity",
                ),
                HighlightClipWindow(
                    started_at_seconds=99.0,
                    ended_at_seconds=100.0,
                    reason="condensed_match_context",
                ),
            ],
            100.0,
        )

        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0].started_at_seconds, 0.0)
        self.assertEqual(windows[0].ended_at_seconds, 1.0)
        self.assertEqual(windows[-1].started_at_seconds, 99.0)
        self.assertEqual(windows[-1].ended_at_seconds, 100.0)

    def test_short_start_context_is_not_extended_by_opening_subtitle(self) -> None:
        settings = self.settings.model_copy(
            deep=True,
            update={
                "highlights": self.settings.highlights.model_copy(
                    update={"condensed_start_edge_seconds": 1.0}
                )
            },
        )
        service = HighlightPlannerService(settings)

        windows = service._protect_speech_boundaries(
            [
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=1.0,
                    reason="condensed_match_context",
                )
            ],
            speech_cues=[_SrtCue(0.0, 8.0, "opening low value narration")],
            match_duration_seconds=100.0,
        )

        self.assertEqual(windows[0].started_at_seconds, 0.0)
        self.assertEqual(windows[0].ended_at_seconds, 1.0)

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

    def test_semantic_weight_zero_preserves_legacy_density(self) -> None:
        service = HighlightPlannerService(self.settings)
        self.settings.llm.story_analysis_enabled = True
        self.settings.llm.story_shadow_mode = False
        self.settings.llm.semantic_weight = 0.0
        window, plan, asset = self._semantic_density_fixture("drop")

        service._active_semantic_reference = service._semantic_reference_for_plan(plan, asset)

        self.assertEqual(service._active_semantic_reference, [])
        self.assertEqual(service._semantic_value_multiplier(window), 1.0)

    def test_semantic_drop_lowers_only_finalizer_value_density(self) -> None:
        service = HighlightPlannerService(self.settings)
        self.settings.llm.story_analysis_enabled = True
        self.settings.llm.story_shadow_mode = False
        self.settings.llm.semantic_weight = 0.5
        window, plan, asset = self._semantic_density_fixture("drop")
        service._active_semantic_reference = service._semantic_reference_for_plan(plan, asset)

        multiplier = service._semantic_value_multiplier(window)

        self.assertGreater(multiplier, 0.0)
        self.assertLess(multiplier, 1.0)

    @staticmethod
    def _semantic_density_fixture(
        recommendation: str,
    ) -> tuple[HighlightClipWindow, HighlightPlanAsset, SemanticAssetView]:
        from arl.shared.semantic_ids import semantic_reference_id

        window = HighlightClipWindow(
            started_at_seconds=10.0,
            ended_at_seconds=20.0,
            reason="condensed_key_event",
        )
        plan = HighlightPlanAsset(
            session_id="session-semantic-density",
            match_index=1,
            source_boundary_start_seconds=0.0,
            source_boundary_end_seconds=100.0,
            windows=[window],
            created_at=datetime.now(timezone.utc),
        )
        candidate_id = semantic_reference_id(
            "candidate",
            plan.session_id,
            plan.match_index,
            window.started_at_seconds,
            window.ended_at_seconds,
            window.reason,
        )
        asset = SemanticAssetView.model_validate(
            {
                "session_id": plan.session_id,
                "match_index": plan.match_index,
                "result": {
                    "candidate_decisions": [
                        {
                            "candidate_id": candidate_id,
                            "recommendation": recommendation,
                        }
                    ]
                },
            }
        )
        return window, plan, asset

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


def _death_like_frame() -> np.ndarray:
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    frame[:, :] = (42, 42, 42)
    frame[0:75, 1500:1920] = 15
    cv2.rectangle(frame, (600, 300), (1300, 670), (70, 70, 70), 3)
    cv2.rectangle(frame, (630, 340), (1270, 630), (55, 55, 55), 2)
    _draw_grid(frame, 600, 860, 780, 200)
    return frame


def _draw_grid(
    frame: np.ndarray,
    x: int,
    y: int,
    width: int,
    height: int,
) -> None:
    cv2.rectangle(frame, (x, y), (x + width, y + height), (220, 220, 220), 3)
    for offset in range(20, width, 28):
        cv2.line(frame, (x + offset, y), (x + offset, y + height), (180, 180, 180), 2)
    for offset in range(20, height, 28):
        cv2.line(frame, (x, y + offset), (x + width, y + offset), (180, 180, 180), 2)


if __name__ == "__main__":
    unittest.main()
