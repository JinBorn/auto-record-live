from __future__ import annotations

import time

from arl.config import Settings
from arl.shared.contracts import LiveState
from arl.shared.logging import log
from arl.windows_agent.models import AgentEvent, AgentStateFile, AgentSnapshot
from arl.windows_agent.probe import DouyinRoomProbe
from arl.windows_agent.state_store import WindowsAgentStateStore


class WindowsAgentService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.probe = DouyinRoomProbe(settings)
        self.state_store = WindowsAgentStateStore(
            settings.douyin.state_file,
            settings.douyin.event_log_path,
        )

    def run(self, once: bool = False) -> None:
        log("windows-agent", "starting")
        log("windows-agent", f"room_url={self.settings.douyin.room_url or '<unset>'}")
        log(
            "windows-agent",
            f"event_log={self.settings.douyin.event_log_path}",
        )

        if once:
            self.run_once()
            return

        interval = self.settings.douyin.poll_interval_seconds
        while True:
            self.run_once()
            time.sleep(interval)

    def run_once(self) -> None:
        previous_state = self.state_store.load()
        snapshot = self.probe.detect()
        self._log_snapshot(snapshot)

        if not self._has_changed(previous_state.last_snapshot, snapshot):
            return

        event = AgentEvent(
            event_type=self._event_name(snapshot),
            snapshot=snapshot,
        )
        self.state_store.append_event(event)
        self.state_store.save(AgentStateFile(last_snapshot=snapshot))
        log("windows-agent", f"emitted event={event.event_type}")

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
            f"state={snapshot.state.value} "
            f"source_type={(snapshot.source_type.value if snapshot.source_type else 'none')} "
            f"reason={snapshot.reason or 'n/a'}",
        )
