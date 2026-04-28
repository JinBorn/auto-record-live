from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from arl.config import Settings
from arl.shared.failure_contracts import (
    FAILURE_CATEGORY_UNKNOWN_UNCLASSIFIED_NON_RETRYABLE,
    classify_failure_reason,
)
from arl.shared.logging import log

from arl.orchestrator.event_reader import AgentEventReader
from arl.orchestrator.models import (
    AgentEventPayload,
    OrchestratorStateFile,
    RecorderAuditEventPayload,
    RecordingJobRecord,
    RecordingJobStatus,
    SessionRecord,
    SessionStatus,
)
from arl.orchestrator.recorder_event_reader import RecorderEventReader
from arl.orchestrator.state_store import OrchestratorStateStore


class OrchestratorService:
    _UNKNOWN_FAILURE_THRESHOLD = 3
    _UNKNOWN_FAILURE_WINDOW = timedelta(minutes=30)

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.reader = AgentEventReader(settings.orchestrator.agent_event_log_path)
        self.recorder_reader = RecorderEventReader(
            settings.orchestrator.recorder_event_log_path
        )
        self.state_store = OrchestratorStateStore(
            settings.orchestrator.state_file,
            settings.orchestrator.audit_log_path,
        )

    def run(self, once: bool = False) -> None:
        log("orchestrator", "starting")
        log(
            "orchestrator",
            f"input_event_log={self.settings.orchestrator.agent_event_log_path}",
        )
        log(
            "orchestrator",
            f"recorder_event_log={self.settings.orchestrator.recorder_event_log_path}",
        )
        log(
            "orchestrator",
            f"state_file={self.settings.orchestrator.state_file}",
        )
        if once:
            self.run_once()
            return

        interval = self.settings.orchestrator.poll_interval_seconds
        while True:
            self.run_once()
            time.sleep(interval)

    def run_once(self) -> None:
        state = self.state_store.load()
        result = self.reader.read_from(state.cursor_offset)
        if result.reset_cursor:
            log(
                "orchestrator",
                "input event log was truncated; cursor reset to file start",
            )

        processed = 0
        for event in result.events:
            self._handle_event(state, event)
            processed += 1

        recorder_result = self.recorder_reader.read_from(state.recorder_cursor_offset)
        if recorder_result.reset_cursor:
            log(
                "orchestrator",
                "recorder event log was truncated; cursor reset to file start",
            )

        recorder_processed = 0
        for recorder_event in recorder_result.events:
            self._handle_recorder_event(state, recorder_event)
            recorder_processed += 1

        state.cursor_offset = result.next_offset
        state.recorder_cursor_offset = recorder_result.next_offset
        self.state_store.save(state)

        if result.invalid_lines > 0:
            log(
                "orchestrator",
                f"skipped invalid event lines={result.invalid_lines}",
            )
        if recorder_result.invalid_lines > 0:
            log(
                "orchestrator",
                f"skipped invalid recorder event lines={recorder_result.invalid_lines}",
            )
        if processed > 0:
            log(
                "orchestrator",
                f"processed events={processed} cursor={state.cursor_offset}",
            )
        if recorder_processed > 0:
            log(
                "orchestrator",
                f"processed recorder events={recorder_processed} cursor={state.recorder_cursor_offset}",
            )

    def _handle_event(
        self,
        state: OrchestratorStateFile,
        event: AgentEventPayload,
    ) -> None:
        if event.event_type == "live_started":
            self._on_live_started(state, event)
            return
        if event.event_type == "live_stopped":
            self._on_live_stopped(state, event)
            return
        self.state_store.append_audit(
            "ignored_unknown_event_type",
            message=f"event_type={event.event_type}",
        )
        log("orchestrator", f"ignored unknown event_type={event.event_type}")

    def _on_live_started(
        self,
        state: OrchestratorStateFile,
        event: AgentEventPayload,
    ) -> None:
        snapshot = event.snapshot
        active_session = self._active_session(state)
        if active_session is not None and active_session.ended_at is None:
            # Avoid duplicate session starts on repeated live_started events.
            if active_session.stream_url is None and snapshot.stream_url is not None:
                active_session.stream_url = snapshot.stream_url
                active_session.source_type = snapshot.source_type
                self.state_store.append_audit(
                    "active_session_enriched",
                    session_id=active_session.session_id,
                    message="updated stream_url from duplicate live_started event",
                )
            self.state_store.append_audit(
                "duplicate_live_started_ignored",
                session_id=active_session.session_id,
                message=f"streamer={snapshot.streamer_name}",
            )
            log(
                "orchestrator",
                f"duplicate live_started ignored session_id={active_session.session_id}",
            )
            return

        session_id = self._build_id("session", snapshot.detected_at)
        session = SessionRecord(
            session_id=session_id,
            streamer_name=snapshot.streamer_name,
            room_url=snapshot.room_url,
            source_type=snapshot.source_type,
            stream_url=snapshot.stream_url,
            status=SessionStatus.LIVE,
            started_at=snapshot.detected_at,
        )
        state.sessions.append(session)
        state.active_session_id = session_id
        self.state_store.append_audit(
            "session_started",
            session_id=session_id,
            message=f"streamer={snapshot.streamer_name}",
        )
        log(
            "orchestrator",
            f"session started session_id={session_id} source={session.source_type or 'none'}",
        )

        if self.settings.orchestrator.auto_create_recording_job:
            job_id = self._build_id("recording", snapshot.detected_at)
            job = RecordingJobRecord(
                job_id=job_id,
                session_id=session_id,
                source_type=snapshot.source_type,
                stream_url=snapshot.stream_url,
                status=RecordingJobStatus.QUEUED,
                created_at=snapshot.detected_at,
            )
            state.recording_jobs.append(job)
            state.active_recording_job_id = job_id
            self.state_store.append_audit(
                "recording_job_created",
                session_id=session_id,
                job_id=job_id,
                message=f"source={job.source_type or 'none'}",
            )
            log(
                "orchestrator",
                f"recording job queued job_id={job_id} session_id={session_id}",
            )

    def _on_live_stopped(
        self,
        state: OrchestratorStateFile,
        event: AgentEventPayload,
    ) -> None:
        snapshot = event.snapshot
        active_session = self._active_session(state)
        if active_session is None or active_session.ended_at is not None:
            self.state_store.append_audit(
                "live_stopped_without_active_session",
                message=f"streamer={snapshot.streamer_name}",
            )
            log("orchestrator", "live_stopped received without active session")
            return

        active_session.ended_at = snapshot.detected_at
        active_session.status = SessionStatus.STOPPED
        active_session.stop_reason = snapshot.reason or "live_stopped"
        self.state_store.append_audit(
            "session_stopped",
            session_id=active_session.session_id,
            message=f"reason={active_session.stop_reason}",
        )
        log(
            "orchestrator",
            f"session stopped session_id={active_session.session_id}",
        )
        state.active_session_id = None

        active_job = self._active_job(state)
        if active_job is not None and active_job.ended_at is None:
            active_job.status = RecordingJobStatus.STOPPED
            active_job.ended_at = snapshot.detected_at
            active_job.stop_reason = snapshot.reason or "live_stopped"
            self.state_store.append_audit(
                "recording_job_stopped",
                session_id=active_job.session_id,
                job_id=active_job.job_id,
                message=f"reason={active_job.stop_reason}",
            )
            log(
                "orchestrator",
                f"recording job stopped job_id={active_job.job_id}",
            )
        state.active_recording_job_id = None

    def _handle_recorder_event(
        self,
        state: OrchestratorStateFile,
        event: RecorderAuditEventPayload,
    ) -> None:
        if event.job_id is None:
            self.state_store.append_audit(
                "recorder_event_without_job_id",
                message=f"event_type={event.event_type} session_id={event.session_id}",
            )
            return

        job = self._find_job(state, event.job_id)
        if job is None:
            self.state_store.append_audit(
                "recorder_event_job_not_found",
                job_id=event.job_id,
                message=f"event_type={event.event_type}",
            )
            return

        known_event_types = {
            "recording_retry_scheduled",
            "recording_retry_exhausted",
            "ffmpeg_fallback_placeholder",
            "ffmpeg_skipped",
            "ffmpeg_record_succeeded",
            "ffmpeg_record_failed",
        }
        if event.event_type not in known_event_types:
            self.state_store.append_audit(
                "recorder_event_ignored",
                session_id=job.session_id,
                job_id=job.job_id,
                message=f"event_type={event.event_type}",
            )
            # Unknown recorder event types are intentionally excluded from monotonic
            # watermark updates so they cannot block later known transition events.
            return

        if self._is_stale_recorder_event(state, event):
            self.state_store.append_audit(
                "recorder_event_stale_ignored",
                session_id=job.session_id,
                job_id=job.job_id,
                message=f"event_type={event.event_type}",
            )
            return

        if event.event_type == "recording_retry_scheduled":
            job.status = RecordingJobStatus.RETRYING
            job.stop_reason = self._event_reason_detail(event) or "retry_scheduled"
            job.ended_at = None
            state.active_recording_job_id = job.job_id
            self._apply_failure_metadata(state, job, event)
            self.state_store.append_audit(
                "recording_job_retrying",
                session_id=job.session_id,
                job_id=job.job_id,
                message=f"reason={job.stop_reason}",
            )
            self._append_recovery_audit(
                job,
                mode="retry",
                origin_event=event.event_type,
            )
            self._mark_recorder_event_applied(state, event)
            return

        if event.event_type == "recording_retry_exhausted":
            job.status = RecordingJobStatus.FAILED
            job.stop_reason = self._event_reason_detail(event) or "retry_exhausted"
            if job.ended_at is None:
                job.ended_at = event.created_at
            if state.active_recording_job_id == job.job_id:
                state.active_recording_job_id = None
            self._apply_failure_metadata(state, job, event)
            self.state_store.append_audit(
                "recording_job_failed",
                session_id=job.session_id,
                job_id=job.job_id,
                message=f"reason={job.stop_reason}",
            )
            self._append_recovery_audit(
                job,
                mode="manual",
                origin_event=event.event_type,
            )
            self._mark_recorder_event_applied(state, event)
            return

        if event.event_type in {"ffmpeg_fallback_placeholder", "ffmpeg_skipped"}:
            job.status = RecordingJobStatus.FAILED
            job.stop_reason = self._event_reason_detail(event) or event.event_type
            if job.ended_at is None:
                job.ended_at = event.created_at
            if state.active_recording_job_id == job.job_id:
                state.active_recording_job_id = None
            self._apply_failure_metadata(state, job, event)
            self.state_store.append_audit(
                "recording_job_failed",
                session_id=job.session_id,
                job_id=job.job_id,
                message=f"reason={job.stop_reason}",
            )
            self._append_recovery_audit(
                job,
                mode="manual",
                origin_event=event.event_type,
            )
            self._mark_recorder_event_applied(state, event)
            return

        if event.event_type == "ffmpeg_record_succeeded":
            job.status = RecordingJobStatus.STOPPED
            job.stop_reason = None
            if job.ended_at is None:
                job.ended_at = event.created_at
            if state.active_recording_job_id == job.job_id:
                state.active_recording_job_id = None
            self._clear_failure_metadata(job)
            self.state_store.append_audit(
                "recording_job_recorded",
                session_id=job.session_id,
                job_id=job.job_id,
                message="ffmpeg_record_succeeded",
            )
            self._mark_recorder_event_applied(state, event)
            return

        if event.event_type == "ffmpeg_record_failed":
            if job.status not in {RecordingJobStatus.FAILED, RecordingJobStatus.STOPPED}:
                self._apply_failure_metadata(state, job, event)
                is_retryable = job.recoverable if job.recoverable is not None else True
                if is_retryable:
                    job.status = RecordingJobStatus.RETRYING
                    job.stop_reason = self._event_reason_detail(event) or "ffmpeg_record_failed"
                    job.ended_at = None
                    self.state_store.append_audit(
                        "recording_job_attempt_failed_retrying",
                        session_id=job.session_id,
                        job_id=job.job_id,
                        message=f"reason={job.stop_reason}",
                    )
                    self._append_recovery_audit(
                        job,
                        mode="retry",
                        origin_event=event.event_type,
                    )
                else:
                    job.status = RecordingJobStatus.FAILED
                    job.stop_reason = self._event_reason_detail(event) or "ffmpeg_record_failed"
                    if job.ended_at is None:
                        job.ended_at = event.created_at
                    if state.active_recording_job_id == job.job_id:
                        state.active_recording_job_id = None
                    self.state_store.append_audit(
                        "recording_job_attempt_failed_terminal",
                        session_id=job.session_id,
                        job_id=job.job_id,
                        message=f"reason={job.stop_reason}",
                    )
                    self._append_recovery_audit(
                        job,
                        mode="manual",
                        origin_event=event.event_type,
                    )
            else:
                self.state_store.append_audit(
                    "recording_job_attempt_failed_ignored_terminal_job",
                    session_id=job.session_id,
                    job_id=job.job_id,
                    message=f"reason={self._event_reason_detail(event) or 'unknown'}",
                )
            self._mark_recorder_event_applied(state, event)
            return

        return

    def _active_session(self, state: OrchestratorStateFile) -> SessionRecord | None:
        if state.active_session_id is None:
            return None
        for session in state.sessions:
            if session.session_id == state.active_session_id:
                return session
        return None

    def _active_job(self, state: OrchestratorStateFile) -> RecordingJobRecord | None:
        if state.active_recording_job_id is None:
            return None
        for job in state.recording_jobs:
            if job.job_id == state.active_recording_job_id:
                return job
        return None

    def _find_job(self, state: OrchestratorStateFile, job_id: str) -> RecordingJobRecord | None:
        for job in state.recording_jobs:
            if job.job_id == job_id:
                return job
        return None

    def _is_stale_recorder_event(
        self,
        state: OrchestratorStateFile,
        event: RecorderAuditEventPayload,
    ) -> bool:
        job_id = event.job_id
        if job_id is None:
            return False
        last_applied = state.recorder_last_event_at_by_job_id.get(job_id)
        if last_applied is None:
            return False
        return event.created_at <= last_applied

    def _mark_recorder_event_applied(
        self,
        state: OrchestratorStateFile,
        event: RecorderAuditEventPayload,
    ) -> None:
        if event.job_id is None:
            return
        state.recorder_last_event_at_by_job_id[event.job_id] = event.created_at

    def _apply_failure_metadata(
        self,
        state: OrchestratorStateFile,
        job: RecordingJobRecord,
        event: RecorderAuditEventPayload,
    ) -> None:
        reason = self._event_reason_detail(event)
        category, recoverable, hint = self._classify_failure(reason)
        if event.failure_category:
            category = event.failure_category
        if event.is_retryable is not None:
            recoverable = event.is_retryable
        job.failure_category = category
        job.recoverable = recoverable
        job.recovery_hint = hint
        self._track_unknown_failure_escalation(
            state=state,
            job=job,
            event=event,
        )

    def _event_reason_detail(self, event: RecorderAuditEventPayload) -> str | None:
        return event.reason_detail or event.reason or event.reason_code

    def _clear_failure_metadata(self, job: RecordingJobRecord) -> None:
        job.failure_category = None
        job.recoverable = None
        job.recovery_hint = None

    def _append_recovery_audit(
        self,
        job: RecordingJobRecord,
        *,
        mode: str,
        origin_event: str,
    ) -> None:
        category = job.failure_category or "unknown"
        recoverable = job.recoverable if job.recoverable is not None else False
        hint = job.recovery_hint or "none"
        if mode == "retry":
            event_type = "recording_job_recovery_retry_planned"
        else:
            event_type = "recording_job_recovery_manual_required"
        self.state_store.append_audit(
            event_type,
            session_id=job.session_id,
            job_id=job.job_id,
            message=(
                f"origin={origin_event} category={category} "
                f"recoverable={recoverable} hint={hint}"
            ),
        )

    def _classify_failure(
        self,
        reason: str | None,
    ) -> tuple[str, bool, str]:
        decision = classify_failure_reason(reason)
        hint_by_category = {
            "http_4xx_non_retryable": (
                "Source rejected the request (HTTP 4xx). "
                "Refresh stream URL/session prerequisites before rerun."
            ),
            "http_5xx_retryable": (
                "Upstream server is unstable (HTTP 5xx). "
                "Retry recording and monitor source availability."
            ),
            "network_timeout_retryable": (
                "Network timeout/transport instability detected. "
                "Retry recording and verify host/source connectivity."
            ),
            "ffmpeg_process_error_retryable": (
                "ffmpeg process failed without a terminal source classification. "
                "Retry once conditions recover and inspect stderr details."
            ),
            "unknown_unclassified_non_retryable": (
                "Failure classification was inconclusive. "
                "Follow manual recovery and inspect recorder-events diagnostics."
            ),
        }
        return (
            decision.failure_category,
            decision.is_retryable,
            hint_by_category[decision.failure_category],
        )

    def _track_unknown_failure_escalation(
        self,
        *,
        state: OrchestratorStateFile,
        job: RecordingJobRecord,
        event: RecorderAuditEventPayload,
    ) -> None:
        if event.job_id is None:
            return
        if (
            job.failure_category != FAILURE_CATEGORY_UNKNOWN_UNCLASSIFIED_NON_RETRYABLE
            or job.recoverable is not False
        ):
            return

        created_at = event.created_at
        window_start = created_at - self._UNKNOWN_FAILURE_WINDOW
        prior = state.unknown_failure_event_times_by_job_id.get(event.job_id, [])
        recent = [timestamp for timestamp in prior if timestamp >= window_start]
        recent.append(created_at)
        state.unknown_failure_event_times_by_job_id[event.job_id] = recent

        if len(recent) < self._UNKNOWN_FAILURE_THRESHOLD:
            return

        last_escalated_at = state.unknown_failure_last_escalated_at_by_job_id.get(event.job_id)
        if last_escalated_at is not None and last_escalated_at >= window_start:
            return

        state.unknown_failure_last_escalated_at_by_job_id[event.job_id] = created_at
        self.state_store.append_audit(
            "recording_unknown_failure_escalated",
            session_id=job.session_id,
            job_id=job.job_id,
            message=(
                "failure_category=unknown_unclassified_non_retryable "
                f"count={len(recent)} window_minutes=30"
            ),
        )
        log(
            "orchestrator",
            (
                "unknown failure escalation "
                f"job_id={job.job_id} session_id={job.session_id} "
                f"count={len(recent)} window_minutes=30"
            ),
        )

    def _build_id(self, prefix: str, detected_at: datetime) -> str:
        timestamp = detected_at.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"{prefix}-{timestamp}-{uuid4().hex[:8]}"
