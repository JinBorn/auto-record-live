from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from arl.orchestrator.models import RecorderAuditEventPayload


@dataclass
class RecorderEventReadResult:
    events: list[RecorderAuditEventPayload]
    next_offset: int
    invalid_lines: int
    reset_cursor: bool


class RecorderEventReader:
    def __init__(self, event_log_path: Path) -> None:
        self.event_log_path = event_log_path

    def read_from(self, offset: int) -> RecorderEventReadResult:
        if not self.event_log_path.exists():
            return RecorderEventReadResult(
                events=[],
                next_offset=0,
                invalid_lines=0,
                reset_cursor=False,
            )

        file_size = self.event_log_path.stat().st_size
        reset_cursor = offset > file_size
        start_offset = 0 if reset_cursor else offset

        events: list[RecorderAuditEventPayload] = []
        invalid_lines = 0
        next_offset = start_offset

        with self.event_log_path.open("r", encoding="utf-8") as handle:
            handle.seek(start_offset)
            while True:
                line = handle.readline()
                if line == "":
                    break
                next_offset = handle.tell()
                payload = line.strip()
                if not payload:
                    continue
                try:
                    raw = json.loads(payload)
                    events.append(RecorderAuditEventPayload.model_validate(raw))
                except (json.JSONDecodeError, ValidationError):
                    invalid_lines += 1

        return RecorderEventReadResult(
            events=events,
            next_offset=next_offset,
            invalid_lines=invalid_lines,
            reset_cursor=reset_cursor,
        )
