from __future__ import annotations

import re
from functools import lru_cache

import cv2
import numpy as np

from .models import KdaReading


DEFAULT_KDA_CROP_REGION = (1665, 0, 85, 32)


def read_kda(
    frame: np.ndarray,
    timestamp_seconds: float,
    crop_region: tuple[int, int, int, int] = DEFAULT_KDA_CROP_REGION,
) -> KdaReading:
    """Extract the player's K/D/A HUD text from the top-right game region."""
    x, y, w, h = crop_region
    frame_h, frame_w = frame.shape[:2]
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > frame_w or y + h > frame_h:
        return KdaReading(timestamp_seconds, None, None, None, 0.0)

    cropped = frame[y : y + h, x : x + w]
    binary = _preprocess(cropped)
    total_pixels = binary.shape[0] * binary.shape[1]
    if cv2.countNonZero(binary) < total_pixels * 0.01:
        return KdaReading(timestamp_seconds, None, None, None, 0.0)

    chars: list[str] = []
    scores: list[float] = []
    for box in _extract_char_boxes(binary):
        x0, y0, w0, h0 = box
        char, score = _recognize_char(binary[y0 : y0 + h0, x0 : x0 + w0])
        if char is None:
            return KdaReading(timestamp_seconds, None, None, None, 0.1)
        chars.append(char)
        scores.append(score)

    text = "".join(chars)
    if not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{1,2}", text):
        return KdaReading(timestamp_seconds, None, None, None, 0.1)

    kills_raw, deaths_raw, assists_raw = text.split("/")
    confidence = min(1.0, sum(scores) / max(1, len(scores)))
    return KdaReading(
        timestamp_seconds=timestamp_seconds,
        kills=int(kills_raw),
        deaths=int(deaths_raw),
        assists=int(assists_raw),
        confidence=confidence,
    )


def _preprocess(cropped: np.ndarray) -> np.ndarray:
    gray = (
        cropped
        if len(cropped.shape) == 2
        else cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
    )
    _, binary = cv2.threshold(gray, 145, 255, cv2.THRESH_BINARY)
    return binary


def _extract_char_boxes(binary: np.ndarray) -> list[tuple[int, int, int, int]]:
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    crop_h, crop_w = binary.shape[:2]
    boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if y > crop_h * 0.8:
            continue
        if w < 2 or h < max(7, crop_h * 0.25):
            continue
        if w > max(24, crop_w * 0.35) or h > crop_h:
            continue
        if area < 2.0:
            continue
        boxes.append((x, y, w, h))

    return _merge_touching_boxes(sorted(boxes, key=lambda item: item[0]))


def _merge_touching_boxes(
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    if not boxes:
        return []

    merged: list[tuple[int, int, int, int]] = []
    for box in boxes:
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


def _recognize_char(char_img: np.ndarray) -> tuple[str | None, float]:
    if char_img.size == 0:
        return None, 0.0

    narrow = _recognize_narrow_char(char_img)
    if narrow is not None:
        return narrow

    best_char: str | None = None
    best_score = 0.0
    for candidate, template in _templates():
        resized = cv2.resize(
            char_img,
            (template.shape[1], template.shape[0]),
            interpolation=cv2.INTER_AREA,
        )
        _, resized = cv2.threshold(resized, 100, 255, cv2.THRESH_BINARY)
        intersection = np.logical_and(resized > 0, template > 0).sum()
        union = np.logical_or(resized > 0, template > 0).sum()
        score = float(intersection / union) if union else 0.0
        if score > best_score:
            best_char = candidate
            best_score = score

    if best_char is None or best_score < 0.32:
        return None, best_score
    return best_char, best_score


def _recognize_narrow_char(char_img: np.ndarray) -> tuple[str, float] | None:
    h, w = char_img.shape[:2]
    if w > 7 or h < 8:
        return None

    ys, xs = np.where(char_img > 0)
    if len(xs) < 5:
        return None

    matrix = np.vstack([ys, np.ones_like(ys)]).T
    slope, _ = np.linalg.lstsq(matrix, xs, rcond=None)[0]
    density = len(xs) / float(w * h)
    if slope < -0.12:
        return "/", min(0.95, 0.65 + min(0.3, abs(float(slope))))
    if slope > -0.05 and density >= 0.25:
        return "1", min(0.95, 0.65 + min(0.3, density))
    return None


@lru_cache(maxsize=1)
def _templates() -> tuple[tuple[str, np.ndarray], ...]:
    templates: list[tuple[str, np.ndarray]] = []
    for char in "0123456789/":
        for font_scale in (0.5, 0.55, 0.6, 0.65, 0.7, 0.75):
            for thickness in (1, 2):
                img = np.zeros((36, 28), dtype=np.uint8)
                cv2.putText(
                    img,
                    char,
                    (2, 27),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale,
                    255,
                    thickness,
                    cv2.LINE_AA,
                )
                _, img = cv2.threshold(img, 80, 255, cv2.THRESH_BINARY)
                ys, xs = np.where(img > 0)
                if len(xs) == 0:
                    continue
                templates.append((char, img[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]))
    return tuple(templates)
