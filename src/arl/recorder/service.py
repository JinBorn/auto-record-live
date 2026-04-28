from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from arl.config import Settings
from arl.orchestrator.models import (
    OrchestratorStateFile,
    RecordingJobRecord,
    RecordingJobStatus,
)
from arl.recorder.models import (
    RecorderAuditEvent,
    RecorderRecoveryAction,
    RecorderStateFile,
)
from arl.shared.contracts import RecordingAsset, SourceType
from arl.shared.failure_contracts import (
    FAILURE_CATEGORY_FFMPEG_PROCESS_ERROR_RETRYABLE,
    CANONICAL_FAILURE_CATEGORIES,
    REASON_CODE_FFMPEG_PROCESS_ERROR,
    classify_failure_reason,
)
from arl.shared.jsonl_store import append_model
from arl.shared.logging import log


@dataclass
class RecordingBuildOutcome:
    output_path: Path | None
    retryable_failure_reason: str | None = None


class RecorderService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.assets_path = settings.storage.temp_dir / "recording-assets.jsonl"
        self.state_path = settings.storage.temp_dir / "recorder-state.json"
        self.audit_path = settings.orchestrator.recorder_event_log_path
        self.recovery_actions_path = (
            settings.storage.temp_dir / "recorder-recovery-actions.jsonl"
        )

    def run(self) -> None:
        log("recorder", "starting")
        log("recorder", f"preferred_resolution={self.settings.recording.preferred_resolution}")
        log("recorder", f"ffmpeg_enabled={self.settings.recording.enable_ffmpeg}")

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
            if job.status == RecordingJobStatus.FAILED:
                recorder_state.retry_attempts_by_job_id.pop(job.job_id, None)
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
            retry_attempt_count = recorder_state.retry_attempts_by_job_id.get(job.job_id, 0)
            outcome = self._build_recording(
                session_id=session.session_id,
                job_id=job.job_id,
                source_type=source_type,
                stream_url=stream_url,
                retry_attempt_count=retry_attempt_count,
            )
            if outcome.retryable_failure_reason is not None:
                retry_decision = classify_failure_reason(outcome.retryable_failure_reason)
                next_retry_attempt = retry_attempt_count + 1
                recorder_state.retry_attempts_by_job_id[job.job_id] = next_retry_attempt
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
                    f"attempt={next_retry_attempt}/{self.settings.recording.auto_retry_max_attempts}",
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
            processed += 1
            log("recorder", f"recording asset written session_id={session.session_id}")

        self._save_state(recorder_state)
        log("recorder", f"processed_jobs={processed}")
        if retries_scheduled > 0:
            log("recorder", f"scheduled_retries={retries_scheduled}")
        if manual_recovery_marked > 0:
            log("recorder", f"manual_recovery_required={manual_recovery_marked}")

    def _load_orchestrator_state(self) -> OrchestratorStateFile:
        path = self.settings.orchestrator.state_file
        if not path.exists():
            return OrchestratorStateFile()
        return OrchestratorStateFile.model_validate_json(path.read_text(encoding="utf-8"))

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
        source_type: SourceType,
        stream_url: str | None,
        retry_attempt_count: int,
    ) -> RecordingBuildOutcome:
        ffmpeg_path = shutil.which("ffmpeg")
        capture_format = self._resolve_browser_capture_format()
        capture_input = self._resolve_browser_capture_input(capture_format)
        if self.settings.recording.enable_ffmpeg and ffmpeg_path is not None:
            if source_type == SourceType.DIRECT_STREAM and stream_url is not None:
                result_path, failure_reason = self._record_with_ffmpeg(
                    session_id=session_id,
                    job_id=job_id,
                    stream_url=stream_url,
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
                result_path, failure_reason = self._record_browser_capture_with_ffmpeg(
                    session_id=session_id,
                    job_id=job_id,
                    capture_format=capture_format,
                    capture_input=capture_input,
                )
                return self._resolve_ffmpeg_result(
                    session_id=session_id,
                    job_id=job_id,
                    source_type=source_type,
                    result_path=result_path,
                    failure_reason=failure_reason,
                    retry_attempt_count=retry_attempt_count,
                )

        skip_reason = None
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
            log("recorder", f"ffmpeg skipped due to missing_browser_capture_input session_id={session_id}")
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
        stream_url: str,
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
            "-i",
            stream_url,
            "-t",
            str(self.settings.recording.direct_stream_timeout_seconds),
            "-c",
            "copy",
            str(output_path),
        ]
        attempts = self.settings.recording.ffmpeg_max_retries + 1
        last_failure_reason = None
        for attempt in range(1, attempts + 1):
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.settings.recording.direct_stream_timeout_seconds + 10,
                )
                self._append_audit(
                    "ffmpeg_record_succeeded",
                    session_id=session_id,
                    job_id=job_id,
                    source_type=SourceType.DIRECT_STREAM,
                )
                return output_path, None
            except (subprocess.SubprocessError, OSError) as error:
                failure_reason = self._format_ffmpeg_failure_reason(error)
                failure_decision = classify_failure_reason(failure_reason)
                last_failure_reason = failure_reason
                log(
                    "recorder",
                    f"ffmpeg record failed session_id={session_id} attempt={attempt}/{attempts} reason={failure_reason}",
                )
                self._append_audit(
                    "ffmpeg_record_failed",
                    session_id=session_id,
                    job_id=job_id,
                    source_type=SourceType.DIRECT_STREAM,
                    reason=failure_reason,
                    decision="attempt_failed",
                    failure_category=failure_decision.failure_category,
                    is_retryable=failure_decision.is_retryable,
                    reason_code=failure_decision.reason_code,
                    reason_detail=failure_reason,
                    attempt=attempt,
                    max_attempts=attempts,
                )
                if not failure_decision.is_retryable:
                    log(
                        "recorder",
                        (
                            "ffmpeg record failure is non-retryable; stop in-run retries "
                            f"session_id={session_id} reason={failure_reason}"
                        ),
                    )
                    break

        return None, last_failure_reason

    def _record_browser_capture_with_ffmpeg(
        self,
        *,
        session_id: str,
        job_id: str,
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
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.settings.recording.browser_capture_timeout_seconds + 10,
                )
                self._append_audit(
                    "ffmpeg_record_succeeded",
                    session_id=session_id,
                    job_id=job_id,
                    source_type=SourceType.BROWSER_CAPTURE,
                )
                return output_path, None
            except (subprocess.SubprocessError, OSError) as error:
                failure_reason = self._format_ffmpeg_failure_reason(error)
                failure_decision = classify_failure_reason(failure_reason)
                last_failure_reason = failure_reason
                log(
                    "recorder",
                    f"ffmpeg browser-capture failed session_id={session_id} attempt={attempt}/{attempts} reason={failure_reason}",
                )
                self._append_audit(
                    "ffmpeg_record_failed",
                    session_id=session_id,
                    job_id=job_id,
                    source_type=SourceType.BROWSER_CAPTURE,
                    reason=failure_reason,
                    decision="attempt_failed",
                    failure_category=failure_decision.failure_category,
                    is_retryable=failure_decision.is_retryable,
                    reason_code=failure_decision.reason_code,
                    reason_detail=failure_reason,
                    attempt=attempt,
                    max_attempts=attempts,
                )
                if not failure_decision.is_retryable:
                    log(
                        "recorder",
                        (
                            "ffmpeg browser-capture failure is non-retryable; stop in-run retries "
                            f"session_id={session_id} reason={failure_reason}"
                        ),
                    )
                    break

        return None, last_failure_reason

    def _format_ffmpeg_failure_reason(self, error: Exception) -> str:
        if isinstance(error, subprocess.TimeoutExpired):
            return f"timed out after {error.timeout}s"
        if isinstance(error, subprocess.CalledProcessError):
            stderr = ""
            if isinstance(error.stderr, str):
                stderr = error.stderr.strip()
            elif isinstance(error.stderr, bytes):
                stderr = error.stderr.decode("utf-8", errors="replace").strip()
            if stderr:
                return stderr.splitlines()[-1][:240]
            return f"exit_status:{error.returncode}"
        if isinstance(error, OSError):
            return f"os_error:{error.__class__.__name__}"
        return f"subprocess_error:{error.__class__.__name__}"

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
        return [
            "Inspect recorder-events.jsonl and ffmpeg stderr for root cause.",
            "Apply targeted fix and rerun recorder.",
        ]

    def _resolve_browser_capture_format(self) -> str:
        configured = self.settings.recording.browser_capture_format.strip().lower()
        if configured and configured != "auto":
            return configured

        if sys.platform.startswith("win"):
            return "gdigrab"
        return "x11grab"

    def _resolve_browser_capture_input(self, capture_format: str) -> str:
        configured = self.settings.recording.browser_capture_input.strip()
        if configured:
            return configured

        if capture_format == "gdigrab":
            return "desktop"
        if capture_format == "x11grab":
            return os.getenv("DISPLAY", "").strip()
        return ""

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
                created_at=datetime.now(timezone.utc),
            ),
        )
