from __future__ import annotations

import json
import shutil
import subprocess  # noqa: F401 — re-exported as patch shim so existing tests can mock arl.exporter.service.subprocess.run after the ffmpeg_runner refactor
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arl.config import Settings
from arl.exporter.models import ExporterAuditEvent, ExporterStateFile
from arl.orchestrator.state_store import load_orchestrator_state
from arl.segmenter.durations import recording_duration_seconds
from arl.shared.contracts import (
    ExportAsset,
    HighlightPlanAsset,
    MatchBoundary,
    RecordingAsset,
    SubtitleAsset,
)
from arl.shared.failure_contracts import FailureDecision, classify_failure_reason
from arl.shared.ffmpeg_runner import rotate_stderr_logs, run_ffmpeg_attempt
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log


class ExporterService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.boundaries_path = settings.storage.temp_dir / "match-boundaries.jsonl"
        self.subtitles_path = settings.storage.temp_dir / "subtitle-assets.jsonl"
        self.highlight_plans_path = settings.storage.temp_dir / "highlight-plans.jsonl"
        self.exports_path = settings.storage.temp_dir / "export-assets.jsonl"
        self.state_path = settings.storage.temp_dir / "exporter-state.json"
        self.audit_path = settings.storage.temp_dir / "exporter-events.jsonl"
        self.stderr_dir = settings.storage.temp_dir / "exporter-stderr"

    def run(
        self,
        *,
        session_ids: set[str] | None = None,
        match_indices: set[int] | None = None,
        force_reprocess: bool = False,
    ) -> None:
        log("exporter", "starting")
        log("exporter", f"ffmpeg_enabled={self.settings.export.enable_ffmpeg}")
        log("exporter", f"ffmpeg_video_codec={self.settings.export.ffmpeg_video_codec}")
        log("exporter", f"burn_subtitles={int(self.settings.export.burn_subtitles)}")
        log(
            "exporter",
            f"use_highlight_plans={int(self.settings.export.use_highlight_plans)}",
        )
        rotate_stderr_logs(self.stderr_dir, self.settings.export.stderr_retain_count)
        all_boundaries = load_models(self.boundaries_path, MatchBoundary)
        boundaries = self._filter_boundaries(
            all_boundaries,
            session_ids=session_ids,
            match_indices=match_indices,
        )
        if session_ids is not None or match_indices is not None:
            session_filter = ",".join(sorted(session_ids)) if session_ids is not None else "-"
            match_index_filter = (
                ",".join(str(item) for item in sorted(match_indices))
                if match_indices is not None
                else "-"
            )
            log(
                "exporter",
                "filters "
                f"total_boundaries={len(all_boundaries)} matched_boundaries={len(boundaries)} "
                f"session_ids={session_filter} match_indices={match_index_filter}",
            )
        subtitles = load_models(self.subtitles_path, SubtitleAsset)
        highlight_plans = (
            load_models(self.highlight_plans_path, HighlightPlanAsset)
            if self.settings.export.use_highlight_plans
            else []
        )
        recording_assets = load_models(
            self.settings.storage.temp_dir / "recording-assets.jsonl",
            RecordingAsset,
        )
        subtitle_map = {(item.session_id, item.match_index): item for item in subtitles}
        highlight_plan_map = {
            (item.session_id, item.match_index): item for item in highlight_plans
        }
        recording_by_session = {item.session_id: item for item in recording_assets}
        platform_by_session = self._platform_by_session()
        state = self._load_state()
        processed_keys = set(state.processed_match_keys)
        deferred_keys = set(state.deferred_match_keys)
        existing_output_keys = {
            self._key(asset.session_id, asset.match_index)
            for asset in load_models(self.exports_path, ExportAsset)
            if Path(asset.path).exists()
        }
        consecutive_fallbacks = 0
        fallback_budget = self.settings.export.batch_fallback_budget
        self._last_failure_classification: FailureDecision | None = None
        self._last_failure_reason: str | None = None

        processed = 0
        for index, boundary in enumerate(boundaries):
            key = self._key(boundary.session_id, boundary.match_index)
            if (
                not force_reprocess
                and key in processed_keys
                and key in existing_output_keys
            ):
                continue
            if not force_reprocess and key in deferred_keys:
                continue
            if force_reprocess and key in processed_keys:
                log(
                    "exporter",
                    "force reprocessing export output "
                    f"session_id={boundary.session_id} match_index={boundary.match_index}",
                )
            elif force_reprocess and key in deferred_keys:
                log(
                    "exporter",
                    "force reprocessing deferred export "
                    f"session_id={boundary.session_id} match_index={boundary.match_index}",
                )
            elif key in processed_keys:
                log(
                    "exporter",
                    "reprocessing missing export output "
                    f"session_id={boundary.session_id} match_index={boundary.match_index}",
                )

            recording_asset = recording_by_session.get(boundary.session_id)

            if self._is_incomplete_boundary(boundary):
                log(
                    "exporter",
                    "skip incomplete match boundary "
                    f"session_id={boundary.session_id} "
                    f"match_index={boundary.match_index} "
                    f"confidence={boundary.confidence:.2f} "
                    f"reason={boundary.reason or 'unknown'}",
                )
                continue

            if (
                not force_reprocess
                and self._is_low_confidence_full_recording_boundary(
                    boundary,
                    recording_asset,
                )
            ):
                log(
                    "exporter",
                    "deferred low-confidence full-recording boundary "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    "reason=no_reliable_edit_signal",
                )
                self._mark_deferred(state, deferred_keys, key)
                continue

            subtitle = subtitle_map.get((boundary.session_id, boundary.match_index))
            if subtitle is None:
                log(
                    "exporter",
                    f"missing subtitle session_id={boundary.session_id} match_index={boundary.match_index}",
                )
                continue
            if not Path(subtitle.path).exists():
                log(
                    "exporter",
                    "missing subtitle file "
                    f"session_id={boundary.session_id} match_index={boundary.match_index}",
                )
                continue

            platform = platform_by_session.get(boundary.session_id, "unknown")
            highlight_plan = (
                self._valid_highlight_plan(
                    highlight_plan_map.get((boundary.session_id, boundary.match_index)),
                    boundary,
                )
                if self.settings.export.use_highlight_plans
                else None
            )
            output_path, was_ffmpeg_fallback = self._write_export(
                boundary,
                subtitle,
                recording_asset,
                platform,
                highlight_plan,
            )
            if output_path is None:
                self._mark_deferred(state, deferred_keys, key)
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
                continue
            export_asset = ExportAsset(
                session_id=boundary.session_id,
                match_index=boundary.match_index,
                path=str(output_path),
                subtitle_path=subtitle.path,
                created_at=datetime.now(timezone.utc),
            )
            append_model(self.exports_path, export_asset)
            if key not in processed_keys:
                state.processed_match_keys.append(key)
                processed_keys.add(key)
            self._clear_deferred(state, deferred_keys, key)
            existing_output_keys.add(key)
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

    def _filter_boundaries(
        self,
        boundaries: list[MatchBoundary],
        *,
        session_ids: set[str] | None,
        match_indices: set[int] | None,
    ) -> list[MatchBoundary]:
        if session_ids is None and match_indices is None:
            return boundaries
        filtered: list[MatchBoundary] = []
        for boundary in boundaries:
            if session_ids is not None and boundary.session_id not in session_ids:
                continue
            if match_indices is not None and boundary.match_index not in match_indices:
                continue
            filtered.append(boundary)
        return filtered

    @staticmethod
    def _mark_deferred(
        state: ExporterStateFile,
        deferred_keys: set[str],
        key: str,
    ) -> None:
        if key in deferred_keys:
            return
        state.deferred_match_keys.append(key)
        deferred_keys.add(key)

    @staticmethod
    def _clear_deferred(
        state: ExporterStateFile,
        deferred_keys: set[str],
        key: str,
    ) -> None:
        if key not in deferred_keys:
            return
        state.deferred_match_keys = [
            item for item in state.deferred_match_keys if item != key
        ]
        deferred_keys.discard(key)

    def _is_low_confidence_full_recording_boundary(
        self,
        boundary: MatchBoundary,
        recording_asset: RecordingAsset | None,
    ) -> bool:
        if boundary.confidence > 0.5:
            return False
        if boundary.match_index != 1:
            return False
        if abs(boundary.started_at_seconds) > 0.001:
            return False
        if recording_asset is None:
            return True
        duration = recording_duration_seconds(recording_asset)
        tolerance_seconds = 2.0
        return abs(boundary.ended_at_seconds - duration) <= tolerance_seconds

    @staticmethod
    def _is_incomplete_boundary(boundary: MatchBoundary) -> bool:
        return (not boundary.is_complete) or boundary.confidence < 0.8

    def _write_export(
        self,
        boundary: MatchBoundary,
        subtitle: SubtitleAsset,
        recording_asset: RecordingAsset | None,
        platform: str,
        highlight_plan: HighlightPlanAsset | None = None,
    ) -> tuple[Path | None, bool]:
        ffmpeg_path = shutil.which("ffmpeg")
        if (
            self.settings.export.enable_ffmpeg
            and recording_asset is not None
            and self._looks_like_video(recording_asset.path)
            and Path(recording_asset.path).exists()
            and ffmpeg_path is not None
        ):
            return self._write_export_with_ffmpeg(
                boundary,
                subtitle,
                recording_asset,
                platform,
                highlight_plan,
            )

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

        return None, False

    def _write_export_with_ffmpeg(
        self,
        boundary: MatchBoundary,
        subtitle: SubtitleAsset,
        recording_asset: RecordingAsset,
        platform: str,
        highlight_plan: HighlightPlanAsset | None = None,
    ) -> tuple[Path | None, bool]:
        output_dir = self._platform_export_dir(platform)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{boundary.session_id}_match{boundary.match_index:02d}.mp4"
        subtitle_path = Path(subtitle.path).resolve()
        subtitle_is_placeholder = self._subtitle_is_placeholder(subtitle_path)
        burn_subtitles = self._should_burn_subtitles(subtitle_is_placeholder)
        if highlight_plan is not None:
            command = self._planned_ffmpeg_command(
                boundary=boundary,
                subtitle_path=subtitle_path,
                burn_subtitles=burn_subtitles,
                recording_path=recording_asset.path,
                output_path=output_path,
                highlight_plan=highlight_plan,
            )
        elif not burn_subtitles and self._should_stream_copy_export():
            log(
                "exporter",
                "subtitle burn disabled; using quality-preserving stream copy "
                f"session_id={boundary.session_id} match_index={boundary.match_index}",
            )
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
            ]
            if subtitle_is_placeholder:
                command.extend(
                    [
                        "-map",
                        "0",
                        "-c",
                        "copy",
                    ]
                )
            else:
                command.extend(
                    [
                        "-i",
                        str(subtitle_path),
                        "-map",
                        "0:v?",
                        "-map",
                        "0:a?",
                        "-map",
                        "1:0",
                        "-c:v",
                        "copy",
                        "-c:a",
                        "copy",
                        "-c:s",
                        "mov_text",
                        "-metadata:s:s:0",
                        "language=chi",
                    ]
                )
            command.extend(["-movflags", "+faststart", str(output_path)])
        else:
            if subtitle_is_placeholder:
                log(
                    "exporter",
                    "placeholder subtitle detected; transcoding without subtitle burn "
                    f"session_id={boundary.session_id} match_index={boundary.match_index}",
                )
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
            ]
            if burn_subtitles:
                command.extend(["-vf", self._subtitle_filter_arg(subtitle_path)])
            command.extend(self._video_encode_args())
            command.extend(self._video_quality_args())
            command.extend(
                [
                    "-c:a",
                    "copy",
                    str(output_path),
                ]
            )
        attempts = self.settings.export.ffmpeg_max_retries + 1
        basename = f"{boundary.session_id}_match{boundary.match_index:02d}"
        last_failure_classification: FailureDecision | None = None
        last_failure_reason: str | None = None
        for attempt in range(1, attempts + 1):
            outcome = run_ffmpeg_attempt(
                command,
                timeout=self.settings.export.ffmpeg_timeout_seconds,
                stderr_log_dir=self.stderr_dir,
                stderr_log_basename=basename,
                attempt=attempt,
            )
            if outcome.success:
                invalid_reason = self._validate_export_output(output_path)
                if invalid_reason is not None:
                    self._remove_file_if_exists(output_path)
                    fd = classify_failure_reason(invalid_reason)
                    last_failure_classification = fd
                    last_failure_reason = invalid_reason
                    self._last_failure_classification = fd
                    self._last_failure_reason = invalid_reason
                    log(
                        "exporter",
                        "ffmpeg export invalid output "
                        f"session_id={boundary.session_id} match_index={boundary.match_index} "
                        f"reason={invalid_reason}",
                    )
                    self._append_audit(
                        "ffmpeg_export_failed",
                        session_id=boundary.session_id,
                        match_index=boundary.match_index,
                        reason=invalid_reason,
                        decision="attempt_failed",
                        failure_category=fd.failure_category,
                        is_retryable=fd.is_retryable,
                        reason_code=fd.reason_code,
                        reason_detail=invalid_reason,
                        attempt=attempt,
                        max_attempts=attempts,
                    )
                    break
                self._append_audit(
                    "ffmpeg_export_succeeded",
                    session_id=boundary.session_id,
                    match_index=boundary.match_index,
                    attempt=attempt,
                    max_attempts=attempts,
                )
                return output_path, False
            fd = outcome.classification
            if fd is None:
                fd = classify_failure_reason(outcome.reason)
            last_failure_classification = fd
            last_failure_reason = outcome.reason
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

        fd = last_failure_classification or classify_failure_reason(last_failure_reason)
        failure_reason = last_failure_reason or "ffmpeg_export_failed"
        log(
            "exporter",
            f"ffmpeg fallback placeholder session_id={boundary.session_id} match_index={boundary.match_index}",
        )
        self._append_audit(
            "ffmpeg_export_fallback_placeholder",
            session_id=boundary.session_id,
            match_index=boundary.match_index,
            reason=failure_reason,
            decision="fallback_placeholder",
            failure_category=fd.failure_category,
            is_retryable=fd.is_retryable,
            reason_code=fd.reason_code,
            reason_detail=failure_reason,
            attempt=attempts,
            max_attempts=attempts,
        )
        self._remove_file_if_exists(output_path)
        return None, True

    def _valid_highlight_plan(
        self,
        plan: HighlightPlanAsset | None,
        boundary: MatchBoundary,
    ) -> HighlightPlanAsset | None:
        if plan is None:
            return None
        tolerance_seconds = 1.0
        if (
            abs(plan.source_boundary_start_seconds - boundary.started_at_seconds)
            > tolerance_seconds
            or abs(plan.source_boundary_end_seconds - boundary.ended_at_seconds)
            > tolerance_seconds
        ):
            log(
                "exporter",
                "ignored stale highlight plan "
                f"session_id={boundary.session_id} match_index={boundary.match_index}",
            )
            return None
        duration = boundary.ended_at_seconds - boundary.started_at_seconds
        if duration <= 0.0 or not plan.windows:
            return None
        if all(
            window.reason == "condensed_visual_activity" for window in plan.windows
        ):
            log(
                "exporter",
                "ignored visual-only highlight plan "
                f"session_id={boundary.session_id} match_index={boundary.match_index}",
            )
            return None
        for window in plan.windows:
            if window.started_at_seconds < 0.0:
                return None
            if window.ended_at_seconds <= window.started_at_seconds:
                return None
            if window.ended_at_seconds > duration + tolerance_seconds:
                return None
        if self._plan_nearly_covers_duration(
            plan,
            duration,
            tolerance_seconds=tolerance_seconds,
        ):
            log(
                "exporter",
                "ignored full-span highlight plan "
                f"session_id={boundary.session_id} match_index={boundary.match_index}",
            )
            return None
        if self._is_condensed_plan(plan) and not self._condensed_plan_covers_edges(
            plan,
            duration,
            tolerance_seconds=tolerance_seconds,
        ):
            log(
                "exporter",
                "ignored incomplete condensed highlight plan "
                f"session_id={boundary.session_id} match_index={boundary.match_index}",
            )
            return None
        return plan

    @staticmethod
    def _is_condensed_plan(plan: HighlightPlanAsset) -> bool:
        return any(window.reason.startswith("condensed_") for window in plan.windows)

    @staticmethod
    def _plan_nearly_covers_duration(
        plan: HighlightPlanAsset,
        duration: float,
        *,
        tolerance_seconds: float,
    ) -> bool:
        if len(plan.windows) != 1:
            return False
        window = plan.windows[0]
        return (
            window.started_at_seconds <= tolerance_seconds
            and window.ended_at_seconds >= duration - tolerance_seconds
        )

    @staticmethod
    def _condensed_plan_covers_edges(
        plan: HighlightPlanAsset,
        duration: float,
        *,
        tolerance_seconds: float,
    ) -> bool:
        starts_at_beginning = any(
            window.started_at_seconds <= tolerance_seconds for window in plan.windows
        )
        ends_at_boundary = any(
            window.ended_at_seconds >= duration - tolerance_seconds
            for window in plan.windows
        )
        return starts_at_beginning and ends_at_boundary

    def _planned_ffmpeg_command(
        self,
        *,
        boundary: MatchBoundary,
        subtitle_path: Path,
        burn_subtitles: bool,
        recording_path: str,
        output_path: Path,
        highlight_plan: HighlightPlanAsset,
    ) -> list[str]:
        duration = boundary.ended_at_seconds - boundary.started_at_seconds
        video_filter_parts: list[str] = []
        if burn_subtitles:
            video_filter_parts.append(self._subtitle_filter_arg(subtitle_path))
        select_expr = self._highlight_select_expr(highlight_plan)
        video_filter_parts.extend(
            [
                f"select='{select_expr}'",
                "setpts=N/FRAME_RATE/TB",
            ]
        )
        audio_filter = f"aselect='{select_expr}',asetpts=N/SR/TB"
        log(
            "exporter",
            "highlight plan export "
            f"session_id={boundary.session_id} match_index={boundary.match_index} "
            f"windows={len(highlight_plan.windows)}",
        )
        command = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(boundary.started_at_seconds),
            "-t",
            str(duration),
            "-i",
            recording_path,
            "-vf",
            ",".join(video_filter_parts),
            "-af",
            audio_filter,
        ]
        command.extend(self._video_encode_args())
        command.extend(self._video_quality_args())
        command.extend([str(output_path)])
        return command

    def _highlight_select_expr(self, plan: HighlightPlanAsset) -> str:
        return "+".join(
            (
                "between("
                f"t,{window.started_at_seconds:.3f},{window.ended_at_seconds:.3f}"
                ")"
            )
            for window in plan.windows
        )

    def _platform_by_session(self) -> dict[str, str]:
        platforms: dict[str, str] = {}
        for state_path in self._platform_state_paths():
            try:
                state = load_orchestrator_state(state_path)
            except Exception as exc:
                log(
                    "exporter",
                    f"platform map state unreadable path={state_path} reason={exc}",
                )
                continue

            for session in state.sessions:
                platforms[session.session_id] = session.platform
            for job in state.recording_jobs:
                platforms.setdefault(job.session_id, job.platform)
        return platforms

    def _platform_state_paths(self) -> list[Path]:
        paths = [self.settings.orchestrator.state_file]
        selected_root = self.settings.storage.temp_dir / "selected-recordings"
        if selected_root.exists():
            try:
                paths.extend(sorted(selected_root.glob("*/orchestrator-state.json")))
            except OSError:
                return paths
        return paths

    def _platform_export_dir(self, platform: str) -> Path:
        return self.settings.storage.export_dir / self._safe_platform_dir(platform)

    @staticmethod
    def _safe_platform_dir(platform: str) -> str:
        safe = "".join(
            char.lower() if char.isalnum() or char in {"-", "_"} else "_"
            for char in platform.strip()
        ).strip("._-")
        return safe or "unknown"

    def _validate_export_output(self, output_path: Path) -> str | None:
        if not output_path.exists():
            return "ffmpeg_output_missing"
        if output_path.stat().st_size <= 0:
            return "ffmpeg_output_empty"

        ffprobe_path = shutil.which("ffprobe")
        if ffprobe_path is None:
            log("exporter", "ffmpeg export validation skipped reason=missing_ffprobe")
            return None

        command = [
            ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type,width,height:format=duration,size",
            "-of",
            "json",
            str(output_path),
        ]
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=max(5, min(self.settings.export.ffmpeg_timeout_seconds, 30)),
            )
        except (subprocess.SubprocessError, OSError) as error:
            return f"ffmpeg_output_probe_failed:{error.__class__.__name__}"

        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return "ffmpeg_output_probe_invalid_json"

        streams = payload.get("streams")
        if not isinstance(streams, list) or not streams:
            return "ffmpeg_output_missing_video_stream"
        stream = streams[0]
        if not isinstance(stream, dict):
            return "ffmpeg_output_missing_video_stream"
        if stream.get("codec_type") not in {None, "video"}:
            return "ffmpeg_output_missing_video_stream"
        width = self._optional_int(stream.get("width"))
        height = self._optional_int(stream.get("height"))
        if width is not None and width <= 0:
            return "ffmpeg_output_invalid_video_dimensions"
        if height is not None and height <= 0:
            return "ffmpeg_output_invalid_video_dimensions"

        duration = None
        if isinstance(payload.get("format"), dict):
            duration = self._optional_float(payload["format"].get("duration"))
        if duration is not None and duration <= 0:
            return "ffmpeg_output_zero_duration"
        return None

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

    @staticmethod
    def _optional_int(value: object) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(value: object) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _remove_file_if_exists(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            return

    def _backoff_seconds(self, attempt: int) -> float:
        initial = self.settings.export.backoff_initial_seconds
        maximum = self.settings.export.backoff_max_seconds
        return min(initial * (2 ** (attempt - 1)), maximum)

    def _should_stream_copy_export(self) -> bool:
        return self.settings.export.ffmpeg_video_codec in {"auto", "copy"}

    def _should_burn_subtitles(self, subtitle_is_placeholder: bool) -> bool:
        return self.settings.export.burn_subtitles and not subtitle_is_placeholder

    def _video_codec_args(self) -> list[str]:
        codec = self.settings.export.ffmpeg_video_codec
        if codec in {"auto", "copy"}:
            return []

        # Hardware encoding support (NVENC/QSV/AMF)
        if self.settings.export.use_hardware_encoding:
            if codec == "h264":
                # Try NVENC first, fallback to QSV, then AMF
                return ["-c:v", "h264_nvenc"]
            if codec == "h265":
                return ["-c:v", "hevc_nvenc", "-tag:v", "hvc1"]

        # CPU software encoding (fallback or default)
        if codec == "h264":
            return ["-c:v", "libx264"]
        if codec == "h265":
            return ["-c:v", "libx265", "-tag:v", "hvc1"]
        raise ValueError(f"unsupported export video codec: {codec}")

    def _video_quality_args(self) -> list[str]:
        """Generate quality control arguments: bitrate or CRF mode."""
        args = ["-preset", self.settings.export.ffmpeg_preset]

        # Prefer fixed bitrate if configured (better quality preservation)
        if self.settings.export.ffmpeg_bitrate:
            args.extend(["-b:v", self.settings.export.ffmpeg_bitrate])
            if self.settings.export.ffmpeg_max_bitrate:
                args.extend(["-maxrate", self.settings.export.ffmpeg_max_bitrate])
                # Add bufsize (typically 2x maxrate for smooth encoding)
                args.extend(["-bufsize", "8M"])
        else:
            # Fallback to CRF mode
            args.extend(["-crf", str(self.settings.export.ffmpeg_crf)])

        return args

    def _video_encode_args(self) -> list[str]:
        codec = self.settings.export.ffmpeg_video_codec
        if codec in {"auto", "copy"}:
            return ["-c:v", "libx264"]
        return self._video_codec_args()

    def _subtitle_filter_arg(self, subtitle_path: Path) -> str:
        escaped = subtitle_path.as_posix().replace(":", "\\:")
        return f"subtitles='{escaped}'"

    def _subtitle_is_placeholder(self, subtitle_path: Path) -> bool:
        try:
            text = subtitle_path.read_text(encoding="utf-8")
        except OSError:
            return False
        return "Placeholder subtitle generated by local pipeline." in text

    def _load_state(self) -> ExporterStateFile:
        if not self.state_path.exists():
            return ExporterStateFile()
        return ExporterStateFile.model_validate_json(self.state_path.read_text(encoding="utf-8"))

    def _save_state(self, state: ExporterStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _key(self, session_id: str, match_index: int) -> str:
        return f"{session_id}:{match_index}"
