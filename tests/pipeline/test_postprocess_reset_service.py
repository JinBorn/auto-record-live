from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import Settings, StorageSettings
from arl.copywriter.models import (
    CoverCandidate,
    CopywriterSemanticAsset,
    CopywriterStateFile,
    LlmCopywritingResult,
    PublishingPackage,
)
from arl.editing.models import EditPlannerStateFile
from arl.exporter.models import ExporterStateFile
from arl.highlights.models import HighlightPlannerStateFile
from arl.postprocess.reset import PostProcessResetService
from arl.segmenter.models import (
    MatchStageHint,
    MatchStageSignal,
    SegmenterStateFile,
    StageSignalIngestStateFile,
)
from arl.shared.contracts import (
    CopyAsset,
    EditPlanAsset,
    ExportAsset,
    HighlightClipWindow,
    HighlightPlanAsset,
    MatchBoundary,
    MatchStage,
    SubtitleAsset,
    TimelineSegment,
)
from arl.shared.jsonl_store import append_model, load_models
from arl.subtitles.models import SubtitleStateFile


class PostProcessResetServiceTest(unittest.TestCase):
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
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reset_removes_only_target_postprocess_rows_state_and_files(self) -> None:
        target = "session-reset-target"
        other = "session-reset-other"
        now = datetime(2026, 6, 8, 10, 0, tzinfo=timezone.utc)

        target_subtitle = self.settings.storage.processed_dir / target / "match-01.srt"
        target_export = self.settings.storage.export_dir / "bilibili" / f"{target}_match01.mp4"
        target_copy = self.settings.storage.processed_dir / target / "match-01-copy.json"
        target_publishing = (
            self.settings.storage.processed_dir / target / "match-01-publishing.json"
        )
        target_cover = self.settings.storage.processed_dir / target / "match-01-cover.jpg"
        target_cover_candidate = (
            self.settings.storage.processed_dir / target / "match-01-cover-01.jpg"
        )
        target_package_dir = (
            self.settings.storage.export_dir
            / "bilibili"
            / "target-streamer - title - 20260617000000_match01"
        )
        target_published_video = target_package_dir / "video.mp4"
        target_published_cover = target_package_dir / "cover.jpg"
        target_published_cover_candidate = target_package_dir / "cover-01.jpg"
        target_published_metadata = target_package_dir / "upload.txt"
        other_subtitle = self.settings.storage.processed_dir / other / "match-01.srt"
        other_export = self.settings.storage.export_dir / "bilibili" / f"{other}_match01.mp4"
        other_copy = self.settings.storage.processed_dir / other / "match-01-copy.json"
        other_publishing = (
            self.settings.storage.processed_dir / other / "match-01-publishing.json"
        )
        other_cover = self.settings.storage.processed_dir / other / "match-01-cover.jpg"
        other_cover_candidate = (
            self.settings.storage.processed_dir / other / "match-01-cover-01.jpg"
        )
        other_package_dir = (
            self.settings.storage.export_dir
            / "bilibili"
            / "other-streamer - title - 20260617000000_match01"
        )
        other_published_video = other_package_dir / "video.mp4"
        other_published_cover = other_package_dir / "cover.jpg"
        other_published_cover_candidate = other_package_dir / "cover-01.jpg"
        other_published_metadata = other_package_dir / "upload.txt"
        for path in [
            target_subtitle,
            target_export,
            target_copy,
            target_publishing,
            target_cover,
            target_cover_candidate,
            target_published_video,
            target_published_cover,
            target_published_cover_candidate,
            target_published_metadata,
            other_subtitle,
            other_export,
            other_copy,
            other_publishing,
            other_cover,
            other_cover_candidate,
            other_published_video,
            other_published_cover,
            other_published_cover_candidate,
            other_published_metadata,
        ]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("artifact\n", encoding="utf-8")

        self._append_postprocess_rows(
            target,
            target_subtitle,
            target_export,
            target_copy,
            target_publishing,
            target_cover,
            target_published_video,
            target_published_cover,
            target_published_metadata,
            now,
            cover_candidate_path=target_cover_candidate,
            published_cover_candidate_path=target_published_cover_candidate,
        )
        self._append_postprocess_rows(
            other,
            other_subtitle,
            other_export,
            other_copy,
            other_publishing,
            other_cover,
            other_published_video,
            other_published_cover,
            other_published_metadata,
            now,
            cover_candidate_path=other_cover_candidate,
            published_cover_candidate_path=other_published_cover_candidate,
        )
        append_model(
            self.temp_root / "match-stage-signals.jsonl",
            MatchStageSignal(
                session_id=target,
                text="manual in game anchor",
                source="manual",
                at_seconds=15.0,
            ),
        )
        self._write_json(
            self.temp_root / "segmenter-state.json",
            SegmenterStateFile(
                processed_asset_keys=[
                    f"{target}:data/raw/{target}/recording-source.mp4",
                    f"{other}:data/raw/{other}/recording-source.mp4",
                ]
            ),
        )
        for filename, state in [
            (
                "subtitles-state.json",
                SubtitleStateFile(processed_match_keys=[f"{target}:1", f"{other}:1"]),
            ),
            (
                "exporter-state.json",
                ExporterStateFile(
                    processed_match_keys=[f"{target}:1", f"{other}:1"],
                    deferred_match_keys=[f"{target}:2", f"{other}:2"],
                ),
            ),
            (
                "highlight-planner-state.json",
                HighlightPlannerStateFile(
                    processed_match_keys=[f"{target}:1", f"{other}:1"]
                ),
            ),
            (
                "editing-state.json",
                EditPlannerStateFile(
                    processed_match_keys=[f"{target}:1", f"{other}:1"]
                ),
            ),
            (
                "copywriter-state.json",
                CopywriterStateFile(processed_match_keys=[f"{target}:1", f"{other}:1"]),
            ),
        ]:
            self._write_json(self.temp_root / filename, state)
        self._write_json(
            self.temp_root / "stage-signal-ingest-state.json",
            StageSignalIngestStateFile(
                processed_subtitle_keys=[
                    f"{target}:1:{target_subtitle}",
                    f"{other}:1:{other_subtitle}",
                ],
                emitted_signal_fingerprints_by_subtitle_key={
                    f"{target}:1:{target_subtitle}": ["target-fingerprint"],
                    f"{other}:1:{other_subtitle}": ["other-fingerprint"],
                },
            ),
        )

        result = PostProcessResetService(self.settings).run(session_ids={target})

        self.assertEqual(result.session_ids, [target])
        self.assertEqual(len(result.deleted_files), 10)
        self.assertEqual(result.skipped_files, [])
        self.assertFalse(target_subtitle.exists())
        self.assertFalse(target_export.exists())
        self.assertFalse(target_copy.exists())
        self.assertFalse(target_publishing.exists())
        self.assertFalse(target_cover.exists())
        self.assertFalse(target_cover_candidate.exists())
        self.assertFalse(target_published_video.exists())
        self.assertFalse(target_published_cover.exists())
        self.assertFalse(target_published_cover_candidate.exists())
        self.assertFalse(target_published_metadata.exists())
        self.assertFalse(target_package_dir.exists())
        self.assertTrue(other_subtitle.exists())
        self.assertTrue(other_export.exists())
        self.assertTrue(other_copy.exists())
        self.assertTrue(other_publishing.exists())
        self.assertTrue(other_cover.exists())
        self.assertTrue(other_cover_candidate.exists())
        self.assertTrue(other_published_video.exists())
        self.assertTrue(other_published_cover.exists())
        self.assertTrue(other_published_cover_candidate.exists())
        self.assertTrue(other_published_metadata.exists())
        self.assertTrue(other_package_dir.exists())

        self.assertEqual(self._session_ids("match-stage-hints.jsonl", MatchStageHint), [other])
        self.assertEqual(self._session_ids("match-boundaries.jsonl", MatchBoundary), [other])
        self.assertEqual(self._session_ids("subtitle-assets.jsonl", SubtitleAsset), [other])
        self.assertEqual(self._session_ids("highlight-plans.jsonl", HighlightPlanAsset), [other])
        self.assertEqual(self._session_ids("edit-plans.jsonl", EditPlanAsset), [other])
        self.assertEqual(
            self._session_ids("copywriter-semantic-assets.jsonl", CopywriterSemanticAsset),
            [other],
        )
        self.assertEqual(self._session_ids("export-assets.jsonl", ExportAsset), [other])
        self.assertEqual(self._session_ids("copy-assets.jsonl", CopyAsset), [other])
        self.assertEqual(
            self._session_ids("publishing-packages.jsonl", PublishingPackage),
            [other],
        )

        signals = load_models(self.temp_root / "match-stage-signals.jsonl", MatchStageSignal)
        self.assertEqual(
            [(signal.session_id, signal.source) for signal in signals],
            [(other, "subtitles_srt"), (target, "manual")],
        )
        self.assertEqual(
            self._read_json(self.temp_root / "segmenter-state.json")["processed_asset_keys"],
            [f"{other}:data/raw/{other}/recording-source.mp4"],
        )
        self.assertEqual(
            self._read_json(self.temp_root / "subtitles-state.json")["processed_match_keys"],
            [f"{other}:1"],
        )
        self.assertEqual(
            self._read_json(self.temp_root / "exporter-state.json")["processed_match_keys"],
            [f"{other}:1"],
        )
        self.assertEqual(
            self._read_json(self.temp_root / "exporter-state.json")["deferred_match_keys"],
            [f"{other}:2"],
        )
        self.assertEqual(
            self._read_json(self.temp_root / "highlight-planner-state.json")[
                "processed_match_keys"
            ],
            [f"{other}:1"],
        )
        self.assertEqual(
            self._read_json(self.temp_root / "editing-state.json")["processed_match_keys"],
            [f"{other}:1"],
        )
        self.assertEqual(
            self._read_json(self.temp_root / "copywriter-state.json")["processed_match_keys"],
            [f"{other}:1"],
        )
        ingest_state = StageSignalIngestStateFile.model_validate_json(
            (self.temp_root / "stage-signal-ingest-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(ingest_state.processed_subtitle_keys, [f"{other}:1:{other_subtitle}"])
        self.assertEqual(
            list(ingest_state.emitted_signal_fingerprints_by_subtitle_key),
            [f"{other}:1:{other_subtitle}"],
        )

    def test_reset_skips_deleting_artifact_paths_outside_generated_roots(self) -> None:
        target = "session-reset-unsafe"
        outside = Path(self.temp_dir.name) / "outside.srt"
        outside.write_text("keep me\n", encoding="utf-8")
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=target,
                match_index=1,
                path=str(outside),
                format="srt",
            ),
        )

        result = PostProcessResetService(self.settings).run(session_ids={target})

        self.assertTrue(outside.exists())
        self.assertEqual(result.deleted_files, [])
        self.assertEqual(result.skipped_files, [f"{outside}:outside_generated_roots"])
        self.assertEqual(load_models(self.temp_root / "subtitle-assets.jsonl", SubtitleAsset), [])

    def test_reset_deletes_orphan_generated_files_for_target_session(self) -> None:
        target = "session-reset-orphan"
        other = "session-reset-orphan-other"
        target_processed = self.settings.storage.processed_dir / target / "match-01.txt"
        target_export = self.settings.storage.export_dir / "unknown" / f"{target}_match01.txt"
        other_export = self.settings.storage.export_dir / "unknown" / f"{other}_match01.txt"
        for path in [target_processed, target_export, other_export]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("orphan\n", encoding="utf-8")

        result = PostProcessResetService(self.settings).run(session_ids={target})

        self.assertFalse(target_processed.exists())
        self.assertFalse(target_export.exists())
        self.assertTrue(other_export.exists())
        self.assertEqual(len(result.deleted_files), 2)
        self.assertEqual(result.skipped_files, [])

    def _append_postprocess_rows(
        self,
        session_id: str,
        subtitle_path: Path,
        export_path: Path,
        copy_path: Path,
        publishing_path: Path,
        cover_path: Path,
        published_video_path: Path,
        published_cover_path: Path,
        published_metadata_path: Path,
        created_at: datetime,
        cover_candidate_path: Path | None = None,
        published_cover_candidate_path: Path | None = None,
    ) -> None:
        append_model(
            self.temp_root / "match-stage-hints.jsonl",
            MatchStageHint(
                session_id=session_id,
                stage=MatchStage.IN_GAME,
                at_seconds=75.0,
            ),
        )
        append_model(
            self.temp_root / "match-stage-signals.jsonl",
            MatchStageSignal(
                session_id=session_id,
                text="in game scoreboard",
                source="subtitles_srt",
                at_seconds=75.0,
            ),
        )
        append_model(
            self.temp_root / "match-boundaries.jsonl",
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=75.0,
                ended_at_seconds=1875.0,
                confidence=0.8,
            ),
        )
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        append_model(
            self.temp_root / "highlight-plans.jsonl",
            HighlightPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=75.0,
                source_boundary_end_seconds=1875.0,
                windows=[
                    HighlightClipWindow(
                        started_at_seconds=0.0,
                        ended_at_seconds=1800.0,
                        reason="fixture",
                    )
                ],
                created_at=created_at,
            ),
        )
        append_model(
            self.temp_root / "edit-plans.jsonl",
            EditPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=75.0,
                source_boundary_end_seconds=1875.0,
                timeline=[
                    TimelineSegment(
                        role="teaser",
                        source_start_seconds=300.0,
                        source_end_seconds=330.0,
                        reason="highlight_keyword",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=0.0,
                        source_end_seconds=1800.0,
                        reason="full_validated_match",
                    ),
                ],
                created_at=created_at,
            ),
        )
        append_model(
            self.temp_root / "copywriter-semantic-assets.jsonl",
            CopywriterSemanticAsset(
                session_id=session_id,
                match_index=1,
                source_subtitle_path=str(subtitle_path),
                source_highlight_plan_path=None,
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
                    hook_line="神钩开团，团战逆转",
                ),
                token_usage={"total_tokens": 42},
                status="generated",
                created_at=created_at,
            ),
        )
        append_model(
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id=session_id,
                match_index=1,
                path=str(export_path),
                subtitle_path=str(subtitle_path),
                created_at=created_at,
            ),
        )
        append_model(
            self.temp_root / "copy-assets.jsonl",
            CopyAsset(
                session_id=session_id,
                match_index=1,
                path=str(copy_path),
                title="title",
                description="description",
                tags=["tag"],
                subtitle_path=str(subtitle_path),
                export_path=str(export_path),
                created_at=created_at,
            ),
        )
        append_model(
            self.temp_root / "publishing-packages.jsonl",
            PublishingPackage(
                session_id=session_id,
                match_index=1,
                path=str(publishing_path),
                source_subtitle_path=str(subtitle_path),
                source_export_path=str(export_path),
                source_recording_path=None,
                transcript_excerpt=["subtitle cue"],
                evidence=["00:01 subtitle cue"],
                title_candidates=["title"],
                recommended_title="title",
                summary="summary",
                cover_lines=["cover", "line"],
                tags=["tag"],
                cover_path=str(cover_path),
                cover_candidates=(
                    [
                        CoverCandidate(
                            path=str(cover_candidate_path),
                            rank=1,
                            source_timestamp_seconds=10.0,
                            score=1.0,
                            reasons=["fixture"],
                            published_path=(
                                str(published_cover_candidate_path)
                                if published_cover_candidate_path is not None
                                else None
                            ),
                        )
                    ]
                    if cover_candidate_path is not None
                    else []
                ),
                published_package_dir=str(published_video_path.parent),
                published_video_path=str(published_video_path),
                published_cover_path=str(published_cover_path),
                published_metadata_path=str(published_metadata_path),
                status="ready",
                created_at=created_at,
            ),
        )

    def _session_ids(self, filename: str, model_type) -> list[str]:
        return [
            item.session_id
            for item in load_models(self.temp_root / filename, model_type)
        ]

    def _write_json(self, path: Path, model) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
