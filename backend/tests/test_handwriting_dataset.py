from __future__ import annotations

import unittest
from unittest.mock import patch

from services.handwriting_dataset import save_teacher_validated_candidate


class _Doc:
    def __init__(self):
        self.data = None

    def create(self, data):
        if self.data is not None:
            raise RuntimeError("already exists")
        self.data = data

    def get(self):
        return type("Snapshot", (), {"exists": self.data is not None})()


class _Collection:
    def __init__(self):
        self.docs = {}

    def document(self, key):
        return self.docs.setdefault(key, _Doc())


class _DB:
    def __init__(self):
        self.collection_ref = _Collection()

    def collection(self, _name):
        return self.collection_ref


class TestHandwritingDataset(unittest.TestCase):
    def setUp(self):
        self.db = _DB()
        self.page = {"page_number": 2, "image_sha256": "page-hash"}
        self.region = {
            "region_id": "region-1",
            "bbox": [0.1, 0.2, 0.6, 0.3],
            "extracted_text": "orig OCR",
            "engine": "RapidOCR",
            "model_version": "3.9.2",
            "validated_at": "2026-11-04T00:00:00+00:00",
        }
        self.env = patch.dict("os.environ", {
            "HANDWRITING_DATASET_ENABLED": "true",
            "HANDWRITING_DATASET_ACCESS_POLICY_CONFIRMED": "true",
            "HANDWRITING_DATASET_TTL_ENFORCED": "true",
            "HANDWRITING_DATASET_RETENTION_DAYS": "30",
            "HANDWRITING_DATASET_SALT": "a" * 48,
            "HANDWRITING_DATASET_VERSION": "test-v1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def _save(self, **kwargs):
        values = {
            "db": self.db,
            "exam_id": "exam-1",
            "hall_ticket": "student-secret-id",
            "source_file_reference": "private/source.pdf",
            "page": self.page,
            "region": self.region,
            "action": "CORRECT",
            "corrected_text": "teacher verified",
            "question_number": 4,
            "sub_part": "b",
        }
        values.update(kwargs)
        return save_teacher_validated_candidate(**values)

    def test_candidate_preserves_original_and_corrected_text_without_student_id(self):
        result = self._save()
        self.assertEqual(result["status"], "CREATED")
        payload = self.db.collection_ref.docs[result["candidate_id"]].data
        self.assertEqual(payload["original_ocr_transcription"], "orig OCR")
        self.assertEqual(payload["teacher_corrected_transcription"], "teacher verified")
        self.assertEqual(payload["source_file_reference"], "private/source.pdf")
        self.assertEqual(payload["page_number"], 2)
        self.assertEqual(payload["question_number"], 4)
        self.assertNotIn("student-secret-id", str(payload))
        self.assertEqual(payload["dataset_version"], "test-v1")
        self.assertEqual(payload["content_type"], "UNKNOWN")
        self.assertEqual(payload["language_or_script"], "unknown")
        self.assertIn(payload["writer_split"], {"training", "evaluation"})

    def test_candidate_creation_is_idempotent(self):
        first = self._save()
        second = self._save()
        self.assertEqual(first["candidate_id"], second["candidate_id"])
        self.assertEqual(second["status"], "ALREADY_EXISTS")

    def test_unreadable_content_never_enters_positive_dataset(self):
        result = self._save(action="UNREADABLE")
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "not_readable_validation")

    def test_dataset_requires_private_access_confirmation(self):
        with patch.dict("os.environ", {"HANDWRITING_DATASET_ACCESS_POLICY_CONFIRMED": "false"}):
            result = self._save()
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "private_access_policy_not_confirmed")

    def test_dataset_requires_expiring_ttl_policy(self):
        with patch.dict("os.environ", {"HANDWRITING_DATASET_TTL_ENFORCED": "false"}):
            result = self._save()
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "ttl_enforcement_not_confirmed")


if __name__ == "__main__":
    unittest.main()
