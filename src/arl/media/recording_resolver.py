from __future__ import annotations

from pathlib import Path

from arl.segmenter.durations import recording_duration_seconds as legacy_duration_seconds
from arl.shared.contracts import (
    MediaSpan,
    RecordingAsset,
    RecordingChunk,
    RecordingChunkManifest,
)


CHUNK_MANIFEST_FILENAME = "recording-chunks.json"
_WINDOW_TOLERANCE_SECONDS = 0.001


def resolve_recording_window(
    asset: RecordingAsset,
    *,
    start_seconds: float,
    end_seconds: float,
) -> list[MediaSpan]:
    if end_seconds <= start_seconds:
        return []

    manifest_path = _chunk_manifest_path(asset)
    manifest = _load_chunk_manifest(manifest_path) if manifest_path is not None else None
    if manifest is None:
        return [
            MediaSpan(
                path=asset.path,
                source_start_seconds=start_seconds,
                source_end_seconds=end_seconds,
                local_start_seconds=start_seconds,
                local_end_seconds=end_seconds,
            )
        ]

    spans: list[MediaSpan] = []
    manifest_base = manifest_path.parent if manifest_path is not None else Path(".")
    for chunk in sorted(manifest.chunks, key=lambda item: item.index):
        overlap_start = max(start_seconds, chunk.started_at_seconds)
        overlap_end = min(end_seconds, chunk.ended_at_seconds)
        if overlap_end - overlap_start <= _WINDOW_TOLERANCE_SECONDS:
            continue
        chunk_path = _resolve_chunk_path(chunk, manifest_base)
        spans.append(
            MediaSpan(
                path=str(chunk_path),
                source_start_seconds=round(overlap_start, 3),
                source_end_seconds=round(overlap_end, 3),
                local_start_seconds=round(overlap_start - chunk.started_at_seconds, 3),
                local_end_seconds=round(overlap_end - chunk.started_at_seconds, 3),
            )
        )
    return spans


def recording_duration_seconds(asset: RecordingAsset) -> float:
    manifest_path = _chunk_manifest_path(asset)
    manifest = _load_chunk_manifest(manifest_path) if manifest_path is not None else None
    if manifest is None or not manifest.chunks:
        return legacy_duration_seconds(asset)
    return max(chunk.ended_at_seconds for chunk in manifest.chunks)


def recording_primary_video_path(asset: RecordingAsset) -> Path | None:
    manifest_path = _chunk_manifest_path(asset)
    manifest = _load_chunk_manifest(manifest_path) if manifest_path is not None else None
    if manifest is None:
        path = Path(asset.path)
        return path if path.suffix.lower() != ".json" else None
    if not manifest.chunks:
        return None
    manifest_base = manifest_path.parent if manifest_path is not None else Path(".")
    return _resolve_chunk_path(sorted(manifest.chunks, key=lambda item: item.index)[0], manifest_base)


def _chunk_manifest_path(asset: RecordingAsset) -> Path | None:
    asset_path = Path(asset.path)
    if asset_path.suffix.lower() == ".json":
        return asset_path
    sidecar_path = asset_path.parent / CHUNK_MANIFEST_FILENAME
    if sidecar_path.is_file():
        return sidecar_path
    return None


def _load_chunk_manifest(path: Path | None) -> RecordingChunkManifest | None:
    if path is None or not path.is_file():
        return None
    try:
        return RecordingChunkManifest.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None


def _resolve_chunk_path(chunk: RecordingChunk, manifest_base: Path) -> Path:
    path = Path(chunk.path)
    if path.is_absolute():
        return path
    return manifest_base / path

