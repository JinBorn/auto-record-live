from __future__ import annotations

from collections.abc import Callable

from arl.config import Settings
from arl.copywriter.service import CopywriterService
from arl.exporter.service import ExporterService
from arl.recorder.asset_repair import RecordingAssetRepairService
from arl.segmenter.semantic_hints import SemanticStageHintService
from arl.segmenter.service import SegmenterService
from arl.shared.logging import log
from arl.subtitles.service import SubtitleService


class PostProcessService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run_once(self) -> None:
        log("postprocess", "starting")
        self._log_unregistered_recordings()
        for stage_name, stage in self._stages():
            log("postprocess", f"stage={stage_name} starting")
            stage()
            log("postprocess", f"stage={stage_name} completed")
        log("postprocess", "completed")
        self._log_status_summary()

    def _stages(self) -> list[tuple[str, Callable[[], None]]]:
        return [
            (
                "stage-hints-semantic",
                lambda: SemanticStageHintService(self.settings).run(),
            ),
            ("segmenter", lambda: SegmenterService(self.settings).run()),
            ("subtitles", lambda: SubtitleService(self.settings).run()),
            ("exporter", lambda: ExporterService(self.settings).run()),
            ("copywriter", lambda: CopywriterService(self.settings).run()),
        ]

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
            f"export_assets={postprocess['export_assets']} "
            f"copy_assets={postprocess['copy_assets']} "
            f"missing_subtitles={postprocess['missing_subtitles']} "
            f"missing_exports={postprocess['missing_exports']} "
            f"missing_copies={postprocess['missing_copies']} "
            f"unregistered_recordings={postprocess['unregistered_recordings']}",
        )
