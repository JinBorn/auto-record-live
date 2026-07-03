from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import cv2
import numpy as np

from arl.vision.scene_classifier import classify_scene, looks_like_death_screen


def test_classify_in_game_from_hud_and_minimap_regions():
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    _draw_grid(frame, 1620, 720, 290, 330)
    _draw_grid(frame, 600, 850, 780, 210)

    reading = classify_scene(frame, 10.0)

    assert reading.scene == "in_game"
    assert reading.confidence > 0.7


def test_classify_loading_from_dark_top_and_card_edges():
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    # Splash-art cards in the center region (well above the HUD area
    # at y ≥ 78 %).  Real loading screens never draw cards over the
    # ability-bar row, and the classifier now enforces that.
    for y in (180, 400):
        for index in range(5):
            left = 230 + index * 320
            # Larger cards with thicker borders and more internal detail
            # to reliably exceed the center_edges threshold.
            cv2.rectangle(frame, (left, y), (left + 280, y + 230), (230, 230, 230), 4)
            cv2.rectangle(frame, (left + 30, y + 30), (left + 250, y + 200), (190, 190, 190), 2)
            for offset in range(30, 250, 30):
                cv2.line(frame, (left + offset, y + 35), (left + offset, y + 195), (170, 170, 170), 1)
            for offset in range(35, 195, 30):
                cv2.line(frame, (left + 35, y + offset), (left + 245, y + offset), (170, 170, 170), 1)

    reading = classify_scene(frame, 20.0)

    assert reading.scene == "loading"
    assert reading.confidence > 0.7


def test_classify_other_when_no_hud_or_loading_cards():
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    reading = classify_scene(frame, 30.0)

    assert reading.scene == "other"


def test_death_screen_not_classified_as_loading():
    """Death/respawn screen with visible ability bar is not 'loading'.

    During death the screen dims (dark top), the death-recap panel adds
    center edges, and the minimap area is sparse — all of which can
    match the loading profile.  But the ability bar (HUD, bottom 22 %)
    is still visible with cooldown digits, producing edges that the
    loading check now rejects.
    """
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    # Dark top region (dimmed by death overlay).
    frame[0:75, 1500:1920] = 20
    # Death-recap panel in center (creates edge density).
    cv2.rectangle(frame, (600, 300), (1300, 670), (80, 80, 80), 3)
    cv2.rectangle(frame, (630, 340), (1270, 630), (60, 60, 60), 2)
    cv2.line(frame, (650, 380), (1250, 380), (100, 100, 100), 2)
    cv2.line(frame, (650, 420), (1250, 420), (100, 100, 100), 2)
    # Visible ability bar — this is the key difference from a loading screen.
    _draw_grid(frame, 600, 860, 780, 200)

    reading = classify_scene(frame, 40.0)

    assert reading.scene != "loading", (
        f"Death screen with visible HUD misclassified as {reading.scene}"
    )
    assert looks_like_death_screen(frame)


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
    test_death_screen_not_classified_as_loading()
    print("Scene classifier tests passed!")
