from __future__ import annotations

from arl.config import Settings
from arl.segmenter.models import MatchStageHint
from arl.shared.contracts import MatchStage, RecordingAsset
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log


class AutoStageHintService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.recording_assets_path = settings.storage.temp_dir / "recording-assets.jsonl"
        self.match_stage_hints_path = settings.storage.temp_dir / "match-stage-hints.jsonl"

    def run(self) -> None:
        log("segmenter", "stage-hints-auto starting")
        assets = load_models(self.recording_assets_path, RecordingAsset)
        existing_hints = load_models(self.match_stage_hints_path, MatchStageHint)
        sessions_with_in_game_hint = {
            hint.session_id for hint in existing_hints if hint.stage == MatchStage.IN_GAME
        }

        processed_assets = 0
        emitted_hints = 0
        for asset in assets:
            if asset.session_id in sessions_with_in_game_hint:
                continue

            duration = self._duration_seconds(asset)
            for at_seconds in self._build_in_game_starts(duration):
                append_model(
                    self.match_stage_hints_path,
                    MatchStageHint(
                        session_id=asset.session_id,
                        stage=MatchStage.IN_GAME,
                        at_seconds=at_seconds,
                    ),
                )
                emitted_hints += 1
            sessions_with_in_game_hint.add(asset.session_id)
            processed_assets += 1
            log(
                "segmenter",
                f"auto stage hints emitted session_id={asset.session_id}",
            )

        log(
            "segmenter",
            f"stage-hints-auto processed_assets={processed_assets} emitted_hints={emitted_hints}",
        )

    def _duration_seconds(self, asset: RecordingAsset) -> float:
        if asset.ended_at is None:
            return 1800.0
        duration = (asset.ended_at - asset.started_at).total_seconds()
        return max(60.0, duration)

    def _build_in_game_starts(self, duration: float) -> list[float]:
        interval_seconds = max(60, int(self.settings.recording.segment_minutes) * 60)
        starts: list[float] = []
        cursor = 0
        while cursor < int(duration):
            starts.append(float(cursor))
            cursor += interval_seconds
        if not starts:
            return [0.0]
        return starts
