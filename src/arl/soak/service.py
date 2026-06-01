from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable

from arl.config import Settings
from arl.maintenance.service import MaintenanceService
from arl.orchestrator.service import OrchestratorService
from arl.postprocess.service import PostProcessService
from arl.recorder.service import RecorderService
from arl.shared.logging import log
from arl.status.service import StatusService
from arl.windows_agent.service import WindowsAgentService


@dataclass(frozen=True)
class SoakStageResult:
    stage: str
    ok: bool
    elapsed_seconds: float
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "ok": self.ok,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "error": self.error,
        }


@dataclass(frozen=True)
class SoakCycleResult:
    cycle: int
    started_at: str
    elapsed_seconds: float
    stages: list[SoakStageResult] = field(default_factory=list)
    health: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "cycle": self.cycle,
            "started_at": self.started_at,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "health": self.health,
            "stages": [stage.as_dict() for stage in self.stages],
        }


@dataclass(frozen=True)
class SoakReport:
    cycles: int
    interval_seconds: float
    started_at: str
    completed_at: str
    elapsed_seconds: float
    health: str
    failed_stages: int
    cycle_results: list[SoakCycleResult]

    def as_dict(self) -> dict[str, object]:
        return {
            "summary": {
                "health": self.health,
                "cycles": self.cycles,
                "interval_seconds": self.interval_seconds,
                "failed_stages": self.failed_stages,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "elapsed_seconds": round(self.elapsed_seconds, 3),
            },
            "cycles": [cycle.as_dict() for cycle in self.cycle_results],
        }


class SoakService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(
        self,
        *,
        cycles: int = 3,
        interval_seconds: float = 30.0,
        run_recorder: bool = True,
        run_postprocess: bool = True,
        run_maintenance: bool = False,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> SoakReport:
        cycles = max(1, cycles)
        interval_seconds = max(0.0, interval_seconds)
        started_at = self._now()
        start = perf_counter()
        cycle_results: list[SoakCycleResult] = []
        log("soak", f"starting cycles={cycles} interval_seconds={interval_seconds}")

        for cycle in range(1, cycles + 1):
            cycle_results.append(
                self._run_cycle(
                    cycle=cycle,
                    run_recorder=run_recorder,
                    run_postprocess=run_postprocess,
                    run_maintenance=run_maintenance,
                )
            )
            if cycle < cycles and interval_seconds > 0:
                sleeper(interval_seconds)

        failed_stages = sum(
            1
            for cycle in cycle_results
            for stage in cycle.stages
            if not stage.ok
        )
        final_health = self._derive_health(cycle_results, failed_stages)
        completed_at = self._now()
        elapsed = perf_counter() - start
        log(
            "soak",
            (
                "completed "
                f"cycles={cycles} health={final_health} failed_stages={failed_stages}"
            ),
        )
        return SoakReport(
            cycles=cycles,
            interval_seconds=interval_seconds,
            started_at=started_at,
            completed_at=completed_at,
            elapsed_seconds=elapsed,
            health=final_health,
            failed_stages=failed_stages,
            cycle_results=cycle_results,
        )

    def _run_cycle(
        self,
        *,
        cycle: int,
        run_recorder: bool,
        run_postprocess: bool,
        run_maintenance: bool,
    ) -> SoakCycleResult:
        log("soak", f"cycle={cycle} starting")
        started_at = self._now()
        cycle_start = perf_counter()
        stages: list[SoakStageResult] = []

        stages.append(self._run_stage("windows-agent", lambda: WindowsAgentService(self.settings).run_once()))
        stages.append(self._run_stage("orchestrator", lambda: OrchestratorService(self.settings).run_once()))
        if run_recorder:
            stages.append(self._run_stage("recorder", lambda: RecorderService(self.settings).run()))
        if run_postprocess:
            stages.append(self._run_stage("postprocess", lambda: PostProcessService(self.settings).run_once()))
        if run_maintenance:
            stages.append(self._run_stage("maintenance", lambda: MaintenanceService(self.settings).run_once()))

        status_holder: dict[str, Any] = {}
        stages.append(
            self._run_stage(
                "status",
                lambda: self._capture_status(status_holder),
            )
        )
        status_payload = status_holder.get("status")
        health = self._status_health(status_payload)

        elapsed = perf_counter() - cycle_start
        log("soak", f"cycle={cycle} completed health={health or 'unknown'}")
        return SoakCycleResult(
            cycle=cycle,
            started_at=started_at,
            elapsed_seconds=elapsed,
            stages=stages,
            health=health,
        )

    def _capture_status(self, holder: dict[str, Any]) -> None:
        holder["status"] = StatusService(self.settings).build()

    def _run_stage(self, name: str, action: Callable[[], None]) -> SoakStageResult:
        start = perf_counter()
        try:
            action()
        except Exception as exc:
            elapsed = perf_counter() - start
            error = f"{exc.__class__.__name__}:{exc}"
            log("soak", f"stage={name} failed reason={error}")
            return SoakStageResult(
                stage=name,
                ok=False,
                elapsed_seconds=elapsed,
                error=error,
            )
        elapsed = perf_counter() - start
        return SoakStageResult(stage=name, ok=True, elapsed_seconds=elapsed)

    def _status_health(self, status_payload: dict[str, Any] | None) -> str | None:
        if not status_payload:
            return None
        summary = status_payload.get("summary")
        if not isinstance(summary, dict):
            return None
        health = summary.get("health")
        return str(health) if health is not None else None

    def _derive_health(
        self,
        cycle_results: list[SoakCycleResult],
        failed_stages: int,
    ) -> str:
        if failed_stages > 0:
            return "action_required"
        final_cycle = cycle_results[-1] if cycle_results else None
        if final_cycle is None or final_cycle.health is None:
            return "degraded"
        return final_cycle.health

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
