from __future__ import annotations

from datetime import datetime

from arl.config import Settings
from arl.segmenter.models import MatchStageHint
from arl.shared.contracts import MatchStage
from arl.shared.jsonl_store import append_model
from arl.shared.logging import log


class StageHintWriter:
    def __init__(self, settings: Settings) -> None:
        self.path = settings.storage.temp_dir / "match-stage-hints.jsonl"

    def append(
        self,
        session_id: str,
        stage: MatchStage,
        *,
        at_seconds: float | None = None,
        detected_at: datetime | None = None,
    ) -> MatchStageHint:
        if at_seconds is None and detected_at is None:
            raise ValueError("at_seconds or detected_at is required")

        hint = MatchStageHint(
            session_id=session_id,
            stage=stage,
            at_seconds=at_seconds,
            detected_at=detected_at,
        )
        append_model(self.path, hint)
        log(
            "segmenter",
            f"stage hint appended session_id={session_id} stage={stage.value}",
        )
        return hint
