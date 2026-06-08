from __future__ import annotations

import unittest
from unittest.mock import patch

from arl.config import Settings
from arl.postprocess.service import PostProcessService


class _StageStub:
    def __init__(self, calls: list[str], name: str) -> None:
        self.calls = calls
        self.name = name

    def run(self, **kwargs) -> None:
        self.calls.append(self.name)


class _FilteredStageStub:
    def __init__(self, calls: list[tuple[str, set[str] | None]], name: str) -> None:
        self.calls = calls
        self.name = name

    def run(self, *, session_ids: set[str] | None = None) -> None:
        self.calls.append((self.name, session_ids))


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

    def test_run_once_passes_session_filters_to_stages(self) -> None:
        calls: list[tuple[str, set[str] | None]] = []
        settings = Settings()

        with patch(
            "arl.postprocess.service.SemanticStageHintService",
            side_effect=lambda _: _FilteredStageStub(calls, "stage-hints-semantic"),
        ), patch(
            "arl.postprocess.service.SegmenterService",
            side_effect=lambda _: _FilteredStageStub(calls, "segmenter"),
        ), patch(
            "arl.postprocess.service.SubtitleService",
            side_effect=lambda _: _FilteredStageStub(calls, "subtitles"),
        ), patch(
            "arl.postprocess.service.ExporterService",
            side_effect=lambda _: _FilteredStageStub(calls, "exporter"),
        ), patch(
            "arl.postprocess.service.CopywriterService",
            side_effect=lambda _: _FilteredStageStub(calls, "copywriter"),
        ):
            PostProcessService(settings).run_once(session_ids={"session-a", "session-b"})

        self.assertEqual(
            calls,
            [
                ("stage-hints-semantic", {"session-a", "session-b"}),
                ("segmenter", {"session-a", "session-b"}),
                ("subtitles", {"session-a", "session-b"}),
                ("exporter", {"session-a", "session-b"}),
                ("copywriter", {"session-a", "session-b"}),
            ],
        )


if __name__ == "__main__":
    unittest.main()
