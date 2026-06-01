from __future__ import annotations

from dataclasses import dataclass


FAILURE_CATEGORY_HTTP_4XX_NON_RETRYABLE = "http_4xx_non_retryable"
FAILURE_CATEGORY_HTTP_5XX_RETRYABLE = "http_5xx_retryable"
FAILURE_CATEGORY_NETWORK_TIMEOUT_RETRYABLE = "network_timeout_retryable"
FAILURE_CATEGORY_FFMPEG_PROCESS_ERROR_RETRYABLE = "ffmpeg_process_error_retryable"
FAILURE_CATEGORY_UNKNOWN_UNCLASSIFIED_NON_RETRYABLE = "unknown_unclassified_non_retryable"
FAILURE_CATEGORY_QUALITY_UNUSABLE_NON_RETRYABLE = "quality_unusable_non_retryable"

CANONICAL_FAILURE_CATEGORIES = {
    FAILURE_CATEGORY_HTTP_4XX_NON_RETRYABLE,
    FAILURE_CATEGORY_HTTP_5XX_RETRYABLE,
    FAILURE_CATEGORY_NETWORK_TIMEOUT_RETRYABLE,
    FAILURE_CATEGORY_FFMPEG_PROCESS_ERROR_RETRYABLE,
    FAILURE_CATEGORY_UNKNOWN_UNCLASSIFIED_NON_RETRYABLE,
    FAILURE_CATEGORY_QUALITY_UNUSABLE_NON_RETRYABLE,
}

REASON_CODE_HTTP_4XX = "http_4xx"
REASON_CODE_HTTP_403_FORBIDDEN = "http_403_forbidden"
REASON_CODE_HTTP_5XX = "http_5xx"
REASON_CODE_NETWORK_TIMEOUT = "network_timeout"
REASON_CODE_FFMPEG_PROCESS_ERROR = "ffmpeg_process_error"
REASON_CODE_UNKNOWN_UNCLASSIFIED = "unknown_unclassified"
REASON_CODE_QUALITY_BELOW_ACTUAL_RESOLUTION = "quality_below_actual_resolution"

CANONICAL_REASON_CODES = {
    REASON_CODE_HTTP_4XX,
    REASON_CODE_HTTP_403_FORBIDDEN,
    REASON_CODE_HTTP_5XX,
    REASON_CODE_NETWORK_TIMEOUT,
    REASON_CODE_FFMPEG_PROCESS_ERROR,
    REASON_CODE_UNKNOWN_UNCLASSIFIED,
    REASON_CODE_QUALITY_BELOW_ACTUAL_RESOLUTION,
}

CORE_DECISION_EVENT_TYPES = {
    "recording_retry_scheduled",
    "ffmpeg_record_failed",
    "ffmpeg_fallback_placeholder",
    "recording_manual_recovery_required",
    "manual_recovery_action_dispatched",
    "manual_recovery_action_resolved",
    "manual_recovery_action_failed",
    "recording_session_retry_budget_exceeded",
    "quality_below_actual_resolution",
    "ffmpeg_export_failed",
    "ffmpeg_export_fallback_placeholder",
}


@dataclass(frozen=True)
class FailureDecision:
    failure_category: str
    is_retryable: bool
    reason_code: str


def classify_failure_reason(reason: str | None) -> FailureDecision:
    text = (reason or "").lower()

    def contains(*markers: str) -> bool:
        return any(marker in text for marker in markers)

    # 403 is the high-confidence cookie-expiration signal. Same retry semantics
    # as other 4xx (non-retryable), but a distinct reason_code so downstream
    # consumers can link it to the cookie_expired_for_<platform> audit channel.
    # Must be matched before the generic 4xx branch so "server returned 403"
    # does not get swallowed by the "server returned 4" prefix.
    if contains("403 forbidden", "server returned 403"):
        return FailureDecision(
            failure_category=FAILURE_CATEGORY_HTTP_4XX_NON_RETRYABLE,
            is_retryable=False,
            reason_code=REASON_CODE_HTTP_403_FORBIDDEN,
        )

    if contains("quality_below_actual_resolution:"):
        return FailureDecision(
            failure_category=FAILURE_CATEGORY_QUALITY_UNUSABLE_NON_RETRYABLE,
            is_retryable=False,
            reason_code=REASON_CODE_QUALITY_BELOW_ACTUAL_RESOLUTION,
        )

    if contains(
        "401 unauthorized",
        "404 not found",
        "410 gone",
        "server returned 4",
    ):
        return FailureDecision(
            failure_category=FAILURE_CATEGORY_HTTP_4XX_NON_RETRYABLE,
            is_retryable=False,
            reason_code=REASON_CODE_HTTP_4XX,
        )

    if contains(
        "server returned 5",
        "internal server error",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
    ):
        return FailureDecision(
            failure_category=FAILURE_CATEGORY_HTTP_5XX_RETRYABLE,
            is_retryable=True,
            reason_code=REASON_CODE_HTTP_5XX,
        )

    if contains(
        "timed out",
        "connection timed out",
        "connection reset",
        "connection refused",
        "network is unreachable",
        "i/o error",
        "resource temporarily unavailable",
        "temporarily unavailable",
    ):
        return FailureDecision(
            failure_category=FAILURE_CATEGORY_NETWORK_TIMEOUT_RETRYABLE,
            is_retryable=True,
            reason_code=REASON_CODE_NETWORK_TIMEOUT,
        )

    if contains(
        "exit_status:",
        "subprocess_error:",
    ):
        return FailureDecision(
            failure_category=FAILURE_CATEGORY_FFMPEG_PROCESS_ERROR_RETRYABLE,
            is_retryable=True,
            reason_code=REASON_CODE_FFMPEG_PROCESS_ERROR,
        )

    return FailureDecision(
        failure_category=FAILURE_CATEGORY_UNKNOWN_UNCLASSIFIED_NON_RETRYABLE,
        is_retryable=False,
        reason_code=REASON_CODE_UNKNOWN_UNCLASSIFIED,
    )


def validate_core_decision_fields(
    *,
    event_type: str,
    decision: str | None,
    failure_category: str | None,
    is_retryable: bool | None,
    reason_code: str | None,
    reason_detail: str | None,
) -> None:
    if event_type not in CORE_DECISION_EVENT_TYPES:
        return
    missing = [
        field
        for field, value in (
            ("decision", decision),
            ("failure_category", failure_category),
            ("is_retryable", is_retryable),
            ("reason_code", reason_code),
            ("reason_detail", reason_detail),
        )
        if value is None
    ]
    if missing:
        raise ValueError(
            f"event_type={event_type} requires canonical decision fields: {', '.join(missing)}"
        )
    if failure_category not in CANONICAL_FAILURE_CATEGORIES:
        raise ValueError(
            f"event_type={event_type} has unsupported failure_category={failure_category}"
        )
    if reason_code not in CANONICAL_REASON_CODES:
        raise ValueError(
            f"event_type={event_type} has unsupported reason_code={reason_code}"
        )
