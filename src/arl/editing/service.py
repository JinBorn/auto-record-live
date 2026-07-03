from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from arl.config import Settings
from arl.editing.audio import (
    BgmLibraryLoadReport,
    BgmLibraryTrack,
    BgmSelectionContext,
    SourceMusicDetection,
    detect_source_background_music,
    detect_source_background_music_spans,
    ensure_default_editing_audio_assets,
    infer_bgm_context_tags,
    load_bgm_library_report,
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
    "condensed_key_event": 0,
    "highlight_keyword": 1,
    "condensed_tactical": 2,
    "condensed_context": 3,
}

_PRIMARY_TEASER_REASONS = {"highlight_keyword"}
_ZOOM_REASONS = {"highlight_keyword", "condensed_key_event", "condensed_tactical"}
_SFX_REASONS = {"highlight_keyword", "condensed_key_event"}
_SFX_MIN_INTERVAL_SECONDS = 20.0
_SFX_MAX_HITS = 4
_SEGMENT_TOLERANCE_SECONDS = 0.001
_BGM_SWITCH_MIN_DURATION_SECONDS = 120.0
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


class EditingPlannerService:
    def __init__(
        self,
        settings: Settings,
        *,
        source_bgm_detector: Callable[..., SourceMusicDetection] | None = None,
    ) -> None:
        self.settings = settings
        self.boundaries_path = settings.storage.temp_dir / "match-boundaries.jsonl"
        self.highlight_plans_path = settings.storage.temp_dir / "highlight-plans.jsonl"
        self.subtitle_assets_path = settings.storage.temp_dir / "subtitle-assets.jsonl"
        self.recording_assets_path = settings.storage.temp_dir / "recording-assets.jsonl"
        self.edit_plans_path = settings.storage.temp_dir / "edit-plans.jsonl"
        self.state_path = settings.storage.temp_dir / "editing-state.json"
        self.source_bgm_detector = source_bgm_detector or detect_source_background_music
        self._source_music_cache: dict[tuple[str, int], SourceMusicDetection] = {}
        self._bgm_library_tracks: list[BgmLibraryTrack] | None = None

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
            subtitle=subtitle,
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
        timeline.extend(main_segments)
        self._apply_zoom_transforms(timeline)
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

    def _apply_zoom_transforms(self, timeline: list[TimelineSegment]) -> None:
        if not self.settings.editing.zoom_enabled:
            return
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
            )
            segment.transform = transform
            index += 1
            remaining -= 1
            if remaining <= 0:
                return

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
        bgm_start_seconds = self._leading_teaser_duration(timeline)
        bgm_duration = max(0.0, rendered_duration - bgm_start_seconds)
        source_music = self._source_music_detection(boundary, recording)
        skip_bgm = source_music.has_music
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
                f"confidence={source_music.confidence:.3f} reason={source_music.reason}",
            )
        elif bgm_path is not None:
            if bgm_path.is_file():
                audio_beds.append(
                    AudioBed(
                        source_path=str(bgm_path),
                        timeline_start_seconds=bgm_start_seconds,
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
        else:
            audio_beds.extend(
                self._selected_or_default_bgm_beds(
                    default_assets,
                    boundary=boundary,
                    highlight_plan=highlight_plan,
                    subtitle=subtitle,
                    streamer_name=streamer_name,
                    rendered_duration=bgm_duration,
                    timeline_start_seconds=bgm_start_seconds,
                )
            )

        configured_sfx_path = self.settings.editing.sfx_path
        sfx_path = (
            configured_sfx_path
            if configured_sfx_path is not None
            else default_assets.get("coin_sfx")
        )
        if sfx_path is None:
            return audio_beds, sound_effects
        if not sfx_path.is_file():
            if configured_sfx_path is not None:
                log(
                    "editing",
                    "skip configured sfx asset "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"path={sfx_path} reason=missing_file",
                )
            return audio_beds, sound_effects

        timeline_cursor = 0.0
        last_sfx_at: float | None = None
        for segment in timeline:
            segment_duration = segment.source_end_seconds - segment.source_start_seconds
            if self._should_emit_sound_effect(
                segment,
                at_seconds=timeline_cursor,
                emitted_count=len(sound_effects),
                last_sfx_at=last_sfx_at,
            ):
                at_seconds = round(timeline_cursor, 3)
                sound_effects.append(
                    SoundEffectHit(
                        source_path=str(sfx_path),
                        at_seconds=at_seconds,
                        gain_db=self.settings.editing.sfx_gain_db,
                        reason=segment.reason,
                    )
                )
                last_sfx_at = at_seconds
            timeline_cursor += max(0.0, segment_duration)
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
            return self.source_bgm_detector(
                Path(span.path),
                start_seconds=span.local_start_seconds,
                end_seconds=span.local_end_seconds,
            )
        if self.source_bgm_detector is detect_source_background_music:
            return detect_source_background_music_spans(
                spans,
                start_seconds=boundary.started_at_seconds,
                end_seconds=boundary.ended_at_seconds,
            )

        detections = [
            self.source_bgm_detector(
                Path(span.path),
                start_seconds=span.local_start_seconds,
                end_seconds=span.local_end_seconds,
            )
            for span in spans
        ]
        if not detections:
            return SourceMusicDetection(False, 0.0, "missing_recording_span")
        confidence = round(
            sum(detection.confidence for detection in detections) / len(detections),
            3,
        )
        has_music_count = sum(1 for detection in detections if detection.has_music)
        required_music_count = max(1, (len(detections) * 3 + 4) // 5)
        has_music = has_music_count >= required_music_count
        return SourceMusicDetection(
            has_music,
            confidence,
            "persistent_music_like_audio" if has_music else "no_persistent_music_bed",
        )

    @staticmethod
    def _should_emit_sound_effect(
        segment: TimelineSegment,
        *,
        at_seconds: float,
        emitted_count: int,
        last_sfx_at: float | None,
    ) -> bool:
        if emitted_count >= _SFX_MAX_HITS:
            return False
        if segment.role not in {"teaser", "main"}:
            return False
        if segment.reason not in _SFX_REASONS:
            return False
        if last_sfx_at is None:
            return True
        return at_seconds - last_sfx_at >= _SFX_MIN_INTERVAL_SECONDS

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
            switch_at = round(
                max(30.0, min(rendered_duration * 0.55, rendered_duration - 30.0)),
                3,
            )
            timeline_switch_seconds = round(timeline_start_seconds + switch_at, 3)
            return [
                AudioBed(
                    source_path=str(playful_path),
                    timeline_start_seconds=timeline_start_seconds,
                    timeline_end_seconds=timeline_switch_seconds,
                    gain_db=self.settings.editing.bgm_gain_db,
                    loop=True,
                    reason="background_music_playful",
                ),
                AudioBed(
                    source_path=str(climax_path),
                    timeline_start_seconds=timeline_switch_seconds,
                    timeline_end_seconds=None,
                    gain_db=self.settings.editing.bgm_gain_db,
                    loop=True,
                    reason="background_music_climax",
                ),
            ]
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
        subtitle: SubtitleAsset | None,
        streamer_name: str | None,
        rendered_duration: float,
        timeline_start_seconds: float = 0.0,
    ) -> list[AudioBed]:
        selected_tracks = self._select_bgm_library_tracks(
            boundary=boundary,
            highlight_plan=highlight_plan,
            subtitle=subtitle,
            streamer_name=streamer_name,
            rendered_duration=rendered_duration,
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
                rendered_duration=rendered_duration,
                timeline_start_seconds=timeline_start_seconds,
                gain_db=self.settings.editing.bgm_gain_db,
            )
        return self._default_bgm_beds(
            assets,
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
        selected = select_bgm_tracks(tracks, context)
        if not selected:
            log(
                "editing",
                "bgm library had no match "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"tags={','.join(tags) or '-'} tracks={len(tracks)}",
            )
        return selected

    @staticmethod
    def _bgm_beds_from_tracks(
        tracks: list[BgmLibraryTrack],
        *,
        rendered_duration: float,
        timeline_start_seconds: float = 0.0,
        gain_db: float,
    ) -> list[AudioBed]:
        if not tracks:
            return []
        if rendered_duration <= 0.0:
            return []
        if len(tracks) == 1:
            return [
                AudioBed(
                    source_path=str(tracks[0].path),
                    timeline_start_seconds=timeline_start_seconds,
                    timeline_end_seconds=None,
                    gain_db=gain_db,
                    loop=True,
                    reason="background_music_library",
                )
            ]
        switch_at = round(
            max(30.0, min(rendered_duration * 0.55, rendered_duration - 30.0)),
            3,
        )
        timeline_switch_seconds = round(timeline_start_seconds + switch_at, 3)
        return [
            AudioBed(
                source_path=str(tracks[0].path),
                timeline_start_seconds=timeline_start_seconds,
                timeline_end_seconds=timeline_switch_seconds,
                gain_db=gain_db,
                loop=True,
                reason="background_music_library",
            ),
            AudioBed(
                source_path=str(tracks[1].path),
                timeline_start_seconds=timeline_switch_seconds,
                timeline_end_seconds=None,
                gain_db=gain_db,
                loop=True,
                reason="background_music_library_climax",
            ),
        ]

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
        return sum(
            max(0.0, segment.source_end_seconds - segment.source_start_seconds)
            for segment in timeline
        )

    @staticmethod
    def _segment_duration(segment: TimelineSegment) -> float:
        return max(0.0, segment.source_end_seconds - segment.source_start_seconds)

    @staticmethod
    def _leading_teaser_duration(timeline: list[TimelineSegment]) -> float:
        duration = 0.0
        for segment in timeline:
            if segment.role != "teaser":
                break
            duration += max(0.0, segment.source_end_seconds - segment.source_start_seconds)
        return round(duration, 3)

    def _select_teaser_windows(
        self,
        windows: list[HighlightClipWindow],
        duration: float,
        *,
        subtitle: SubtitleAsset | None = None,
    ) -> list[HighlightClipWindow]:
        subtitle_cues = self._subtitle_cues(subtitle)
        candidates = self._teaser_candidates(
            windows,
            duration,
            reasons=_PRIMARY_TEASER_REASONS,
        )
        candidates.sort(
            key=lambda window: (
                -self._teaser_signal_score(window, subtitle_cues),
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
    ) -> bool:
        expected_windows = (
            self._select_teaser_windows(
                highlight_plan.windows,
                duration,
                subtitle=subtitle,
            )
            if highlight_plan is not None
            else []
        )
        teaser_segments = [
            segment for segment in plan.timeline if segment.role == "teaser"
        ]
        if len(teaser_segments) != len(expected_windows):
            return False
        return all(
            self._timeline_segment_matches_window(segment, window)
            for segment, window in zip(teaser_segments, expected_windows, strict=True)
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
