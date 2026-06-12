from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import numpy as np

from arl.vision.timer_ocr import read_timer


def test_read_timer_no_timer():
    """Test reading from a frame with no timer (lobby screen)."""
    black_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    reading = read_timer(black_frame, 10.0)

    assert reading.timestamp_seconds == 10.0
    assert reading.game_time_text is None
    assert reading.confidence < 0.5


def test_read_timer_out_of_bounds():
    """Test reading with crop region out of frame bounds."""
    small_frame = np.zeros((100, 100, 3), dtype=np.uint8)

    reading = read_timer(small_frame, 5.0, crop_region=(1770, 5, 150, 50))

    assert reading.timestamp_seconds == 5.0
    assert reading.game_time_text is None
    assert reading.confidence == 0.0


def test_parse_timer_logic():
    """Test the internal timer parsing logic."""
    from arl.vision.match_stitcher import _parse_timer

    assert _parse_timer("00:30") == 30.0
    assert _parse_timer("05:45") == 345.0
    assert _parse_timer("23:12") == 1392.0
    assert _parse_timer("invalid") == 0.0
    assert _parse_timer("") == 0.0


if __name__ == "__main__":
    test_read_timer_no_timer()
    test_read_timer_out_of_bounds()
    test_parse_timer_logic()
    print("All timer OCR tests passed!")
