from __future__ import annotations

import json
from pathlib import Path

from .models import VisionAnalysisStateFile


class VisionAnalysisStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> VisionAnalysisStateFile:
        if not self.path.exists():
            return VisionAnalysisStateFile()
        try:
            return VisionAnalysisStateFile.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return VisionAnalysisStateFile()

    def save(self, state: VisionAnalysisStateFile) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
