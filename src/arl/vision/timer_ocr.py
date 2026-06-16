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
    """Template matching based OCR for LoL timer digits."""
    gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)

    white_pixels = cv2.countNonZero(binary)
    total_pixels = binary.shape[0] * binary.shape[1]

    if white_pixels < total_pixels * 0.05:
        return TimerReading(timestamp_seconds, None, 0.0)

    text = _simple_digit_ocr(binary)
    if text and re.match(r"^\d{1,2}:\d{2}$", text):
        return TimerReading(timestamp_seconds, text, 0.85)

    return TimerReading(timestamp_seconds, None, 0.1)


def _simple_digit_ocr(binary: np.ndarray) -> str | None:
    """Extract timer text using contour analysis."""
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return None

    digit_boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if 5 <= w <= 20 and 10 <= h <= 40:
            digit_boxes.append((x, y, w, h))

    if len(digit_boxes) < 4:
        return None

    digit_boxes.sort(key=lambda box: box[0])

    chars: list[str] = []
    for i, (x, y, w, h) in enumerate(digit_boxes):
        digit_img = binary[y : y + h, x : x + w]
        digit = _recognize_digit_by_features(digit_img)

        if digit is None:
            if i > 0 and len(chars) > 0 and chars[-1] != ":":
                chars.append(":")
            continue

        chars.append(digit)

    result = "".join(chars)
    result = re.sub(r":{2,}", ":", result)

    if ":" in result:
        parts = result.split(":")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{int(parts[0]):d}:{int(parts[1]):02d}"

    return None


def _recognize_digit_by_features(digit_img: np.ndarray) -> str | None:
    """Recognize single digit using simple features."""
    if digit_img.size == 0:
        return None

    h, w = digit_img.shape
    if w < 5 or h < 10:
        return None

    white_ratio = cv2.countNonZero(digit_img) / (h * w)

    if white_ratio < 0.2:
        return "1"
    elif white_ratio > 0.7:
        return "8"
    elif white_ratio > 0.55:
        return "0"
    elif white_ratio > 0.45:
        return "6"
    else:
        top_half = digit_img[: h // 2, :]
        bottom_half = digit_img[h // 2 :, :]
        top_ratio = cv2.countNonZero(top_half) / top_half.size
        bottom_ratio = cv2.countNonZero(bottom_half) / bottom_half.size

        if top_ratio > bottom_ratio * 1.3:
            return "2"
        elif bottom_ratio > top_ratio * 1.3:
            return "5"
        else:
            return "3"


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
