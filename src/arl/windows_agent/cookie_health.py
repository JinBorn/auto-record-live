from __future__ import annotations

from dataclasses import dataclass

from arl.windows_agent.platform_probe import CookieState, PlatformProbe


@dataclass(frozen=True)
class CookieHealthRow:
    """One row of the `arl cookie-health` report."""

    platform: str
    status: str  # "fresh" | "expired" | "not_configured" | "error"
    detail: str


@dataclass(frozen=True)
class CookieHealthReport:
    rows: list[CookieHealthRow]
    exit_code: int  # 0 = all rows OK or not_configured; 1 = at least one expired


def run_cookie_health(probes: list[PlatformProbe]) -> CookieHealthReport:
    """Run one detection cycle per probe and classify cookie state.

    Probe errors are surfaced as `status="error"` with the exception class in
    `detail`, so a single broken probe does not abort the report. Exit code
    is non-zero only on at least one `expired` status — operators care about
    the actionable case, not transient probe failures.
    """

    rows: list[CookieHealthRow] = []
    has_expired = False
    for probe in probes:
        try:
            snapshot = probe.detect()
        except Exception as exc:  # noqa: BLE001 — probe-isolation pattern
            rows.append(
                CookieHealthRow(
                    platform=probe.platform_name,
                    status="error",
                    detail=f"probe_error:{exc.__class__.__name__}",
                )
            )
            continue
        state = probe.classify_cookie_state(snapshot)
        rows.append(
            CookieHealthRow(
                platform=probe.platform_name,
                status=state.value,
                detail=snapshot.reason or "n/a",
            )
        )
        if state == CookieState.EXPIRED:
            has_expired = True
    return CookieHealthReport(rows=rows, exit_code=1 if has_expired else 0)
