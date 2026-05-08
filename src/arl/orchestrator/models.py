from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from arl.shared.contracts import LiveState, SourceType
from arl.shared.failure_contracts import validate_core_decision_fields


class RecordingJobStatus(str, Enum):
    QUEUED = "queued"
    RETRYING = "retrying"
    STOPPED = "stopped"
    FAILED = "failed"


class SessionStatus(str, Enum):
    LIVE = "live"
    STOPPED = "stopped"


class AgentSnapshotPayload(BaseModel):
    state: LiveState
    streamer_name: str
    room_url: str
    source_type: SourceType | None = None
    stream_url: str | None = None
    stream_headers: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None
    detected_at: datetime
    platform: str = "douyin"


class AgentEventPayload(BaseModel):
    event_type: str
    snapshot: AgentSnapshotPayload


class RecorderAuditEventPayload(BaseModel):
    event_type: str
    session_id: str
    job_id: str | None = None
    source_type: SourceType | None = None
    decision: str | None = None
    failure_category: str | None = None
    is_retryable: bool | None = None
    reason_code: str | None = None
    reason_detail: str | None = None
    reason: str | None = None
    attempt: int | None = None
    max_attempts: int | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _validate_core_decision_contract(self) -> "RecorderAuditEventPayload":
        validate_core_decision_fields(
            event_type=self.event_type,
            decision=self.decision,
            failure_category=self.failure_category,
            is_retryable=self.is_retryable,
            reason_code=self.reason_code,
            reason_detail=self.reason_detail,
        )
        return self


class SessionRecord(BaseModel):
    session_id: str
    streamer_name: str
    room_url: str
    platform: str = "douyin"
    source_type: SourceType | None = None
    stream_url: str | None = None
    stream_headers: dict[str, str] = Field(default_factory=dict)
    status: SessionStatus
    started_at: datetime
    ended_at: datetime | None = None
    stop_reason: str | None = None


class RecordingJobRecord(BaseModel):
    job_id: str
    session_id: str
    platform: str = "douyin"
    source_type: SourceType | None = None
    stream_url: str | None = None
    stream_headers: dict[str, str] = Field(default_factory=dict)
    status: RecordingJobStatus
    created_at: datetime
    ended_at: datetime | None = None
    stop_reason: str | None = None
    failure_category: str | None = None
    recoverable: bool | None = None
    recovery_hint: str | None = None


class OrchestratorAuditEvent(BaseModel):
    event_type: str
    session_id: str | None = None
    job_id: str | None = None
    message: str | None = None
    created_at: datetime


class OrchestratorStateFile(BaseModel):
    cursor_offset: int = 0
    recorder_cursor_offset: int = 0
    recorder_last_event_at_by_job_id: dict[str, datetime] = Field(default_factory=dict)
    unknown_failure_event_times_by_job_id: dict[str, list[datetime]] = Field(default_factory=dict)
    unknown_failure_last_escalated_at_by_job_id: dict[str, datetime] = Field(default_factory=dict)
    # Per-platform active id maps. Multi-platform deployments (e.g. ARL_PLATFORMS=
    # douyin,bilibili) need each platform to track its own active session/job
    # independently, otherwise a live_started on one platform would supersede the
    # other's already-live session.
    active_session_id_by_platform: dict[str, str] = Field(default_factory=dict)
    active_recording_job_id_by_platform: dict[str, str] = Field(default_factory=dict)
    sessions: list[SessionRecord] = Field(default_factory=list)
    recording_jobs: list[RecordingJobRecord] = Field(default_factory=list)

    # Legacy single-platform active id fields. Kept only to load older state
    # files written before per-platform routing; excluded from serialization so
    # new saves use the dict shape exclusively. Migrated into the dicts on load.
    active_session_id: str | None = Field(default=None, exclude=True)
    active_recording_job_id: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _migrate_legacy_active_ids(self) -> "OrchestratorStateFile":
        if self.active_session_id and not self.active_session_id_by_platform:
            for session in self.sessions:
                if session.session_id == self.active_session_id:
                    self.active_session_id_by_platform[session.platform] = (
                        session.session_id
                    )
                    break
            self.active_session_id = None
        if (
            self.active_recording_job_id
            and not self.active_recording_job_id_by_platform
        ):
            for job in self.recording_jobs:
                if job.job_id == self.active_recording_job_id:
                    self.active_recording_job_id_by_platform[job.platform] = (
                        job.job_id
                    )
                    break
            self.active_recording_job_id = None
        return self
