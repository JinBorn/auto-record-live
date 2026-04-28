from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from arl.shared.contracts import SourceType
from arl.shared.failure_contracts import validate_core_decision_fields


class RecorderStateFile(BaseModel):
    processed_job_ids: list[str] = Field(default_factory=list)
    retry_attempts_by_job_id: dict[str, int] = Field(default_factory=dict)
    manual_required_job_ids: list[str] = Field(default_factory=list)


class RecorderAuditEvent(BaseModel):
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
    def _validate_core_decision_contract(self) -> "RecorderAuditEvent":
        validate_core_decision_fields(
            event_type=self.event_type,
            decision=self.decision,
            failure_category=self.failure_category,
            is_retryable=self.is_retryable,
            reason_code=self.reason_code,
            reason_detail=self.reason_detail,
        )
        return self


class RecorderRecoveryAction(BaseModel):
    action_type: str
    session_id: str
    job_id: str
    source_type: SourceType | None = None
    failure_category: str | None = None
    recoverable: bool | None = None
    stop_reason: str | None = None
    recovery_hint: str | None = None
    steps: list[str] = Field(default_factory=list)
    created_at: datetime
