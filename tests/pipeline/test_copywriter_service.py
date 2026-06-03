from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import Settings, StorageSettings
from arl.copywriter.models import CopywriterStateFile
from arl.copywriter.service import CopywriterService
from arl.shared.contracts import CopyAsset, ExportAsset, SubtitleAsset
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

        state = CopywriterStateFile.model_validate_json(
            (self.temp_root / "copywriter-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [f"{session_id}:1"])

        CopywriterService(self.settings).run()
        self.assertEqual(len(load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)), 1)

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

    def _now(self) -> datetime:
        return datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
