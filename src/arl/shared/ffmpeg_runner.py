from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from arl.shared.failure_contracts import FailureDecision, classify_failure_reason


@dataclass(frozen=True)
class FfmpegAttemptOutcome:
    """Result of one ffmpeg invocation.

    On success ``success=True`` and every other field is ``None`` — callers
    treat this as the "happy path" and decide what audit row to emit. On
    failure, ``reason`` is the one-line failure summary (same shape as
    recorder's pre-extraction ``_format_ffmpeg_failure_reason`` output),
    ``classification`` is the canonical decision tuple (callers decide their
    own ``decision`` string from this — recorder yields on transient, exporter
    does not), and ``stderr_excerpt`` / ``stderr_log_path`` are populated only
    when ffmpeg produced non-empty stderr.
    """

    success: bool
    reason: str | None
    classification: FailureDecision | None
    stderr_excerpt: str | None
    stderr_log_path: str | None


def run_ffmpeg_attempt(
    command: list[str],
    *,
    timeout: float,
    stderr_log_dir: Path,
    stderr_log_basename: str,
    attempt: int,
) -> FfmpegAttemptOutcome:
    """Run one ffmpeg invocation; on failure capture + classify + dump stderr.

    The helper owns the full per-attempt mechanics that 05-10 recorder
    hardening codified: ``check=True, capture_output=True, text=True``,
    excerpt = head 5 + tail 15 lines (each <=240 chars, total <=4 KB), full
    stderr dumped atomically to
    ``<stderr_log_dir>/<safe_basename>-<attempt>.log``. The caller keeps
    ownership of the retry loop and audit emission.
    """

    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as error:
        reason = format_ffmpeg_failure_reason(error)
        classification = classify_failure_reason(reason)
        stderr_text = _extract_full_stderr(error)
        if not stderr_text:
            return FfmpegAttemptOutcome(
                success=False,
                reason=reason,
                classification=classification,
                stderr_excerpt=None,
                stderr_log_path=None,
            )
        excerpt = _build_stderr_excerpt(stderr_text)
        log_path = _write_stderr_log(
            stderr_text,
            stderr_log_dir=stderr_log_dir,
            stderr_log_basename=stderr_log_basename,
            attempt=attempt,
        )
        return FfmpegAttemptOutcome(
            success=False,
            reason=reason,
            classification=classification,
            stderr_excerpt=excerpt,
            stderr_log_path=log_path,
        )
    return FfmpegAttemptOutcome(
        success=True,
        reason=None,
        classification=None,
        stderr_excerpt=None,
        stderr_log_path=None,
    )


def rotate_stderr_logs(stderr_dir: Path, retain_count: int) -> None:
    """Keep the newest ``retain_count`` files in ``stderr_dir`` by mtime.

    ``retain_count <= 0`` wipes the directory. Silent on OSError so a
    permissions blip during startup never crashes the service loop.
    """

    if not stderr_dir.exists():
        return
    try:
        files = [entry for entry in stderr_dir.iterdir() if entry.is_file()]
    except OSError:
        return
    if retain_count <= 0:
        for entry in files:
            try:
                entry.unlink()
            except OSError:
                continue
        return
    files.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    for entry in files[retain_count:]:
        try:
            entry.unlink()
        except OSError:
            continue


def format_ffmpeg_failure_reason(error: Exception) -> str:
    """Reduce a subprocess error into a one-line failure reason.

    Public because in-recorder ffmpeg probes (e.g. X11 display readiness)
    need the same string shape as the recording path but don't want the
    full attempt machinery (capture, classify, dump).
    """

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


def _extract_full_stderr(error: Exception) -> str:
    raw: object = None
    if isinstance(error, subprocess.CalledProcessError):
        raw = error.stderr
    elif isinstance(error, subprocess.TimeoutExpired):
        raw = getattr(error, "stderr", None)
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace").strip()
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _build_stderr_excerpt(stderr_text: str) -> str:
    max_line_chars = 240
    head_lines = 5
    tail_lines = 15
    total_budget = 4096
    lines = stderr_text.splitlines()
    if not lines:
        return ""
    if len(lines) <= head_lines + tail_lines:
        picked = lines
    else:
        picked = lines[:head_lines] + ["..."] + lines[-tail_lines:]
    truncated = [line[:max_line_chars] for line in picked]
    excerpt = "\n".join(truncated)
    if len(excerpt) > total_budget:
        excerpt = excerpt[:total_budget]
    return excerpt


def _write_stderr_log(
    stderr_text: str,
    *,
    stderr_log_dir: Path,
    stderr_log_basename: str,
    attempt: int,
) -> str | None:
    try:
        stderr_log_dir.mkdir(parents=True, exist_ok=True)
        safe_basename = stderr_log_basename.replace(os.sep, "_").replace("/", "_")
        log_path = stderr_log_dir / f"{safe_basename}-{attempt}.log"
        tmp_path = log_path.with_suffix(log_path.suffix + ".tmp")
        tmp_path.write_text(stderr_text, encoding="utf-8")
        tmp_path.replace(log_path)
    except OSError:
        return None
    return log_path.as_posix()
