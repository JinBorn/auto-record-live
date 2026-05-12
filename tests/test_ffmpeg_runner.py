from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arl.shared.failure_contracts import (
    FAILURE_CATEGORY_FFMPEG_PROCESS_ERROR_RETRYABLE,
    FAILURE_CATEGORY_HTTP_4XX_NON_RETRYABLE,
    FAILURE_CATEGORY_HTTP_5XX_RETRYABLE,
    FAILURE_CATEGORY_NETWORK_TIMEOUT_RETRYABLE,
    REASON_CODE_FFMPEG_PROCESS_ERROR,
    REASON_CODE_HTTP_4XX,
    REASON_CODE_HTTP_5XX,
    REASON_CODE_NETWORK_TIMEOUT,
)
from arl.shared.ffmpeg_runner import (
    FfmpegAttemptOutcome,
    format_ffmpeg_failure_reason,
    rotate_stderr_logs,
    run_ffmpeg_attempt,
)


class RunFfmpegAttemptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.stderr_log_dir = Path(self.temp_dir.name) / "stderr"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_success_returns_success_outcome_with_none_fields(self) -> None:
        with patch("arl.shared.ffmpeg_runner.subprocess.run", return_value=None):
            outcome = run_ffmpeg_attempt(
                ["ffmpeg", "-i", "in.mp4", "out.mp4"],
                timeout=5,
                stderr_log_dir=self.stderr_log_dir,
                stderr_log_basename="job-success",
                attempt=1,
            )
        self.assertIsInstance(outcome, FfmpegAttemptOutcome)
        self.assertTrue(outcome.success)
        self.assertIsNone(outcome.reason)
        self.assertIsNone(outcome.classification)
        self.assertIsNone(outcome.stderr_excerpt)
        self.assertIsNone(outcome.stderr_log_path)
        # Nothing should be written on the happy path.
        self.assertFalse(self.stderr_log_dir.exists())

    def test_4xx_classified_non_retryable_with_excerpt_and_log(self) -> None:
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Server returned 404 Not Found"
        )
        with patch("arl.shared.ffmpeg_runner.subprocess.run", side_effect=error):
            outcome = run_ffmpeg_attempt(
                ["ffmpeg", "-i", "stream.m3u8", "out.mp4"],
                timeout=5,
                stderr_log_dir=self.stderr_log_dir,
                stderr_log_basename="job-4xx",
                attempt=1,
            )
        self.assertFalse(outcome.success)
        self.assertIsNotNone(outcome.classification)
        self.assertEqual(outcome.classification.reason_code, REASON_CODE_HTTP_4XX)
        self.assertEqual(
            outcome.classification.failure_category,
            FAILURE_CATEGORY_HTTP_4XX_NON_RETRYABLE,
        )
        self.assertFalse(outcome.classification.is_retryable)
        self.assertIsNotNone(outcome.stderr_excerpt)
        self.assertIn("404", outcome.stderr_excerpt)
        self.assertIsNotNone(outcome.stderr_log_path)
        log_path = Path(outcome.stderr_log_path)
        self.assertTrue(log_path.exists())
        self.assertEqual(log_path.read_text(encoding="utf-8"), "Server returned 404 Not Found")
        self.assertEqual(log_path.name, "job-4xx-1.log")

    def test_5xx_classified_retryable_with_excerpt_and_log(self) -> None:
        error = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Server returned 503 Service Unavailable"
        )
        with patch("arl.shared.ffmpeg_runner.subprocess.run", side_effect=error):
            outcome = run_ffmpeg_attempt(
                ["ffmpeg", "-i", "stream.m3u8", "out.mp4"],
                timeout=5,
                stderr_log_dir=self.stderr_log_dir,
                stderr_log_basename="job-5xx",
                attempt=2,
            )
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.classification.reason_code, REASON_CODE_HTTP_5XX)
        self.assertEqual(
            outcome.classification.failure_category,
            FAILURE_CATEGORY_HTTP_5XX_RETRYABLE,
        )
        self.assertTrue(outcome.classification.is_retryable)
        self.assertIsNotNone(outcome.stderr_log_path)
        self.assertTrue(outcome.stderr_log_path.endswith("job-5xx-2.log"))

    def test_timeout_returns_timeout_reason_no_stderr(self) -> None:
        error = subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=5.0)
        with patch("arl.shared.ffmpeg_runner.subprocess.run", side_effect=error):
            outcome = run_ffmpeg_attempt(
                ["ffmpeg", "-i", "stream.m3u8", "out.mp4"],
                timeout=5,
                stderr_log_dir=self.stderr_log_dir,
                stderr_log_basename="job-timeout",
                attempt=1,
            )
        self.assertFalse(outcome.success)
        self.assertTrue(outcome.reason.startswith("timed out after"))
        self.assertEqual(outcome.classification.reason_code, REASON_CODE_NETWORK_TIMEOUT)
        self.assertEqual(
            outcome.classification.failure_category,
            FAILURE_CATEGORY_NETWORK_TIMEOUT_RETRYABLE,
        )
        self.assertTrue(outcome.classification.is_retryable)
        # Timeout with no stderr: helper must NOT create a log file.
        self.assertIsNone(outcome.stderr_excerpt)
        self.assertIsNone(outcome.stderr_log_path)

    def test_no_stderr_failure_skips_log_write(self) -> None:
        error = subprocess.CalledProcessError(2, ["ffmpeg"], stderr=None)
        with patch("arl.shared.ffmpeg_runner.subprocess.run", side_effect=error):
            outcome = run_ffmpeg_attempt(
                ["ffmpeg", "-i", "stream.m3u8", "out.mp4"],
                timeout=5,
                stderr_log_dir=self.stderr_log_dir,
                stderr_log_basename="job-empty-stderr",
                attempt=1,
            )
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.reason, "exit_status:2")
        # exit_status:N falls into the ffmpeg_process_error bucket per classify_failure_reason.
        self.assertEqual(
            outcome.classification.reason_code, REASON_CODE_FFMPEG_PROCESS_ERROR
        )
        self.assertEqual(
            outcome.classification.failure_category,
            FAILURE_CATEGORY_FFMPEG_PROCESS_ERROR_RETRYABLE,
        )
        self.assertIsNone(outcome.stderr_excerpt)
        self.assertIsNone(outcome.stderr_log_path)
        # No log file written because stderr was empty.
        self.assertFalse(self.stderr_log_dir.exists())

    def test_oserror_failure_classified_unknown_unclassified(self) -> None:
        with patch(
            "arl.shared.ffmpeg_runner.subprocess.run",
            side_effect=OSError("missing binary"),
        ):
            outcome = run_ffmpeg_attempt(
                ["ffmpeg", "-i", "stream.m3u8", "out.mp4"],
                timeout=5,
                stderr_log_dir=self.stderr_log_dir,
                stderr_log_basename="job-oserror",
                attempt=1,
            )
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.reason, "os_error:OSError")
        # os_error reasons don't match any classifier marker => unknown_unclassified.
        self.assertIsNotNone(outcome.classification)
        self.assertIsNone(outcome.stderr_excerpt)
        self.assertIsNone(outcome.stderr_log_path)

    def test_excerpt_caps_long_stderr_at_4096_chars(self) -> None:
        # 40 lines × ~50 chars each — easily exceeds 4 KB if not truncated.
        long_stderr = "\n".join(
            [f"line-{idx:02d} long stderr content " + "x" * 30 for idx in range(40)]
        )
        error = subprocess.CalledProcessError(1, ["ffmpeg"], stderr=long_stderr)
        with patch("arl.shared.ffmpeg_runner.subprocess.run", side_effect=error):
            outcome = run_ffmpeg_attempt(
                ["ffmpeg"],
                timeout=5,
                stderr_log_dir=self.stderr_log_dir,
                stderr_log_basename="job-excerpt",
                attempt=1,
            )
        self.assertIsNotNone(outcome.stderr_excerpt)
        self.assertLessEqual(len(outcome.stderr_excerpt), 4096)
        # Head 5 + tail 15 contract: first and last lines must be present.
        self.assertIn("line-00", outcome.stderr_excerpt)
        self.assertIn("line-39", outcome.stderr_excerpt)
        # Full dump on disk must contain the entire stderr text untruncated.
        self.assertEqual(
            Path(outcome.stderr_log_path).read_text(encoding="utf-8"), long_stderr
        )


class RotateStderrLogsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.stderr_dir = Path(self.temp_dir.name) / "stderr"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_keeps_newest_n_by_mtime(self) -> None:
        import os as _os

        self.stderr_dir.mkdir(parents=True, exist_ok=True)
        retain = 4
        seeded = retain + 3
        for idx in range(seeded):
            entry = self.stderr_dir / f"old-{idx:02d}.log"
            entry.write_text(f"file-{idx}", encoding="utf-8")
            mtime = 1_700_000_000 + idx
            _os.utime(entry, (mtime, mtime))

        rotate_stderr_logs(self.stderr_dir, retain)

        remaining = sorted(self.stderr_dir.iterdir(), key=lambda p: p.name)
        self.assertEqual(len(remaining), retain)
        expected = {f"old-{idx:02d}.log" for idx in range(seeded - retain, seeded)}
        self.assertEqual({entry.name for entry in remaining}, expected)

    def test_retain_zero_or_negative_wipes_directory(self) -> None:
        self.stderr_dir.mkdir(parents=True, exist_ok=True)
        for idx in range(3):
            (self.stderr_dir / f"f-{idx}.log").write_text(".", encoding="utf-8")

        rotate_stderr_logs(self.stderr_dir, 0)

        self.assertEqual(list(self.stderr_dir.iterdir()), [])

    def test_missing_dir_is_silent(self) -> None:
        # Must not raise on a nonexistent stderr_dir.
        rotate_stderr_logs(self.stderr_dir, 5)


class FormatFfmpegFailureReasonTest(unittest.TestCase):
    def test_called_process_error_with_stderr_takes_last_line_truncated(self) -> None:
        long_last_line = "x" * 500
        stderr = f"early\nmiddle\n{long_last_line}"
        error = subprocess.CalledProcessError(1, ["ffmpeg"], stderr=stderr)
        reason = format_ffmpeg_failure_reason(error)
        self.assertEqual(len(reason), 240)
        self.assertTrue(reason.startswith("x"))

    def test_called_process_error_without_stderr_uses_exit_status(self) -> None:
        error = subprocess.CalledProcessError(7, ["ffmpeg"], stderr=None)
        self.assertEqual(format_ffmpeg_failure_reason(error), "exit_status:7")

    def test_timeout_uses_timeout_value(self) -> None:
        error = subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=12.5)
        self.assertEqual(format_ffmpeg_failure_reason(error), "timed out after 12.5s")

    def test_os_error_uses_class_name(self) -> None:
        self.assertEqual(format_ffmpeg_failure_reason(OSError()), "os_error:OSError")


if __name__ == "__main__":
    unittest.main()
