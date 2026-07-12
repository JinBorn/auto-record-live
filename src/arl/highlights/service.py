from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from arl.config import Settings
from arl.highlights.models import ClassifiedCue, HighlightPlannerStateFile
from arl.media.recording_resolver import (
    recording_primary_video_path,
    resolve_recording_window,
)
from arl.shared.contracts import (
    HighlightClipWindow,
    HighlightPlanAsset,
    KdaEventCue,
    MatchBoundary,
    RecordingAsset,
    SubtitleAsset,
)
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log
from arl.shared.semantic_contracts import SemanticAssetView
from arl.shared.semantic_ids import semantic_reference_id


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


@dataclass(frozen=True)
class _CombatActivitySample:
    at_seconds: float
    activity: float


_SPEECH_BOUNDARY_TOLERANCE_SECONDS = 0.15
_SPEECH_CHAIN_GAP_SECONDS = 0.6
_CONDENSED_DURATION_BUDGET_MULTIPLIER = 1.25
_CONDENSED_DURATION_BUDGET_EXTRA_SECONDS = 60.0

_COMBAT_KEYWORDS = tuple(
    item.lower()
    for item in (
        "fight",
        "teamfight",
        "gank",
        "chase",
        "escape",
        "all in",
        "团战",
        "打团",
        "打架",
        "开团",
        "抓",
        "追",
        "逃",
        "撤",
        "反杀",
        "越塔",
    )
)


def condensed_duration_budget(
    target_duration_seconds: float,
    match_duration_seconds: float,
) -> float:
    """Plan-duration cap for condensed plans; shared with quality-report."""
    if target_duration_seconds <= 0.0:
        return match_duration_seconds
    budget = max(
        target_duration_seconds * _CONDENSED_DURATION_BUDGET_MULTIPLIER,
        target_duration_seconds + _CONDENSED_DURATION_BUDGET_EXTRA_SECONDS,
    )
    return min(match_duration_seconds, budget)


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
        self.semantic_assets_path = (
            settings.storage.temp_dir / "copywriter-semantic-assets.jsonl"
        )
        self.state_path = settings.storage.temp_dir / "highlight-planner-state.json"
        self._active_semantic_reference: list[
            tuple[HighlightClipWindow, float, str]
        ] = []

    def run(
        self,
        *,
        session_ids: set[str] | None = None,
        match_indices: set[int] | None = None,
        force_reprocess: bool = False,
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
        semantic_asset_map = {
            (asset.session_id, asset.match_index): asset
            for asset in load_models(self.semantic_assets_path, SemanticAssetView)
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
            self._active_semantic_reference = self._semantic_reference_for_plan(
                existing_plan,
                semantic_asset_map.get((boundary.session_id, boundary.match_index)),
            )
            existing_plan_matches = self._plan_matches_boundary(existing_plan, boundary)
            if key in processed_keys and existing_plan_matches and not force_reprocess:
                continue
            if existing_plan_matches and not force_reprocess:
                if key not in processed_keys:
                    state.processed_match_keys.append(key)
                    processed_keys.add(key)
                continue
            if existing_plan is not None and force_reprocess:
                log(
                    "highlights",
                    "force replanning highlight plan "
                    f"session_id={boundary.session_id} match_index={boundary.match_index}",
                )
            elif existing_plan is not None:
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

    @staticmethod
    def _clip_cues_to_duration(cues: list[_SrtCue], duration: float) -> list[_SrtCue]:
        if duration <= 0.0:
            return []
        clipped: list[_SrtCue] = []
        for cue in cues:
            if cue.started_at_seconds >= duration:
                continue
            end_seconds = min(cue.ended_at_seconds, duration)
            if end_seconds <= cue.started_at_seconds:
                continue
            clipped.append(
                _SrtCue(
                    started_at_seconds=cue.started_at_seconds,
                    ended_at_seconds=end_seconds,
                    text=cue.text,
                )
            )
        return clipped

    def _clamp_highlight_windows(
        self,
        windows: list[HighlightClipWindow],
        duration: float,
    ) -> list[HighlightClipWindow]:
        if duration <= 0.0:
            return []
        min_seconds = self.settings.highlights.condensed_min_window_duration_seconds
        clamped: list[HighlightClipWindow] = []
        for window in windows:
            start_seconds = max(0.0, window.started_at_seconds)
            end_seconds = min(duration, window.ended_at_seconds)
            is_boundary_context = (
                window.reason == "condensed_match_context"
                and (start_seconds <= 0.001 or end_seconds >= duration - 0.001)
            )
            if end_seconds - start_seconds < min_seconds and not is_boundary_context:
                continue
            clamped.append(
                HighlightClipWindow(
                    started_at_seconds=start_seconds,
                    ended_at_seconds=end_seconds,
                    reason=window.reason,
                )
            )
        return self._merge_clip_windows(clamped)

    @classmethod
    def _merge_clip_windows(
        cls,
        windows: list[HighlightClipWindow],
    ) -> list[HighlightClipWindow]:
        ordered = sorted(
            windows,
            key=lambda window: (window.started_at_seconds, window.ended_at_seconds),
        )
        merged: list[HighlightClipWindow] = []
        for window in ordered:
            if not merged:
                merged.append(window)
                continue
            previous = merged[-1]
            if window.started_at_seconds <= previous.ended_at_seconds + 0.001:
                merged[-1] = HighlightClipWindow(
                    started_at_seconds=previous.started_at_seconds,
                    ended_at_seconds=max(previous.ended_at_seconds, window.ended_at_seconds),
                    reason=cls._merge_clip_reason(previous.reason, window.reason),
                )
                continue
            merged.append(window)
        return merged

    @staticmethod
    def _merge_clip_reason(first: str, second: str) -> str:
        priorities = {
            "highlight_keyword": 0,
            "condensed_key_event": 1,
            "condensed_tactical": 2,
            "condensed_continuity": 3,
            "condensed_match_context": 4,
            "match_start_context": 5,
            "match_end_context": 5,
        }
        return min(
            (first, second),
            key=lambda reason: (priorities.get(reason, 100), reason),
        )

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

    def _find_recording_asset(self, boundary: MatchBoundary) -> RecordingAsset | None:
        recording_assets_path = self.settings.storage.temp_dir / "recording-assets.jsonl"
        if not recording_assets_path.exists():
            return None

        recordings = load_models(recording_assets_path, RecordingAsset)
        latest: RecordingAsset | None = None
        for rec in recordings:
            if rec.session_id != boundary.session_id:
                continue
            latest = rec
        return latest

    def _find_recording_video_path(self, boundary: MatchBoundary) -> Path | None:
        recording = self._find_recording_asset(boundary)
        if recording is None:
            return None
        primary_path = recording_primary_video_path(recording)
        if primary_path is not None and primary_path.exists():
            return primary_path
        return None

    def _detect_kda_event_cues(
        self,
        *,
        recording: RecordingAsset | None,
        boundary: MatchBoundary,
        duration: float,
    ) -> list[ClassifiedCue]:
        if (
            recording is None
            or not self.settings.highlights.condensed_kda_event_detection_enabled
        ):
            return []

        from arl.vision.frame_sampler import sample_every_frame_window, sample_frame_window
        from arl.vision.kda_ocr import read_kda

        try:
            samples = self._sample_kda_frames(
                recording,
                boundary=boundary,
                sample_frame_window=sample_frame_window,
            )
        except RuntimeError as exc:
            log(
                "highlights",
                "skip KDA event detection "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"reason=sample_failed detail={exc}",
            )
            return []

        previous: tuple[float, int, int, int] | None = None
        last_death_observed_at: float | None = None
        events: list[ClassifiedCue] = []
        for timestamp_seconds, frame in samples:
            reading = read_kda(
                frame,
                timestamp_seconds,
                crop_region=self.settings.highlights.condensed_kda_crop_region,
            )
            if (
                reading.kills is None
                or reading.deaths is None
                or reading.assists is None
                or reading.confidence
                < self.settings.highlights.condensed_kda_min_confidence
            ):
                continue

            current = (
                reading.timestamp_seconds,
                reading.kills,
                reading.deaths,
                reading.assists,
            )
            if previous is None:
                previous = current
                continue

            previous_ts, previous_kills, previous_deaths, previous_assists = previous
            current_ts, current_kills, current_deaths, current_assists = current
            if current_ts <= previous_ts:
                continue

            kill_delta = current_kills - previous_kills
            death_delta = current_deaths - previous_deaths
            assist_delta = current_assists - previous_assists
            if kill_delta < 0 or death_delta < 0 or assist_delta < 0:
                continue

            reading_gap = current_ts - previous_ts
            if (
                reading_gap
                > self.settings.highlights.condensed_kda_max_reading_gap_seconds
            ):
                previous = current
                continue

            if (
                kill_delta + death_delta
                > self.settings.highlights.condensed_kda_max_event_delta
            ):
                previous = current
                continue

            if kill_delta > 0 or death_delta > 0:
                refined = current_ts
                if self.settings.highlights.condensed_kda_frame_refinement_enabled:
                    refined = self._refine_kda_change_timestamp(
                        recording,
                        previous=previous,
                        current=current,
                        sample_every_frame_window=sample_every_frame_window,
                        read_kda=read_kda,
                    )
                if refined is None:
                    # A single coarse OCR change is not sufficient evidence for
                    # a frame-timed edit. False positives are worse than an
                    # omitted coin hit.
                    previous = current
                    continue
                current_ts = refined
                current_relative_seconds = current_ts - boundary.started_at_seconds
                previous_relative_seconds = previous_ts - boundary.started_at_seconds
                suppression_seconds = (
                    self.settings.highlights.condensed_kda_post_death_kill_suppression_seconds
                )
                if (
                    death_delta == 0
                    and kill_delta > 0
                    and last_death_observed_at is not None
                    and suppression_seconds > 0.0
                    and current_relative_seconds - last_death_observed_at
                    <= suppression_seconds
                ):
                    previous = current
                    continue

                preroll_seconds = (
                    self.settings.highlights.condensed_kda_death_preroll_seconds
                    if death_delta > 0
                    else self.settings.highlights.condensed_kda_kill_preroll_seconds
                )
                event_start = max(
                    0.0,
                    previous_ts - boundary.started_at_seconds - preroll_seconds,
                )
                event_end = min(
                    duration,
                    current_ts
                    - boundary.started_at_seconds
                    + self.settings.highlights.condensed_kda_postroll_seconds,
                )
                if event_end <= event_start:
                    event_end = min(duration, event_start + 1.0)
                events.append(
                    ClassifiedCue(
                        started_at_seconds=event_start,
                        ended_at_seconds=event_end,
                        text=(
                            "kda_change "
                            f"kills={previous_kills}->{current_kills} "
                            f"deaths={previous_deaths}->{current_deaths} "
                            f"previous_at={previous_relative_seconds:.3f} "
                            f"current_at={current_relative_seconds:.3f}"
                        ),
                        category="key_event",
                        priority=self.settings.highlights.condensed_priority_key_event,
                    )
                )
                if death_delta > 0:
                    last_death_observed_at = current_relative_seconds

            previous = current

        return events

    def _refine_kda_change_timestamp(
        self,
        recording: RecordingAsset,
        *,
        previous: tuple[float, int, int, int],
        current: tuple[float, int, int, int],
        sample_every_frame_window,
        read_kda,
    ) -> float | None:
        """Return the first frame of a stable KDA change inside a coarse span."""
        previous_ts, previous_kills, previous_deaths, previous_assists = previous
        current_ts, current_kills, current_deaths, current_assists = current
        target = (current_kills, current_deaths, current_assists)
        baseline = (previous_kills, previous_deaths, previous_assists)
        spans = resolve_recording_window(
            recording,
            start_seconds=previous_ts,
            end_seconds=current_ts,
        )
        consecutive_required = 3
        first_target_at: float | None = None
        consecutive = 0
        saw_baseline = False
        for span in spans:
            path = Path(span.path)
            if not path.exists():
                continue
            for local_ts, frame in sample_every_frame_window(
                path, span.local_start_seconds, span.local_end_seconds
            ):
                source_ts = span.source_start_seconds + (
                    local_ts - span.local_start_seconds
                )
                reading = read_kda(
                    frame,
                    source_ts,
                    crop_region=self.settings.highlights.condensed_kda_crop_region,
                )
                value = (reading.kills, reading.deaths, reading.assists)
                if (
                    None in value
                    or reading.confidence
                    < self.settings.highlights.condensed_kda_min_confidence
                ):
                    continue
                if value == baseline:
                    saw_baseline = True
                    first_target_at = None
                    consecutive = 0
                elif value == target and saw_baseline:
                    if consecutive == 0:
                        first_target_at = source_ts
                    consecutive += 1
                    if consecutive >= consecutive_required:
                        return first_target_at
                else:
                    first_target_at = None
                    consecutive = 0
        return None

    def _sample_kda_frames(
        self,
        recording: RecordingAsset,
        *,
        boundary: MatchBoundary,
        sample_frame_window,
    ) -> list[tuple[float, object]]:
        spans = resolve_recording_window(
            recording,
            start_seconds=boundary.started_at_seconds,
            end_seconds=boundary.ended_at_seconds,
        )
        if not spans:
            log(
                "highlights",
                "skip KDA event detection "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                "reason=recording_window_unavailable",
            )
            return []

        samples: list[tuple[float, object]] = []
        for span in spans:
            span_path = Path(span.path)
            if not span_path.exists():
                log(
                    "highlights",
                    "skip KDA span sampling "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"reason=missing_span path={span_path}",
                )
                continue
            span_samples = sample_frame_window(
                span_path,
                span.local_start_seconds,
                span.local_end_seconds,
                interval_seconds=(
                    self.settings.highlights.condensed_kda_sample_interval_seconds
                ),
            )
            for local_timestamp_seconds, frame in span_samples:
                source_timestamp_seconds = span.source_start_seconds + (
                    local_timestamp_seconds - span.local_start_seconds
                )
                samples.append((source_timestamp_seconds, frame))
        samples.sort(key=lambda item: item[0])
        return samples

    def _trim_silent_kda_death_waits(
        self,
        windows: list[HighlightClipWindow],
        *,
        kda_event_cues: list[ClassifiedCue],
        speech_cues: list[_SrtCue],
        classified_cues: list[ClassifiedCue],
    ) -> list[HighlightClipWindow]:
        threshold = self.settings.highlights.condensed_kda_death_silent_gap_trim_seconds
        if not windows or not kda_event_cues or threshold <= 0.0:
            return windows

        death_cues = [
            cue for cue in kda_event_cues if self._kda_cue_death_delta(cue) > 0
        ]
        if not death_cues:
            return windows

        min_piece_seconds = self.settings.highlights.condensed_min_window_duration_seconds
        reaction_tail_seconds = (
            self.settings.highlights.condensed_kda_death_reaction_tail_seconds
        )
        death_lead_in_guard_seconds = max(5.0, reaction_tail_seconds)
        trimmed: list[HighlightClipWindow] = []
        trimmed_gap_count = 0
        for window in windows:
            ranges = [(window.started_at_seconds, window.ended_at_seconds)]
            if window.reason == "condensed_key_event":
                for cue in death_cues:
                    if not self._ranges_overlap(
                        window.started_at_seconds,
                        window.ended_at_seconds,
                        cue.started_at_seconds,
                        cue.ended_at_seconds,
                    ):
                        continue
                    observed_at = self._kda_cue_time(cue, "current_at")
                    if observed_at is None:
                        continue
                    if reaction_tail_seconds > 0.0:
                        reaction_end = min(
                            cue.ended_at_seconds,
                            observed_at + reaction_tail_seconds,
                        )
                        if reaction_end > window.ended_at_seconds:
                            ranges = [
                                (
                                    range_start,
                                    reaction_end
                                    if range_end == window.ended_at_seconds
                                    else range_end,
                                )
                                for range_start, range_end in ranges
                            ]
                    search_start = max(
                        cue.started_at_seconds,
                        observed_at
                        - self.settings.highlights.condensed_kda_death_silent_trim_lookback_seconds,
                    )
                    search_end = min(cue.ended_at_seconds, observed_at)
                    for gap_start, gap_end in self._subtitle_silent_gaps(
                        speech_cues,
                        start_seconds=search_start,
                        end_seconds=search_end,
                        min_gap_seconds=threshold,
                    ):
                        if observed_at - gap_end <= death_lead_in_guard_seconds:
                            continue
                        next_ranges = []
                        remove_start = min(gap_end, gap_start + reaction_tail_seconds)
                        if gap_end - remove_start < threshold:
                            continue
                        for range_start, range_end in ranges:
                            next_ranges.extend(
                                self._subtract_range(
                                    range_start,
                                    range_end,
                                    remove_start,
                                    gap_end,
                                    min_piece_seconds=min_piece_seconds,
                                )
                            )
                        if len(next_ranges) != len(ranges):
                            trimmed_gap_count += 1
                        ranges = next_ranges

            for range_start, range_end in ranges:
                if range_end <= range_start:
                    continue
                trimmed.append(
                    HighlightClipWindow(
                        started_at_seconds=range_start,
                        ended_at_seconds=range_end,
                        reason=window.reason,
                    )
                )

        trimmed = self._trim_post_death_low_value_waits(
            trimmed,
            kda_event_cues=kda_event_cues,
            classified_cues=classified_cues,
        )

        if trimmed_gap_count:
            log(
                "highlights",
                "trimmed silent KDA death gaps "
                f"count={trimmed_gap_count} threshold={threshold:.1f}s",
            )

        return sorted(trimmed, key=lambda item: (item.started_at_seconds, item.ended_at_seconds))

    def _extend_action_resolution_windows(
        self,
        windows: list[HighlightClipWindow],
        *,
        classified_cues: list[ClassifiedCue],
    ) -> list[HighlightClipWindow]:
        tail_seconds = self.settings.highlights.condensed_action_resolution_tail_seconds
        gap_seconds = self.settings.highlights.condensed_action_resolution_gap_seconds
        if not windows or tail_seconds <= 0.0:
            return windows

        resolution_cues = sorted(
            (
                cue
                for cue in classified_cues
                if cue.category in {"key_event", "tactical", "narration"}
                and not cue.text.startswith("kda_change ")
            ),
            key=lambda cue: (cue.started_at_seconds, cue.ended_at_seconds),
        )
        if not resolution_cues:
            return windows

        ordered = sorted(windows, key=lambda item: (item.started_at_seconds, item.ended_at_seconds))
        extended: list[HighlightClipWindow] = []
        extended_count = 0
        for index, window in enumerate(ordered):
            new_end = window.ended_at_seconds
            if window.reason in {"condensed_key_event", "condensed_tactical"}:
                limit = window.ended_at_seconds + tail_seconds
                if index + 1 < len(ordered):
                    next_start = ordered[index + 1].started_at_seconds
                    if next_start > window.ended_at_seconds:
                        limit = min(limit, next_start)

                cursor = window.ended_at_seconds
                for cue in resolution_cues:
                    if cue.ended_at_seconds <= cursor:
                        continue
                    if cue.started_at_seconds > limit:
                        break
                    if cue.started_at_seconds - cursor > gap_seconds:
                        break

                    new_end = max(new_end, min(cue.ended_at_seconds, limit))
                    cursor = max(cursor, cue.ended_at_seconds)
                    if new_end >= limit:
                        break

            if new_end - window.ended_at_seconds > 0.001:
                extended_count += 1
            extended.append(
                HighlightClipWindow(
                    started_at_seconds=window.started_at_seconds,
                    ended_at_seconds=new_end,
                    reason=window.reason,
                )
            )

        if extended_count:
            log(
                "highlights",
                "extended action resolution windows "
                f"count={extended_count} tail={tail_seconds:.1f}s gap={gap_seconds:.1f}s",
            )

        return extended

    def _protect_speech_boundaries(
        self,
        windows: list[HighlightClipWindow],
        *,
        speech_cues: list[_SrtCue],
        match_duration_seconds: float,
        max_extension_seconds: float | None = None,
    ) -> list[HighlightClipWindow]:
        if not windows or not speech_cues or match_duration_seconds <= 0.0:
            return windows

        ordered_cues = sorted(
            speech_cues,
            key=lambda cue: (cue.started_at_seconds, cue.ended_at_seconds),
        )
        protected: list[HighlightClipWindow] = []
        adjusted = 0
        for window in sorted(windows, key=lambda item: (item.started_at_seconds, item.ended_at_seconds)):
            start_seconds = window.started_at_seconds
            end_seconds = window.ended_at_seconds
            if self._is_short_start_context_window(window):
                protected.append(window)
                continue

            for cue in ordered_cues:
                if cue.ended_at_seconds <= start_seconds:
                    continue
                if cue.started_at_seconds >= end_seconds:
                    break
                if cue.started_at_seconds < start_seconds < cue.ended_at_seconds:
                    start_seconds = max(0.0, cue.started_at_seconds)
                    break

            new_end = self._speech_safe_window_end(
                end_seconds,
                speech_cues=ordered_cues,
                match_duration_seconds=match_duration_seconds,
            )
            if max_extension_seconds is not None:
                # Budget-shrink mode: speech protection may only extend a
                # boundary by the cap. Never retreat a boundary here — a
                # retreat could cut back into a protected KDA span and break
                # coverage guarantees.
                start_seconds = max(
                    start_seconds,
                    window.started_at_seconds - max_extension_seconds,
                )
                new_end = min(
                    new_end,
                    max(
                        end_seconds,
                        min(
                            match_duration_seconds,
                            end_seconds + max_extension_seconds,
                        ),
                    ),
                )
            if abs(start_seconds - window.started_at_seconds) > 0.001 or abs(new_end - end_seconds) > 0.001:
                adjusted += 1
            if new_end - start_seconds <= 0.001:
                continue
            protected.append(
                HighlightClipWindow(
                    started_at_seconds=start_seconds,
                    ended_at_seconds=new_end,
                    reason=window.reason,
                )
            )

        if adjusted:
            log(
                "highlights",
                "protected speech boundaries "
                f"count={adjusted} tolerance={_SPEECH_BOUNDARY_TOLERANCE_SECONDS:.2f}s",
            )

        return protected

    @staticmethod
    def _speech_chains(ordered_cues: list[_SrtCue]) -> list[tuple[float, float]]:
        """Merge cues whose gaps are below the chain threshold into spans."""
        chains: list[tuple[float, float]] = []
        for cue in ordered_cues:
            if (
                chains
                and cue.started_at_seconds - chains[-1][1]
                <= _SPEECH_CHAIN_GAP_SECONDS
            ):
                chains[-1] = (
                    chains[-1][0],
                    max(chains[-1][1], cue.ended_at_seconds),
                )
                continue
            chains.append((cue.started_at_seconds, cue.ended_at_seconds))
        return chains

    @staticmethod
    def _speech_chain_exit(
        at_seconds: float,
        speech_chains: list[tuple[float, float]],
    ) -> float:
        """Move a window start forward out of any speech chain it lands in."""
        for chain_start, chain_end in speech_chains:
            if chain_start - _SPEECH_BOUNDARY_TOLERANCE_SECONDS <= at_seconds < chain_end:
                return chain_end
            if chain_start > at_seconds:
                break
        return at_seconds

    @staticmethod
    def _speech_chain_entry(
        at_seconds: float,
        speech_chains: list[tuple[float, float]],
    ) -> float:
        """Move a window end backward out of any speech chain it lands in."""
        result = at_seconds
        for chain_start, chain_end in speech_chains:
            if chain_start >= at_seconds:
                break
            if chain_start < at_seconds < chain_end + _SPEECH_BOUNDARY_TOLERANCE_SECONDS:
                result = chain_start
        return result

    def _is_short_start_context_window(self, window: HighlightClipWindow) -> bool:
        if self.settings.highlights.condensed_start_edge_seconds is None:
            return False
        return (
            window.reason == "condensed_match_context"
            and window.started_at_seconds <= 0.001
            and window.ended_at_seconds
            <= self._condensed_start_edge_seconds() + _SPEECH_BOUNDARY_TOLERANCE_SECONDS
        )

    @staticmethod
    def _speech_safe_window_end(
        end_seconds: float,
        *,
        speech_cues: list[_SrtCue],
        match_duration_seconds: float,
    ) -> float:
        safe_end = end_seconds
        cursor = end_seconds
        for cue in speech_cues:
            if cue.ended_at_seconds <= cursor:
                continue
            if cue.started_at_seconds - cursor > _SPEECH_BOUNDARY_TOLERANCE_SECONDS:
                if cue.started_at_seconds - cursor > _SPEECH_CHAIN_GAP_SECONDS:
                    break
                if safe_end == end_seconds:
                    break
            if cue.started_at_seconds <= cursor + _SPEECH_BOUNDARY_TOLERANCE_SECONDS:
                safe_end = max(safe_end, cue.ended_at_seconds)
                cursor = max(cursor, cue.ended_at_seconds)
                continue
            if cue.started_at_seconds <= cursor + _SPEECH_CHAIN_GAP_SECONDS and safe_end > end_seconds:
                safe_end = max(safe_end, cue.ended_at_seconds)
                cursor = max(cursor, cue.ended_at_seconds)
                continue
            break
        return min(match_duration_seconds, safe_end)

    def _trim_post_death_low_value_waits(
        self,
        windows: list[HighlightClipWindow],
        *,
        kda_event_cues: list[ClassifiedCue],
        classified_cues: list[ClassifiedCue],
    ) -> list[HighlightClipWindow]:
        trim_seconds = self.settings.highlights.condensed_kda_death_wait_trim_seconds
        if not windows or trim_seconds <= 0.0:
            return windows

        death_cues = [
            cue for cue in kda_event_cues if self._kda_cue_death_delta(cue) > 0
        ]
        death_observed_times = [
            observed_at
            for observed_at in (
                self._kda_cue_time(cue, "current_at") for cue in death_cues
            )
            if observed_at is not None
        ]
        if not death_observed_times:
            return windows

        trimmed: list[HighlightClipWindow] = []
        dropped = 0
        shifted = 0
        for window in windows:
            death_observed_at = max(
                (
                    observed_at
                    for observed_at in death_observed_times
                    if observed_at <= window.started_at_seconds
                    and window.started_at_seconds - observed_at <= trim_seconds
                ),
                default=None,
            )
            if death_observed_at is None:
                trimmed.append(window)
                continue
            if any(
                self._ranges_overlap(
                    window.started_at_seconds,
                    window.ended_at_seconds,
                    cue.started_at_seconds,
                    cue.ended_at_seconds,
                )
                for cue in kda_event_cues
            ):
                trimmed.append(window)
                continue

            if window.reason == "condensed_context":
                dropped += 1
                continue

            if window.reason != "condensed_key_event":
                trimmed.append(window)
                continue

            first_important = self._first_subtitle_key_or_tactical_cue(
                classified_cues,
                start_seconds=window.started_at_seconds,
                end_seconds=window.ended_at_seconds,
            )
            if first_important is None:
                trimmed.append(window)
                continue

            new_start = max(
                window.started_at_seconds,
                first_important.started_at_seconds
                - self.settings.highlights.condensed_context_padding_seconds,
            )
            if (
                new_start - window.started_at_seconds
                < self.settings.highlights.condensed_min_window_duration_seconds
            ):
                trimmed.append(window)
                continue
            if (
                window.ended_at_seconds - new_start
                < self.settings.highlights.condensed_min_window_duration_seconds
            ):
                dropped += 1
                continue
            shifted += 1
            trimmed.append(
                HighlightClipWindow(
                    started_at_seconds=new_start,
                    ended_at_seconds=window.ended_at_seconds,
                    reason=window.reason,
                )
            )

        if dropped or shifted:
            log(
                "highlights",
                "trimmed post-death low-value waits "
                f"dropped={dropped} shifted={shifted} window={trim_seconds:.1f}s",
            )

        return trimmed

    def _trim_low_value_internal_gaps(
        self,
        windows: list[HighlightClipWindow],
        *,
        speech_cues: list[_SrtCue],
        kda_event_cues: list[ClassifiedCue],
        classified_cues: list[ClassifiedCue],
        match_duration_seconds: float,
        combat_protected_intervals: list[tuple[float, float]] | None = None,
    ) -> list[HighlightClipWindow]:
        settings = self.settings.highlights
        if (
            not windows
            or not settings.condensed_composite_trim_enabled
            or settings.condensed_internal_gap_trim_seconds <= 0.0
            or match_duration_seconds <= 0.0
        ):
            return windows

        trim_reasons = {"condensed_key_event", "condensed_tactical"}
        min_piece_seconds = settings.condensed_min_window_duration_seconds
        keep_seconds = settings.condensed_internal_gap_keep_seconds
        min_removed_seconds = max(1.0, min_piece_seconds / 2.0)
        protected_intervals = self._merge_time_ranges(
            [
                *self._internal_trim_protected_intervals(
                    kda_event_cues=kda_event_cues,
                    classified_cues=classified_cues,
                    match_duration_seconds=match_duration_seconds,
                ),
                *(combat_protected_intervals or []),
            ]
        )

        trimmed: list[HighlightClipWindow] = []
        trimmed_gap_count = 0
        removed_duration = 0.0
        for window in sorted(
            windows,
            key=lambda item: (item.started_at_seconds, item.ended_at_seconds),
        ):
            ranges = [(window.started_at_seconds, window.ended_at_seconds)]
            if window.reason in trim_reasons:
                for gap_start, gap_end in self._subtitle_silent_gaps(
                    speech_cues,
                    start_seconds=window.started_at_seconds,
                    end_seconds=window.ended_at_seconds,
                    min_gap_seconds=settings.condensed_internal_gap_trim_seconds,
                ):
                    remove_start = gap_start + keep_seconds
                    remove_end = gap_end - keep_seconds
                    if remove_end - remove_start < min_removed_seconds:
                        continue
                    removable_ranges = self._subtract_protected_intervals(
                        [(remove_start, remove_end)],
                        protected_intervals,
                        keep_seconds=keep_seconds,
                        min_piece_seconds=min_removed_seconds,
                    )
                    for candidate_start, candidate_end in removable_ranges:
                        if candidate_end - candidate_start < min_removed_seconds:
                            continue
                        before_duration = sum(end - start for start, end in ranges)
                        next_ranges: list[tuple[float, float]] = []
                        for range_start, range_end in ranges:
                            next_ranges.extend(
                                self._subtract_range(
                                    range_start,
                                    range_end,
                                    candidate_start,
                                    candidate_end,
                                    min_piece_seconds=min_piece_seconds,
                                )
                            )
                        after_duration = sum(end - start for start, end in next_ranges)
                        if after_duration < before_duration - 0.001:
                            trimmed_gap_count += 1
                            removed_duration += before_duration - after_duration
                            ranges = next_ranges

            for range_start, range_end in ranges:
                if range_end <= range_start:
                    continue
                trimmed.append(
                    HighlightClipWindow(
                        started_at_seconds=range_start,
                        ended_at_seconds=range_end,
                        reason=window.reason,
                    )
                )

        if trimmed_gap_count:
            log(
                "highlights",
                "trimmed low-value internal gaps "
                f"count={trimmed_gap_count} removed={removed_duration:.1f}s "
                f"threshold={settings.condensed_internal_gap_trim_seconds:.1f}s "
                f"keep={keep_seconds:.1f}s",
            )

        return sorted(
            trimmed,
            key=lambda item: (item.started_at_seconds, item.ended_at_seconds),
        )

    def _internal_trim_protected_intervals(
        self,
        *,
        kda_event_cues: list[ClassifiedCue],
        classified_cues: list[ClassifiedCue],
        match_duration_seconds: float,
    ) -> list[tuple[float, float]]:
        intervals: list[tuple[float, float]] = []
        for cue in [*classified_cues, *kda_event_cues]:
            if cue.category == "low_value":
                continue
            start_seconds = max(0.0, min(match_duration_seconds, cue.started_at_seconds))
            end_seconds = max(0.0, min(match_duration_seconds, cue.ended_at_seconds))
            if end_seconds > start_seconds:
                intervals.append((start_seconds, end_seconds))

            if not cue.text.startswith("kda_change "):
                continue
            observed_at = self._kda_cue_time(cue, "current_at")
            if observed_at is None:
                continue
            reaction_tail_seconds = (
                self.settings.highlights.condensed_kda_death_reaction_tail_seconds
            )
            guard_seconds = max(5.0, reaction_tail_seconds)
            guard_start = max(0.0, observed_at - guard_seconds)
            guard_end = min(match_duration_seconds, observed_at + reaction_tail_seconds)
            if guard_end > guard_start:
                intervals.append((guard_start, guard_end))

        return self._merge_time_ranges(intervals)

    def _subtract_protected_intervals(
        self,
        ranges: list[tuple[float, float]],
        protected_intervals: list[tuple[float, float]],
        *,
        keep_seconds: float,
        min_piece_seconds: float,
    ) -> list[tuple[float, float]]:
        removable = list(ranges)
        for protected_start, protected_end in protected_intervals:
            guard_start = protected_start - keep_seconds
            guard_end = protected_end + keep_seconds
            next_ranges: list[tuple[float, float]] = []
            for range_start, range_end in removable:
                next_ranges.extend(
                    self._subtract_range(
                        range_start,
                        range_end,
                        guard_start,
                        guard_end,
                        min_piece_seconds=min_piece_seconds,
                    )
                )
            removable = next_ranges
            if not removable:
                break
        return removable

    @staticmethod
    def _merge_time_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
        ordered = sorted((start, end) for start, end in ranges if end > start)
        merged: list[tuple[float, float]] = []
        for start, end in ordered:
            if not merged or start > merged[-1][1] + 0.001:
                merged.append((start, end))
                continue
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        return merged

    def _detect_combat_protected_intervals(
        self,
        *,
        classified_cues: list[ClassifiedCue],
        kda_event_cues: list[ClassifiedCue],
        match_duration_seconds: float,
        windows: list[HighlightClipWindow] | None = None,
        recording: RecordingAsset | None = None,
        boundary: MatchBoundary | None = None,
        activity_samples: list[_CombatActivitySample] | None = None,
    ) -> list[tuple[float, float]]:
        settings = self.settings.highlights
        if (
            not settings.condensed_combat_continuity_enabled
            or match_duration_seconds <= 0.0
        ):
            return []

        anchors = self._merge_time_ranges(
            [
                (cue.started_at_seconds, cue.ended_at_seconds)
                for cue in classified_cues
                if cue.text.startswith("kda_change ")
                or self._is_combat_cue(cue)
            ]
            + [
                (cue.started_at_seconds, cue.ended_at_seconds)
                for cue in kda_event_cues
            ]
        )
        if not anchors:
            return []
        if windows:
            expanded_anchors: list[tuple[float, float]] = []
            for anchor_start, anchor_end in anchors:
                containing = [
                    window
                    for window in windows
                    if window.reason in {"condensed_key_event", "condensed_tactical"}
                    and window.started_at_seconds <= anchor_start
                    and window.ended_at_seconds >= anchor_end
                    and window.ended_at_seconds - window.started_at_seconds
                    <= settings.condensed_combat_safety_cap_seconds
                ]
                if containing:
                    expanded_anchors.append(
                        (
                            min(window.started_at_seconds for window in containing),
                            max(window.ended_at_seconds for window in containing),
                        )
                    )
                else:
                    expanded_anchors.append((anchor_start, anchor_end))
            anchors = self._merge_time_ranges(expanded_anchors)

        lookaround = settings.condensed_combat_lookaround_seconds
        candidate_ranges = self._merge_time_ranges(
            [
                (
                    max(0.0, start - lookaround),
                    min(match_duration_seconds, end + lookaround),
                )
                for start, end in anchors
            ]
        )
        samples = activity_samples
        evidence_mode = "injected"
        if samples is None:
            visual_samples = self._sample_combat_activity(
                candidate_ranges,
                recording=recording,
                boundary=boundary,
            )
            cue_samples = self._combat_cue_activity_samples(
                classified_cues,
                candidate_ranges=candidate_ranges,
            )
            samples = [*visual_samples, *cue_samples]
            evidence_mode = "video+cue" if visual_samples else "cue_only"
        samples = sorted(samples, key=lambda item: item.at_seconds)

        protected: list[tuple[float, float]] = []
        for anchor_start, anchor_end in anchors:
            containing_start, containing_end = next(
                (
                    (start, end)
                    for start, end in candidate_ranges
                    if start <= anchor_start and end >= anchor_end
                ),
                (anchor_start, anchor_end),
            )
            local_samples = [
                sample
                for sample in samples
                if containing_start <= sample.at_seconds <= containing_end
            ]
            start = self._adaptive_combat_edge(
                local_samples,
                anchor_seconds=anchor_start,
                direction=-1,
                boundary_seconds=containing_start,
            )
            end = self._adaptive_combat_edge(
                local_samples,
                anchor_seconds=anchor_end,
                direction=1,
                boundary_seconds=containing_end,
            )
            safety_cap = settings.condensed_combat_safety_cap_seconds
            if end - start > safety_cap:
                if anchor_end - anchor_start >= safety_cap:
                    start, end = anchor_start, anchor_end
                    log(
                        "highlights",
                        "combat anchor exceeds safety cap; preserving anchor "
                        f"anchor={anchor_start:.1f}-{anchor_end:.1f} cap={safety_cap:.1f}s",
                    )
                else:
                    midpoint = (anchor_start + anchor_end) / 2.0
                    start = max(start, midpoint - safety_cap / 2.0)
                    end = min(end, start + safety_cap)
                    start = max(0.0, min(start, anchor_start))
                    end = min(match_duration_seconds, max(end, anchor_end))
                    log(
                        "highlights",
                        "combat continuity safety cap applied "
                        f"anchor={anchor_start:.1f}-{anchor_end:.1f} cap={safety_cap:.1f}s",
                    )
            protected.append((start, end))

        merged = self._merge_time_ranges(protected)
        log(
            "highlights",
            "detected combat continuity intervals "
            f"count={len(merged)} duration={sum(end - start for start, end in merged):.1f}s "
            f"anchors={len(anchors)} evidence={evidence_mode}",
        )
        return merged

    def _combat_cue_activity_samples(
        self,
        classified_cues: list[ClassifiedCue],
        *,
        candidate_ranges: list[tuple[float, float]],
    ) -> list[_CombatActivitySample]:
        settings = self.settings.highlights
        samples: list[_CombatActivitySample] = []
        for cue in classified_cues:
            if cue.text.startswith("kda_change "):
                activity = settings.condensed_combat_enter_activity_threshold
            elif self._is_combat_cue(cue):
                activity = settings.condensed_combat_enter_activity_threshold
            elif cue.category in {"key_event", "tactical", "narration"}:
                activity = settings.condensed_combat_release_activity_threshold
            else:
                continue
            if not any(
                start <= cue.started_at_seconds <= end
                or start <= cue.ended_at_seconds <= end
                for start, end in candidate_ranges
            ):
                continue
            samples.extend(
                [
                    _CombatActivitySample(cue.started_at_seconds, activity),
                    _CombatActivitySample(cue.ended_at_seconds, activity),
                ]
            )
        return samples

    @staticmethod
    def _is_combat_cue(cue: ClassifiedCue) -> bool:
        if cue.category not in {"key_event", "tactical"}:
            return False
        text = cue.text.lower()
        return any(keyword in text for keyword in _COMBAT_KEYWORDS)

    def _adaptive_combat_edge(
        self,
        samples: list[_CombatActivitySample],
        *,
        anchor_seconds: float,
        direction: int,
        boundary_seconds: float,
    ) -> float:
        settings = self.settings.highlights
        relevant = [
            sample
            for sample in samples
            if (sample.at_seconds <= anchor_seconds if direction < 0 else sample.at_seconds >= anchor_seconds)
        ]
        relevant.sort(key=lambda item: item.at_seconds, reverse=direction < 0)
        if not relevant:
            return anchor_seconds

        edge = anchor_seconds
        low_run = 0
        release_samples = settings.condensed_combat_release_samples
        enter_threshold = settings.condensed_combat_enter_activity_threshold
        release_threshold = min(
            enter_threshold,
            settings.condensed_combat_release_activity_threshold,
        )
        # The anchor itself is strong deterministic evidence. Visual samples
        # decide how far continuity extends away from it; they do not need to
        # rediscover the encounter before hysteresis can begin.
        saw_activity = True
        for sample in relevant:
            if sample.activity >= enter_threshold:
                saw_activity = True
                low_run = 0
                edge = sample.at_seconds
                continue
            if saw_activity and sample.activity >= release_threshold:
                low_run = 0
                edge = sample.at_seconds
                continue
            low_run += 1
            if low_run >= release_samples:
                break
            if saw_activity:
                edge = sample.at_seconds
        return max(boundary_seconds, edge) if direction < 0 else min(boundary_seconds, edge)

    def _sample_combat_activity(
        self,
        ranges: list[tuple[float, float]],
        *,
        recording: RecordingAsset | None,
        boundary: MatchBoundary | None,
    ) -> list[_CombatActivitySample]:
        if recording is None or boundary is None:
            return []
        candidate_duration = sum(end - start for start, end in ranges)
        max_visual_duration = (
            self.settings.highlights.condensed_combat_safety_cap_seconds * 2.0
        )
        if candidate_duration > max_visual_duration:
            log(
                "highlights",
                "skip combat visual sampling "
                f"candidate_duration={candidate_duration:.1f}s "
                f"limit={max_visual_duration:.1f}s evidence=cue_only",
            )
            return []
        import cv2

        interval = self.settings.highlights.condensed_combat_sample_interval_seconds
        samples: list[_CombatActivitySample] = []
        for start, end in ranges:
            source_start = boundary.started_at_seconds + start
            source_end = boundary.started_at_seconds + end
            spans = resolve_recording_window(
                recording,
                start_seconds=source_start,
                end_seconds=source_end,
            )
            previous = None
            for span in spans:
                cap = cv2.VideoCapture(str(span.path))
                if not cap.isOpened():
                    continue
                local_seconds = span.local_start_seconds
                while local_seconds <= span.local_end_seconds + 0.001:
                    cap.set(cv2.CAP_PROP_POS_MSEC, local_seconds * 1000.0)
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        local_seconds += interval
                        continue
                    source_seconds = span.source_start_seconds + (
                        local_seconds - span.local_start_seconds
                    )
                    timestamp = source_seconds - boundary.started_at_seconds
                    if timestamp < start - 0.001 or timestamp > end + 0.001:
                        local_seconds += interval
                        continue
                    try:
                        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    except (cv2.error, TypeError):
                        continue
                    height, width = gray.shape
                    if width > 320:
                        gray = cv2.resize(
                            gray,
                            (320, max(1, int(height * 320 / width))),
                            interpolation=cv2.INTER_AREA,
                        )
                    if previous is not None and previous.shape == gray.shape:
                        activity = float(cv2.absdiff(previous, gray).mean() / 255.0)
                        samples.append(_CombatActivitySample(timestamp, activity))
                    previous = gray
                    local_seconds += interval
                cap.release()
        return samples

    def _protect_death_like_continuity_entries(
        self,
        windows: list[HighlightClipWindow],
        *,
        boundary: MatchBoundary,
        recording: RecordingAsset | None,
    ) -> list[HighlightClipWindow]:
        if not windows or recording is None:
            return windows

        from arl.vision.scene_classifier import looks_like_death_screen

        ordered = sorted(
            windows,
            key=lambda item: (item.started_at_seconds, item.ended_at_seconds),
        )
        protected: list[HighlightClipWindow] = [ordered[0]]
        adjusted = 0
        max_entry_gap = max(0.0, self.settings.highlights.keep_edge_seconds)

        for window in ordered[1:]:
            previous = protected[-1]
            gap = window.started_at_seconds - previous.ended_at_seconds
            if (
                window.reason == "condensed_continuity"
                and 0.0 < gap <= max_entry_gap
            ):
                frame = self._sample_boundary_frame(
                    recording,
                    boundary=boundary,
                    relative_seconds=window.started_at_seconds,
                )
                if frame is not None and looks_like_death_screen(frame):
                    window = HighlightClipWindow(
                        started_at_seconds=previous.ended_at_seconds,
                        ended_at_seconds=window.ended_at_seconds,
                        reason=window.reason,
                    )
                    adjusted += 1
            protected.append(window)

        if adjusted:
            log(
                "highlights",
                "protected death-like continuity entries "
                f"count={adjusted} max_entry_gap={max_entry_gap:.1f}s",
            )

        return protected

    def _restore_missing_kda_event_windows(
        self,
        windows: list[HighlightClipWindow],
        *,
        kda_event_cues: list[ClassifiedCue],
        match_duration_seconds: float,
    ) -> list[HighlightClipWindow]:
        if not windows or not kda_event_cues or match_duration_seconds <= 0.0:
            return windows

        restored = list(windows)
        restored_count = 0
        for cue in kda_event_cues:
            if not cue.text.startswith("kda_change "):
                continue
            cue_start = max(0.0, min(match_duration_seconds, cue.started_at_seconds))
            cue_end = max(0.0, min(match_duration_seconds, cue.ended_at_seconds))
            if cue_end <= cue_start:
                continue

            covered = any(
                window.reason in {"highlight_keyword", "condensed_key_event"}
                and window.started_at_seconds <= cue_start + 0.001
                and window.ended_at_seconds >= cue_end - 0.001
                for window in restored
            )
            if covered:
                continue

            restored.append(
                HighlightClipWindow(
                    started_at_seconds=cue_start,
                    ended_at_seconds=cue_end,
                    reason="condensed_key_event",
                )
            )
            restored_count += 1

        if not restored_count:
            return windows

        log(
            "highlights",
            "restored missing KDA event windows "
            f"count={restored_count}",
        )
        return self._clamp_highlight_windows(restored, match_duration_seconds)

    def _shrink_windows_to_budget(
        self,
        windows: list[HighlightClipWindow],
        *,
        kda_event_cues: list[ClassifiedCue],
        speech_cues: list[_SrtCue],
        classified_cues: list[ClassifiedCue],
        target_duration_seconds: float,
        match_duration_seconds: float,
        combat_protected_intervals: list[tuple[float, float]] | None = None,
    ) -> tuple[list[HighlightClipWindow], str | None]:
        """Final-stage duration convergence for condensed plans.

        Runs after the restore/bridge fixpoint, which may legitimately have
        re-inflated the plan past the duration budget (KDA restore, speech
        extension, bridging have no budget awareness). Trims the lowest-value
        window spans first; full KDA cue spans are never trimmed and every cut
        snaps to a speech-safe boundary. Returns the shrunk windows plus an
        exception reason when protected content prevents reaching the budget.
        """
        from arl.highlights.window_optimizer import bridge_highlight_windows

        settings = self.settings.highlights
        if (
            not settings.condensed_budget_shrink_enabled
            or not windows
            or target_duration_seconds <= 0.0
            or match_duration_seconds <= 0.0
        ):
            return windows, None
        budget_seconds = condensed_duration_budget(
            target_duration_seconds,
            match_duration_seconds,
        )
        total = self._clip_window_total_duration(windows)
        if total <= budget_seconds + 1.0:
            return windows, None

        protected_spans = self._merge_time_ranges(
            [
                (
                    max(0.0, min(match_duration_seconds, cue.started_at_seconds)),
                    max(0.0, min(match_duration_seconds, cue.ended_at_seconds)),
                )
                for cue in kda_event_cues
                if cue.text.startswith("kda_change ")
                and cue.ended_at_seconds > cue.started_at_seconds
            ]
            + list(combat_protected_intervals or [])
        )
        ordered_cues = sorted(
            speech_cues,
            key=lambda cue: (cue.started_at_seconds, cue.ended_at_seconds),
        )
        speech_chains = self._speech_chains(ordered_cues)
        trim_step = max(3.0, settings.condensed_budget_trim_step_seconds)
        min_window = max(1.0, settings.condensed_min_window_duration_seconds)
        max_extension = max(
            0.0,
            settings.condensed_budget_max_speech_extension_seconds,
        )
        untrimmable_reasons = {"condensed_match_context"}

        working = sorted(
            windows,
            key=lambda item: (item.started_at_seconds, item.ended_at_seconds),
        )
        original_total = total
        exception_reason: str | None = None

        for _round in range(5):
            working, converged = self._trim_windows_toward_budget(
                working,
                classified_cues=classified_cues,
                protected_spans=protected_spans,
                ordered_cues=ordered_cues,
                speech_chains=speech_chains,
                budget_seconds=budget_seconds,
                match_duration_seconds=match_duration_seconds,
                trim_step=trim_step,
                min_window=min_window,
                untrimmable_reasons=untrimmable_reasons,
            )
            working = self._clamp_highlight_windows(
                working,
                match_duration_seconds,
            )
            working = self._protect_speech_boundaries(
                working,
                speech_cues=ordered_cues,
                match_duration_seconds=match_duration_seconds,
                max_extension_seconds=max_extension,
            )
            working = self._clamp_highlight_windows(
                working,
                match_duration_seconds,
            )
            # Bridge after speech protection (same order as the restore path)
            # so capped protection can never retreat or drop a bridge and
            # reopen a >45s source gap.
            working = bridge_highlight_windows(
                working,
                max_gap_seconds=settings.condensed_boring_gap_threshold_seconds,
                bridge_window_seconds=settings.condensed_continuity_bridge_seconds,
                match_duration=match_duration_seconds,
            )
            working = self._clamp_highlight_windows(
                working,
                match_duration_seconds,
            )
            total = self._clip_window_total_duration(working)
            if total <= budget_seconds + 1.0:
                break
            if not converged:
                # The trim pass bottomed out on protected content; further
                # rounds cannot make progress.
                break

        total = self._clip_window_total_duration(working)
        if total > budget_seconds + 1.0:
            protected_total = sum(end - start for start, end in protected_spans)
            combat_total = sum(
                end - start for start, end in (combat_protected_intervals or [])
            )
            exception_reason = (
                "protected content floor reached: "
                f"plan={total:.0f}s budget={budget_seconds:.0f}s "
                f"protected={protected_total:.0f}s "
                f"combat_protected={combat_total:.0f}s "
                f"combat_intervals={len(combat_protected_intervals or [])}"
            )
        log(
            "highlights",
            "budget shrink "
            f"from={original_total:.1f}s to={total:.1f}s "
            f"budget={budget_seconds:.1f}s target={target_duration_seconds:.1f}s "
            f"exception={'yes' if exception_reason else 'no'}",
        )
        return working, exception_reason

    def _trim_windows_toward_budget(
        self,
        windows: list[HighlightClipWindow],
        *,
        classified_cues: list[ClassifiedCue],
        protected_spans: list[tuple[float, float]],
        ordered_cues: list[_SrtCue],
        speech_chains: list[tuple[float, float]],
        budget_seconds: float,
        match_duration_seconds: float,
        trim_step: float,
        min_window: float,
        untrimmable_reasons: set[str],
    ) -> tuple[list[HighlightClipWindow], bool]:
        """One trim round: repeatedly cut the lowest-value trimmable window.

        Returns (windows, converged) where converged=False means the round
        bottomed out with the duration still above budget.
        """
        working = list(windows)
        # Reserve headroom for the bridge/speech-protect pass that follows.
        inner_budget = budget_seconds * 0.93
        exhausted: set[int] = set()
        density: dict[int, float] = {
            index: self._window_value_density(window, classified_cues)
            for index, window in enumerate(working)
        }
        for _ in range(600):
            total = self._clip_window_total_duration(working)
            if total <= inner_budget:
                return working, True
            candidates = [
                (density[index], index)
                for index, window in enumerate(working)
                if index not in exhausted
                and window.reason not in untrimmable_reasons
                and not self._is_short_start_context_window(window)
                and (window.ended_at_seconds - window.started_at_seconds)
                > min_window + 0.001
            ]
            if not candidates:
                return working, False
            candidates.sort()
            _, index = candidates[0]
            window = working[index]
            # The edit planner requires windows anchored to both boundary
            # edges; never trim the anchored side of an edge window.
            lock_head = window.started_at_seconds <= 0.5
            lock_tail = window.ended_at_seconds >= match_duration_seconds - 0.5
            trimmed = self._trim_single_window(
                window,
                classified_cues=classified_cues,
                protected_spans=protected_spans,
                ordered_cues=ordered_cues,
                speech_chains=speech_chains,
                match_duration_seconds=match_duration_seconds,
                trim_step=trim_step,
                min_window=min_window,
                lock_head=lock_head,
                lock_tail=lock_tail,
            )
            if trimmed is None:
                exhausted.add(index)
                continue
            if not trimmed:
                working.pop(index)
                exhausted = {
                    i if i < index else i - 1 for i in exhausted if i != index
                }
                density = {
                    (i if i < index else i - 1): value
                    for i, value in density.items()
                    if i != index
                }
                continue
            before = working[index]
            working[index] = trimmed[0]
            density[index] = self._window_value_density(
                trimmed[0],
                classified_cues,
            )
            if (
                abs(before.started_at_seconds - trimmed[0].started_at_seconds)
                <= 0.001
                and abs(before.ended_at_seconds - trimmed[0].ended_at_seconds)
                <= 0.001
            ):
                exhausted.add(index)
        return working, False

    def _trim_single_window(
        self,
        window: HighlightClipWindow,
        *,
        classified_cues: list[ClassifiedCue],
        protected_spans: list[tuple[float, float]],
        ordered_cues: list[_SrtCue],
        speech_chains: list[tuple[float, float]],
        match_duration_seconds: float,
        trim_step: float,
        min_window: float,
        lock_head: bool = False,
        lock_tail: bool = False,
    ) -> list[HighlightClipWindow] | None:
        """Trim one step off the lower-value end of a window.

        Returns [new_window] on progress, [] to drop the window entirely, or
        None when the window cannot be trimmed further (protected/speech
        floors reached).
        """
        start = window.started_at_seconds
        end = window.ended_at_seconds
        duration = end - start
        overlapping = [
            span
            for span in protected_spans
            if span[0] < end - 0.001 and span[1] > start + 0.001
        ]
        # Hull of protected content inside this window; cuts stop at the hull.
        hull_start = min((span[0] for span in overlapping), default=None)
        hull_end = max((span[1] for span in overlapping), default=None)

        # A window with no protected content and near-zero cue value is pure
        # filler: drop it outright instead of nibbling at it. Continuity
        # windows are exempt — they bridge source-time gaps and their head
        # anchors death-screen entry protection, so they only ever shrink
        # from the tail, down to bridge size.
        is_continuity = window.reason == "condensed_continuity"
        if (
            not is_continuity
            and not overlapping
            and not lock_head
            and not lock_tail
            and duration <= trim_step * 2.0
        ):
            if self._window_value_density(window, classified_cues) <= 0.01:
                return []

        head_limit = end - min_window
        if hull_start is not None:
            head_limit = min(head_limit, hull_start)
        tail_limit = start + min_window
        if hull_end is not None:
            tail_limit = max(tail_limit, hull_end)

        head_value = self._span_cue_value(
            start,
            min(end, start + trim_step),
            classified_cues,
        )
        tail_value = self._span_cue_value(
            max(start, end - trim_step),
            end,
            classified_cues,
        )

        def try_head() -> HighlightClipWindow | None:
            naive = min(start + trim_step, head_limit)
            if naive <= start + 0.5:
                return None
            # Prefer skipping forward past the sentence the cut lands in;
            # when that overshoots the protected/min-duration limit, retreat
            # to the sentence start instead. Both are speech-safe cuts.
            new_start = self._speech_chain_exit(naive, speech_chains)
            if new_start > head_limit:
                new_start = self._speech_chain_entry(naive, speech_chains)
            new_start = min(new_start, head_limit)
            if new_start <= start + 0.5:
                return None
            return HighlightClipWindow(
                started_at_seconds=round(new_start, 3),
                ended_at_seconds=end,
                reason=window.reason,
            )

        def try_tail() -> HighlightClipWindow | None:
            naive = max(end - trim_step, tail_limit)
            if naive >= end - 0.5:
                return None
            # Prefer extending forward to the sentence end; when the chain
            # runs all the way to the current end (dense speech), retreat to
            # the sentence start instead so dense-subtitle plans can still
            # converge. Both are speech-safe cuts.
            new_end = self._speech_safe_window_end(
                naive,
                speech_cues=ordered_cues,
                match_duration_seconds=match_duration_seconds,
            )
            if new_end >= end - 0.5:
                new_end = self._speech_chain_entry(naive, speech_chains)
            new_end = max(new_end, tail_limit)
            if new_end >= end - 0.5:
                return None
            return HighlightClipWindow(
                started_at_seconds=start,
                ended_at_seconds=round(new_end, 3),
                reason=window.reason,
            )

        attempts: tuple = ()
        if is_continuity:
            attempts = (try_tail,)
        elif head_value <= tail_value:
            attempts = (try_head, try_tail)
        else:
            attempts = (try_tail, try_head)
        if lock_head:
            attempts = tuple(a for a in attempts if a is not try_head)
        if lock_tail:
            attempts = tuple(a for a in attempts if a is not try_tail)
        for attempt in attempts:
            trimmed = attempt()
            if trimmed is not None:
                return [trimmed]
        return None

    def _window_value_density(
        self,
        window: HighlightClipWindow,
        classified_cues: list[ClassifiedCue],
    ) -> float:
        duration = max(0.001, window.ended_at_seconds - window.started_at_seconds)
        base_density = (
            self._span_cue_value(
                window.started_at_seconds,
                window.ended_at_seconds,
                classified_cues,
            )
            / duration
        )
        return base_density * self._semantic_value_multiplier(window)

    def _semantic_reference_for_plan(
        self,
        plan: HighlightPlanAsset | None,
        semantic_asset: SemanticAssetView | None,
    ) -> list[tuple[HighlightClipWindow, float, str]]:
        if (
            plan is None
            or semantic_asset is None
            or not self.settings.llm.story_analysis_enabled
            or self.settings.llm.story_shadow_mode
            or self.settings.llm.semantic_weight <= 0.0
        ):
            return []
        decisions = {
            item.candidate_id: item
            for item in semantic_asset.result.candidate_decisions
        }
        reference: list[tuple[HighlightClipWindow, float, str]] = []
        for window in plan.windows:
            candidate_id = semantic_reference_id(
                "candidate",
                plan.session_id,
                plan.match_index,
                window.started_at_seconds,
                window.ended_at_seconds,
                window.reason,
            )
            decision = decisions.get(candidate_id)
            if decision is None:
                continue
            score = (
                decision.importance_score
                + decision.story_relevance_score
                + decision.emotion_score
                + decision.instructional_score
                + decision.outcome_clarity_score
            ) / 5.0
            reference.append((window, score, decision.recommendation))
        return reference

    def _semantic_value_multiplier(self, window: HighlightClipWindow) -> float:
        if not self._active_semantic_reference:
            return 1.0
        weighted_score = 0.0
        overlap_total = 0.0
        drop_overlap = 0.0
        shorten_overlap = 0.0
        for reference, score, recommendation in self._active_semantic_reference:
            overlap = min(window.ended_at_seconds, reference.ended_at_seconds) - max(
                window.started_at_seconds,
                reference.started_at_seconds,
            )
            if overlap <= 0.0:
                continue
            weighted_score += score * overlap
            overlap_total += overlap
            if recommendation == "drop":
                drop_overlap += overlap
            elif recommendation == "shorten":
                shorten_overlap += overlap
        if overlap_total <= 0.0:
            return 1.0
        weight = self.settings.llm.semantic_weight
        average_score = weighted_score / overlap_total
        recommendation_penalty = (drop_overlap + shorten_overlap * 0.5) / overlap_total
        return max(0.05, 1.0 + weight * average_score - weight * recommendation_penalty)

    @staticmethod
    def _span_cue_value(
        start: float,
        end: float,
        classified_cues: list[ClassifiedCue],
    ) -> float:
        if end <= start:
            return 0.0
        value = 0.0
        for cue in classified_cues:
            overlap = min(end, cue.ended_at_seconds) - max(
                start,
                cue.started_at_seconds,
            )
            if overlap > 0.0:
                value += max(0.0, cue.priority) * overlap
        return value

    def _enforce_condensed_duration_budget(
        self,
        windows: list[HighlightClipWindow],
        *,
        classified_cues: list[ClassifiedCue],
        target_duration_seconds: float,
        match_duration_seconds: float,
    ) -> list[HighlightClipWindow]:
        if (
            not windows
            or not classified_cues
            or target_duration_seconds <= 0.0
            or match_duration_seconds <= 0.0
        ):
            return windows

        current_duration = self._clip_window_total_duration(windows)
        budget_seconds = self._condensed_duration_budget(
            target_duration_seconds,
            match_duration_seconds,
        )
        if current_duration <= budget_seconds + 1.0:
            return windows

        end_edge_seconds = min(
            max(0.0, self.settings.highlights.keep_edge_seconds),
            match_duration_seconds / 2.0,
        )
        start_edge_seconds = min(
            self._condensed_start_edge_seconds(),
            max(0.0, match_duration_seconds - end_edge_seconds),
        )
        bridge_seconds = 0.0
        if (
            self.settings.highlights.condensed_boring_gap_threshold_seconds > 0.0
            and self.settings.highlights.condensed_continuity_bridge_seconds > 0.0
        ):
            bridge_seconds = min(
                max(
                    self.settings.highlights.condensed_continuity_bridge_seconds,
                    2.0,
                ),
                self.settings.highlights.condensed_boring_gap_threshold_seconds / 2.0,
            )
        content_budget = (
            budget_seconds - start_edge_seconds - end_edge_seconds - bridge_seconds * 2.0
        )
        content_budget = min(
            content_budget,
            max(0.0, match_duration_seconds - start_edge_seconds - end_edge_seconds),
        )
        if (
            content_budget
            < self.settings.highlights.condensed_min_window_duration_seconds
        ):
            return windows

        content_window = self._best_budget_content_window(
            classified_cues,
            window_duration_seconds=content_budget,
            match_duration_seconds=match_duration_seconds,
            start_edge_seconds=start_edge_seconds,
            end_edge_seconds=end_edge_seconds,
        )
        if content_window is None:
            return windows

        budgeted: list[HighlightClipWindow] = []
        if start_edge_seconds > 0.0:
            budgeted.append(
                HighlightClipWindow(
                    started_at_seconds=0.0,
                    ended_at_seconds=start_edge_seconds,
                    reason="condensed_match_context",
                )
            )
        budgeted.append(content_window)
        if end_edge_seconds > 0.0:
            budgeted.append(
                HighlightClipWindow(
                    started_at_seconds=max(0.0, match_duration_seconds - end_edge_seconds),
                    ended_at_seconds=match_duration_seconds,
                    reason="condensed_match_context",
                )
            )

        budgeted = self._clamp_highlight_windows(budgeted, match_duration_seconds)
        budgeted_duration = self._clip_window_total_duration(budgeted)
        log(
            "highlights",
            "capped condensed duration budget "
            f"from={current_duration:.1f}s to={budgeted_duration:.1f}s "
            f"budget={budget_seconds:.1f}s target={target_duration_seconds:.1f}s",
        )
        return budgeted

    @staticmethod
    def _condensed_duration_budget(
        target_duration_seconds: float,
        match_duration_seconds: float,
    ) -> float:
        return condensed_duration_budget(
            target_duration_seconds,
            match_duration_seconds,
        )

    def _condensed_start_edge_seconds(self) -> float:
        configured = self.settings.highlights.condensed_start_edge_seconds
        if configured is not None:
            return max(0.0, configured)
        return max(0.0, self.settings.highlights.keep_edge_seconds)

    def _best_budget_content_window(
        self,
        classified_cues: list[ClassifiedCue],
        *,
        window_duration_seconds: float,
        match_duration_seconds: float,
        start_edge_seconds: float,
        end_edge_seconds: float,
    ) -> HighlightClipWindow | None:
        content_start_boundary = start_edge_seconds
        content_end_boundary = match_duration_seconds - end_edge_seconds
        usable_cues = [
            cue
            for cue in classified_cues
            if cue.category != "low_value"
            and min(content_end_boundary, cue.ended_at_seconds)
            > max(content_start_boundary, cue.started_at_seconds)
        ]
        if not usable_cues:
            return None

        window_duration_seconds = min(
            window_duration_seconds,
            max(0.0, content_end_boundary - content_start_boundary),
        )
        if window_duration_seconds <= 0.0:
            return None

        latest_start = max(
            content_start_boundary,
            content_end_boundary - window_duration_seconds,
        )
        best_start = content_start_boundary
        best_score = -1.0
        for cue in usable_cues:
            center = (cue.started_at_seconds + cue.ended_at_seconds) / 2.0
            start = min(
                max(content_start_boundary, center - window_duration_seconds / 2.0),
                latest_start,
            )
            end = min(content_end_boundary, start + window_duration_seconds)
            score = self._score_budget_content_window(usable_cues, start, end)
            score += start * 0.000001
            if score > best_score:
                best_score = score
                best_start = start

        if best_score <= 0.0:
            return None

        best_end = min(
            content_end_boundary,
            best_start + window_duration_seconds,
        )
        overlapping = [
            cue
            for cue in usable_cues
            if self._ranges_overlap(
                best_start,
                best_end,
                cue.started_at_seconds,
                cue.ended_at_seconds,
            )
        ]
        if not overlapping:
            return None
        strongest = max(
            overlapping,
            key=lambda cue: (self._cue_budget_weight(cue), cue.priority),
        )
        return HighlightClipWindow(
            started_at_seconds=best_start,
            ended_at_seconds=best_end,
            reason=self._reason_for_cue_category(strongest.category),
        )

    @classmethod
    def _score_budget_content_window(
        cls,
        cues: list[ClassifiedCue],
        start_seconds: float,
        end_seconds: float,
    ) -> float:
        score = 0.0
        for cue in cues:
            overlap = min(end_seconds, cue.ended_at_seconds) - max(
                start_seconds,
                cue.started_at_seconds,
            )
            if overlap <= 0.0:
                continue
            cue_duration = max(
                0.1,
                cue.ended_at_seconds - cue.started_at_seconds,
            )
            score += cls._cue_budget_weight(cue) * min(1.0, overlap / cue_duration)
        return score

    @staticmethod
    def _cue_budget_weight(cue: ClassifiedCue) -> float:
        category_weight = {
            "key_event": 3.0,
            "tactical": 2.0,
            "narration": 0.5,
        }.get(cue.category, 0.1)
        return category_weight * max(0.0, cue.priority)

    @staticmethod
    def _reason_for_cue_category(category: str) -> str:
        reason_map = {
            "key_event": "condensed_key_event",
            "tactical": "condensed_tactical",
            "narration": "condensed_context",
        }
        return reason_map.get(category, "condensed_context")

    @staticmethod
    def _clip_window_total_duration(windows: list[HighlightClipWindow]) -> float:
        return sum(
            max(0.0, window.ended_at_seconds - window.started_at_seconds)
            for window in windows
        )

    @staticmethod
    def _sample_boundary_frame(
        recording: RecordingAsset,
        *,
        boundary: MatchBoundary,
        relative_seconds: float,
    ) -> object | None:
        from arl.vision.frame_sampler import sample_frame_window

        source_seconds = boundary.started_at_seconds + relative_seconds
        spans = resolve_recording_window(
            recording,
            start_seconds=source_seconds,
            end_seconds=source_seconds + 0.25,
        )
        for span in spans:
            local_seconds = span.local_start_seconds + (
                source_seconds - span.source_start_seconds
            )
            try:
                frames = sample_frame_window(
                    Path(span.path),
                    local_seconds,
                    min(span.local_end_seconds, local_seconds + 0.25),
                    interval_seconds=0.25,
                )
            except RuntimeError:
                continue
            if frames:
                return frames[0][1]
        return None

    @staticmethod
    def _first_subtitle_key_or_tactical_cue(
        classified_cues: list[ClassifiedCue],
        *,
        start_seconds: float,
        end_seconds: float,
    ) -> ClassifiedCue | None:
        candidates = [
            cue
            for cue in classified_cues
            if cue.category in {"key_event", "tactical"}
            and not cue.text.startswith("kda_change ")
            and min(end_seconds, cue.ended_at_seconds)
            > max(start_seconds, cue.started_at_seconds)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda cue: cue.started_at_seconds)

    @staticmethod
    def _subtitle_silent_gaps(
        speech_cues: list[_SrtCue],
        *,
        start_seconds: float,
        end_seconds: float,
        min_gap_seconds: float,
    ) -> list[tuple[float, float]]:
        if end_seconds <= start_seconds:
            return []

        intervals = sorted(
            (
                max(start_seconds, cue.started_at_seconds),
                min(end_seconds, cue.ended_at_seconds),
            )
            for cue in speech_cues
            if min(end_seconds, cue.ended_at_seconds)
            > max(start_seconds, cue.started_at_seconds)
        )

        gaps: list[tuple[float, float]] = []
        cursor = start_seconds
        for interval_start, interval_end in intervals:
            if interval_start - cursor >= min_gap_seconds:
                gaps.append((cursor, interval_start))
            cursor = max(cursor, interval_end)
        if end_seconds - cursor >= min_gap_seconds:
            gaps.append((cursor, end_seconds))
        return gaps

    @staticmethod
    def _subtract_range(
        range_start: float,
        range_end: float,
        remove_start: float,
        remove_end: float,
        *,
        min_piece_seconds: float,
    ) -> list[tuple[float, float]]:
        if remove_end <= range_start or remove_start >= range_end:
            return [(range_start, range_end)]

        pieces: list[tuple[float, float]] = []
        left = (range_start, max(range_start, remove_start))
        right = (min(range_end, remove_end), range_end)
        for start, end in (left, right):
            if end - start >= min_piece_seconds:
                pieces.append((start, end))
        return pieces

    @staticmethod
    def _kda_cue_death_delta(cue: ClassifiedCue) -> int:
        match = re.search(r"deaths=(\d+)->(\d+)", cue.text)
        if match is None:
            return 0
        return int(match.group(2)) - int(match.group(1))

    @staticmethod
    def _kda_cue_time(cue: ClassifiedCue, key: str) -> float | None:
        match = re.search(rf"\b{re.escape(key)}=([0-9]+(?:\.[0-9]+)?)", cue.text)
        return float(match.group(1)) if match is not None else None

    @staticmethod
    def _ranges_overlap(
        first_start: float,
        first_end: float,
        second_start: float,
        second_end: float,
    ) -> bool:
        return min(first_end, second_end) > max(first_start, second_start)

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
        from arl.highlights.window_optimizer import (
            bridge_highlight_windows,
            optimize_windows,
        )

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

        bounded_cues = self._clip_cues_to_duration(cues, duration)
        meaningful_cues = [
            cue for cue in bounded_cues if not self._is_placeholder_text(cue.text)
        ]
        if not meaningful_cues:
            log(
                "highlights",
                "skip condensed plan "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                "reason=meaningful_subtitle_required",
            )
            return None

        recording = self._find_recording_asset(boundary)
        video_path = (
            recording_primary_video_path(recording) if recording is not None else None
        )
        if video_path is not None and not video_path.exists():
            video_path = None

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
        kda_event_cues = self._detect_kda_event_cues(
            recording=recording,
            boundary=boundary,
            duration=duration,
        )
        if kda_event_cues:
            classified_cues.extend(kda_event_cues)
            log(
                "highlights",
                "detected KDA key events "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"count={len(kda_event_cues)}",
            )

        # 2. 内容密度分析
        density_result = analyze_content_density(
            classified_cues=classified_cues,
            match_duration_seconds=duration,
            video_path=video_path if self.settings.highlights.condensed_use_visual_analysis else None,
            weight_highlight_events=self.settings.highlights.condensed_weight_highlight_events,
            weight_narration=self.settings.highlights.condensed_weight_narration,
            weight_visual=self.settings.highlights.condensed_weight_visual,
            weight_baseline=self.settings.highlights.condensed_weight_baseline,
            use_visual_analysis=(
                self.settings.highlights.condensed_use_visual_analysis
                and bool(classified_cues)
            ),
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
            edge_context_seconds=self.settings.highlights.keep_edge_seconds,
            start_edge_context_seconds=self._condensed_start_edge_seconds(),
            bridge_window_seconds=self.settings.highlights.condensed_continuity_bridge_seconds,
            max_continuous_window_seconds=(
                float(self.settings.highlights.condensed_target_duration_range[1]) * 60.0
            ),
        )
        windows = self._trim_silent_kda_death_waits(
            windows,
            kda_event_cues=kda_event_cues,
            speech_cues=meaningful_cues,
            classified_cues=classified_cues,
        )
        windows = self._extend_action_resolution_windows(
            windows,
            classified_cues=classified_cues,
        )
        windows = self._protect_speech_boundaries(
            windows,
            speech_cues=meaningful_cues,
            match_duration_seconds=duration,
        )
        windows = self._clamp_highlight_windows(windows, duration)
        windows = bridge_highlight_windows(
            windows,
            max_gap_seconds=self.settings.highlights.condensed_boring_gap_threshold_seconds,
            bridge_window_seconds=self.settings.highlights.condensed_continuity_bridge_seconds,
            match_duration=duration,
        )
        windows = self._clamp_highlight_windows(windows, duration)
        windows = self._protect_death_like_continuity_entries(
            windows,
            boundary=boundary,
            recording=recording,
        )
        budgeted_windows = self._enforce_condensed_duration_budget(
            windows,
            classified_cues=classified_cues,
            target_duration_seconds=density_result.target_duration_seconds,
            match_duration_seconds=duration,
        )
        if budgeted_windows != windows:
            windows = self._protect_speech_boundaries(
                budgeted_windows,
                speech_cues=meaningful_cues,
                match_duration_seconds=duration,
            )
            windows = self._clamp_highlight_windows(windows, duration)
            windows = bridge_highlight_windows(
                windows,
                max_gap_seconds=self.settings.highlights.condensed_boring_gap_threshold_seconds,
                bridge_window_seconds=self.settings.highlights.condensed_continuity_bridge_seconds,
                match_duration=duration,
            )
            windows = self._clamp_highlight_windows(windows, duration)
            windows = self._protect_death_like_continuity_entries(
                windows,
                boundary=boundary,
                recording=recording,
            )

        restored_windows = self._restore_missing_kda_event_windows(
            windows,
            kda_event_cues=kda_event_cues,
            match_duration_seconds=duration,
        )
        if restored_windows != windows:
            windows = self._protect_speech_boundaries(
                restored_windows,
                speech_cues=meaningful_cues,
                match_duration_seconds=duration,
            )
            windows = self._clamp_highlight_windows(windows, duration)
            windows = bridge_highlight_windows(
                windows,
                max_gap_seconds=self.settings.highlights.condensed_boring_gap_threshold_seconds,
                bridge_window_seconds=self.settings.highlights.condensed_continuity_bridge_seconds,
                match_duration=duration,
            )
            windows = self._clamp_highlight_windows(windows, duration)
            windows = self._protect_death_like_continuity_entries(
                windows,
                boundary=boundary,
                recording=recording,
            )

        combat_protected_intervals = self._detect_combat_protected_intervals(
            classified_cues=classified_cues,
            kda_event_cues=kda_event_cues,
            match_duration_seconds=duration,
            windows=windows,
            recording=recording,
            boundary=boundary,
        )

        trimmed_windows = self._trim_low_value_internal_gaps(
            windows,
            speech_cues=meaningful_cues,
            kda_event_cues=kda_event_cues,
            classified_cues=classified_cues,
            match_duration_seconds=duration,
            combat_protected_intervals=combat_protected_intervals,
        )
        if trimmed_windows != windows:
            windows = self._protect_speech_boundaries(
                trimmed_windows,
                speech_cues=meaningful_cues,
                match_duration_seconds=duration,
            )
            windows = self._clamp_highlight_windows(windows, duration)
            restored_after_trim = self._restore_missing_kda_event_windows(
                windows,
                kda_event_cues=kda_event_cues,
                match_duration_seconds=duration,
            )
            if restored_after_trim != windows:
                windows = self._protect_speech_boundaries(
                    restored_after_trim,
                    speech_cues=meaningful_cues,
                    match_duration_seconds=duration,
                )
                windows = self._clamp_highlight_windows(windows, duration)
            windows = bridge_highlight_windows(
                windows,
                max_gap_seconds=self.settings.highlights.condensed_boring_gap_threshold_seconds,
                bridge_window_seconds=self.settings.highlights.condensed_continuity_bridge_seconds,
                match_duration=duration,
            )
            windows = self._clamp_highlight_windows(windows, duration)
            windows = self._protect_death_like_continuity_entries(
                windows,
                boundary=boundary,
                recording=recording,
            )

        if not windows:
            return None

        windows, budget_exception_reason = self._finalize_condensed_windows(
            windows,
            kda_event_cues=kda_event_cues,
            speech_cues=meaningful_cues,
            classified_cues=classified_cues,
            target_duration_seconds=density_result.target_duration_seconds,
            match_duration_seconds=duration,
            combat_protected_intervals=combat_protected_intervals,
        )
        if not windows:
            return None

        # 4. 构造HighlightPlanAsset
        return HighlightPlanAsset(
            session_id=boundary.session_id,
            match_index=boundary.match_index,
            source_boundary_start_seconds=boundary.started_at_seconds,
            source_boundary_end_seconds=boundary.ended_at_seconds,
            target_duration_seconds=round(density_result.target_duration_seconds, 3),
            budget_seconds=round(
                self._condensed_duration_budget(
                    density_result.target_duration_seconds,
                    duration,
                ),
                3,
            ),
            budget_exception_reason=budget_exception_reason,
            windows=[
                HighlightClipWindow(
                    started_at_seconds=round(w.started_at_seconds, 3),
                    ended_at_seconds=round(w.ended_at_seconds, 3),
                    reason=w.reason,
                )
                for w in windows
            ],
            kda_events=[
                KdaEventCue(
                    started_at_seconds=round(cue.started_at_seconds, 3),
                    ended_at_seconds=round(cue.ended_at_seconds, 3),
                    text=cue.text,
                )
                for cue in kda_event_cues
            ],
            created_at=datetime.now(timezone.utc),
        )

    def _finalize_condensed_windows(
        self,
        windows: list[HighlightClipWindow],
        *,
        kda_event_cues: list[ClassifiedCue],
        speech_cues: list[_SrtCue],
        classified_cues: list[ClassifiedCue],
        target_duration_seconds: float,
        match_duration_seconds: float,
        combat_protected_intervals: list[tuple[float, float]] | None = None,
    ) -> tuple[list[HighlightClipWindow], str | None]:
        """Finalize stable condensed candidates under deterministic hard constraints.

        Everything before this boundary discovers, repairs, and bridges candidate
        windows. Everything inside/after it is allowed to reduce value-ranked
        content, but must preserve KDA spans, speech boundaries, edge anchors,
        source-gap limits, and the persisted duration-budget exception contract.
        Semantic weighting is intentionally injected at this boundary in a later
        step so candidate discovery remains deterministic.
        """
        return self._shrink_windows_to_budget(
            windows,
            kda_event_cues=kda_event_cues,
            speech_cues=speech_cues,
            classified_cues=classified_cues,
            target_duration_seconds=target_duration_seconds,
            match_duration_seconds=match_duration_seconds,
            combat_protected_intervals=combat_protected_intervals,
        )
