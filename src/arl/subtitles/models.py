from __future__ import annotations

from pydantic import BaseModel, Field


class SubtitleStateFile(BaseModel):
    processed_match_keys: list[str] = Field(default_factory=list)
