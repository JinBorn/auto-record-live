from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from arl.config import apply_publish_preset, load_settings
from arl.copywriter.service import CopywriterService
from arl.editing.service import EditingPlannerService
from arl.exporter.service import ExporterService
from arl.highlights.service import HighlightPlannerService
from arl.maintenance.service import MaintenanceService
from arl.orchestrator.service import OrchestratorService
from arl.postprocess.reset import PostProcessResetService
from arl.postprocess.service import PostProcessService
from arl.quality_report.service import QualityReportService
from arl.recovery.service import RecoveryService
from arl.recorder.asset_repair import RecordingAssetRepairService
from arl.recorder.service import RecorderService
from arl.segmenter.auto_hints import AutoStageHintService
from arl.segmenter.hints import StageHintWriter
from arl.segmenter.signals import StageSignalWriter
from arl.segmenter.signals_from_subtitles import StageSignalFromSubtitlesService
from arl.segmenter.semantic_hints import SemanticStageHintService
from arl.segmenter.service import SegmenterService
from arl.selected_recording.service import SelectedRecordingService
from arl.shared.contracts import MatchStage
from arl.soak.service import SoakService
from arl.status.service import StatusService
from arl.subtitles.service import SubtitleService
from arl.windows_agent.cookie_health import (
    build_cookie_health_probes,
    load_cookie_health_live_room_keys,
    run_cookie_health,
)
from arl.windows_agent.live_status import LiveStatusReport, run_live_status
from arl.vision_analysis.service import VisionAnalysisService
from arl.windows_agent.registry import build_probes
from arl.windows_agent.service import WindowsAgentService


def _parse_csv_values(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _collect_session_ids(args: argparse.Namespace) -> set[str] | None:
    session_ids: set[str] | None = None
    if getattr(args, "session_id", None) or getattr(args, "session_ids", None):
        session_ids = set()
        session_id = getattr(args, "session_id", None)
        if session_id:
            session_ids.add(session_id)
        raw_session_ids = getattr(args, "session_ids", None)
        if raw_session_ids:
            session_ids.update(_parse_csv_values(raw_session_ids))
        if not session_ids:
            session_ids = None
    return session_ids


def _parse_positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer value: {raw}") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError(f"value must be > 0: {raw}")
    return value


def _parse_csv_int_values(raw: str) -> list[int]:
    return [_parse_positive_int(item) for item in _parse_csv_values(raw)]


def _parse_iso_datetime(raw: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO datetime: {raw}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_live_status_text(report: LiveStatusReport) -> str:
    lines: list[str] = []
    for row in report.rows:
        lines.append(
            " ".join(
                [
                    f"index={row.index}",
                    f"platform={row.platform}",
                    f"state={row.state}",
                    f"streamer_name={row.streamer_name or 'n/a'}",
                    f"room_url={row.room_url or 'n/a'}",
                    f"source_type={row.source_type or 'none'}",
                    f"reason={row.reason}",
                ]
            )
        )
    summary = report.as_dict()["summary"]
    lines.append(
        " ".join(
            [
                "summary=live_status",
                f"total={summary['total']}",
                f"live={summary['live']}",
                f"offline={summary['offline']}",
                f"error={summary['error']}",
            ]
        )
    )
    return "\n".join(lines)


def _resolve_settings(
    args: argparse.Namespace,
):
    settings = load_settings()
    stage_keywords_path = getattr(args, "stage_keywords_path", None)
    if stage_keywords_path is not None:
        settings = settings.model_copy(
            deep=True,
            update={
                "segmenter": settings.segmenter.model_copy(
                    update={"stage_keywords_path": stage_keywords_path}
                )
            },
        )
    if getattr(args, "publish", False):
        settings = apply_publish_preset(settings)
    return settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arl")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("show-config", help="Print resolved settings.")
    subparsers.add_parser("status", help="Print local pipeline health/status summary.")
    live_status = subparsers.add_parser(
        "live-status",
        help="Probe configured live rooms once and list whether each room is live.",
    )
    live_status.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of line-oriented text.",
    )
    maintenance = subparsers.add_parser(
        "maintenance",
        help="Run local long-run maintenance once.",
    )
    maintenance.add_argument(
        "--once",
        action="store_true",
        help="Run one maintenance pass and exit (default behavior).",
    )
    soak = subparsers.add_parser(
        "soak",
        help="Run repeated unattended pipeline health cycles.",
    )
    soak.add_argument(
        "--cycles",
        type=_parse_positive_int,
        default=3,
        help="Number of soak cycles to run (default: 3).",
    )
    soak.add_argument(
        "--interval-seconds",
        type=float,
        default=30.0,
        help="Sleep seconds between cycles (default: 30).",
    )
    soak.add_argument(
        "--skip-recorder",
        action="store_true",
        help="Skip recorder stage during soak cycles.",
    )
    soak.add_argument(
        "--skip-postprocess",
        action="store_true",
        help="Skip postprocess stage during soak cycles.",
    )
    soak.add_argument(
        "--maintenance",
        action="store_true",
        help="Run maintenance during each soak cycle.",
    )
    windows_agent = subparsers.add_parser("windows-agent", help="Run the Windows agent.")
    windows_agent.add_argument(
        "--once",
        action="store_true",
        help="Probe once and emit state if it changed.",
    )
    subparsers.add_parser(
        "cookie-health",
        help=(
            "Run one detection cycle per platform credential and report cookie status "
            "(fresh / expired / not_configured / error). Exits non-zero if "
            "any configured cookie is detected expired."
        ),
    )
    orchestrator = subparsers.add_parser(
        "orchestrator",
        help="Consume Windows agent events and maintain session/job state.",
    )
    orchestrator.add_argument(
        "--once",
        action="store_true",
        help="Process new events once and exit.",
    )
    subparsers.add_parser("recorder", help="Run the recorder worker.")
    repair_recording_assets = subparsers.add_parser(
        "repair-recording-assets",
        help="Register completed raw MP4 recordings that are missing from recording-assets.jsonl.",
    )
    repair_recording_assets.add_argument(
        "--min-age-seconds",
        type=float,
        default=60.0,
        help="Only repair raw MP4 files not modified for this many seconds (default: 60).",
    )
    record_rooms = subparsers.add_parser(
        "record-rooms",
        help="Probe and record selected configured rooms by live-status index.",
    )
    record_room_selector = record_rooms.add_mutually_exclusive_group(required=True)
    record_room_selector.add_argument(
        "--room-index",
        type=_parse_positive_int,
        help="Record one configured room by the 1-based index shown by live-status.",
    )
    record_room_selector.add_argument(
        "--room-indices",
        type=_parse_csv_int_values,
        help="Record comma-separated room indices shown by live-status, e.g. 1,3.",
    )
    record_room_selector.add_argument(
        "--all-live",
        action="store_true",
        help="Probe all configured rooms and record the ones currently live.",
    )
    record_rooms.add_argument(
        "--max-concurrent-jobs",
        type=_parse_positive_int,
        help="Override ARL_RECORDER_MAX_CONCURRENT_JOBS for this selected run.",
    )
    record_rooms.add_argument(
        "--placeholder",
        action="store_true",
        help="Do not force real ffmpeg recording for this selected run.",
    )
    recovery = subparsers.add_parser("recovery", help="Dispatch manual recovery actions.")
    recovery_mode = recovery.add_mutually_exclusive_group()
    recovery_mode.add_argument(
        "--resolve-job-id",
        help="Mark all pending recovery actions for this job as resolved.",
    )
    recovery_mode.add_argument(
        "--resolve-job-ids",
        help="Mark pending recovery actions as resolved for comma-separated job ids.",
    )
    recovery_mode.add_argument(
        "--fail-job-id",
        help="Mark all pending recovery actions for this job as failed.",
    )
    recovery_mode.add_argument(
        "--fail-job-ids",
        help="Mark pending recovery actions as failed for comma-separated job ids.",
    )
    recovery_mode.add_argument(
        "--resolve-action-key",
        help="Mark one pending recovery action as resolved by action key.",
    )
    recovery_mode.add_argument(
        "--fail-action-key",
        help="Mark one pending recovery action as failed by action key.",
    )
    recovery_mode.add_argument(
        "--list-pending",
        action="store_true",
        help="List all pending dispatched recovery actions.",
    )
    recovery_mode.add_argument(
        "--pending-report",
        action="store_true",
        help="Show pending recovery actions grouped by job with operator commands.",
    )
    recovery_mode.add_argument(
        "--summary",
        action="store_true",
        help="Show aggregated recovery action status summary.",
    )
    recovery_mode.add_argument(
        "--maintenance",
        action="store_true",
        help="Archive terminal recovery events and compact terminal actions/state.",
    )
    recovery.add_argument(
        "--message",
        help="Optional operator message for resolved/failed status updates.",
    )
    segmenter = subparsers.add_parser("segmenter", help="Run the match segmenter worker.")
    segmenter.add_argument(
        "--session-id",
        help="Only segment one session id.",
    )
    segmenter.add_argument(
        "--session-ids",
        help="Only segment comma-separated session ids.",
    )
    segmenter.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Rebuild existing match boundaries for matched sessions.",
    )
    detect_matches = subparsers.add_parser(
        "detect-matches",
        help="Detect match segments from raw recordings using vision.",
    )
    detect_matches.add_argument(
        "--session-id",
        help="Session ID to detect matches for.",
    )
    detect_matches.add_argument(
        "--session-ids",
        help="Comma-separated list of session IDs.",
    )
    detect_matches.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Force re-detection even if cached.",
    )
    postprocess = subparsers.add_parser(
        "postprocess",
        help="Run the post-live processing chain once.",
    )
    postprocess.add_argument(
        "--once",
        action="store_true",
        help="Run one post-processing pass and exit (default behavior).",
    )
    postprocess.add_argument(
        "--session-id",
        help="Only run postprocess stages for one session id.",
    )
    postprocess.add_argument(
        "--session-ids",
        help="Only run postprocess stages for comma-separated session ids.",
    )
    postprocess.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Use the publish-edit preset: condensed highlights, edit plans, "
            "burned ASS subtitles, zoom, and low-volume BGM/SFX."
        ),
    )
    postprocess_reset = subparsers.add_parser(
        "postprocess-reset",
        help=(
            "Remove generated postprocess manifests/state for session(s) so "
            "they can be processed again."
        ),
    )
    postprocess_reset.add_argument(
        "--session-id",
        help="Reset generated postprocess data for one session id.",
    )
    postprocess_reset.add_argument(
        "--session-ids",
        help="Reset generated postprocess data for comma-separated session ids.",
    )
    postprocess_reset.add_argument(
        "--keep-files",
        action="store_true",
        help="Only reset manifests/state; do not delete generated subtitle/export/copy files.",
    )
    vision_analysis = subparsers.add_parser(
        "vision-analysis",
        help="Run the shared recording-scoped visual analysis stage.",
    )
    vision_analysis.add_argument("--session-id", help="Only analyze one session id.")
    vision_analysis.add_argument("--session-ids", help="Only analyze comma-separated session ids.")
    vision_analysis.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Append replacement visual assets even when a compatible cache exists.",
    )
    subparsers.add_parser(
        "stage-hints-auto",
        help="Auto-generate in_game stage hints from recording duration heuristics.",
    )
    stage_hints_semantic = subparsers.add_parser(
        "stage-hints-semantic",
        help="Auto-generate semantic stage hints (champion/loading/in_game/post_game).",
    )
    stage_hints_semantic.add_argument(
        "--stage-keywords-path",
        type=Path,
        help=(
            "Optional stage-keyword override JSON path. "
            "When set, this command prefers CLI path over ARL_STAGE_KEYWORDS_PATH."
        ),
    )
    stage_signals_from_subtitles = subparsers.add_parser(
        "stage-signals-from-subtitles",
        help="Extract semantic stage signals from subtitle assets.",
    )
    stage_signals_from_subtitles.add_argument(
        "--stage-keywords-path",
        type=Path,
        help=(
            "Optional stage-keyword override JSON path. "
            "When set, this command prefers CLI path over ARL_STAGE_KEYWORDS_PATH."
        ),
    )
    stage_signals_from_subtitles.add_argument(
        "--force-reprocess",
        action="store_true",
        help=(
            "Re-read subtitle assets even if already processed. "
            "Previously emitted identical signals stay deduplicated."
        ),
    )
    stage_signals_from_subtitles.add_argument(
        "--session-id",
        help="Only process subtitle assets for one session id.",
    )
    stage_signals_from_subtitles.add_argument(
        "--session-ids",
        help="Only process subtitle assets for comma-separated session ids.",
    )
    stage_signals_from_subtitles.add_argument(
        "--subtitle-path",
        type=Path,
        help="Only process subtitle assets matching one subtitle path.",
    )
    stage_signals_from_subtitles.add_argument(
        "--subtitle-paths",
        help="Only process subtitle assets matching comma-separated subtitle paths.",
    )
    stage_signals_from_subtitles.add_argument(
        "--match-index",
        type=_parse_positive_int,
        help="Only process subtitle assets for one match index.",
    )
    stage_signals_from_subtitles.add_argument(
        "--match-indices",
        type=_parse_csv_int_values,
        help="Only process subtitle assets for comma-separated match indices.",
    )
    stage_hint = subparsers.add_parser(
        "stage-hint",
        help="Append one match stage hint for segmenter.",
    )
    stage_hint.add_argument("--session-id", required=True, help="Target session id.")
    stage_hint.add_argument(
        "--stage",
        required=True,
        choices=[stage.value for stage in MatchStage],
        help="LoL match stage value.",
    )
    timestamp_group = stage_hint.add_mutually_exclusive_group(required=True)
    timestamp_group.add_argument(
        "--at-seconds",
        type=float,
        help="Relative seconds from recording start.",
    )
    timestamp_group.add_argument(
        "--detected-at",
        type=_parse_iso_datetime,
        help="Absolute ISO timestamp (for example 2026-04-26T12:30:00+00:00).",
    )
    stage_signal = subparsers.add_parser(
        "stage-signal",
        help="Append one semantic stage signal for semantic hint generation.",
    )
    stage_signal.add_argument("--session-id", required=True, help="Target session id.")
    stage_signal.add_argument("--text", required=True, help="Observed semantic signal text.")
    stage_signal.add_argument(
        "--source",
        default="manual",
        help="Signal source tag (default: manual).",
    )
    signal_timestamp_group = stage_signal.add_mutually_exclusive_group(required=True)
    signal_timestamp_group.add_argument(
        "--at-seconds",
        type=float,
        help="Relative seconds from recording start.",
    )
    signal_timestamp_group.add_argument(
        "--detected-at",
        type=_parse_iso_datetime,
        help="Absolute ISO timestamp (for example 2026-04-26T12:30:00+00:00).",
    )
    subtitles = subparsers.add_parser("subtitles", help="Run the subtitle worker.")
    subtitles.add_argument(
        "--force-reprocess",
        action="store_true",
        help=(
            "Regenerate targeted subtitle assets even when subtitle state and files "
            "already exist. Subtitle-derived stage signals are re-ingested with "
            "deduplication."
        ),
    )
    subtitles.add_argument(
        "--stage-keywords-path",
        type=Path,
        help=(
            "Optional stage-keyword override JSON path used by subtitle-triggered "
            "stage-signal ingest. When set, this command prefers CLI path over "
            "ARL_STAGE_KEYWORDS_PATH."
        ),
    )
    subtitles.add_argument(
        "--session-id",
        help="Only process match boundaries for one session id.",
    )
    subtitles.add_argument(
        "--session-ids",
        help="Only process match boundaries for comma-separated session ids.",
    )
    subtitles.add_argument(
        "--match-index",
        type=_parse_positive_int,
        help="Only process one match index from boundaries.",
    )
    subtitles.add_argument(
        "--match-indices",
        type=_parse_csv_int_values,
        help="Only process comma-separated match indices from boundaries.",
    )
    highlight_planner = subparsers.add_parser(
        "highlight-planner",
        help="Run the conservative highlight planner worker.",
    )
    highlight_planner.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Regenerate targeted highlight plans even when a matching plan already exists.",
    )
    highlight_planner.add_argument(
        "--session-id",
        help="Only plan highlight windows for one session id.",
    )
    highlight_planner.add_argument(
        "--session-ids",
        help="Only plan highlight windows for comma-separated session ids.",
    )
    highlight_planner.add_argument(
        "--match-index",
        type=_parse_positive_int,
        help="Only plan one match index from boundaries.",
    )
    highlight_planner.add_argument(
        "--match-indices",
        type=_parse_csv_int_values,
        help="Only plan comma-separated match indices from boundaries.",
    )
    edit_planner = subparsers.add_parser(
        "edit-planner",
        help="Run the teaser edit planner worker.",
    )
    edit_planner.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Regenerate targeted edit plans even when a matching plan already exists.",
    )
    edit_planner.add_argument(
        "--session-id",
        help="Only plan edit timelines for one session id.",
    )
    edit_planner.add_argument(
        "--session-ids",
        help="Only plan edit timelines for comma-separated session ids.",
    )
    edit_planner.add_argument(
        "--match-index",
        type=_parse_positive_int,
        help="Only plan one match index from boundaries.",
    )
    edit_planner.add_argument(
        "--match-indices",
        type=_parse_csv_int_values,
        help="Only plan comma-separated match indices from boundaries.",
    )
    exporter = subparsers.add_parser("exporter", help="Run the exporter worker.")
    exporter.add_argument(
        "--session-id",
        help="Only export match boundaries for one session id.",
    )
    exporter.add_argument(
        "--session-ids",
        help="Only export match boundaries for comma-separated session ids.",
    )
    exporter.add_argument(
        "--match-index",
        type=_parse_positive_int,
        help="Only export one match index from boundaries.",
    )
    exporter.add_argument(
        "--match-indices",
        type=_parse_csv_int_values,
        help="Only export comma-separated match indices from boundaries.",
    )
    exporter.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Re-export even when exporter state and export assets already exist.",
    )
    copywriter = subparsers.add_parser("copywriter", help="Run the title/copy generation worker.")
    copywriter.add_argument(
        "--session-id",
        help="Only generate copy for one session id.",
    )
    copywriter.add_argument(
        "--session-ids",
        help="Only generate copy for comma-separated session ids.",
    )
    copywriter.add_argument(
        "--match-index",
        type=_parse_positive_int,
        help="Only generate copy for one match index.",
    )
    copywriter.add_argument(
        "--match-indices",
        type=_parse_csv_int_values,
        help="Only generate copy for comma-separated match indices.",
    )
    copywriter.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Regenerate copy and publishing package rows even when outputs already exist.",
    )
    quality_report = subparsers.add_parser(
        "quality-report",
        help="Score existing publish exports and write Markdown/JSON quality reports.",
    )
    quality_report.add_argument(
        "--session-id",
        help="Only report exports for one session id.",
    )
    quality_report.add_argument(
        "--session-ids",
        help="Only report exports for comma-separated session ids.",
    )
    quality_report.add_argument(
        "--match-index",
        type=_parse_positive_int,
        help="Only report one match index.",
    )
    quality_report.add_argument(
        "--match-indices",
        type=_parse_csv_int_values,
        help="Only report comma-separated match indices.",
    )
    quality_report.add_argument(
        "--all-latest",
        action="store_true",
        help="Report the latest export asset for every matched session/match.",
    )
    quality_report.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when warnings are emitted.",
    )
    quality_report.add_argument(
        "--top-gaps",
        type=_parse_positive_int,
        help="Number of longest no-subtitle gaps to include per report.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    settings = _resolve_settings(args)

    if args.command == "show-config":
        print(settings.model_dump_json(indent=2))
        return 0

    if args.command == "status":
        print(json.dumps(StatusService(settings).build(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "live-status":
        report = run_live_status(build_probes(settings.platforms))
        if args.json:
            print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        else:
            print(_format_live_status_text(report))
        return 0

    if args.command == "maintenance":
        print(json.dumps(MaintenanceService(settings).run_once().as_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "soak":
        report = SoakService(settings).run(
            cycles=args.cycles,
            interval_seconds=args.interval_seconds,
            run_recorder=not args.skip_recorder,
            run_postprocess=not args.skip_postprocess,
            run_maintenance=args.maintenance,
        )
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 1 if report.failed_stages > 0 else 0

    if args.command == "windows-agent":
        WindowsAgentService(settings).run(once=args.once)
        return 0

    if args.command == "cookie-health":
        live_room_keys = load_cookie_health_live_room_keys(
            settings.windows_agent.state_file
        )
        report = run_cookie_health(
            build_cookie_health_probes(
                settings.platforms,
                live_room_keys=live_room_keys,
            )
        )
        for row in report.rows:
            print(
                f"platform={row.platform} "
                f"status={row.status} "
                f"detail={row.detail}"
            )
        summary = (
            "summary=expired_cookie_detected"
            if report.exit_code != 0
            else "summary=ok"
        )
        print(summary)
        if report.exit_code != 0:
            print(
                "hint=Refresh the relevant cookie env var "
                "(ARL_DOUYIN_COOKIE / ARL_BILIBILI_SESSDATA) per README cookie-grab instructions.",
            )
        return report.exit_code

    if args.command == "orchestrator":
        OrchestratorService(settings).run(once=args.once)
        return 0

    if args.command == "recorder":
        RecorderService(settings).run()
        return 0

    if args.command == "repair-recording-assets":
        result = RecordingAssetRepairService(settings).run(
            min_age_seconds=args.min_age_seconds,
        )
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "record-rooms":
        room_indices: list[int] | None = None
        if args.room_index is not None:
            room_indices = [args.room_index]
        elif args.room_indices is not None:
            room_indices = args.room_indices
        try:
            result = SelectedRecordingService(settings).run(
                room_indices=room_indices,
                all_live=args.all_live,
                force_ffmpeg=not args.placeholder,
                max_concurrent_jobs=args.max_concurrent_jobs,
            )
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "recovery":
        service = RecoveryService(settings)
        if args.summary:
            print(json.dumps(service.summary(), ensure_ascii=False, indent=2))
            return 0
        if args.maintenance:
            print(json.dumps(service.maintain(), ensure_ascii=False, indent=2))
            return 0
        if args.pending_report:
            print(json.dumps(service.pending_report(), ensure_ascii=False, indent=2))
            return 0
        if args.list_pending:
            print(json.dumps(service.list_pending_actions(), ensure_ascii=False, indent=2))
            return 0
        if args.resolve_job_id:
            service.mark_resolved(args.resolve_job_id, args.message)
            return 0
        if args.resolve_job_ids:
            result = service.mark_jobs_resolved(
                _parse_csv_values(args.resolve_job_ids),
                args.message,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.fail_job_id:
            service.mark_failed(args.fail_job_id, args.message)
            return 0
        if args.fail_job_ids:
            result = service.mark_jobs_failed(
                _parse_csv_values(args.fail_job_ids),
                args.message,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.resolve_action_key:
            service.mark_action_resolved(args.resolve_action_key, args.message)
            return 0
        if args.fail_action_key:
            service.mark_action_failed(args.fail_action_key, args.message)
            return 0
        service.run()
        return 0

    if args.command == "segmenter":
        SegmenterService(settings).run(
            session_ids=_collect_session_ids(args),
            force_reprocess=args.force_reprocess,
        )
        return 0

    if args.command == "detect-matches":
        from arl.vision import VisionMatchDetector
        from arl.shared.contracts import RecordingAsset
        from arl.shared.jsonl_store import load_models

        session_ids = _collect_session_ids(args)
        if not session_ids:
            parser.error("detect-matches requires --session-id or --session-ids")

        recording_assets_path = settings.storage.temp_dir / "recording-assets.jsonl"
        assets = load_models(recording_assets_path, RecordingAsset)
        matched_assets = [a for a in assets if a.session_id in session_ids]

        if not matched_assets:
            print(f"No recording assets found for session_ids={','.join(sorted(session_ids))}")
            return 0

        detector = VisionMatchDetector(settings.vision)
        for asset in matched_assets:
            recording_path = Path(asset.path)
            if not recording_path.exists():
                print(f"Recording not found: {recording_path}")
                continue

            print(f"\nDetecting matches for session_id={asset.session_id}")
            segments = detector.detect(recording_path)
            print(f"Detected {len(segments)} segments:")
            for idx, seg in enumerate(segments, start=1):
                print(
                    f"  Match {idx}: "
                    f"{seg.start_seconds:.1f}s - {seg.end_seconds:.1f}s "
                    f"(duration={(seg.end_seconds - seg.start_seconds):.1f}s) "
                    f"complete={seg.is_complete} "
                    f"confidence={seg.confidence:.2f} "
                    f"reason={seg.reason}"
                )
        return 0

    if args.command == "postprocess":
        PostProcessService(settings).run_once(session_ids=_collect_session_ids(args))
        return 0

    if args.command == "vision-analysis":
        assets = VisionAnalysisService(settings).run(
            session_ids=_collect_session_ids(args),
            force_reprocess=args.force_reprocess,
        )
        print(json.dumps([item.model_dump(mode="json") for item in assets], ensure_ascii=False, indent=2))
        return 0

    if args.command == "postprocess-reset":
        reset_session_ids: set[str] = set()
        if args.session_id:
            reset_session_ids.add(args.session_id)
        if args.session_ids:
            reset_session_ids.update(_parse_csv_values(args.session_ids))
        if not reset_session_ids:
            parser.error("postprocess-reset requires --session-id or --session-ids")
        result = PostProcessResetService(settings).run(
            session_ids=reset_session_ids,
            delete_files=not args.keep_files,
        )
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    if args.command == "stage-hints-auto":
        AutoStageHintService(settings).run()
        return 0

    if args.command == "stage-hints-semantic":
        SemanticStageHintService(settings).run()
        return 0

    if args.command == "stage-signals-from-subtitles":
        session_ids = _collect_session_ids(args)

        subtitle_paths: set[Path] | None = None
        if args.subtitle_path or args.subtitle_paths:
            subtitle_paths = set()
            if args.subtitle_path:
                subtitle_paths.add(args.subtitle_path)
            if args.subtitle_paths:
                subtitle_paths.update(Path(item) for item in _parse_csv_values(args.subtitle_paths))
            if not subtitle_paths:
                subtitle_paths = None
        match_indices: set[int] | None = None
        if args.match_index is not None or args.match_indices:
            match_indices = set()
            if args.match_index is not None:
                match_indices.add(args.match_index)
            if args.match_indices:
                match_indices.update(args.match_indices)
            if not match_indices:
                match_indices = None

        StageSignalFromSubtitlesService(settings).run(
            force_reprocess=args.force_reprocess,
            session_ids=session_ids,
            subtitle_paths=subtitle_paths,
            match_indices=match_indices,
        )
        return 0

    if args.command == "stage-hint":
        hint = StageHintWriter(settings).append(
            session_id=args.session_id,
            stage=MatchStage(args.stage),
            at_seconds=args.at_seconds,
            detected_at=args.detected_at,
        )
        print(json.dumps(hint.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    if args.command == "stage-signal":
        signal = StageSignalWriter(settings).append(
            session_id=args.session_id,
            text=args.text,
            source=args.source,
            at_seconds=args.at_seconds,
            detected_at=args.detected_at,
        )
        print(json.dumps(signal.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    if args.command == "subtitles":
        subtitle_session_ids = _collect_session_ids(args)

        subtitle_match_indices: set[int] | None = None
        if args.match_index is not None or args.match_indices:
            subtitle_match_indices = set()
            if args.match_index is not None:
                subtitle_match_indices.add(args.match_index)
            if args.match_indices:
                subtitle_match_indices.update(args.match_indices)
            if not subtitle_match_indices:
                subtitle_match_indices = None

        SubtitleService(settings).run(
            session_ids=subtitle_session_ids,
            match_indices=subtitle_match_indices,
            force_reprocess=args.force_reprocess,
        )
        return 0

    if args.command == "highlight-planner":
        highlight_session_ids = _collect_session_ids(args)

        highlight_match_indices: set[int] | None = None
        if args.match_index is not None or args.match_indices:
            highlight_match_indices = set()
            if args.match_index is not None:
                highlight_match_indices.add(args.match_index)
            if args.match_indices:
                highlight_match_indices.update(args.match_indices)
            if not highlight_match_indices:
                highlight_match_indices = None

        HighlightPlannerService(settings).run(
            session_ids=highlight_session_ids,
            match_indices=highlight_match_indices,
            force_reprocess=args.force_reprocess,
        )
        return 0

    if args.command == "edit-planner":
        edit_session_ids = _collect_session_ids(args)

        edit_match_indices: set[int] | None = None
        if args.match_index is not None or args.match_indices:
            edit_match_indices = set()
            if args.match_index is not None:
                edit_match_indices.add(args.match_index)
            if args.match_indices:
                edit_match_indices.update(args.match_indices)
            if not edit_match_indices:
                edit_match_indices = None

        EditingPlannerService(settings).run(
            session_ids=edit_session_ids,
            match_indices=edit_match_indices,
            force_reprocess=args.force_reprocess,
        )
        return 0

    if args.command == "exporter":
        export_session_ids = _collect_session_ids(args)

        export_match_indices: set[int] | None = None
        if args.match_index is not None or args.match_indices:
            export_match_indices = set()
            if args.match_index is not None:
                export_match_indices.add(args.match_index)
            if args.match_indices:
                export_match_indices.update(args.match_indices)
            if not export_match_indices:
                export_match_indices = None

        ExporterService(settings).run(
            session_ids=export_session_ids,
            match_indices=export_match_indices,
            force_reprocess=args.force_reprocess,
        )
        return 0

    if args.command == "copywriter":
        copywriter_session_ids = _collect_session_ids(args)

        copywriter_match_indices: set[int] | None = None
        if args.match_index is not None or args.match_indices:
            copywriter_match_indices = set()
            if args.match_index is not None:
                copywriter_match_indices.add(args.match_index)
            if args.match_indices:
                copywriter_match_indices.update(args.match_indices)
            if not copywriter_match_indices:
                copywriter_match_indices = None

        CopywriterService(settings).run(
            session_ids=copywriter_session_ids,
            match_indices=copywriter_match_indices,
            force_reprocess=args.force_reprocess,
        )
        return 0

    if args.command == "quality-report":
        report_session_ids = _collect_session_ids(args)
        report_match_indices: set[int] | None = None
        if args.match_index is not None or args.match_indices:
            report_match_indices = set()
            if args.match_index is not None:
                report_match_indices.add(args.match_index)
            if args.match_indices:
                report_match_indices.update(args.match_indices)
            if not report_match_indices:
                report_match_indices = None
        if not args.all_latest and report_session_ids is None:
            parser.error(
                "quality-report requires --session-id/--session-ids or --all-latest"
            )
        report = QualityReportService(settings).run(
            session_ids=report_session_ids,
            match_indices=report_match_indices,
            all_latest=args.all_latest,
            strict=args.strict,
            top_gaps=args.top_gaps,
        )
        print(report.markdown.rstrip())
        return report.exit_code

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("[arl] interrupted")
        raise SystemExit(130)
