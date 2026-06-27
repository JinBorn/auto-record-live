from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from arl.config import Settings, StorageSettings
from arl.recorder.asset_repair import RecordingAssetRepairService
from arl.shared.contracts import (
    RecordingAsset,
    RecordingChunk,
    RecordingChunkManifest,
    SourceType,
)
from arl.shared.jsonl_store import append_model, load_models


class RecordingAssetRepairServiceTest(unittest.TestCase):
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

    def test_repair_registers_unregistered_raw_mp4(self) -> None:
        session_id = "session-20260606101149-9fe32958"
        recording_path = self._write_raw_recording(session_id)

        with patch.object(
            RecordingAssetRepairService,
            "_probe_duration_seconds",
            return_value=120.5,
        ):
            result = RecordingAssetRepairService(self.settings).run(
                min_age_seconds=0,
            )

        self.assertEqual(result.repaired_assets, 1)
        assets = load_models(self.temp_root / "recording-assets.jsonl", RecordingAsset)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].session_id, session_id)
        self.assertEqual(Path(assets[0].path), recording_path)
        self.assertEqual(assets[0].source_type, SourceType.DIRECT_STREAM)
        self.assertEqual(
            assets[0].started_at.isoformat(),
            "2026-06-06T10:11:49+00:00",
        )
        self.assertEqual(
            assets[0].ended_at.isoformat(),
            "2026-06-06T10:13:49.500000+00:00",
        )

    def test_repair_skips_existing_asset(self) -> None:
        session_id = "session-20260606101149-9fe32958"
        recording_path = self._write_raw_recording(session_id)
        append_model(
            self.temp_root / "recording-assets.jsonl",
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.DIRECT_STREAM,
                path=str(recording_path),
                started_at=RecordingAssetRepairService._started_at_from_session_id(
                    session_id
                ),
                ended_at=RecordingAssetRepairService._started_at_from_session_id(
                    session_id
                ),
            ),
        )

        with patch.object(
            RecordingAssetRepairService,
            "_probe_duration_seconds",
            return_value=120.5,
        ):
            result = RecordingAssetRepairService(self.settings).run(
                min_age_seconds=0,
            )

        self.assertEqual(result.repaired_assets, 0)
        assets = load_models(self.temp_root / "recording-assets.jsonl", RecordingAsset)
        self.assertEqual(len(assets), 1)

    def test_repair_registers_unregistered_chunk_manifest(self) -> None:
        session_id = "session-20260606101149-9fe32958"
        manifest_path = self._write_chunk_manifest(session_id)

        result = RecordingAssetRepairService(self.settings).run(
            min_age_seconds=0,
        )

        self.assertEqual(result.repaired_assets, 1)
        assets = load_models(self.temp_root / "recording-assets.jsonl", RecordingAsset)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].session_id, session_id)
        self.assertEqual(Path(assets[0].path), manifest_path)
        self.assertEqual(assets[0].source_type, SourceType.DIRECT_STREAM)
        self.assertEqual(
            assets[0].started_at.isoformat(),
            "2026-06-06T10:11:49+00:00",
        )
        self.assertEqual(
            assets[0].ended_at.isoformat(),
            "2026-06-06T10:12:09+00:00",
        )

    def _write_raw_recording(self, session_id: str) -> Path:
        recording_path = self.settings.storage.raw_dir / session_id / "recording-source.mp4"
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_bytes(b"fake mp4 bytes")
        old_time = 1_000_000_000
        os.utime(recording_path, (old_time, old_time))
        return recording_path

    def _write_chunk_manifest(self, session_id: str) -> Path:
        raw_dir = self.settings.storage.raw_dir / session_id
        chunk_dir = raw_dir / "chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        first_chunk = chunk_dir / "recording-00000.mp4"
        second_chunk = chunk_dir / "recording-00001.mp4"
        first_chunk.write_bytes(b"chunk-0")
        second_chunk.write_bytes(b"chunk-1")
        manifest_path = raw_dir / "recording-chunks.json"
        started_at = datetime(2026, 6, 6, 10, 11, 49, tzinfo=timezone.utc)
        manifest = RecordingChunkManifest(
            session_id=session_id,
            source_type=SourceType.DIRECT_STREAM,
            path=str(manifest_path),
            started_at=started_at,
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
            created_at=started_at,
        )
        manifest_path.write_text(
            manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        old_time = 1_000_000_000
        os.utime(manifest_path, (old_time, old_time))
        return manifest_path


if __name__ == "__main__":
    unittest.main()
