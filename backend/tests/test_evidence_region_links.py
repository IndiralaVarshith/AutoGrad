from __future__ import annotations

import unittest

from models.schemas import CriterionResult, OCRRegion
from services.evidence_verifier import verify_evidence


class TestEvidenceRegionLinks(unittest.TestCase):
    def test_verified_quote_links_to_matching_source_region(self):
        criterion = CriterionResult(
            criterion="Correct explanation",
            verbatim_quote="the system controls the transfer of energy",
            marks_awarded=1.0,
        )
        region = OCRRegion(
            region_id="page-2-r-7",
            page_number=2,
            bbox=[0.1, 0.2, 0.8, 0.3],
            extracted_text="The system controls the transfer of energy.",
        )

        updated, _ = verify_evidence(
            [criterion],
            "The system controls the transfer of energy.",
            ocr_regions=[region],
        )

        self.assertTrue(updated[0].quote_verified)
        self.assertEqual(updated[0].evidence_region_ids, ["page-2-r-7"])

    def test_short_quote_is_not_given_a_speculative_region_link(self):
        criterion = CriterionResult(
            criterion="Correct term",
            verbatim_quote="osmosis",
            marks_awarded=1.0,
        )
        region = OCRRegion(
            region_id="page-1-r-1",
            page_number=1,
            extracted_text="osmosis",
        )

        updated, _ = verify_evidence(
            [criterion],
            "osmosis",
            ocr_regions=[region],
        )

        self.assertTrue(updated[0].quote_verified)
        self.assertEqual(updated[0].evidence_region_ids, [])


if __name__ == "__main__":
    unittest.main()
