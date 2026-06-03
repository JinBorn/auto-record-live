from __future__ import annotations

from dataclasses import dataclass
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arl.config import Settings
from arl.orchestrator.models import (
    OrchestratorStateFile,
    RecordingJobRecord,
    RecordingJobStatus,
)
from arl.orchestrator.state_store import load_orchestrator_state
from arl.recorder.models import (
    RecorderAuditEvent,
    RecorderRecoveryAction,
    RecorderStateFile,
)
from arl.shared.contracts import RecordingAsset, SourceType
from arl.shared.failure_contracts import (
    FAILURE_CATEGORY_FFMPEG_PROCESS_ERROR_RETRYABLE,
    FAILURE_CATEGORY_QUALITY_UNUSABLE_NON_RETRYABLE,
    FAILURE_CATEGORY_UNKNOWN_UNCLASSIFIED_NON_RETRYABLE,
    REASON_CODE_HTTP_403_FORBIDDEN,
    REASON_CODE_QUALITY_BELOW_ACTUAL_RESOLUTION,
    REASON_CODE_UNKNOWN_UNCLASSIFIED,
    CANONICAL_FAILURE_CATEGORIES,
    classify_failure_reason,
)
from arl.shared.ffmpeg_runner import (
    format_ffmpeg_failure_reason,
    rotate_stderr_logs,
    run_ffmpeg_attempt,
)
from arl.shared.jsonl_store import append_model
from arl.shared.logging import log


# Maps a recording job's platform to the field name on the matching
# PlatformSettings entry whose non-empty value indicates the operator opted
# into cookie-based auth for that platform. Used by recorder to decide whether
# a 403 ffmpeg failure deserves a cookie_expired_for_<platform> audit signal.
_PLATFORM_COOKIE_FIELD = {
    "douyin": "cookie",
    "bilibili": "sessdata",
}


@dataclass
class RecordingBuildOutcome:
    output_path: Path | None
    retryable_failure_reason: str | None = None


@dataclass(frozen=True)
class ActualQualityProbeResult:
    width: int | None
    height: int | None
    bitrate_kbps: int | None
    reason: str | None = None


class RecorderService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.assets_path = settings.storage.temp_dir / "recording-assets.jsonl"
        self.state_path = settings.storage.temp_dir / "recorder-state.json"
        self.audit_path = settings.orchestrator.recorder_event_log_path
        self.recovery_actions_path = (
            settings.storage.temp_dir / "recorder-recovery-actions.jsonl"
        )
        self._x11_probe_cache: dict[str, tuple[bool, str]] = {}

    def run(self) -> None:
        log("recorder", "starting")
        log("recorder", f"preferred_resolution={self.settings.recording.preferred_resolution}")
        log("recorder", f"ffmpeg_enabled={self.settings.recording.enable_ffmpeg}")
        rotate_stderr_logs(
            self._recorder_stderr_dir,
            self.settings.recording.stderr_retain_count,
        )

        orchestrator_state = self._load_orchestrator_state()
        recorder_state = self._load_state()

        processed = 0
        retries_scheduled = 0
        manual_recovery_marked = 0
        for job in orchestrator_state.recording_jobs:
            session = next(
                (item for item in orchestrator_state.sessions if item.session_id == job.session_id),
                None,
            )
            if session is None:
                continue

            source_type = job.source_type or session.source_type or SourceType.BROWSER_CAPTURE
            stream_url = job.stream_url or session.stream_url
            stream_headers = job.stream_headers or session.stream_headers
            if job.status == RecordingJobStatus.FAILED:
                recorder_state.retry_attempts_by_job_id.pop(job.job_id, None)
                recorder_state.next_eligible_at_by_job_id.pop(job.job_id, None)
                if job.job_id not in recorder_state.manual_required_job_ids:
                    recorder_state.manual_required_job_ids.append(job.job_id)
                    manual_recovery_marked += 1
                    manual_reason_detail = self._manual_recovery_reason(job.stop_reason, job.recovery_hint)
                    manual_decision = self._manual_recovery_decision(
                        failure_category=job.failure_category,
                        stop_reason=job.stop_reason,
                        recovery_hint=job.recovery_hint,
                    )
                    self._append_recovery_action(
                        session_id=session.session_id,
                        job=job,
                        source_type=source_type,
                    )
                    self._append_audit(
                        "recording_manual_recovery_required",
                        session_id=session.session_id,
                        job_id=job.job_id,
                        source_type=source_type,
                        reason=manual_reason_detail,
                        decision="manual_required",
                        failure_category=manual_decision.failure_category,
                        is_retryable=manual_decision.is_retryable,
                        reason_code=manual_decision.reason_code,
                        reason_detail=manual_reason_detail,
                    )
                    log(
                        "recorder",
                        "manual recovery required "
                        f"session_id={session.session_id} job_id={job.job_id}",
                    )
                continue

            if (
                job.status == RecordingJobStatus.RETRYING
                and job.job_id in recorder_state.processed_job_ids
            ):
                recorder_state.processed_job_ids = [
                    processed_job_id
                    for processed_job_id in recorder_state.processed_job_ids
                    if processed_job_id != job.job_id
                ]
                log("recorder", f"reopened retrying job_id={job.job_id}")
            elif job.job_id in recorder_state.processed_job_ids:
                continue

            if job.job_id in recorder_state.manual_required_job_ids:
                recorder_state.manual_required_job_ids = [
                    manual_job_id
                    for manual_job_id in recorder_state.manual_required_job_ids
                    if manual_job_id != job.job_id
                ]

            now = datetime.now(timezone.utc)
            eligible_at = recorder_state.next_eligible_at_by_job_id.get(job.job_id)
            if eligible_at is not None and eligible_at > now:
                log(
                    "recorder",
                    "job deferred "
                    f"session_id={session.session_id} job_id={job.job_id} "
                    f"eligible_at={eligible_at.isoformat()}",
                )
                continue

            retry_attempt_count = recorder_state.retry_attempts_by_job_id.get(job.job_id, 0)
            outcome = self._build_recording(
                session_id=session.session_id,
                job_id=job.job_id,
                platform=job.platform,
                source_type=source_type,
                stream_url=stream_url,
                stream_headers=stream_headers,
                retry_attempt_count=retry_attempt_count,
            )
            if outcome.retryable_failure_reason is not None:
                retry_decision = classify_failure_reason(outcome.retryable_failure_reason)
                next_retry_attempt = retry_attempt_count + 1
                recorder_state.retry_attempts_by_job_id[job.job_id] = next_retry_attempt

                session_retries = (
                    recorder_state.retries_by_session_id.get(session.session_id, 0) + 1
                )
                recorder_state.retries_by_session_id[session.session_id] = session_retries
                budget = self.settings.recording.session_retry_budget
                if session_retries >= budget:
                    self._emit_session_budget_exceeded(
                        recorder_state=recorder_state,
                        orchestrator_state=orchestrator_state,
                        session_id=session.session_id,
                        budget=budget,
                    )
                    recorder_state.retries_by_session_id[session.session_id] = 0
                    continue

                eligible_at_next = now + self._next_eligible_after_yield(next_retry_attempt)
                recorder_state.next_eligible_at_by_job_id[job.job_id] = eligible_at_next
                retries_scheduled += 1
                self._append_audit(
                    "recording_retry_scheduled",
                    session_id=session.session_id,
                    job_id=job.job_id,
                    source_type=source_type,
                    reason=outcome.retryable_failure_reason,
                    decision="retry_scheduled",
                    failure_category=retry_decision.failure_category,
                    is_retryable=retry_decision.is_retryable,
                    reason_code=retry_decision.reason_code,
                    reason_detail=outcome.retryable_failure_reason,
                    attempt=next_retry_attempt,
                    max_attempts=self.settings.recording.auto_retry_max_attempts,
                )
                log(
                    "recorder",
                    "recording retry scheduled "
                    f"session_id={session.session_id} job_id={job.job_id} "
                    f"attempt={next_retry_attempt}/{self.settings.recording.auto_retry_max_attempts} "
                    f"eligible_at={eligible_at_next.isoformat()}",
                )
                continue

            output_path = outcome.output_path
            if output_path is None:
                continue
            asset = RecordingAsset(
                session_id=session.session_id,
                source_type=source_type,
                path=str(output_path),
                started_at=session.started_at,
                ended_at=session.ended_at,
            )
            append_model(self.assets_path, asset)
            if job.job_id not in recorder_state.processed_job_ids:
                recorder_state.processed_job_ids.append(job.job_id)
            recorder_state.retry_attempts_by_job_id.pop(job.job_id, None)
            recorder_state.next_eligible_at_by_job_id.pop(job.job_id, None)
            recorder_state.retries_by_session_id.pop(session.session_id, None)
            processed += 1
            self._save_state(recorder_state)
            log("recorder", f"recording asset written session_id={session.session_id}")
            if source_type == SourceType.DIRECT_STREAM and output_path.suffix.lower() == ".mp4":
                self._remux_direct_stream_recording(
                    session_id=session.session_id,
                    job_id=job.job_id,
                    output_path=output_path,
                )

        self._save_state(recorder_state)
        log("recorder", f"processed_jobs={processed}")
        if retries_scheduled > 0:
            log("recorder", f"scheduled_retries={retries_scheduled}")
        if manual_recovery_marked > 0:
            log("recorder", f"manual_recovery_required={manual_recovery_marked}")

    def _load_orchestrator_state(self) -> OrchestratorStateFile:
        return load_orchestrator_state(self.settings.orchestrator.state_file)

    def _load_state(self) -> RecorderStateFile:
        if not self.state_path.exists():
            return RecorderStateFile()
        return RecorderStateFile.model_validate_json(self.state_path.read_text(encoding="utf-8"))

    def _save_state(self, state: RecorderStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _create_placeholder_recording(self, session_id: str, source_type: SourceType) -> Path:
        output_dir = self.settings.storage.raw_dir / session_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "recording-source.txt"
        output_path.write_text(
            (
                "placeholder recording artifact\n"
                f"session_id={session_id}\n"
                f"source_type={source_type.value}\n"
            ),
            encoding="utf-8",
        )
        return output_path

    def _build_recording(
        self,
        *,
        session_id: str,
        job_id: str,
        platform: str,
        source_type: SourceType,
        stream_url: str | None,
        stream_headers: dict[str, str],
        retry_attempt_count: int,
    ) -> RecordingBuildOutcome:
        ffmpeg_path = shutil.which("ffmpeg")
        capture_format = self._resolve_browser_capture_format()
        capture_input = self._resolve_browser_capture_input(capture_format)
        configured_capture_input = self.settings.recording.browser_capture_input.strip()
        if self.settings.recording.enable_ffmpeg and ffmpeg_path is not None:
            if source_type == SourceType.DIRECT_STREAM and stream_url is not None:
                result_path, failure_reason = self._record_with_ffmpeg(
                    session_id=session_id,
                    job_id=job_id,
                    platform=platform,
                    stream_url=stream_url,
                    stream_headers=stream_headers,
                )
                return self._resolve_ffmpeg_result(
                    session_id=session_id,
                    job_id=job_id,
                    source_type=source_type,
                    result_path=result_path,
                    failure_reason=failure_reason,
                    retry_attempt_count=retry_attempt_count,
                )
            if (
                source_type == SourceType.BROWSER_CAPTURE
                and capture_input
            ):
                selected_input = capture_input
                if capture_format == "x11grab" and not configured_capture_input:
                    selected_input, display_ready, probe_reason = self._select_x11_capture_input(
                        capture_input
                    )
                    if not display_ready:
                        log(
                            "recorder",
                            (
                                "ffmpeg skipped due to unavailable_browser_capture_display "
                                f"session_id={session_id} format={capture_format} "
                                f"input={selected_input} reason={probe_reason}"
                            ),
                        )
                        unavailable_reason = f"unavailable_browser_capture_display:{probe_reason}"
                        self._append_audit(
                            "ffmpeg_skipped",
                            session_id=session_id,
                            job_id=job_id,
                            source_type=source_type,
                            reason=unavailable_reason,
                        )
                        return RecordingBuildOutcome(
                            output_path=self._create_placeholder_recording(session_id, source_type),
                        )
                log(
                    "recorder",
                    (
                        "browser_capture_input_selected "
                        f"session_id={session_id} format={capture_format} input={selected_input}"
                    ),
                )
                result_path, failure_reason = self._record_browser_capture_with_ffmpeg(
                    session_id=session_id,
                    job_id=job_id,
                    platform=platform,
                    capture_format=capture_format,
                    capture_input=selected_input,
                )
                return self._resolve_ffmpeg_result(
                    session_id=session_id,
                    job_id=job_id,
                    source_type=source_type,
                    result_path=result_path,
                    failure_reason=failure_reason,
                    retry_attempt_count=retry_attempt_count,
                )

        skip_reason: str | None = None
        if self.settings.recording.enable_ffmpeg and ffmpeg_path is None:
            log("recorder", f"ffmpeg skipped due to missing_binary session_id={session_id}")
            skip_reason = "missing_binary"
        elif (
            self.settings.recording.enable_ffmpeg
            and source_type == SourceType.DIRECT_STREAM
            and stream_url is None
        ):
            log("recorder", f"ffmpeg skipped due to missing_stream_url session_id={session_id}")
            skip_reason = "missing_stream_url"
        elif (
            self.settings.recording.enable_ffmpeg
            and source_type == SourceType.BROWSER_CAPTURE
            and not capture_input
        ):
            log(
                "recorder",
                (
                    "ffmpeg skipped due to missing_browser_capture_input "
                    f"session_id={session_id} format={capture_format} "
                    f"configured_input={configured_capture_input or '<empty>'} "
                    f"resolved_input={capture_input or '<empty>'}"
                ),
            )
            skip_reason = "missing_browser_capture_input"

        if skip_reason is not None:
            self._append_audit(
                "ffmpeg_skipped",
                session_id=session_id,
                job_id=job_id,
                source_type=source_type,
                reason=skip_reason,
            )
        return RecordingBuildOutcome(
            output_path=self._create_placeholder_recording(session_id, source_type),
        )

    def _resolve_ffmpeg_result(
        self,
        *,
        session_id: str,
        job_id: str,
        source_type: SourceType,
        result_path: Path | None,
        failure_reason: str | None,
        retry_attempt_count: int,
    ) -> RecordingBuildOutcome:
        if result_path is not None:
            return RecordingBuildOutcome(output_path=result_path)

        failure_reason = failure_reason or "ffmpeg_execution_failed"
        failure_decision = classify_failure_reason(failure_reason)
        if (
            failure_decision.failure_category
            == FAILURE_CATEGORY_QUALITY_UNUSABLE_NON_RETRYABLE
        ):
            return RecordingBuildOutcome(output_path=None)
        retryable_failure = failure_decision.is_retryable
        if retryable_failure and self.settings.recording.auto_retry_max_attempts > 0:
            if retry_attempt_count < self.settings.recording.auto_retry_max_attempts:
                return RecordingBuildOutcome(
                    output_path=None,
                    retryable_failure_reason=failure_reason,
                )
            self._append_audit(
                "recording_retry_exhausted",
                session_id=session_id,
                job_id=job_id,
                source_type=source_type,
                reason=failure_reason,
                decision="manual_required",
                failure_category=failure_decision.failure_category,
                is_retryable=failure_decision.is_retryable,
                reason_code=failure_decision.reason_code,
                reason_detail=failure_reason,
                attempt=retry_attempt_count,
                max_attempts=self.settings.recording.auto_retry_max_attempts,
            )

        log("recorder", f"ffmpeg fallback placeholder session_id={session_id}")
        self._append_audit(
            "ffmpeg_fallback_placeholder",
            session_id=session_id,
            job_id=job_id,
            source_type=source_type,
            reason=failure_reason,
            decision="fallback_placeholder",
            failure_category=failure_decision.failure_category,
            is_retryable=failure_decision.is_retryable,
            reason_code=failure_decision.reason_code,
            reason_detail=failure_reason,
        )
        return RecordingBuildOutcome(
            output_path=self._create_placeholder_recording(session_id, source_type),
        )

    def _record_with_ffmpeg(
        self,
        *,
        session_id: str,
        job_id: str,
        platform: str,
        stream_url: str,
        stream_headers: dict[str, str],
    ) -> tuple[Path | None, str | None]:
        output_dir = self.settings.storage.raw_dir / session_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "recording-source.mp4"
        capture_seconds = self._direct_stream_capture_seconds()

        command = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        command.extend(self._build_ffmpeg_header_args(stream_headers))
        command.extend(
            [
                "-i",
                stream_url,
                "-t",
                str(capture_seconds),
                "-c",
                "copy",
                "-movflags",
                "+frag_keyframe+empty_moov+default_base_moof",
                str(output_path),
            ]
        )
        attempts = self.settings.recording.ffmpeg_max_retries + 1
        last_failure_reason = None
        for attempt in range(1, attempts + 1):
            outcome = run_ffmpeg_attempt(
                command,
                timeout=self.settings.recording.direct_stream_timeout_seconds + 10,
                stderr_log_dir=self._recorder_stderr_dir,
                stderr_log_basename=job_id,
                attempt=attempt,
            )
            if outcome.success:
                quality_failure = self._validate_actual_resolution(
                    session_id=session_id,
                    job_id=job_id,
                    output_path=output_path,
                )
                if quality_failure is not None:
                    return None, quality_failure
                self._append_audit(
                    "ffmpeg_record_succeeded",
                    session_id=session_id,
                    job_id=job_id,
                    source_type=SourceType.DIRECT_STREAM,
                )
                return output_path, None
            failure_reason = outcome.reason
            failure_decision = outcome.classification
            last_failure_reason = failure_reason
            yield_on_transient = failure_decision.is_retryable
            decision = (
                "attempt_failed_yield_to_next_probe"
                if yield_on_transient
                else "attempt_failed"
            )
            log(
                "recorder",
                f"ffmpeg record failed session_id={session_id} attempt={attempt}/{attempts} "
                f"decision={decision} reason={failure_reason}",
            )
            self._append_audit(
                "ffmpeg_record_failed",
                session_id=session_id,
                job_id=job_id,
                source_type=SourceType.DIRECT_STREAM,
                reason=failure_reason,
                decision=decision,
                failure_category=failure_decision.failure_category,
                is_retryable=failure_decision.is_retryable,
                reason_code=failure_decision.reason_code,
                reason_detail=failure_reason,
                attempt=attempt,
                max_attempts=attempts,
                stderr_excerpt=outcome.stderr_excerpt,
                stderr_log_path=outcome.stderr_log_path,
            )
            self._maybe_emit_cookie_expired(
                platform=platform,
                session_id=session_id,
                job_id=job_id,
                source_type=SourceType.DIRECT_STREAM,
                reason=failure_reason,
                reason_code=failure_decision.reason_code,
            )
            # Both transient and non-retryable yield after a single ffmpeg
            # invocation: transient yields to the next probe (orchestrator
            # can refresh a stale stream URL); non-retryable stops in-run
            # so cross-run retry budget isn't burned on a doomed URL.
            break

        return None, last_failure_reason

    def _direct_stream_capture_seconds(self) -> int:
        budget = max(1, self.settings.recording.direct_stream_timeout_seconds)
        headroom = max(0, self.settings.recording.direct_stream_finalize_headroom_seconds)
        if headroom <= 0:
            return budget
        if budget <= headroom * 2:
            return budget
        return max(1, budget - headroom)

    def _remux_direct_stream_recording(
        self,
        *,
        session_id: str,
        job_id: str,
        output_path: Path,
    ) -> None:
        if not output_path.exists():
            return

        remux_path = output_path.with_name(f"{output_path.stem}.remux{output_path.suffix}")
        command = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(output_path),
            "-map",
            "0",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(remux_path),
        ]
        outcome = run_ffmpeg_attempt(
            command,
            timeout=self._remux_timeout_seconds(),
            stderr_log_dir=self._recorder_stderr_dir,
            stderr_log_basename=f"{job_id}-remux",
            attempt=1,
        )
        if not outcome.success:
            log(
                "recorder",
                "ffmpeg remux skipped "
                f"session_id={session_id} job_id={job_id} reason={outcome.reason}",
            )
            self._remove_file_if_exists(remux_path)
            return
        if not remux_path.exists():
            log(
                "recorder",
                "ffmpeg remux output missing "
                f"session_id={session_id} job_id={job_id} path={remux_path}",
            )
            return
        try:
            remux_path.replace(output_path)
        except OSError as error:
            log(
                "recorder",
                "ffmpeg remux replace failed "
                f"session_id={session_id} job_id={job_id} error={error.__class__.__name__}",
            )
            self._remove_file_if_exists(remux_path)
            return
        log("recorder", f"ffmpeg remuxed recording session_id={session_id}")

    def _remux_timeout_seconds(self) -> float:
        return max(60.0, min(float(self.settings.recording.direct_stream_timeout_seconds), 600.0))

    @staticmethod
    def _remove_file_if_exists(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            return

    def _validate_actual_resolution(
        self,
        *,
        session_id: str,
        job_id: str,
        output_path: Path,
    ) -> str | None:
        if not self.settings.recording.validate_actual_resolution:
            return None
        if not output_path.exists():
            return None
        ffprobe_path = shutil.which("ffprobe")
        if ffprobe_path is None:
            log(
                "recorder",
                f"actual resolution validation skipped missing_ffprobe session_id={session_id}",
            )
            return None

        probe = self._probe_actual_quality(output_path, ffprobe_path=ffprobe_path)
        if probe.height is None:
            log(
                "recorder",
                "actual resolution validation inconclusive "
                f"session_id={session_id} reason={probe.reason or 'unknown'}",
            )
            return None

        min_height = self.settings.recording.min_actual_resolution_height
        if probe.height >= min_height:
            return None

        reason_detail = (
            "quality_below_actual_resolution:"
            f"{probe.width or 0}x{probe.height}<0x{min_height}"
        )
        if probe.bitrate_kbps is not None:
            reason_detail = f"{reason_detail};bitrate_kbps={probe.bitrate_kbps}"

        try:
            output_path.unlink()
        except OSError:
            log(
                "recorder",
                "failed to remove below-resolution partial "
                f"session_id={session_id} path={output_path}",
            )

        self._append_audit(
            "quality_below_actual_resolution",
            session_id=session_id,
            job_id=job_id,
            source_type=SourceType.DIRECT_STREAM,
            reason=reason_detail,
            decision="quality_rejected",
            failure_category=FAILURE_CATEGORY_QUALITY_UNUSABLE_NON_RETRYABLE,
            is_retryable=False,
            reason_code=REASON_CODE_QUALITY_BELOW_ACTUAL_RESOLUTION,
            reason_detail=reason_detail,
            observed_width=probe.width,
            observed_height=probe.height,
            observed_bitrate_kbps=probe.bitrate_kbps,
            min_required_height=min_height,
        )
        log(
            "recorder",
            "quality rejected below actual resolution "
            f"session_id={session_id} observed={probe.width or 0}x{probe.height} "
            f"min_height={min_height}",
        )
        return reason_detail

    def _probe_actual_quality(
        self,
        output_path: Path,
        *,
        ffprobe_path: str,
    ) -> ActualQualityProbeResult:
        command = [
            ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,bit_rate:format=bit_rate",
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
                timeout=self.settings.recording.actual_resolution_probe_timeout_seconds,
            )
        except (subprocess.SubprocessError, OSError) as error:
            return ActualQualityProbeResult(
                width=None,
                height=None,
                bitrate_kbps=None,
                reason=format_ffmpeg_failure_reason(error),
            )

        stdout = getattr(result, "stdout", "") or ""
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return ActualQualityProbeResult(
                width=None,
                height=None,
                bitrate_kbps=None,
                reason="invalid_ffprobe_json",
            )

        streams = payload.get("streams")
        stream = streams[0] if isinstance(streams, list) and streams else {}
        if not isinstance(stream, dict):
            stream = {}
        width = self._optional_int(stream.get("width"))
        height = self._optional_int(stream.get("height"))
        bit_rate = self._optional_int(stream.get("bit_rate"))
        if bit_rate is None and isinstance(payload.get("format"), dict):
            bit_rate = self._optional_int(payload["format"].get("bit_rate"))
        bitrate_kbps = bit_rate // 1000 if bit_rate is not None else None
        return ActualQualityProbeResult(
            width=width,
            height=height,
            bitrate_kbps=bitrate_kbps,
        )

    @staticmethod
    def _optional_int(value: object) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _build_ffmpeg_header_args(stream_headers: dict[str, str]) -> list[str]:
        if not stream_headers:
            return []

        args: list[str] = []
        header_lines: list[str] = []
        for key, value in stream_headers.items():
            if key.lower() == "user-agent":
                args.extend(["-user_agent", value])
                continue
            header_lines.append(f"{key}: {value}")
        if header_lines:
            args.extend(["-headers", "\r\n".join(header_lines)])
        return args

    def _record_browser_capture_with_ffmpeg(
        self,
        *,
        session_id: str,
        job_id: str,
        platform: str,
        capture_format: str,
        capture_input: str,
    ) -> tuple[Path | None, str | None]:
        output_dir = self.settings.storage.raw_dir / session_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "recording-source.mp4"

        command = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            capture_format,
            "-framerate",
            str(self.settings.recording.browser_capture_fps),
            "-video_size",
            self.settings.recording.browser_capture_resolution,
            "-i",
            capture_input,
            "-t",
            str(self.settings.recording.browser_capture_timeout_seconds),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
        attempts = self.settings.recording.ffmpeg_max_retries + 1
        last_failure_reason = None
        for attempt in range(1, attempts + 1):
            outcome = run_ffmpeg_attempt(
                command,
                timeout=self.settings.recording.browser_capture_timeout_seconds + 10,
                stderr_log_dir=self._recorder_stderr_dir,
                stderr_log_basename=job_id,
                attempt=attempt,
            )
            if outcome.success:
                self._append_audit(
                    "ffmpeg_record_succeeded",
                    session_id=session_id,
                    job_id=job_id,
                    source_type=SourceType.BROWSER_CAPTURE,
                )
                return output_path, None
            failure_reason = outcome.reason
            failure_decision = outcome.classification
            last_failure_reason = failure_reason
            yield_on_transient = failure_decision.is_retryable
            decision = (
                "attempt_failed_yield_to_next_probe"
                if yield_on_transient
                else "attempt_failed"
            )
            log(
                "recorder",
                f"ffmpeg browser-capture failed session_id={session_id} "
                f"attempt={attempt}/{attempts} decision={decision} reason={failure_reason}",
            )
            self._append_audit(
                "ffmpeg_record_failed",
                session_id=session_id,
                job_id=job_id,
                source_type=SourceType.BROWSER_CAPTURE,
                reason=failure_reason,
                decision=decision,
                failure_category=failure_decision.failure_category,
                is_retryable=failure_decision.is_retryable,
                reason_code=failure_decision.reason_code,
                reason_detail=failure_reason,
                attempt=attempt,
                max_attempts=attempts,
                stderr_excerpt=outcome.stderr_excerpt,
                stderr_log_path=outcome.stderr_log_path,
            )
            self._maybe_emit_cookie_expired(
                platform=platform,
                session_id=session_id,
                job_id=job_id,
                source_type=SourceType.BROWSER_CAPTURE,
                reason=failure_reason,
                reason_code=failure_decision.reason_code,
            )
            break

        return None, last_failure_reason

    @staticmethod
    def _next_eligible_after_yield(attempt: int) -> timedelta:
        if attempt <= 1:
            return timedelta(seconds=1)
        if attempt == 2:
            return timedelta(seconds=5)
        if attempt == 3:
            return timedelta(seconds=15)
        return timedelta(seconds=60)

    @property
    def _recorder_stderr_dir(self) -> Path:
        return self.settings.storage.temp_dir / "recorder-stderr"

    def _manual_recovery_reason(
        self,
        stop_reason: str | None,
        recovery_hint: str | None,
    ) -> str:
        if stop_reason and recovery_hint:
            return f"{stop_reason};hint={recovery_hint}"
        if stop_reason:
            return stop_reason
        if recovery_hint:
            return recovery_hint
        return "manual_recovery_required"

    def _append_recovery_action(
        self,
        *,
        session_id: str,
        job: RecordingJobRecord,
        source_type: SourceType,
    ) -> None:
        failure_decision = self._manual_recovery_decision(
            failure_category=job.failure_category,
            stop_reason=job.stop_reason,
            recovery_hint=job.recovery_hint,
        )
        append_model(
            self.recovery_actions_path,
            RecorderRecoveryAction(
                action_type=self._manual_recovery_action_type(failure_decision.failure_category),
                session_id=session_id,
                job_id=job.job_id,
                source_type=source_type,
                failure_category=failure_decision.failure_category,
                recoverable=failure_decision.is_retryable,
                stop_reason=job.stop_reason,
                recovery_hint=job.recovery_hint,
                steps=self._manual_recovery_steps(failure_decision.failure_category),
                created_at=datetime.now(timezone.utc),
            ),
        )

    def _manual_recovery_decision(
        self,
        *,
        failure_category: str | None,
        stop_reason: str | None,
        recovery_hint: str | None,
    ):
        if failure_category in CANONICAL_FAILURE_CATEGORIES:
            if failure_category == FAILURE_CATEGORY_FFMPEG_PROCESS_ERROR_RETRYABLE:
                return classify_failure_reason("exit_status:1")
            return classify_failure_reason(
                {
                    "http_4xx_non_retryable": "server returned 404 not found",
                    "http_5xx_retryable": "server returned 503 service unavailable",
                    "network_timeout_retryable": "timed out",
                    "quality_unusable_non_retryable": "quality_below_actual_resolution:0x720<0x1080",
                    "unknown_unclassified_non_retryable": "unknown_unclassified",
                }.get(failure_category, "unknown_unclassified")
            )
        reason = self._manual_recovery_reason(stop_reason, recovery_hint)
        return classify_failure_reason(reason)

    def _manual_recovery_action_type(self, failure_category: str) -> str:
        mapping = {
            "http_4xx_non_retryable": "restore_source_prerequisites",
            "http_5xx_retryable": "check_network_source_stability",
            "network_timeout_retryable": "check_network_source_stability",
            "ffmpeg_process_error_retryable": "inspect_ffmpeg_process_failure",
            "unknown_unclassified_non_retryable": "inspect_failure_logs",
            "quality_unusable_non_retryable": "wait_for_higher_quality_source",
        }
        return mapping.get(failure_category, "inspect_failure_logs")

    def _manual_recovery_steps(self, failure_category: str) -> list[str]:
        if failure_category == "http_4xx_non_retryable":
            return [
                "Restore stream prerequisites (stream URL or browser capture input).",
                "Run recorder once again after prerequisites are available.",
            ]
        if failure_category in {
            "http_5xx_retryable",
            "network_timeout_retryable",
            "ffmpeg_process_error_retryable",
        }:
            return [
                "Check source/network stability and recorder host connectivity.",
                "Retry recorder after transient conditions recover.",
            ]
        if failure_category == "quality_unusable_non_retryable":
            return [
                "Wait for the room/platform to expose a 1080p or higher source.",
                "Retry recording after the next live stream snapshot refresh.",
            ]
        return [
            "Inspect recorder-events.jsonl and ffmpeg stderr for root cause.",
            "Apply targeted fix and rerun recorder.",
        ]

    def _resolve_browser_capture_format(self) -> str:
        configured = self.settings.recording.browser_capture_format.strip().lower()
        if configured and configured != "auto":
            supported_formats = {"gdigrab", "x11grab", "avfoundation"}
            if configured in supported_formats:
                return configured
            fallback = self._default_browser_capture_format()
            log(
                "recorder",
                (
                    "unsupported_browser_capture_format "
                    f"configured={configured} fallback={fallback}"
                ),
            )
            return fallback

        return self._default_browser_capture_format()

    def _default_browser_capture_format(self) -> str:
        if sys.platform.startswith("win"):
            return "gdigrab"
        if sys.platform == "darwin":
            return "avfoundation"
        return "x11grab"

    def _resolve_browser_capture_input(self, capture_format: str) -> str:
        configured = self.settings.recording.browser_capture_input.strip()
        if configured:
            return configured

        if capture_format == "gdigrab":
            return "desktop"
        if capture_format == "x11grab":
            return os.getenv("DISPLAY", "").strip()
        if capture_format == "avfoundation":
            return "default:none"
        return ""

    def _probe_x11_display_ready(self, capture_input: str) -> tuple[bool, str]:
        cached = self._x11_probe_cache.get(capture_input)
        if cached is not None:
            return cached

        command = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "x11grab",
            "-video_size",
            "16x16",
            "-framerate",
            "1",
            "-i",
            capture_input,
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            outcome = (True, "ok")
        except (subprocess.SubprocessError, OSError) as error:
            outcome = (False, format_ffmpeg_failure_reason(error))
        self._x11_probe_cache[capture_input] = outcome
        return outcome

    def _select_x11_capture_input(self, initial_input: str) -> tuple[str, bool, str]:
        candidate_inputs: list[str] = []
        for candidate in (initial_input.strip(), ":0", ":0.0"):
            if candidate and candidate not in candidate_inputs:
                candidate_inputs.append(candidate)

        last_reason = "missing_browser_capture_input"
        for candidate in candidate_inputs:
            display_ready, probe_reason = self._probe_x11_display_ready(candidate)
            if display_ready:
                return candidate, True, "ok"
            last_reason = probe_reason
        return (candidate_inputs[0] if candidate_inputs else initial_input), False, last_reason

    def _emit_session_budget_exceeded(
        self,
        *,
        recorder_state: RecorderStateFile,
        orchestrator_state: OrchestratorStateFile,
        session_id: str,
        budget: int,
    ) -> None:
        reason_detail = f"session_retry_budget_exceeded:{budget}"
        emitted_any = False
        for job in orchestrator_state.recording_jobs:
            if job.session_id != session_id:
                continue
            # Escalate every job still in the pipeline for this session. In
            # production the orchestrator transitions STOPPED → RETRYING after a
            # scheduled retry; in single-process recorder runs jobs may remain
            # STOPPED. Both should be escalated when the session budget trips —
            # only FAILED jobs (already terminal) are left alone.
            if job.status == RecordingJobStatus.FAILED:
                continue
            emitted_any = True
            recorder_state.next_eligible_at_by_job_id.pop(job.job_id, None)
            self._append_audit(
                "recording_session_retry_budget_exceeded",
                session_id=session_id,
                job_id=job.job_id,
                source_type=job.source_type or SourceType.BROWSER_CAPTURE,
                reason=reason_detail,
                decision="manual_required",
                failure_category=FAILURE_CATEGORY_UNKNOWN_UNCLASSIFIED_NON_RETRYABLE,
                is_retryable=False,
                reason_code=REASON_CODE_UNKNOWN_UNCLASSIFIED,
                reason_detail=reason_detail,
            )
        log(
            "recorder",
            "session retry budget exceeded "
            f"session_id={session_id} budget={budget} escalated={emitted_any}",
        )

    def _append_audit(
        self,
        event_type: str,
        *,
        session_id: str,
        job_id: str,
        source_type: SourceType,
        reason: str | None = None,
        decision: str | None = None,
        failure_category: str | None = None,
        is_retryable: bool | None = None,
        reason_code: str | None = None,
        reason_detail: str | None = None,
        attempt: int | None = None,
        max_attempts: int | None = None,
        stderr_excerpt: str | None = None,
        stderr_log_path: str | None = None,
        observed_width: int | None = None,
        observed_height: int | None = None,
        observed_bitrate_kbps: int | None = None,
        min_required_height: int | None = None,
    ) -> None:
        append_model(
            self.audit_path,
            RecorderAuditEvent(
                event_type=event_type,
                session_id=session_id,
                job_id=job_id,
                source_type=source_type,
                decision=decision,
                failure_category=failure_category,
                is_retryable=is_retryable,
                reason_code=reason_code,
                reason_detail=reason_detail,
                reason=reason,
                attempt=attempt,
                max_attempts=max_attempts,
                stderr_excerpt=stderr_excerpt,
                stderr_log_path=stderr_log_path,
                observed_width=observed_width,
                observed_height=observed_height,
                observed_bitrate_kbps=observed_bitrate_kbps,
                min_required_height=min_required_height,
                created_at=datetime.now(timezone.utc),
            ),
        )

    def _maybe_emit_cookie_expired(
        self,
        *,
        platform: str | None,
        session_id: str,
        job_id: str,
        source_type: SourceType,
        reason: str,
        reason_code: str | None,
    ) -> None:
        # Recorder-side cookie-expiration signal: emit only on 403 (high-
        # confidence cookie suspect) AND when the operator opted into cookie-
        # based auth for this platform (env var set). Mirrors the probe-side
        # cookie_expired_for_<platform> audit shape so consumers can grep both
        # sources from orchestrator-events.jsonl with one pattern.
        if reason_code != REASON_CODE_HTTP_403_FORBIDDEN:
            return
        if not self._platform_cookie_configured(platform):
            return
        event_type = f"cookie_expired_for_{platform}"
        self._append_audit(
            event_type,
            session_id=session_id,
            job_id=job_id,
            source_type=source_type,
            reason=reason,
        )
        log(
            "recorder",
            f"emitted event={event_type} session_id={session_id} job_id={job_id}",
        )

    def _platform_cookie_configured(self, platform: str | None) -> bool:
        if platform is None:
            return False
        field_name = _PLATFORM_COOKIE_FIELD.get(platform)
        if field_name is None:
            return False
        for entry in self.settings.platforms:
            if entry.type != platform:
                continue
            value = getattr(entry, field_name, "")
            return bool(value)
        return False
