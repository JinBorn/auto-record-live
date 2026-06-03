from __future__ import annotations

import shutil
import subprocess  # noqa: F401 — re-exported as patch shim so existing tests can mock arl.exporter.service.subprocess.run after the ffmpeg_runner refactor
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arl.config import Settings
from arl.exporter.models import ExporterAuditEvent, ExporterStateFile
from arl.shared.contracts import ExportAsset, MatchBoundary, RecordingAsset, SubtitleAsset
from arl.shared.failure_contracts import FailureDecision
from arl.shared.ffmpeg_runner import rotate_stderr_logs, run_ffmpeg_attempt
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log


class ExporterService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.boundaries_path = settings.storage.temp_dir / "match-boundaries.jsonl"
        self.subtitles_path = settings.storage.temp_dir / "subtitle-assets.jsonl"
        self.exports_path = settings.storage.temp_dir / "export-assets.jsonl"
        self.state_path = settings.storage.temp_dir / "exporter-state.json"
        self.audit_path = settings.storage.temp_dir / "exporter-events.jsonl"
        self.stderr_dir = settings.storage.temp_dir / "exporter-stderr"

    def run(self) -> None:
        log("exporter", "starting")
        log("exporter", f"ffmpeg_enabled={self.settings.export.enable_ffmpeg}")
        rotate_stderr_logs(self.stderr_dir, self.settings.export.stderr_retain_count)
        boundaries = load_models(self.boundaries_path, MatchBoundary)
        subtitles = load_models(self.subtitles_path, SubtitleAsset)
        recording_assets = load_models(
            self.settings.storage.temp_dir / "recording-assets.jsonl",
            RecordingAsset,
        )
        subtitle_map = {(item.session_id, item.match_index): item for item in subtitles}
        recording_by_session = {item.session_id: item for item in recording_assets}
        state = self._load_state()
        consecutive_fallbacks = 0
        fallback_budget = self.settings.export.batch_fallback_budget
        self._last_failure_classification: FailureDecision | None = None
        self._last_failure_reason: str | None = None

        processed = 0
        for index, boundary in enumerate(boundaries):
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
            output_path, was_ffmpeg_fallback = self._write_export(
                boundary,
                subtitle,
                recording_asset,
            )
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
            if was_ffmpeg_fallback:
                consecutive_fallbacks += 1
                if consecutive_fallbacks >= fallback_budget:
                    remaining_matches = len(boundaries) - index - 1
                    self._append_batch_aborted_audit(
                        boundary=boundary,
                        consecutive_fallbacks=consecutive_fallbacks,
                        remaining_matches=remaining_matches,
                    )
                    log(
                        "exporter",
                        "batch aborted "
                        f"budget={fallback_budget} "
                        f"consecutive_fallbacks={consecutive_fallbacks} "
                        f"remaining_matches={remaining_matches}",
                    )
                    break
            else:
                consecutive_fallbacks = 0

        self._save_state(state)
        log("exporter", f"processed_exports={processed}")

    def _write_export(
        self,
        boundary: MatchBoundary,
        subtitle: SubtitleAsset,
        recording_asset: RecordingAsset | None,
    ) -> tuple[Path, bool]:
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

        return self._write_placeholder_export(boundary, subtitle), False

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
    ) -> tuple[Path, bool]:
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
        basename = f"{boundary.session_id}_match{boundary.match_index:02d}"
        last_outcome = None
        for attempt in range(1, attempts + 1):
            outcome = run_ffmpeg_attempt(
                command,
                timeout=self.settings.export.ffmpeg_timeout_seconds,
                stderr_log_dir=self.stderr_dir,
                stderr_log_basename=basename,
                attempt=attempt,
            )
            if outcome.success:
                self._append_audit(
                    "ffmpeg_export_succeeded",
                    session_id=boundary.session_id,
                    match_index=boundary.match_index,
                    attempt=attempt,
                    max_attempts=attempts,
                )
                return output_path, False
            last_outcome = outcome
            fd = outcome.classification
            self._last_failure_classification = fd
            self._last_failure_reason = outcome.reason
            log(
                "exporter",
                "ffmpeg export failed "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"attempt={attempt}/{attempts} reason={outcome.reason}",
            )
            self._append_audit(
                "ffmpeg_export_failed",
                session_id=boundary.session_id,
                match_index=boundary.match_index,
                reason=outcome.reason,
                decision="attempt_failed",
                failure_category=fd.failure_category,
                is_retryable=fd.is_retryable,
                reason_code=fd.reason_code,
                reason_detail=outcome.reason,
                attempt=attempt,
                max_attempts=attempts,
                stderr_excerpt=outcome.stderr_excerpt,
                stderr_log_path=outcome.stderr_log_path,
            )
            if not fd.is_retryable:
                break
            if attempt < attempts:
                time.sleep(self._backoff_seconds(attempt))

        fd = last_outcome.classification
        log(
            "exporter",
            f"ffmpeg fallback placeholder session_id={boundary.session_id} match_index={boundary.match_index}",
        )
        self._append_audit(
            "ffmpeg_export_fallback_placeholder",
            session_id=boundary.session_id,
            match_index=boundary.match_index,
            reason=last_outcome.reason,
            decision="fallback_placeholder",
            failure_category=fd.failure_category,
            is_retryable=fd.is_retryable,
            reason_code=fd.reason_code,
            reason_detail=last_outcome.reason,
            attempt=attempts,
            max_attempts=attempts,
        )
        return self._write_placeholder_export(boundary, subtitle), True

    def _append_batch_aborted_audit(
        self,
        *,
        boundary: MatchBoundary,
        consecutive_fallbacks: int,
        remaining_matches: int,
    ) -> None:
        fd = self._last_failure_classification
        if fd is None:
            return
        self._append_audit(
            "ffmpeg_export_batch_aborted",
            session_id=boundary.session_id,
            match_index=boundary.match_index,
            reason=self._last_failure_reason,
            decision="batch_aborted",
            failure_category=fd.failure_category,
            is_retryable=fd.is_retryable,
            reason_code=fd.reason_code,
            reason_detail=self._last_failure_reason or "unknown",
            consecutive_fallbacks=consecutive_fallbacks,
            remaining_matches=remaining_matches,
        )

    def _append_audit(self, event_type: str, **fields: Any) -> None:
        event = ExporterAuditEvent(
            event_type=event_type,
            created_at=datetime.now(timezone.utc),
            **fields,
        )
        append_model(self.audit_path, event)

    def _looks_like_video(self, path: str) -> bool:
        suffix = Path(path).suffix.lower()
        return suffix in {".mp4", ".mkv", ".flv", ".ts", ".mov"}

    def _backoff_seconds(self, attempt: int) -> float:
        initial = self.settings.export.backoff_initial_seconds
        maximum = self.settings.export.backoff_max_seconds
        return min(initial * (2 ** (attempt - 1)), maximum)

    def _subtitle_filter_arg(self, subtitle_path: Path) -> str:
        escaped = subtitle_path.as_posix().replace(":", "\\:")
        return f"subtitles='{escaped}'"

    def _load_state(self) -> ExporterStateFile:
        if not self.state_path.exists():
            return ExporterStateFile()
        return ExporterStateFile.model_validate_json(self.state_path.read_text(encoding="utf-8"))

    def _save_state(self, state: ExporterStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _key(self, session_id: str, match_index: int) -> str:
        return f"{session_id}:{match_index}"
