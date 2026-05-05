from __future__ import annotations

import time

from arl.config import Settings
from arl.shared.contracts import LiveState
from arl.shared.logging import log
from arl.windows_agent.models import AgentEvent, AgentSnapshot, AgentStateFile
from arl.windows_agent.platform_probe import PlatformProbe
from arl.windows_agent.registry import build_probes
from arl.windows_agent.state_store import WindowsAgentStateStore


class WindowsAgentService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.probes: list[PlatformProbe] = build_probes(settings.platforms)
        self.state_store = WindowsAgentStateStore(
            settings.windows_agent.state_file,
            settings.windows_agent.event_log_path,
        )

    def run(self, once: bool = False) -> None:
        log("windows-agent", "starting")
        log(
            "windows-agent",
            f"platforms={[p.platform_name for p in self.probes]}",
        )
        log(
            "windows-agent",
            f"event_log={self.settings.windows_agent.event_log_path}",
        )

        if once:
            self.run_once()
            return

        interval = self.settings.windows_agent.poll_interval_seconds
        while True:
            self.run_once()
            time.sleep(interval)

    def run_once(self) -> None:
        previous_state = self.state_store.load()
        new_snapshots: list[AgentSnapshot] = []
        for probe in self.probes:
            try:
                snapshot = probe.detect()
            except Exception as exc:
                # Per-platform isolation: one probe crashing must not block the
                # other probes' detection cycle.
                log(
                    "windows-agent",
                    f"probe platform={probe.platform_name} crashed reason={exc.__class__.__name__}",
                )
                continue

            self._log_snapshot(snapshot)
            new_snapshots.append(snapshot)

            previous_snapshot = previous_state.get(snapshot.platform, snapshot.room_url)
            if not self._has_changed(previous_snapshot, snapshot):
                continue

            event = AgentEvent(
                event_type=self._event_name(snapshot),
                snapshot=snapshot,
            )
            self.state_store.append_event(event)
            log(
                "windows-agent",
                f"emitted event={event.event_type} platform={snapshot.platform}",
            )

        if new_snapshots:
            for snapshot in new_snapshots:
                previous_state.set(snapshot)
            self.state_store.save(previous_state)

    def _has_changed(
        self,
        previous: AgentSnapshot | None,
        current: AgentSnapshot,
    ) -> bool:
        if previous is None:
            return True

        return any(
            [
                previous.state != current.state,
                previous.source_type != current.source_type,
                previous.stream_url != current.stream_url,
                previous.reason != current.reason,
            ]
        )

    def _event_name(self, snapshot: AgentSnapshot) -> str:
        if snapshot.state == LiveState.LIVE:
            return "live_started"
        return "live_stopped"

    def _log_snapshot(self, snapshot: AgentSnapshot) -> None:
        log(
            "windows-agent",
            "detected "
            f"platform={snapshot.platform} "
            f"state={snapshot.state.value} "
            f"source_type={(snapshot.source_type.value if snapshot.source_type else 'none')} "
            f"reason={snapshot.reason or 'n/a'}",
        )
