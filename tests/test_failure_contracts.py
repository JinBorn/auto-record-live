from __future__ import annotations

import unittest

from arl.shared.failure_contracts import (
    CANONICAL_REASON_CODES,
    FAILURE_CATEGORY_HTTP_4XX_NON_RETRYABLE,
    FAILURE_CATEGORY_HTTP_5XX_RETRYABLE,
    FAILURE_CATEGORY_NETWORK_TIMEOUT_RETRYABLE,
    FAILURE_CATEGORY_QUALITY_UNUSABLE_NON_RETRYABLE,
    FAILURE_CATEGORY_UNKNOWN_UNCLASSIFIED_NON_RETRYABLE,
    REASON_CODE_HTTP_4XX,
    REASON_CODE_HTTP_403_FORBIDDEN,
    REASON_CODE_HTTP_5XX,
    REASON_CODE_NETWORK_TIMEOUT,
    REASON_CODE_QUALITY_BELOW_ACTUAL_RESOLUTION,
    REASON_CODE_UNKNOWN_UNCLASSIFIED,
    classify_failure_reason,
)


class ClassifyFailureReasonTest(unittest.TestCase):
    """Coverage for the 403 split landed in PR1 of the recorder 403 cookie
    expiration audit link task. 403 keeps the http_4xx_non_retryable category
    (retry semantics unchanged) but is_emitted under a distinct reason_code so
    downstream consumers can link it to cookie_expired_for_<platform>."""

    def test_403_forbidden_marker_returns_dedicated_reason_code(self) -> None:
        decision = classify_failure_reason("HTTP error 403 Forbidden")
        self.assertEqual(decision.reason_code, REASON_CODE_HTTP_403_FORBIDDEN)
        self.assertEqual(decision.failure_category, FAILURE_CATEGORY_HTTP_4XX_NON_RETRYABLE)
        self.assertFalse(decision.is_retryable)

    def test_server_returned_403_marker_returns_dedicated_reason_code(self) -> None:
        decision = classify_failure_reason("Server returned 403")
        self.assertEqual(decision.reason_code, REASON_CODE_HTTP_403_FORBIDDEN)
        self.assertEqual(decision.failure_category, FAILURE_CATEGORY_HTTP_4XX_NON_RETRYABLE)
        self.assertFalse(decision.is_retryable)

    def test_403_marker_inside_longer_stderr_excerpt(self) -> None:
        # ffmpeg stderr often embeds the status code mid-line.
        decision = classify_failure_reason(
            "[https @ 0xdeadbeef] HTTP error 403 Forbidden"
        )
        self.assertEqual(decision.reason_code, REASON_CODE_HTTP_403_FORBIDDEN)

    def test_404_still_returns_generic_http_4xx_reason_code(self) -> None:
        decision = classify_failure_reason("Server returned 404 Not Found")
        self.assertEqual(decision.reason_code, REASON_CODE_HTTP_4XX)
        self.assertEqual(decision.failure_category, FAILURE_CATEGORY_HTTP_4XX_NON_RETRYABLE)
        self.assertFalse(decision.is_retryable)

    def test_401_still_returns_generic_http_4xx_reason_code(self) -> None:
        decision = classify_failure_reason("HTTP 401 Unauthorized")
        self.assertEqual(decision.reason_code, REASON_CODE_HTTP_4XX)
        self.assertFalse(decision.is_retryable)

    def test_410_still_returns_generic_http_4xx_reason_code(self) -> None:
        decision = classify_failure_reason("HTTP 410 Gone")
        self.assertEqual(decision.reason_code, REASON_CODE_HTTP_4XX)

    def test_503_still_returns_http_5xx_reason_code(self) -> None:
        decision = classify_failure_reason("Server returned 503 Service Unavailable")
        self.assertEqual(decision.reason_code, REASON_CODE_HTTP_5XX)
        self.assertEqual(decision.failure_category, FAILURE_CATEGORY_HTTP_5XX_RETRYABLE)
        self.assertTrue(decision.is_retryable)

    def test_network_timeout_returns_network_timeout_reason_code(self) -> None:
        decision = classify_failure_reason("timed out after 5s")
        self.assertEqual(decision.reason_code, REASON_CODE_NETWORK_TIMEOUT)
        self.assertEqual(
            decision.failure_category, FAILURE_CATEGORY_NETWORK_TIMEOUT_RETRYABLE
        )

    def test_unknown_falls_through_to_unknown_unclassified(self) -> None:
        decision = classify_failure_reason("definitely_not_a_known_marker")
        self.assertEqual(decision.reason_code, REASON_CODE_UNKNOWN_UNCLASSIFIED)
        self.assertEqual(
            decision.failure_category, FAILURE_CATEGORY_UNKNOWN_UNCLASSIFIED_NON_RETRYABLE
        )

    def test_quality_resolution_marker_returns_quality_reason_code(self) -> None:
        decision = classify_failure_reason(
            "quality_below_actual_resolution:1280x720<0x1080"
        )
        self.assertEqual(
            decision.reason_code,
            REASON_CODE_QUALITY_BELOW_ACTUAL_RESOLUTION,
        )
        self.assertEqual(
            decision.failure_category,
            FAILURE_CATEGORY_QUALITY_UNUSABLE_NON_RETRYABLE,
        )
        self.assertFalse(decision.is_retryable)

    def test_http_403_reason_code_is_in_canonical_set(self) -> None:
        # Required so RecorderAuditEvent's validate_core_decision_fields accepts
        # the new reason_code on core decision events.
        self.assertIn(REASON_CODE_HTTP_403_FORBIDDEN, CANONICAL_REASON_CODES)

    def test_quality_resolution_reason_code_is_in_canonical_set(self) -> None:
        self.assertIn(
            REASON_CODE_QUALITY_BELOW_ACTUAL_RESOLUTION,
            CANONICAL_REASON_CODES,
        )


if __name__ == "__main__":
    unittest.main()
