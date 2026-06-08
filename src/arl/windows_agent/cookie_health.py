from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from arl.config import PlatformSettings
from arl.shared.contracts import LiveState
from arl.windows_agent.registry import build_probe
from arl.windows_agent.state_store import WindowsAgentStateStore
from arl.windows_agent.platform_probe import CookieState, PlatformProbe


LiveRoomKey = tuple[str, str]


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


def build_cookie_health_probes(
    platforms: list[PlatformSettings],
    *,
    live_room_keys: set[LiveRoomKey] | None = None,
) -> list[PlatformProbe]:
    """Build one representative probe per platform credential.

    Cookie health is credential-scoped, not room-scoped. When the operator
    monitors several rooms with the same platform cookie, checking the first
    configured room is enough to classify that credential and avoids repeated
    API probes against every room. If a room in the credential group is known
    to be live, prefer that room because live pages expose the strongest cookie
    validation signal.
    """

    selected: dict[tuple[str, str, str], PlatformSettings] = {}
    live_room_keys = live_room_keys or set()
    for platform in platforms:
        key = _cookie_health_key(platform)
        current = selected.get(key)
        if current is None or (
            not _is_live_room(current, live_room_keys)
            and _is_live_room(platform, live_room_keys)
        ):
            selected[key] = platform
    return [build_probe(platform) for platform in selected.values()]


def load_cookie_health_live_room_keys(state_path: Path) -> set[LiveRoomKey]:
    """Return rooms whose latest windows-agent snapshot is live.

    This is a best-effort selector hint. Cookie-health must still run if the
    state file is absent or unreadable, so malformed state falls back to the
    room-order representative selection.
    """

    try:
        state = WindowsAgentStateStore(state_path, event_log_path=state_path).load()
    except Exception:  # noqa: BLE001 - best-effort stale-state hint
        return set()
    return {
        (snapshot.platform, snapshot.room_url)
        for snapshot in state.last_snapshots.values()
        if snapshot.state == LiveState.LIVE
    }


def _is_live_room(
    platform: PlatformSettings,
    live_room_keys: set[LiveRoomKey],
) -> bool:
    return (platform.type, platform.room_url) in live_room_keys


def _cookie_health_key(platform: PlatformSettings) -> tuple[str, str, str]:
    for credential_field in ("sessdata", "cookie"):
        credential = getattr(platform, credential_field, None)
        if isinstance(credential, str):
            return (platform.type, credential_field, credential)
    return (platform.type, "none", "")


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
