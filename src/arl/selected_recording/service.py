from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from pathlib import Path
from uuid import uuid4

from arl.config import PlatformSettings, Settings
from arl.orchestrator.models import OrchestratorStateFile, RecordingJobStatus
from arl.orchestrator.state_store import load_orchestrator_state
from arl.orchestrator.service import OrchestratorService
from arl.recorder.service import RecorderService
from arl.windows_agent.live_status import run_live_status
from arl.windows_agent.registry import build_probes
from arl.windows_agent.service import WindowsAgentService


@dataclass(frozen=True)
class SelectedRoom:
    index: int
    platform: str
    streamer_name: str
    room_url: str

    @classmethod
    def from_platform(cls, index: int, platform: PlatformSettings) -> "SelectedRoom":
        return cls(
            index=index,
            platform=platform.type,
            streamer_name=platform.streamer_name,
            room_url=platform.room_url,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "platform": self.platform,
            "streamer_name": self.streamer_name,
            "room_url": self.room_url,
        }


@dataclass(frozen=True)
class SelectedRecordingResult:
    selected_rooms: list[SelectedRoom]
    state_dir: Path | None
    state_dirs: list[Path] | None = None
    sessions: int = 0
    recording_jobs_by_status: dict[str, int] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "selected_rooms": [room.as_dict() for room in self.selected_rooms],
            "state_dir": str(self.state_dir) if self.state_dir is not None else None,
            "state_dirs": [str(path) for path in self.state_dirs or []],
            "sessions": self.sessions,
            "recording_jobs_by_status": self.recording_jobs_by_status or {},
        }


class SelectedRecordingService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(
        self,
        *,
        room_indices: list[int] | None = None,
        all_live: bool = False,
        force_ffmpeg: bool = True,
        max_concurrent_jobs: int | None = None,
    ) -> SelectedRecordingResult:
        selected_indices = self._resolve_selected_indices(
            room_indices=room_indices,
            all_live=all_live,
        )
        if not selected_indices:
            return SelectedRecordingResult(selected_rooms=[], state_dir=None)

        selected_rooms = [
            SelectedRoom.from_platform(index, self.settings.platforms[index - 1])
            for index in selected_indices
        ]
        selected_platforms = [
            self.settings.platforms[index - 1] for index in selected_indices
        ]
        state_dirs: list[Path] = []
        total_sessions = 0
        status_counts: Counter[str] = Counter()

        while True:
            selected_settings, state_dir = self._build_selected_settings(
                selected_platforms=selected_platforms,
                force_ffmpeg=force_ffmpeg,
                max_concurrent_jobs=max_concurrent_jobs,
            )
            state_dirs.append(state_dir)

            state = self._run_recording_cycle(selected_settings)
            total_sessions += len(state.sessions)
            status_counts.update(job.status.value for job in state.recording_jobs)

            if not self._should_continue_after_cycle(state):
                break

            time.sleep(self.settings.windows_agent.poll_interval_seconds)

        return SelectedRecordingResult(
            selected_rooms=selected_rooms,
            state_dir=state_dirs[0] if state_dirs else None,
            state_dirs=state_dirs,
            sessions=total_sessions,
            recording_jobs_by_status=dict(status_counts),
        )

    def _run_recording_cycle(self, selected_settings: Settings) -> OrchestratorStateFile:
        WindowsAgentService(selected_settings).run(once=True)
        OrchestratorService(selected_settings).run(once=True)
        try:
            RecorderService(selected_settings).run()
        finally:
            OrchestratorService(selected_settings).run(once=True)
        return load_orchestrator_state(selected_settings.orchestrator.state_file)

    @staticmethod
    def _should_continue_after_cycle(state: OrchestratorStateFile) -> bool:
        live_sessions = [
            session
            for session in state.sessions
            if session.status.value == "live" and session.ended_at is None
        ]
        if not live_sessions or not state.recording_jobs:
            return False

        if any(job.status == RecordingJobStatus.FAILED for job in state.recording_jobs):
            return False

        active_jobs = [
            job
            for job in state.recording_jobs
            if job.status not in {
                RecordingJobStatus.STOPPED,
                RecordingJobStatus.FAILED,
            }
        ]
        if active_jobs:
            return False

        return any(job.status == RecordingJobStatus.STOPPED for job in state.recording_jobs)

    def _resolve_selected_indices(
        self,
        *,
        room_indices: list[int] | None,
        all_live: bool,
    ) -> list[int]:
        room_count = len(self.settings.platforms)
        if room_count == 0:
            raise ValueError("no configured rooms found")

        if all_live:
            report = run_live_status(build_probes(self.settings.platforms))
            return [row.index for row in report.rows if row.state == "live"]

        if not room_indices:
            raise ValueError("room indices are required unless --all-live is used")

        selected: list[int] = []
        seen: set[int] = set()
        for index in room_indices:
            if index < 1 or index > room_count:
                raise ValueError(
                    f"room index {index} out of range; valid range is 1..{room_count}"
                )
            if index in seen:
                continue
            seen.add(index)
            selected.append(index)
        return selected

    def _build_selected_settings(
        self,
        *,
        selected_platforms: list[PlatformSettings],
        force_ffmpeg: bool,
        max_concurrent_jobs: int | None,
    ) -> tuple[Settings, Path]:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_id = f"{run_id}-{uuid4().hex[:8]}"
        state_dir = self.settings.storage.temp_dir / "selected-recordings" / run_id
        agent_event_log = state_dir / "windows-agent-events.jsonl"
        recorder_event_log = state_dir / "recorder-events.jsonl"

        recording_updates: dict[str, object] = {}
        if force_ffmpeg:
            recording_updates["enable_ffmpeg"] = True
        if max_concurrent_jobs is not None:
            recording_updates["max_concurrent_jobs"] = max_concurrent_jobs

        return (
            self.settings.model_copy(
                deep=True,
                update={
                    "platforms": selected_platforms,
                    "windows_agent": self.settings.windows_agent.model_copy(
                        update={
                            "state_file": state_dir / "windows-agent-state.json",
                            "event_log_path": agent_event_log,
                        }
                    ),
                    "orchestrator": self.settings.orchestrator.model_copy(
                        update={
                            "agent_event_log_path": agent_event_log,
                            "recorder_event_log_path": recorder_event_log,
                            "state_file": state_dir / "orchestrator-state.json",
                            "audit_log_path": state_dir / "orchestrator-events.jsonl",
                        }
                    ),
                    "recording": self.settings.recording.model_copy(
                        update=recording_updates
                    ),
                },
            ),
            state_dir,
        )
