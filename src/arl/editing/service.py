from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arl.config import Settings
from arl.copywriter.models import CopywriterSemanticAsset
from arl.editing.audio import (
    BgmLibraryLoadReport,
    BgmLibraryTrack,
    BgmSelectionContext,
    SourceMusicDetection,
    SourceMusicSpan,
    SfxLibraryLoadReport,
    SfxLibraryTrack,
    detect_source_background_music,
    detect_source_background_music_spans,
    ensure_default_editing_audio_assets,
    infer_bgm_context_tags,
    load_bgm_library_report,
    load_sfx_library_report,
    select_bgm_tracks,
)
from arl.editing.models import EditPlannerStateFile
from arl.media.recording_resolver import resolve_recording_window
from arl.orchestrator.models import OrchestratorStateFile
from arl.shared.contracts import (
    AudioBed,
    EditPlanAsset,
    HighlightClipWindow,
    HighlightPlanAsset,
    MatchBoundary,
    RecordingAsset,
    SoundEffectHit,
    SubtitleAsset,
    TimelineSegment,
    TimelineVideoTransform,
    MediaSpan,
)
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log
from arl.subtitles.ass import SrtCue, parse_srt_cues


_REASON_PRIORITY = {
    "highlight_keyword": 0,
    "condensed_key_event": 1,
    "teaser_fallback_top_scored": 2,
    "condensed_tactical": 3,
    "condensed_context": 4,
}

_ZOOM_REASONS = {
    "highlight_keyword",
    "condensed_key_event",
    "condensed_tactical",
    "llm_teaser",
    "teaser_fallback_top_scored",
}
_SFX_REASONS = {"highlight_keyword", "condensed_key_event"}
_SEGMENT_TOLERANCE_SECONDS = 0.001
_BGM_SWITCH_MIN_DURATION_SECONDS = 120.0
_BGM_MIN_FRAGMENT_SECONDS = 3.0
_KDA_KILLS_RE = re.compile(r"\bkills=(\d+)->(\d+)")
_KDA_CURRENT_AT_RE = re.compile(r"\bcurrent_at=([0-9]+(?:\.[0-9]+)?)")
_MULTIKILL_KEYWORDS = (
    "double kill",
    "triple kill",
    "quadra kill",
    "penta kill",
    "\u53cc\u6740",
    "\u4e09\u6740",
    "\u56db\u6740",
    "\u4e94\u6740",
)
_TEASER_SIGNAL_KEYWORDS = (
    (("kda_change",), 36),
    (("单杀", "solo kill"), 34),
    (("五杀", "penta"), 34),
    (("四杀", "quadra"), 32),
    (("三杀", "triple kill"), 30),
    (("双杀", "double kill"), 28),
    (("击杀", "杀你", "杀了", "kill"), 22),
    (("反杀", "越塔", "开团", "团战"), 18),
    (("电刀", "ap", "机器人", "blitz"), 14),
    (("清线", "伤害高", "什么伤害"), 12),
    (("韩服", "千分", "套路"), 10),
    (("粉丝", "认出来", "认出"), 8),
)


@dataclass(frozen=True)
class _KdaKillEvent:
    source_timestamp_seconds: float
    kill_delta: int
    is_multi_kill: bool


@dataclass(frozen=True)
class _SfxCandidate:
    at_seconds: float
    reason: str
    category: str
    segment_index: int


@dataclass(frozen=True)
class _ZoomCandidate:
    source_timestamp_seconds: float
    priority: int
    x_anchor: float
    y_anchor: float
    target: str


@dataclass(frozen=True)
class _BgmSwitchCandidate:
    relative_seconds: float
    weight: int


@dataclass(frozen=True)
class _BgmPhaseInterval:
    phase: str
    start_seconds: float
    end_seconds: float


class EditingPlannerService:
    def __init__(
        self,
        settings: Settings,
        *,
        source_bgm_detector: Callable[..., SourceMusicDetection] | None = None,
        chat_frame_sampler: Callable[..., list[tuple[float, Any]]] | None = None,
    ) -> None:
        self.settings = settings
        self.boundaries_path = settings.storage.temp_dir / "match-boundaries.jsonl"
        self.highlight_plans_path = settings.storage.temp_dir / "highlight-plans.jsonl"
        self.semantic_assets_path = (
            settings.storage.temp_dir / "copywriter-semantic-assets.jsonl"
        )
        self.subtitle_assets_path = settings.storage.temp_dir / "subtitle-assets.jsonl"
        self.recording_assets_path = settings.storage.temp_dir / "recording-assets.jsonl"
        self.edit_plans_path = settings.storage.temp_dir / "edit-plans.jsonl"
        self.state_path = settings.storage.temp_dir / "editing-state.json"
        self.source_bgm_detector = source_bgm_detector or detect_source_background_music
        self.chat_frame_sampler = chat_frame_sampler
        self._source_music_cache: dict[tuple[str, int], SourceMusicDetection] = {}
        self._bgm_library_tracks: list[BgmLibraryTrack] | None = None
        self._sfx_library_tracks: list[SfxLibraryTrack] | None = None

    def run(
        self,
        *,
        session_ids: set[str] | None = None,
        match_indices: set[int] | None = None,
        force_reprocess: bool = False,
    ) -> None:
        log("editing", "starting")
        if not self.settings.editing.enabled:
            log("editing", "disabled")
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
                "editing",
                "filters "
                f"total_boundaries={len(all_boundaries)} matched_boundaries={len(boundaries)} "
                f"session_ids={session_filter} match_indices={match_index_filter}",
            )

        highlight_plan_map = {
            (plan.session_id, plan.match_index): plan
            for plan in load_models(self.highlight_plans_path, HighlightPlanAsset)
        }
        recording_map = self._latest_recording_by_session(
            load_models(self.recording_assets_path, RecordingAsset)
        )
        subtitle_map = {
            (asset.session_id, asset.match_index): asset
            for asset in load_models(self.subtitle_assets_path, SubtitleAsset)
        }
        semantic_map = {
            (asset.session_id, asset.match_index): asset
            for asset in load_models(self.semantic_assets_path, CopywriterSemanticAsset)
        }
        streamer_names = self._streamer_names_by_session()
        existing_edit_plan_map = {
            (plan.session_id, plan.match_index): plan
            for plan in load_models(self.edit_plans_path, EditPlanAsset)
        }
        existing_plan_keys = {
            self._key(session_id, match_index)
            for session_id, match_index in existing_edit_plan_map
        }
        state = self._load_state()
        self._compact_state(state, existing_plan_keys)
        processed_keys = set(state.processed_match_keys)

        processed = 0
        emitted = 0
        skipped_incomplete = 0
        skipped_missing_highlight = 0
        skipped_invalid_highlight = 0
        skipped_no_plan = 0
        emitted_without_teaser = 0

        for boundary in boundaries:
            key = self._key(boundary.session_id, boundary.match_index)
            existing_edit_plan = existing_edit_plan_map.get(
                (boundary.session_id, boundary.match_index)
            )
            recording = recording_map.get(boundary.session_id)
            existing_plan_matches = self._edit_plan_matches_boundary(
                existing_edit_plan,
                boundary,
                recording,
                highlight_plan_map.get((boundary.session_id, boundary.match_index)),
                subtitle_map.get((boundary.session_id, boundary.match_index)),
                semantic_map.get((boundary.session_id, boundary.match_index)),
                streamer_names.get(boundary.session_id),
            )
            if key in processed_keys and existing_plan_matches and not force_reprocess:
                continue
            if existing_plan_matches and not force_reprocess:
                if key not in processed_keys:
                    state.processed_match_keys.append(key)
                    processed_keys.add(key)
                continue
            if existing_edit_plan is not None and force_reprocess:
                log(
                    "editing",
                    "force replanning edit plan "
                    f"session_id={boundary.session_id} match_index={boundary.match_index}",
                )
            elif existing_edit_plan is not None:
                log(
                    "editing",
                    "replanning stale edit plan "
                    f"session_id={boundary.session_id} match_index={boundary.match_index}",
                )
            if key in processed_keys:
                state.processed_match_keys = [
                    item for item in state.processed_match_keys if item != key
                ]
                processed_keys.discard(key)

            if self._is_incomplete_boundary(boundary):
                skipped_incomplete += 1
                continue

            highlight_plan = highlight_plan_map.get(
                (boundary.session_id, boundary.match_index)
            )
            if highlight_plan is None:
                skipped_missing_highlight += 1
                log(
                    "editing",
                    "skip edit plan "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    "reason=missing_highlight_plan",
                )
                continue
            if not self._highlight_plan_matches_boundary(highlight_plan, boundary):
                skipped_invalid_highlight += 1
                log(
                    "editing",
                    "skip edit plan "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    "reason=stale_highlight_plan",
                )
                continue

            plan = self._build_edit_plan(
                boundary,
                highlight_plan,
                recording,
                subtitle_map.get((boundary.session_id, boundary.match_index)),
                semantic_map.get((boundary.session_id, boundary.match_index)),
                streamer_names.get(boundary.session_id),
            )
            if plan is None:
                skipped_no_plan += 1
                continue

            append_model(self.edit_plans_path, plan)
            state.processed_match_keys.append(key)
            processed_keys.add(key)
            existing_edit_plan_map[(plan.session_id, plan.match_index)] = plan
            processed += 1
            emitted += 1
            if not any(segment.role == "teaser" for segment in plan.timeline):
                emitted_without_teaser += 1
            log(
                "editing",
                "edit plan written "
                f"session_id={plan.session_id} match_index={plan.match_index} "
                f"segments={len(plan.timeline)}",
            )

        self._save_state(state)
        log(
            "editing",
            "processed_matches="
            f"{processed} emitted_plans={emitted} "
            f"skipped_incomplete={skipped_incomplete} "
            f"skipped_missing_highlight={skipped_missing_highlight} "
            f"skipped_invalid_highlight={skipped_invalid_highlight} "
            f"skipped_no_plan={skipped_no_plan} "
            f"emitted_without_teaser={emitted_without_teaser}",
        )

    def _build_edit_plan(
        self,
        boundary: MatchBoundary,
        highlight_plan: HighlightPlanAsset,
        recording: RecordingAsset | None,
        subtitle: SubtitleAsset | None,
        semantic_asset: CopywriterSemanticAsset | None,
        streamer_name: str | None,
    ) -> EditPlanAsset | None:
        duration = boundary.ended_at_seconds - boundary.started_at_seconds
        if duration <= 0.0:
            return None

        main_segments = self._build_main_segments(highlight_plan.windows, duration)
        if not main_segments:
            log(
                "editing",
                "skip edit plan "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                "reason=no_valid_main_windows",
            )
            return None

        teaser_windows = self._select_teaser_windows(
            highlight_plan.windows,
            duration,
            planned_export_duration=self._timeline_duration(main_segments),
            subtitle=subtitle,
            semantic_asset=semantic_asset,
        )
        if not teaser_windows:
            log(
                "editing",
                "edit plan omits teaser "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                "reason=no_high_confidence_teaser",
            )

        timeline = [
            TimelineSegment(
                role="teaser",
                source_start_seconds=round(window.started_at_seconds, 3),
                source_end_seconds=round(window.ended_at_seconds, 3),
                reason=window.reason,
            )
            for window in teaser_windows
        ]
        transition_segment = self._build_transition_segment(
            semantic_asset=semantic_asset,
            has_teaser=bool(teaser_windows),
        )
        if transition_segment is not None:
            timeline.append(transition_segment)
        timeline.extend(main_segments)
        self._apply_zoom_transforms(
            timeline,
            boundary=boundary,
            recording=recording,
            subtitle=subtitle,
        )
        audio_beds, sound_effects = self._build_audio_instructions(
            boundary,
            highlight_plan,
            timeline,
            recording,
            subtitle,
            streamer_name,
        )
        return EditPlanAsset(
            session_id=boundary.session_id,
            match_index=boundary.match_index,
            source_boundary_start_seconds=boundary.started_at_seconds,
            source_boundary_end_seconds=boundary.ended_at_seconds,
            timeline=timeline,
            audio_beds=audio_beds,
            sound_effects=sound_effects,
            created_at=datetime.now(timezone.utc),
        )

    def _build_transition_segment(
        self,
        *,
        semantic_asset: CopywriterSemanticAsset | None,
        has_teaser: bool,
    ) -> TimelineSegment | None:
        if not has_teaser:
            return None
        if self.settings.editing.transition_mode != "black_card":
            if self.settings.editing.transition_mode == "crossfade":
                log("editing", "skip transition reason=crossfade_reserved")
            return None
        return TimelineSegment(
            role="transition",
            source_start_seconds=0.0,
            source_end_seconds=0.0,
            duration_seconds=self._transition_duration_seconds(),
            reason="transition_black_card",
            text=self._transition_text(semantic_asset),
        )

    def _transition_text(
        self,
        semantic_asset: CopywriterSemanticAsset | None,
    ) -> str:
        if semantic_asset is not None and semantic_asset.result.hook_line:
            return semantic_asset.result.hook_line.strip()
        return self.settings.editing.transition_text

    def _transition_duration_seconds(self) -> float:
        return round(
            min(10.0, max(0.1, self.settings.editing.transition_duration_seconds)),
            3,
        )

    def _apply_zoom_transforms(
        self,
        timeline: list[TimelineSegment],
        *,
        boundary: MatchBoundary,
        recording: RecordingAsset | None,
        subtitle: SubtitleAsset | None,
    ) -> None:
        if not self.settings.editing.zoom_enabled:
            return
        if self.settings.editing.zoom_mode == "legacy":
            self._apply_legacy_zoom_transforms(timeline)
            return
        selected = self._selected_zoom_candidates(
            self._zoom_candidates(
                timeline,
                boundary=boundary,
                recording=recording,
                subtitle=subtitle,
            )
        )
        if not selected:
            return
        timeline[:] = self._timeline_with_closeups(timeline, selected)

    def _apply_legacy_zoom_transforms(self, timeline: list[TimelineSegment]) -> None:
        remaining = self.settings.editing.zoom_max_segments
        if remaining <= 0:
            return
        x_anchor, y_anchor, target = self._zoom_focus()
        index = 0
        while index < len(timeline):
            segment = timeline[index]
            if segment.role not in {"teaser", "main"} or segment.reason not in _ZOOM_REASONS:
                index += 1
                continue
            if (
                segment.role == "main"
                and self._segment_duration(segment)
                > self.settings.editing.zoom_max_duration_seconds
                + _SEGMENT_TOLERANCE_SECONDS
            ):
                index += 1
                continue
            transform = TimelineVideoTransform(
                kind="punch_in",
                scale=self.settings.editing.zoom_scale,
                x_anchor=x_anchor,
                y_anchor=y_anchor,
                target=target,
                ease_in_seconds=0.0,
                ease_out_seconds=0.0,
            )
            segment.transform = transform
            index += 1
            remaining -= 1
            if remaining <= 0:
                return

    def _selected_zoom_candidates(
        self,
        candidates: list[_ZoomCandidate],
    ) -> list[_ZoomCandidate]:
        if self.settings.editing.zoom_max_segments <= 0:
            return []
        candidates = sorted(
            candidates,
            key=lambda candidate: (
                candidate.priority,
                candidate.source_timestamp_seconds,
                candidate.target,
            ),
        )
        selected: list[_ZoomCandidate] = []
        for candidate in candidates:
            if len(selected) >= self.settings.editing.zoom_max_segments:
                break
            if any(
                abs(candidate.source_timestamp_seconds - existing.source_timestamp_seconds)
                < self.settings.editing.zoom_min_interval_seconds
                for existing in selected
            ):
                continue
            selected.append(candidate)
        return sorted(selected, key=lambda candidate: candidate.source_timestamp_seconds)

    def _zoom_candidates(
        self,
        timeline: list[TimelineSegment],
        *,
        boundary: MatchBoundary,
        recording: RecordingAsset | None,
        subtitle: SubtitleAsset | None,
    ) -> list[_ZoomCandidate]:
        candidates: list[_ZoomCandidate] = []
        kda_x, kda_y, kda_target = self._kda_zoom_focus()
        for event in self._kda_kill_events(subtitle):
            if self._timestamp_in_zoomable_segment(
                event.source_timestamp_seconds,
                timeline,
            ):
                candidates.append(
                    _ZoomCandidate(
                        source_timestamp_seconds=event.source_timestamp_seconds,
                        priority=0,
                        x_anchor=kda_x,
                        y_anchor=kda_y,
                        target=kda_target,
                    )
                )

        candidates.extend(
            self._chat_burst_zoom_candidates(
                timeline,
                boundary=boundary,
                recording=recording,
            )
        )

        primary_timestamps = [
            candidate.source_timestamp_seconds
            for candidate in candidates
            if candidate.priority < 2
        ]
        fallback_x, fallback_y, fallback_target = self._zoom_focus()
        for segment in timeline:
            if not self._segment_can_receive_closeup(segment):
                continue
            if any(
                self._source_timestamp_in_segment(timestamp, segment)
                for timestamp in primary_timestamps
            ):
                continue
            candidates.append(
                _ZoomCandidate(
                    source_timestamp_seconds=(
                        segment.source_start_seconds + self._segment_duration(segment) / 2.0
                    ),
                    priority=2,
                    x_anchor=fallback_x,
                    y_anchor=fallback_y,
                    target=fallback_target,
                )
            )
        return candidates

    def _chat_burst_zoom_candidates(
        self,
        timeline: list[TimelineSegment],
        *,
        boundary: MatchBoundary,
        recording: RecordingAsset | None,
    ) -> list[_ZoomCandidate]:
        if not self.settings.editing.zoom_chat_burst_enabled:
            return []
        if recording is None:
            return []
        recording_path = Path(recording.path)
        if not recording_path.is_file():
            return []
        candidates: list[_ZoomCandidate] = []
        for segment in timeline:
            if not self._segment_can_receive_closeup(segment):
                continue
            try:
                frames = self._sample_chat_frames(
                    recording_path,
                    boundary_start_seconds=boundary.started_at_seconds,
                    segment=segment,
                )
            except Exception as exc:
                log(
                    "editing",
                    "skip chat burst zoom "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"reason={exc.__class__.__name__}",
                )
                continue
            for timestamp in self._chat_burst_timestamps_from_frames(
                frames,
                threshold=self.settings.editing.zoom_chat_burst_threshold,
            ):
                candidates.append(
                    _ZoomCandidate(
                        source_timestamp_seconds=timestamp
                        - boundary.started_at_seconds,
                        priority=1,
                        x_anchor=0.0,
                        y_anchor=1.0,
                        target="chat",
                    )
                )
        return candidates

    def _sample_chat_frames(
        self,
        recording_path: Path,
        *,
        boundary_start_seconds: float,
        segment: TimelineSegment,
    ) -> list[tuple[float, Any]]:
        sampler = self.chat_frame_sampler
        if sampler is None:
            from arl.vision.frame_sampler import sample_frame_window

            sampler = sample_frame_window
        return sampler(
            recording_path,
            boundary_start_seconds + segment.source_start_seconds,
            boundary_start_seconds + segment.source_end_seconds,
            interval_seconds=self.settings.editing.zoom_chat_burst_sample_interval_seconds,
        )

    @classmethod
    def _chat_burst_timestamps_from_frames(
        cls,
        frames: list[tuple[float, Any]],
        *,
        threshold: float,
    ) -> list[float]:
        if len(frames) < 2:
            return []
        scored: list[tuple[float, float]] = []
        previous_crop = cls._chat_region_crop(frames[0][1])
        for timestamp, frame in frames[1:]:
            current_crop = cls._chat_region_crop(frame)
            if current_crop is None or previous_crop is None:
                previous_crop = current_crop
                continue
            if current_crop.shape != previous_crop.shape:
                previous_crop = current_crop
                continue
            score = cls._chat_region_diff_score(previous_crop, current_crop)
            if score >= threshold:
                scored.append((timestamp, score))
            previous_crop = current_crop
        scored.sort(key=lambda item: (-item[1], item[0]))
        return [timestamp for timestamp, _score in scored]

    @staticmethod
    def _chat_region_crop(frame: Any) -> Any | None:
        try:
            import cv2
        except ModuleNotFoundError:
            return None
        if frame is None or not hasattr(frame, "shape"):
            return None
        height, width = frame.shape[:2]
        if height <= 0 or width <= 0:
            return None
        x1 = 0
        x2 = max(1, int(width * 0.36))
        y1 = max(0, int(height * 0.55))
        y2 = max(y1 + 1, int(height * 0.95))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        if len(crop.shape) == 3:
            crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        return crop

    @staticmethod
    def _chat_region_diff_score(previous: Any, current: Any) -> float:
        try:
            import cv2
        except ModuleNotFoundError:
            return 0.0
        diff = cv2.absdiff(previous, current)
        return float(diff.mean() / 255.0)

    def _timeline_with_closeups(
        self,
        timeline: list[TimelineSegment],
        selected: list[_ZoomCandidate],
    ) -> list[TimelineSegment]:
        rebuilt: list[TimelineSegment] = []
        consumed: set[_ZoomCandidate] = set()
        for segment in timeline:
            segment_candidates = [
                candidate
                for candidate in selected
                if candidate not in consumed
                if self._source_timestamp_in_segment(
                    candidate.source_timestamp_seconds,
                    segment,
                )
                and self._segment_can_receive_closeup(segment)
            ]
            if not segment_candidates:
                rebuilt.append(segment)
                continue
            cursor = segment.source_start_seconds
            for candidate in sorted(
                segment_candidates,
                key=lambda item: item.source_timestamp_seconds,
            ):
                window = self._closeup_window(segment, candidate.source_timestamp_seconds)
                if window is None:
                    continue
                window_start, window_end = window
                if window_end <= cursor + _SEGMENT_TOLERANCE_SECONDS:
                    continue
                if window_start > cursor + _SEGMENT_TOLERANCE_SECONDS:
                    rebuilt.append(self._segment_piece(segment, cursor, window_start))
                rebuilt.append(
                    self._segment_piece(
                        segment,
                        max(cursor, window_start),
                        window_end,
                        transform=TimelineVideoTransform(
                            kind="punch_in",
                            scale=self.settings.editing.zoom_scale,
                            x_anchor=candidate.x_anchor,
                            y_anchor=candidate.y_anchor,
                            target=candidate.target,
                            ease_in_seconds=self.settings.editing.zoom_ease_seconds,
                            ease_out_seconds=self.settings.editing.zoom_ease_seconds,
                        ),
                    )
                )
                cursor = max(cursor, window_end)
                consumed.add(candidate)
            if cursor < segment.source_end_seconds - _SEGMENT_TOLERANCE_SECONDS:
                rebuilt.append(self._segment_piece(segment, cursor, segment.source_end_seconds))
        if not any(segment.transform is not None for segment in rebuilt):
            return timeline
        return rebuilt

    def _closeup_window(
        self,
        segment: TimelineSegment,
        source_timestamp_seconds: float,
    ) -> tuple[float, float] | None:
        duration = self._segment_duration(segment)
        if duration < 3.0 - _SEGMENT_TOLERANCE_SECONDS:
            return None
        closeup_duration = min(self.settings.editing.zoom_closeup_seconds, duration)
        window_start = source_timestamp_seconds - closeup_duration / 2.0
        window_start = max(segment.source_start_seconds, window_start)
        window_start = min(window_start, segment.source_end_seconds - closeup_duration)
        window_end = min(segment.source_end_seconds, window_start + closeup_duration)
        if window_end - window_start < 3.0 - _SEGMENT_TOLERANCE_SECONDS:
            return None
        return round(window_start, 3), round(window_end, 3)

    @staticmethod
    def _segment_piece(
        segment: TimelineSegment,
        start_seconds: float,
        end_seconds: float,
        *,
        transform: TimelineVideoTransform | None = None,
    ) -> TimelineSegment:
        return segment.model_copy(
            update={
                "source_start_seconds": round(start_seconds, 3),
                "source_end_seconds": round(end_seconds, 3),
                "transform": transform,
            }
        )

    def _timestamp_in_zoomable_segment(
        self,
        source_timestamp_seconds: float,
        timeline: list[TimelineSegment],
    ) -> bool:
        return any(
            self._segment_can_receive_closeup(segment)
            and self._source_timestamp_in_segment(source_timestamp_seconds, segment)
            for segment in timeline
        )

    def _segment_can_receive_closeup(self, segment: TimelineSegment) -> bool:
        if segment.role not in {"teaser", "main"}:
            return False
        if segment.reason not in _ZOOM_REASONS:
            return False
        return self._segment_duration(segment) >= 3.0 - _SEGMENT_TOLERANCE_SECONDS

    def _zoom_focus(self) -> tuple[float, float, str]:
        target = self.settings.editing.zoom_target
        if target == "chat":
            return 0.0, 1.0, "chat"
        if target == "center":
            return 0.5, 0.5, "center"
        return (
            self.settings.editing.zoom_x_anchor,
            self.settings.editing.zoom_y_anchor,
            "custom",
        )

    def _kda_zoom_focus(self) -> tuple[float, float, str]:
        if self.settings.editing.zoom_target == "chat":
            return 0.5, 0.5, "center"
        return self._zoom_focus()

    def _build_audio_instructions(
        self,
        boundary: MatchBoundary,
        highlight_plan: HighlightPlanAsset,
        timeline: list[TimelineSegment],
        recording: RecordingAsset | None,
        subtitle: SubtitleAsset | None,
        streamer_name: str | None,
    ) -> tuple[list[AudioBed], list[SoundEffectHit]]:
        if not self.settings.editing.audio_mixing_enabled:
            return [], []

        audio_beds: list[AudioBed] = []
        sound_effects: list[SoundEffectHit] = []
        rendered_duration = self._timeline_duration(timeline)
        bgm_start_seconds = self._leading_non_main_duration(timeline)
        bgm_duration = max(0.0, rendered_duration - bgm_start_seconds)
        source_music = self._source_music_detection(boundary, recording)
        source_music_windows, source_music_coverage = (
            self._source_music_avoidance_windows(
                source_music,
                boundary=boundary,
                timeline=timeline,
                bgm_start_seconds=bgm_start_seconds,
                rendered_duration=rendered_duration,
            )
        )
        skip_bgm = self._source_music_requires_global_bgm_skip(
            source_music,
            source_music_coverage=source_music_coverage,
        )
        default_assets = (
            self._default_audio_assets(boundary)
            if self.settings.editing.bgm_path is None
            or self.settings.editing.sfx_path is None
            else {}
        )

        bgm_path = self.settings.editing.bgm_path
        if skip_bgm:
            log(
                "editing",
                "skip bgm because source already has music "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"confidence={source_music.confidence:.3f} "
                f"coverage={source_music_coverage:.3f} reason={source_music.reason}",
            )
        elif bgm_path is not None:
            if bgm_path.is_file():
                audio_beds = [
                    AudioBed(
                        source_path=str(bgm_path),
                        timeline_start_seconds=bgm_start_seconds,
                        timeline_end_seconds=None,
                        gain_db=self.settings.editing.bgm_gain_db,
                        loop=True,
                    )
                ]
            else:
                log(
                    "editing",
                    "skip configured bgm asset "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"path={bgm_path} reason=missing_file",
                )
        else:
            audio_beds.extend(
                self._selected_or_default_bgm_beds(
                    default_assets,
                    boundary=boundary,
                    highlight_plan=highlight_plan,
                    timeline=timeline,
                    subtitle=subtitle,
                    streamer_name=streamer_name,
                    rendered_duration=bgm_duration,
                    timeline_start_seconds=bgm_start_seconds,
                )
            )
        if audio_beds and source_music_windows:
            audio_beds = self._bgm_beds_avoiding_source_music(
                audio_beds,
                source_music_windows,
                rendered_duration=rendered_duration,
            )

        configured_sfx_path = self.settings.editing.sfx_path
        configured_sfx_missing = False
        if configured_sfx_path is not None and not configured_sfx_path.is_file():
            log(
                "editing",
                "skip configured sfx asset "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"path={configured_sfx_path} reason=missing_file",
            )
            configured_sfx_missing = True
            configured_sfx_path = None

        sfx_candidates = (
            []
            if configured_sfx_missing
            else self._sound_effect_candidates(
                timeline,
                subtitle=subtitle,
            )
        )
        kill_sfx_hits = self._sound_effect_hits_from_candidates(
            sfx_candidates,
            configured_sfx_path=configured_sfx_path,
            default_sfx_path=default_assets.get("coin_sfx"),
        )

        transition_sfx_path = self._transition_sfx_path()
        timeline_cursor = 0.0
        for segment in timeline:
            segment_duration = self._segment_duration(segment)
            if segment.role == "transition" and transition_sfx_path is not None:
                sound_effects.append(
                    SoundEffectHit(
                        source_path=str(transition_sfx_path),
                        at_seconds=round(timeline_cursor, 3),
                        gain_db=self.settings.editing.transition_sfx_gain_db,
                        reason=segment.reason,
                    )
                )
            timeline_cursor += max(0.0, segment_duration)
        sound_effects.extend(kill_sfx_hits)
        sound_effects.sort(key=lambda hit: hit.at_seconds)
        return audio_beds, sound_effects

    def _source_music_detection(
        self,
        boundary: MatchBoundary,
        recording: RecordingAsset | None,
    ) -> SourceMusicDetection:
        if not self.settings.editing.skip_bgm_when_source_has_music:
            return SourceMusicDetection(False, 0.0, "disabled")
        if recording is None:
            return SourceMusicDetection(False, 0.0, "missing_recording_asset")
        cache_key = (boundary.session_id, boundary.match_index)
        cached = self._source_music_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            spans = resolve_recording_window(
                recording,
                start_seconds=boundary.started_at_seconds,
                end_seconds=boundary.ended_at_seconds,
            )
            detection = self._detect_source_music_from_spans(
                spans,
                boundary=boundary,
            )
        except Exception as exc:
            log(
                "editing",
                "source music detection skipped "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"reason={exc.__class__.__name__}",
            )
            detection = SourceMusicDetection(False, 0.0, "detector_error")
        self._source_music_cache[cache_key] = detection
        return detection

    def _detect_source_music_from_spans(
        self,
        spans: list[MediaSpan],
        *,
        boundary: MatchBoundary,
    ) -> SourceMusicDetection:
        if not spans:
            return SourceMusicDetection(False, 0.0, "missing_recording_span")
        if len(spans) == 1:
            span = spans[0]
            detection = self.source_bgm_detector(
                Path(span.path),
                start_seconds=span.local_start_seconds,
                end_seconds=span.local_end_seconds,
            )
            return self._translate_source_music_detection(detection, span)
        if self.source_bgm_detector is detect_source_background_music:
            return detect_source_background_music_spans(
                spans,
                start_seconds=boundary.started_at_seconds,
                end_seconds=boundary.ended_at_seconds,
            )

        detections: list[SourceMusicDetection] = []
        source_spans: list[SourceMusicSpan] = []
        for span in spans:
            detection = self.source_bgm_detector(
                Path(span.path),
                start_seconds=span.local_start_seconds,
                end_seconds=span.local_end_seconds,
            )
            translated = self._translate_source_music_detection(detection, span)
            detections.append(translated)
            source_spans.extend(translated.music_spans)
        if not detections:
            return SourceMusicDetection(False, 0.0, "missing_recording_span")
        confidence = round(
            sum(detection.confidence for detection in detections) / len(detections),
            3,
        )
        music_spans = self._merge_source_music_spans(source_spans)
        if music_spans:
            return SourceMusicDetection(
                any(detection.has_music for detection in detections),
                confidence,
                "persistent_music_like_audio"
                if any(detection.has_music for detection in detections)
                else "sampled_music_like_audio",
                music_spans=music_spans,
                coverage_ratio=self._source_music_span_coverage_ratio(
                    music_spans,
                    start_seconds=boundary.started_at_seconds,
                    end_seconds=boundary.ended_at_seconds,
                ),
            )
        has_music_count = sum(1 for detection in detections if detection.has_music)
        required_music_count = max(1, (len(detections) * 3 + 4) // 5)
        has_music = has_music_count >= required_music_count
        return SourceMusicDetection(
            has_music,
            confidence,
            "persistent_music_like_audio" if has_music else "no_persistent_music_bed",
        )

    def _translate_source_music_detection(
        self,
        detection: SourceMusicDetection,
        span: MediaSpan,
    ) -> SourceMusicDetection:
        if not detection.music_spans:
            return detection
        translated = [
            SourceMusicSpan(
                start_seconds=round(
                    span.source_start_seconds
                    + (music_span.start_seconds - span.local_start_seconds),
                    3,
                ),
                end_seconds=round(
                    span.source_start_seconds
                    + (music_span.end_seconds - span.local_start_seconds),
                    3,
                ),
                confidence=music_span.confidence,
            )
            for music_span in detection.music_spans
        ]
        music_spans = self._merge_source_music_spans(translated)
        return SourceMusicDetection(
            detection.has_music,
            detection.confidence,
            detection.reason,
            music_spans=music_spans,
            coverage_ratio=self._source_music_span_coverage_ratio(
                music_spans,
                start_seconds=span.source_start_seconds,
                end_seconds=span.source_end_seconds,
            ),
        )

    @staticmethod
    def _merge_source_music_spans(
        spans: list[SourceMusicSpan],
    ) -> tuple[SourceMusicSpan, ...]:
        merged: list[SourceMusicSpan] = []
        for span in sorted(spans, key=lambda item: (item.start_seconds, item.end_seconds)):
            if span.end_seconds <= span.start_seconds:
                continue
            if not merged:
                merged.append(span)
                continue
            previous = merged[-1]
            if span.start_seconds <= previous.end_seconds + _SEGMENT_TOLERANCE_SECONDS:
                merged[-1] = SourceMusicSpan(
                    start_seconds=previous.start_seconds,
                    end_seconds=max(previous.end_seconds, span.end_seconds),
                    confidence=max(previous.confidence, span.confidence),
                )
            else:
                merged.append(span)
        return tuple(merged)

    @staticmethod
    def _source_music_span_coverage_ratio(
        spans: tuple[SourceMusicSpan, ...],
        *,
        start_seconds: float,
        end_seconds: float,
    ) -> float:
        duration = max(0.0, end_seconds - start_seconds)
        if duration <= 0.0:
            return 0.0
        covered = 0.0
        for span in spans:
            overlap = min(end_seconds, span.end_seconds) - max(
                start_seconds,
                span.start_seconds,
            )
            if overlap > 0.0:
                covered += overlap
        return round(min(1.0, covered / duration), 3)

    def _source_music_avoidance_windows(
        self,
        source_music: SourceMusicDetection,
        *,
        boundary: MatchBoundary,
        timeline: list[TimelineSegment],
        bgm_start_seconds: float,
        rendered_duration: float,
    ) -> tuple[list[tuple[float, float]], float]:
        if not source_music.music_spans:
            return [], 1.0 if source_music.has_music else 0.0
        active_start = bgm_start_seconds
        active_end = rendered_duration
        if active_end <= active_start:
            return [], 0.0
        padding = self.settings.editing.bgm_source_music_padding_seconds
        windows: list[tuple[float, float]] = []
        for music_span in source_music.music_spans:
            span_start = music_span.start_seconds - boundary.started_at_seconds
            span_end = music_span.end_seconds - boundary.started_at_seconds
            for start, end in self._source_interval_to_rendered_windows(
                timeline,
                source_start_seconds=span_start,
                source_end_seconds=span_end,
            ):
                padded_start = max(active_start, start - padding)
                padded_end = min(active_end, end + padding)
                if padded_end - padded_start > _SEGMENT_TOLERANCE_SECONDS:
                    windows.append((round(padded_start, 3), round(padded_end, 3)))
        merged = self._merge_time_windows(windows)
        coverage = self._time_window_coverage_ratio(
            merged,
            start_seconds=active_start,
            end_seconds=active_end,
        )
        return merged, coverage

    def _source_music_requires_global_bgm_skip(
        self,
        source_music: SourceMusicDetection,
        *,
        source_music_coverage: float,
    ) -> bool:
        if source_music.has_music and not source_music.music_spans:
            return True
        return (
            source_music_coverage
            > self.settings.editing.bgm_source_music_majority_threshold
        )

    @classmethod
    def _source_interval_to_rendered_windows(
        cls,
        timeline: list[TimelineSegment],
        *,
        source_start_seconds: float,
        source_end_seconds: float,
    ) -> list[tuple[float, float]]:
        if source_end_seconds <= source_start_seconds:
            return []
        windows: list[tuple[float, float]] = []
        output_cursor = 0.0
        for segment in timeline:
            duration = cls._segment_duration(segment)
            if segment.role == "main":
                overlap_start = max(source_start_seconds, segment.source_start_seconds)
                overlap_end = min(source_end_seconds, segment.source_end_seconds)
                if overlap_end - overlap_start > _SEGMENT_TOLERANCE_SECONDS:
                    windows.append(
                        (
                            round(
                                output_cursor
                                + (overlap_start - segment.source_start_seconds),
                                3,
                            ),
                            round(
                                output_cursor
                                + (overlap_end - segment.source_start_seconds),
                                3,
                            ),
                        )
                    )
            output_cursor += duration
        return windows

    @staticmethod
    def _merge_time_windows(
        windows: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        merged: list[tuple[float, float]] = []
        for start, end in sorted(windows):
            if end <= start:
                continue
            if not merged or start > merged[-1][1] + _SEGMENT_TOLERANCE_SECONDS:
                merged.append((start, end))
                continue
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        return [(round(start, 3), round(end, 3)) for start, end in merged]

    @staticmethod
    def _time_window_coverage_ratio(
        windows: list[tuple[float, float]],
        *,
        start_seconds: float,
        end_seconds: float,
    ) -> float:
        duration = max(0.0, end_seconds - start_seconds)
        if duration <= 0.0:
            return 0.0
        covered = 0.0
        for start, end in windows:
            overlap = min(end_seconds, end) - max(start_seconds, start)
            if overlap > 0.0:
                covered += overlap
        return round(min(1.0, covered / duration), 3)

    @staticmethod
    def _bgm_beds_avoiding_source_music(
        beds: list[AudioBed],
        avoidance_windows: list[tuple[float, float]],
        *,
        rendered_duration: float,
    ) -> list[AudioBed]:
        if not beds or not avoidance_windows:
            return beds
        fragments: list[AudioBed] = []
        for bed in beds:
            bed_start = bed.timeline_start_seconds
            bed_end = bed.timeline_end_seconds or rendered_duration
            intervals = [(bed_start, bed_end)]
            for avoid_start, avoid_end in avoidance_windows:
                next_intervals: list[tuple[float, float]] = []
                for start, end in intervals:
                    if avoid_end <= start or avoid_start >= end:
                        next_intervals.append((start, end))
                        continue
                    if avoid_start - start >= _BGM_MIN_FRAGMENT_SECONDS:
                        next_intervals.append((start, min(end, avoid_start)))
                    if end - avoid_end >= _BGM_MIN_FRAGMENT_SECONDS:
                        next_intervals.append((max(start, avoid_end), end))
                intervals = next_intervals
                if not intervals:
                    break
            for start, end in intervals:
                if end - start < _BGM_MIN_FRAGMENT_SECONDS:
                    continue
                fragments.append(
                    bed.model_copy(
                        update={
                            "timeline_start_seconds": round(start, 3),
                            "timeline_end_seconds": (
                                None
                                if bed.timeline_end_seconds is None
                                and abs(end - rendered_duration)
                                <= _SEGMENT_TOLERANCE_SECONDS
                                else round(end, 3)
                            ),
                        }
                    )
                )
        return fragments

    def _sound_effect_candidates(
        self,
        timeline: list[TimelineSegment],
        *,
        subtitle: SubtitleAsset | None,
    ) -> list[_SfxCandidate]:
        if self.settings.editing.sfx_max_hits <= 0:
            return []
        kda_events = (
            self._kda_kill_events(subtitle)
            if self.settings.editing.sfx_kda_alignment_enabled
            else []
        )
        kda_timestamps = (
            self._kda_event_timestamps(subtitle)
            if self.settings.editing.sfx_kda_alignment_enabled
            else []
        )
        mapped = self._map_kda_events_to_timeline(timeline, kda_events)
        segment_output_starts = self._timeline_output_starts(timeline)
        candidates: list[_SfxCandidate] = []
        segments_with_kda = self._timeline_segments_containing_kda_timestamps(
            timeline,
            kda_timestamps,
        )
        for segment_index, event, at_seconds in mapped:
            candidates.append(
                _SfxCandidate(
                    at_seconds=self._clamped_sfx_time(
                        segment_output_starts[segment_index],
                        self._segment_duration(timeline[segment_index]),
                        at_seconds + self.settings.editing.sfx_timing_offset_seconds,
                    ),
                    reason=timeline[segment_index].reason,
                    category="multi_kill" if event.is_multi_kill else "kill_coin",
                    segment_index=segment_index,
                )
            )

        timeline_cursor = 0.0
        for segment_index, segment in enumerate(timeline):
            segment_duration = self._segment_duration(segment)
            if (
                segment_index not in segments_with_kda
                and self._segment_is_sfx_eligible(segment)
            ):
                candidates.append(
                    _SfxCandidate(
                        at_seconds=round(timeline_cursor, 3),
                        reason=segment.reason,
                        category="kill_coin",
                        segment_index=segment_index,
                    )
                )
            timeline_cursor += max(0.0, segment_duration)

        candidates.sort(
            key=lambda candidate: (
                candidate.at_seconds,
                candidate.segment_index,
                0 if candidate.category == "multi_kill" else 1,
            )
        )
        selected: list[_SfxCandidate] = []
        last_sfx_at: float | None = None
        for candidate in candidates:
            if len(selected) >= self.settings.editing.sfx_max_hits:
                break
            if (
                last_sfx_at is not None
                and candidate.at_seconds - last_sfx_at
                < self.settings.editing.sfx_min_interval_seconds
            ):
                continue
            selected.append(candidate)
            last_sfx_at = candidate.at_seconds
        return selected

    def _sound_effect_hits_from_candidates(
        self,
        candidates: list[_SfxCandidate],
        *,
        configured_sfx_path: Path | None,
        default_sfx_path: Path | None,
    ) -> list[SoundEffectHit]:
        hits: list[SoundEffectHit] = []
        for candidate in candidates:
            track = self._sfx_track_for_category(
                candidate.category,
                configured_sfx_path=configured_sfx_path,
                default_sfx_path=default_sfx_path,
            )
            if track is None:
                continue
            path, gain_db = track
            hits.append(
                SoundEffectHit(
                    source_path=str(path),
                    at_seconds=round(candidate.at_seconds, 3),
                    gain_db=gain_db,
                    reason=candidate.reason,
                )
            )
        return hits

    @staticmethod
    def _segment_is_sfx_eligible(segment: TimelineSegment) -> bool:
        if segment.role not in {"teaser", "main"}:
            return False
        return segment.reason in _SFX_REASONS

    def _transition_sfx_path(self) -> Path | None:
        configured = self.settings.editing.transition_sfx_path
        if configured is not None:
            return configured if configured.is_file() else None
        track = self._first_sfx_library_track("transition_whoosh")
        return track.path if track is not None else None

    def _sfx_track_for_category(
        self,
        category: str,
        *,
        configured_sfx_path: Path | None,
        default_sfx_path: Path | None,
    ) -> tuple[Path, float] | None:
        if configured_sfx_path is not None:
            return configured_sfx_path, self.settings.editing.sfx_gain_db
        category_order = [category]
        if category != "kill_coin":
            category_order.append("kill_coin")
        for candidate_category in category_order:
            track = self._first_sfx_library_track(candidate_category)
            if track is not None:
                gain_db = self.settings.editing.sfx_gain_db
                if track.gain_db is not None:
                    gain_db = min(6.0, max(-60.0, track.gain_db))
                return track.path, gain_db
        if default_sfx_path is not None and default_sfx_path.is_file():
            return default_sfx_path, self.settings.editing.sfx_gain_db
        return None

    def _first_sfx_library_track(self, category: str) -> SfxLibraryTrack | None:
        for track in self._sfx_library():
            if track.category == category:
                return track
        return None

    def _sfx_library(self) -> list[SfxLibraryTrack]:
        if self._sfx_library_tracks is None:
            report = load_sfx_library_report(self.settings.editing.sfx_library_path)
            self._log_sfx_library_report(report)
            self._sfx_library_tracks = list(report.tracks)
        return self._sfx_library_tracks

    def _log_sfx_library_report(self, report: SfxLibraryLoadReport) -> None:
        if report.manifest_path is None:
            return
        if report.missing_manifest:
            return
        if report.parse_error is not None:
            log(
                "editing",
                "sfx library unavailable "
                f"path={report.manifest_path} reason=parse_error error={report.parse_error}",
            )
            return
        if report.invalid_schema:
            log(
                "editing",
                f"sfx library unavailable path={report.manifest_path} reason=invalid_schema",
            )
            return
        log(
            "editing",
            "sfx library loaded "
            f"path={report.manifest_path} tracks={len(report.tracks)} "
            f"total_items={report.total_items} "
            f"skipped_non_object={report.skipped_non_object_count} "
            f"skipped_missing_category={report.skipped_missing_category_count} "
            f"skipped_missing_path={report.skipped_missing_path_count} "
            f"skipped_missing_file={report.skipped_missing_file_count}",
        )

    def _kda_kill_events(self, subtitle: SubtitleAsset | None) -> list[_KdaKillEvent]:
        cues = self._subtitle_cues(subtitle)
        events: list[_KdaKillEvent] = []
        for cue in cues:
            if not cue.text.startswith("kda_change "):
                continue
            event = self._kda_kill_event_from_cue(cue, cues)
            if event is not None:
                events.append(event)
        events.sort(key=lambda event: event.source_timestamp_seconds)
        return events

    def _kda_event_timestamps(self, subtitle: SubtitleAsset | None) -> list[float]:
        timestamps: list[float] = []
        for cue in self._subtitle_cues(subtitle):
            if cue.text.startswith("kda_change "):
                timestamps.append(self._kda_event_timestamp(cue))
        timestamps.sort()
        return timestamps

    def _kda_kill_event_from_cue(
        self,
        cue: SrtCue,
        cues: list[SrtCue],
    ) -> _KdaKillEvent | None:
        kills_match = _KDA_KILLS_RE.search(cue.text)
        if kills_match is None:
            return None
        previous_kills = int(kills_match.group(1))
        current_kills = int(kills_match.group(2))
        kill_delta = current_kills - previous_kills
        if kill_delta <= 0:
            return None
        timestamp = self._kda_event_timestamp(cue)
        return _KdaKillEvent(
            source_timestamp_seconds=timestamp,
            kill_delta=kill_delta,
            is_multi_kill=kill_delta >= 2
            or self._has_multikill_keyword_near(cues, timestamp),
        )

    @staticmethod
    def _kda_event_timestamp(cue: SrtCue) -> float:
        match = _KDA_CURRENT_AT_RE.search(cue.text)
        if match is not None:
            return float(match.group(1))
        if cue.ended_at_seconds > cue.started_at_seconds:
            return (cue.started_at_seconds + cue.ended_at_seconds) / 2.0
        return cue.started_at_seconds

    def _has_multikill_keyword_near(
        self,
        cues: list[SrtCue],
        timestamp: float,
    ) -> bool:
        window = self.settings.editing.sfx_multikill_window_seconds
        for cue in cues:
            if cue.started_at_seconds > timestamp + window:
                continue
            if cue.ended_at_seconds < timestamp - window:
                continue
            normalized = cue.text.lower()
            if any(keyword in normalized for keyword in _MULTIKILL_KEYWORDS):
                return True
        return False

    @classmethod
    def _map_kda_events_to_timeline(
        cls,
        timeline: list[TimelineSegment],
        events: list[_KdaKillEvent],
    ) -> list[tuple[int, _KdaKillEvent, float]]:
        mapped: list[tuple[int, _KdaKillEvent, float]] = []
        output_cursor = 0.0
        unmapped = list(events)
        for segment_index, segment in enumerate(timeline):
            duration = cls._segment_duration(segment)
            if segment.role == "transition":
                output_cursor += duration
                continue
            remaining: list[_KdaKillEvent] = []
            for event in unmapped:
                if cls._source_timestamp_in_segment(
                    event.source_timestamp_seconds,
                    segment,
                ):
                    at_seconds = output_cursor + (
                        event.source_timestamp_seconds - segment.source_start_seconds
                    )
                    mapped.append(
                        (segment_index, event, round(max(0.0, at_seconds), 3))
                    )
                else:
                    remaining.append(event)
            unmapped = remaining
            output_cursor += duration
        return mapped

    @classmethod
    def _timeline_segments_containing_kda_timestamps(
        cls,
        timeline: list[TimelineSegment],
        timestamps: list[float],
    ) -> set[int]:
        segments: set[int] = set()
        for segment_index, segment in enumerate(timeline):
            if not cls._segment_is_sfx_eligible(segment):
                continue
            if any(
                cls._source_timestamp_in_segment(
                    timestamp,
                    segment,
                )
                for timestamp in timestamps
            ):
                segments.add(segment_index)
        return segments

    @staticmethod
    def _source_timestamp_in_segment(
        source_timestamp_seconds: float,
        segment: TimelineSegment,
    ) -> bool:
        if segment.role not in {"teaser", "main"}:
            return False
        return (
            segment.source_start_seconds - _SEGMENT_TOLERANCE_SECONDS
            <= source_timestamp_seconds
            <= segment.source_end_seconds + _SEGMENT_TOLERANCE_SECONDS
        )

    @staticmethod
    def _clamped_sfx_time(
        segment_output_start_seconds: float,
        segment_duration_seconds: float,
        at_seconds: float,
    ) -> float:
        return round(
            max(
                segment_output_start_seconds,
                min(at_seconds, segment_output_start_seconds + segment_duration_seconds),
            ),
            3,
        )

    @classmethod
    def _timeline_output_starts(cls, timeline: list[TimelineSegment]) -> list[float]:
        starts: list[float] = []
        output_cursor = 0.0
        for segment in timeline:
            starts.append(output_cursor)
            output_cursor += cls._segment_duration(segment)
        return starts

    def _default_audio_assets(self, boundary: MatchBoundary) -> dict[str, Path]:
        try:
            return ensure_default_editing_audio_assets(
                self.settings.storage.temp_dir / "editing-audio"
            )
        except OSError as exc:
            log(
                "editing",
                "skip default audio assets "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"reason={exc.__class__.__name__}",
            )
            return {}

    def _default_bgm_beds(
        self,
        assets: dict[str, Path],
        *,
        timeline: list[TimelineSegment],
        highlight_plan: HighlightPlanAsset | None,
        subtitle: SubtitleAsset | None,
        rendered_duration: float,
        timeline_start_seconds: float = 0.0,
    ) -> list[AudioBed]:
        playful_path = assets.get("playful_bgm")
        climax_path = assets.get("climax_bgm")
        if rendered_duration <= 0.0:
            return []
        if playful_path is None or not playful_path.is_file():
            return []
        if (
            rendered_duration >= _BGM_SWITCH_MIN_DURATION_SECONDS
            and climax_path is not None
            and climax_path.is_file()
        ):
            phases = ("laning", "climax")
            intervals = self._bgm_phase_intervals(
                timeline,
                highlight_plan=highlight_plan,
                subtitle=subtitle,
                rendered_duration=rendered_duration,
                timeline_start_seconds=timeline_start_seconds,
                phases=phases,
            )
            return self._bgm_beds_from_paths(
                [playful_path, climax_path],
                intervals=intervals,
                timeline_start_seconds=timeline_start_seconds,
                rendered_duration=rendered_duration,
                gain_db=self.settings.editing.bgm_gain_db,
                reason_prefix="generated",
            )
        return [
            AudioBed(
                source_path=str(playful_path),
                timeline_start_seconds=timeline_start_seconds,
                timeline_end_seconds=None,
                gain_db=self.settings.editing.bgm_gain_db,
                loop=True,
                reason="background_music_playful",
            )
        ]

    def _selected_or_default_bgm_beds(
        self,
        assets: dict[str, Path],
        *,
        boundary: MatchBoundary,
        highlight_plan: HighlightPlanAsset,
        timeline: list[TimelineSegment],
        subtitle: SubtitleAsset | None,
        streamer_name: str | None,
        rendered_duration: float,
        timeline_start_seconds: float = 0.0,
    ) -> list[AudioBed]:
        requested_phases = self._requested_bgm_phases(
            rendered_duration,
            allow_three=True,
        )
        selected_tracks = self._select_bgm_library_tracks(
            boundary=boundary,
            highlight_plan=highlight_plan,
            subtitle=subtitle,
            streamer_name=streamer_name,
            rendered_duration=rendered_duration,
            requested_phases=requested_phases,
        )
        if selected_tracks:
            log(
                "editing",
                "selected bgm library tracks "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"tracks={','.join(str(track.path) for track in selected_tracks)}",
            )
            return self._bgm_beds_from_tracks(
                selected_tracks,
                requested_phases=requested_phases,
                timeline=timeline,
                highlight_plan=highlight_plan,
                subtitle=subtitle,
                rendered_duration=rendered_duration,
                timeline_start_seconds=timeline_start_seconds,
                gain_db=self.settings.editing.bgm_gain_db,
            )
        return self._default_bgm_beds(
            assets,
            timeline=timeline,
            highlight_plan=highlight_plan,
            subtitle=subtitle,
            rendered_duration=rendered_duration,
            timeline_start_seconds=timeline_start_seconds,
        )

    def _select_bgm_library_tracks(
        self,
        *,
        boundary: MatchBoundary,
        highlight_plan: HighlightPlanAsset | None,
        subtitle: SubtitleAsset | None,
        streamer_name: str | None,
        rendered_duration: float,
        requested_phases: tuple[str, ...],
    ) -> list[BgmLibraryTrack]:
        tracks = self._bgm_library()
        if not tracks or highlight_plan is None:
            return []
        highlight_reasons = [window.reason for window in highlight_plan.windows]
        transcript_text = self._subtitle_transcript_text(subtitle)
        tags = infer_bgm_context_tags(
            transcript_text=transcript_text,
            highlight_reasons=highlight_reasons,
            streamer_name=streamer_name,
        )
        context = BgmSelectionContext(
            tags=tags,
            highlight_reasons=tuple(highlight_reasons),
            rendered_duration_seconds=rendered_duration,
            selection_key=(
                f"{boundary.session_id}:{boundary.match_index}:"
                f"{','.join(tags)}:{','.join(highlight_reasons)}"
            ),
        )
        selected = select_bgm_tracks(
            tracks,
            context,
            requested_phases=requested_phases,
        )
        if not selected:
            log(
                "editing",
                "bgm library had no match "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"tags={','.join(tags) or '-'} tracks={len(tracks)}",
            )
        return selected

    def _bgm_beds_from_tracks(
        self,
        tracks: list[BgmLibraryTrack],
        *,
        requested_phases: tuple[str, ...],
        timeline: list[TimelineSegment],
        highlight_plan: HighlightPlanAsset | None,
        subtitle: SubtitleAsset | None,
        rendered_duration: float,
        timeline_start_seconds: float = 0.0,
        gain_db: float,
    ) -> list[AudioBed]:
        if not tracks:
            return []
        if rendered_duration <= 0.0:
            return []
        phases = self._phases_for_selected_bgm_tracks(
            requested_phases,
            track_count=len(tracks),
        )
        intervals = self._bgm_phase_intervals(
            timeline,
            highlight_plan=highlight_plan,
            subtitle=subtitle,
            rendered_duration=rendered_duration,
            timeline_start_seconds=timeline_start_seconds,
            phases=phases,
        )
        return self._bgm_beds_from_paths(
            [track.path for track in tracks],
            intervals=intervals,
            timeline_start_seconds=timeline_start_seconds,
            rendered_duration=rendered_duration,
            gain_db=gain_db,
            reason_prefix="library",
        )

    def _requested_bgm_phases(
        self,
        rendered_duration: float,
        *,
        allow_three: bool,
    ) -> tuple[str, ...]:
        if rendered_duration < _BGM_SWITCH_MIN_DURATION_SECONDS:
            return ("laning",)
        if allow_three and (
            rendered_duration >= self.settings.editing.bgm_multi_phase_min_seconds
        ):
            return ("laning", "momentum", "climax")
        return ("laning", "climax")

    @staticmethod
    def _phases_for_selected_bgm_tracks(
        requested_phases: tuple[str, ...],
        *,
        track_count: int,
    ) -> tuple[str, ...]:
        if track_count <= 0:
            return ()
        if track_count == 1:
            return (requested_phases[0] if requested_phases else "laning",)
        if track_count == 2 and len(requested_phases) >= 3:
            return (requested_phases[0], requested_phases[-1])
        return requested_phases[:track_count] or ("laning",)

    def _bgm_phase_intervals(
        self,
        timeline: list[TimelineSegment],
        *,
        highlight_plan: HighlightPlanAsset | None,
        subtitle: SubtitleAsset | None,
        rendered_duration: float,
        timeline_start_seconds: float,
        phases: tuple[str, ...],
    ) -> list[_BgmPhaseInterval]:
        if rendered_duration <= 0.0 or not phases:
            return []
        phase_count = len(phases)
        switch_points = self._bgm_switch_points(
            timeline,
            highlight_plan=highlight_plan,
            subtitle=subtitle,
            rendered_duration=rendered_duration,
            timeline_start_seconds=timeline_start_seconds,
            phase_count=phase_count,
        )
        boundaries = [0.0, *switch_points, rendered_duration]
        intervals: list[_BgmPhaseInterval] = []
        for index, phase in enumerate(phases):
            start = round(boundaries[index], 3)
            end = round(boundaries[index + 1], 3)
            if end - start <= _SEGMENT_TOLERANCE_SECONDS:
                continue
            intervals.append(
                _BgmPhaseInterval(
                    phase=phase,
                    start_seconds=start,
                    end_seconds=end,
                )
            )
        return intervals

    def _bgm_switch_points(
        self,
        timeline: list[TimelineSegment],
        *,
        highlight_plan: HighlightPlanAsset | None,
        subtitle: SubtitleAsset | None,
        rendered_duration: float,
        timeline_start_seconds: float,
        phase_count: int,
    ) -> list[float]:
        if phase_count <= 1:
            return []
        fallback_points = self._fallback_bgm_switch_points(
            rendered_duration,
            phase_count=phase_count,
        )
        candidates = self._bgm_switch_candidates(
            timeline,
            highlight_plan=highlight_plan,
            subtitle=subtitle,
            rendered_duration=rendered_duration,
            timeline_start_seconds=timeline_start_seconds,
        )
        selected: list[float] = []
        used: set[int] = set()
        for index, fallback in enumerate(fallback_points):
            lower, upper = self._bgm_switch_bounds(
                rendered_duration,
                phase_count=phase_count,
                switch_index=index,
            )
            if selected:
                lower = max(
                    lower,
                    selected[-1] + self.settings.editing.bgm_switch_min_gap_seconds,
                )
            best: tuple[int, float, int, float] | None = None
            for candidate_index, candidate in enumerate(candidates):
                if candidate_index in used:
                    continue
                if not lower <= candidate.relative_seconds <= upper:
                    continue
                distance = abs(candidate.relative_seconds - fallback)
                max_distance = max(
                    self.settings.editing.bgm_switch_min_gap_seconds,
                    rendered_duration * 0.25,
                )
                if distance > max_distance:
                    continue
                key = (
                    -candidate.weight,
                    distance,
                    candidate_index,
                    candidate.relative_seconds,
                )
                if best is None or key < best:
                    best = key
            if best is None:
                selected_point = max(lower, min(fallback, upper))
            else:
                _weight, _distance, candidate_index, selected_point = best
                used.add(candidate_index)
            selected.append(round(selected_point, 3))
        return selected

    def _fallback_bgm_switch_points(
        self,
        rendered_duration: float,
        *,
        phase_count: int,
    ) -> list[float]:
        ratios = (0.55,) if phase_count == 2 else (0.40, 0.75)
        return [
            self._clamp_bgm_switch_point(
                rendered_duration * ratio,
                rendered_duration=rendered_duration,
                phase_count=phase_count,
                switch_index=index,
            )
            for index, ratio in enumerate(ratios[: phase_count - 1])
        ]

    def _clamp_bgm_switch_point(
        self,
        value: float,
        *,
        rendered_duration: float,
        phase_count: int,
        switch_index: int,
    ) -> float:
        lower, upper = self._bgm_switch_bounds(
            rendered_duration,
            phase_count=phase_count,
            switch_index=switch_index,
        )
        return round(max(lower, min(value, upper)), 3)

    def _bgm_switch_bounds(
        self,
        rendered_duration: float,
        *,
        phase_count: int,
        switch_index: int,
    ) -> tuple[float, float]:
        min_gap = self.settings.editing.bgm_switch_min_gap_seconds
        lower = min_gap * (switch_index + 1)
        upper = rendered_duration - min_gap * (phase_count - switch_index - 1)
        if lower <= upper:
            return lower, upper
        fallback_gap = rendered_duration / max(phase_count, 1)
        return (
            fallback_gap * (switch_index + 0.5),
            fallback_gap * (switch_index + 1.5),
        )

    def _bgm_switch_candidates(
        self,
        timeline: list[TimelineSegment],
        *,
        highlight_plan: HighlightPlanAsset | None,
        subtitle: SubtitleAsset | None,
        rendered_duration: float,
        timeline_start_seconds: float,
    ) -> list[_BgmSwitchCandidate]:
        candidates: list[_BgmSwitchCandidate] = []
        for timestamp in self._kda_event_timestamps(subtitle):
            rendered_second = self._source_timestamp_to_rendered_second(
                timeline,
                timestamp,
                main_only=True,
            )
            if rendered_second is None:
                continue
            relative = rendered_second - timeline_start_seconds
            if 0.0 < relative < rendered_duration:
                candidates.append(_BgmSwitchCandidate(round(relative, 3), 5))
        if highlight_plan is not None:
            reason_weights = {
                "highlight_keyword": 4,
                "condensed_key_event": 4,
                "condensed_tactical": 3,
                "condensed_context": 1,
                "condensed_match_context": 1,
            }
            for window in highlight_plan.windows:
                weight = reason_weights.get(window.reason, 1)
                timestamp = (window.started_at_seconds + window.ended_at_seconds) / 2.0
                rendered_second = self._source_timestamp_to_rendered_second(
                    timeline,
                    timestamp,
                    main_only=True,
                )
                if rendered_second is None:
                    continue
                relative = rendered_second - timeline_start_seconds
                if 0.0 < relative < rendered_duration:
                    candidates.append(_BgmSwitchCandidate(round(relative, 3), weight))
        candidates.sort(key=lambda item: (-item.weight, item.relative_seconds))
        return candidates

    @classmethod
    def _source_timestamp_to_rendered_second(
        cls,
        timeline: list[TimelineSegment],
        source_timestamp_seconds: float,
        *,
        main_only: bool,
    ) -> float | None:
        output_cursor = 0.0
        for segment in timeline:
            duration = cls._segment_duration(segment)
            can_map_role = segment.role == "main" or (
                not main_only and segment.role == "teaser"
            )
            if can_map_role and cls._source_timestamp_in_segment(
                source_timestamp_seconds,
                segment,
            ):
                return round(
                    output_cursor
                    + (source_timestamp_seconds - segment.source_start_seconds),
                    3,
                )
            output_cursor += duration
        return None

    def _bgm_beds_from_paths(
        self,
        paths: list[Path],
        *,
        intervals: list[_BgmPhaseInterval],
        timeline_start_seconds: float,
        rendered_duration: float,
        gain_db: float,
        reason_prefix: str,
    ) -> list[AudioBed]:
        beds: list[AudioBed] = []
        if not paths or not intervals:
            return beds
        half_crossfade = self.settings.editing.bgm_crossfade_seconds / 2.0
        for index, (path, interval) in enumerate(zip(paths, intervals, strict=False)):
            start = interval.start_seconds
            end = interval.end_seconds
            if len(intervals) > 1 and index > 0:
                start = max(0.0, start - half_crossfade)
            if len(intervals) > 1 and index < len(intervals) - 1:
                end = min(rendered_duration, end + half_crossfade)
            timeline_end: float | None = round(timeline_start_seconds + end, 3)
            if index == len(intervals) - 1:
                timeline_end = None
            beds.append(
                AudioBed(
                    source_path=str(path),
                    timeline_start_seconds=round(timeline_start_seconds + start, 3),
                    timeline_end_seconds=timeline_end,
                    gain_db=gain_db,
                    loop=True,
                    reason=self._bgm_reason(reason_prefix, interval.phase, index=index),
                )
            )
        return beds

    @staticmethod
    def _bgm_reason(reason_prefix: str, phase: str, *, index: int) -> str:
        if reason_prefix == "generated":
            return (
                "background_music_climax"
                if phase == "climax"
                else "background_music_playful"
            )
        if phase == "climax":
            return "background_music_library_climax"
        if phase == "momentum":
            return "background_music_library_momentum"
        return (
            "background_music_library"
            if index == 0
            else f"background_music_library_{phase}"
        )

    def _bgm_library(self) -> list[BgmLibraryTrack]:
        if self._bgm_library_tracks is None:
            report = load_bgm_library_report(self.settings.editing.bgm_library_path)
            self._log_bgm_library_report(report)
            self._bgm_library_tracks = list(report.tracks)
        return self._bgm_library_tracks

    def _log_bgm_library_report(self, report: BgmLibraryLoadReport) -> None:
        if report.manifest_path is None:
            return
        if report.missing_manifest:
            log(
                "editing",
                f"bgm library unavailable path={report.manifest_path} reason=missing_file",
            )
            return
        if report.parse_error is not None:
            log(
                "editing",
                "bgm library unavailable "
                f"path={report.manifest_path} reason=parse_error error={report.parse_error}",
            )
            return
        if report.invalid_schema:
            log(
                "editing",
                f"bgm library unavailable path={report.manifest_path} reason=invalid_schema",
            )
            return
        log(
            "editing",
            "bgm library loaded "
            f"path={report.manifest_path} tracks={len(report.tracks)} "
            f"total_items={report.total_items} "
            f"skipped_non_object={report.skipped_non_object_count} "
            f"skipped_missing_path={report.skipped_missing_path_count} "
            f"skipped_missing_file={report.skipped_missing_file_count}",
        )

    @staticmethod
    def _subtitle_transcript_text(subtitle: SubtitleAsset | None) -> str:
        if subtitle is None:
            return ""
        path = Path(subtitle.path)
        if not path.is_file():
            return ""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return ""
        text_lines = [
            line.strip()
            for line in lines
            if line.strip()
            and "-->" not in line
            and not line.strip().isdigit()
        ]
        return " ".join(text_lines)

    @staticmethod
    def _timeline_duration(timeline: list[TimelineSegment]) -> float:
        return sum(EditingPlannerService._segment_duration(segment) for segment in timeline)

    @staticmethod
    def _segment_duration(segment: TimelineSegment) -> float:
        if segment.role == "transition":
            return max(0.0, segment.duration_seconds or 0.0)
        return max(0.0, segment.source_end_seconds - segment.source_start_seconds)

    @staticmethod
    def _leading_non_main_duration(timeline: list[TimelineSegment]) -> float:
        duration = 0.0
        for segment in timeline:
            if segment.role == "main":
                break
            duration += EditingPlannerService._segment_duration(segment)
        return round(duration, 3)

    def _select_teaser_windows(
        self,
        windows: list[HighlightClipWindow],
        duration: float,
        *,
        planned_export_duration: float | None = None,
        subtitle: SubtitleAsset | None = None,
        semantic_asset: CopywriterSemanticAsset | None = None,
    ) -> list[HighlightClipWindow]:
        max_total_seconds = self._teaser_budget_seconds(
            planned_export_duration if planned_export_duration is not None else duration
        )
        semantic_windows = self._semantic_teaser_windows(
            windows,
            duration,
            max_total_seconds=max_total_seconds,
            semantic_asset=semantic_asset,
        )
        if semantic_windows:
            return semantic_windows
        subtitle_cues = self._subtitle_cues(subtitle)
        candidates = self._teaser_candidates(
            windows,
            duration,
            reasons=set(self.settings.editing.teaser_candidate_reasons),
        )
        scored_candidates = [
            (window, self._teaser_signal_score(window, subtitle_cues))
            for window in candidates
        ]
        high_confidence = [
            (window, score)
            for window, score in scored_candidates
            if score > 0 or window.reason == "highlight_keyword"
        ]
        fallback_candidates = high_confidence
        if not fallback_candidates and self.settings.editing.teaser_fallback_enabled:
            fallback_candidates = scored_candidates
            if fallback_candidates:
                log("editing", "teaser fallback reason=teaser_fallback_top_scored")
        candidates = [
            (
                HighlightClipWindow(
                    started_at_seconds=window.started_at_seconds,
                    ended_at_seconds=window.ended_at_seconds,
                    reason=(
                        "teaser_fallback_top_scored"
                        if not high_confidence
                        and self.settings.editing.teaser_fallback_enabled
                        else window.reason
                    ),
                ),
                score,
            )
            for window, score in fallback_candidates
        ]
        candidates.sort(
            key=lambda item: (
                -item[1],
                _REASON_PRIORITY.get(item[0].reason, 100),
                item[0].started_at_seconds,
                item[0].ended_at_seconds,
            )
        )

        selected: list[HighlightClipWindow] = []
        total_seconds = 0.0
        max_segments = self.settings.editing.teaser_max_segments
        min_seconds = self.settings.editing.teaser_min_segment_seconds
        for window, _score in candidates:
            if len(selected) >= max_segments:
                break
            remaining = max_total_seconds - total_seconds
            if remaining < min_seconds:
                break
            start = max(0.0, window.started_at_seconds)
            end = min(duration, window.ended_at_seconds, start + remaining)
            if end - start < min_seconds:
                continue
            selected.append(
                HighlightClipWindow(
                    started_at_seconds=start,
                    ended_at_seconds=end,
                    reason=window.reason,
                )
            )
            total_seconds += end - start
        return selected

    def _teaser_budget_seconds(self, planned_export_duration: float) -> float:
        configured_cap = self.settings.editing.teaser_max_total_seconds
        if not self.settings.editing.teaser_dynamic_budget_enabled:
            return configured_cap
        fraction = (
            self.settings.editing.teaser_budget_fraction_min
            + self.settings.editing.teaser_budget_fraction_max
        ) / 2.0
        dynamic_budget = planned_export_duration * fraction
        dynamic_budget = min(
            self.settings.editing.teaser_budget_max_seconds,
            max(self.settings.editing.teaser_budget_min_seconds, dynamic_budget),
        )
        return max(0.0, min(configured_cap, dynamic_budget))

    def _semantic_teaser_windows(
        self,
        windows: list[HighlightClipWindow],
        duration: float,
        *,
        max_total_seconds: float,
        semantic_asset: CopywriterSemanticAsset | None,
    ) -> list[HighlightClipWindow]:
        if semantic_asset is None:
            return []
        candidates: list[HighlightClipWindow] = []
        for recommendation in semantic_asset.result.teaser_recommendations:
            snapped = self._snap_semantic_teaser_window(
                windows,
                duration,
                recommendation.source_start_seconds,
                recommendation.source_end_seconds,
            )
            if snapped is None:
                continue
            candidates.append(snapped)
        selected: list[HighlightClipWindow] = []
        total_seconds = 0.0
        for window in candidates:
            if len(selected) >= self.settings.editing.teaser_max_segments:
                break
            remaining = max_total_seconds - total_seconds
            if remaining < self.settings.editing.teaser_min_segment_seconds:
                break
            end = min(window.ended_at_seconds, window.started_at_seconds + remaining)
            if end - window.started_at_seconds < self.settings.editing.teaser_min_segment_seconds:
                continue
            selected.append(
                HighlightClipWindow(
                    started_at_seconds=window.started_at_seconds,
                    ended_at_seconds=end,
                    reason="llm_teaser",
                )
            )
            total_seconds += end - window.started_at_seconds
        return selected

    def _snap_semantic_teaser_window(
        self,
        windows: list[HighlightClipWindow],
        duration: float,
        start_seconds: float,
        end_seconds: float,
    ) -> HighlightClipWindow | None:
        requested_start = max(0.0, start_seconds)
        requested_end = min(duration, end_seconds)
        if requested_end <= requested_start:
            return None
        best: tuple[float, HighlightClipWindow] | None = None
        for window in windows:
            overlap = min(requested_end, window.ended_at_seconds) - max(
                requested_start,
                window.started_at_seconds,
            )
            if overlap <= 0.0:
                continue
            start = max(requested_start, window.started_at_seconds)
            end = min(requested_end, window.ended_at_seconds)
            min_duration = self.settings.editing.teaser_min_segment_seconds
            if end - start < min_duration:
                center = (start + end) / 2.0
                start = max(window.started_at_seconds, center - min_duration / 2.0)
                end = min(window.ended_at_seconds, start + min_duration)
                start = max(window.started_at_seconds, end - min_duration)
            candidate = HighlightClipWindow(
                started_at_seconds=start,
                ended_at_seconds=end,
                reason="llm_teaser",
            )
            if not self._valid_teaser_window(candidate, duration):
                continue
            score = overlap / max(0.001, requested_end - requested_start)
            if best is None or score > best[0]:
                best = (score, candidate)
        return best[1] if best is not None else None

    @staticmethod
    def _subtitle_cues(subtitle: SubtitleAsset | None) -> list[SrtCue]:
        if subtitle is None:
            return []
        path = Path(subtitle.path)
        if not path.is_file():
            return []
        try:
            return parse_srt_cues(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return []

    @classmethod
    def _teaser_signal_score(
        cls,
        window: HighlightClipWindow,
        subtitle_cues: list[SrtCue],
    ) -> int:
        score = 0
        for cue in subtitle_cues:
            overlap = min(cue.ended_at_seconds, window.ended_at_seconds) - max(
                cue.started_at_seconds,
                window.started_at_seconds,
            )
            if overlap <= 0.0:
                continue
            text_score = cls._teaser_text_score(cue.text)
            if text_score <= 0:
                continue
            score += text_score
            if cue.started_at_seconds >= window.started_at_seconds:
                score += 2
            if cue.ended_at_seconds <= window.ended_at_seconds:
                score += 2
        return score

    @staticmethod
    def _teaser_text_score(text: str) -> int:
        normalized = text.lower()
        score = 0
        for keywords, weight in _TEASER_SIGNAL_KEYWORDS:
            if any(keyword in normalized for keyword in keywords):
                score += weight
        return score

    def _teaser_candidates(
        self,
        windows: list[HighlightClipWindow],
        duration: float,
        *,
        reasons: set[str],
    ) -> list[HighlightClipWindow]:
        return [
            window
            for window in windows
            if window.reason in reasons
            and self._valid_teaser_window(window, duration)
        ]

    def _build_main_segments(
        self,
        windows: list[HighlightClipWindow],
        duration: float,
    ) -> list[TimelineSegment]:
        main_windows = self._normalized_main_windows(windows, duration)
        if not main_windows:
            return []
        if self._windows_nearly_cover_full_duration(main_windows, duration):
            return []
        if not self._main_windows_cover_edges(main_windows, duration):
            return []

        return [
            TimelineSegment(
                role="main",
                source_start_seconds=round(window.started_at_seconds, 3),
                source_end_seconds=round(window.ended_at_seconds, 3),
                reason=window.reason,
            )
            for window in main_windows
        ]

    @staticmethod
    def _normalized_main_windows(
        windows: list[HighlightClipWindow],
        duration: float,
    ) -> list[HighlightClipWindow]:
        normalized = sorted(
            (
                HighlightClipWindow(
                    started_at_seconds=max(0.0, window.started_at_seconds),
                    ended_at_seconds=min(duration, window.ended_at_seconds),
                    reason=window.reason,
                )
                for window in windows
                if window.started_at_seconds >= 0.0
                and window.ended_at_seconds > window.started_at_seconds
                and window.ended_at_seconds <= duration + 1.0
            ),
            key=lambda window: (window.started_at_seconds, window.ended_at_seconds),
        )

        merged: list[HighlightClipWindow] = []
        for window in normalized:
            if window.ended_at_seconds - window.started_at_seconds <= _SEGMENT_TOLERANCE_SECONDS:
                continue
            if (
                merged
                and window.started_at_seconds
                <= merged[-1].ended_at_seconds + _SEGMENT_TOLERANCE_SECONDS
            ):
                previous = merged[-1]
                merged[-1] = HighlightClipWindow(
                    started_at_seconds=previous.started_at_seconds,
                    ended_at_seconds=max(previous.ended_at_seconds, window.ended_at_seconds),
                    reason=EditingPlannerService._merge_main_reason(
                        previous.reason,
                        window.reason,
                    ),
                )
                continue
            merged.append(window)
        return merged

    @staticmethod
    def _merge_main_reason(first: str, second: str) -> str:
        if first == second:
            return first
        for reason in ("condensed_key_event", "highlight_keyword", "condensed_tactical"):
            if reason in {first, second}:
                return reason
        return first

    @staticmethod
    def _windows_nearly_cover_full_duration(
        windows: list[HighlightClipWindow],
        duration: float,
    ) -> bool:
        if len(windows) != 1:
            return False
        window = windows[0]
        return (
            window.started_at_seconds <= _SEGMENT_TOLERANCE_SECONDS
            and window.ended_at_seconds >= duration - _SEGMENT_TOLERANCE_SECONDS
        )

    @staticmethod
    def _main_windows_cover_edges(
        windows: list[HighlightClipWindow],
        duration: float,
    ) -> bool:
        starts_at_beginning = any(
            window.started_at_seconds <= _SEGMENT_TOLERANCE_SECONDS
            for window in windows
        )
        ends_at_boundary = any(
            window.ended_at_seconds >= duration - _SEGMENT_TOLERANCE_SECONDS
            for window in windows
        )
        return starts_at_beginning and ends_at_boundary

    def _valid_teaser_window(self, window: HighlightClipWindow, duration: float) -> bool:
        if window.started_at_seconds < 0.0:
            return False
        if window.ended_at_seconds <= window.started_at_seconds:
            return False
        if window.ended_at_seconds > duration + 1.0:
            return False
        return (
            min(window.ended_at_seconds, duration) - window.started_at_seconds
            >= self.settings.editing.teaser_min_segment_seconds
        )

    @staticmethod
    def _is_incomplete_boundary(boundary: MatchBoundary) -> bool:
        return (not boundary.is_complete) or boundary.confidence < 0.8

    def _highlight_plan_matches_boundary(
        self,
        plan: HighlightPlanAsset,
        boundary: MatchBoundary,
    ) -> bool:
        return self._boundary_metadata_matches(
            plan.source_boundary_start_seconds,
            plan.source_boundary_end_seconds,
            boundary,
        )

    def _edit_plan_matches_boundary(
        self,
        plan: EditPlanAsset | None,
        boundary: MatchBoundary,
        recording: RecordingAsset | None,
        highlight_plan: HighlightPlanAsset | None,
        subtitle: SubtitleAsset | None,
        semantic_asset: CopywriterSemanticAsset | None,
        streamer_name: str | None,
    ) -> bool:
        if plan is None:
            return False
        duration = boundary.ended_at_seconds - boundary.started_at_seconds
        matches_shape = self._boundary_metadata_matches(
            plan.source_boundary_start_seconds,
            plan.source_boundary_end_seconds,
            boundary,
        ) and self._edit_plan_has_current_main_shape(
            plan,
            duration,
        ) and self._edit_plan_has_current_teaser_shape(
            plan,
            highlight_plan,
            duration,
            subtitle,
            semantic_asset,
        ) and self._edit_plan_has_current_transition_shape(
            plan,
            semantic_asset,
        ) and self._edit_plan_has_current_zoom_shape(plan)
        if not matches_shape:
            return False
        return self._edit_plan_has_current_audio_shape(
            plan,
            boundary,
            recording,
            highlight_plan,
            subtitle,
            streamer_name,
        )

    def _edit_plan_has_current_audio_shape(
        self,
        plan: EditPlanAsset,
        boundary: MatchBoundary,
        recording: RecordingAsset | None,
        highlight_plan: HighlightPlanAsset | None,
        subtitle: SubtitleAsset | None,
        streamer_name: str | None,
    ) -> bool:
        if not self.settings.editing.audio_mixing_enabled:
            return not plan.audio_beds and not plan.sound_effects
        if highlight_plan is None:
            return True
        expected_audio_beds, expected_sound_effects = self._build_audio_instructions(
            boundary,
            highlight_plan,
            plan.timeline,
            recording,
            subtitle,
            streamer_name,
        )
        return self._audio_beds_match(
            plan.audio_beds,
            expected_audio_beds,
        ) and self._sound_effects_match(
            plan.sound_effects,
            expected_sound_effects,
        )

    @classmethod
    def _audio_beds_match(
        cls,
        actual: list[AudioBed],
        expected: list[AudioBed],
    ) -> bool:
        if len(actual) != len(expected):
            return False
        return all(
            cls._audio_bed_matches(actual_bed, expected_bed)
            for actual_bed, expected_bed in zip(actual, expected, strict=True)
        )

    @classmethod
    def _audio_bed_matches(cls, actual: AudioBed, expected: AudioBed) -> bool:
        return (
            actual.source_path == expected.source_path
            and cls._float_matches(
                actual.timeline_start_seconds,
                expected.timeline_start_seconds,
            )
            and cls._optional_float_matches(
                actual.timeline_end_seconds,
                expected.timeline_end_seconds,
            )
            and cls._float_matches(actual.gain_db, expected.gain_db)
            and actual.loop == expected.loop
            and actual.reason == expected.reason
        )

    @classmethod
    def _sound_effects_match(
        cls,
        actual: list[SoundEffectHit],
        expected: list[SoundEffectHit],
    ) -> bool:
        if len(actual) != len(expected):
            return False
        return all(
            cls._sound_effect_matches(actual_hit, expected_hit)
            for actual_hit, expected_hit in zip(actual, expected, strict=True)
        )

    @classmethod
    def _sound_effect_matches(
        cls,
        actual: SoundEffectHit,
        expected: SoundEffectHit,
    ) -> bool:
        return (
            actual.source_path == expected.source_path
            and cls._float_matches(actual.at_seconds, expected.at_seconds)
            and cls._float_matches(actual.gain_db, expected.gain_db)
            and actual.reason == expected.reason
        )

    @staticmethod
    def _optional_float_matches(actual: float | None, expected: float | None) -> bool:
        if actual is None or expected is None:
            return actual is None and expected is None
        return EditingPlannerService._float_matches(actual, expected)

    @staticmethod
    def _float_matches(actual: float, expected: float) -> bool:
        return abs(actual - expected) <= _SEGMENT_TOLERANCE_SECONDS

    def _edit_plan_has_current_teaser_shape(
        self,
        plan: EditPlanAsset,
        highlight_plan: HighlightPlanAsset | None,
        duration: float,
        subtitle: SubtitleAsset | None,
        semantic_asset: CopywriterSemanticAsset | None,
    ) -> bool:
        expected_windows = (
            self._select_teaser_windows(
                highlight_plan.windows,
                duration,
                planned_export_duration=self._timeline_duration(
                    self._build_main_segments(highlight_plan.windows, duration)
                ),
                subtitle=subtitle,
                semantic_asset=semantic_asset,
            )
            if highlight_plan is not None
            else []
        )
        teaser_segments = [
            segment for segment in plan.timeline if segment.role == "teaser"
        ]
        teaser_windows = self._merged_role_windows(teaser_segments)
        if len(teaser_windows) != len(expected_windows):
            return False
        return all(
            self._timeline_segment_matches_window(segment, window)
            for segment, window in zip(teaser_windows, expected_windows, strict=True)
        )

    def _edit_plan_has_current_transition_shape(
        self,
        plan: EditPlanAsset,
        semantic_asset: CopywriterSemanticAsset | None,
    ) -> bool:
        transition_segments = [
            segment for segment in plan.timeline if segment.role == "transition"
        ]
        teaser_count = sum(1 for segment in plan.timeline if segment.role == "teaser")
        expected = self._build_transition_segment(
            semantic_asset=semantic_asset,
            has_teaser=teaser_count > 0,
        )
        if expected is None:
            return not transition_segments
        if len(transition_segments) != 1:
            return False
        transition_index = plan.timeline.index(transition_segments[0])
        if transition_index != teaser_count:
            return False
        actual = transition_segments[0]
        return (
            actual.source_path is None
            and actual.transform is None
            and actual.reason == expected.reason
            and actual.text == expected.text
            and self._float_matches(
                actual.duration_seconds or 0.0,
                expected.duration_seconds or 0.0,
            )
            and self._float_matches(actual.source_start_seconds, 0.0)
            and self._float_matches(actual.source_end_seconds, 0.0)
        )

    @classmethod
    def _timeline_segment_matches_window(
        cls,
        segment: TimelineSegment,
        window: HighlightClipWindow,
    ) -> bool:
        return (
            cls._float_matches(
                segment.source_start_seconds,
                round(window.started_at_seconds, 3),
            )
            and cls._float_matches(
                segment.source_end_seconds,
                round(window.ended_at_seconds, 3),
            )
            and segment.reason == window.reason
        )

    @staticmethod
    def _merged_role_windows(segments: list[TimelineSegment]) -> list[TimelineSegment]:
        merged: list[TimelineSegment] = []
        for segment in segments:
            if (
                merged
                and merged[-1].reason == segment.reason
                and abs(merged[-1].source_end_seconds - segment.source_start_seconds)
                <= _SEGMENT_TOLERANCE_SECONDS
            ):
                previous = merged[-1]
                merged[-1] = previous.model_copy(
                    update={"source_end_seconds": segment.source_end_seconds}
                )
                continue
            merged.append(segment)
        return merged

    @staticmethod
    def _edit_plan_has_current_main_shape(
        plan: EditPlanAsset,
        duration: float,
    ) -> bool:
        main_segments = [
            segment for segment in plan.timeline if segment.role == "main"
        ]
        if not main_segments:
            return False
        if any(segment.reason == "full_validated_match" for segment in main_segments):
            return False
        if len(main_segments) == 1:
            segment = main_segments[0]
            if (
                abs(segment.source_start_seconds) <= _SEGMENT_TOLERANCE_SECONDS
                and abs(segment.source_end_seconds - duration)
                <= _SEGMENT_TOLERANCE_SECONDS
            ):
                return False
        starts_at_beginning = any(
            abs(segment.source_start_seconds) <= _SEGMENT_TOLERANCE_SECONDS
            for segment in main_segments
        )
        ends_at_boundary = any(
            abs(segment.source_end_seconds - duration) <= _SEGMENT_TOLERANCE_SECONDS
            for segment in main_segments
        )
        return starts_at_beginning and ends_at_boundary

    def _edit_plan_has_current_zoom_shape(self, plan: EditPlanAsset) -> bool:
        if self.settings.editing.zoom_mode == "closeup":
            return self._edit_plan_has_current_closeup_zoom_shape(plan)
        return self._edit_plan_has_current_legacy_zoom_shape(plan)

    def _edit_plan_has_current_closeup_zoom_shape(self, plan: EditPlanAsset) -> bool:
        if not self.settings.editing.zoom_enabled:
            return all(segment.transform is None for segment in plan.timeline)
        if self.settings.editing.zoom_max_segments <= 0:
            return all(segment.transform is None for segment in plan.timeline)
        transformed = [
            segment for segment in plan.timeline if segment.transform is not None
        ]
        if len(transformed) > self.settings.editing.zoom_max_segments:
            return False
        has_zoomable = any(
            self._segment_can_receive_closeup(segment) for segment in plan.timeline
        )
        if has_zoomable and not transformed:
            return False
        for segment in transformed:
            transform = segment.transform
            if transform is None or transform.kind != "punch_in":
                return False
            if not self._segment_can_receive_closeup(segment):
                return False
            if (
                self._segment_duration(segment)
                > self.settings.editing.zoom_closeup_seconds
                + _SEGMENT_TOLERANCE_SECONDS
            ):
                return False
            if abs(transform.scale - self.settings.editing.zoom_scale) > 0.001:
                return False
            if abs(transform.ease_in_seconds - self.settings.editing.zoom_ease_seconds) > 0.001:
                return False
            if abs(transform.ease_out_seconds - self.settings.editing.zoom_ease_seconds) > 0.001:
                return False
        return True

    def _edit_plan_has_current_legacy_zoom_shape(self, plan: EditPlanAsset) -> bool:
        expected_indices = self._expected_zoom_segment_indices(plan.timeline)
        for index, segment in enumerate(plan.timeline):
            transform = segment.transform
            if index not in expected_indices:
                if transform is not None:
                    return False
                continue
            if transform is None:
                return False
            if transform.kind != "punch_in":
                return False
            if (
                segment.role == "main"
                and self._segment_duration(segment)
                > self.settings.editing.zoom_max_duration_seconds
                + _SEGMENT_TOLERANCE_SECONDS
            ):
                return False
            x_anchor, y_anchor, target = self._zoom_focus()
            if abs(transform.scale - self.settings.editing.zoom_scale) > 0.001:
                return False
            if abs(transform.x_anchor - x_anchor) > 0.001:
                return False
            if abs(transform.y_anchor - y_anchor) > 0.001:
                return False
            if transform.target != target:
                return False
        return True

    def _expected_zoom_segment_indices(self, timeline: list[TimelineSegment]) -> set[int]:
        if not self.settings.editing.zoom_enabled:
            return set()
        remaining = self.settings.editing.zoom_max_segments
        if remaining <= 0:
            return set()
        selected: set[int] = set()
        index = 0
        while index < len(timeline):
            segment = timeline[index]
            if segment.role not in {"teaser", "main"} or segment.reason not in _ZOOM_REASONS:
                index += 1
                continue
            if (
                segment.role == "main"
                and self._segment_duration(segment)
                > self.settings.editing.zoom_max_duration_seconds
                + _SEGMENT_TOLERANCE_SECONDS
            ):
                index += 1
                continue
            selected.add(index)
            remaining -= 1
            if remaining <= 0:
                break
            index += 1
        return selected

    @staticmethod
    def _boundary_metadata_matches(
        source_start_seconds: float,
        source_end_seconds: float,
        boundary: MatchBoundary,
    ) -> bool:
        tolerance_seconds = 1.0
        return (
            abs(source_start_seconds - boundary.started_at_seconds) <= tolerance_seconds
            and abs(source_end_seconds - boundary.ended_at_seconds) <= tolerance_seconds
        )

    def _compact_state(
        self,
        state: EditPlannerStateFile,
        existing_plan_keys: set[str],
    ) -> None:
        before = len(state.processed_match_keys)
        state.processed_match_keys = [
            key for key in state.processed_match_keys if key in existing_plan_keys
        ]
        after = len(state.processed_match_keys)
        if before != after:
            log("editing", f"compacted planner state processed_keys={before}->{after}")

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

    @staticmethod
    def _latest_recording_by_session(
        assets: list[RecordingAsset],
    ) -> dict[str, RecordingAsset]:
        latest: dict[str, RecordingAsset] = {}
        for asset in assets:
            latest[asset.session_id] = asset
        return latest

    def _streamer_names_by_session(self) -> dict[str, str]:
        names: dict[str, str] = {}
        state_paths = [self.settings.orchestrator.state_file]
        selected_root = self.settings.storage.temp_dir / "selected-recordings"
        if selected_root.is_dir():
            state_paths.extend(selected_root.glob("*/orchestrator-state.json"))
        for state_path in state_paths:
            if not state_path.is_file():
                continue
            try:
                state = OrchestratorStateFile.model_validate_json(
                    state_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            for session in state.sessions:
                if session.streamer_name:
                    names[session.session_id] = session.streamer_name
        return names

    def _load_state(self) -> EditPlannerStateFile:
        if not self.state_path.exists():
            return EditPlannerStateFile()
        return EditPlannerStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )

    def _save_state(self, state: EditPlannerStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _key(session_id: str, match_index: int) -> str:
        return f"{session_id}:{match_index}"
