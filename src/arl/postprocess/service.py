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
        self._log_status_summary()

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
                lambda: self._run_stage(CopywriterService(self.settings), session_ids=session_ids),
            ),
        ]

    @staticmethod
    def _run_stage(stage: Any, *, session_ids: set[str] | None) -> None:
        if session_ids is None:
            stage.run()
            return
        stage.run(session_ids=session_ids)

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

    def _log_status_summary(self) -> None:
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
            f"edit_plans={postprocess['edit_plans']} "
            f"export_assets={postprocess['export_assets']} "
            f"copy_assets={postprocess['copy_assets']} "
            f"missing_subtitles={postprocess['missing_subtitles']} "
            f"missing_exports={postprocess['missing_exports']} "
            f"missing_copies={postprocess['missing_copies']} "
            f"unregistered_recordings={postprocess['unregistered_recordings']}",
        )
