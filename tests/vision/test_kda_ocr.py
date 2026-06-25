from __future__ import annotations

import cv2
import numpy as np

from arl.vision.kda_ocr import read_kda


def _frame_with_text(text: str, *, width: int = 140, height: int = 40) -> np.ndarray:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.putText(
        frame,
        text,
        (2, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return frame


def test_read_kda_synthetic_single_digit() -> None:
    reading = read_kda(_frame_with_text("1/0/0"), 12.0, (0, 0, 140, 40))

    assert reading.timestamp_seconds == 12.0
    assert reading.kills == 1
    assert reading.deaths == 0
    assert reading.assists == 0
    assert reading.confidence >= 0.4


def test_read_kda_synthetic_multi_digit() -> None:
    reading = read_kda(_frame_with_text("12/3/10"), 18.0, (0, 0, 140, 40))

    assert reading.kills == 12
    assert reading.deaths == 3
    assert reading.assists == 10
    assert reading.confidence >= 0.4


def test_read_kda_rejects_blank_crop() -> None:
    frame = np.zeros((40, 140, 3), dtype=np.uint8)

    reading = read_kda(frame, 30.0, (0, 0, 140, 40))

    assert reading.kills is None
    assert reading.deaths is None
    assert reading.assists is None
    assert reading.confidence == 0.0
