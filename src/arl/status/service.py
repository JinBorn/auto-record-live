from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arl.config import Settings
from arl.copywriter.models import CopywriterStateFile
from arl.exporter.models import ExporterAuditEvent, ExporterStateFile
from arl.orchestrator.models import OrchestratorStateFile, RecordingJobStatus
from arl.orchestrator.state_store import load_orchestrator_state
from arl.recorder.models import RecorderAuditEvent, RecorderStateFile
from arl.recovery.service import RecoveryService
from arl.shared.contracts import CopyAsset, ExportAsset, MatchBoundary, RecordingAsset, SubtitleAsset
from arl.shared.jsonl_store import load_models
from arl.subtitles.models import SubtitleAuditEvent, SubtitleStateFile


class StatusService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.temp_dir = settings.storage.temp_dir

    def build(self) -> dict[str, Any]:
        orchestrator_state = self._load_orchestrator_state()
        recorder_state = self._load_recorder_state()
        subtitle_state = self._load_subtitle_state()
        exporter_state = self._load_exporter_state()
        copywriter_state = self._load_copywriter_state()

        recording_assets = load_models(
            self.temp_dir / "recording-assets.jsonl",
            RecordingAsset,
        )
        boundaries = load_models(self.temp_dir / "match-boundaries.jsonl", MatchBoundary)
        subtitle_assets = load_models(
            self.temp_dir / "subtitle-assets.jsonl",
            SubtitleAsset,
        )
        export_assets = load_models(self.temp_dir / "export-assets.jsonl", ExportAsset)
        copy_assets = load_models(self.temp_dir / "copy-assets.jsonl", CopyAsset)
        recorder_events = load_models(
            self.settings.orchestrator.recorder_event_log_path,
            RecorderAuditEvent,
        )
        subtitle_events = load_models(
            self.temp_dir / "subtitles-events.jsonl",
            SubtitleAuditEvent,
        )
        exporter_events = load_models(
            self.temp_dir / "exporter-events.jsonl",
            ExporterAuditEvent,
        )
        recovery_summary = RecoveryService(self.settings).summary()

        missing_subtitles = self._count_missing_subtitles(boundaries, subtitle_assets)
        missing_exports = self._count_missing_exports(boundaries, export_assets)
        missing_copies = self._count_missing_copies(boundaries, copy_assets)
        subtitle_fallback_reasons = self._subtitle_fallback_reasons(subtitle_events)
        recorder_failure_events = self._recorder_failure_events(recorder_events)
        exporter_fallback_events = [
            event
            for event in exporter_events
            if event.event_type == "ffmpeg_export_fallback_placeholder"
        ]
        exporter_batch_aborted_events = [
            event
            for event in exporter_events
            if event.event_type == "ffmpeg_export_batch_aborted"
        ]
        failed_jobs = [
            job
            for job in orchestrator_state.recording_jobs
            if job.status == RecordingJobStatus.FAILED
        ]

        action_required_reasons = self._action_required_reasons(
            recorder_state=recorder_state,
            failed_jobs=failed_jobs,
            pending_actions=int(recovery_summary.get("actions_pending", 0)),
            failed_actions=int(recovery_summary.get("actions_failed", 0)),
            undispatched_actions=int(recovery_summary.get("actions_undispatched", 0)),
            exporter_batch_aborted_events=exporter_batch_aborted_events,
        )
        degraded_reasons = self._degraded_reasons(
            subtitle_fallback_reasons=subtitle_fallback_reasons,
            exporter_fallback_events=exporter_fallback_events,
            missing_subtitles=missing_subtitles,
            missing_exports=missing_exports,
            missing_copies=missing_copies,
            recorder_failure_events=recorder_failure_events,
        )
        action_required = bool(action_required_reasons)
        degraded = bool(degraded_reasons)
        health = "action_required" if action_required else "degraded" if degraded else "ok"

        return {
            "summary": {
                "health": health,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "action_required_reasons": action_required_reasons,
                "degraded_reasons": degraded_reasons,
            },
            "orchestrator": {
                "sessions_by_status": self._counter_dict(
                    session.status.value for session in orchestrator_state.sessions
                ),
                "recording_jobs_by_status": self._counter_dict(
                    job.status.value for job in orchestrator_state.recording_jobs
                ),
                "active_platforms": sorted(
                    {
                        self._active_key_platform(active_key)
                        for active_key in orchestrator_state.active_session_id_by_platform
                    }
                ),
                "active_streams": sorted(orchestrator_state.active_session_id_by_platform),
            },
            "recorder": {
                "recording_assets": len(recording_assets),
                "processed_jobs": len(recorder_state.processed_job_ids),
                "deferred_jobs": len(recorder_state.next_eligible_at_by_job_id),
                "manual_required_jobs": len(recorder_state.manual_required_job_ids),
                "recent_failure_events": len(recorder_failure_events),
            },
            "postprocess": {
                "match_boundaries": len(boundaries),
                "subtitle_assets": len(subtitle_assets),
                "export_assets": len(export_assets),
                "copy_assets": len(copy_assets),
                "missing_subtitles": missing_subtitles,
                "missing_exports": missing_exports,
                "missing_copies": missing_copies,
            },
            "subtitles": {
                "processed_matches": len(subtitle_state.processed_match_keys),
                "fallback_reasons": subtitle_fallback_reasons,
                "devices": self._subtitle_devices(subtitle_events),
                "fallback_devices": self._subtitle_fallback_devices(subtitle_events),
            },
            "exporter": {
                "processed_matches": len(exporter_state.processed_match_keys),
                "fallback_events": len(exporter_fallback_events),
                "batch_aborted_events": len(exporter_batch_aborted_events),
            },
            "copywriter": {
                "processed_matches": len(copywriter_state.processed_match_keys),
            },
            "recovery": {
                "pending_actions": recovery_summary.get("actions_pending", 0),
                "failed_actions": recovery_summary.get("actions_failed", 0),
                "undispatched_actions": recovery_summary.get("actions_undispatched", 0),
            },
        }

    def _load_orchestrator_state(self) -> OrchestratorStateFile:
        return load_orchestrator_state(self.settings.orchestrator.state_file)

    def _load_recorder_state(self) -> RecorderStateFile:
        path = self.temp_dir / "recorder-state.json"
        if not path.exists():
            return RecorderStateFile()
        return RecorderStateFile.model_validate_json(path.read_text(encoding="utf-8"))

    def _load_subtitle_state(self) -> SubtitleStateFile:
        path = self.temp_dir / "subtitles-state.json"
        if not path.exists():
            return SubtitleStateFile()
        return SubtitleStateFile.model_validate_json(path.read_text(encoding="utf-8"))

    def _load_exporter_state(self) -> ExporterStateFile:
        path = self.temp_dir / "exporter-state.json"
        if not path.exists():
            return ExporterStateFile()
        return ExporterStateFile.model_validate_json(path.read_text(encoding="utf-8"))

    def _load_copywriter_state(self) -> CopywriterStateFile:
        path = self.temp_dir / "copywriter-state.json"
        if not path.exists():
            return CopywriterStateFile()
        return CopywriterStateFile.model_validate_json(path.read_text(encoding="utf-8"))

    def _count_missing_subtitles(
        self,
        boundaries: list[MatchBoundary],
        subtitle_assets: list[SubtitleAsset],
    ) -> int:
        available = {
            (asset.session_id, asset.match_index)
            for asset in subtitle_assets
            if Path(asset.path).exists()
        }
        return sum(
            1
            for boundary in boundaries
            if (boundary.session_id, boundary.match_index) not in available
        )

    def _count_missing_exports(
        self,
        boundaries: list[MatchBoundary],
        export_assets: list[ExportAsset],
    ) -> int:
        available = {
            (asset.session_id, asset.match_index)
            for asset in export_assets
            if Path(asset.path).exists()
        }
        return sum(
            1
            for boundary in boundaries
            if (boundary.session_id, boundary.match_index) not in available
        )

    def _count_missing_copies(
        self,
        boundaries: list[MatchBoundary],
        copy_assets: list[CopyAsset],
    ) -> int:
        available = {
            (asset.session_id, asset.match_index)
            for asset in copy_assets
            if Path(asset.path).exists()
        }
        return sum(
            1
            for boundary in boundaries
            if (boundary.session_id, boundary.match_index) not in available
        )

    def _subtitle_fallback_reasons(
        self,
        subtitle_events: list[SubtitleAuditEvent],
    ) -> dict[str, int]:
        return self._counter_dict(
            event.reason or "unknown"
            for event in subtitle_events
            if event.event_type == "subtitle_fallback_placeholder"
        )

    def _subtitle_devices(
        self,
        subtitle_events: list[SubtitleAuditEvent],
    ) -> dict[str, int]:
        return self._counter_dict(
            f"{event.device}:{event.compute_type or 'unknown'}"
            for event in subtitle_events
            if event.device
        )

    def _subtitle_fallback_devices(
        self,
        subtitle_events: list[SubtitleAuditEvent],
    ) -> dict[str, int]:
        return self._counter_dict(
            event.fallback_device
            for event in subtitle_events
            if event.fallback_device
        )

    def _recorder_failure_events(
        self,
        recorder_events: list[RecorderAuditEvent],
    ) -> list[RecorderAuditEvent]:
        return [
            event
            for event in recorder_events
            if event.event_type
            in {
                "ffmpeg_record_failed",
                "ffmpeg_fallback_placeholder",
                "quality_below_actual_resolution",
                "recording_retry_exhausted",
                "recording_session_retry_budget_exceeded",
                "recording_manual_recovery_required",
            }
        ]

    def _action_required_reasons(
        self,
        *,
        recorder_state: RecorderStateFile,
        failed_jobs,
        pending_actions: int,
        failed_actions: int,
        undispatched_actions: int,
        exporter_batch_aborted_events: list[ExporterAuditEvent],
    ) -> list[dict[str, object]]:
        reasons: list[dict[str, object]] = []
        if recorder_state.manual_required_job_ids:
            reasons.append(
                {
                    "code": "recorder_manual_required",
                    "count": len(recorder_state.manual_required_job_ids),
                    "job_ids": self._sample_strings(recorder_state.manual_required_job_ids),
                }
            )
        if failed_jobs:
            reasons.append(
                {
                    "code": "orchestrator_failed_jobs",
                    "count": len(failed_jobs),
                    "job_ids": self._sample_strings(job.job_id for job in failed_jobs),
                }
            )
        if pending_actions > 0:
            reasons.append(
                {
                    "code": "recovery_pending_actions",
                    "count": pending_actions,
                }
            )
        if undispatched_actions > 0:
            reasons.append(
                {
                    "code": "recovery_undispatched_actions",
                    "count": undispatched_actions,
                }
            )
        if failed_actions > 0:
            reasons.append(
                {
                    "code": "recovery_failed_actions",
                    "count": failed_actions,
                }
            )
        if exporter_batch_aborted_events:
            reasons.append(
                {
                    "code": "exporter_batch_aborted",
                    "count": len(exporter_batch_aborted_events),
                    "session_ids": self._sample_strings(
                        event.session_id
                        for event in exporter_batch_aborted_events
                        if event.session_id
                    ),
                }
            )
        return reasons

    def _degraded_reasons(
        self,
        *,
        subtitle_fallback_reasons: dict[str, int],
        exporter_fallback_events: list[ExporterAuditEvent],
        missing_subtitles: int,
        missing_exports: int,
        missing_copies: int,
        recorder_failure_events: list[RecorderAuditEvent],
    ) -> list[dict[str, object]]:
        reasons: list[dict[str, object]] = []
        if subtitle_fallback_reasons:
            reasons.append(
                {
                    "code": "subtitle_fallbacks",
                    "count": sum(subtitle_fallback_reasons.values()),
                    "reasons": subtitle_fallback_reasons,
                }
            )
        if exporter_fallback_events:
            reasons.append(
                {
                    "code": "exporter_fallbacks",
                    "count": len(exporter_fallback_events),
                    "session_ids": self._sample_strings(
                        event.session_id for event in exporter_fallback_events if event.session_id
                    ),
                }
            )
        if missing_subtitles > 0:
            reasons.append({"code": "missing_subtitles", "count": missing_subtitles})
        if missing_exports > 0:
            reasons.append({"code": "missing_exports", "count": missing_exports})
        if missing_copies > 0:
            reasons.append({"code": "missing_copies", "count": missing_copies})
        if recorder_failure_events:
            reasons.append(
                {
                    "code": "recorder_failure_events",
                    "count": len(recorder_failure_events),
                    "event_types": self._counter_dict(
                        event.event_type for event in recorder_failure_events
                    ),
                    "job_ids": self._sample_strings(
                        event.job_id for event in recorder_failure_events if event.job_id
                    ),
                }
            )
        return reasons

    def _sample_strings(self, values, *, limit: int = 5) -> list[str]:
        sampled: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value)
            if text in seen:
                continue
            sampled.append(text)
            seen.add(text)
            if len(sampled) >= limit:
                break
        return sampled

    def _counter_dict(self, values) -> dict[str, int]:
        return dict(Counter(values))

    def _active_key_platform(self, active_key: str) -> str:
        return active_key.split(":", 1)[0]
