from __future__ import annotations

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

    def run(self, *, session_ids: set[str] | None = None) -> None:
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
        existing_boundary_sessions = {
            boundary.session_id
            for boundary in load_models(self.boundaries_path, MatchBoundary)
        }
        hints_by_session = self._group_hints_by_session(stage_hints)
        state = self._load_state()
        processed_asset_keys = set(state.processed_asset_keys)

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
            boundaries = self._build_boundaries(
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
        for index, start in enumerate(in_game_starts):
            end = duration if index + 1 >= len(in_game_starts) else in_game_starts[index + 1]
            if end <= start:
                continue
            boundaries.append(
                MatchBoundary(
                    session_id=asset.session_id,
                    match_index=len(boundaries) + 1,
                    started_at_seconds=start,
                    ended_at_seconds=end,
                    confidence=0.8,
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

    def _load_state(self) -> SegmenterStateFile:
        if not self.state_path.exists():
            return SegmenterStateFile()
        return SegmenterStateFile.model_validate_json(self.state_path.read_text(encoding="utf-8"))

    def _save_state(self, state: SegmenterStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
