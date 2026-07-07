from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from arl.config import Settings
from arl.copywriter.cover import render_cover
from arl.copywriter.llm import (
    LlmProvider,
    LlmProviderError,
    OpenAICompatibleProvider,
    parse_llm_copywriting_result,
)
from arl.copywriter.models import (
    CopyDraft,
    CopywriterSemanticAsset,
    CopywriterStateFile,
    LlmCopywritingResult,
    PublishingPackage,
)
from arl.orchestrator.models import OrchestratorStateFile
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
        llm_provider: LlmProvider | None = None,
    ) -> None:
        self.settings = settings
        self.subtitle_assets_path = settings.storage.temp_dir / "subtitle-assets.jsonl"
        self.export_assets_path = settings.storage.temp_dir / "export-assets.jsonl"
        self.recording_assets_path = settings.storage.temp_dir / "recording-assets.jsonl"
        self.boundaries_path = settings.storage.temp_dir / "match-boundaries.jsonl"
        self.highlight_plans_path = settings.storage.temp_dir / "highlight-plans.jsonl"
        self.semantic_assets_path = (
            settings.storage.temp_dir / "copywriter-semantic-assets.jsonl"
        )
        self.copy_assets_path = settings.storage.temp_dir / "copy-assets.jsonl"
        self.publishing_packages_path = settings.storage.temp_dir / "publishing-packages.jsonl"
        self.state_path = settings.storage.temp_dir / "copywriter-state.json"
        self.cover_renderer = cover_renderer or render_cover
        self.llm_provider = llm_provider

    def run(
        self,
        *,
        session_ids: set[str] | None = None,
        match_indices: set[int] | None = None,
        force_reprocess: bool = False,
    ) -> None:
        self.run_semantic(
            session_ids=session_ids,
            match_indices=match_indices,
            force_reprocess=force_reprocess,
        )
        self.run_publishing(
            session_ids=session_ids,
            match_indices=match_indices,
            force_reprocess=force_reprocess,
        )

    def run_semantic(
        self,
        *,
        session_ids: set[str] | None = None,
        match_indices: set[int] | None = None,
        force_reprocess: bool = False,
    ) -> None:
        if not self.settings.llm.enabled:
            return
        if not self.settings.llm.api_key:
            log("copywriter", "llm semantic skipped reason=missing_api_key")
            return

        log("copywriter", "llm semantic starting")
        all_subtitles = load_models(self.subtitle_assets_path, SubtitleAsset)
        subtitles = self._filter_subtitles(
            all_subtitles,
            session_ids=session_ids,
            match_indices=match_indices,
        )
        boundary_map = {
            (item.session_id, item.match_index): item
            for item in load_models(self.boundaries_path, MatchBoundary)
        }
        highlight_plan_map = {
            (item.session_id, item.match_index): item
            for item in load_models(self.highlight_plans_path, HighlightPlanAsset)
        }
        streamer_names = self._streamer_names_by_session()
        semantic_assets = self._latest_semantic_assets_by_match(
            load_models(self.semantic_assets_path, CopywriterSemanticAsset)
        )

        generated = 0
        cached = 0
        failed = 0
        for subtitle in subtitles:
            subtitle_path = Path(subtitle.path)
            if not subtitle_path.exists():
                continue
            boundary = boundary_map.get((subtitle.session_id, subtitle.match_index))
            highlight_plan = self._valid_highlight_plan(
                highlight_plan_map.get((subtitle.session_id, subtitle.match_index)),
                boundary,
            )
            cues = self._parse_subtitle_cues(subtitle_path)
            if not cues:
                continue
            prompt_input = self._semantic_prompt_input(
                subtitle=subtitle,
                cues=cues,
                boundary=boundary,
                highlight_plan=highlight_plan,
                streamer_name=streamer_names.get(subtitle.session_id),
            )
            prompt_fingerprint = self._prompt_fingerprint()
            input_fingerprint = self._stable_fingerprint(prompt_input)
            existing = semantic_assets.get((subtitle.session_id, subtitle.match_index))
            if (
                not force_reprocess
                and existing is not None
                and existing.model == self.settings.llm.model
                and existing.prompt_fingerprint == prompt_fingerprint
                and existing.input_fingerprint == input_fingerprint
            ):
                cached += 1
                continue

            try:
                result, token_usage = self._generate_llm_copy(prompt_input, cues)
            except LlmProviderError as exc:
                failed += 1
                log(
                    "copywriter",
                    "llm semantic fallback "
                    f"session_id={subtitle.session_id} match_index={subtitle.match_index} "
                    f"reason={exc}",
                )
                continue

            asset = CopywriterSemanticAsset(
                session_id=subtitle.session_id,
                match_index=subtitle.match_index,
                source_subtitle_path=subtitle.path,
                provider=self.settings.llm.base_url,
                model=self.settings.llm.model,
                prompt_fingerprint=prompt_fingerprint,
                input_fingerprint=input_fingerprint,
                result=result,
                token_usage=token_usage,
                status="generated",
                created_at=datetime.now(timezone.utc),
            )
            append_model(self.semantic_assets_path, asset)
            semantic_assets[(asset.session_id, asset.match_index)] = asset
            generated += 1
            usage = ",".join(f"{key}={value}" for key, value in token_usage.items()) or "-"
            log(
                "copywriter",
                "llm semantic asset written "
                f"session_id={asset.session_id} match_index={asset.match_index} "
                f"usage={usage}",
            )

        log(
            "copywriter",
            f"llm_semantic generated={generated} cached={cached} failed={failed}",
        )

    def run_publishing(
        self,
        *,
        session_ids: set[str] | None = None,
        match_indices: set[int] | None = None,
        force_reprocess: bool = False,
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
        semantic_assets = self._latest_semantic_assets_by_match(
            load_models(self.semantic_assets_path, CopywriterSemanticAsset)
        )
        streamer_names = self._streamer_names_by_session()
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
        latest_package_by_key = {
            self._key(asset.session_id, asset.match_index): asset
            for asset in package_assets
        }
        existing_package_row_keys = set(latest_package_by_key)
        existing_package_output_keys = {
            key
            for key, asset in latest_package_by_key.items()
            if self._publishing_package_outputs_exist(asset)
        }

        processed = 0
        for subtitle in subtitles:
            key = self._key(subtitle.session_id, subtitle.match_index)
            if (
                not force_reprocess
                and key in processed_keys
                and key in existing_copy_output_keys
                and key in existing_package_output_keys
            ):
                continue
            if force_reprocess and key in processed_keys:
                log(
                    "copywriter",
                    "force reprocessing copy output "
                    f"session_id={subtitle.session_id} match_index={subtitle.match_index}",
                )
            elif key in processed_keys and key not in existing_copy_output_keys:
                log(
                    "copywriter",
                    "reprocessing missing copy output "
                    f"session_id={subtitle.session_id} match_index={subtitle.match_index}",
                )
            elif key in processed_keys and key not in existing_package_output_keys:
                log(
                    "copywriter",
                    "reprocessing missing publishing package artifacts "
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
                streamer_name=streamer_names.get(subtitle.session_id),
                semantic_asset=semantic_assets.get(
                    (subtitle.session_id, subtitle.match_index)
                ),
            )
            output_path = self._write_draft(draft)
            if force_reprocess or key not in existing_copy_row_keys:
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
                export=export,
                recording=recording,
                boundary=boundary,
                highlight_plan=highlight_plan,
            )
            package_output_path = self._package_output_path(
                package.session_id,
                package.match_index,
            )
            package = package.model_copy(update={"path": str(package_output_path)})
            package = self._publish_export_files(package)
            self._write_package(package, package_output_path)
            if force_reprocess or key not in existing_package_row_keys:
                append_model(self.publishing_packages_path, package)
                existing_package_row_keys.add(key)
            else:
                self._replace_latest_package_row(package)
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
            return self._latest_subtitles_by_match(subtitles)
        filtered: list[SubtitleAsset] = []
        for subtitle in subtitles:
            if session_ids is not None and subtitle.session_id not in session_ids:
                continue
            if match_indices is not None and subtitle.match_index not in match_indices:
                continue
            filtered.append(subtitle)
        return self._latest_subtitles_by_match(filtered)

    @staticmethod
    def _latest_subtitles_by_match(subtitles: list[SubtitleAsset]) -> list[SubtitleAsset]:
        latest_by_key: dict[tuple[str, int], SubtitleAsset] = {}
        key_order: list[tuple[str, int]] = []
        for subtitle in subtitles:
            key = (subtitle.session_id, subtitle.match_index)
            if key not in latest_by_key:
                key_order.append(key)
            latest_by_key[key] = subtitle
        return [latest_by_key[key] for key in key_order]

    @staticmethod
    def _latest_semantic_assets_by_match(
        assets: list[CopywriterSemanticAsset],
    ) -> dict[tuple[str, int], CopywriterSemanticAsset]:
        latest: dict[tuple[str, int], CopywriterSemanticAsset] = {}
        for asset in assets:
            latest[(asset.session_id, asset.match_index)] = asset
        return latest

    def _semantic_prompt_input(
        self,
        *,
        subtitle: SubtitleAsset,
        cues: list[_SubtitleCue],
        boundary: MatchBoundary | None,
        highlight_plan: HighlightPlanAsset | None,
        streamer_name: str | None,
    ) -> dict[str, object]:
        duration = (
            max(0.0, boundary.ended_at_seconds - boundary.started_at_seconds)
            if boundary is not None
            else max((cue.ended_at_seconds for cue in cues), default=0.0)
        )
        selected_cues = self._select_llm_input_cues(cues, highlight_plan)
        return {
            "session_id": subtitle.session_id,
            "match_index": subtitle.match_index,
            "streamer_name": streamer_name or "",
            "match_duration_seconds": round(duration, 3),
            "subtitle_cues": [
                {
                    "start": round(cue.started_at_seconds, 3),
                    "end": round(cue.ended_at_seconds, 3),
                    "text": cue.text,
                }
                for cue in selected_cues
            ],
            "highlight_windows": [
                {
                    "start": round(window.started_at_seconds, 3),
                    "end": round(window.ended_at_seconds, 3),
                    "reason": window.reason,
                }
                for window in (highlight_plan.windows if highlight_plan is not None else [])
            ],
        }

    def _select_llm_input_cues(
        self,
        cues: list[_SubtitleCue],
        highlight_plan: HighlightPlanAsset | None,
    ) -> list[_SubtitleCue]:
        max_cues = self.settings.llm.max_input_cues
        if len(cues) <= max_cues:
            return cues
        selected: list[_SubtitleCue] = []
        selected_keys: set[tuple[float, float, str]] = set()

        def _add(cue: _SubtitleCue) -> None:
            key = (cue.started_at_seconds, cue.ended_at_seconds, cue.text)
            if key in selected_keys:
                return
            selected.append(cue)
            selected_keys.add(key)

        head_tail = max(5, min(20, max_cues // 8))
        for cue in cues[:head_tail]:
            _add(cue)
        for cue in cues[-head_tail:]:
            _add(cue)

        if highlight_plan is not None:
            for cue in cues:
                if len(selected) >= max_cues:
                    break
                if any(
                    min(cue.ended_at_seconds, window.ended_at_seconds)
                    > max(cue.started_at_seconds, window.started_at_seconds)
                    for window in highlight_plan.windows
                ):
                    _add(cue)

        index = 0
        while len(selected) < max_cues and index < len(cues):
            _add(cues[index])
            index += max(1, len(cues) // max_cues)
        return sorted(selected, key=lambda cue: cue.started_at_seconds)[:max_cues]

    def _generate_llm_copy(
        self,
        prompt_input: dict[str, object],
        cues: list[_SubtitleCue],
    ) -> tuple[LlmCopywritingResult, dict[str, int]]:
        provider = self.llm_provider or OpenAICompatibleProvider(self.settings.llm)
        system_prompt = self._llm_system_prompt()
        user_prompt = json.dumps(prompt_input, ensure_ascii=False, indent=2)
        last_error: LlmProviderError | None = None
        for attempt in range(self.settings.llm.max_retries + 1):
            try:
                response = provider.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                result = parse_llm_copywriting_result(response.content)
                if self._llm_title_is_raw_excerpt(result, cues):
                    raise LlmProviderError("raw_excerpt_title")
                return result, response.token_usage
            except LlmProviderError as exc:
                last_error = exc
                if attempt < self.settings.llm.max_retries:
                    log(
                        "copywriter",
                        "llm semantic retry "
                        f"attempt={attempt + 1}/{self.settings.llm.max_retries} "
                        f"reason={exc}",
                    )
        raise last_error or LlmProviderError("unknown_llm_error")

    @staticmethod
    def _llm_system_prompt() -> str:
        return (
            "You write concise Simplified Chinese Bilibili upload copy for League "
            "of Legends livestream edits. Return JSON only with keys: "
            "title_candidates (exactly 3 strings, each <=30 compact chars), "
            "recommended_title, cover_lines (2-4 strings, each <=10 compact chars), "
            "summary (<=96 compact chars), description (1-3 sentences), tags "
            "(5-8 strings), hook_line, teaser_recommendations (up to 3 objects with "
            "source_start_seconds, source_end_seconds, hook_reason). Do not copy a "
            "raw leading subtitle line as the title; synthesize the gameplay/topic hook."
        )

    def _llm_title_is_raw_excerpt(
        self,
        result: LlmCopywritingResult,
        cues: list[_SubtitleCue],
    ) -> bool:
        normalized_title = self._normalize_copy_text(result.recommended_title)
        leading = [cue.text for cue in cues if not self._is_placeholder_text(cue.text)][:5]
        candidates = [self._normalize_copy_text(text) for text in leading]
        for count in range(2, min(4, len(leading) + 1)):
            candidates.append(self._normalize_copy_text(" ".join(leading[:count])))
        return normalized_title in {candidate for candidate in candidates if candidate}

    @staticmethod
    def _normalize_copy_text(value: str) -> str:
        return re.sub(r"\s+", " ", value.strip()).strip(" .!?;:").lower()

    @staticmethod
    def _prompt_fingerprint() -> str:
        return hashlib.sha256(CopywriterService._llm_system_prompt().encode("utf-8")).hexdigest()

    @staticmethod
    def _stable_fingerprint(payload: dict[str, object]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _build_outputs(
        self,
        *,
        subtitle: SubtitleAsset,
        export: ExportAsset | None,
        recording: RecordingAsset | None,
        highlight_plan: HighlightPlanAsset | None,
        streamer_name: str | None,
        semantic_asset: CopywriterSemanticAsset | None = None,
    ) -> tuple[CopyDraft, PublishingPackage]:
        cues = self._parse_subtitle_cues(Path(subtitle.path))
        signal_cues = self._select_signal_cues(cues, highlight_plan)
        headline_cues = self._select_headline_cues(
            cues,
            signal_cues,
            highlight_plan,
        )
        excerpt = [cue.text for cue in headline_cues[:3]]
        llm_result = semantic_asset.result if semantic_asset is not None else None
        if llm_result is not None:
            title_candidates = llm_result.title_candidates
            recommended_title = llm_result.recommended_title
            description = llm_result.description
            tags = llm_result.tags
            summary = llm_result.summary
            cover_lines = llm_result.cover_lines
            status = "llm_generated"
        else:
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
            summary = self._summary(excerpt, match_index=subtitle.match_index)
            cover_lines = self._cover_lines(
                excerpt=excerpt,
                title=recommended_title,
                match_index=subtitle.match_index,
            )
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
            streamer_name=streamer_name,
            source_subtitle_path=subtitle.path,
            source_export_path=export.path if export is not None else None,
            source_recording_path=recording.path if recording is not None else None,
            transcript_excerpt=excerpt,
            evidence=self._evidence(headline_cues or signal_cues),
            title_candidates=title_candidates,
            recommended_title=recommended_title,
            summary=summary,
            cover_lines=cover_lines,
            tags=tags,
            status=status,
            created_at=created_at,
        )
        return draft, package

    def _publish_export_files(self, package: PublishingPackage) -> PublishingPackage:
        if not package.source_export_path:
            return package
        source_video = Path(package.source_export_path)
        if not source_video.exists():
            return package

        stem = self._published_stem(package)
        package_dir = source_video.parent / stem
        target_video = package_dir / f"video{source_video.suffix}"
        published_video_path = self._link_or_copy_file(source_video, target_video)

        published_cover_path: Path | None = None
        source_cover: Path | None = None
        if package.cover_path:
            source_cover = Path(package.cover_path)
            if source_cover.exists():
                target_cover = package_dir / f"cover{source_cover.suffix or '.jpg'}"
                published_cover_path = self._copy_file(source_cover, target_cover)
        published_metadata_path: Path | None = None
        if published_video_path is not None:
            published_metadata_path = self._write_published_metadata(
                package,
                package_dir / "upload.txt",
            )

        self._cleanup_legacy_flat_publish_aliases(
            stem=stem,
            source_video=source_video,
            source_cover=source_cover,
        )

        return package.model_copy(
            update={
                "published_package_dir": str(package_dir),
                "published_video_path": (
                    str(published_video_path) if published_video_path is not None else None
                ),
                "published_cover_path": (
                    str(published_cover_path) if published_cover_path is not None else None
                ),
                "published_metadata_path": (
                    str(published_metadata_path)
                    if published_metadata_path is not None
                    else None
                ),
            }
        )

    def _published_stem(self, package: PublishingPackage) -> str:
        streamer = self._safe_filename_part(package.streamer_name or "unknown-streamer")
        title = self._safe_filename_part(package.recommended_title or "untitled")
        session_hint = self._session_filename_hint(package.session_id)
        base = f"{streamer} - {title} - {session_hint}_match{package.match_index:02d}"
        return self._limit_filename_stem(base, max_chars=96)

    @staticmethod
    def _session_filename_hint(session_id: str) -> str:
        match = re.match(r"^session-(\d{14})", session_id)
        if match is not None:
            return match.group(1)
        return session_id[-8:] if len(session_id) > 8 else session_id

    @staticmethod
    def _safe_filename_part(value: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        return cleaned or "untitled"

    @staticmethod
    def _limit_filename_stem(stem: str, *, max_chars: int) -> str:
        if len(stem) <= max_chars:
            return stem
        return stem[:max_chars].rstrip(" .")

    @staticmethod
    def _write_published_metadata(
        package: PublishingPackage,
        target: Path,
    ) -> Path | None:
        target.parent.mkdir(parents=True, exist_ok=True)
        hashtags = " ".join(f"#{tag}" for tag in package.tags)
        rows = [
            f"Title: {package.recommended_title}",
            "",
            "Description:",
            package.summary,
            "",
            "Hashtags:",
            hashtags,
            "",
            f"Streamer: {package.streamer_name or 'unknown-streamer'}",
            f"Session: {package.session_id}",
            f"Match: {package.match_index:02d}",
            "",
            "Cover Lines:",
            *[f"- {line}" for line in package.cover_lines],
        ]
        if package.evidence:
            rows.extend(
                [
                    "",
                    "Evidence:",
                    *[f"- {item}" for item in package.evidence],
                ]
            )
        rows.extend(
            [
                "",
                "Sources:",
                f"- subtitle: {package.source_subtitle_path}",
                f"- export: {package.source_export_path or '-'}",
                f"- recording: {package.source_recording_path or '-'}",
            ]
        )
        try:
            target.write_text("\n".join(rows).rstrip() + "\n", encoding="utf-8")
        except OSError as exc:
            log(
                "copywriter",
                "publish metadata write skipped "
                f"target={target} reason={exc.__class__.__name__}",
            )
            return None
        return target

    def _cleanup_legacy_flat_publish_aliases(
        self,
        *,
        stem: str,
        source_video: Path,
        source_cover: Path | None,
    ) -> None:
        protected_paths = {self._resolved_path(source_video)}
        if source_cover is not None:
            protected_paths.add(self._resolved_path(source_cover))

        for candidate in self._legacy_flat_publish_alias_candidates(
            stem=stem,
            source_video=source_video,
            source_cover=source_cover,
        ):
            self._remove_legacy_flat_publish_alias(candidate, protected_paths)

    def _legacy_flat_publish_aliases_present(self, package: PublishingPackage) -> bool:
        if not package.source_export_path:
            return False
        source_video = Path(package.source_export_path)
        if not source_video.exists():
            return False
        source_cover = Path(package.cover_path) if package.cover_path else None
        protected_paths = {self._resolved_path(source_video)}
        if source_cover is not None:
            protected_paths.add(self._resolved_path(source_cover))
        return any(
            self._is_legacy_flat_publish_alias(candidate, protected_paths)
            for candidate in self._legacy_flat_publish_alias_candidates(
                stem=self._published_stem(package),
                source_video=source_video,
                source_cover=source_cover,
            )
        )

    @staticmethod
    def _legacy_flat_publish_alias_candidates(
        *,
        stem: str,
        source_video: Path,
        source_cover: Path | None,
    ) -> list[Path]:
        candidates = [
            source_video.parent / f"{stem}{source_video.suffix}",
            source_video.parent / f"{stem}.txt",
        ]
        cover_suffixes = {".jpg"}
        if source_cover is not None:
            cover_suffixes.add(source_cover.suffix or ".jpg")
        candidates.extend(
            source_video.parent / f"{stem}{suffix}" for suffix in sorted(cover_suffixes)
        )
        return list(dict.fromkeys(candidates))

    @classmethod
    def _remove_legacy_flat_publish_alias(
        cls,
        candidate: Path,
        protected_paths: set[Path],
    ) -> None:
        if not cls._is_legacy_flat_publish_alias(candidate, protected_paths):
            return
        try:
            candidate.unlink()
            log("copywriter", f"removed legacy published alias path={candidate}")
        except OSError as exc:
            log(
                "copywriter",
                "legacy published alias cleanup skipped "
                f"path={candidate} reason={exc.__class__.__name__}",
            )

    @classmethod
    def _is_legacy_flat_publish_alias(
        cls,
        candidate: Path,
        protected_paths: set[Path],
    ) -> bool:
        return (
            cls._resolved_path(candidate) not in protected_paths
            and candidate.exists()
            and candidate.is_file()
        )

    @staticmethod
    def _resolved_path(path: Path) -> Path:
        try:
            return path.resolve(strict=False)
        except OSError:
            return path.absolute()

    @staticmethod
    def _link_or_copy_file(source: Path, target: Path) -> Path | None:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if target.exists():
                if target.samefile(source):
                    return target
                target.unlink()
            os.link(source, target)
            return target
        except OSError:
            return CopywriterService._copy_file(source, target)

    @staticmethod
    def _copy_file(source: Path, target: Path) -> Path | None:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if target.exists():
                if target.samefile(source):
                    return target
                target.unlink()
            shutil.copy2(source, target)
            return target
        except OSError as exc:
            log(
                "copywriter",
                "publish file copy skipped "
                f"source={source} target={target} reason={exc.__class__.__name__}",
            )
            return None

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

    def _select_headline_cues(
        self,
        cues: list[_SubtitleCue],
        signal_cues: list[_SubtitleCue],
        highlight_plan: HighlightPlanAsset | None,
    ) -> list[_SubtitleCue]:
        if not cues:
            return []

        signal_keys = {
            (cue.started_at_seconds, cue.ended_at_seconds, cue.text)
            for cue in signal_cues
        }
        scored: list[tuple[int, float, _SubtitleCue]] = []
        for cue in cues:
            score = self._headline_score(cue.text)
            if (cue.started_at_seconds, cue.ended_at_seconds, cue.text) in signal_keys:
                score += 3
            if self._overlaps_highlight_keyword(cue, highlight_plan):
                score += 5
            if score <= 0:
                continue
            scored.append((-score, cue.started_at_seconds, cue))

        if not scored:
            return signal_cues or cues

        selected: list[_SubtitleCue] = []
        seen: set[tuple[float, float, str]] = set()
        for _, _, cue in sorted(scored, key=lambda item: (item[0], item[1], item[2].ended_at_seconds)):
            key = (cue.started_at_seconds, cue.ended_at_seconds, cue.text)
            if key in seen:
                continue
            selected.append(cue)
            seen.add(key)
            if len(selected) >= 5:
                break
        return selected

    @staticmethod
    def _overlaps_highlight_keyword(
        cue: _SubtitleCue,
        highlight_plan: HighlightPlanAsset | None,
    ) -> bool:
        if highlight_plan is None:
            return False
        return any(
            window.reason == "highlight_keyword"
            and min(cue.ended_at_seconds, window.ended_at_seconds)
            > max(cue.started_at_seconds, window.started_at_seconds)
            for window in highlight_plan.windows
        )

    def _headline_score(self, text: str) -> int:
        score = 0
        if self._contains_all(text, ["电刀", "AP", "机器人"]):
            score += 42
        elif self._contains_any(text, ["电刀", "机器人"]):
            score += 16
        if self._contains_any(text, ["上单", "韩服", "千分", "套路"]):
            score += 8
        if self._contains_any(text, ["清线"]) and self._contains_any(
            text,
            ["伤害高", "伤害这么高", "什么伤害"],
        ):
            score += 10
        elif self._contains_any(text, ["清线"]):
            score += 5
        elif self._contains_any(text, ["伤害高", "伤害这么高", "什么伤害"]):
            score += 2
        if self._contains_all(text, ["装", "没钱"]):
            score += 18
        if self._contains_any(text, ["人设", "有钱", "小康"]):
            score += 6
        if self._contains_any(text, ["炒股", "股票"]):
            score += 14
        if self._contains_any(text, ["粉丝", "认出来", "认出", "认识"]):
            score += 6
        if self._contains_any(text, ["教学", "续费", "为师", "师的名号", "坑了"]):
            score += 7
        if self._contains_any(text, ["击杀", "杀你", "杀一波", "单杀"]):
            score += 5
        if self._contains_any(text, ["小龙", "打团", "团战", "胜利"]):
            score += 4
        if len(text) <= 4:
            score -= 3
        return score

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
        headline = self._summary_headline(excerpt)
        primary = self._expanded_title_if_needed(
            headline=headline,
            first=first,
            excerpt=excerpt,
        )
        candidates = [primary]
        if not first.endswith(("?", "？", "!", "！")):
            candidates.append(f"{first}｜对局高光")
        joined = self._truncate(" ".join(excerpt[:2]), 36)
        candidates.append(f"这一波聊到重点：{joined}")
        candidates.append(f"第{match_index:02d}局高光：{self._truncate(excerpt[0], 18)}")
        return self._dedupe(candidates)

    def _expanded_title_if_needed(
        self,
        *,
        headline: str,
        first: str,
        excerpt: list[str],
    ) -> str:
        primary = headline or first
        if not self._title_needs_context(primary):
            return primary

        parts = [primary] if primary else []
        for line in excerpt:
            phrase = self._title_context_phrase(line)
            if not phrase:
                continue
            if any(self._title_phrases_overlap(phrase, part) for part in parts):
                continue
            parts.append(phrase)
            if len(parts) >= 3 and self._compact_title_length(" ".join(parts)) >= 22:
                break

        expanded = " ".join(parts).strip()
        return self._truncate(expanded, 42) if expanded else primary

    def _title_needs_context(self, title: str) -> bool:
        if not title.strip():
            return True
        if self._compact_title_length(title) >= 10:
            return False
        return not self._is_strong_compact_title(title)

    def _is_strong_compact_title(self, title: str) -> bool:
        return self._contains_any(
            title,
            [
                "电刀",
                "AP",
                "机器人",
                "上单",
                "韩服",
                "千分",
                "清线",
                "伤害",
                "装没钱",
                "人设",
                "炒股",
                "股票",
                "教学",
                "击杀",
                "单杀",
                "小龙",
                "团战",
            ],
        )

    def _title_context_phrase(self, line: str) -> str:
        cleaned = self._clean_line(line)
        if not cleaned:
            return ""
        return self._truncate(cleaned, 18)

    @staticmethod
    def _title_phrases_overlap(first: str, second: str) -> bool:
        if not first or not second:
            return False
        return first in second or second in first

    @staticmethod
    def _compact_title_length(title: str) -> int:
        return len(re.sub(r"[\s｜:：,，。！？!?、；;]+", "", title))

    def _summary_headline(self, excerpt: list[str]) -> str:
        text = " ".join(excerpt)
        phrases: list[str] = []
        has_persona = self._contains_all(text, ["装", "没钱"])
        has_finance = self._contains_any(text, ["炒股", "股票"])
        has_generic_damage = self._contains_any(
            text,
            ["伤害高", "伤害这么高", "什么伤害"],
        )
        if self._contains_all(text, ["电刀", "AP", "机器人"]):
            phrases.append("电刀AP机器人")
        elif "机器人" in text:
            phrases.append("机器人套路")
        elif "电刀" in text:
            phrases.append("电刀出装")
        if self._contains_any(text, ["上单", "韩服", "千分"]):
            if "上单" in text and phrases:
                phrases[0] = f"上单{phrases[0]}"
            elif "上单" in text:
                phrases.append("上单套路")
            if self._contains_any(text, ["韩服", "千分"]):
                phrases.append("韩服千分套路")
        if "清线" in text and has_generic_damage:
            phrases.append("清线快伤害高")
        if has_persona:
            phrases.append("装没钱人设")
        if has_finance:
            phrases.append("炒股经济学")
        if (
            has_generic_damage
            and "清线" not in text
            and not (has_persona or has_finance)
        ):
            phrases.append("伤害这么高")
        if self._contains_any(text, ["粉丝", "认出来", "认出", "认识"]):
            phrases.append("被粉丝认出来")
        if self._contains_any(text, ["教学", "续费", "为师", "师的名号"]):
            phrases.append("在线教学不收续费")
        if self._contains_any(text, ["坑了", "坑"]):
            phrases.append("坑了别爆师门")
        if self._contains_any(text, ["击杀", "杀你", "杀一波", "单杀"]):
            phrases.append("一波击杀打开局面")
        if self._contains_any(text, ["小龙", "打团", "团战"]):
            phrases.append("小龙团战节奏")

        headline = " ".join(self._dedupe(phrases)[:4])
        return self._truncate(headline, 42) if headline else ""

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
        headline = self._summary_headline(excerpt)
        text = self._cover_text(title_text=title_text, headline=headline)
        if not text:
            text = self._truncate(" ".join(excerpt[:2]), 56)
        return self._split_cover_lines(text, max_chars=12, max_lines=4)

    def _cover_text(self, *, title_text: str, headline: str) -> str:
        if not headline:
            return title_text
        if not title_text:
            return headline

        title_phrases = self._cover_phrases(title_text)
        headline_phrases = self._cover_phrases(headline)
        if len(headline_phrases) > len(title_phrases):
            return headline
        return title_text

    @staticmethod
    def _cover_phrases(text: str) -> list[str]:
        normalized = re.sub(r"[，。！？、；;:：|｜]+", " ", text).strip()
        return [item for item in normalized.split() if item]

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

    @staticmethod
    def _contains_any(text: str, keywords: list[str]) -> bool:
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _contains_all(text: str, keywords: list[str]) -> bool:
        return all(keyword in text for keyword in keywords)

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

    def _replace_latest_package_row(self, package: PublishingPackage) -> None:
        if not self.publishing_packages_path.exists():
            append_model(self.publishing_packages_path, package)
            return

        target_key = self._key(package.session_id, package.match_index)
        lines: list[str] = []
        replace_indexes: list[int] = []
        for raw_line in self.publishing_packages_path.read_text(
            encoding="utf-8"
        ).splitlines():
            if not raw_line.strip():
                continue
            line_index = len(lines)
            try:
                existing = PublishingPackage.model_validate(json.loads(raw_line))
            except (json.JSONDecodeError, ValueError):
                lines.append(raw_line)
                continue
            if self._key(existing.session_id, existing.match_index) == target_key:
                replace_indexes.append(line_index)
            lines.append(raw_line)

        if not replace_indexes:
            append_model(self.publishing_packages_path, package)
            return

        lines[replace_indexes[-1]] = json.dumps(
            package.model_dump(mode="json"),
            ensure_ascii=False,
        )
        self.publishing_packages_path.write_text(
            "\n".join(lines).rstrip() + "\n",
            encoding="utf-8",
        )

    def _publishing_package_outputs_exist(self, package: PublishingPackage) -> bool:
        if package.path is None or not Path(package.path).exists():
            return False
        if package.cover_path is not None and not Path(package.cover_path).exists():
            return False
        if (
            package.published_package_dir is not None
            and not Path(package.published_package_dir).is_dir()
        ):
            return False

        source_export_exists = (
            package.source_export_path is not None
            and Path(package.source_export_path).exists()
        )
        if source_export_exists and package.published_package_dir is None:
            return False
        if self._legacy_flat_publish_aliases_present(package):
            return False
        has_published_aliases = any(
            path is not None
            for path in (
                package.published_video_path,
                package.published_cover_path,
                package.published_metadata_path,
            )
        )
        if not source_export_exists and not has_published_aliases:
            return True

        if (
            package.published_video_path is None
            or not Path(package.published_video_path).exists()
        ):
            return False
        if (
            package.published_metadata_path is None
            or not Path(package.published_metadata_path).exists()
        ):
            return False
        if package.cover_path is not None and (
            package.published_cover_path is None
            or not Path(package.published_cover_path).exists()
        ):
            return False
        return True

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
        export: ExportAsset | None,
        recording: RecordingAsset | None,
        boundary: MatchBoundary | None,
        highlight_plan: HighlightPlanAsset | None,
    ) -> PublishingPackage:
        source_path: Path | None = None
        use_source_timeline = False
        if recording is not None:
            recording_path = Path(recording.path)
            if recording_path.exists():
                source_path = recording_path
                use_source_timeline = True
        if source_path is None:
            export_path_raw = export.path if export is not None else package.source_export_path
            if export_path_raw is not None:
                export_path = Path(export_path_raw)
                if export_path.exists():
                    source_path = export_path
        if source_path is None:
            return package
        cover_path = (
            self.settings.storage.processed_dir
            / package.session_id
            / f"match-{package.match_index:02d}-cover.jpg"
        )
        at_seconds = (
            self._cover_source_time(package, boundary, highlight_plan)
            if use_source_timeline
            else 0.0
        )
        try:
            rendered = self.cover_renderer(
                source_path,
                cover_path,
                package.cover_lines,
                at_seconds=at_seconds,
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

    def _streamer_names_by_session(self) -> dict[str, str]:
        names: dict[str, str] = {}
        for path in self._orchestrator_state_paths():
            if not path.exists():
                continue
            try:
                state = OrchestratorStateFile.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                log(
                    "copywriter",
                    "streamer metadata unavailable "
                    f"path={path} reason={exc.__class__.__name__}",
                )
                continue
            for session in state.sessions:
                if session.streamer_name.strip():
                    names[session.session_id] = session.streamer_name
        return names

    def _orchestrator_state_paths(self) -> list[Path]:
        paths = [self.settings.orchestrator.state_file]
        selected_root = self.settings.storage.temp_dir / "selected-recordings"
        if selected_root.exists():
            try:
                paths.extend(sorted(selected_root.glob("*/orchestrator-state.json")))
            except OSError:
                return paths
        return paths

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
