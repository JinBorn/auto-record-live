from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import (
    OrchestratorSettings,
    Settings,
    StorageSettings,
)
from arl.exporter.models import ExporterStateFile
from arl.orchestrator.models import (
    OrchestratorStateFile,
    RecordingJobRecord,
    RecordingJobStatus,
    SessionRecord,
    SessionStatus,
)
from arl.recorder.models import RecorderStateFile
from arl.shared.contracts import (
    ExportAsset,
    MatchBoundary,
    RecordingAsset,
    SourceType,
    SubtitleAsset,
)
from arl.shared.jsonl_store import append_model
from arl.status.service import StatusService
from arl.subtitles.models import SubtitleAuditEvent, SubtitleStateFile


class StatusServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.temp_root = root / "tmp"
        self.settings = Settings(
            storage=StorageSettings(
                raw_dir=root / "raw",
                processed_dir=root / "processed",
                export_dir=root / "exports",
                temp_dir=self.temp_root,
            ),
            orchestrator=OrchestratorSettings(
                state_file=self.temp_root / "orchestrator-state.json",
                agent_event_log_path=self.temp_root / "windows-agent-events.jsonl",
                recorder_event_log_path=self.temp_root / "recorder-events.jsonl",
                audit_log_path=self.temp_root / "orchestrator-events.jsonl",
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_empty_status_is_ok(self) -> None:
        status = StatusService(self.settings).build()

        self.assertEqual(status["summary"]["health"], "ok")
        self.assertEqual(status["summary"]["action_required_reasons"], [])
        self.assertEqual(status["summary"]["degraded_reasons"], [])
        self.assertEqual(status["recorder"]["recording_assets"], 0)
        self.assertEqual(status["postprocess"]["missing_subtitles"], 0)
        self.assertEqual(status["postprocess"]["missing_exports"], 0)

    def test_healthy_completed_manifests_are_ok(self) -> None:
        session_id = "session-status-ok"
        self._write_orchestrator_state(session_id=session_id)
        recording_path = self._write_file("raw", session_id, "recording.mp4")
        subtitle_path = self._write_file("processed", session_id, "match-01.srt")
        export_path = self._write_file("exports", session_id, "match-01.mp4")
        append_model(
            self.temp_root / "recording-assets.jsonl",
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.DIRECT_STREAM,
                path=str(recording_path),
                started_at=self._now(),
                ended_at=self._now(),
            ),
        )
        append_model(
            self.temp_root / "match-boundaries.jsonl",
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=60.0,
                confidence=0.8,
            ),
        )
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        append_model(
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id=session_id,
                match_index=1,
                path=str(export_path),
                subtitle_path=str(subtitle_path),
                created_at=self._now(),
            ),
        )
        self._write_json_state(
            self.temp_root / "subtitles-state.json",
            SubtitleStateFile(processed_match_keys=[f"{session_id}:1"]),
        )
        self._write_json_state(
            self.temp_root / "exporter-state.json",
            ExporterStateFile(processed_match_keys=[f"{session_id}:1"]),
        )

        status = StatusService(self.settings).build()

        self.assertEqual(status["summary"]["health"], "ok")
        self.assertEqual(status["postprocess"]["match_boundaries"], 1)
        self.assertEqual(status["postprocess"]["subtitle_assets"], 1)
        self.assertEqual(status["postprocess"]["export_assets"], 1)

    def test_subtitle_fallback_and_missing_outputs_are_degraded(self) -> None:
        session_id = "session-status-degraded"
        self._write_orchestrator_state(session_id=session_id)
        append_model(
            self.temp_root / "match-boundaries.jsonl",
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=60.0,
                confidence=0.8,
            ),
        )
        append_model(
            self.temp_root / "subtitles-events.jsonl",
            SubtitleAuditEvent(
                event_type="subtitle_fallback_placeholder",
                session_id=session_id,
                match_index=1,
                device="cpu",
                compute_type="int8",
                fallback_device="cpu",
                reason="transcribe_failed",
                reason_detail="transcribe_exc:RuntimeError:CUDA",
                created_at=self._now(),
            ),
        )

        status = StatusService(self.settings).build()

        self.assertEqual(status["summary"]["health"], "degraded")
        self.assertEqual(status["subtitles"]["fallback_reasons"], {"transcribe_failed": 1})
        self.assertEqual(status["subtitles"]["devices"], {"cpu:int8": 1})
        self.assertEqual(status["subtitles"]["fallback_devices"], {"cpu": 1})
        self.assertEqual(status["postprocess"]["missing_subtitles"], 1)
        self.assertEqual(status["postprocess"]["missing_exports"], 1)
        self.assertEqual(
            status["summary"]["degraded_reasons"],
            [
                {
                    "code": "subtitle_fallbacks",
                    "count": 1,
                    "reasons": {"transcribe_failed": 1},
                },
                {"code": "missing_subtitles", "count": 1},
                {"code": "missing_exports", "count": 1},
            ],
        )

    def test_failed_job_requires_action(self) -> None:
        session_id = "session-status-action"
        self._write_orchestrator_state(
            session_id=session_id,
            job_status=RecordingJobStatus.FAILED,
        )
        self._write_json_state(
            self.temp_root / "recorder-state.json",
            RecorderStateFile(manual_required_job_ids=["recording-status-action"]),
        )

        status = StatusService(self.settings).build()

        self.assertEqual(status["summary"]["health"], "action_required")
        self.assertEqual(status["recorder"]["manual_required_jobs"], 1)
        self.assertEqual(status["orchestrator"]["recording_jobs_by_status"], {"failed": 1})
        self.assertEqual(
            status["summary"]["action_required_reasons"],
            [
                {
                    "code": "recorder_manual_required",
                    "count": 1,
                    "job_ids": ["recording-status-action"],
                },
                {
                    "code": "orchestrator_failed_jobs",
                    "count": 1,
                    "job_ids": ["recording-status-action"],
                },
            ],
        )

    def _write_orchestrator_state(
        self,
        *,
        session_id: str,
        job_status: RecordingJobStatus = RecordingJobStatus.STOPPED,
    ) -> None:
        state = OrchestratorStateFile(
            sessions=[
                SessionRecord(
                    session_id=session_id,
                    streamer_name="streamer",
                    room_url="https://live.example.test/1",
                    platform="douyin",
                    source_type=SourceType.DIRECT_STREAM,
                    status=SessionStatus.STOPPED,
                    started_at=self._now(),
                    ended_at=self._now(),
                )
            ],
            recording_jobs=[
                RecordingJobRecord(
                    job_id="recording-status-action",
                    session_id=session_id,
                    platform="douyin",
                    source_type=SourceType.DIRECT_STREAM,
                    status=job_status,
                    created_at=self._now(),
                    ended_at=self._now(),
                )
            ],
        )
        self._write_json_state(self.settings.orchestrator.state_file, state)

    def _write_file(self, *parts: str) -> Path:
        path = self.settings.storage.temp_dir.parent.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
        return path

    def _write_json_state(self, path: Path, model) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _now(self) -> datetime:
        return datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
