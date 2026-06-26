from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from arl.config import Settings
from arl.copywriter.cover import render_cover
from arl.copywriter.models import CopyDraft, CopywriterStateFile, PublishingPackage
from arl.shared.contracts import (
    CopyAsset,
    ExportAsset,
    HighlightPlanAsset,
    MatchBoundary,
    RecordingAsset,
    SubtitleAsset,
)
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log


_HIGHLIGHT_REASON_PRIORITY = {
    "highlight_keyword": 0,
    "condensed_key_event": 0,
    "condensed_tactical": 1,
    "narration": 2,
    "match_start_context": 3,
    "match_end_context": 4,
    "condensed_context": 5,
}


@dataclass(frozen=True)
class _SubtitleCue:
    started_at_seconds: float
    ended_at_seconds: float
    text: str


class CopywriterService:
    def __init__(
        self,
        settings: Settings,
        *,
        cover_renderer: Callable[..., bool] | None = None,
    ) -> None:
        self.settings = settings
        self.subtitle_assets_path = settings.storage.temp_dir / "subtitle-assets.jsonl"
        self.export_assets_path = settings.storage.temp_dir / "export-assets.jsonl"
        self.recording_assets_path = settings.storage.temp_dir / "recording-assets.jsonl"
        self.boundaries_path = settings.storage.temp_dir / "match-boundaries.jsonl"
        self.highlight_plans_path = settings.storage.temp_dir / "highlight-plans.jsonl"
        self.copy_assets_path = settings.storage.temp_dir / "copy-assets.jsonl"
        self.publishing_packages_path = settings.storage.temp_dir / "publishing-packages.jsonl"
        self.state_path = settings.storage.temp_dir / "copywriter-state.json"
        self.cover_renderer = cover_renderer or render_cover

    def run(
        self,
        *,
        session_ids: set[str] | None = None,
        match_indices: set[int] | None = None,
    ) -> None:
        log("copywriter", "starting")
        all_subtitles = load_models(self.subtitle_assets_path, SubtitleAsset)
        subtitles = self._filter_subtitles(
            all_subtitles,
            session_ids=session_ids,
            match_indices=match_indices,
        )
        if session_ids is not None or match_indices is not None:
            session_filter = ",".join(sorted(session_ids)) if session_ids is not None else "-"
            match_index_filter = (
                ",".join(str(item) for item in sorted(match_indices))
                if match_indices is not None
                else "-"
            )
            log(
                "copywriter",
                (
                    "filters "
                    f"total_subtitles={len(all_subtitles)} matched_subtitles={len(subtitles)} "
                    f"session_ids={session_filter} match_indices={match_index_filter}"
                ),
            )
        exports = load_models(self.export_assets_path, ExportAsset)
        export_map = {(item.session_id, item.match_index): item for item in exports}
        recording_map = self._latest_recording_by_session(
            load_models(self.recording_assets_path, RecordingAsset)
        )
        boundary_map = {
            (item.session_id, item.match_index): item
            for item in load_models(self.boundaries_path, MatchBoundary)
        }
        highlight_plan_map = {
            (item.session_id, item.match_index): item
            for item in load_models(self.highlight_plans_path, HighlightPlanAsset)
        }
        state = self._load_state()
        processed_keys = set(state.processed_match_keys)
        copy_assets = load_models(self.copy_assets_path, CopyAsset)
        package_assets = load_models(self.publishing_packages_path, PublishingPackage)
        existing_copy_row_keys = {
            self._key(asset.session_id, asset.match_index) for asset in copy_assets
        }
        existing_copy_output_keys = {
            self._key(asset.session_id, asset.match_index)
            for asset in copy_assets
            if Path(asset.path).exists()
        }
        existing_package_row_keys = {
            self._key(asset.session_id, asset.match_index) for asset in package_assets
        }
        existing_package_output_keys = {
            self._key(asset.session_id, asset.match_index)
            for asset in package_assets
            if asset.path is not None and Path(asset.path).exists()
        }

        processed = 0
        for subtitle in subtitles:
            key = self._key(subtitle.session_id, subtitle.match_index)
            if (
                key in processed_keys
                and key in existing_copy_output_keys
                and key in existing_package_output_keys
            ):
                continue
            if key in processed_keys and key not in existing_copy_output_keys:
                log(
                    "copywriter",
                    "reprocessing missing copy output "
                    f"session_id={subtitle.session_id} match_index={subtitle.match_index}",
                )
            elif key in processed_keys and key not in existing_package_output_keys:
                log(
                    "copywriter",
                    "reprocessing missing publishing package "
                    f"session_id={subtitle.session_id} match_index={subtitle.match_index}",
                )

            subtitle_path = Path(subtitle.path)
            if not subtitle_path.exists():
                log(
                    "copywriter",
                    "missing subtitle "
                    f"session_id={subtitle.session_id} match_index={subtitle.match_index}",
                )
                continue

            export = export_map.get((subtitle.session_id, subtitle.match_index))
            recording = recording_map.get(subtitle.session_id)
            boundary = boundary_map.get((subtitle.session_id, subtitle.match_index))
            highlight_plan = self._valid_highlight_plan(
                highlight_plan_map.get((subtitle.session_id, subtitle.match_index)),
                boundary,
            )
            draft, package = self._build_outputs(
                subtitle=subtitle,
                export=export,
                recording=recording,
                highlight_plan=highlight_plan,
            )
            output_path = self._write_draft(draft)
            if key not in existing_copy_row_keys:
                append_model(
                    self.copy_assets_path,
                    CopyAsset(
                        session_id=draft.session_id,
                        match_index=draft.match_index,
                        path=str(output_path),
                        title=draft.recommended_title,
                        description=draft.description,
                        tags=draft.tags,
                        subtitle_path=draft.source_subtitle_path,
                        export_path=draft.source_export_path,
                        created_at=draft.created_at,
                    ),
                )
                existing_copy_row_keys.add(key)
            existing_copy_output_keys.add(key)
            package = self._render_cover_if_possible(
                package,
                recording=recording,
                boundary=boundary,
                highlight_plan=highlight_plan,
            )
            package_output_path = self._package_output_path(
                package.session_id,
                package.match_index,
            )
            package = package.model_copy(update={"path": str(package_output_path)})
            self._write_package(package, package_output_path)
            if key not in existing_package_row_keys:
                append_model(self.publishing_packages_path, package)
                existing_package_row_keys.add(key)
            existing_package_output_keys.add(key)
            if key not in processed_keys:
                state.processed_match_keys.append(key)
                processed_keys.add(key)
            processed += 1
            log(
                "copywriter",
                f"copy asset written session_id={draft.session_id} match_index={draft.match_index}",
            )

        self._save_state(state)
        log("copywriter", f"processed_copies={processed}")

    def _filter_subtitles(
        self,
        subtitles: list[SubtitleAsset],
        *,
        session_ids: set[str] | None,
        match_indices: set[int] | None,
    ) -> list[SubtitleAsset]:
        if session_ids is None and match_indices is None:
            return subtitles
        filtered: list[SubtitleAsset] = []
        for subtitle in subtitles:
            if session_ids is not None and subtitle.session_id not in session_ids:
                continue
            if match_indices is not None and subtitle.match_index not in match_indices:
                continue
            filtered.append(subtitle)
        return filtered

    def _build_outputs(
        self,
        *,
        subtitle: SubtitleAsset,
        export: ExportAsset | None,
        recording: RecordingAsset | None,
        highlight_plan: HighlightPlanAsset | None,
    ) -> tuple[CopyDraft, PublishingPackage]:
        cues = self._parse_subtitle_cues(Path(subtitle.path))
        signal_cues = self._select_signal_cues(cues, highlight_plan)
        excerpt = [cue.text for cue in signal_cues[:3]]
        title_candidates = self._title_candidates(
            excerpt=excerpt,
            match_index=subtitle.match_index,
        )
        recommended_title = title_candidates[0]
        description = self._description(
            excerpt=excerpt,
            match_index=subtitle.match_index,
        )
        tags = self._tags(excerpt)
        status = "generated" if excerpt else "placeholder_input"
        created_at = datetime.now(timezone.utc)
        draft = CopyDraft(
            session_id=subtitle.session_id,
            match_index=subtitle.match_index,
            source_subtitle_path=subtitle.path,
            source_export_path=export.path if export is not None else None,
            transcript_excerpt=excerpt,
            title_candidates=title_candidates,
            recommended_title=recommended_title,
            description=description,
            tags=tags,
            status=status,
            created_at=created_at,
        )
        package = PublishingPackage(
            session_id=subtitle.session_id,
            match_index=subtitle.match_index,
            source_subtitle_path=subtitle.path,
            source_export_path=export.path if export is not None else None,
            source_recording_path=recording.path if recording is not None else None,
            transcript_excerpt=excerpt,
            evidence=self._evidence(signal_cues),
            title_candidates=title_candidates,
            recommended_title=recommended_title,
            summary=self._summary(excerpt, match_index=subtitle.match_index),
            cover_lines=self._cover_lines(
                excerpt=excerpt,
                title=recommended_title,
                match_index=subtitle.match_index,
            ),
            tags=tags,
            status=status,
            created_at=created_at,
        )
        return draft, package

    def _valid_highlight_plan(
        self,
        plan: HighlightPlanAsset | None,
        boundary: MatchBoundary | None,
    ) -> HighlightPlanAsset | None:
        if plan is None:
            return None
        if boundary is None:
            return plan
        tolerance_seconds = 1.0
        if (
            abs(plan.source_boundary_start_seconds - boundary.started_at_seconds)
            > tolerance_seconds
            or abs(plan.source_boundary_end_seconds - boundary.ended_at_seconds)
            > tolerance_seconds
        ):
            return None
        return plan

    def _subtitle_text_lines(self, subtitle_path: Path) -> list[str]:
        return [cue.text for cue in self._parse_subtitle_cues(subtitle_path)]

    def _parse_subtitle_cues(self, subtitle_path: Path) -> list[_SubtitleCue]:
        lines = subtitle_path.read_text(encoding="utf-8").splitlines()
        cues: list[_SubtitleCue] = []
        index = 0
        while index < len(lines):
            line = lines[index].strip()
            if "-->" not in line:
                index += 1
                continue

            start_raw, end_raw = [item.strip() for item in line.split("-->", 1)]
            start_seconds = self._parse_srt_timestamp(start_raw)
            end_seconds = self._parse_srt_timestamp(end_raw)
            if start_seconds is None or end_seconds is None or end_seconds <= start_seconds:
                index += 1
                continue

            index += 1
            text_rows: list[str] = []
            while index < len(lines) and lines[index].strip():
                text_rows.append(lines[index].strip())
                index += 1
            text = self._clean_line(" ".join(text_rows))
            if text and not self._is_placeholder_text(text):
                cues.append(_SubtitleCue(start_seconds, end_seconds, text))
            index += 1
        return cues

    def _select_signal_cues(
        self,
        cues: list[_SubtitleCue],
        highlight_plan: HighlightPlanAsset | None,
    ) -> list[_SubtitleCue]:
        if not cues:
            return []
        if highlight_plan is None or not highlight_plan.windows:
            return cues
        selected_with_priority: list[tuple[int, float, _SubtitleCue]] = []
        for cue in cues:
            overlapping_priorities = [
                _HIGHLIGHT_REASON_PRIORITY.get(window.reason, 100)
                for window in highlight_plan.windows
                if min(cue.ended_at_seconds, window.ended_at_seconds)
                > max(cue.started_at_seconds, window.started_at_seconds)
            ]
            if not overlapping_priorities:
                continue
            selected_with_priority.append(
                (
                    min(overlapping_priorities),
                    cue.started_at_seconds,
                    cue,
                )
            )
        selected = [
            cue
            for _, _, cue in sorted(
                selected_with_priority,
                key=lambda item: (item[0], item[1], item[2].ended_at_seconds),
            )
        ]
        return selected or cues

    def _parse_srt_timestamp(self, raw: str) -> float | None:
        try:
            timestamp = raw.strip()
            separator = "," if "," in timestamp else "."
            hhmmss, millis = timestamp.split(separator, 1)
            hours, minutes, seconds = hhmmss.split(":", 2)
            return max(
                0.0,
                int(hours) * 3600
                + int(minutes) * 60
                + int(seconds)
                + int(millis[:3].ljust(3, "0")) / 1000.0,
            )
        except ValueError:
            return None

    def _title_candidates(self, *, excerpt: list[str], match_index: int) -> list[str]:
        if not excerpt:
            return [
                f"第{match_index:02d}局高光回放",
                f"第{match_index:02d}局直播切片",
                "这一波值得回看",
            ]

        first = self._truncate(excerpt[0], 28)
        candidates = [first]
        if not first.endswith(("?", "？", "!", "！")):
            candidates[0] = f"{first}｜对局高光"
        joined = self._truncate(" ".join(excerpt[:2]), 24)
        candidates.append(f"这一波聊到重点：{joined}")
        candidates.append(f"第{match_index:02d}局高光：{self._truncate(excerpt[0], 18)}")
        return self._dedupe(candidates)

    def _description(self, *, excerpt: list[str], match_index: int) -> str:
        if not excerpt:
            return f"第{match_index:02d}局直播切片，已生成标题、简介和发布标签。"
        summary = self._truncate(" ".join(excerpt), 80)
        return f"本段聚焦「{summary}」，保留现场解说节奏，适合作为英雄联盟直播切片发布。"

    def _summary(self, excerpt: list[str], match_index: int) -> str:
        if not excerpt:
            return f"第{match_index:02d}局直播切片，等待可用字幕后可生成更完整总结。"
        return self._truncate(" ".join(excerpt[:4]), 96)

    def _cover_lines(
        self,
        *,
        excerpt: list[str],
        title: str,
        match_index: int,
    ) -> list[str]:
        if not excerpt:
            return [f"第{match_index:02d}局高光", "直播切片"]
        title_text = title.split("｜", 1)[0].strip()
        text = title_text or self._truncate(" ".join(excerpt[:2]), 56)
        return self._split_cover_lines(text, max_chars=12, max_lines=4)

    def _evidence(self, cues: list[_SubtitleCue]) -> list[str]:
        return [
            f"{self._format_timestamp(cue.started_at_seconds)} {self._truncate(cue.text, 48)}"
            for cue in cues[:5]
        ]

    def _tags(self, excerpt: list[str]) -> list[str]:
        text = " ".join(excerpt)
        tags = ["英雄联盟", "直播切片", "对局高光"]
        keyword_tags = [
            ("装备", "装备选择"),
            ("电刀", "装备选择"),
            ("AP", "AP套路"),
            ("机器人", "机器人"),
            ("击杀", "精彩击杀"),
            ("杀", "精彩击杀"),
            ("团", "团战"),
            ("胜利", "胜利时刻"),
            ("操作", "操作集锦"),
            ("套路", "套路玩法"),
        ]
        for keyword, tag in keyword_tags:
            if keyword in text:
                tags.append(tag)
        return self._dedupe(tags)

    def _write_draft(self, draft: CopyDraft) -> Path:
        output_dir = self.settings.storage.processed_dir / draft.session_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"match-{draft.match_index:02d}-copy.json"
        output_path.write_text(
            json.dumps(draft.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return output_path

    def _write_package(self, package: PublishingPackage, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return output_path

    def _package_output_path(self, session_id: str, match_index: int) -> Path:
        return (
            self.settings.storage.processed_dir
            / session_id
            / f"match-{match_index:02d}-publishing.json"
        )

    def _render_cover_if_possible(
        self,
        package: PublishingPackage,
        *,
        recording: RecordingAsset | None,
        boundary: MatchBoundary | None,
        highlight_plan: HighlightPlanAsset | None,
    ) -> PublishingPackage:
        if recording is None:
            return package
        recording_path = Path(recording.path)
        if not recording_path.exists():
            return package
        cover_path = (
            self.settings.storage.processed_dir
            / package.session_id
            / f"match-{package.match_index:02d}-cover.jpg"
        )
        try:
            rendered = self.cover_renderer(
                recording_path,
                cover_path,
                package.cover_lines,
                at_seconds=self._cover_source_time(package, boundary, highlight_plan),
            )
        except Exception as exc:
            log(
                "copywriter",
                "cover render skipped "
                f"session_id={package.session_id} match_index={package.match_index} "
                f"reason={exc.__class__.__name__}",
            )
            return package
        if not rendered:
            return package
        return package.model_copy(update={"cover_path": str(cover_path)})

    def _cover_source_time(
        self,
        package: PublishingPackage,
        boundary: MatchBoundary | None,
        highlight_plan: HighlightPlanAsset | None,
    ) -> float:
        relative_seconds = 0.0
        if package.evidence:
            parsed = self._parse_evidence_timestamp(package.evidence[0])
            if parsed is not None:
                relative_seconds = parsed
        elif highlight_plan is not None and highlight_plan.windows:
            relative_seconds = max(0.0, highlight_plan.windows[0].started_at_seconds)
        if boundary is not None:
            return max(0.0, boundary.started_at_seconds + relative_seconds)
        return max(0.0, relative_seconds)

    @staticmethod
    def _latest_recording_by_session(
        recordings: list[RecordingAsset],
    ) -> dict[str, RecordingAsset]:
        latest: dict[str, RecordingAsset] = {}
        for recording in recordings:
            latest[recording.session_id] = recording
        return latest

    def _load_state(self) -> CopywriterStateFile:
        if not self.state_path.exists():
            return CopywriterStateFile()
        return CopywriterStateFile.model_validate_json(self.state_path.read_text(encoding="utf-8"))

    def _save_state(self, state: CopywriterStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _key(self, session_id: str, match_index: int) -> str:
        return f"{session_id}:{match_index}"

    @staticmethod
    def _clean_line(raw_line: str) -> str:
        line = re.sub(r"<[^>]+>", "", raw_line.strip())
        return re.sub(r"\s+", " ", line).strip()

    @staticmethod
    def _is_placeholder_text(text: str) -> bool:
        return "placeholder subtitle generated by local pipeline" in text.lower()

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        stripped = text.strip()
        if len(stripped) <= max_chars:
            return stripped
        return stripped[: max_chars - 3].rstrip("，。！？,.!? ") + "..."

    def _split_cover_lines(
        self,
        text: str,
        *,
        max_chars: int,
        max_lines: int,
    ) -> list[str]:
        normalized = re.sub(r"[，。！？、；;:：|｜]+", " ", text).strip()
        if not normalized:
            return []
        words = [item for item in normalized.split() if item]
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                lines.extend(self._hard_wrap(current, max_chars))
            current = word
        if current:
            lines.extend(self._hard_wrap(current, max_chars))
        return [line for line in lines if line][:max_lines] or [self._truncate(normalized, max_chars)]

    @staticmethod
    def _hard_wrap(text: str, max_chars: int) -> list[str]:
        if len(text) <= max_chars:
            return [text]
        return [text[index : index + max_chars] for index in range(0, len(text), max_chars)]

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        total_seconds = max(0, int(seconds))
        minutes, secs = divmod(total_seconds, 60)
        return f"{minutes:02d}:{secs:02d}"

    def _parse_evidence_timestamp(self, evidence: str) -> float | None:
        match = re.match(r"^(\d{2}):(\d{2})\b", evidence)
        if match is None:
            return None
        return int(match.group(1)) * 60.0 + int(match.group(2))

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value in seen:
                continue
            deduped.append(value)
            seen.add(value)
        return deduped
