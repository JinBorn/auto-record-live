from __future__ import annotations

import unittest
from unittest.mock import patch

from arl.config import Settings
from arl.soak.service import SoakService


class _StageStub:
    calls: list[str] = []
    failures: set[str] = set()

    def __init__(self, settings: Settings, name: str) -> None:
        self.name = name

    def run_once(self) -> None:
        self._run()

    def run(self) -> None:
        self._run()

    def build(self) -> dict:
        self._run()
        return {"summary": {"health": "ok"}}

    def _run(self) -> None:
        self.calls.append(self.name)
        if self.name in self.failures:
            raise RuntimeError(f"{self.name} failed")


def _factory(name: str):
    return lambda settings: _StageStub(settings, name)


class SoakServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        _StageStub.calls = []
        _StageStub.failures = set()
        self.settings = Settings()

    def test_soak_runs_expected_stages_per_cycle(self) -> None:
        with self._patch_stages():
            report = SoakService(self.settings).run(
                cycles=2,
                interval_seconds=0,
                sleeper=lambda _: None,
            )

        self.assertEqual(report.health, "ok")
        self.assertEqual(report.failed_stages, 0)
        self.assertEqual(
            _StageStub.calls,
            [
                "windows-agent",
                "orchestrator",
                "recorder",
                "postprocess",
                "status",
                "windows-agent",
                "orchestrator",
                "recorder",
                "postprocess",
                "status",
            ],
        )

    def test_soak_records_stage_failure_and_continues_to_status(self) -> None:
        _StageStub.failures = {"recorder"}
        with self._patch_stages():
            report = SoakService(self.settings).run(
                cycles=1,
                interval_seconds=0,
                sleeper=lambda _: None,
            )

        self.assertEqual(report.health, "action_required")
        self.assertEqual(report.failed_stages, 1)
        self.assertIn("status", _StageStub.calls)
        stages = report.cycle_results[0].stages
        recorder = next(stage for stage in stages if stage.stage == "recorder")
        self.assertFalse(recorder.ok)
        self.assertIn("RuntimeError:recorder failed", recorder.error or "")

    def test_soak_can_skip_recorder_and_postprocess(self) -> None:
        with self._patch_stages():
            report = SoakService(self.settings).run(
                cycles=1,
                interval_seconds=0,
                run_recorder=False,
                run_postprocess=False,
                sleeper=lambda _: None,
            )

        self.assertEqual(report.health, "ok")
        self.assertEqual(
            _StageStub.calls,
            ["windows-agent", "orchestrator", "status"],
        )

    def _patch_stages(self):
        return patch.multiple(
            "arl.soak.service",
            WindowsAgentService=_factory("windows-agent"),
            OrchestratorService=_factory("orchestrator"),
            RecorderService=_factory("recorder"),
            PostProcessService=_factory("postprocess"),
            MaintenanceService=_factory("maintenance"),
            StatusService=_factory("status"),
        )


if __name__ == "__main__":
    unittest.main()
