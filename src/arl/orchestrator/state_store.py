from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from arl.orchestrator.models import OrchestratorAuditEvent, OrchestratorStateFile


# Codecs attempted by ``load_orchestrator_state`` in priority order.
# UTF-8 is the only encoding produced by ``OrchestratorStateStore.save``.
# ``gbk`` is retained as a one-time auto-heal for legacy files written before
# this store enforced an explicit encoding (on Windows zh-CN, bare
# ``Path.write_text`` falls back to the platform locale, which is CP936/GBK).
# The next ``save`` rewrites the file as UTF-8, so the fallback is exercised
# at most once per legacy file.
_STATE_LOAD_DECODERS: tuple[str, ...] = ("utf-8", "gbk")


def load_orchestrator_state(state_path: Path) -> OrchestratorStateFile:
    """Load orchestrator state with explicit UTF-8 decoding and legacy fallback.

    Any consumer (orchestrator owner, recorder reader) must use this helper
    instead of ``Path.read_text`` so encoding behavior stays in one place.
    """
    if not state_path.exists():
        return OrchestratorStateFile()
    raw = _read_state_text(state_path)
    if not raw.strip():
        return OrchestratorStateFile()
    return OrchestratorStateFile.model_validate_json(raw)


def _read_state_text(state_path: Path) -> str:
    data = state_path.read_bytes()
    last_error: UnicodeDecodeError | None = None
    for encoding in _STATE_LOAD_DECODERS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError as error:
            last_error = error
    raise RuntimeError(
        f"Cannot decode orchestrator state file {state_path} as any of "
        f"{list(_STATE_LOAD_DECODERS)}. Delete the file to rebuild from event "
        f"logs or convert it manually."
    ) from last_error


class OrchestratorStateStore:
    def __init__(self, state_path: Path, audit_log_path: Path) -> None:
        self.state_path = state_path
        self.audit_log_path = audit_log_path

    def load(self) -> OrchestratorStateFile:
        return load_orchestrator_state(self.state_path)

    def save(self, state: OrchestratorStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

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
