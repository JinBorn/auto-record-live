from __future__ import annotations

from collections.abc import Callable
from typing import Any

from arl.config import Settings
from arl.copywriter.service import CopywriterService
from arl.editing.service import EditingPlannerService
from arl.exporter.service import ExporterService
from arl.highlights.service import HighlightPlannerService
from arl.recorder.asset_repair import RecordingAssetRepairService
from arl.segmenter.semantic_hints import SemanticStageHintService
from arl.segmenter.service import SegmenterService
from arl.shared.contracts import ExportAsset, MatchBoundary, RecordingAsset
from arl.shared.jsonl_store import load_models
from arl.shared.logging import log
from arl.subtitles.service import SubtitleService


class PostProcessService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run_once(self, *, session_ids: set[str] | None = None) -> None:
        log("postprocess", "starting")
        if session_ids is not None:
            log("postprocess", f"filters session_ids={','.join(sorted(session_ids))}")
        self._log_unregistered_recordings()
        for stage_name, stage in self._stages(session_ids=session_ids):
            log("postprocess", f"stage={stage_name} starting")
            stage()
            log("postprocess", f"stage={stage_name} completed")
        log("postprocess", "completed")
        self._log_status_summary(session_ids=session_ids)

    def _stages(
        self,
        *,
        session_ids: set[str] | None,
    ) -> list[tuple[str, Callable[[], None]]]:
        return [
            (
                "stage-hints-semantic",
                lambda: self._run_stage(
                    SemanticStageHintService(self.settings),
                    session_ids=session_ids,
                ),
            ),
            (
                "segmenter",
                lambda: self._run_stage(SegmenterService(self.settings), session_ids=session_ids),
            ),
            (
                "subtitles",
                lambda: self._run_stage(SubtitleService(self.settings), session_ids=session_ids),
            ),
            (
                "highlight-planner",
                lambda: self._run_stage(
                    HighlightPlannerService(self.settings),
                    session_ids=session_ids,
                ),
            ),
            (
                "copywriter-semantic",
                lambda: self._run_copywriter_semantic(session_ids=session_ids),
            ),
            (
                "edit-planner",
                lambda: self._run_stage(
                    EditingPlannerService(self.settings),
                    session_ids=session_ids,
                ),
            ),
            (
                "exporter",
                lambda: self._run_stage(ExporterService(self.settings), session_ids=session_ids),
            ),
            (
                "copywriter",
                lambda: self._run_copywriter_publishing(session_ids=session_ids),
            ),
        ]

    @staticmethod
    def _run_stage(stage: Any, *, session_ids: set[str] | None) -> None:
        if session_ids is None:
            stage.run()
            return
        stage.run(session_ids=session_ids)

    def _run_copywriter_semantic(self, *, session_ids: set[str] | None) -> None:
        service = CopywriterService(self.settings)
        if session_ids is None:
            service.run_semantic()
            return
        service.run_semantic(session_ids=session_ids)

    def _run_copywriter_publishing(self, *, session_ids: set[str] | None) -> None:
        service = CopywriterService(self.settings)
        if session_ids is None:
            service.run_publishing()
            return
        service.run_publishing(session_ids=session_ids)

    def _log_unregistered_recordings(self) -> None:
        unregistered = RecordingAssetRepairService(self.settings).find_unregistered()
        if not unregistered:
            return
        sample = ", ".join(str(item.path) for item in unregistered[:3])
        log(
            "postprocess",
            "unregistered_recordings_found "
            f"count={len(unregistered)} sample={sample} "
            "hint=run `arl repair-recording-assets` before postprocess",
        )

    def _log_status_summary(self, *, session_ids: set[str] | None = None) -> None:
        try:
            from arl.status.service import StatusService

            status = StatusService(self.settings).build()
        except Exception as exc:
            log("postprocess", f"status summary unavailable reason={exc}")
            return

        summary = status["summary"]
        postprocess = status["postprocess"]
        log(
            "postprocess",
            "status "
            f"health={summary['health']} "
            f"match_boundaries={postprocess['match_boundaries']} "
            f"subtitle_assets={postprocess['subtitle_assets']} "
            f"highlight_plans={postprocess['highlight_plans']} "
            f"copywriter_semantic_assets={postprocess['copywriter_semantic_assets']} "
            f"edit_plans={postprocess['edit_plans']} "
            f"export_assets={postprocess['export_assets']} "
            f"copy_assets={postprocess['copy_assets']} "
            f"missing_subtitles={postprocess['missing_subtitles']} "
            f"missing_exports={postprocess['missing_exports']} "
            f"missing_copies={postprocess['missing_copies']} "
            f"unregistered_recordings={postprocess['unregistered_recordings']}",
        )
        if session_ids is not None:
            self._log_filtered_session_diagnostics(session_ids)

    def _log_filtered_session_diagnostics(self, session_ids: set[str]) -> None:
        temp_dir = self.settings.storage.temp_dir
        recording_assets = load_models(
            temp_dir / "recording-assets.jsonl",
            RecordingAsset,
        )
        boundaries = load_models(temp_dir / "match-boundaries.jsonl", MatchBoundary)
        export_assets = load_models(temp_dir / "export-assets.jsonl", ExportAsset)

        recordings_by_session: dict[str, list[RecordingAsset]] = {}
        boundaries_by_session: dict[str, list[MatchBoundary]] = {}
        exports_by_session: dict[str, list[ExportAsset]] = {}
        for asset in recording_assets:
            recordings_by_session.setdefault(asset.session_id, []).append(asset)
        for boundary in boundaries:
            boundaries_by_session.setdefault(boundary.session_id, []).append(boundary)
        for asset in export_assets:
            exports_by_session.setdefault(asset.session_id, []).append(asset)

        for session_id in sorted(session_ids):
            if exports_by_session.get(session_id):
                continue
            if not recordings_by_session.get(session_id):
                log(
                    "postprocess",
                    "no_export_for_session "
                    f"session_id={session_id} reason=recording_asset_missing "
                    "hint=run `arl repair-recording-assets` before postprocess",
                )
                continue

            session_boundaries = boundaries_by_session.get(session_id, [])
            if not session_boundaries:
                log(
                    "postprocess",
                    "no_export_for_session "
                    f"session_id={session_id} reason=no_match_boundaries "
                    "hint=segmenter produced no match boundary",
                )
                continue

            usable_boundaries = [
                boundary
                for boundary in session_boundaries
                if boundary.is_complete and boundary.confidence >= 0.8
            ]
            if usable_boundaries:
                log(
                    "postprocess",
                    "no_export_for_session "
                    f"session_id={session_id} reason=usable_boundary_without_export "
                    f"usable_boundaries={len(usable_boundaries)} "
                    "hint=check exporter-events.jsonl",
                )
                continue

            details = ";".join(
                self._boundary_diagnostic(boundary)
                for boundary in sorted(
                    session_boundaries,
                    key=lambda item: item.match_index,
                )
            )
            log(
                "postprocess",
                "no_export_for_session "
                f"session_id={session_id} reason=no_usable_match_boundary "
                f"boundaries={len(session_boundaries)} details={details} "
                "hint=no export is written until a complete boundary "
                "with confidence>=0.8 exists",
            )

    @staticmethod
    def _boundary_diagnostic(boundary: MatchBoundary) -> str:
        return (
            f"match_index={boundary.match_index},"
            f"complete={str(boundary.is_complete).lower()},"
            f"confidence={boundary.confidence:.2f},"
            f"reason={boundary.reason or 'unknown'}"
        )
