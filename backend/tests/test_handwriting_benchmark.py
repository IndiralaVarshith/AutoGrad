from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))

from benchmark_handwriting_sample import (  # noqa: E402
    _has_preinstalled_rapidocr_models,
    _reference_is_teacher_verified,
)
from services.ocr import resolve_rapidocr_model_root  # noqa: E402


class TestHandwritingBenchmarkReferenceGate(unittest.TestCase):
    def test_rapidocr_preflight_refuses_missing_local_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(_has_preinstalled_rapidocr_models(root))
            for filename in (
                "PP-OCRv6_det_small.onnx",
                "PP-OCRv6_rec_small.onnx",
                "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
            ):
                (root / filename).touch()
            self.assertTrue(_has_preinstalled_rapidocr_models(root))

    def test_rapidocr_resolver_uses_complete_bundled_weights_when_cache_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty_cache = root / "empty-cache"
            empty_cache.mkdir()
            bundled = root / "rapidocr" / "models"
            bundled.mkdir(parents=True)
            for filename in (
                "PP-OCRv6_det_small.onnx",
                "PP-OCRv6_rec_small.onnx",
                "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
            ):
                (bundled / filename).touch()
            selected = resolve_rapidocr_model_root(
                configured_root=empty_cache,
                bundled_root=bundled,
                cache_root=empty_cache,
            )
            self.assertEqual(selected, bundled.resolve())

    def test_current_candidate_reference_is_not_qualified_as_verified(self):
        fixture = json.loads(
            (BACKEND_DIR / "tests/fixtures/handwriting_ground_truth.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(_reference_is_teacher_verified(fixture))

    def test_every_segment_and_dataset_need_teacher_signoff(self):
        fixture = {
            "annotation_status": "TEACHER_VERIFIED",
            "verified_by": "reviewer-pseudonym",
            "verified_at": "2026-11-04T00:00:00Z",
            "segments": [
                {
                    "verification": {
                        "status": "TEACHER_VERIFIED",
                        "verified_by": "reviewer-pseudonym",
                        "verified_at": "2026-11-04T00:00:00Z",
                    }
                },
                {
                    "verification": {
                        "status": "TEACHER_VERIFIED",
                        "verified_by": "reviewer-pseudonym",
                        "verified_at": "2026-11-04T00:00:00Z",
                    }
                },
            ],
        }
        self.assertTrue(_reference_is_teacher_verified(fixture))
        incomplete = copy.deepcopy(fixture)
        incomplete["segments"][1]["verification"]["status"] = "UNVERIFIED_CANDIDATE"
        self.assertFalse(_reference_is_teacher_verified(incomplete))


if __name__ == "__main__":
    unittest.main()
