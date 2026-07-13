from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from arl.cli import build_parser, main
from arl.config import Settings


class CliUnattendedTest(unittest.TestCase):
    def test_vision_analysis_command_parses_filters_and_force(self) -> None:
        args = build_parser().parse_args(
            [
                "vision-analysis",
                "--session-ids",
                "session-a,session-b",
                "--force-reprocess",
            ]
        )

        self.assertEqual(args.command, "vision-analysis")
        self.assertEqual(args.session_ids, "session-a,session-b")
        self.assertTrue(args.force_reprocess)

    def test_postprocess_command_parses(self) -> None:
        args = build_parser().parse_args(["postprocess", "--once"])

        self.assertEqual(args.command, "postprocess")
        self.assertTrue(args.once)
        self.assertFalse(args.publish)

    def test_postprocess_command_parses_publish_preset(self) -> None:
        args = build_parser().parse_args(["postprocess", "--once", "--publish"])

        self.assertEqual(args.command, "postprocess")
        self.assertTrue(args.once)
        self.assertTrue(args.publish)

    def test_postprocess_publish_preset_reaches_service_settings(self) -> None:
        captured: dict[str, Settings] = {}

        class _PostprocessStub:
            def __init__(self, settings: Settings) -> None:
                captured["settings"] = settings

            def run_once(self, *, session_ids=None) -> None:
                return None

        with patch("sys.argv", ["arl", "postprocess", "--once", "--publish"]), patch(
            "arl.cli.load_settings",
            return_value=Settings(),
        ), patch("arl.cli.PostProcessService", _PostprocessStub):
            self.assertEqual(main(), 0)

        settings = captured["settings"]
        self.assertEqual(settings.highlights.mode, "condensed")
        self.assertTrue(settings.editing.enabled)
        self.assertTrue(settings.editing.zoom_enabled)
        self.assertTrue(settings.editing.audio_mixing_enabled)
        self.assertTrue(settings.export.enable_ffmpeg)
        self.assertTrue(settings.export.burn_subtitles)
        self.assertTrue(settings.export.use_ass_subtitles)
        self.assertTrue(settings.export.use_edit_plans)
        self.assertTrue(settings.export.use_highlight_plans)

    def test_postprocess_command_parses_session_filters(self) -> None:
        args = build_parser().parse_args(
            [
                "postprocess",
                "--once",
                "--session-id",
                "session-a",
                "--session-ids",
                "session-b,session-c",
            ]
        )

        self.assertEqual(args.command, "postprocess")
        self.assertEqual(args.session_id, "session-a")
        self.assertEqual(args.session_ids, "session-b,session-c")

    def test_postprocess_reset_command_parses(self) -> None:
        args = build_parser().parse_args(
            [
                "postprocess-reset",
                "--session-id",
                "session-a",
                "--session-ids",
                "session-b,session-c",
                "--keep-files",
            ]
        )

        self.assertEqual(args.command, "postprocess-reset")
        self.assertEqual(args.session_id, "session-a")
        self.assertEqual(args.session_ids, "session-b,session-c")
        self.assertTrue(args.keep_files)

    def test_status_command_parses(self) -> None:
        args = build_parser().parse_args(["status"])

        self.assertEqual(args.command, "status")

    def test_copywriter_command_parses(self) -> None:
        args = build_parser().parse_args(["copywriter"])

        self.assertEqual(args.command, "copywriter")

    def test_copywriter_command_parses_filters(self) -> None:
        args = build_parser().parse_args(
            [
                "copywriter",
                "--session-id",
                "session-a",
                "--session-ids",
                "session-b,session-c",
                "--match-index",
                "2",
                "--match-indices",
                "3,4",
                "--force-reprocess",
            ]
        )

        self.assertEqual(args.command, "copywriter")
        self.assertEqual(args.session_id, "session-a")
        self.assertEqual(args.session_ids, "session-b,session-c")
        self.assertEqual(args.match_index, 2)
        self.assertEqual(args.match_indices, [3, 4])
        self.assertTrue(args.force_reprocess)

    def test_quality_report_command_parses_filters(self) -> None:
        args = build_parser().parse_args(
            [
                "quality-report",
                "--session-id",
                "session-a",
                "--session-ids",
                "session-b,session-c",
                "--match-index",
                "2",
                "--match-indices",
                "3,4",
                "--all-latest",
                "--strict",
                "--top-gaps",
                "3",
            ]
        )

        self.assertEqual(args.command, "quality-report")
        self.assertEqual(args.session_id, "session-a")
        self.assertEqual(args.session_ids, "session-b,session-c")
        self.assertEqual(args.match_index, 2)
        self.assertEqual(args.match_indices, [3, 4])
        self.assertTrue(args.all_latest)
        self.assertTrue(args.strict)
        self.assertEqual(args.top_gaps, 3)

    def test_quality_report_passes_filters_and_returns_service_exit_code(self) -> None:
        captured: dict[str, object] = {}

        class _QualityReportStub:
            def __init__(self, settings: Settings) -> None:
                return None

            def run(
                self,
                *,
                session_ids=None,
                match_indices=None,
                all_latest: bool = False,
                strict: bool = False,
                top_gaps: int | None = None,
            ):
                captured["session_ids"] = session_ids
                captured["match_indices"] = match_indices
                captured["all_latest"] = all_latest
                captured["strict"] = strict
                captured["top_gaps"] = top_gaps
                return SimpleNamespace(markdown="## Quality Report\n", exit_code=1)

        with patch(
            "sys.argv",
            [
                "arl",
                "quality-report",
                "--session-id",
                "session-a",
                "--session-ids",
                "session-b,session-c",
                "--match-index",
                "2",
                "--match-indices",
                "3,4",
                "--all-latest",
                "--strict",
                "--top-gaps",
                "3",
            ],
        ), patch("arl.cli.load_settings", return_value=Settings()), patch(
            "arl.cli.QualityReportService",
            _QualityReportStub,
        ):
            self.assertEqual(main(), 1)

        self.assertEqual(captured["session_ids"], {"session-a", "session-b", "session-c"})
        self.assertEqual(captured["match_indices"], {2, 3, 4})
        self.assertTrue(captured["all_latest"])
        self.assertTrue(captured["strict"])
        self.assertEqual(captured["top_gaps"], 3)

    def test_quality_report_requires_session_or_all_latest(self) -> None:
        with patch("sys.argv", ["arl", "quality-report"]), patch(
            "arl.cli.load_settings",
            return_value=Settings(),
        ):
            with self.assertRaises(SystemExit):
                main()

    def test_maintenance_command_parses(self) -> None:
        args = build_parser().parse_args(["maintenance", "--once"])

        self.assertEqual(args.command, "maintenance")
        self.assertTrue(args.once)

    def test_recovery_pending_report_command_parses(self) -> None:
        args = build_parser().parse_args(["recovery", "--pending-report"])

        self.assertEqual(args.command, "recovery")
        self.assertTrue(args.pending_report)

    def test_soak_command_parses(self) -> None:
        args = build_parser().parse_args(
            [
                "soak",
                "--cycles",
                "2",
                "--interval-seconds",
                "0",
                "--skip-recorder",
                "--maintenance",
            ]
        )

        self.assertEqual(args.command, "soak")
        self.assertEqual(args.cycles, 2)
        self.assertEqual(args.interval_seconds, 0.0)
        self.assertTrue(args.skip_recorder)
        self.assertTrue(args.maintenance)

    def test_record_rooms_command_parses_room_indices(self) -> None:
        args = build_parser().parse_args(
            [
                "record-rooms",
                "--room-indices",
                "1,3",
                "--max-concurrent-jobs",
                "2",
            ]
        )

        self.assertEqual(args.command, "record-rooms")
        self.assertEqual(args.room_indices, [1, 3])
        self.assertEqual(args.max_concurrent_jobs, 2)
        self.assertFalse(args.placeholder)

    def test_record_rooms_command_parses_all_live(self) -> None:
        args = build_parser().parse_args(["record-rooms", "--all-live", "--placeholder"])

        self.assertEqual(args.command, "record-rooms")
        self.assertTrue(args.all_live)
        self.assertTrue(args.placeholder)

    def test_repair_recording_assets_command_parses(self) -> None:
        args = build_parser().parse_args(
            ["repair-recording-assets", "--min-age-seconds", "10"]
        )

        self.assertEqual(args.command, "repair-recording-assets")
        self.assertEqual(args.min_age_seconds, 10.0)

    def test_segmenter_command_parses_filters(self) -> None:
        args = build_parser().parse_args(
            [
                "segmenter",
                "--session-id",
                "session-a",
                "--session-ids",
                "session-b,session-c",
            ]
        )

        self.assertEqual(args.command, "segmenter")
        self.assertEqual(args.session_id, "session-a")
        self.assertEqual(args.session_ids, "session-b,session-c")

    def test_exporter_command_parses_filters_and_force(self) -> None:
        args = build_parser().parse_args(
            [
                "exporter",
                "--session-id",
                "session-a",
                "--session-ids",
                "session-b,session-c",
                "--match-index",
                "2",
                "--match-indices",
                "3,4",
                "--force-reprocess",
            ]
        )

        self.assertEqual(args.command, "exporter")
        self.assertEqual(args.session_id, "session-a")
        self.assertEqual(args.session_ids, "session-b,session-c")
        self.assertEqual(args.match_index, 2)
        self.assertEqual(args.match_indices, [3, 4])
        self.assertTrue(args.force_reprocess)

    def test_highlight_planner_command_parses_filters(self) -> None:
        args = build_parser().parse_args(
            [
                "highlight-planner",
                "--session-id",
                "session-a",
                "--session-ids",
                "session-b,session-c",
                "--match-index",
                "2",
                "--match-indices",
                "3,4",
            ]
        )

        self.assertEqual(args.command, "highlight-planner")
        self.assertEqual(args.session_id, "session-a")
        self.assertEqual(args.session_ids, "session-b,session-c")
        self.assertEqual(args.match_index, 2)
        self.assertEqual(args.match_indices, [3, 4])

    def test_edit_planner_command_parses_filters(self) -> None:
        args = build_parser().parse_args(
            [
                "edit-planner",
                "--session-id",
                "session-a",
                "--session-ids",
                "session-b,session-c",
                "--match-index",
                "2",
                "--match-indices",
                "3,4",
                "--force-reprocess",
            ]
        )

        self.assertEqual(args.command, "edit-planner")
        self.assertEqual(args.session_id, "session-a")
        self.assertEqual(args.session_ids, "session-b,session-c")
        self.assertEqual(args.match_index, 2)
        self.assertEqual(args.match_indices, [3, 4])


if __name__ == "__main__":
    unittest.main()
