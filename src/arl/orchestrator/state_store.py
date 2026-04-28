from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from arl.orchestrator.models import OrchestratorAuditEvent, OrchestratorStateFile


class OrchestratorStateStore:
    def __init__(self, state_path: Path, audit_log_path: Path) -> None:
        self.state_path = state_path
        self.audit_log_path = audit_log_path

    def load(self) -> OrchestratorStateFile:
        if not self.state_path.exists():
            return OrchestratorStateFile()
        return OrchestratorStateFile.model_validate_json(self.state_path.read_text())

    def save(self, state: OrchestratorStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n")

    def append_audit(
        self,
        event_type: str,
        *,
        session_id: str | None = None,
        job_id: str | None = None,
        message: str | None = None,
    ) -> None:
        event = OrchestratorAuditEvent(
            event_type=event_type,
            session_id=session_id,
            job_id=job_id,
            message=message,
            created_at=datetime.now(timezone.utc),
        )
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False))
            handle.write("\n")
