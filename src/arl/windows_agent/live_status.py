from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from arl.windows_agent.models import AgentSnapshot
from arl.windows_agent.platform_probe import PlatformProbe


@dataclass(frozen=True)
class LiveStatusRow:
    platform: str
    state: str
    streamer_name: str
    room_url: str
    source_type: str | None
    stream_url: str | None
    reason: str
    detected_at: str | None

    @classmethod
    def from_snapshot(cls, snapshot: AgentSnapshot) -> "LiveStatusRow":
        return cls(
            platform=snapshot.platform,
            state=snapshot.state.value,
            streamer_name=snapshot.streamer_name,
            room_url=snapshot.room_url,
            source_type=snapshot.source_type.value if snapshot.source_type else None,
            stream_url=snapshot.stream_url,
            reason=snapshot.reason or "n/a",
            detected_at=snapshot.detected_at.isoformat(),
        )

    @classmethod
    def from_probe_error(cls, probe: PlatformProbe, exc: Exception) -> "LiveStatusRow":
        settings = getattr(probe, "settings", None)
        room_url = str(getattr(settings, "room_url", "") or "")
        streamer_name = str(getattr(settings, "streamer_name", "") or "")
        return cls(
            platform=probe.platform_name,
            state="error",
            streamer_name=streamer_name,
            room_url=room_url,
            source_type=None,
            stream_url=None,
            reason=f"probe_error:{exc.__class__.__name__}",
            detected_at=None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "state": self.state,
            "streamer_name": self.streamer_name,
            "room_url": self.room_url,
            "source_type": self.source_type,
            "stream_url": self.stream_url,
            "reason": self.reason,
            "detected_at": self.detected_at,
        }


@dataclass(frozen=True)
class LiveStatusReport:
    rows: list[LiveStatusRow]
    generated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "generated_at": self.generated_at,
                "total": len(self.rows),
                "live": sum(1 for row in self.rows if row.state == "live"),
                "offline": sum(1 for row in self.rows if row.state == "offline"),
                "error": sum(1 for row in self.rows if row.state == "error"),
            },
            "rooms": [row.as_dict() for row in self.rows],
        }


def run_live_status(probes: list[PlatformProbe]) -> LiveStatusReport:
    rows: list[LiveStatusRow] = []
    for probe in probes:
        try:
            snapshot = probe.detect()
        except Exception as exc:  # noqa: BLE001 - per-room probe isolation
            rows.append(LiveStatusRow.from_probe_error(probe, exc))
            continue
        rows.append(LiveStatusRow.from_snapshot(snapshot))
    return LiveStatusReport(
        rows=rows,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
