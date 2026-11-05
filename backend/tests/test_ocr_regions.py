from __future__ import annotations

import base64
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

import services.ocr as ocr_module


def _png_bytes(color: str = "#fafafa", size: tuple[int, int] = (100, 100)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeRapidOCR:
    def __call__(self, image_bytes):
        return SimpleNamespace(
            txts=("Q3.", "faint word"),
            scores=(0.95, 0.42),
            boxes=(
                ((10, 20), (90, 20), (90, 40), (10, 40)),
                ((-5, 40), (60, 40), (60, 70), (-5, 70)),
            ),
        )


class TestPageRegionOCR(unittest.TestCase):
    def test_regions_preserve_page_boxes_without_fabricating_calibrated_classes(self):
        image = base64.b64encode(_png_bytes()).decode("ascii")
        with patch.object(ocr_module, "_get_rapidocr_engine", return_value=_FakeRapidOCR()):
            pages = ocr_module.extract_page_regions([image], source_hash="")

        self.assertEqual(len(pages), 1)
        page = pages[0]
        self.assertEqual(page.page_number, 1)
        self.assertEqual(page.status, "PROCESSED")
        self.assertEqual(page.width, 100)
        self.assertEqual(page.height, 100)
        self.assertEqual(page.regions[0].question_number_candidate, 3)
        self.assertEqual(page.regions[0].bbox, [0.1, 0.2, 0.9, 0.4])
        self.assertEqual(page.regions[0].uncertainty_class, "UNCALIBRATED")
        self.assertIn("not calibrated", page.regions[0].uncertainty_reason)
        self.assertEqual(page.regions[1].bbox, [0.0, 0.4, 0.6, 0.7])
        self.assertEqual(page.regions[1].uncertainty_class, "UNCALIBRATED")
        self.assertIn("raw score is low", page.regions[1].uncertainty_reason)

    def test_blank_page_is_recorded_without_loading_ocr_models(self):
        image = base64.b64encode(
            _png_bytes(color="white", size=(1200, 1600))
        ).decode("ascii")
        with patch.object(
            ocr_module,
            "_get_rapidocr_engine",
            side_effect=AssertionError("blank pages should not load OCR models"),
        ):
            pages = ocr_module.extract_page_regions([image])

        self.assertEqual(pages[0].status, "BLANK_CANDIDATE")
        self.assertTrue(pages[0].blank_candidate)
        self.assertIn("POSSIBLY_BLANK", pages[0].quality_flags)

    def test_uncalibrated_provider_confidence_is_not_promoted_to_probability(self):
        response = SimpleNamespace(candidates=[])
        self.assertEqual(ocr_module._compute_c_ocr(response, 0, 1), 0.0)


if __name__ == "__main__":
    unittest.main()
