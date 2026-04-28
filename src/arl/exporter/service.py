from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from arl.config import Settings
from arl.exporter.models import ExporterStateFile
from arl.shared.contracts import ExportAsset, MatchBoundary, RecordingAsset, SubtitleAsset
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log


class ExporterService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.boundaries_path = settings.storage.temp_dir / "match-boundaries.jsonl"
        self.subtitles_path = settings.storage.temp_dir / "subtitle-assets.jsonl"
        self.exports_path = settings.storage.temp_dir / "export-assets.jsonl"
        self.state_path = settings.storage.temp_dir / "exporter-state.json"

    def run(self) -> None:
        log("exporter", "starting")
        log("exporter", f"ffmpeg_enabled={self.settings.export.enable_ffmpeg}")
        boundaries = load_models(self.boundaries_path, MatchBoundary)
        subtitles = load_models(self.subtitles_path, SubtitleAsset)
        recording_assets = load_models(
            self.settings.storage.temp_dir / "recording-assets.jsonl",
            RecordingAsset,
        )
        subtitle_map = {(item.session_id, item.match_index): item for item in subtitles}
        recording_by_session = {item.session_id: item for item in recording_assets}
        state = self._load_state()

        processed = 0
        for boundary in boundaries:
            key = self._key(boundary.session_id, boundary.match_index)
            if key in state.processed_match_keys:
                continue

            subtitle = subtitle_map.get((boundary.session_id, boundary.match_index))
            if subtitle is None:
                log(
                    "exporter",
                    f"missing subtitle session_id={boundary.session_id} match_index={boundary.match_index}",
                )
                continue

            recording_asset = recording_by_session.get(boundary.session_id)
            output_path = self._write_export(boundary, subtitle, recording_asset)
            export_asset = ExportAsset(
                session_id=boundary.session_id,
                match_index=boundary.match_index,
                path=str(output_path),
                subtitle_path=subtitle.path,
                created_at=datetime.now(timezone.utc),
            )
            append_model(self.exports_path, export_asset)
            state.processed_match_keys.append(key)
            processed += 1
            log(
                "exporter",
                f"export asset written session_id={boundary.session_id} match_index={boundary.match_index}",
            )

        self._save_state(state)
        log("exporter", f"processed_exports={processed}")

    def _write_export(
        self,
        boundary: MatchBoundary,
        subtitle: SubtitleAsset,
        recording_asset: RecordingAsset | None,
    ):
        ffmpeg_path = shutil.which("ffmpeg")
        if (
            self.settings.export.enable_ffmpeg
            and recording_asset is not None
            and self._looks_like_video(recording_asset.path)
            and Path(recording_asset.path).exists()
            and ffmpeg_path is not None
        ):
            return self._write_export_with_ffmpeg(boundary, subtitle, recording_asset)

        if self.settings.export.enable_ffmpeg:
            if recording_asset is None:
                reason = "missing_recording_asset"
            elif not self._looks_like_video(recording_asset.path):
                reason = "non_video_recording_asset"
            elif not Path(recording_asset.path).exists():
                reason = "recording_asset_not_found"
            elif ffmpeg_path is None:
                reason = "missing_binary"
            else:
                reason = "unmet_prerequisite"
            log(
                "exporter",
                f"ffmpeg skipped session_id={boundary.session_id} match_index={boundary.match_index} reason={reason}",
            )

        return self._write_placeholder_export(boundary, subtitle)

    def _write_placeholder_export(
        self,
        boundary: MatchBoundary,
        subtitle: SubtitleAsset,
    ) -> Path:
        output_dir = self.settings.storage.export_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{boundary.session_id}_match{boundary.match_index:02d}.txt"
        output_path.write_text(
            (
                "placeholder exported video artifact\n"
                f"session_id={boundary.session_id}\n"
                f"match_index={boundary.match_index}\n"
                f"subtitle_path={subtitle.path}\n"
            ),
            encoding="utf-8",
        )
        return output_path

    def _write_export_with_ffmpeg(
        self,
        boundary: MatchBoundary,
        subtitle: SubtitleAsset,
        recording_asset: RecordingAsset,
    ) -> Path:
        output_dir = self.settings.storage.export_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{boundary.session_id}_match{boundary.match_index:02d}.mp4"
        subtitle_path = Path(subtitle.path).resolve()
        subtitle_filter = self._subtitle_filter_arg(subtitle_path)

        command = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(boundary.started_at_seconds),
            "-to",
            str(boundary.ended_at_seconds),
            "-i",
            recording_asset.path,
            "-vf",
            subtitle_filter,
            "-preset",
            self.settings.export.ffmpeg_preset,
            "-crf",
            str(self.settings.export.ffmpeg_crf),
            str(output_path),
        ]
        attempts = self.settings.export.ffmpeg_max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                subprocess.run(
                    command,
                    check=True,
                    timeout=self.settings.export.ffmpeg_timeout_seconds,
                )
                return output_path
            except (subprocess.SubprocessError, OSError) as error:
                log(
                    "exporter",
                    "ffmpeg export failed "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"attempt={attempt}/{attempts} reason={error}",
                )

        log(
            "exporter",
            f"ffmpeg fallback placeholder session_id={boundary.session_id} match_index={boundary.match_index}",
        )
        return self._write_placeholder_export(boundary, subtitle)

    def _looks_like_video(self, path: str) -> bool:
        suffix = Path(path).suffix.lower()
        return suffix in {".mp4", ".mkv", ".flv", ".ts", ".mov"}

    def _subtitle_filter_arg(self, subtitle_path: Path) -> str:
        escaped = str(subtitle_path).replace("\\", "\\\\").replace(":", "\\:")
        return f"subtitles={escaped}"

    def _load_state(self) -> ExporterStateFile:
        if not self.state_path.exists():
            return ExporterStateFile()
        return ExporterStateFile.model_validate_json(self.state_path.read_text(encoding="utf-8"))

    def _save_state(self, state: ExporterStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _key(self, session_id: str, match_index: int) -> str:
        return f"{session_id}:{match_index}"
