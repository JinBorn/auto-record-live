from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VisionLayoutProfile:
    name: str
    width: int
    height: int

    def supports(self, frame: Any) -> bool:
        shape = getattr(frame, "shape", None)
        return bool(shape and len(shape) >= 2 and shape[1] == self.width and shape[0] == self.height)


LOL_ZH_1080P = VisionLayoutProfile(
    name="lol_zh_1080p_v1",
    width=1920,
    height=1080,
)
