from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from arl.config import Settings
from arl.orchestrator.state_store import OrchestratorStateStore, load_orchestrator_state
from arl.shared.logging import log


@dataclass(frozen=True)
class MaintenanceResult:
    consumed_logs_archived: dict[str, int]
    audit_logs_archived: dict[str, int]
    launcher_logs_removed: int

    def as_dict(self) -> dict[str, object]:
        return {
            "consumed_logs_archived": self.consumed_logs_archived,
            "audit_logs_archived": self.audit_logs_archived,
            "launcher_logs_removed": self.launcher_logs_removed,
        }


class MaintenanceService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.temp_dir = settings.storage.temp_dir
        self.archive_dir = settings.maintenance.archive_dir

    def run_once(self) -> MaintenanceResult:
        log("maintenance", "starting")
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        consumed = self._archive_consumed_orchestrator_inputs()
        audit = self._archive_large_audit_logs()
        removed = self._rotate_launcher_logs()
        result = MaintenanceResult(
            consumed_logs_archived=consumed,
            audit_logs_archived=audit,
            launcher_logs_removed=removed,
        )
        log(
            "maintenance",
            (
                "completed "
                f"consumed_logs={sum(consumed.values())} "
                f"audit_logs={sum(audit.values())} "
                f"launcher_logs_removed={removed}"
            ),
        )
        return result

    def _archive_consumed_orchestrator_inputs(self) -> dict[str, int]:
        state_path = self.settings.orchestrator.state_file
        state = load_orchestrator_state(state_path)
        archived: dict[str, int] = {}

        agent_bytes = self._archive_consumed_prefix(
            self.settings.orchestrator.agent_event_log_path,
            state.cursor_offset,
        )
        if agent_bytes > 0:
            archived["windows-agent-events.jsonl"] = agent_bytes
            state.cursor_offset = 0

        recorder_bytes = self._archive_consumed_prefix(
            self.settings.orchestrator.recorder_event_log_path,
            state.recorder_cursor_offset,
        )
        if recorder_bytes > 0:
            archived["recorder-events.jsonl"] = recorder_bytes
            state.recorder_cursor_offset = 0

        if archived:
            OrchestratorStateStore(
                state_path,
                self.settings.orchestrator.audit_log_path,
            ).save(state)
        return archived

    def _archive_consumed_prefix(self, path: Path, cursor_offset: int) -> int:
        if cursor_offset <= 0 or not path.exists():
            return 0
        size = path.stat().st_size
        if size <= self.settings.maintenance.max_jsonl_bytes:
            return 0
        archive_offset = min(cursor_offset, size)
        data = path.read_bytes()
        archived = data[:archive_offset]
        if not archived.strip():
            return 0
        self._write_archive(path, archived)
        path.write_bytes(data[archive_offset:])
        return len(archived)

    def _archive_large_audit_logs(self) -> dict[str, int]:
        archived: dict[str, int] = {}
        for path in self._audit_log_paths():
            archived_bytes = self._archive_old_jsonl_lines(path)
            if archived_bytes > 0:
                archived[path.name] = archived_bytes
        return archived

    def _audit_log_paths(self) -> list[Path]:
        return [
            self.settings.orchestrator.audit_log_path,
            self.temp_dir / "subtitles-events.jsonl",
            self.temp_dir / "exporter-events.jsonl",
            self.temp_dir / "recovery-events.jsonl",
        ]

    def _archive_old_jsonl_lines(self, path: Path) -> int:
        if not path.exists():
            return 0
        if path.stat().st_size <= self.settings.maintenance.max_jsonl_bytes:
            return 0
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        keep = self.settings.maintenance.keep_recent_lines
        if len(lines) <= keep:
            return 0
        archived_text = "".join(lines[:-keep])
        kept_text = "".join(lines[-keep:])
        if not archived_text.strip():
            return 0
        archived = archived_text.encode("utf-8")
        self._write_archive(path, archived)
        path.write_text(kept_text, encoding="utf-8")
        return len(archived)

    def _rotate_launcher_logs(self) -> int:
        retain = self.settings.maintenance.launcher_log_retain_count
        log_dir = self.temp_dir / "launcher-logs"
        if not log_dir.exists():
            return 0
        logs = [path for path in log_dir.glob("*.log") if path.is_file()]
        logs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        removed = 0
        for path in logs[retain:]:
            path.unlink()
            removed += 1
        return removed

    def _write_archive(self, source_path: Path, data: bytes) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        archive_name = f"{source_path.name}.{timestamp}.archive"
        archive_path = self.archive_dir / archive_name
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_bytes(data)
