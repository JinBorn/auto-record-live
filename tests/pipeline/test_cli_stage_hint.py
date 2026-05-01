from __future__ import annotations

import sys
import unittest
from datetime import timezone
from pathlib import Path
from unittest.mock import patch

from arl.cli import build_parser, main
from arl.config import SegmenterSettings, Settings


class StageHintCliParserTest(unittest.TestCase):
    def test_stage_hints_auto_command_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["stage-hints-auto"])
        self.assertEqual(args.command, "stage-hints-auto")

    def test_stage_hints_semantic_command_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["stage-hints-semantic"])
        self.assertEqual(args.command, "stage-hints-semantic")

    def test_stage_hints_semantic_command_parses_stage_keywords_path(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "stage-hints-semantic",
                "--stage-keywords-path",
                "examples/stage-keywords.example.json",
            ]
        )
        self.assertEqual(args.command, "stage-hints-semantic")
        self.assertEqual(args.stage_keywords_path, Path("examples/stage-keywords.example.json"))

    def test_stage_signals_from_subtitles_command_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["stage-signals-from-subtitles"])
        self.assertEqual(args.command, "stage-signals-from-subtitles")

    def test_stage_signals_from_subtitles_command_parses_stage_keywords_path(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "stage-signals-from-subtitles",
                "--stage-keywords-path",
                "examples/stage-keywords.example.json",
            ]
        )
        self.assertEqual(args.command, "stage-signals-from-subtitles")
        self.assertEqual(args.stage_keywords_path, Path("examples/stage-keywords.example.json"))

    def test_stage_signals_from_subtitles_command_parses_force_reprocess(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "stage-signals-from-subtitles",
                "--force-reprocess",
            ]
        )
        self.assertEqual(args.command, "stage-signals-from-subtitles")
        self.assertTrue(args.force_reprocess)

    def test_stage_signals_from_subtitles_command_parses_filters(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "stage-signals-from-subtitles",
                "--session-id",
                "session-001",
                "--session-ids",
                "session-002, session-003",
                "--subtitle-path",
                "/tmp/a.srt",
                "--subtitle-paths",
                "/tmp/b.srt,/tmp/c.srt",
                "--match-index",
                "2",
                "--match-indices",
                "3, 4",
            ]
        )
        self.assertEqual(args.command, "stage-signals-from-subtitles")
        self.assertEqual(args.session_id, "session-001")
        self.assertEqual(args.session_ids, "session-002, session-003")
        self.assertEqual(args.subtitle_path, Path("/tmp/a.srt"))
        self.assertEqual(args.subtitle_paths, "/tmp/b.srt,/tmp/c.srt")
        self.assertEqual(args.match_index, 2)
        self.assertEqual(args.match_indices, [3, 4])

    def test_stage_signals_from_subtitles_command_rejects_invalid_match_indices(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "stage-signals-from-subtitles",
                    "--match-indices",
                    "1,abc",
                ]
            )

    def test_stage_signals_from_subtitles_command_rejects_non_positive_match_index(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "stage-signals-from-subtitles",
                    "--match-index",
                    "0",
                ]
            )
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "stage-signals-from-subtitles",
                    "--match-indices",
                    "1,-2",
                ]
            )

    def test_subtitles_command_parses_stage_keywords_path(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "subtitles",
                "--stage-keywords-path",
                "examples/stage-keywords.example.json",
            ]
        )
        self.assertEqual(args.command, "subtitles")
        self.assertEqual(args.stage_keywords_path, Path("examples/stage-keywords.example.json"))

    def test_subtitles_command_parses_filters(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "subtitles",
                "--session-id",
                "session-sub-1",
                "--session-ids",
                "session-sub-2, session-sub-3",
                "--match-index",
                "2",
                "--match-indices",
                "3,4",
            ]
        )
        self.assertEqual(args.command, "subtitles")
        self.assertEqual(args.session_id, "session-sub-1")
        self.assertEqual(args.session_ids, "session-sub-2, session-sub-3")
        self.assertEqual(args.match_index, 2)
        self.assertEqual(args.match_indices, [3, 4])

    def test_subtitles_command_rejects_invalid_match_indices(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "subtitles",
                    "--match-indices",
                    "2,abc",
                ]
            )
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "subtitles",
                    "--match-index",
                    "0",
                ]
            )

    def test_stage_signal_detected_at_without_timezone_defaults_to_utc(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "stage-signal",
                "--session-id",
                "session-cli-signal-001",
                "--text",
                "in game scoreboard",
                "--detected-at",
                "2026-04-26T13:25:00",
            ]
        )
        self.assertEqual(args.command, "stage-signal")
        self.assertIsNotNone(args.detected_at)
        self.assertEqual(args.detected_at.tzinfo, timezone.utc)

    def test_stage_hint_detected_at_without_timezone_defaults_to_utc(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "stage-hint",
                "--session-id",
                "session-cli-001",
                "--stage",
                "in_game",
                "--detected-at",
                "2026-04-26T13:20:00",
            ]
        )
        self.assertEqual(args.command, "stage-hint")
        self.assertIsNotNone(args.detected_at)
        self.assertEqual(args.detected_at.tzinfo, timezone.utc)

    def test_stage_hint_invalid_detected_at_rejected(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "stage-hint",
                    "--session-id",
                    "session-cli-002",
                    "--stage",
                    "loading",
                    "--detected-at",
                    "not-a-datetime",
                ]
            )

    def test_stage_signal_requires_timestamp(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "stage-signal",
                    "--session-id",
                    "session-cli-signal-002",
                    "--text",
                    "loading",
                ]
            )


class StageKeywordPathCliOverrideTest(unittest.TestCase):
    def _settings_with_env_stage_keywords(self, env_path: Path) -> Settings:
        return Settings(
            segmenter=SegmenterSettings(stage_keywords_path=env_path),
        )

    def _run_command_and_capture_path(
        self,
        argv: list[str],
        *,
        service_patch_target: str,
        env_path: Path,
    ) -> Path | None:
        captured: dict[str, Path | None] = {}

        class _FakeService:
            def __init__(self, settings: Settings) -> None:
                captured["path"] = settings.segmenter.stage_keywords_path

            def run(
                self,
                *,
                force_reprocess: bool = False,
                session_ids: set[str] | None = None,
                subtitle_paths: set[Path] | None = None,
                match_indices: set[int] | None = None,
            ) -> None:
                return None

        with patch.object(sys, "argv", argv), patch(
            "arl.cli.load_settings",
            return_value=self._settings_with_env_stage_keywords(env_path),
        ), patch(service_patch_target, _FakeService):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        return captured.get("path")

    def test_stage_hints_semantic_cli_path_overrides_loaded_settings(self) -> None:
        cli_path = Path("/tmp/cli-stage-keywords-semantic.json")
        actual = self._run_command_and_capture_path(
            [
                "arl",
                "stage-hints-semantic",
                "--stage-keywords-path",
                str(cli_path),
            ],
            service_patch_target="arl.cli.SemanticStageHintService",
            env_path=Path("/tmp/env-stage-keywords-semantic.json"),
        )
        self.assertEqual(actual, cli_path)

    def test_stage_hints_semantic_uses_loaded_settings_when_cli_path_not_set(self) -> None:
        env_path = Path("/tmp/env-stage-keywords-semantic-no-cli.json")
        actual = self._run_command_and_capture_path(
            [
                "arl",
                "stage-hints-semantic",
            ],
            service_patch_target="arl.cli.SemanticStageHintService",
            env_path=env_path,
        )
        self.assertEqual(actual, env_path)

    def test_stage_signals_from_subtitles_cli_path_overrides_loaded_settings(self) -> None:
        cli_path = Path("/tmp/cli-stage-keywords-signals.json")
        actual = self._run_command_and_capture_path(
            [
                "arl",
                "stage-signals-from-subtitles",
                "--stage-keywords-path",
                str(cli_path),
            ],
            service_patch_target="arl.cli.StageSignalFromSubtitlesService",
            env_path=Path("/tmp/env-stage-keywords-signals.json"),
        )
        self.assertEqual(actual, cli_path)

    def test_stage_signals_from_subtitles_passes_force_reprocess(self) -> None:
        captured: dict[str, object] = {}

        class _FakeService:
            def __init__(self, settings: Settings) -> None:
                return None

            def run(
                self,
                *,
                force_reprocess: bool = False,
                session_ids: set[str] | None = None,
                subtitle_paths: set[Path] | None = None,
                match_indices: set[int] | None = None,
            ) -> None:
                captured["force_reprocess"] = force_reprocess
                captured["session_ids"] = session_ids
                captured["subtitle_paths"] = subtitle_paths
                captured["match_indices"] = match_indices

        with patch.object(
            sys,
            "argv",
            [
                "arl",
                "stage-signals-from-subtitles",
                "--force-reprocess",
                "--session-id",
                "session-a",
                "--session-ids",
                "session-b,session-c",
                "--subtitle-path",
                "/tmp/a.srt",
                "--subtitle-paths",
                "/tmp/b.srt,/tmp/c.srt",
                "--match-index",
                "2",
                "--match-indices",
                "3,4",
            ],
        ), patch(
            "arl.cli.load_settings",
            return_value=self._settings_with_env_stage_keywords(
                Path("/tmp/env-stage-keywords-signals-force.json")
            ),
        ), patch("arl.cli.StageSignalFromSubtitlesService", _FakeService):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertTrue(captured.get("force_reprocess", False))
        self.assertEqual(
            captured.get("session_ids"),
            {"session-a", "session-b", "session-c"},
        )
        self.assertEqual(
            captured.get("subtitle_paths"),
            {Path("/tmp/a.srt"), Path("/tmp/b.srt"), Path("/tmp/c.srt")},
        )
        self.assertEqual(captured.get("match_indices"), {2, 3, 4})

    def test_subtitles_cli_path_overrides_loaded_settings(self) -> None:
        cli_path = Path("/tmp/cli-stage-keywords-subtitles.json")
        actual = self._run_command_and_capture_path(
            [
                "arl",
                "subtitles",
                "--stage-keywords-path",
                str(cli_path),
            ],
            service_patch_target="arl.cli.SubtitleService",
            env_path=Path("/tmp/env-stage-keywords-subtitles.json"),
        )
        self.assertEqual(actual, cli_path)

    def test_subtitles_passes_filters(self) -> None:
        captured: dict[str, object] = {}

        class _FakeService:
            def __init__(self, settings: Settings) -> None:
                return None

            def run(
                self,
                *,
                session_ids: set[str] | None = None,
                match_indices: set[int] | None = None,
            ) -> None:
                captured["session_ids"] = session_ids
                captured["match_indices"] = match_indices

        with patch.object(
            sys,
            "argv",
            [
                "arl",
                "subtitles",
                "--session-id",
                "session-a",
                "--session-ids",
                "session-b,session-c",
                "--match-index",
                "2",
                "--match-indices",
                "3,4",
            ],
        ), patch(
            "arl.cli.load_settings",
            return_value=self._settings_with_env_stage_keywords(
                Path("/tmp/env-stage-keywords-subtitles-filters.json")
            ),
        ), patch("arl.cli.SubtitleService", _FakeService):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured.get("session_ids"), {"session-a", "session-b", "session-c"})
        self.assertEqual(captured.get("match_indices"), {2, 3, 4})


if __name__ == "__main__":
    unittest.main()
