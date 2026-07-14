from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from arl.vision_analysis.new_signal_ocr import read_match_result


class NewSignalOcrTests(TestCase):
    def test_match_result_skips_image_work_without_chinese_ocr_backend(self) -> None:
        with (
            patch(
                "arl.vision_analysis.new_signal_ocr._tesseract_chinese_available",
                return_value=False,
            ),
            patch(
                "arl.vision_analysis.new_signal_ocr._crop",
                side_effect=AssertionError("crop should not run"),
            ),
        ):
            result = read_match_result(object(), (560, 300, 800, 420))

        self.assertEqual(result, (None, 0.0))
