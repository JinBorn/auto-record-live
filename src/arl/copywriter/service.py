from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from arl.config import Settings
from arl.copywriter.models import CopyDraft, CopywriterStateFile
from arl.shared.contracts import CopyAsset, ExportAsset, SubtitleAsset
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log


class CopywriterService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.subtitle_assets_path = settings.storage.temp_dir / "subtitle-assets.jsonl"
        self.export_assets_path = settings.storage.temp_dir / "export-assets.jsonl"
        self.copy_assets_path = settings.storage.temp_dir / "copy-assets.jsonl"
        self.state_path = settings.storage.temp_dir / "copywriter-state.json"

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
        state = self._load_state()
        processed_keys = set(state.processed_match_keys)
        existing_output_keys = {
            self._key(asset.session_id, asset.match_index)
            for asset in load_models(self.copy_assets_path, CopyAsset)
            if Path(asset.path).exists()
        }

        processed = 0
        for subtitle in subtitles:
            key = self._key(subtitle.session_id, subtitle.match_index)
            if key in processed_keys and key in existing_output_keys:
                continue
            if key in processed_keys:
                log(
                    "copywriter",
                    "reprocessing missing copy output "
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
            draft = self._build_draft(subtitle=subtitle, export=export)
            output_path = self._write_draft(draft)
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
            if key not in processed_keys:
                state.processed_match_keys.append(key)
                processed_keys.add(key)
            existing_output_keys.add(key)
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

    def _build_draft(
        self,
        *,
        subtitle: SubtitleAsset,
        export: ExportAsset | None,
    ) -> CopyDraft:
        lines = self._subtitle_text_lines(Path(subtitle.path))
        excerpt = lines[:3]
        title_candidates = self._title_candidates(
            excerpt=excerpt,
            match_index=subtitle.match_index,
        )
        recommended_title = title_candidates[0]
        description = self._description(
            excerpt=excerpt,
            match_index=subtitle.match_index,
        )
        return CopyDraft(
            session_id=subtitle.session_id,
            match_index=subtitle.match_index,
            source_subtitle_path=subtitle.path,
            source_export_path=export.path if export is not None else None,
            transcript_excerpt=excerpt,
            title_candidates=title_candidates,
            recommended_title=recommended_title,
            description=description,
            tags=self._tags(excerpt),
            status="generated" if excerpt else "placeholder_input",
            created_at=datetime.now(timezone.utc),
        )

    def _subtitle_text_lines(self, subtitle_path: Path) -> list[str]:
        text = subtitle_path.read_text(encoding="utf-8")
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = self._clean_line(raw_line)
            if not line:
                continue
            if line.isdigit() or "-->" in line:
                continue
            if line.lower().startswith("placeholder subtitle generated"):
                continue
            lines.append(line)
        return lines

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

    def _tags(self, excerpt: list[str]) -> list[str]:
        text = " ".join(excerpt)
        tags = ["英雄联盟", "直播切片", "对局高光"]
        keyword_tags = [
            ("装备", "装备选择"),
            ("击杀", "精彩击杀"),
            ("团", "团战"),
            ("胜利", "胜利时刻"),
            ("操作", "操作集锦"),
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
    def _truncate(text: str, max_chars: int) -> str:
        stripped = text.strip()
        if len(stripped) <= max_chars:
            return stripped
        return stripped[: max_chars - 3].rstrip("，。！？,.!? ") + "..."

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
