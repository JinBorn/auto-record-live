from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from arl.shared.failure_contracts import validate_core_decision_fields


class ExporterStateFile(BaseModel):
    processed_match_keys: list[str] = Field(default_factory=list)


class ExporterAuditEvent(BaseModel):
    """Schema mirror of `RecorderAuditEvent` for `exporter-events.jsonl`.

    `match_index` replaces recorder's `job_id` as the per-row work identifier.
    Canonical decision fields are enforced via `validate_core_decision_fields`
    when `event_type` is in `CORE_DECISION_EVENT_TYPES` (i.e.
    `ffmpeg_export_failed`, `ffmpeg_export_fallback_placeholder`, and
    `ffmpeg_export_batch_aborted`).
    `ffmpeg_export_succeeded` rows skip validation, mirroring
    `ffmpeg_record_succeeded`.
    """

    event_type: str
    session_id: str
    match_index: int | None = None
    decision: str | None = None
    failure_category: str | None = None
    is_retryable: bool | None = None
    reason_code: str | None = None
    reason_detail: str | None = None
    reason: str | None = None
    attempt: int | None = None
    max_attempts: int | None = None
    stderr_excerpt: str | None = None
    stderr_log_path: str | None = None
    consecutive_fallbacks: int | None = None
    remaining_matches: int | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _validate_core_decision_contract(self) -> "ExporterAuditEvent":
        validate_core_decision_fields(
            event_type=self.event_type,
            decision=self.decision,
            failure_category=self.failure_category,
            is_retryable=self.is_retryable,
            reason_code=self.reason_code,
            reason_detail=self.reason_detail,
        )
        return self
