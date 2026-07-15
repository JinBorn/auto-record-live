from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from arl.vision.kda_ocr import _real_font_templates, read_kda

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kda_lol_zh_1080p"

# Human-verified 85x32 HUD crops from a real 1080p recording
# (session-20260617073649-4b5ec478). Truth labels were confirmed frame by
# frame during the 2026-07-15 false-kill investigation.
REAL_FRAME_TRUTH = [
    ("t3150.png", 0, 0, 0),
    ("t3400.png", 1, 0, 0),
    ("t3760.png", 3, 1, 1),
    ("t3900.png", 4, 1, 2),
    ("t4000.png", 5, 1, 2),
    ("t4100.png", 6, 2, 2),
    ("t4136.png", 7, 2, 2),
    ("t4144.png", 8, 2, 2),
    ("t4200.png", 9, 2, 2),
    ("t4120.png", 6, 2, 2),
    ("t4130.png", 6, 2, 2),
]


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


def _frame_with_fixture(name: str) -> np.ndarray:
    crop = cv2.imread(str(FIXTURE_DIR / name))
    assert crop is not None, f"missing fixture {name}"
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    height, width = crop.shape[:2]
    frame[0:height, 1665 : 1665 + width] = crop
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


def test_real_font_templates_are_loaded() -> None:
    templates = _real_font_templates()

    assert len(templates) == 11
    assert sorted({char for char, _ in templates}) == sorted("0123456789/")


@pytest.mark.parametrize(("name", "kills", "deaths", "assists"), REAL_FRAME_TRUTH)
def test_read_kda_real_hud_frames(
    name: str, kills: int, deaths: int, assists: int
) -> None:
    reading = read_kda(_frame_with_fixture(name), 0.0)

    assert (reading.kills, reading.deaths, reading.assists) == (kills, deaths, assists)
    # Real-font templates should match far above the Hershey-era ~0.82 ceiling.
    assert reading.confidence >= 0.8


def test_read_kda_rejects_ambiguous_degraded_glyph() -> None:
    # t4132: true scoreboard is 6/2/2 but the "6" gap is smeared shut by
    # compression; template scores tie within 0.004 of "8". The reading must
    # be rejected (missing), never parsed as a different KDA value.
    reading = read_kda(_frame_with_fixture("t4132.png"), 0.0)

    assert reading.kills is None
    assert reading.deaths is None
    assert reading.assists is None
