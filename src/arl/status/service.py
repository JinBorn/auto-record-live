from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arl.config import Settings
from arl.copywriter.models import CopywriterStateFile
from arl.editing.models import EditPlannerStateFile
from arl.exporter.models import ExporterAuditEvent, ExporterStateFile
from arl.highlights.models import HighlightPlannerStateFile
from arl.orchestrator.models import OrchestratorStateFile, RecordingJobStatus
from arl.orchestrator.state_store import load_orchestrator_state
from arl.recorder.asset_repair import RecordingAssetRepairService, UnregisteredRecording
from arl.recorder.models import RecorderAuditEvent, RecorderStateFile
from arl.recovery.service import RecoveryService
from arl.shared.contracts import (
    CopyAsset,
    EditPlanAsset,
    ExportAsset,
    HighlightPlanAsset,
    MatchBoundary,
    RecordingAsset,
    SubtitleAsset,
)
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
        highlight_state = self._load_highlight_state()
        editing_state = self._load_editing_state()
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
        highlight_plans = load_models(
            self.temp_dir / "highlight-plans.jsonl",
            HighlightPlanAsset,
        )
        edit_plans = load_models(self.temp_dir / "edit-plans.jsonl", EditPlanAsset)
        recorder_events = load_models(
            self.settings.orchestrator.recorder_event_log_path,
            RecorderAuditEvent,
        )
        unregistered_recordings = RecordingAssetRepairService(
            self.settings
        ).find_unregistered()
        subtitle_events = load_models(
            self.temp_dir / "subtitles-events.jsonl",
            SubtitleAuditEvent,
        )
        exporter_events = load_models(
            self.temp_dir / "exporter-events.jsonl",
            ExporterAuditEvent,
        )
        recovery_summary = RecoveryService(self.settings).summary()
        complete_boundaries = [
            boundary for boundary in boundaries if boundary.is_complete
        ]

        missing_subtitles = self._count_missing_subtitles(
            complete_boundaries,
            subtitle_assets,
        )
        missing_exports = self._count_missing_exports(complete_boundaries, export_assets)
        missing_copies = self._count_missing_copies(complete_boundaries, copy_assets)
        subtitle_fallback_reasons = self._subtitle_fallback_reasons(subtitle_events)
        recorder_failure_events = self._recorder_failure_events(recorder_events)
        bilibili_cookie_expired_events = [
            event
            for event in recorder_events
            if event.event_type == "cookie_expired_for_bilibili"
        ]
        bilibili_stream_url_events = [
            event
            for event in recorder_events
            if event.event_type == "stream_url_expired_for_bilibili"
        ]
        exporter_fallback_events = self._unresolved_exporter_fallback_events(
            exporter_events,
            export_assets,
        )
        exporter_batch_aborted_events = self._unresolved_exporter_batch_aborted_events(
            exporter_events,
            boundaries,
            export_assets,
        )
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
            bilibili_cookie_expired_events=bilibili_cookie_expired_events,
        )
        degraded_reasons = self._degraded_reasons(
            subtitle_fallback_reasons=subtitle_fallback_reasons,
            exporter_fallback_events=exporter_fallback_events,
            missing_subtitles=missing_subtitles,
            missing_exports=missing_exports,
            missing_copies=missing_copies,
            recorder_failure_events=recorder_failure_events,
            bilibili_stream_url_events=bilibili_stream_url_events,
            unregistered_recordings=unregistered_recordings,
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
                "complete_match_boundaries": len(complete_boundaries),
                "incomplete_match_boundaries": len(boundaries) - len(complete_boundaries),
                "subtitle_assets": len(subtitle_assets),
                "highlight_plans": len(highlight_plans),
                "edit_plans": len(edit_plans),
                "export_assets": len(export_assets),
                "copy_assets": len(copy_assets),
                "missing_subtitles": missing_subtitles,
                "missing_exports": missing_exports,
                "missing_copies": missing_copies,
                "unregistered_recordings": len(unregistered_recordings),
                "unregistered_recording_paths": [
                    str(item.path) for item in unregistered_recordings[:5]
                ],
            },
            "subtitles": {
                "processed_matches": len(subtitle_state.processed_match_keys),
                "fallback_reasons": subtitle_fallback_reasons,
                "devices": self._subtitle_devices(subtitle_events),
                "fallback_devices": self._subtitle_fallback_devices(subtitle_events),
            },
            "highlights": {
                "processed_matches": len(highlight_state.processed_match_keys),
                "plans": len(highlight_plans),
            },
            "editing": {
                "processed_matches": len(editing_state.processed_match_keys),
                "plans": len(edit_plans),
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

    def _load_highlight_state(self) -> HighlightPlannerStateFile:
        path = self.temp_dir / "highlight-planner-state.json"
        if not path.exists():
            return HighlightPlannerStateFile()
        return HighlightPlannerStateFile.model_validate_json(path.read_text(encoding="utf-8"))

    def _load_editing_state(self) -> EditPlannerStateFile:
        path = self.temp_dir / "editing-state.json"
        if not path.exists():
            return EditPlannerStateFile()
        return EditPlannerStateFile.model_validate_json(path.read_text(encoding="utf-8"))

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

    def _unresolved_exporter_fallback_events(
        self,
        exporter_events: list[ExporterAuditEvent],
        export_assets: list[ExportAsset],
    ) -> list[ExporterAuditEvent]:
        media_export_times = self._present_media_export_times(export_assets)
        latest_outcomes = self._latest_exporter_match_outcomes(exporter_events)
        unresolved: list[ExporterAuditEvent] = []
        for event in exporter_events:
            if event.event_type != "ffmpeg_export_fallback_placeholder":
                continue
            if event.match_index is None:
                unresolved.append(event)
                continue
            key = (event.session_id, event.match_index)
            if latest_outcomes.get(key) is not event:
                continue
            resolved_at = media_export_times.get((event.session_id, event.match_index))
            if resolved_at is not None and resolved_at >= event.created_at:
                continue
            unresolved.append(event)
        return unresolved

    def _unresolved_exporter_batch_aborted_events(
        self,
        exporter_events: list[ExporterAuditEvent],
        boundaries: list[MatchBoundary],
        export_assets: list[ExportAsset],
    ) -> list[ExporterAuditEvent]:
        media_export_times = self._present_media_export_times(export_assets)
        latest_batch_events = self._latest_exporter_batch_aborts(exporter_events)
        boundaries_by_session: dict[str, list[MatchBoundary]] = {}
        for boundary in boundaries:
            boundaries_by_session.setdefault(boundary.session_id, []).append(boundary)

        unresolved: list[ExporterAuditEvent] = []
        for event in latest_batch_events.values():
            session_boundaries = boundaries_by_session.get(event.session_id, [])
            if not session_boundaries:
                unresolved.append(event)
                continue
            if all(
                (
                    media_export_times.get((boundary.session_id, boundary.match_index))
                    is not None
                    and media_export_times[(boundary.session_id, boundary.match_index)]
                    >= event.created_at
                )
                for boundary in session_boundaries
            ):
                continue
            unresolved.append(event)
        return unresolved

    def _latest_exporter_match_outcomes(
        self,
        exporter_events: list[ExporterAuditEvent],
    ) -> dict[tuple[str, int], ExporterAuditEvent]:
        latest: dict[tuple[str, int], tuple[datetime, int, ExporterAuditEvent]] = {}
        terminal_event_types = {
            "ffmpeg_export_succeeded",
            "ffmpeg_export_fallback_placeholder",
        }
        for index, event in enumerate(exporter_events):
            if event.event_type not in terminal_event_types or event.match_index is None:
                continue
            key = (event.session_id, event.match_index)
            current = latest.get(key)
            marker = (event.created_at, index, event)
            if current is None or marker[:2] > current[:2]:
                latest[key] = marker
        return {key: event for key, (_, _, event) in latest.items()}

    def _latest_exporter_batch_aborts(
        self,
        exporter_events: list[ExporterAuditEvent],
    ) -> dict[str, ExporterAuditEvent]:
        latest: dict[str, tuple[datetime, int, ExporterAuditEvent]] = {}
        for index, event in enumerate(exporter_events):
            if event.event_type != "ffmpeg_export_batch_aborted":
                continue
            current = latest.get(event.session_id)
            marker = (event.created_at, index, event)
            if current is None or marker[:2] > current[:2]:
                latest[event.session_id] = marker
        return {session_id: event for session_id, (_, _, event) in latest.items()}

    def _present_media_export_times(
        self,
        export_assets: list[ExportAsset],
    ) -> dict[tuple[str, int], datetime]:
        times: dict[tuple[str, int], datetime] = {}
        for asset in export_assets:
            path = Path(asset.path)
            if path.suffix.lower() != ".mp4" or not path.exists():
                continue
            key = (asset.session_id, asset.match_index)
            current = times.get(key)
            if current is None or asset.created_at > current:
                times[key] = asset.created_at
        return times

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
        bilibili_cookie_expired_events: list[RecorderAuditEvent],
    ) -> list[dict[str, object]]:
        reasons: list[dict[str, object]] = []
        if bilibili_cookie_expired_events:
            reasons.append(
                {
                    "code": "bilibili_sessdata_expired",
                    "count": len(bilibili_cookie_expired_events),
                    "job_ids": self._sample_strings(
                        event.job_id
                        for event in bilibili_cookie_expired_events
                        if event.job_id
                    ),
                }
            )
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
        bilibili_stream_url_events: list[RecorderAuditEvent],
        unregistered_recordings: list[UnregisteredRecording],
    ) -> list[dict[str, object]]:
        reasons: list[dict[str, object]] = []
        if bilibili_stream_url_events:
            reasons.append(
                {
                    "code": "bilibili_stream_url_expired",
                    "count": len(bilibili_stream_url_events),
                    "reasons": self._counter_dict(
                        event.reason or "unknown"
                        for event in bilibili_stream_url_events
                    ),
                    "job_ids": self._sample_strings(
                        event.job_id
                        for event in bilibili_stream_url_events
                        if event.job_id
                    ),
                }
            )
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
        if unregistered_recordings:
            reasons.append(
                {
                    "code": "unregistered_recordings",
                    "count": len(unregistered_recordings),
                    "paths": self._sample_strings(
                        str(item.path) for item in unregistered_recordings
                    ),
                }
            )
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
