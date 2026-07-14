from __future__ import annotations

import re
import shutil
from functools import lru_cache
from typing import Any

import cv2
from arl.vision.kda_ocr import _extract_char_boxes, _preprocess, _recognize_char


def read_respawn_countdown(
    frame: Any,
    crop_region: tuple[int, int, int, int],
) -> tuple[int | None, float]:
    crop = _crop(frame, crop_region)
    if crop is None:
        return None, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    _, binary = cv2.threshold(gray, 165, 255, cv2.THRESH_BINARY)
    text = ""
    try:
        if not _tesseract_available():
            raise RuntimeError("tesseract unavailable")
        import pytesseract

        text = pytesseract.image_to_string(
            binary,
            config="--psm 7 -c tessedit_char_whitelist=0123456789秒",
        )
    except Exception:
        text = _template_digits(crop)
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
    if not _tesseract_chinese_available():
        return None, 0.0
    crop = _crop(frame, crop_region)
    if crop is None:
        return None, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    text = ""
    try:
        import pytesseract

        text = pytesseract.image_to_string(gray, lang="chi_sim", config="--psm 6")
    except Exception:
        pass
    normalized = re.sub(r"\s+", "", text)
    if "胜利" in normalized:
        return "victory", 0.9
    if "失败" in normalized:
        return "defeat", 0.9
    return None, 0.0


def looks_like_player_dead(frame: Any) -> bool:
    """Detect zero-health player HUD on the supported 1080p layout."""
    crop = _crop(frame, (750, 990, 400, 60))
    if crop is None:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    green = (
        (hsv[:, :, 0] >= 35)
        & (hsv[:, :, 0] <= 85)
        & (hsv[:, :, 1] >= 80)
        & (hsv[:, :, 2] >= 60)
    )
    return float(green.mean()) <= 0.02


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


def _template_digits(crop: Any) -> str:
    binary = _preprocess(crop)
    digits: list[str] = []
    scores: list[float] = []
    for x, y, width, height in _extract_char_boxes(binary):
        char, score = _recognize_char(binary[y : y + height, x : x + width])
        if char is not None and char.isdigit():
            digits.append(char)
            scores.append(score)
    if not digits or max(scores, default=0.0) < 0.45:
        return ""
    return "".join(digits[-3:])


@lru_cache(maxsize=1)
def _tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


@lru_cache(maxsize=1)
def _tesseract_chinese_available() -> bool:
    if not _tesseract_available():
        return False
    try:
        import pytesseract

        return "chi_sim" in pytesseract.get_languages(config="")
    except Exception:
        return False
