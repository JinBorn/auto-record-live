from __future__ import annotations

import re
from typing import Any

import cv2


def read_respawn_countdown(
    frame: Any,
    crop_region: tuple[int, int, int, int],
) -> tuple[int | None, float]:
    crop = _crop(frame, crop_region)
    if crop is None:
        return None, 0.0
    try:
        import pytesseract
    except ImportError:
        return None, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    _, binary = cv2.threshold(gray, 165, 255, cv2.THRESH_BINARY)
    text = pytesseract.image_to_string(
        binary,
        config="--psm 7 -c tessedit_char_whitelist=0123456789秒",
    )
    match = re.search(r"(?<!\d)(\d{1,3})(?!\d)", text)
    if match is None:
        return None, 0.0
    seconds = int(match.group(1))
    if not 1 <= seconds <= 120:
        return None, 0.0
    return seconds, 0.8


def read_match_result(
    frame: Any,
    crop_region: tuple[int, int, int, int],
) -> tuple[str | None, float]:
    crop = _crop(frame, crop_region)
    if crop is None:
        return None, 0.0
    try:
        import pytesseract
    except ImportError:
        return None, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    text = pytesseract.image_to_string(gray, lang="chi_sim", config="--psm 6")
    normalized = re.sub(r"\s+", "", text)
    if "胜利" in normalized:
        return "victory", 0.9
    if "失败" in normalized:
        return "defeat", 0.9
    return None, 0.0


def _crop(frame: Any, region: tuple[int, int, int, int]):
    shape = getattr(frame, "shape", None)
    if not shape or len(shape) < 2:
        return None
    x, y, width, height = region
    frame_height, frame_width = shape[:2]
    if (
        x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x + width > frame_width
        or y + height > frame_height
    ):
        return None
    return frame[y : y + height, x : x + width]
