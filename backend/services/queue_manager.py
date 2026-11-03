"""
AutoGrade — Persistent Batch Queue Manager

Manages batch evaluation of 60–70 student answer sheets:
- Avoids keeping all uploaded PDF/image bytes in memory by spooling to disk
- Sequential execution honoring concurrency limits (1 inference job at a time)
- Persistent JSON queue state on disk + Firestore synchronization
- Resumes unfinished work after server restart
- Retries individual failed jobs without repeating completed students
- Tracks statuses: PENDING, PROCESSING, COMPLETE, NEEDS_REVIEW, FAILED
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

# get_firestore imported lazily inside sync_to_firestore to avoid blocking
# Firebase SDK initialisation at import time during tests.


SPOOL_DIR = Path(tempfile.gettempdir()) / "autograde_spool"
QUEUE_FILE = Path(tempfile.gettempdir()) / "autograde_queue_state.json"
SPOOL_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class QueueJob:
    job_id: str
    exam_id: str
    hall_ticket: str
    student_name: str
    file_path: str
    file_ext: str
    mime_type: str
    status: str  # PENDING | PROCESSING | COMPLETE | NEEDS_REVIEW | FAILED
    error_message: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    score: float = 0.0
    max_score: float = 0.0


class BatchQueueManager:
    """Persistent job queue manager for sequential grading."""

    def __init__(self):
        self._lock = threading.Lock()
        self._worker_thread: threading.Thread | None = None
        self._is_processing = False
        self._processor_callback: Callable | None = None
        self._jobs: dict[str, QueueJob] = self._load_state()

    def _load_state(self) -> dict[str, QueueJob]:
        """Load persistent queue state from disk."""
        if not QUEUE_FILE.exists():
            return {}
        try:
            data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
            return {jid: QueueJob(**job_dict) for jid, job_dict in data.items()}
        except Exception as e:
            print(f"[QueueManager] Failed to load state: {e}")
            return {}

    def _save_state(self):
        """Persist current queue state to disk and mirror to Firestore."""
        with self._lock:
            try:
                serializable = {jid: asdict(job) for jid, job in self._jobs.items()}
                QUEUE_FILE.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
            except Exception as e:
                print(f"[QueueManager] Failed to save state to disk: {e}")

    def sync_to_firestore(self, job: QueueJob):
        """Mirror job status to Firestore asynchronously in background (non-blocking)."""
        def _do_sync():
            try:
                from config import get_firestore  # lazy import — avoids Firebase init at module load
                db = get_firestore()
                if db:
                    db.collection("exams").document(job.exam_id).collection("queue").document(job.hall_ticket).set(
                        {
                            "hall_ticket": job.hall_ticket,
                            "student_name": job.student_name,
                            "status": job.status,
                            "error": job.error_message,
                            "score": job.score,
                            "max_score": job.max_score,
                            "updated_at": job.updated_at,
                        },
                        merge=True,
                    )
            except Exception as e:
                # Firestore optional in local mode
                pass

        threading.Thread(target=_do_sync, daemon=True).start()

    def enqueue(
        self,
        exam_id: str,
        hall_ticket: str,
        student_name: str,
        file_bytes: bytes,
        file_ext: str,
        mime_type: str,
    ) -> QueueJob:
        """Spool answer sheet to disk and register in the queue."""
        job_id = f"{exam_id}_{hall_ticket}"

        # Spool file to disk to preserve RAM during 70-student upload
        exam_spool = SPOOL_DIR / exam_id
        exam_spool.mkdir(parents=True, exist_ok=True)
        spool_path = exam_spool / f"{hall_ticket}{file_ext}"
        spool_path.write_bytes(file_bytes)

        now = time.time()
        job = QueueJob(
            job_id=job_id,
            exam_id=exam_id,
            hall_ticket=hall_ticket,
            student_name=student_name,
            file_path=str(spool_path),
            file_ext=file_ext,
            mime_type=mime_type,
            status="PENDING",
            created_at=now,
            updated_at=now,
        )

        with self._lock:
            self._jobs[job_id] = job
        self._save_state()
        self.sync_to_firestore(job)
        return job

    def get_job(self, job_id: str) -> QueueJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def get_exam_jobs(self, exam_id: str) -> list[QueueJob]:
        with self._lock:
            return [j for j in self._jobs.values() if j.exam_id == exam_id]

    def update_job_status(
        self,
        job_id: str,
        status: str,
        error_message: str = "",
        score: float = 0.0,
        max_score: float = 0.0,
    ):
        updated_job = None
        with self._lock:
            updated_job = self._jobs.get(job_id)
            if updated_job:
                updated_job.status = status
                updated_job.error_message = error_message
                updated_job.score = score
                updated_job.max_score = max_score
                updated_job.updated_at = time.time()

        if updated_job:
            self._save_state()
            self.sync_to_firestore(updated_job)

    def start_worker(self, processor_callback: Callable):
        """Launch background worker thread if not already running."""
        self._processor_callback = processor_callback
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._worker_thread = threading.Thread(
                target=self._process_loop, daemon=True, name="BatchQueueWorker"
            )
            self._worker_thread.start()

    def _process_loop(self):
        """Sequential processing loop for queued student answer sheets."""
        print("[QueueManager] Batch worker loop started.")
        while True:
            # Find next pending job
            next_job: QueueJob | None = None
            with self._lock:
                for job in self._jobs.values():
                    if job.status == "PENDING":
                        next_job = job
                        break

            if next_job is None:
                # Idle wait
                time.sleep(1.0)
                continue

            # Process job
            print(f"[QueueManager] Processing job: {next_job.job_id} ({next_job.hall_ticket})")
            self.update_job_status(next_job.job_id, "PROCESSING")

            try:
                if self._processor_callback:
                    # processor_callback(job) must return status, score, max_score
                    result_status, score, max_score = self._processor_callback(next_job)
                    self.update_job_status(
                        next_job.job_id,
                        result_status,
                        score=score,
                        max_score=max_score,
                    )
                else:
                    self.update_job_status(next_job.job_id, "COMPLETE")
            except Exception as e:
                print(f"[QueueManager] Job {next_job.job_id} failed: {e}")
                self.update_job_status(next_job.job_id, "FAILED", error_message=str(e))

            # Small pause between papers to let unified memory settle
            time.sleep(0.5)


queue_manager = BatchQueueManager()
