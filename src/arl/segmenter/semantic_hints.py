from __future__ import annotations

from arl.config import Settings
from arl.segmenter.models import MatchStageHint, MatchStageSignal
from arl.segmenter.signals_from_subtitles import StageSignalFromSubtitlesService
from arl.segmenter.stage_text import classify_stage_from_text, load_stage_keywords
from arl.shared.contracts import MatchStage, RecordingAsset
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log


class SemanticStageHintService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.recording_assets_path = settings.storage.temp_dir / "recording-assets.jsonl"
        self.match_stage_hints_path = settings.storage.temp_dir / "match-stage-hints.jsonl"
        self.match_stage_signals_path = settings.storage.temp_dir / "match-stage-signals.jsonl"
        self.stage_keywords = load_stage_keywords(
            settings.segmenter.stage_keywords_path,
            component="segmenter",
        )
        self.cycle_seconds = max(60, int(settings.recording.segment_minutes) * 60)
        self.champion_select_seconds = 45.0
        self.loading_seconds = 30.0
        self.post_game_seconds = 25.0
        self._stage_order = {
            MatchStage.CHAMPION_SELECT: 0,
            MatchStage.LOADING: 1,
            MatchStage.IN_GAME: 2,
            MatchStage.POST_GAME: 3,
            MatchStage.UNKNOWN: 4,
        }

    def run(self) -> None:
        log("segmenter", "stage-hints-semantic starting")
        try:
            StageSignalFromSubtitlesService(self.settings).run()
        except Exception as exc:
            log("segmenter", f"stage-hints-semantic subtitle signal ingest skipped reason={exc}")
        assets = load_models(self.recording_assets_path, RecordingAsset)
        existing_hints = load_models(self.match_stage_hints_path, MatchStageHint)
        signals = load_models(self.match_stage_signals_path, MatchStageSignal)
        signals_by_session = self._group_signals_by_session(signals)
        sessions_with_hints = {hint.session_id for hint in existing_hints}

        processed_assets = 0
        emitted_hints = 0
        for asset in assets:
            if asset.session_id in sessions_with_hints:
                continue

            duration = self._duration_seconds(asset)
            signal_hints = self._build_semantic_hints_from_signals(
                asset,
                duration,
                signals_by_session.get(asset.session_id, []),
            )
            if signal_hints:
                hints = signal_hints
                strategy = "signals"
            else:
                hints = self._build_template_hints(asset.session_id, duration)
                strategy = "template"

            for hint in hints:
                append_model(self.match_stage_hints_path, hint)
                emitted_hints += 1

            sessions_with_hints.add(asset.session_id)
            processed_assets += 1
            log(
                "segmenter",
                f"semantic stage hints emitted session_id={asset.session_id} strategy={strategy} count={len(hints)}",
            )

        log(
            "segmenter",
            f"stage-hints-semantic processed_assets={processed_assets} emitted_hints={emitted_hints}",
        )

    def _duration_seconds(self, asset: RecordingAsset) -> float:
        if asset.ended_at is None:
            return 1800.0
        duration = (asset.ended_at - asset.started_at).total_seconds()
        return max(60.0, duration)

    def _build_template_hints(
        self,
        session_id: str,
        duration: float,
    ) -> list[MatchStageHint]:
        hints: list[MatchStageHint] = []
        cycle_start = 0.0
        while cycle_start < duration:
            cycle_end = min(duration, cycle_start + self.cycle_seconds)
            for stage, at_seconds in self._cycle_stage_points(cycle_start, cycle_end):
                hints.append(
                    MatchStageHint(
                        session_id=session_id,
                        stage=stage,
                        at_seconds=round(at_seconds, 3),
                    )
                )
            cycle_start += self.cycle_seconds
        return hints

    def _cycle_stage_points(
        self,
        cycle_start: float,
        cycle_end: float,
    ) -> list[tuple[MatchStage, float]]:
        champion_at = cycle_start
        loading_at = min(cycle_end, champion_at + self.champion_select_seconds)
        in_game_at = loading_at + self.loading_seconds
        if in_game_at >= cycle_end:
            in_game_at = max(cycle_start, cycle_end - max(1.0, self.post_game_seconds))
        post_game_at = max(in_game_at, cycle_end - self.post_game_seconds)
        return [
            (MatchStage.CHAMPION_SELECT, champion_at),
            (MatchStage.LOADING, loading_at),
            (MatchStage.IN_GAME, in_game_at),
            (MatchStage.POST_GAME, post_game_at),
        ]

    def _build_semantic_hints_from_signals(
        self,
        asset: RecordingAsset,
        duration: float,
        signals: list[MatchStageSignal],
    ) -> list[MatchStageHint]:
        events: list[tuple[float, MatchStage]] = []
        for signal in signals:
            stage = classify_stage_from_text(signal.text, self.stage_keywords)
            if stage is None:
                continue
            at_seconds = self._signal_at_seconds(asset, signal)
            if at_seconds is None:
                continue
            if at_seconds < 0.0 or at_seconds >= duration:
                continue
            events.append((round(at_seconds, 3), stage))

        if not events:
            return []

        events.sort(key=lambda item: (item[0], self._stage_order[item[1]]))
        deduped: list[tuple[float, MatchStage]] = []
        for at_seconds, stage in events:
            if deduped and deduped[-1][1] == stage:
                continue
            deduped.append((at_seconds, stage))

        if not any(stage == MatchStage.IN_GAME for _, stage in deduped):
            return []

        return [
            MatchStageHint(
                session_id=asset.session_id,
                stage=stage,
                at_seconds=at_seconds,
            )
            for at_seconds, stage in deduped
        ]

    def _signal_at_seconds(
        self,
        asset: RecordingAsset,
        signal: MatchStageSignal,
    ) -> float | None:
        if signal.at_seconds is not None:
            return signal.at_seconds
        if signal.detected_at is None:
            return None
        return (signal.detected_at - asset.started_at).total_seconds()

    def _group_signals_by_session(
        self,
        signals: list[MatchStageSignal],
    ) -> dict[str, list[MatchStageSignal]]:
        grouped: dict[str, list[MatchStageSignal]] = {}
        for signal in signals:
            if signal.session_id not in grouped:
                grouped[signal.session_id] = []
            grouped[signal.session_id].append(signal)
        return grouped
