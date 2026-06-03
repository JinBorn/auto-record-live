from __future__ import annotations

import unittest
from unittest.mock import patch

from arl.config import Settings
from arl.postprocess.service import PostProcessService


class _StageStub:
    def __init__(self, calls: list[str], name: str) -> None:
        self.calls = calls
        self.name = name

    def run(self) -> None:
        self.calls.append(self.name)


class PostProcessServiceTest(unittest.TestCase):
    def test_run_once_invokes_stages_in_order(self) -> None:
        calls: list[str] = []
        settings = Settings()

        with patch(
            "arl.postprocess.service.SemanticStageHintService",
            side_effect=lambda _: _StageStub(calls, "stage-hints-semantic"),
        ), patch(
            "arl.postprocess.service.SegmenterService",
            side_effect=lambda _: _StageStub(calls, "segmenter"),
        ), patch(
            "arl.postprocess.service.SubtitleService",
            side_effect=lambda _: _StageStub(calls, "subtitles"),
        ), patch(
            "arl.postprocess.service.ExporterService",
            side_effect=lambda _: _StageStub(calls, "exporter"),
        ), patch(
            "arl.postprocess.service.CopywriterService",
            side_effect=lambda _: _StageStub(calls, "copywriter"),
        ):
            PostProcessService(settings).run_once()

        self.assertEqual(
            calls,
            ["stage-hints-semantic", "segmenter", "subtitles", "exporter", "copywriter"],
        )


if __name__ == "__main__":
    unittest.main()
