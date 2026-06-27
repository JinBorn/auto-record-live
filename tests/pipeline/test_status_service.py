from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import (
    OrchestratorSettings,
    Settings,
    StorageSettings,
)
from arl.copywriter.models import CopywriterStateFile
from arl.editing.models import EditPlannerStateFile
from arl.exporter.models import ExporterAuditEvent, ExporterStateFile
from arl.highlights.models import HighlightPlannerStateFile
from arl.orchestrator.models import (
    OrchestratorStateFile,
    RecordingJobRecord,
    RecordingJobStatus,
    SessionRecord,
    SessionStatus,
)
from arl.recorder.models import RecorderAuditEvent, RecorderStateFile
from arl.shared.contracts import (
    CopyAsset,
    EditPlanAsset,
    ExportAsset,
    HighlightClipWindow,
    HighlightPlanAsset,
    MatchBoundary,
    RecordingAsset,
    RecordingChunk,
    RecordingChunkManifest,
    SourceType,
    SubtitleAsset,
    TimelineSegment,
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
        copy_path = self._write_file("processed", session_id, "match-01-copy.json")
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
            self.temp_root / "highlight-plans.jsonl",
            HighlightPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=60.0,
                windows=[
                    HighlightClipWindow(
                        started_at_seconds=0.0,
                        ended_at_seconds=60.0,
                        reason="fixture",
                    )
                ],
                created_at=self._now(),
            ),
        )
        append_model(
            self.temp_root / "edit-plans.jsonl",
            EditPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=60.0,
                timeline=[
                    TimelineSegment(
                        role="teaser",
                        source_start_seconds=10.0,
                        source_end_seconds=20.0,
                        reason="highlight_keyword",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=0.0,
                        source_end_seconds=60.0,
                        reason="full_validated_match",
                    ),
                ],
                created_at=self._now(),
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
        append_model(
            self.temp_root / "copy-assets.jsonl",
            CopyAsset(
                session_id=session_id,
                match_index=1,
                path=str(copy_path),
                title="fixture title",
                description="fixture description",
                tags=["fixture"],
                subtitle_path=str(subtitle_path),
                export_path=str(export_path),
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
        self._write_json_state(
            self.temp_root / "highlight-planner-state.json",
            HighlightPlannerStateFile(processed_match_keys=[f"{session_id}:1"]),
        )
        self._write_json_state(
            self.temp_root / "editing-state.json",
            EditPlannerStateFile(processed_match_keys=[f"{session_id}:1"]),
        )
        self._write_json_state(
            self.temp_root / "copywriter-state.json",
            CopywriterStateFile(processed_match_keys=[f"{session_id}:1"]),
        )

        status = StatusService(self.settings).build()

        self.assertEqual(status["summary"]["health"], "ok")
        self.assertEqual(status["postprocess"]["match_boundaries"], 1)
        self.assertEqual(status["postprocess"]["subtitle_assets"], 1)
        self.assertEqual(status["postprocess"]["highlight_plans"], 1)
        self.assertEqual(status["postprocess"]["edit_plans"], 1)
        self.assertEqual(status["postprocess"]["export_assets"], 1)
        self.assertEqual(status["postprocess"]["copy_assets"], 1)
        self.assertEqual(status["postprocess"]["missing_copies"], 0)
        self.assertEqual(status["highlights"]["plans"], 1)
        self.assertEqual(status["highlights"]["processed_matches"], 1)
        self.assertEqual(status["editing"]["plans"], 1)
        self.assertEqual(status["editing"]["processed_matches"], 1)
        self.assertEqual(status["copywriter"]["processed_matches"], 1)

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
        self.assertEqual(status["postprocess"]["missing_copies"], 1)
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
                {"code": "missing_copies", "count": 1},
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

    def test_bilibili_cookie_expired_event_requires_action(self) -> None:
        append_model(
            self.settings.orchestrator.recorder_event_log_path,
            RecorderAuditEvent(
                event_type="cookie_expired_for_bilibili",
                session_id="session-bili",
                job_id="job-bili",
                source_type=SourceType.DIRECT_STREAM,
                reason="sessdata_expired:api_error:code=-101:account_not_logged_in",
                created_at=self._now(),
            ),
        )

        status = StatusService(self.settings).build()

        self.assertEqual(status["summary"]["health"], "action_required")
        self.assertEqual(
            status["summary"]["action_required_reasons"],
            [
                {
                    "code": "bilibili_sessdata_expired",
                    "count": 1,
                    "job_ids": ["job-bili"],
                }
            ],
        )

    def test_bilibili_stream_url_event_is_degraded_diagnostic(self) -> None:
        append_model(
            self.settings.orchestrator.recorder_event_log_path,
            RecorderAuditEvent(
                event_type="stream_url_expired_for_bilibili",
                session_id="session-bili",
                job_id="job-bili",
                source_type=SourceType.DIRECT_STREAM,
                reason="refresh_failed:stream_url_missing",
                created_at=self._now(),
            ),
        )

        status = StatusService(self.settings).build()

        self.assertEqual(status["summary"]["health"], "degraded")
        self.assertEqual(
            status["summary"]["degraded_reasons"],
            [
                {
                    "code": "bilibili_stream_url_expired",
                    "count": 1,
                    "reasons": {"refresh_failed:stream_url_missing": 1},
                    "job_ids": ["job-bili"],
                }
            ],
        )

    def test_incomplete_boundaries_do_not_count_as_missing_outputs(self) -> None:
        session_id = "session-status-incomplete"
        self._write_orchestrator_state(session_id=session_id)
        append_model(
            self.temp_root / "match-boundaries.jsonl",
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=900.0,
                confidence=0.95,
                is_complete=False,
                reason="incomplete_no_end",
            ),
        )

        status = StatusService(self.settings).build()

        self.assertEqual(status["summary"]["health"], "ok")
        self.assertEqual(status["postprocess"]["match_boundaries"], 1)
        self.assertEqual(status["postprocess"]["complete_match_boundaries"], 0)
        self.assertEqual(status["postprocess"]["incomplete_match_boundaries"], 1)
        self.assertEqual(status["postprocess"]["missing_subtitles"], 0)
        self.assertEqual(status["postprocess"]["missing_exports"], 0)
        self.assertEqual(status["postprocess"]["missing_copies"], 0)

    def test_status_ignores_exporter_failures_resolved_by_later_mp4(self) -> None:
        session_id = "session-exporter-resolved"
        subtitle_path = self._write_file("processed", session_id, "match-01.srt")
        export_path = self._write_file("exports", "bilibili", f"{session_id}_match01.mp4")
        copy_path = self._write_file("processed", session_id, "match-01-copy.json")
        failed_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        resolved_at = datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc)
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
                created_at=resolved_at,
            ),
        )
        append_model(
            self.temp_root / "copy-assets.jsonl",
            CopyAsset(
                session_id=session_id,
                match_index=1,
                path=str(copy_path),
                title="fixture title",
                description="fixture description",
                tags=["fixture"],
                subtitle_path=str(subtitle_path),
                export_path=str(export_path),
                created_at=resolved_at,
            ),
        )
        append_model(
            self.temp_root / "exporter-events.jsonl",
            ExporterAuditEvent(
                event_type="ffmpeg_export_fallback_placeholder",
                session_id=session_id,
                match_index=1,
                decision="fallback_placeholder",
                failure_category="ffmpeg_process_error_retryable",
                is_retryable=True,
                reason_code="ffmpeg_process_error",
                reason_detail="timed out after 120s",
                reason="timed out after 120s",
                created_at=failed_at,
            ),
        )
        append_model(
            self.temp_root / "exporter-events.jsonl",
            ExporterAuditEvent(
                event_type="ffmpeg_export_batch_aborted",
                session_id=session_id,
                match_index=1,
                decision="batch_aborted",
                failure_category="ffmpeg_process_error_retryable",
                is_retryable=True,
                reason_code="ffmpeg_process_error",
                reason_detail="timed out after 120s",
                reason="timed out after 120s",
                consecutive_fallbacks=3,
                remaining_matches=0,
                created_at=failed_at,
            ),
        )

        status = StatusService(self.settings).build()

        self.assertEqual(status["summary"]["health"], "ok")
        self.assertEqual(status["exporter"]["fallback_events"], 0)
        self.assertEqual(status["exporter"]["batch_aborted_events"], 0)
        self.assertEqual(status["summary"]["degraded_reasons"], [])
        self.assertEqual(status["summary"]["action_required_reasons"], [])

    def test_unregistered_raw_recording_is_degraded_diagnostic(self) -> None:
        session_id = "session-20260606101149-9fe32958"
        recording_path = (
            self.settings.storage.raw_dir / session_id / "recording-source.mp4"
        )
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_bytes(b"fake mp4 bytes")
        old_time = 1_000_000_000
        os.utime(recording_path, (old_time, old_time))

        status = StatusService(self.settings).build()

        self.assertEqual(status["summary"]["health"], "degraded")
        self.assertEqual(status["postprocess"]["unregistered_recordings"], 1)
        self.assertEqual(
            status["postprocess"]["unregistered_recording_paths"],
            [str(recording_path)],
        )
        self.assertEqual(
            status["summary"]["degraded_reasons"],
            [
                {
                    "code": "unregistered_recordings",
                    "count": 1,
                    "paths": [str(recording_path)],
                }
            ],
        )

    def test_unregistered_chunk_manifest_is_degraded_diagnostic(self) -> None:
        session_id = "session-20260606101149-9fe32958"
        manifest_path = self._write_chunk_manifest(session_id)

        status = StatusService(self.settings).build()

        self.assertEqual(status["summary"]["health"], "degraded")
        self.assertEqual(status["postprocess"]["unregistered_recordings"], 1)
        self.assertEqual(
            status["postprocess"]["unregistered_recording_paths"],
            [str(manifest_path)],
        )

    def test_registered_chunk_manifest_is_not_unregistered(self) -> None:
        session_id = "session-20260606101149-9fe32958"
        manifest_path = self._write_chunk_manifest(session_id)
        append_model(
            self.temp_root / "recording-assets.jsonl",
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.DIRECT_STREAM,
                path=str(manifest_path),
                started_at=datetime(2026, 6, 6, 10, 11, 49, tzinfo=timezone.utc),
                ended_at=datetime(2026, 6, 6, 10, 12, 9, tzinfo=timezone.utc),
            ),
        )

        status = StatusService(self.settings).build()

        self.assertEqual(status["summary"]["health"], "ok")
        self.assertEqual(status["postprocess"]["unregistered_recordings"], 0)

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

    def _write_chunk_manifest(self, session_id: str) -> Path:
        raw_dir = self.settings.storage.raw_dir / session_id
        chunk_dir = raw_dir / "chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        (chunk_dir / "recording-00000.mp4").write_bytes(b"chunk-0")
        (chunk_dir / "recording-00001.mp4").write_bytes(b"chunk-1")
        manifest_path = raw_dir / "recording-chunks.json"
        started_at = datetime(2026, 6, 6, 10, 11, 49, tzinfo=timezone.utc)
        manifest = RecordingChunkManifest(
            session_id=session_id,
            source_type=SourceType.DIRECT_STREAM,
            path=str(manifest_path),
            started_at=started_at,
            chunks=[
                RecordingChunk(
                    path="chunks/recording-00000.mp4",
                    started_at_seconds=0.0,
                    ended_at_seconds=10.0,
                    duration_seconds=10.0,
                    index=0,
                ),
                RecordingChunk(
                    path="chunks/recording-00001.mp4",
                    started_at_seconds=10.0,
                    ended_at_seconds=20.0,
                    duration_seconds=10.0,
                    index=1,
                ),
            ],
            created_at=started_at,
        )
        manifest_path.write_text(
            manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        old_time = 1_000_000_000
        os.utime(manifest_path, (old_time, old_time))
        return manifest_path

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
