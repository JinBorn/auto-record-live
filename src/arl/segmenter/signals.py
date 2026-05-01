from __future__ import annotations

from datetime import datetime

from arl.config import Settings
from arl.segmenter.models import MatchStageSignal
from arl.shared.jsonl_store import append_model
from arl.shared.logging import log


class StageSignalWriter:
    def __init__(self, settings: Settings) -> None:
        self.path = settings.storage.temp_dir / "match-stage-signals.jsonl"

    def append(
        self,
        session_id: str,
        text: str,
        *,
        source: str = "manual",
        at_seconds: float | None = None,
        detected_at: datetime | None = None,
    ) -> MatchStageSignal:
        if at_seconds is None and detected_at is None:
            raise ValueError("at_seconds or detected_at is required")

        signal = MatchStageSignal(
            session_id=session_id,
            text=text,
            source=source,
            at_seconds=at_seconds,
            detected_at=detected_at,
        )
        append_model(self.path, signal)
        log(
            "segmenter",
            f"stage signal appended session_id={session_id} source={source}",
        )
        return signal
