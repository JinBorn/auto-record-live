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
    MatchBoundary,
    RecordingAsset,
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

        from arl.vision.frame_sampler import sample_frame_window
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
