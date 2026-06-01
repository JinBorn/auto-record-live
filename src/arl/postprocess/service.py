from __future__ import annotations

from collections.abc import Callable

from arl.config import Settings
from arl.exporter.service import ExporterService
from arl.segmenter.semantic_hints import SemanticStageHintService
from arl.segmenter.service import SegmenterService
from arl.shared.logging import log
from arl.subtitles.service import SubtitleService


class PostProcessService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run_once(self) -> None:
        log("postprocess", "starting")
        for stage_name, stage in self._stages():
            log("postprocess", f"stage={stage_name} starting")
            stage()
            log("postprocess", f"stage={stage_name} completed")
        log("postprocess", "completed")

    def _stages(self) -> list[tuple[str, Callable[[], None]]]:
        return [
            (
                "stage-hints-semantic",
                lambda: SemanticStageHintService(self.settings).run(),
            ),
            ("segmenter", lambda: SegmenterService(self.settings).run()),
            ("subtitles", lambda: SubtitleService(self.settings).run()),
            ("exporter", lambda: ExporterService(self.settings).run()),
        ]

