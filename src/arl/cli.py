from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from arl.config import load_settings
from arl.exporter.service import ExporterService
from arl.orchestrator.service import OrchestratorService
from arl.recovery.service import RecoveryService
from arl.recorder.service import RecorderService
from arl.segmenter.auto_hints import AutoStageHintService
from arl.segmenter.hints import StageHintWriter
from arl.segmenter.signals import StageSignalWriter
from arl.segmenter.signals_from_subtitles import StageSignalFromSubtitlesService
from arl.segmenter.semantic_hints import SemanticStageHintService
from arl.segmenter.service import SegmenterService
from arl.shared.contracts import MatchStage
from arl.subtitles.service import SubtitleService
from arl.windows_agent.service import WindowsAgentService


def _parse_csv_values(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


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


def _resolve_settings(
    args: argparse.Namespace,
):
    settings = load_settings()
    stage_keywords_path = getattr(args, "stage_keywords_path", None)
    if stage_keywords_path is None:
        return settings
    return settings.model_copy(
        deep=True,
        update={
            "segmenter": settings.segmenter.model_copy(
                update={"stage_keywords_path": stage_keywords_path}
            )
        },
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arl")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("show-config", help="Print resolved settings.")
    windows_agent = subparsers.add_parser("windows-agent", help="Run the Windows agent.")
    windows_agent.add_argument(
        "--once",
        action="store_true",
        help="Probe once and emit state if it changed.",
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
    subparsers.add_parser("segmenter", help="Run the match segmenter worker.")
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
    subparsers.add_parser("exporter", help="Run the exporter worker.")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    settings = _resolve_settings(args)

    if args.command == "show-config":
        print(settings.model_dump_json(indent=2))
        return 0

    if args.command == "windows-agent":
        WindowsAgentService(settings).run(once=args.once)
        return 0

    if args.command == "orchestrator":
        OrchestratorService(settings).run(once=args.once)
        return 0

    if args.command == "recorder":
        RecorderService(settings).run()
        return 0

    if args.command == "recovery":
        service = RecoveryService(settings)
        if args.summary:
            print(json.dumps(service.summary(), ensure_ascii=False, indent=2))
            return 0
        if args.maintenance:
            print(json.dumps(service.maintain(), ensure_ascii=False, indent=2))
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
        SegmenterService(settings).run()
        return 0

    if args.command == "stage-hints-auto":
        AutoStageHintService(settings).run()
        return 0

    if args.command == "stage-hints-semantic":
        SemanticStageHintService(settings).run()
        return 0

    if args.command == "stage-signals-from-subtitles":
        session_ids: set[str] | None = None
        if args.session_id or args.session_ids:
            session_ids = set()
            if args.session_id:
                session_ids.add(args.session_id)
            if args.session_ids:
                session_ids.update(_parse_csv_values(args.session_ids))
            if not session_ids:
                session_ids = None

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
        subtitle_session_ids: set[str] | None = None
        if args.session_id or args.session_ids:
            subtitle_session_ids = set()
            if args.session_id:
                subtitle_session_ids.add(args.session_id)
            if args.session_ids:
                subtitle_session_ids.update(_parse_csv_values(args.session_ids))
            if not subtitle_session_ids:
                subtitle_session_ids = None

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
        )
        return 0

    if args.command == "exporter":
        ExporterService(settings).run()
        return 0

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
