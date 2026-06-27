from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
import shutil
import subprocess
from pathlib import Path

from arl.config import Settings
from arl.shared.contracts import RecordingAsset, RecordingChunkManifest, SourceType
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log


_SESSION_ID_PATTERN = re.compile(r"^session-(\d{14})-[0-9a-fA-F]+$")


@dataclass(frozen=True)
class UnregisteredRecording:
    session_id: str
    path: Path
    modified_at: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "path": str(self.path),
            "modified_at": self.modified_at.isoformat(),
        }


@dataclass(frozen=True)
class RecordingAssetRepairResult:
    scanned_recordings: int
    repaired_assets: int
    skipped_recent: int
    skipped_unreadable: int
    repaired: list[dict[str, object]]

    def as_dict(self) -> dict[str, object]:
        return {
            "scanned_recordings": self.scanned_recordings,
            "repaired_assets": self.repaired_assets,
            "skipped_recent": self.skipped_recent,
            "skipped_unreadable": self.skipped_unreadable,
            "repaired": self.repaired,
        }


class RecordingAssetRepairService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.assets_path = settings.storage.temp_dir / "recording-assets.jsonl"

    def find_unregistered(
        self,
        *,
        min_age_seconds: float = 60.0,
    ) -> list[UnregisteredRecording]:
        existing_assets = load_models(self.assets_path, RecordingAsset)
        existing_keys = {
            (asset.session_id, self._path_key(Path(asset.path)))
            for asset in existing_assets
        }

        candidates: list[UnregisteredRecording] = []
        now = datetime.now(timezone.utc)
        for path in self._raw_recording_paths():
            session_id = path.parent.name
            key = (session_id, self._path_key(path))
            if key in existing_keys:
                continue
            try:
                modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if (now - modified_at).total_seconds() < min_age_seconds:
                continue
            candidates.append(
                UnregisteredRecording(
                    session_id=session_id,
                    path=path,
                    modified_at=modified_at,
                )
            )
        return candidates

    def run(self, *, min_age_seconds: float = 60.0) -> RecordingAssetRepairResult:
        scanned = 0
        skipped_recent = 0
        skipped_unreadable = 0
        repaired: list[dict[str, object]] = []

        existing_assets = load_models(self.assets_path, RecordingAsset)
        existing_keys = {
            (asset.session_id, self._path_key(Path(asset.path)))
            for asset in existing_assets
        }
        now = datetime.now(timezone.utc)

        for path in self._raw_recording_paths():
            scanned += 1
            session_id = path.parent.name
            key = (session_id, self._path_key(path))
            if key in existing_keys:
                continue

            try:
                stat = path.stat()
            except OSError:
                skipped_unreadable += 1
                continue
            modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if (now - modified_at).total_seconds() < min_age_seconds:
                skipped_recent += 1
                continue
            if stat.st_size <= 0:
                skipped_unreadable += 1
                continue

            duration_seconds, started_at, ended_at, source_type = (
                self._recording_metadata(path, session_id, modified_at)
            )
            if duration_seconds is None or duration_seconds <= 0:
                skipped_unreadable += 1
                continue

            asset = RecordingAsset(
                session_id=session_id,
                source_type=source_type,
                path=str(path),
                started_at=started_at,
                ended_at=ended_at,
            )
            append_model(self.assets_path, asset)
            existing_keys.add(key)
            row = {
                "session_id": session_id,
                "path": str(path),
                "duration_seconds": duration_seconds,
                "started_at": started_at.isoformat(),
                "ended_at": ended_at.isoformat(),
            }
            repaired.append(row)
            log(
                "recording-repair",
                "recording asset repaired "
                f"session_id={session_id} path={path} "
                f"duration_seconds={duration_seconds:.3f}",
            )

        return RecordingAssetRepairResult(
            scanned_recordings=scanned,
            repaired_assets=len(repaired),
            skipped_recent=skipped_recent,
            skipped_unreadable=skipped_unreadable,
            repaired=repaired,
        )

    def _raw_recording_paths(self) -> list[Path]:
        raw_dir = self.settings.storage.raw_dir
        if not raw_dir.exists():
            return []
        return sorted(
            [
                *raw_dir.glob("session-*/recording-source.mp4"),
                *raw_dir.glob("session-*/recording-chunks.json"),
            ]
        )

    def _recording_metadata(
        self,
        path: Path,
        session_id: str,
        modified_at: datetime,
    ) -> tuple[float | None, datetime, datetime, SourceType]:
        if path.name == "recording-chunks.json":
            manifest = self._load_chunk_manifest(path)
            if manifest is None or not manifest.chunks:
                return None, modified_at, modified_at, SourceType.DIRECT_STREAM
            manifest_base = path.parent
            for chunk in manifest.chunks:
                chunk_path = Path(chunk.path)
                if not chunk_path.is_absolute():
                    chunk_path = manifest_base / chunk_path
                try:
                    if chunk_path.stat().st_size <= 0:
                        return None, modified_at, modified_at, manifest.source_type
                except OSError:
                    return None, modified_at, modified_at, manifest.source_type
            duration_seconds = max(chunk.ended_at_seconds for chunk in manifest.chunks)
            started_at = manifest.started_at
            ended_at = manifest.ended_at or started_at + timedelta(
                seconds=duration_seconds
            )
            return duration_seconds, started_at, ended_at, manifest.source_type

        duration_seconds = self._probe_duration_seconds(path)
        if duration_seconds is None or duration_seconds <= 0:
            return None, modified_at, modified_at, SourceType.DIRECT_STREAM
        started_at = self._started_at_from_session_id(session_id)
        if started_at is None:
            started_at = modified_at - timedelta(seconds=duration_seconds)
        ended_at = started_at + timedelta(seconds=duration_seconds)
        return duration_seconds, started_at, ended_at, SourceType.DIRECT_STREAM

    @staticmethod
    def _load_chunk_manifest(path: Path) -> RecordingChunkManifest | None:
        try:
            return RecordingChunkManifest.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None

    def _probe_duration_seconds(self, path: Path) -> float | None:
        ffprobe_path = shutil.which("ffprobe")
        if ffprobe_path is None:
            return None
        command = [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        try:
            payload = json.loads(result.stdout or "{}")
            duration = payload.get("format", {}).get("duration")
            return float(duration)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _started_at_from_session_id(session_id: str) -> datetime | None:
        match = _SESSION_ID_PATTERN.match(session_id)
        if match is None:
            return None
        raw = match.group(1)
        try:
            return datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _path_key(path: Path) -> str:
        return path.resolve(strict=False).as_posix().lower()
