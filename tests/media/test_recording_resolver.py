from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.media.recording_resolver import (
    CHUNK_MANIFEST_FILENAME,
    recording_duration_seconds,
    recording_primary_video_path,
    resolve_recording_window,
)
from arl.shared.contracts import (
    RecordingAsset,
    RecordingChunk,
    RecordingChunkManifest,
    SourceType,
)


class RecordingResolverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_resolves_single_file_window(self) -> None:
        recording_path = self.root / "recording-source.mp4"
        recording_path.write_text("media", encoding="utf-8")
        asset = self._asset(recording_path)

        spans = resolve_recording_window(
            asset,
            start_seconds=5.0,
            end_seconds=12.5,
        )

        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].path, str(recording_path))
        self.assertEqual(spans[0].source_start_seconds, 5.0)
        self.assertEqual(spans[0].source_end_seconds, 12.5)
        self.assertEqual(spans[0].local_start_seconds, 5.0)
        self.assertEqual(spans[0].local_end_seconds, 12.5)
        self.assertEqual(recording_primary_video_path(asset), recording_path)

    def test_resolves_one_chunk_manifest_window(self) -> None:
        manifest_path = self._write_manifest(
            [
                RecordingChunk(
                    path="chunks/recording-00000.mp4",
                    started_at_seconds=0.0,
                    ended_at_seconds=900.0,
                    duration_seconds=900.0,
                    index=0,
                )
            ]
        )
        asset = self._asset(manifest_path)

        spans = resolve_recording_window(
            asset,
            start_seconds=120.0,
            end_seconds=180.0,
        )

        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].path, str(manifest_path.parent / "chunks/recording-00000.mp4"))
        self.assertEqual(spans[0].source_start_seconds, 120.0)
        self.assertEqual(spans[0].source_end_seconds, 180.0)
        self.assertEqual(spans[0].local_start_seconds, 120.0)
        self.assertEqual(spans[0].local_end_seconds, 180.0)
        self.assertEqual(recording_duration_seconds(asset), 900.0)

    def test_resolves_cross_chunk_window(self) -> None:
        manifest_path = self._write_manifest(
            [
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
                RecordingChunk(
                    path="chunks/recording-00002.mp4",
                    started_at_seconds=20.0,
                    ended_at_seconds=30.0,
                    duration_seconds=10.0,
                    index=2,
                ),
            ]
        )
        asset = self._asset(manifest_path)

        spans = resolve_recording_window(
            asset,
            start_seconds=8.0,
            end_seconds=23.0,
        )

        self.assertEqual(
            [
                (
                    span.source_start_seconds,
                    span.source_end_seconds,
                    span.local_start_seconds,
                    span.local_end_seconds,
                )
                for span in spans
            ],
            [
                (8.0, 10.0, 8.0, 10.0),
                (10.0, 20.0, 0.0, 10.0),
                (20.0, 23.0, 0.0, 3.0),
            ],
        )

    def test_clamps_window_to_available_chunks(self) -> None:
        manifest_path = self._write_manifest(
            [
                RecordingChunk(
                    path="chunks/recording-00000.mp4",
                    started_at_seconds=10.0,
                    ended_at_seconds=20.0,
                    duration_seconds=10.0,
                    index=0,
                )
            ]
        )
        asset = self._asset(manifest_path)

        spans = resolve_recording_window(
            asset,
            start_seconds=0.0,
            end_seconds=30.0,
        )

        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].source_start_seconds, 10.0)
        self.assertEqual(spans[0].source_end_seconds, 20.0)
        self.assertEqual(spans[0].local_start_seconds, 0.0)
        self.assertEqual(spans[0].local_end_seconds, 10.0)

    def test_invalid_sidecar_manifest_falls_back_to_single_file(self) -> None:
        recording_path = self.root / "recording-source.mp4"
        recording_path.write_text("media", encoding="utf-8")
        (self.root / CHUNK_MANIFEST_FILENAME).write_text("{broken", encoding="utf-8")
        asset = self._asset(recording_path)

        spans = resolve_recording_window(
            asset,
            start_seconds=1.0,
            end_seconds=2.0,
        )

        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].path, str(recording_path))

    def _asset(self, path: Path) -> RecordingAsset:
        return RecordingAsset(
            session_id="session-chunked",
            source_type=SourceType.DIRECT_STREAM,
            path=str(path),
            started_at=datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
            ended_at=datetime(2026, 6, 27, 12, 30, tzinfo=timezone.utc),
        )

    def _write_manifest(self, chunks: list[RecordingChunk]) -> Path:
        manifest_path = self.root / CHUNK_MANIFEST_FILENAME
        manifest = RecordingChunkManifest(
            session_id="session-chunked",
            source_type=SourceType.DIRECT_STREAM,
            path=str(manifest_path),
            started_at=datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
            ended_at=datetime(2026, 6, 27, 12, 30, tzinfo=timezone.utc),
            chunks=chunks,
            created_at=datetime(2026, 6, 27, 12, 31, tzinfo=timezone.utc),
        )
        manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return manifest_path


if __name__ == "__main__":
    unittest.main()

