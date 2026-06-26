from __future__ import annotations

from datetime import datetime, timezone

from arl.config import Settings
from arl.editing.models import EditPlannerStateFile
from arl.shared.contracts import (
    AudioBed,
    EditPlanAsset,
    HighlightClipWindow,
    HighlightPlanAsset,
    MatchBoundary,
    SoundEffectHit,
    TimelineSegment,
    TimelineVideoTransform,
)
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log


_REASON_PRIORITY = {
    "condensed_key_event": 0,
    "highlight_keyword": 1,
    "condensed_tactical": 2,
    "condensed_context": 3,
}

_HIGH_SIGNAL_REASONS = {"highlight_keyword", "condensed_key_event", "condensed_tactical"}


class EditingPlannerService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.boundaries_path = settings.storage.temp_dir / "match-boundaries.jsonl"
        self.highlight_plans_path = settings.storage.temp_dir / "highlight-plans.jsonl"
        self.edit_plans_path = settings.storage.temp_dir / "edit-plans.jsonl"
        self.state_path = settings.storage.temp_dir / "editing-state.json"

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
        skipped_no_teaser = 0

        for boundary in boundaries:
            key = self._key(boundary.session_id, boundary.match_index)
            existing_edit_plan = existing_edit_plan_map.get(
                (boundary.session_id, boundary.match_index)
            )
            existing_plan_matches = self._edit_plan_matches_boundary(
                existing_edit_plan,
                boundary,
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

            plan = self._build_edit_plan(boundary, highlight_plan)
            if plan is None:
                skipped_no_teaser += 1
                continue

            append_model(self.edit_plans_path, plan)
            state.processed_match_keys.append(key)
            processed_keys.add(key)
            existing_edit_plan_map[(plan.session_id, plan.match_index)] = plan
            processed += 1
            emitted += 1
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
            f"skipped_no_teaser={skipped_no_teaser}",
        )

    def _build_edit_plan(
        self,
        boundary: MatchBoundary,
        highlight_plan: HighlightPlanAsset,
    ) -> EditPlanAsset | None:
        duration = boundary.ended_at_seconds - boundary.started_at_seconds
        if duration <= 0.0:
            return None

        teaser_windows = self._select_teaser_windows(highlight_plan.windows, duration)
        if not teaser_windows:
            log(
                "editing",
                "skip edit plan "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                "reason=no_valid_teaser_windows",
            )
            return None

        timeline = [
            TimelineSegment(
                role="teaser",
                source_start_seconds=round(window.started_at_seconds, 3),
                source_end_seconds=round(window.ended_at_seconds, 3),
                reason=window.reason,
            )
            for window in teaser_windows
        ]
        timeline.append(
            TimelineSegment(
                role="main",
                source_start_seconds=0.0,
                source_end_seconds=round(duration, 3),
                reason="full_validated_match",
            )
        )
        self._apply_zoom_transforms(timeline)
        audio_beds, sound_effects = self._build_audio_instructions(
            boundary,
            timeline,
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

    def _apply_zoom_transforms(self, timeline: list[TimelineSegment]) -> None:
        if not self.settings.editing.zoom_enabled:
            return
        remaining = self.settings.editing.zoom_max_segments
        if remaining <= 0:
            return
        for segment in timeline:
            if segment.role != "teaser" or segment.reason not in _HIGH_SIGNAL_REASONS:
                continue
            segment.transform = TimelineVideoTransform(
                kind="punch_in",
                scale=self.settings.editing.zoom_scale,
                x_anchor=self.settings.editing.zoom_x_anchor,
                y_anchor=self.settings.editing.zoom_y_anchor,
            )
            remaining -= 1
            if remaining <= 0:
                return

    def _build_audio_instructions(
        self,
        boundary: MatchBoundary,
        timeline: list[TimelineSegment],
    ) -> tuple[list[AudioBed], list[SoundEffectHit]]:
        if not self.settings.editing.audio_mixing_enabled:
            return [], []

        audio_beds: list[AudioBed] = []
        sound_effects: list[SoundEffectHit] = []
        bgm_path = self.settings.editing.bgm_path
        if bgm_path is not None:
            if bgm_path.is_file():
                audio_beds.append(
                    AudioBed(
                        source_path=str(bgm_path),
                        timeline_start_seconds=0.0,
                        timeline_end_seconds=None,
                        gain_db=self.settings.editing.bgm_gain_db,
                        loop=True,
                    )
                )
            else:
                log(
                    "editing",
                    "skip configured bgm asset "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"path={bgm_path} reason=missing_file",
                )

        sfx_path = self.settings.editing.sfx_path
        if sfx_path is None:
            return audio_beds, sound_effects
        if not sfx_path.is_file():
            log(
                "editing",
                "skip configured sfx asset "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"path={sfx_path} reason=missing_file",
            )
            return audio_beds, sound_effects

        timeline_cursor = 0.0
        for segment in timeline:
            segment_duration = segment.source_end_seconds - segment.source_start_seconds
            if segment.role == "teaser" and segment.reason in _HIGH_SIGNAL_REASONS:
                sound_effects.append(
                    SoundEffectHit(
                        source_path=str(sfx_path),
                        at_seconds=round(timeline_cursor, 3),
                        gain_db=self.settings.editing.sfx_gain_db,
                        reason=segment.reason,
                    )
                )
            timeline_cursor += max(0.0, segment_duration)
        return audio_beds, sound_effects

    def _select_teaser_windows(
        self,
        windows: list[HighlightClipWindow],
        duration: float,
    ) -> list[HighlightClipWindow]:
        candidates = [
            window
            for window in windows
            if self._valid_teaser_window(window, duration)
        ]
        candidates.sort(
            key=lambda window: (
                _REASON_PRIORITY.get(window.reason, 100),
                window.started_at_seconds,
                window.ended_at_seconds,
            )
        )

        selected: list[HighlightClipWindow] = []
        total_seconds = 0.0
        max_segments = self.settings.editing.teaser_max_segments
        max_total_seconds = self.settings.editing.teaser_max_total_seconds
        min_seconds = self.settings.editing.teaser_min_segment_seconds
        for window in candidates:
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
    ) -> bool:
        if plan is None:
            return False
        return self._boundary_metadata_matches(
            plan.source_boundary_start_seconds,
            plan.source_boundary_end_seconds,
            boundary,
        )

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
