from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from arl.config import Settings, StorageSettings
from arl.postprocess.service import PostProcessService
from arl.shared.contracts import MatchBoundary, RecordingAsset, SourceType
from arl.shared.jsonl_store import append_model


_NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)


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
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

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
            "arl.postprocess.service.HighlightPlannerService",
            side_effect=lambda _: _StageStub(calls, "highlight-planner"),
        ), patch(
            "arl.postprocess.service.EditingPlannerService",
            side_effect=lambda _: _StageStub(calls, "edit-planner"),
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
            [
                "stage-hints-semantic",
                "segmenter",
                "subtitles",
                "highlight-planner",
                "edit-planner",
                "exporter",
                "copywriter",
            ],
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
            "arl.postprocess.service.HighlightPlannerService",
            side_effect=lambda _: _FilteredStageStub(calls, "highlight-planner"),
        ), patch(
            "arl.postprocess.service.EditingPlannerService",
            side_effect=lambda _: _FilteredStageStub(calls, "edit-planner"),
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
                ("highlight-planner", {"session-a", "session-b"}),
                ("edit-planner", {"session-a", "session-b"}),
                ("exporter", {"session-a", "session-b"}),
                ("copywriter", {"session-a", "session-b"}),
            ],
        )

    def test_run_once_logs_no_usable_boundary_for_filtered_session(self) -> None:
        session_id = "session-incomplete"
        settings = Settings(
            storage=StorageSettings(
                temp_dir=self.temp_root,
                raw_dir=self.temp_root / "raw",
                processed_dir=self.temp_root / "processed",
                export_dir=self.temp_root / "exports",
            )
        )
        append_model(
            self.temp_root / "recording-assets.jsonl",
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.DIRECT_STREAM,
                path=str(self.temp_root / "raw" / session_id / "recording-source.mp4"),
                started_at=_NOW,
                ended_at=_NOW,
            ),
        )
        append_model(
            self.temp_root / "match-boundaries.jsonl",
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=2800.0,
                confidence=0.4,
                is_complete=False,
                reason="incomplete_no_end",
            ),
        )
        log_rows: list[tuple[str, str]] = []

        with self._patch_filtered_stages([]), patch(
            "arl.postprocess.service.log",
            side_effect=lambda component, message: log_rows.append(
                (component, message)
            ),
        ):
            PostProcessService(settings).run_once(session_ids={session_id})

        messages = [message for component, message in log_rows if component == "postprocess"]
        diagnostic = "\n".join(messages)
        self.assertIn("reason=no_usable_match_boundary", diagnostic)
        self.assertIn("reason=incomplete_no_end", diagnostic)
        self.assertIn("confidence=0.40", diagnostic)
        self.assertIn("no export is written", diagnostic)

    def _patch_filtered_stages(self, calls: list[tuple[str, set[str] | None]]):
        return patch.multiple(
            "arl.postprocess.service",
            SemanticStageHintService=lambda _: _FilteredStageStub(
                calls,
                "stage-hints-semantic",
            ),
            SegmenterService=lambda _: _FilteredStageStub(calls, "segmenter"),
            SubtitleService=lambda _: _FilteredStageStub(calls, "subtitles"),
            HighlightPlannerService=lambda _: _FilteredStageStub(
                calls,
                "highlight-planner",
            ),
            EditingPlannerService=lambda _: _FilteredStageStub(calls, "edit-planner"),
            ExporterService=lambda _: _FilteredStageStub(calls, "exporter"),
            CopywriterService=lambda _: _FilteredStageStub(calls, "copywriter"),
        )


if __name__ == "__main__":
    unittest.main()
