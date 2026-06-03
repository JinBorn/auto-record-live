from __future__ import annotations

import unittest

from arl.cli import build_parser


class CliUnattendedTest(unittest.TestCase):
    def test_postprocess_command_parses(self) -> None:
        args = build_parser().parse_args(["postprocess", "--once"])

        self.assertEqual(args.command, "postprocess")
        self.assertTrue(args.once)

    def test_status_command_parses(self) -> None:
        args = build_parser().parse_args(["status"])

        self.assertEqual(args.command, "status")

    def test_copywriter_command_parses(self) -> None:
        args = build_parser().parse_args(["copywriter"])

        self.assertEqual(args.command, "copywriter")

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


if __name__ == "__main__":
    unittest.main()
