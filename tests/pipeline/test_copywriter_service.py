from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from arl.config import Settings, StorageSettings
from arl.copywriter.cover import render_cover
from arl.copywriter.models import CopywriterStateFile, PublishingPackage
from arl.copywriter.service import CopywriterService
from arl.shared.contracts import (
    CopyAsset,
    ExportAsset,
    HighlightClipWindow,
    HighlightPlanAsset,
    MatchBoundary,
    RecordingAsset,
    SourceType,
    SubtitleAsset,
)
from arl.shared.jsonl_store import append_model, load_models


class CopywriterServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.temp_root = self.root / "tmp"
        self.processed_root = self.root / "processed"
        self.export_root = self.root / "exports"
        self.settings = Settings(
            storage=StorageSettings(
                raw_dir=self.root / "raw",
                processed_dir=self.processed_root,
                export_dir=self.export_root,
                temp_dir=self.temp_root,
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_generates_copy_asset_from_subtitle_and_export(self) -> None:
        session_id = "session-copywriter-001"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:00,000 --> 00:00:02,000\n你怎么出现这种装备跟在里?\n\n"
            "2\n00:00:02,000 --> 00:00:04,000\n刚才你们冒出这种装备\n",
        )
        export_path = self._write_export(session_id)
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
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id=session_id,
                match_index=1,
                path=str(export_path),
                subtitle_path=str(subtitle_path),
                created_at=self._now(),
            ),
        )

        CopywriterService(self.settings).run()

        copy_assets = load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)
        self.assertEqual(len(copy_assets), 1)
        asset = copy_assets[0]
        self.assertEqual(asset.session_id, session_id)
        self.assertEqual(asset.match_index, 1)
        self.assertEqual(asset.export_path, str(export_path))
        self.assertIn("装备", asset.title)
        self.assertIn("装备选择", asset.tags)

        output_path = Path(asset.path)
        self.assertTrue(output_path.exists())
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["recommended_title"], asset.title)
        self.assertEqual(
            payload["transcript_excerpt"],
            ["你怎么出现这种装备跟在里?", "刚才你们冒出这种装备"],
        )
        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertEqual(package.session_id, session_id)
        self.assertEqual(package.match_index, 1)
        self.assertEqual(package.recommended_title, asset.title)
        self.assertIn("summary", json.loads(Path(package.path or "").read_text(encoding="utf-8")))
        self.assertEqual(package.cover_path, None)

        state = CopywriterStateFile.model_validate_json(
            (self.temp_root / "copywriter-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [f"{session_id}:1"])

        CopywriterService(self.settings).run()
        self.assertEqual(len(load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)), 1)
        self.assertEqual(
            len(load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)),
            1,
        )

        package_output_path = Path(package.path or "")
        output_path.unlink()
        package_output_path.unlink()
        CopywriterService(self.settings).run()
        self.assertTrue(output_path.exists())
        self.assertTrue(package_output_path.exists())
        self.assertEqual(len(load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)), 1)
        self.assertEqual(
            len(load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)),
            1,
        )

    def test_missing_subtitle_is_skipped_without_processing_key(self) -> None:
        session_id = "session-copywriter-missing"
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(self.processed_root / session_id / "missing.srt"),
                format="srt",
            ),
        )

        CopywriterService(self.settings).run()

        self.assertEqual(load_models(self.temp_root / "copy-assets.jsonl", CopyAsset), [])
        state = CopywriterStateFile.model_validate_json(
            (self.temp_root / "copywriter-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [])

    def test_filters_by_session_ids(self) -> None:
        for session_id in ["session-copywriter-filter-a", "session-copywriter-filter-b"]:
            subtitle_path = self._write_subtitle(
                session_id,
                "1\n00:00:00,000 --> 00:00:02,000\nfiltered subtitle\n",
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

        CopywriterService(self.settings).run(session_ids={"session-copywriter-filter-b"})

        copy_assets = load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)
        self.assertEqual(len(copy_assets), 1)
        self.assertEqual(copy_assets[0].session_id, "session-copywriter-filter-b")

    def test_publishing_package_prefers_highlight_window_cues(self) -> None:
        session_id = "session-copywriter-highlight"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:00,000 --> 00:00:02,000\n普通开局先补刀\n\n"
            "2\n00:01:00,000 --> 00:01:04,000\n上单电刀AP机器人 清线快伤害高\n",
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
            self.temp_root / "match-boundaries.jsonl",
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=100.0,
                confidence=0.95,
            ),
        )
        append_model(
            self.temp_root / "highlight-plans.jsonl",
            HighlightPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=100.0,
                windows=[
                    HighlightClipWindow(
                        started_at_seconds=58.0,
                        ended_at_seconds=66.0,
                        reason="highlight_keyword",
                    )
                ],
                created_at=self._now(),
            ),
        )

        CopywriterService(self.settings).run()

        copy_assets = load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)
        self.assertEqual(len(copy_assets), 1)
        self.assertIn("电刀AP机器人", copy_assets[0].title)
        self.assertNotIn("普通开局", copy_assets[0].title)

        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertIn("电刀AP机器人", package.recommended_title)
        self.assertIn("电刀", "".join(package.cover_lines))
        self.assertEqual(package.evidence[0], "01:00 上单电刀AP机器人 清线快伤害高")

    def test_cover_renderer_is_optional_and_records_cover_path(self) -> None:
        session_id = "session-copywriter-cover"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:02,000 --> 00:00:04,000\n上单电刀AP机器人 清线快伤害高\n",
        )
        recording_path = self._write_recording(session_id)
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
            self.temp_root / "recording-assets.jsonl",
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.DIRECT_STREAM,
                path=str(recording_path),
                started_at=self._now(),
                ended_at=self._now(),
            ),
        )
        append_model(
            self.temp_root / "match-boundaries.jsonl",
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=10.0,
                ended_at_seconds=100.0,
                confidence=0.95,
            ),
        )
        seen: dict[str, object] = {}

        def _cover_renderer(
            recording: Path,
            output: Path,
            cover_lines: list[str],
            *,
            at_seconds: float,
        ) -> bool:
            seen["recording"] = recording
            seen["cover_lines"] = cover_lines
            seen["at_seconds"] = at_seconds
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("cover", encoding="utf-8")
            return True

        CopywriterService(self.settings, cover_renderer=_cover_renderer).run()

        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertIsNotNone(package.cover_path)
        self.assertTrue(Path(package.cover_path or "").exists())
        self.assertEqual(seen["recording"], recording_path)
        self.assertEqual(seen["at_seconds"], 12.0)
        seen_cover_lines = seen["cover_lines"]
        self.assertIsInstance(seen_cover_lines, list)
        self.assertIn("电刀", "".join(str(item) for item in seen_cover_lines))

    def test_render_cover_skips_when_ffmpeg_is_missing(self) -> None:
        recording_path = self._write_recording("session-copywriter-no-ffmpeg")
        output_path = self.processed_root / "session-copywriter-no-ffmpeg" / "cover.jpg"

        with patch("arl.copywriter.cover.shutil.which", return_value=None):
            rendered = render_cover(recording_path, output_path, ["封面文案"])

        self.assertFalse(rendered)
        self.assertFalse(output_path.exists())

    def _write_subtitle(self, session_id: str, content: str) -> Path:
        path = self.processed_root / session_id / "match-01.srt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _write_export(self, session_id: str) -> Path:
        path = self.export_root / f"{session_id}_match01.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
        return path

    def _write_recording(self, session_id: str) -> Path:
        path = self.root / "raw" / session_id / "recording-source.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
        return path

    def _now(self) -> datetime:
        return datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
