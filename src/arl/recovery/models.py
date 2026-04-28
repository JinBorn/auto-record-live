from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from arl.shared.failure_contracts import validate_core_decision_fields


class RecoveryStateFile(BaseModel):
    processed_action_keys: list[str] = Field(default_factory=list)
    status_by_action_key: dict[str, str] = Field(default_factory=dict)


class RecoveryDispatchEvent(BaseModel):
    event_type: str
    session_id: str
    job_id: str
    action_type: str
    status: str
    decision: str | None = None
    failure_category: str | None = None
    is_retryable: bool | None = None
    reason_code: str | None = None
    reason_detail: str | None = None
    action_key: str | None = None
    message: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _validate_core_decision_contract(self) -> "RecoveryDispatchEvent":
        validate_core_decision_fields(
            event_type=self.event_type,
            decision=self.decision,
            failure_category=self.failure_category,
            is_retryable=self.is_retryable,
            reason_code=self.reason_code,
            reason_detail=self.reason_detail,
        )
        return self
