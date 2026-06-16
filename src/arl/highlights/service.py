from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from arl.config import Settings
from arl.highlights.models import HighlightPlannerStateFile
from arl.shared.contracts import (
    HighlightClipWindow,
    HighlightPlanAsset,
    MatchBoundary,
    SubtitleAsset,
)
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log


@dataclass(frozen=True)
class _SrtCue:
    started_at_seconds: float
    ended_at_seconds: float
    text: str


@dataclass(frozen=True)
class _WindowDraft:
    started_at_seconds: float
    ended_at_seconds: float
    reason: str


_HIGHLIGHT_KEYWORDS = tuple(
    item.lower()
    for item in [
        "kill",
        "killed",
        "solo kill",
        "double kill",
        "triple kill",
        "quadra kill",
        "penta kill",
        "ace",
        "fight",
        "teamfight",
        "dragon",
        "baron",
        "herald",
        "elder",
        "tower",
        "turret",
        "inhibitor",
        "nexus",
        "base",
        "victory",
        "defeat",
        "game over",
        "\u51fb\u6740",
        "\u6740\u4e86",
        "\u88ab\u6740",
        "\u5355\u6740",
        "\u53cc\u6740",
        "\u4e09\u6740",
        "\u56db\u6740",
        "\u4e94\u6740",
        "\u56e2\u6218",
        "\u6253\u56e2",
        "\u5c0f\u9f99",
        "\u5927\u9f99",
        "\u7537\u7235",
        "\u5148\u950b",
        "\u8fdc\u53e4\u9f99",
        "\u9632\u5fa1\u5854",
        "\u63a8\u5854",
        "\u9ad8\u5730",
        "\u6c34\u6676",
        "\u57fa\u5730",
        "\u7206\u70b8",
        "\u80dc\u5229",
        "\u5931\u8d25",
        "\u7ed3\u675f",
        "\u7ec8\u7ed3",
    ]
)

_TACTICAL_KEYWORDS = tuple(
    item.lower()
    for item in [
        # \u53ec\u5524\u5e08\u6280\u80fd\uff08\u4e2d\u82f1\uff09
        "flash",
        "tp",
        "teleport",
        "ignite",
        "heal",
        "cleanse",
        "exhaust",
        "barrier",
        "ghost",
        "\u95ea\u73b0",
        "\u4f20\u9001",
        "\u70b9\u71c3",
        "\u6cbb\u7597",
        "\u51c0\u5316",
        "\u865a\u5f31",
        "\u5c4f\u969c",
        "\u75be\u8dd1",
        # \u4f4d\u7f6e\u548c\u79fb\u52a8
        "top",
        "mid",
        "bot",
        "jungle",
        "river",
        "lane",
        "bush",
        "\u4e0a\u8def",
        "\u4e2d\u8def",
        "\u4e0b\u8def",
        "\u6253\u91ce",
        "\u91ce\u533a",
        "\u6cb3\u9053",
        "\u7ebf",
        "\u8349\u4e1b",
        "\u4e09\u89d2\u8349",
        "gank",
        "push",
        "retreat",
        "roam",
        "\u56de\u9632",
        "\u6293",
        "\u63a8",
        "\u64a4",
        "\u5b88",
        "\u6e38\u8d70",
        "\u652f\u63f4",
        # \u88c5\u5907\u548c\u7ecf\u6d4e
        "build",
        "item",
        "gold",
        "buy",
        "\u88c5\u5907",
        "\u51fa\u88c5",
        "\u7ecf\u6d4e",
        "\u8865\u5200",
        "\u4e70",
        "\u5408\u6210",
        "\u795e\u8bdd",
        "\u4f20\u8bf4",
        "\u7834\u8d25",
        "\u65e0\u5c3d",
        "\u706b\u70ae",
        "\u7f8a\u5200",
        "\u91d1\u8eab",
        "\u4e2d\u4e9a",
        # \u89c6\u91ce\u548c\u6218\u672f
        "ward",
        "vision",
        "pink",
        "control ward",
        "\u773c",
        "\u89c6\u91ce",
        "\u771f\u773c",
        "\u63a7\u5236\u5b88\u536b",
        "\u8e72",
        "\u57cb\u4f0f",
        "\u53cd\u8e72",
        "\u6392\u773c",
        # \u6280\u80fd\u548cCD
        "ult",
        "ultimate",
        "cd",
        "cooldown",
        "skill",
        "\u5927\u62db",
        "\u6280\u80fd",
        "\u51b7\u5374",
        "\u6ca1\u5927",
        "\u6709\u5927",
        "\u5927\u62db\u597d\u4e86",
        # \u56e2\u961f\u534f\u4f5c
        "group",
        "split",
        "engage",
        "disengage",
        "peel",
        "focus",
        "\u5f00\u56e2",
        "\u96c6\u5408",
        "\u5206\u63a8",
        "\u5e26\u7ebf",
        "\u4fdd\u62a4",
        "\u5207",
        "\u79d2",
        # \u8d44\u6e90\u548c\u76ee\u6807
        "buff",
        "\u7ea2",
        "\u84dd",
        "\u77f3\u5934\u4eba",
        "\u4e09\u72fc",
        "\u6cb3\u87f9",
        # \u6e38\u620f\u72b6\u6001
        "level",
        "\u7ecf\u9a8c",
        "\u7b49\u7ea7",
        "\u590d\u6d3b",
        "\u6cc9\u6c34",
        "\u5175\u7ebf",
        "\u70ae\u8f66",
        "\u8d85\u7ea7\u5175",
    ]
)


class HighlightPlannerService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.boundaries_path = settings.storage.temp_dir / "match-boundaries.jsonl"
        self.subtitles_path = settings.storage.temp_dir / "subtitle-assets.jsonl"
        self.plans_path = settings.storage.temp_dir / "highlight-plans.jsonl"
        self.state_path = settings.storage.temp_dir / "highlight-planner-state.json"

    def run(
        self,
        *,
        session_ids: set[str] | None = None,
        match_indices: set[int] | None = None,
    ) -> None:
        log("highlights", "starting")
        if not self.settings.highlights.enabled:
            log("highlights", "disabled")
            return

        all_boundaries = load_models(self.boundaries_path, MatchBoundary)
        boundaries = self._filter_boundaries(
            all_boundaries,
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
                "highlights",
                "filters "
                f"total_boundaries={len(all_boundaries)} matched_boundaries={len(boundaries)} "
                f"session_ids={session_filter} match_indices={match_index_filter}",
            )

        subtitles = load_models(self.subtitles_path, SubtitleAsset)
        subtitle_map = {(item.session_id, item.match_index): item for item in subtitles}
        state = self._load_state()
        existing_plan_map = {
            (plan.session_id, plan.match_index): plan
            for plan in load_models(self.plans_path, HighlightPlanAsset)
        }
        existing_plan_keys = {
            self._key(session_id, match_index)
            for session_id, match_index in existing_plan_map
        }
        self._compact_state(state, existing_plan_keys)
        processed_keys = set(state.processed_match_keys)

        processed = 0
        emitted = 0
        skipped_missing_subtitle = 0
        skipped_no_plan = 0
        for boundary in boundaries:
            key = self._key(boundary.session_id, boundary.match_index)
            existing_plan = existing_plan_map.get(
                (boundary.session_id, boundary.match_index)
            )
            existing_plan_matches = self._plan_matches_boundary(existing_plan, boundary)
            if key in processed_keys and existing_plan_matches:
                continue
            if existing_plan_matches:
                if key not in processed_keys:
                    state.processed_match_keys.append(key)
                    processed_keys.add(key)
                continue
            if existing_plan is not None:
                log(
                    "highlights",
                    "replanning stale highlight plan "
                    f"session_id={boundary.session_id} match_index={boundary.match_index}",
                )
            if key in processed_keys:
                state.processed_match_keys = [
                    item for item in state.processed_match_keys if item != key
                ]
                processed_keys.discard(key)

            subtitle = subtitle_map.get((boundary.session_id, boundary.match_index))
            if subtitle is None or not Path(subtitle.path).exists():
                skipped_missing_subtitle += 1
                log(
                    "highlights",
                    "skip highlight plan "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    "reason=subtitle_missing",
                )
                continue

            cues = self._parse_srt_cues(Path(subtitle.path))

            # 模式路由：根据 highlights.mode 分发
            if self.settings.highlights.mode == "highlight":
                plan = self._build_highlight_plan(boundary=boundary, cues=cues)
            elif self.settings.highlights.mode == "condensed":
                plan = self._build_condensed_plan(
                    boundary=boundary,
                    cues=cues,
                    subtitle=subtitle,
                )
            else:  # "disabled"
                plan = None

            if plan is None:
                skipped_no_plan += 1
                continue

            append_model(self.plans_path, plan)
            state.processed_match_keys.append(key)
            processed_keys.add(key)
            existing_plan_map[(plan.session_id, plan.match_index)] = plan
            emitted += 1
            processed += 1
            log(
                "highlights",
                "highlight plan written "
                f"session_id={plan.session_id} match_index={plan.match_index} "
                f"windows={len(plan.windows)}",
            )

        self._save_state(state)
        log(
            "highlights",
            "processed_matches="
            f"{processed} emitted_plans={emitted} "
            f"skipped_missing_subtitle={skipped_missing_subtitle} "
            f"skipped_no_plan={skipped_no_plan}",
        )

    def _plan_matches_boundary(
        self,
        plan: HighlightPlanAsset | None,
        boundary: MatchBoundary,
    ) -> bool:
        if plan is None:
            return False
        tolerance_seconds = 1.0
        return (
            abs(plan.source_boundary_start_seconds - boundary.started_at_seconds)
            <= tolerance_seconds
            and abs(plan.source_boundary_end_seconds - boundary.ended_at_seconds)
            <= tolerance_seconds
        )

    def _build_highlight_plan(
        self,
        *,
        boundary: MatchBoundary,
        cues: list[_SrtCue],
    ) -> HighlightPlanAsset | None:
        duration = boundary.ended_at_seconds - boundary.started_at_seconds
        if duration <= 0.0:
            return None
        if not boundary.is_complete:
            return None
        if boundary.confidence <= 0.5:
            return None
        if duration < self.settings.highlights.min_boundary_duration_seconds:
            return None

        meaningful_cues = [
            cue for cue in cues if not self._is_placeholder_text(cue.text)
        ]
        if not meaningful_cues:
            return None

        windows: list[_WindowDraft] = []
        edge_seconds = min(self.settings.highlights.keep_edge_seconds, duration / 2.0)
        if edge_seconds > 0.0:
            windows.append(_WindowDraft(0.0, edge_seconds, "match_start_context"))
            windows.append(
                _WindowDraft(duration - edge_seconds, duration, "match_end_context")
            )

        for cue in meaningful_cues:
            has_keyword = self._has_highlight_keyword(cue.text)
            padding = (
                self.settings.highlights.highlight_padding_seconds
                if has_keyword
                else self.settings.highlights.cue_padding_seconds
            )
            start = max(0.0, cue.started_at_seconds - padding)
            end = min(duration, cue.ended_at_seconds + padding)
            if end <= start:
                continue
            windows.append(
                _WindowDraft(
                    start,
                    end,
                    "highlight_keyword" if has_keyword else "narration",
                )
            )

        windows = self._merge_windows(windows, self.settings.highlights.merge_gap_seconds)
        windows = self._limit_windows(windows, self.settings.highlights.max_windows)
        if not windows:
            return None

        retained_seconds = self._total_duration(windows)
        reduction_seconds = duration - retained_seconds
        if reduction_seconds < self.settings.highlights.min_reduction_seconds:
            return None

        required_retained = min(
            duration,
            max(
                self.settings.highlights.min_retained_seconds,
                duration * self.settings.highlights.min_retained_fraction,
            ),
        )
        if retained_seconds < required_retained:
            return None

        if len(windows) == 1 and self._nearly_covers_duration(windows[0], duration):
            return None

        return HighlightPlanAsset(
            session_id=boundary.session_id,
            match_index=boundary.match_index,
            source_boundary_start_seconds=boundary.started_at_seconds,
            source_boundary_end_seconds=boundary.ended_at_seconds,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=round(window.started_at_seconds, 3),
                    ended_at_seconds=round(window.ended_at_seconds, 3),
                    reason=window.reason,
                )
                for window in windows
            ],
            created_at=datetime.now(timezone.utc),
        )

    def _parse_srt_cues(self, path: Path) -> list[_SrtCue]:
        lines = path.read_text(encoding="utf-8").splitlines()
        cues: list[_SrtCue] = []
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
            text = self._clean_text(" ".join(text_rows))
            if text:
                cues.append(_SrtCue(start_seconds, end_seconds, text))
            index += 1
        return cues

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

    def _merge_windows(
        self,
        windows: list[_WindowDraft],
        merge_gap_seconds: float,
    ) -> list[_WindowDraft]:
        ordered = sorted(
            (window for window in windows if window.ended_at_seconds > window.started_at_seconds),
            key=lambda item: (item.started_at_seconds, item.ended_at_seconds),
        )
        merged: list[_WindowDraft] = []
        for window in ordered:
            if not merged:
                merged.append(window)
                continue
            previous = merged[-1]
            if window.started_at_seconds - previous.ended_at_seconds <= merge_gap_seconds:
                merged[-1] = _WindowDraft(
                    previous.started_at_seconds,
                    max(previous.ended_at_seconds, window.ended_at_seconds),
                    self._merge_reason(previous.reason, window.reason),
                )
                continue
            merged.append(window)
        return merged

    def _limit_windows(
        self,
        windows: list[_WindowDraft],
        max_windows: int,
    ) -> list[_WindowDraft]:
        limited = list(windows)
        while len(limited) > max_windows:
            merge_index = min(
                range(len(limited) - 1),
                key=lambda index: (
                    limited[index + 1].started_at_seconds
                    - limited[index].ended_at_seconds
                ),
            )
            first = limited[merge_index]
            second = limited[merge_index + 1]
            limited[merge_index : merge_index + 2] = [
                _WindowDraft(
                    first.started_at_seconds,
                    max(first.ended_at_seconds, second.ended_at_seconds),
                    self._merge_reason(first.reason, second.reason),
                )
            ]
        return limited

    def _compact_state(
        self,
        state: HighlightPlannerStateFile,
        existing_plan_keys: set[str],
    ) -> None:
        before = len(state.processed_match_keys)
        state.processed_match_keys = [
            key for key in state.processed_match_keys if key in existing_plan_keys
        ]
        after = len(state.processed_match_keys)
        if before != after:
            log(
                "highlights",
                f"compacted planner state processed_keys={before}->{after}",
            )

    def _filter_boundaries(
        self,
        boundaries: list[MatchBoundary],
        *,
        session_ids: set[str] | None,
        match_indices: set[int] | None,
    ) -> list[MatchBoundary]:
        if session_ids is None and match_indices is None:
            return boundaries
        filtered: list[MatchBoundary] = []
        for boundary in boundaries:
            if session_ids is not None and boundary.session_id not in session_ids:
                continue
            if match_indices is not None and boundary.match_index not in match_indices:
                continue
            filtered.append(boundary)
        return filtered

    def _load_state(self) -> HighlightPlannerStateFile:
        if not self.state_path.exists():
            return HighlightPlannerStateFile()
        return HighlightPlannerStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )

    def _save_state(self, state: HighlightPlannerStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _total_duration(windows: list[_WindowDraft]) -> float:
        return sum(window.ended_at_seconds - window.started_at_seconds for window in windows)

    @staticmethod
    def _nearly_covers_duration(window: _WindowDraft, duration: float) -> bool:
        return window.started_at_seconds <= 1.0 and window.ended_at_seconds >= duration - 1.0

    @staticmethod
    def _merge_reason(first: str, second: str) -> str:
        if first == second:
            return first
        if "highlight_keyword" in {first, second}:
            return "highlight_keyword"
        return "merged"

    @staticmethod
    def _clean_text(raw: str) -> str:
        without_tags = re.sub(r"<[^>]+>", "", raw.strip())
        return re.sub(r"\s+", " ", without_tags).strip()

    @staticmethod
    def _is_placeholder_text(text: str) -> bool:
        return "placeholder subtitle generated by local pipeline" in text.lower()

    @staticmethod
    def _has_highlight_keyword(text: str) -> bool:
        normalized = text.lower()
        return any(keyword in normalized for keyword in _HIGHLIGHT_KEYWORDS)

    @staticmethod
    def _key(session_id: str, match_index: int) -> str:
        return f"{session_id}:{match_index}"

    def _build_condensed_plan(
        self,
        *,
        boundary: MatchBoundary,
        cues: list[_SrtCue],
        subtitle: SubtitleAsset,
    ) -> HighlightPlanAsset | None:
        """构建condensed模式的highlight plan。

        流程：
        1. 基本门槛检查（时长、完整性、置信度）
        2. 字幕分类（classify_cues）
        3. 内容密度分析（analyze_content_density）
        4. 窗口优化（optimize_windows）
        5. 构造HighlightPlanAsset
        """
        from pathlib import Path

        from arl.highlights.content_analyzer import analyze_content_density
        from arl.highlights.cue_classifier import classify_cues
        from arl.highlights.window_optimizer import optimize_windows

        duration = boundary.ended_at_seconds - boundary.started_at_seconds
        if duration <= 0.0:
            return None
        if not boundary.is_complete:
            return None
        if boundary.confidence <= 0.5:
            return None

        # condensed模式：放宽最小时长要求（允许短对局）
        min_duration_for_condensed = 360.0  # 6分钟
        if duration < min_duration_for_condensed:
            log(
                "highlights",
                f"skip condensed plan session_id={boundary.session_id} "
                f"match_index={boundary.match_index} "
                f"reason=duration_too_short duration={duration:.1f}s",
            )
            return None

        meaningful_cues = [
            cue for cue in cues if not self._is_placeholder_text(cue.text)
        ]
        if not meaningful_cues:
            return None

        # 1. 字幕分类
        all_tactical_keywords = _TACTICAL_KEYWORDS + tuple(
            kw.lower() for kw in self.settings.highlights.custom_tactical_keywords
        )
        classified_cues = classify_cues(
            cues=[(c.started_at_seconds, c.ended_at_seconds, c.text) for c in meaningful_cues],
            highlight_keywords=_HIGHLIGHT_KEYWORDS,
            tactical_keywords=all_tactical_keywords,
            low_value_min_length=self.settings.highlights.condensed_low_value_min_length,
            low_value_similarity_threshold=self.settings.highlights.condensed_low_value_similarity_threshold,
            low_value_repeat_window_seconds=self.settings.highlights.condensed_low_value_repeat_window_seconds,
        )

        # 2. 内容密度分析
        # 尝试找到对应的录像文件用于视觉分析
        video_path = None
        if self.settings.highlights.condensed_use_visual_analysis:
            recording_assets_path = self.settings.storage.temp_dir / "recording-assets.jsonl"
            if recording_assets_path.exists():
                from arl.shared.contracts import RecordingAsset

                recordings = load_models(recording_assets_path, RecordingAsset)
                for rec in recordings:
                    if rec.session_id == boundary.session_id:
                        recording_path = Path(rec.path)
                        if recording_path.exists() and recording_path.suffix == ".mp4":
                            video_path = recording_path
                            break

        density_result = analyze_content_density(
            classified_cues=classified_cues,
            match_duration_seconds=duration,
            video_path=video_path,
            weight_highlight_events=self.settings.highlights.condensed_weight_highlight_events,
            weight_narration=self.settings.highlights.condensed_weight_narration,
            weight_visual=self.settings.highlights.condensed_weight_visual,
            weight_baseline=self.settings.highlights.condensed_weight_baseline,
            use_visual_analysis=self.settings.highlights.condensed_use_visual_analysis,
            visual_sample_interval=self.settings.highlights.condensed_visual_sample_interval_seconds,
            high_density_threshold=self.settings.highlights.condensed_high_density_threshold,
            low_density_threshold=self.settings.highlights.condensed_low_density_threshold,
            high_density_duration_range=self.settings.highlights.condensed_high_density_duration_range,
            mid_density_duration_range=self.settings.highlights.condensed_mid_density_duration_range,
            low_density_duration_range=self.settings.highlights.condensed_low_density_duration_range,
        )

        # 3. 窗口优化
        windows = optimize_windows(
            classified_cues=classified_cues,
            target_duration_seconds=density_result.target_duration_seconds,
            match_duration_seconds=duration,
            context_padding_seconds=self.settings.highlights.condensed_context_padding_seconds,
            merge_gap_seconds=self.settings.highlights.condensed_merge_gap_seconds,
            min_window_duration_seconds=self.settings.highlights.condensed_min_window_duration_seconds,
            boring_gap_threshold_seconds=self.settings.highlights.condensed_boring_gap_threshold_seconds,
        )

        if not windows:
            return None

        # 4. 构造HighlightPlanAsset
        return HighlightPlanAsset(
            session_id=boundary.session_id,
            match_index=boundary.match_index,
            source_boundary_start_seconds=boundary.started_at_seconds,
            source_boundary_end_seconds=boundary.ended_at_seconds,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=round(w.started_at_seconds, 3),
                    ended_at_seconds=round(w.ended_at_seconds, 3),
                    reason=w.reason,
                )
                for w in windows
            ],
            created_at=datetime.now(timezone.utc),
        )
