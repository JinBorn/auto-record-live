from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import cv2
import numpy as np

from arl.vision.scene_classifier import classify_scene


def test_classify_in_game_from_hud_and_minimap_regions():
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    _draw_grid(frame, 1620, 720, 290, 330)
    _draw_grid(frame, 600, 850, 780, 210)

    reading = classify_scene(frame, 10.0)

    assert reading.scene == "in_game"
    assert reading.confidence > 0.7


def test_classify_loading_from_dark_top_and_card_edges():
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    for y in (120, 590):
        for index in range(5):
            left = 230 + index * 320
            cv2.rectangle(frame, (left, y), (left + 260, y + 420), (220, 220, 220), 3)
            cv2.rectangle(frame, (left + 35, y + 70), (left + 225, y + 350), (180, 180, 180), 2)
            for offset in range(30, 230, 35):
                cv2.line(frame, (left + offset, y + 80), (left + offset, y + 340), (160, 160, 160), 1)
            for offset in range(90, 330, 35):
                cv2.line(frame, (left + 40, y + offset), (left + 220, y + offset), (160, 160, 160), 1)

    reading = classify_scene(frame, 20.0)

    assert reading.scene == "loading"
    assert reading.confidence > 0.7


def test_classify_other_when_no_hud_or_loading_cards():
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    reading = classify_scene(frame, 30.0)

    assert reading.scene == "other"


def _draw_grid(
    frame: np.ndarray,
    x: int,
    y: int,
    width: int,
    height: int,
) -> None:
    cv2.rectangle(frame, (x, y), (x + width, y + height), (220, 220, 220), 3)
    for offset in range(20, width, 28):
        cv2.line(frame, (x + offset, y), (x + offset, y + height), (180, 180, 180), 2)
    for offset in range(20, height, 28):
        cv2.line(frame, (x, y + offset), (x + width, y + offset), (180, 180, 180), 2)


if __name__ == "__main__":
    test_classify_in_game_from_hud_and_minimap_regions()
    test_classify_loading_from_dark_top_and_card_edges()
    test_classify_other_when_no_hud_or_loading_cards()
    print("Scene classifier tests passed!")
