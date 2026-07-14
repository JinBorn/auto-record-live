from __future__ import annotations

from pathlib import Path

from arl.config import Settings
from arl.segmenter.durations import recording_duration_seconds
from arl.segmenter.models import MatchStageHint, SegmenterStateFile
from arl.shared.contracts import MatchBoundary, MatchStage, RecordingAsset
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log


class SegmenterService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.recording_assets_path = settings.storage.temp_dir / "recording-assets.jsonl"
        self.match_stage_hints_path = settings.storage.temp_dir / "match-stage-hints.jsonl"
        self.boundaries_path = settings.storage.temp_dir / "match-boundaries.jsonl"
        self.state_path = settings.storage.temp_dir / "segmenter-state.json"

    def run(
        self,
        *,
        session_ids: set[str] | None = None,
        force_reprocess: bool = False,
    ) -> None:
        log("segmenter", "starting")
        assets = load_models(self.recording_assets_path, RecordingAsset)
        filtered_assets = self._filter_assets(assets, session_ids=session_ids)
        if session_ids is not None:
            log(
                "segmenter",
                (
                    "filters "
                    f"total_assets={len(assets)} matched_assets={len(filtered_assets)} "
                    f"session_ids={','.join(sorted(session_ids))}"
                ),
            )
        stage_hints = load_models(self.match_stage_hints_path, MatchStageHint)
        all_boundaries = load_models(self.boundaries_path, MatchBoundary)
        hints_by_session = self._group_hints_by_session(stage_hints)
        state = self._load_state()
        processed_asset_keys = set(state.processed_asset_keys)

        if force_reprocess and filtered_assets:
            force_session_ids = {asset.session_id for asset in filtered_assets}
            removed_rows = self._rewrite_boundaries_excluding_sessions(force_session_ids)
            if removed_rows:
                log(
                    "segmenter",
                    "force reprocessing boundaries "
                    f"session_ids={','.join(sorted(force_session_ids))} "
                    f"removed_rows={removed_rows}",
                )
            all_boundaries = [
                boundary
                for boundary in all_boundaries
                if boundary.session_id not in force_session_ids
            ]
            state.processed_asset_keys = [
                key
                for key in state.processed_asset_keys
                if not self._key_matches_any_session(key, force_session_ids)
            ]
            processed_asset_keys = set(state.processed_asset_keys)

        existing_boundary_sessions = {boundary.session_id for boundary in all_boundaries}

        processed = 0
        for asset in filtered_assets:
            key = f"{asset.session_id}:{asset.path}"
            if key in processed_asset_keys and asset.session_id in existing_boundary_sessions:
                continue
            if key in processed_asset_keys:
                log(
                    "segmenter",
                    f"reprocessing missing boundaries session_id={asset.session_id}",
                )

            duration = self._duration_seconds(asset)
            boundaries = self._build_boundaries_with_vision(
                asset,
                duration,
                hints_by_session.get(asset.session_id, []),
            )
            for boundary in boundaries:
                append_model(self.boundaries_path, boundary)
            if key not in processed_asset_keys:
                state.processed_asset_keys.append(key)
                processed_asset_keys.add(key)
            existing_boundary_sessions.add(asset.session_id)
            processed += 1
            log(
                "segmenter",
                f"match boundaries emitted session_id={asset.session_id} count={len(boundaries)}",
            )

        self._save_state(state)
        log("segmenter", f"processed_assets={processed}")

    def _filter_assets(
        self,
        assets: list[RecordingAsset],
        *,
        session_ids: set[str] | None,
    ) -> list[RecordingAsset]:
        if session_ids is None:
            return assets
        return [asset for asset in assets if asset.session_id in session_ids]

    def _duration_seconds(self, asset: RecordingAsset) -> float:
        return recording_duration_seconds(asset)

    def _build_boundaries(
        self,
        asset: RecordingAsset,
        duration: float,
        stage_hints: list[MatchStageHint],
    ) -> list[MatchBoundary]:
        in_game_starts = self._resolve_in_game_starts(asset, duration, stage_hints)
        if not in_game_starts:
            return [self._fallback_boundary(asset, duration)]

        boundaries: list[MatchBoundary] = []
        post_game_times = self._resolve_post_game_times(asset, duration, stage_hints)
        for index, start in enumerate(in_game_starts):
            fallback_end = (
                duration if index + 1 >= len(in_game_starts) else in_game_starts[index + 1]
            )
            end = self._boundary_end_from_post_game(
                start=start,
                fallback_end=fallback_end,
                post_game_times=post_game_times,
            )
            if end <= start:
                continue
            boundaries.append(
                MatchBoundary(
                    session_id=asset.session_id,
                    match_index=len(boundaries) + 1,
                    started_at_seconds=start,
                    ended_at_seconds=end,
                    confidence=0.8,
                    is_complete=True,
                    reason="stage_hints",
                )
            )

        if not boundaries:
            return [self._fallback_boundary(asset, duration)]
        return boundaries

    def _resolve_in_game_starts(
        self,
        asset: RecordingAsset,
        duration: float,
        stage_hints: list[MatchStageHint],
    ) -> list[float]:
        starts: set[float] = set()
        for hint in stage_hints:
            if hint.stage != MatchStage.IN_GAME:
                continue
            at_seconds = self._hint_at_seconds(asset, hint)
            if at_seconds is None:
                continue
            if at_seconds < 0.0 or at_seconds >= duration:
                continue
            starts.add(round(at_seconds, 3))
        return sorted(starts)

    def _resolve_post_game_times(
        self,
        asset: RecordingAsset,
        duration: float,
        stage_hints: list[MatchStageHint],
    ) -> list[float]:
        times: set[float] = set()
        for hint in stage_hints:
            if hint.stage != MatchStage.POST_GAME:
                continue
            at_seconds = self._hint_at_seconds(asset, hint)
            if at_seconds is None:
                continue
            if at_seconds <= 0.0 or at_seconds > duration:
                continue
            times.add(round(at_seconds, 3))
        return sorted(times)

    @staticmethod
    def _boundary_end_from_post_game(
        *,
        start: float,
        fallback_end: float,
        post_game_times: list[float],
    ) -> float:
        for at_seconds in post_game_times:
            if start < at_seconds <= fallback_end:
                return at_seconds
        return fallback_end

    def _hint_at_seconds(
        self,
        asset: RecordingAsset,
        hint: MatchStageHint,
    ) -> float | None:
        if hint.at_seconds is not None:
            return hint.at_seconds
        if hint.detected_at is None:
            return None
        return (hint.detected_at - asset.started_at).total_seconds()

    def _fallback_boundary(self, asset: RecordingAsset, duration: float) -> MatchBoundary:
        return MatchBoundary(
            session_id=asset.session_id,
            match_index=1,
            started_at_seconds=0.0,
            ended_at_seconds=duration,
            confidence=0.5,
            is_complete=False,
            reason="fallback_no_reliable_match_signal",
        )

    def _group_hints_by_session(
        self,
        stage_hints: list[MatchStageHint],
    ) -> dict[str, list[MatchStageHint]]:
        grouped: dict[str, list[MatchStageHint]] = {}
        for hint in stage_hints:
            if hint.session_id not in grouped:
                grouped[hint.session_id] = []
            grouped[hint.session_id].append(hint)
        return grouped

    def _rewrite_boundaries_excluding_sessions(self, session_ids: set[str]) -> int:
        if not session_ids:
            return 0
        boundaries = load_models(self.boundaries_path, MatchBoundary)
        kept = [
            boundary for boundary in boundaries if boundary.session_id not in session_ids
        ]
        removed = len(boundaries) - len(kept)
        if removed == 0:
            return 0
        self.boundaries_path.parent.mkdir(parents=True, exist_ok=True)
        with self.boundaries_path.open("w", encoding="utf-8") as handle:
            for boundary in kept:
                handle.write(boundary.model_dump_json())
                handle.write("\n")
        return removed

    @staticmethod
    def _key_matches_any_session(key: str, session_ids: set[str]) -> bool:
        return any(key.startswith(f"{session_id}:") for session_id in session_ids)

    def _load_state(self) -> SegmenterStateFile:
        if not self.state_path.exists():
            return SegmenterStateFile()
        return SegmenterStateFile.model_validate_json(self.state_path.read_text(encoding="utf-8"))

    def _save_state(self, state: SegmenterStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _build_boundaries_with_vision(
        self,
        asset: RecordingAsset,
        duration: float,
        stage_hints: list[MatchStageHint],
    ) -> list[MatchBoundary]:
        """Build boundaries using vision detection if enabled, else fall back to legacy."""
        if self.settings.vision.match_detection_enabled:
            try:
                return self._detect_matches_visually(asset, duration)
            except Exception as e:
                log(
                    "segmenter",
                    f"vision detection failed: {e}, falling back to legacy session_id={asset.session_id}",
                )
        return self._build_boundaries(asset, duration, stage_hints)

    def _detect_matches_visually(
        self,
        asset: RecordingAsset,
        duration: float,
    ) -> list[MatchBoundary]:
        """Use vision module to detect match boundaries."""
        from arl.vision import VisionMatchDetector
        from arl.vision_analysis.view import VisionAnalysisView

        recording_path = Path(asset.path)
        if not recording_path.exists():
            raise FileNotFoundError(f"Recording not found: {recording_path}")

        detector = VisionMatchDetector(self.settings.vision)
        view = VisionAnalysisView.latest_for_session(
            self.settings.storage.temp_dir / "vision-analysis-assets.jsonl",
            asset.session_id,
        )
        segments = []
        if (
            view is not None
            and view.detector_usable("timer")
            and view.detector_usable("scene")
        ):
            candidate_segments = detector.detect_from_readings(
                timer_readings=view.timer_readings(),
                scene_readings=view.scene_readings(),
            )
            if candidate_segments:
                segments = candidate_segments
                log(
                    "segmenter",
                    f"vision source=shared_asset session_id={asset.session_id}",
                )
        if not segments:
            log(
                "segmenter",
                f"vision source=legacy_scan session_id={asset.session_id}",
            )
            segments = detector.detect(recording_path)

        if not segments:
            log(
                "segmenter",
                f"vision detected no segments, using fallback session_id={asset.session_id}",
            )
            return [self._fallback_boundary(asset, duration)]

        boundaries: list[MatchBoundary] = []
        for idx, segment in enumerate(segments, start=1):
            if not segment.is_complete:
                log(
                    "segmenter",
                    "vision marked incomplete match "
                    f"session_id={asset.session_id} match_index={idx} "
                    f"reason={segment.reason} confidence={segment.confidence:.2f}",
                )
            boundaries.append(
                MatchBoundary(
                    session_id=asset.session_id,
                    match_index=idx,
                    started_at_seconds=segment.start_seconds,
                    ended_at_seconds=segment.end_seconds,
                    confidence=segment.confidence,
                    is_complete=segment.is_complete,
                    reason=segment.reason,
                )
            )

        log(
            "segmenter",
            f"vision detected {len(boundaries)} segments session_id={asset.session_id}",
        )
        return boundaries
