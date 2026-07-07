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
from arl.media.recording_resolver import (
    recording_duration_seconds,
    resolve_recording_window,
)
from arl.orchestrator.state_store import load_orchestrator_state
from arl.shared.contracts import (
    AudioBed,
    EditPlanAsset,
    ExportAsset,
    HighlightPlanAsset,
    MatchBoundary,
    MediaSpan,
    RecordingAsset,
    SoundEffectHit,
    SubtitleAsset,
    TimelineSegment,
    TimelineVideoTransform,
)
from arl.shared.failure_contracts import FailureDecision, classify_failure_reason
from arl.shared.ffmpeg_runner import rotate_stderr_logs, run_ffmpeg_attempt
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log
from arl.subtitles.ass import AssSubtitleStyle, SrtCue, parse_srt_cues, write_ass_from_srt
from arl.subtitles.retime import (
    retime_srt_cues_for_edit_plan,
    retime_srt_cues_for_highlight_plan,
)


_BGM_GAIN_RANGE_DB = (-60.0, 0.0)
_SFX_GAIN_RANGE_DB = (-60.0, 6.0)
_BGM_FADE_SECONDS = 2.0
_BGM_DUCK_THRESHOLD = 0.03
_BGM_DUCK_RATIO = 6.0
_BGM_DUCK_ATTACK_MS = 20
_BGM_DUCK_RELEASE_MS = 350


class ExporterService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.boundaries_path = settings.storage.temp_dir / "match-boundaries.jsonl"
        self.subtitles_path = settings.storage.temp_dir / "subtitle-assets.jsonl"
        self.highlight_plans_path = settings.storage.temp_dir / "highlight-plans.jsonl"
        self.edit_plans_path = settings.storage.temp_dir / "edit-plans.jsonl"
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
        log("exporter", f"use_ass_subtitles={int(self.settings.export.use_ass_subtitles)}")
        log(
            "exporter",
            f"use_edit_plans={int(self.settings.export.use_edit_plans)}",
        )
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
        edit_plans = (
            load_models(self.edit_plans_path, EditPlanAsset)
            if self.settings.export.use_edit_plans
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
        edit_plan_map = {(item.session_id, item.match_index): item for item in edit_plans}
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
            edit_plan = (
                self._valid_edit_plan(
                    edit_plan_map.get((boundary.session_id, boundary.match_index)),
                    boundary,
                )
                if self.settings.export.use_edit_plans
                else None
            )
            highlight_plan = (
                self._valid_highlight_plan(
                    highlight_plan_map.get((boundary.session_id, boundary.match_index)),
                    boundary,
                )
                if edit_plan is None and self.settings.export.use_highlight_plans
                else None
            )
            output_path, was_ffmpeg_fallback = self._write_export(
                boundary,
                subtitle,
                recording_asset,
                platform,
                highlight_plan,
                edit_plan,
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
        edit_plan: EditPlanAsset | None = None,
    ) -> tuple[Path | None, bool]:
        ffmpeg_path = shutil.which("ffmpeg")
        if (
            self.settings.export.enable_ffmpeg
            and recording_asset is not None
            and self._recording_asset_is_exportable(recording_asset, boundary)
            and ffmpeg_path is not None
        ):
            return self._write_export_with_ffmpeg(
                boundary,
                subtitle,
                recording_asset,
                platform,
                highlight_plan,
                edit_plan,
            )

        if self.settings.export.enable_ffmpeg:
            if recording_asset is None:
                reason = "missing_recording_asset"
            elif not Path(recording_asset.path).exists():
                reason = "recording_asset_not_found"
            elif not self._recording_asset_is_exportable(recording_asset, boundary):
                reason = "recording_asset_not_exportable"
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
        edit_plan: EditPlanAsset | None = None,
    ) -> tuple[Path | None, bool]:
        output_dir = self._platform_export_dir(platform)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{boundary.session_id}_match{boundary.match_index:02d}.mp4"
        boundary_spans = self._resolve_export_spans(
            recording_asset,
            start_seconds=boundary.started_at_seconds,
            end_seconds=boundary.ended_at_seconds,
        )
        use_span_commands = self._requires_span_command(recording_asset, boundary_spans)
        subtitle_path = Path(subtitle.path).resolve()
        subtitle_is_placeholder = self._subtitle_is_placeholder(subtitle_path)
        burn_subtitles = self._should_burn_subtitles(subtitle_is_placeholder)
        subtitle_filter_path = subtitle_path
        if edit_plan is not None:
            edit_plan_burn_subtitles = burn_subtitles
            if burn_subtitles:
                edit_subtitle_path = self._edit_plan_subtitle_render_path(
                    subtitle_path,
                    boundary=boundary,
                    edit_plan=edit_plan,
                )
                if edit_subtitle_path is None:
                    edit_plan_burn_subtitles = False
                else:
                    subtitle_filter_path = edit_subtitle_path
            if use_span_commands:
                command = self._edit_plan_span_ffmpeg_command(
                    boundary=boundary,
                    subtitle_path=subtitle_filter_path,
                    burn_subtitles=edit_plan_burn_subtitles,
                    recording_asset=recording_asset,
                    output_path=output_path,
                    edit_plan=edit_plan,
                )
            else:
                command = self._edit_plan_ffmpeg_command(
                    boundary=boundary,
                    subtitle_path=subtitle_filter_path,
                    burn_subtitles=edit_plan_burn_subtitles,
                    recording_path=recording_asset.path,
                    output_path=output_path,
                    edit_plan=edit_plan,
                )
        else:
            if burn_subtitles and not (highlight_plan is not None and use_span_commands):
                subtitle_filter_path = self._subtitle_render_path(
                    subtitle_path,
                    boundary=boundary,
                )
                if subtitle_filter_path is None:
                    return None, False
            if highlight_plan is not None:
                highlight_burn_subtitles = burn_subtitles
                if use_span_commands:
                    if burn_subtitles:
                        highlight_subtitle_path = self._highlight_plan_subtitle_render_path(
                            subtitle_path,
                            boundary=boundary,
                            highlight_plan=highlight_plan,
                        )
                        if highlight_subtitle_path is None:
                            highlight_burn_subtitles = False
                        else:
                            subtitle_filter_path = highlight_subtitle_path
                    command = self._planned_span_ffmpeg_command(
                        boundary=boundary,
                        subtitle_path=subtitle_filter_path,
                        burn_subtitles=highlight_burn_subtitles,
                        recording_asset=recording_asset,
                        output_path=output_path,
                        highlight_plan=highlight_plan,
                    )
                else:
                    command = self._planned_ffmpeg_command(
                        boundary=boundary,
                        subtitle_path=subtitle_filter_path,
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
                if use_span_commands and len(boundary_spans) > 1:
                    command = self._span_concat_ffmpeg_command(
                        spans=boundary_spans,
                        subtitle_path=subtitle_filter_path,
                        burn_subtitles=False,
                        soft_subtitles=not subtitle_is_placeholder,
                        output_path=output_path,
                    )
                else:
                    span = boundary_spans[0]
                    command = [
                        "ffmpeg",
                        "-y",
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-ss",
                        str(span.local_start_seconds),
                        "-to",
                        str(span.local_end_seconds),
                        "-i",
                        span.path,
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
            elif use_span_commands:
                if subtitle_is_placeholder:
                    log(
                        "exporter",
                        "placeholder subtitle detected; transcoding without subtitle burn "
                        f"session_id={boundary.session_id} match_index={boundary.match_index}",
                    )
                command = self._span_concat_ffmpeg_command(
                    spans=boundary_spans,
                    subtitle_path=subtitle_filter_path,
                    burn_subtitles=burn_subtitles,
                    soft_subtitles=False,
                    output_path=output_path,
                )
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
                    command.extend(
                        ["-vf", self._subtitle_filter_arg(subtitle_filter_path)]
                    )
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

    def _valid_edit_plan(
        self,
        plan: EditPlanAsset | None,
        boundary: MatchBoundary,
    ) -> EditPlanAsset | None:
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
                "ignored stale edit plan "
                f"session_id={boundary.session_id} match_index={boundary.match_index}",
            )
            return None
        duration = boundary.ended_at_seconds - boundary.started_at_seconds
        if duration <= 0.0 or not plan.timeline:
            return None

        main_indices = [
            index for index, segment in enumerate(plan.timeline) if segment.role == "main"
        ]
        if not main_indices:
            return None
        first_main_index = main_indices[0]
        segment_tolerance_seconds = 0.001
        transition_count = 0

        for index, segment in enumerate(plan.timeline):
            if segment.role == "transition":
                transition_count += 1
                if transition_count > 1:
                    return None
                if not self._valid_transition_segment(segment):
                    return None
                if index >= first_main_index:
                    return None
                if index == 0 or not any(
                    earlier.role == "teaser" for earlier in plan.timeline[:index]
                ):
                    return None
                continue
            if segment.role not in {"teaser", "main"}:
                return None
            if segment.source_path is not None:
                return None
            if not self._valid_timeline_transform(segment.transform):
                return None
            if segment.source_start_seconds < 0.0:
                return None
            if segment.source_end_seconds <= segment.source_start_seconds:
                return None
            if segment.source_end_seconds > duration + tolerance_seconds:
                return None
            if index < first_main_index and segment.role != "teaser":
                return None
            if index >= first_main_index and segment.role != "main":
                return None
        main_segments = [plan.timeline[index] for index in main_indices]
        if any(segment.reason == "full_validated_match" for segment in main_segments):
            return None
        if len(main_segments) == 1:
            only_main = main_segments[0]
            if (
                abs(only_main.source_start_seconds) <= segment_tolerance_seconds
                and abs(only_main.source_end_seconds - duration)
                <= segment_tolerance_seconds
            ):
                return None
        if not any(
            abs(segment.source_start_seconds) <= segment_tolerance_seconds
            for segment in main_segments
        ):
            return None
        if not any(
            abs(segment.source_end_seconds - duration) <= segment_tolerance_seconds
            for segment in main_segments
        ):
            return None
        previous_main_end = -segment_tolerance_seconds
        for segment in main_segments:
            if segment.source_start_seconds < previous_main_end - segment_tolerance_seconds:
                return None
            previous_main_end = segment.source_end_seconds
        rendered_duration = self._edit_plan_output_duration(plan)
        if not self._valid_edit_plan_audio(plan, boundary, rendered_duration):
            return None
        return plan

    @staticmethod
    def _valid_transition_segment(segment: TimelineSegment) -> bool:
        if segment.reason != "transition_black_card":
            return False
        if segment.source_path is not None or segment.transform is not None:
            return False
        if abs(segment.source_start_seconds) > 0.001:
            return False
        if abs(segment.source_end_seconds) > 0.001:
            return False
        duration = segment.duration_seconds or 0.0
        return 0.0 < duration <= 10.0

    def _valid_edit_plan_audio(
        self,
        plan: EditPlanAsset,
        boundary: MatchBoundary,
        rendered_duration: float,
    ) -> bool:
        for index, bed in enumerate(plan.audio_beds):
            if not self._audio_source_is_valid(bed.source_path):
                log(
                    "exporter",
                    "ignored edit plan audio bed "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"index={index} reason=missing_source",
                )
                return False
            if bed.timeline_start_seconds < 0.0:
                return False
            if bed.timeline_start_seconds >= rendered_duration:
                return False
            if bed.timeline_end_seconds is not None:
                if bed.timeline_end_seconds <= bed.timeline_start_seconds:
                    return False
                if bed.timeline_end_seconds > rendered_duration + 0.001:
                    return False
            if not self._gain_in_range(bed.gain_db, _BGM_GAIN_RANGE_DB):
                return False

        for index, hit in enumerate(plan.sound_effects):
            if not self._audio_source_is_valid(hit.source_path):
                log(
                    "exporter",
                    "ignored edit plan sound effect "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"index={index} reason=missing_source",
                )
                return False
            if hit.at_seconds < 0.0 or hit.at_seconds >= rendered_duration:
                return False
            if not self._gain_in_range(hit.gain_db, _SFX_GAIN_RANGE_DB):
                return False
        return True

    @staticmethod
    def _audio_source_is_valid(source_path: str) -> bool:
        path = Path(source_path)
        return path.exists() and path.is_file()

    @staticmethod
    def _gain_in_range(value: float, valid_range: tuple[float, float]) -> bool:
        minimum, maximum = valid_range
        return minimum <= value <= maximum

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
        audio_filter = self._audio_filter_chain(
            f"aselect='{select_expr}',asetpts=N/SR/TB"
        )
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

    def _planned_span_ffmpeg_command(
        self,
        *,
        boundary: MatchBoundary,
        subtitle_path: Path,
        burn_subtitles: bool,
        recording_asset: RecordingAsset,
        output_path: Path,
        highlight_plan: HighlightPlanAsset,
    ) -> list[str]:
        spans: list[MediaSpan] = []
        for window in highlight_plan.windows:
            spans.extend(
                self._resolve_export_spans(
                    recording_asset,
                    start_seconds=boundary.started_at_seconds
                    + window.started_at_seconds,
                    end_seconds=boundary.started_at_seconds + window.ended_at_seconds,
                )
            )
        log(
            "exporter",
            "highlight plan chunk export "
            f"session_id={boundary.session_id} match_index={boundary.match_index} "
            f"windows={len(highlight_plan.windows)} spans={len(spans)}",
        )
        return self._span_concat_ffmpeg_command(
            spans=spans,
            subtitle_path=subtitle_path,
            burn_subtitles=burn_subtitles,
            soft_subtitles=False,
            output_path=output_path,
        )

    def _edit_plan_ffmpeg_command(
        self,
        *,
        boundary: MatchBoundary,
        subtitle_path: Path,
        burn_subtitles: bool,
        recording_path: str,
        output_path: Path,
        edit_plan: EditPlanAsset,
    ) -> list[str]:
        duration = boundary.ended_at_seconds - boundary.started_at_seconds
        has_audio_mix = bool(edit_plan.audio_beds or edit_plan.sound_effects)
        filter_parts: list[str] = []
        concat_inputs: list[str] = []
        for index, segment in enumerate(edit_plan.timeline):
            video_label = f"v{index}"
            audio_label = f"a{index}"
            if segment.role == "transition":
                filter_parts.extend(
                    self._transition_filter_parts(
                        segment,
                        video_label=video_label,
                        audio_label=audio_label,
                    )
                )
            else:
                video_filters = self._timeline_video_filters(segment)
                filter_parts.append(f"[0:v]{','.join(video_filters)}[{video_label}]")
                filter_parts.append(
                    "[0:a]"
                    f"atrim=start={segment.source_start_seconds:.3f}:"
                    f"end={segment.source_end_seconds:.3f},"
                    f"asetpts=PTS-STARTPTS[{audio_label}]"
                )
            concat_inputs.extend([f"[{video_label}]", f"[{audio_label}]"])
        concat_audio_label = "basea" if has_audio_mix else "a"
        filter_parts.append(
            ""
            f"{''.join(concat_inputs)}"
            f"concat=n={len(edit_plan.timeline)}:v=1:a=1[v][{concat_audio_label}]"
        )
        video_output_label = "v"
        if burn_subtitles:
            video_output_label = "vsub"
            filter_parts.append(
                f"[v]{self._subtitle_filter_arg(subtitle_path)}[{video_output_label}]"
            )
        if has_audio_mix:
            filter_parts.extend(self._edit_plan_audio_filters(edit_plan))
        audio_output_label = self._append_audio_loudnorm_filter(filter_parts)
        log(
            "exporter",
            "edit plan export "
            f"session_id={boundary.session_id} match_index={boundary.match_index} "
            f"segments={len(edit_plan.timeline)}",
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
        ]
        command.extend(self._edit_plan_audio_inputs(edit_plan))
        command.extend(
            [
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                f"[{video_output_label}]",
                "-map",
                f"[{audio_output_label}]",
            ]
        )
        command.extend(self._video_encode_args())
        command.extend(self._video_quality_args())
        command.extend([str(output_path)])
        return command

    def _edit_plan_span_ffmpeg_command(
        self,
        *,
        boundary: MatchBoundary,
        subtitle_path: Path,
        burn_subtitles: bool,
        recording_asset: RecordingAsset,
        output_path: Path,
        edit_plan: EditPlanAsset,
    ) -> list[str]:
        expanded: list[tuple[MediaSpan | TimelineSegment, TimelineVideoTransform | None]] = []
        for segment in edit_plan.timeline:
            if segment.role == "transition":
                expanded.append((segment, None))
                continue
            segment_spans = self._resolve_export_spans(
                recording_asset,
                start_seconds=boundary.started_at_seconds
                + segment.source_start_seconds,
                end_seconds=boundary.started_at_seconds + segment.source_end_seconds,
            )
            expanded.extend((span, segment.transform) for span in segment_spans)

        has_audio_mix = bool(edit_plan.audio_beds or edit_plan.sound_effects)
        filter_parts: list[str] = []
        concat_inputs: list[str] = []
        media_input_count = sum(
            1 for span, _transform in expanded if not isinstance(span, TimelineSegment)
        )
        next_media_input_index = 0
        for index, (span, transform) in enumerate(expanded):
            video_label = f"v{index}"
            audio_label = f"a{index}"
            if isinstance(span, TimelineSegment):
                filter_parts.extend(
                    self._transition_filter_parts(
                        span,
                        video_label=video_label,
                        audio_label=audio_label,
                    )
                )
            else:
                video_filters = self._media_span_video_filters(span, transform)
                filter_parts.append(
                    f"[{next_media_input_index}:v]{','.join(video_filters)}[{video_label}]"
                )
                filter_parts.append(
                    f"[{next_media_input_index}:a]"
                    f"atrim=start={span.local_start_seconds:.3f}:"
                    f"end={span.local_end_seconds:.3f},"
                    f"asetpts=PTS-STARTPTS[{audio_label}]"
                )
                next_media_input_index += 1
            concat_inputs.extend([f"[{video_label}]", f"[{audio_label}]"])
        concat_audio_label = "basea" if has_audio_mix else "a"
        filter_parts.append(
            ""
            f"{''.join(concat_inputs)}"
            f"concat=n={len(expanded)}:v=1:a=1[v][{concat_audio_label}]"
        )
        video_output_label = "v"
        if burn_subtitles:
            video_output_label = "vsub"
            filter_parts.append(
                f"[v]{self._subtitle_filter_arg(subtitle_path)}[{video_output_label}]"
            )
        if has_audio_mix:
            filter_parts.extend(
                self._edit_plan_audio_filters(
                    edit_plan,
                    first_audio_input_index=media_input_count,
                )
            )
        audio_output_label = self._append_audio_loudnorm_filter(filter_parts)
        log(
            "exporter",
            "edit plan chunk export "
            f"session_id={boundary.session_id} match_index={boundary.match_index} "
            f"segments={len(edit_plan.timeline)} spans={len(expanded)}",
        )
        command = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        for span, _transform in expanded:
            if isinstance(span, TimelineSegment):
                continue
            command.extend(["-i", span.path])
        command.extend(self._edit_plan_audio_inputs(edit_plan))
        command.extend(
            [
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                f"[{video_output_label}]",
                "-map",
                f"[{audio_output_label}]",
            ]
        )
        command.extend(self._video_encode_args())
        command.extend(self._video_quality_args())
        command.extend([str(output_path)])
        return command

    def _edit_plan_audio_inputs(self, edit_plan: EditPlanAsset) -> list[str]:
        args: list[str] = []
        for bed in edit_plan.audio_beds:
            if bed.loop:
                args.extend(["-stream_loop", "-1"])
            args.extend(["-i", bed.source_path])
        for hit in edit_plan.sound_effects:
            args.extend(["-i", hit.source_path])
        return args

    def _edit_plan_audio_filters(
        self,
        edit_plan: EditPlanAsset,
        *,
        first_audio_input_index: int = 1,
    ) -> list[str]:
        filter_parts: list[str] = []
        audio_bed_count = len(edit_plan.audio_beds)
        base_mix_label = "basea"
        if audio_bed_count:
            base_mix_label = "basemix"
            split_labels = [base_mix_label] + [
                f"basechain{index}" for index in range(audio_bed_count)
            ]
            filter_parts.append(
                f"[basea]asplit={len(split_labels)}"
                f"{''.join(f'[{label}]' for label in split_labels)}"
            )
        mix_inputs = [f"[{base_mix_label}]"]
        rendered_duration = self._edit_plan_output_duration(edit_plan)
        next_input_index = first_audio_input_index
        for index, bed in enumerate(edit_plan.audio_beds):
            label = f"bgm{index}"
            raw_label = f"bgmraw{index}"
            filter_parts.append(
                self._audio_bed_filter(
                    bed,
                    input_index=next_input_index,
                    label=raw_label,
                    rendered_duration=rendered_duration,
                )
            )
            filter_parts.append(
                self._audio_bed_duck_filter(
                    raw_label=raw_label,
                    sidechain_label=f"basechain{index}",
                    output_label=label,
                )
            )
            mix_inputs.append(f"[{label}]")
            next_input_index += 1
        for index, hit in enumerate(edit_plan.sound_effects):
            label = f"sfx{index}"
            filter_parts.append(
                self._sound_effect_filter(
                    hit,
                    input_index=next_input_index,
                    label=label,
                )
            )
            mix_inputs.append(f"[{label}]")
            next_input_index += 1
        filter_parts.append(
            ""
            f"{''.join(mix_inputs)}"
            f"amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=0[a]"
        )
        return filter_parts

    @staticmethod
    def _audio_bed_duck_filter(
        *,
        raw_label: str,
        sidechain_label: str,
        output_label: str,
    ) -> str:
        return (
            f"[{raw_label}][{sidechain_label}]"
            "sidechaincompress="
            f"threshold={_BGM_DUCK_THRESHOLD:.3f}:"
            f"ratio={_BGM_DUCK_RATIO:.1f}:"
            f"attack={_BGM_DUCK_ATTACK_MS}:"
            f"release={_BGM_DUCK_RELEASE_MS}:"
            "makeup=1"
            f"[{output_label}]"
        )

    def _append_audio_loudnorm_filter(self, filter_parts: list[str]) -> str:
        if not self.settings.export.audio_loudnorm_enabled:
            return "a"
        filter_parts.append(
            f"[a]{self.settings.export.audio_loudnorm_filter}[aout]"
        )
        return "aout"

    def _audio_filter_chain(self, base_filter: str) -> str:
        if not self.settings.export.audio_loudnorm_enabled:
            return base_filter
        return f"{base_filter},{self.settings.export.audio_loudnorm_filter}"

    def _audio_bed_filter(
        self,
        bed: AudioBed,
        *,
        input_index: int,
        label: str,
        rendered_duration: float,
    ) -> str:
        start = bed.timeline_start_seconds
        end = bed.timeline_end_seconds if bed.timeline_end_seconds is not None else rendered_duration
        bed_duration = max(0.001, end - start)
        filters = [
            f"atrim=start=0.000:duration={bed_duration:.3f}",
            "asetpts=PTS-STARTPTS",
            f"volume={self._linear_gain(bed.gain_db)}",
        ]
        filters.extend(self._audio_bed_fade_filters(bed_duration))
        delay_ms = self._milliseconds(start)
        if delay_ms > 0:
            filters.append(f"adelay={delay_ms}|{delay_ms}")
        return f"[{input_index}:a]{','.join(filters)}[{label}]"

    @staticmethod
    def _audio_bed_fade_filters(duration_seconds: float) -> list[str]:
        fade_seconds = min(_BGM_FADE_SECONDS, duration_seconds / 3.0)
        if fade_seconds < 0.05:
            return []
        filters = [f"afade=t=in:st=0.000:d={fade_seconds:.3f}"]
        if duration_seconds > fade_seconds * 2.0:
            filters.append(
                f"afade=t=out:st={duration_seconds - fade_seconds:.3f}:"
                f"d={fade_seconds:.3f}"
            )
        return filters

    def _sound_effect_filter(
        self,
        hit: SoundEffectHit,
        *,
        input_index: int,
        label: str,
    ) -> str:
        delay_ms = self._milliseconds(hit.at_seconds)
        return (
            f"[{input_index}:a]"
            "asetpts=PTS-STARTPTS,"
            f"volume={self._linear_gain(hit.gain_db)},"
            f"adelay={delay_ms}|{delay_ms}"
            f"[{label}]"
        )

    @staticmethod
    def _linear_gain(gain_db: float) -> str:
        return f"{10 ** (gain_db / 20.0):.6f}"

    @staticmethod
    def _milliseconds(seconds: float) -> int:
        return max(0, int(round(seconds * 1000)))

    @staticmethod
    def _edit_plan_output_duration(edit_plan: EditPlanAsset) -> float:
        return sum(
            (
                max(0.0, segment.duration_seconds or 0.0)
                if segment.role == "transition"
                else max(0.0, segment.source_end_seconds - segment.source_start_seconds)
            )
            for segment in edit_plan.timeline
        )

    def _span_concat_ffmpeg_command(
        self,
        *,
        spans: list[MediaSpan],
        subtitle_path: Path,
        burn_subtitles: bool,
        soft_subtitles: bool,
        output_path: Path,
    ) -> list[str]:
        filter_parts: list[str] = []
        concat_inputs: list[str] = []
        for index, span in enumerate(spans):
            video_label = f"v{index}"
            audio_label = f"a{index}"
            video_filters = self._media_span_video_filters(span, None)
            filter_parts.append(f"[{index}:v]{','.join(video_filters)}[{video_label}]")
            filter_parts.append(
                f"[{index}:a]"
                f"atrim=start={span.local_start_seconds:.3f}:"
                f"end={span.local_end_seconds:.3f},"
                f"asetpts=PTS-STARTPTS[{audio_label}]"
            )
            concat_inputs.extend([f"[{video_label}]", f"[{audio_label}]"])
        filter_parts.append(
            f"{''.join(concat_inputs)}concat=n={len(spans)}:v=1:a=1[v][a]"
        )
        video_output_label = "v"
        if burn_subtitles:
            video_output_label = "vsub"
            filter_parts.append(
                f"[v]{self._subtitle_filter_arg(subtitle_path)}[{video_output_label}]"
            )
        audio_output_label = self._append_audio_loudnorm_filter(filter_parts)

        command = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        for span in spans:
            command.extend(["-i", span.path])
        subtitle_input_index = len(spans)
        if soft_subtitles:
            command.extend(["-i", str(subtitle_path)])
        command.extend(
            [
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                f"[{video_output_label}]",
                "-map",
                f"[{audio_output_label}]",
            ]
        )
        if soft_subtitles:
            command.extend(
                [
                    "-map",
                    f"{subtitle_input_index}:0",
                ]
            )
        command.extend(self._video_encode_args())
        command.extend(self._video_quality_args())
        if soft_subtitles:
            command.extend(
                [
                    "-c:s",
                    "mov_text",
                    "-metadata:s:s:0",
                    "language=chi",
                ]
            )
        command.extend(["-movflags", "+faststart", str(output_path)])
        return command

    def _media_span_video_filters(
        self,
        span: MediaSpan,
        transform: TimelineVideoTransform | None,
    ) -> list[str]:
        filters: list[str] = [
            (
                f"trim=start={span.local_start_seconds:.3f}:"
                f"end={span.local_end_seconds:.3f}"
            ),
            "setpts=PTS-STARTPTS",
        ]
        filters.extend(self._timeline_transform_filters(transform))
        return filters

    def _timeline_video_filters(
        self,
        segment: TimelineSegment,
    ) -> list[str]:
        filters: list[str] = [
            (
                f"trim=start={segment.source_start_seconds:.3f}:"
                f"end={segment.source_end_seconds:.3f}"
            ),
            "setpts=PTS-STARTPTS",
        ]
        filters.extend(self._timeline_transform_filters(segment.transform))
        return filters

    def _transition_filter_parts(
        self,
        segment: TimelineSegment,
        *,
        video_label: str,
        audio_label: str,
    ) -> list[str]:
        duration = max(0.001, segment.duration_seconds or 0.0)
        video_filters = [
            f"color=c=black:s=1920x1080:r=30:d={duration:.3f}",
            "format=yuv420p",
            "setsar=1",
        ]
        text = (segment.text or "").strip()
        if text:
            video_filters.append(
                "drawtext="
                f"text='{self._drawtext_escape(text)}':"
                "fontcolor=white:"
                "fontsize=54:"
                "x=(w-text_w)/2:"
                "y=(h-text_h)/2"
            )
        return [
            f"{','.join(video_filters)}[{video_label}]",
            "anullsrc=channel_layout=stereo:sample_rate=48000,"
            f"atrim=start=0.000:duration={duration:.3f},"
            f"asetpts=PTS-STARTPTS[{audio_label}]",
        ]

    @staticmethod
    def _drawtext_escape(text: str) -> str:
        return (
            text.replace("\\", "\\\\")
            .replace("'", r"\'")
            .replace(":", r"\:")
            .replace("%", r"\%")
        )

    def _timeline_transform_filters(
        self,
        transform: TimelineVideoTransform | None,
    ) -> list[str]:
        if transform is None or transform.kind == "none":
            return []
        if transform.kind != "punch_in":
            return []
        scale = transform.scale
        return [
            f"scale=iw*{scale:.3f}:ih*{scale:.3f}",
            (
                f"crop=iw/{scale:.3f}:ih/{scale:.3f}:"
                f"x=(iw-iw/{scale:.3f})*{transform.x_anchor:.3f}:"
                f"y=(ih-ih/{scale:.3f})*{transform.y_anchor:.3f}"
            ),
        ]

    @staticmethod
    def _valid_timeline_transform(transform: TimelineVideoTransform | None) -> bool:
        if transform is None:
            return True
        if transform.kind == "none":
            return 0.0 <= transform.x_anchor <= 1.0 and 0.0 <= transform.y_anchor <= 1.0
        if transform.kind != "punch_in":
            return False
        return (
            1.0 < transform.scale <= 1.5
            and 0.0 <= transform.x_anchor <= 1.0
            and 0.0 <= transform.y_anchor <= 1.0
        )

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

    def _recording_asset_is_exportable(
        self,
        recording_asset: RecordingAsset,
        boundary: MatchBoundary,
    ) -> bool:
        if not Path(recording_asset.path).exists():
            return False
        spans = self._resolve_export_spans(
            recording_asset,
            start_seconds=boundary.started_at_seconds,
            end_seconds=boundary.ended_at_seconds,
        )
        if not spans:
            return False
        return all(
            Path(span.path).exists() and self._looks_like_video(span.path)
            for span in spans
        )

    def _resolve_export_spans(
        self,
        recording_asset: RecordingAsset,
        *,
        start_seconds: float,
        end_seconds: float,
    ) -> list[MediaSpan]:
        return resolve_recording_window(
            recording_asset,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )

    def _requires_span_command(
        self,
        recording_asset: RecordingAsset,
        spans: list[MediaSpan],
    ) -> bool:
        if Path(recording_asset.path).suffix.lower() == ".json":
            return True
        if len(spans) != 1:
            return True
        return not self._same_path(spans[0].path, recording_asset.path)

    @staticmethod
    def _same_path(left: str, right: str) -> bool:
        try:
            return Path(left).resolve() == Path(right).resolve()
        except OSError:
            return Path(left) == Path(right)

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

    def _subtitle_render_path(
        self,
        subtitle_path: Path,
        *,
        boundary: MatchBoundary,
    ) -> Path | None:
        if not self.settings.export.use_ass_subtitles:
            return subtitle_path

        ass_path = subtitle_path.with_suffix(".ass")
        try:
            write_ass_from_srt(
                subtitle_path,
                ass_path,
                AssSubtitleStyle(
                    font_name=self.settings.export.ass_font_name,
                    font_size=self.settings.export.ass_font_size,
                    margin_v=self.settings.export.ass_margin_v,
                    outline=self.settings.export.ass_outline,
                    max_chars_per_line=self.settings.export.ass_max_chars_per_line,
                    max_lines=self.settings.export.ass_max_lines,
                ),
            )
        except (OSError, ValueError) as exc:
            log(
                "exporter",
                "ass subtitle sidecar skipped "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"reason={exc}",
            )
            return None

        log(
            "exporter",
            "ass subtitle sidecar written "
            f"session_id={boundary.session_id} match_index={boundary.match_index}",
        )
        return ass_path

    def _edit_plan_subtitle_render_path(
        self,
        subtitle_path: Path,
        *,
        boundary: MatchBoundary,
        edit_plan: EditPlanAsset,
    ) -> Path | None:
        retimed_srt_path = subtitle_path.with_name(
            f"{subtitle_path.stem}-edit-plan.srt"
        )
        try:
            source_cues = parse_srt_cues(subtitle_path.read_text(encoding="utf-8"))
            retimed_cues = self._retime_srt_cues_for_edit_plan(source_cues, edit_plan)
            if not retimed_cues:
                log(
                    "exporter",
                    "edit-plan subtitle burn skipped reason=no_retimed_cues "
                    f"session_id={boundary.session_id} match_index={boundary.match_index}",
                )
                return None
            self._write_srt_cues(retimed_srt_path, retimed_cues)
        except OSError as exc:
            log(
                "exporter",
                "edit-plan subtitle sidecar skipped "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"reason={exc}",
            )
            return None

        if not self.settings.export.use_ass_subtitles:
            return retimed_srt_path

        ass_path = retimed_srt_path.with_suffix(".ass")
        try:
            write_ass_from_srt(
                retimed_srt_path,
                ass_path,
                AssSubtitleStyle(
                    font_name=self.settings.export.ass_font_name,
                    font_size=self.settings.export.ass_font_size,
                    margin_v=self.settings.export.ass_margin_v,
                    outline=self.settings.export.ass_outline,
                    max_chars_per_line=self.settings.export.ass_max_chars_per_line,
                    max_lines=self.settings.export.ass_max_lines,
                ),
            )
        except (OSError, ValueError) as exc:
            log(
                "exporter",
                "edit-plan ass subtitle sidecar skipped "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"reason={exc}",
            )
            return None
        return ass_path

    def _highlight_plan_subtitle_render_path(
        self,
        subtitle_path: Path,
        *,
        boundary: MatchBoundary,
        highlight_plan: HighlightPlanAsset,
    ) -> Path | None:
        retimed_srt_path = subtitle_path.with_name(
            f"{subtitle_path.stem}-highlight-plan.srt"
        )
        try:
            source_cues = parse_srt_cues(subtitle_path.read_text(encoding="utf-8"))
            retimed_cues = self._retime_srt_cues_for_highlight_plan(
                source_cues,
                highlight_plan,
            )
            if not retimed_cues:
                log(
                    "exporter",
                    "highlight-plan subtitle burn skipped reason=no_retimed_cues "
                    f"session_id={boundary.session_id} match_index={boundary.match_index}",
                )
                return None
            self._write_srt_cues(retimed_srt_path, retimed_cues)
        except OSError as exc:
            log(
                "exporter",
                "highlight-plan subtitle sidecar skipped "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"reason={exc}",
            )
            return None

        if not self.settings.export.use_ass_subtitles:
            return retimed_srt_path

        ass_path = retimed_srt_path.with_suffix(".ass")
        try:
            write_ass_from_srt(
                retimed_srt_path,
                ass_path,
                AssSubtitleStyle(
                    font_name=self.settings.export.ass_font_name,
                    font_size=self.settings.export.ass_font_size,
                    margin_v=self.settings.export.ass_margin_v,
                    outline=self.settings.export.ass_outline,
                    max_chars_per_line=self.settings.export.ass_max_chars_per_line,
                    max_lines=self.settings.export.ass_max_lines,
                ),
            )
        except (OSError, ValueError) as exc:
            log(
                "exporter",
                "highlight-plan ass subtitle sidecar skipped "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"reason={exc}",
            )
            return None
        return ass_path

    @staticmethod
    def _retime_srt_cues_for_edit_plan(
        source_cues: list[SrtCue],
        edit_plan: EditPlanAsset,
    ) -> list[SrtCue]:
        return retime_srt_cues_for_edit_plan(source_cues, edit_plan)

    @staticmethod
    def _retime_srt_cues_for_highlight_plan(
        source_cues: list[SrtCue],
        highlight_plan: HighlightPlanAsset,
    ) -> list[SrtCue]:
        return retime_srt_cues_for_highlight_plan(source_cues, highlight_plan)

    def _write_srt_cues(self, path: Path, cues: list[SrtCue]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        rows: list[str] = []
        for index, cue in enumerate(cues, start=1):
            rows.extend(
                [
                    str(index),
                    (
                        f"{self._format_srt_timestamp(cue.started_at_seconds)} --> "
                        f"{self._format_srt_timestamp(cue.ended_at_seconds)}"
                    ),
                    cue.text,
                    "",
                ]
            )
        path.write_text("\n".join(rows).rstrip() + "\n", encoding="utf-8")

    @staticmethod
    def _format_srt_timestamp(seconds: float) -> str:
        milliseconds = max(0, int(round(seconds * 1000)))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

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
            if (
                self.settings.export.use_hardware_encoding
                and self.settings.export.ffmpeg_video_codec in {"h264", "h265"}
            ):
                args.extend(["-rc", "cbr", "-cbr_padding", "1"])
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
