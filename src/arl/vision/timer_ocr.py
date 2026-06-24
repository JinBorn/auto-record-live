from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np

from .models import TimerReading


def read_timer(
    frame: np.ndarray,
    timestamp_seconds: float,
    crop_region: tuple[int, int, int, int] = (1770, 5, 150, 50),
    detector: str = "auto",
) -> TimerReading:
    """Extract game timer from frame top-right region.

    Args:
        frame: BGR frame from opencv
        timestamp_seconds: Recording timestamp
        crop_region: (x, y, w, h) for timer crop
        detector: "auto" | "template" | "tesseract" | "easyocr"

    Returns:
        TimerReading with game_time_text=None if lobby/select screen
    """
    x, y, w, h = crop_region
    frame_h, frame_w = frame.shape[:2]

    if x + w > frame_w or y + h > frame_h:
        return TimerReading(timestamp_seconds, None, 0.0)

    cropped = frame[y : y + h, x : x + w]

    if detector == "auto":
        detector = "template"

    if detector == "template":
        return _read_timer_template(cropped, timestamp_seconds)
    elif detector == "tesseract":
        return _read_timer_tesseract(cropped, timestamp_seconds)
    elif detector == "easyocr":
        return _read_timer_easyocr(cropped, timestamp_seconds)
    else:
        raise ValueError(f"Unknown detector: {detector}")


def _read_timer_template(cropped: np.ndarray, timestamp_seconds: float) -> TimerReading:
    """Template matching based OCR for LoL timer digits.

    The configured crop also contains nearby HUD values on real recordings
    (gold, FPS, latency). Restrict detection to the upper row and read the
    rightmost four digits as ``MMSS`` so those lower-row diagnostics do not
    pollute timer parsing.
    """
    gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 145, 255, cv2.THRESH_BINARY)

    white_pixels = cv2.countNonZero(binary)
    total_pixels = binary.shape[0] * binary.shape[1]

    if white_pixels < total_pixels * 0.01:
        return TimerReading(timestamp_seconds, None, 0.0)

    text = _simple_digit_ocr(binary)
    if text and re.match(r"^\d{1,2}:\d{2}$", text) and _timer_text_is_valid(text):
        return TimerReading(timestamp_seconds, text, 0.85)

    return TimerReading(timestamp_seconds, None, 0.1)


def _simple_digit_ocr(binary: np.ndarray) -> str | None:
    """Extract timer text using contour analysis."""
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return None

    crop_height = binary.shape[0]
    digit_boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if y > crop_height * 0.55:
            continue
        if 3 <= w <= 18 and 8 <= h <= 28:
            digit_boxes.append((x, y, w, h))

    if len(digit_boxes) < 4:
        return None

    digit_boxes = _merge_nearby_digit_boxes(digit_boxes)
    if len(digit_boxes) < 4:
        return None

    digit_boxes.sort(key=lambda box: box[0])
    digit_boxes = digit_boxes[-4:]

    chars: list[str] = []
    for x, y, w, h in digit_boxes:
        digit_img = binary[y : y + h, x : x + w]
        digit = _recognize_digit_by_features(digit_img)
        if digit is None:
            continue

        chars.append(digit)

    if len(chars) != 4:
        return None

    result = f"{chars[0]}{chars[1]}:{chars[2]}{chars[3]}"

    if ":" in result:
        parts = result.split(":")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{int(parts[0]):d}:{int(parts[1]):02d}"

    return None


def _merge_nearby_digit_boxes(
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """Merge contour fragments that belong to the same timer digit."""
    if not boxes:
        return []

    merged: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=lambda item: item[0]):
        x, y, w, h = box
        if not merged:
            merged.append(box)
            continue

        last_x, last_y, last_w, last_h = merged[-1]
        last_right = last_x + last_w
        overlaps_vertically = not (y + h < last_y or last_y + last_h < y)
        if x <= last_right and overlaps_vertically:
            new_x = min(last_x, x)
            new_y = min(last_y, y)
            new_right = max(last_right, x + w)
            new_bottom = max(last_y + last_h, y + h)
            merged[-1] = (new_x, new_y, new_right - new_x, new_bottom - new_y)
            continue

        merged.append(box)

    return merged


def _recognize_digit_by_features(digit_img: np.ndarray) -> str | None:
    """Recognize one HUD digit with coarse seven-segment features."""
    if digit_img.size == 0:
        return None

    h, w = digit_img.shape
    if w < 3 or h < 8:
        return None

    if w <= 5:
        return "1"

    normalized = cv2.resize(digit_img, (14, 24), interpolation=cv2.INTER_NEAREST)
    zones = {
        "a": normalized[0:4, 3:11],
        "b": normalized[3:11, 9:14],
        "c": normalized[13:21, 9:14],
        "d": normalized[20:24, 3:11],
        "e": normalized[13:21, 0:5],
        "f": normalized[3:11, 0:5],
        "g": normalized[10:14, 3:11],
    }
    thresholds = {"b": 0.45, "e": 0.55}
    active = set()
    for segment, zone in zones.items():
        threshold = thresholds.get(segment, 0.35)
        if cv2.countNonZero(zone) / zone.size >= threshold:
            active.add(segment)

    patterns: dict[str, set[str]] = {
        "0": {"a", "b", "c", "d", "e", "f"},
        "2": {"a", "b", "d", "e", "g"},
        "3": {"a", "b", "c", "d", "g"},
        "4": {"b", "c", "f", "g"},
        "5": {"a", "c", "d", "f", "g"},
        "6": {"a", "c", "d", "e", "f", "g"},
        "7": {"a", "b", "c"},
        "8": {"a", "b", "c", "d", "e", "f", "g"},
        "9": {"a", "b", "c", "d", "f", "g"},
    }
    digit, score = min(
        (
            (candidate, len(active ^ expected))
            for candidate, expected in patterns.items()
        ),
        key=lambda item: item[1],
    )
    return digit if score <= 2 else None


def _timer_text_is_valid(text: str) -> bool:
    try:
        minutes, seconds = text.split(":", 1)
        return bool(minutes) and 0 <= int(seconds) <= 59
    except ValueError:
        return False


def _read_timer_tesseract(cropped: np.ndarray, timestamp_seconds: float) -> TimerReading:
    """Tesseract-based OCR (requires pytesseract)."""
    try:
        import pytesseract

        gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)

        text = pytesseract.image_to_string(
            binary, config="--psm 7 digits"
        ).strip()

        if re.match(r"^\d{1,2}:\d{2}$", text):
            return TimerReading(timestamp_seconds, text, 0.9)

        return TimerReading(timestamp_seconds, None, 0.1)
    except ImportError:
        return TimerReading(timestamp_seconds, None, 0.0)


def _read_timer_easyocr(cropped: np.ndarray, timestamp_seconds: float) -> TimerReading:
    """EasyOCR-based OCR (downloads ~100MB model on first run)."""
    try:
        import easyocr

        reader = easyocr.Reader(["en"], gpu=False)
        results = reader.readtext(cropped)

        for _, text, conf in results:
            text = text.strip()
            if re.match(r"^\d{1,2}:\d{2}$", text):
                return TimerReading(timestamp_seconds, text, float(conf))

        return TimerReading(timestamp_seconds, None, 0.1)
    except ImportError:
        return TimerReading(timestamp_seconds, None, 0.0)
