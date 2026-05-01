from __future__ import annotations

import json
from pathlib import Path

from arl.windows_agent.models import AgentEvent, AgentStateFile


class WindowsAgentStateStore:
    def __init__(self, state_path: Path, event_log_path: Path) -> None:
        self.state_path = state_path
        self.event_log_path = event_log_path

    def load(self) -> AgentStateFile:
        if not self.state_path.exists():
            return AgentStateFile()
        raw = self.state_path.read_text(encoding="utf-8")
        if not raw.strip():
            return AgentStateFile()
        return AgentStateFile.model_validate_json(raw)

    def save(self, state: AgentStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def append_event(self, event: AgentEvent) -> None:
        self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False))
            handle.write("\n")
