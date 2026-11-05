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
import hashlib
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

# get_firestore imported lazily inside sync_to_firestore to avoid blocking
# Firebase SDK initialisation at import time during tests.


SPOOL_DIR = Path(tempfile.gettempdir()) / "autograde_spool"
QUEUE_FILE = Path(tempfile.gettempdir()) / "autograde_queue_state.json"
SPOOL_DIR.mkdir(parents=True, exist_ok=True)
_logger = logging.getLogger(__name__)
_TERMINAL_STATUSES = {"COMPLETE", "NEEDS_REVIEW", "FAILED", "TIMED_OUT", "CANCELLED"}


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
    file_sha256: str = ""
    request_key: str = ""
    original_filename: str = ""
    rubric_fingerprint: str = ""
    rubric_version: str = ""
    rubric_total_marks: float = 0.0
    rubric_data: list[dict[str, Any]] | None = None
    current_stage: str = "queued"
    stage_detail: str = ""
    attempt_count: int = 0
    max_attempts: int = 3
    cancel_requested: bool = False
    error_code: str = ""
    result: dict[str, Any] | None = None


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
            jobs = {jid: QueueJob(**job_dict) for jid, job_dict in data.items()}
            for job in jobs.values():
                if job.status == "PROCESSING":
                    job.status = "PENDING"
                    job.current_stage = "queued"
                    job.stage_detail = "Resuming after backend restart"
            return jobs
        except Exception as e:
            _logger.exception("Could not load persisted evaluation jobs: %s", e)
            return {}

    def _save_state(self):
        """Persist current queue state to disk and mirror to Firestore."""
        with self._lock:
            try:
                serializable = {jid: asdict(job) for jid, job in self._jobs.items()}
                QUEUE_FILE.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
            except Exception as e:
                _logger.exception("Could not persist evaluation jobs: %s", e)

    def sync_to_firestore(self, job: QueueJob):
        """Mirror job status to Firestore asynchronously in background (non-blocking)."""
        snapshot = QueueJob(**asdict(job))

        def _do_sync():
            try:
                from config import get_firestore  # lazy import — avoids Firebase init at module load
                db = get_firestore()
                if db:
                    db.collection("exams").document(snapshot.exam_id).collection("queue").document(snapshot.hall_ticket).set(
                        {
                            "hall_ticket": snapshot.hall_ticket,
                            "student_name": snapshot.student_name,
                            "status": snapshot.status,
                            "error": snapshot.error_message,
                            "error_code": snapshot.error_code,
                            "score": snapshot.score if snapshot.result is not None else None,
                            "max_score": snapshot.max_score if snapshot.result is not None else None,
                            "current_stage": snapshot.current_stage,
                            "updated_at": snapshot.updated_at,
                        },
                        merge=True,
                    )
            except Exception as e:
                _logger.warning("Queue status Firestore sync failed for %s: %s", snapshot.job_id, e)

        threading.Thread(target=_do_sync, daemon=True).start()

    def enqueue(
        self,
        exam_id: str,
        hall_ticket: str,
        student_name: str,
        file_bytes: bytes,
        file_ext: str,
        mime_type: str,
        *,
        request_key: str = "",
        original_filename: str = "",
        rubric_fingerprint: str = "",
        rubric_version: str = "",
        rubric_total_marks: float = 0.0,
        rubric_data: list[dict[str, Any]] | None = None,
    ) -> QueueJob:
        """Spool answer sheet to disk and register in the queue."""
        file_sha256 = hashlib.sha256(file_bytes).hexdigest()
        if not request_key:
            request_key = hashlib.sha256(
                f"{exam_id}\0{hall_ticket}\0{file_sha256}\0{rubric_fingerprint}".encode()
            ).hexdigest()

        now = time.time()
        with self._lock:
            for existing in self._jobs.values():
                if existing.request_key == request_key and existing.status != "CANCELLED":
                    return existing

            job_id = f"eval-{uuid.uuid4().hex}"
            exam_dir = hashlib.sha256(exam_id.encode()).hexdigest()[:20]
            exam_spool = SPOOL_DIR / exam_dir
            exam_spool.mkdir(parents=True, exist_ok=True)
            spool_path = exam_spool / f"{job_id}{file_ext}"
            spool_path.write_bytes(file_bytes)

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
                file_sha256=file_sha256,
                request_key=request_key,
                original_filename=original_filename,
                rubric_fingerprint=rubric_fingerprint,
                rubric_version=rubric_version,
                rubric_total_marks=rubric_total_marks,
                rubric_data=rubric_data,
            )
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
        status: str | None = None,
        error_message: str | None = None,
        score: float | None = None,
        max_score: float | None = None,
        *,
        current_stage: str | None = None,
        stage_detail: str | None = None,
        error_code: str | None = None,
        result: dict[str, Any] | None = None,
        clear_result: bool = False,
        sync: bool = True,
    ):
        updated_job = None
        with self._lock:
            updated_job = self._jobs.get(job_id)
            if updated_job:
                if status is not None:
                    updated_job.status = status
                if error_message is not None:
                    updated_job.error_message = error_message
                if score is not None:
                    updated_job.score = score
                if max_score is not None:
                    updated_job.max_score = max_score
                if current_stage is not None:
                    updated_job.current_stage = current_stage
                if stage_detail is not None:
                    updated_job.stage_detail = stage_detail
                if error_code is not None:
                    updated_job.error_code = error_code
                if result is not None:
                    updated_job.result = result
                if clear_result:
                    updated_job.result = None
                updated_job.updated_at = time.time()

        if updated_job:
            self._save_state()
            if sync:
                self.sync_to_firestore(updated_job)

    def update_job_stage(self, job_id: str, stage: str, detail: str = "") -> None:
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"Evaluation job {job_id} no longer exists.")
        if job.cancel_requested:
            raise JobCancelled(f"Evaluation job {job_id} was cancelled.")
        self.update_job_status(
            job_id,
            current_stage=stage,
            stage_detail=detail,
            sync=False,
        )

    def request_cancel(self, job_id: str) -> QueueJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status in _TERMINAL_STATUSES:
                return job
            if job.status == "PENDING":
                job.status = "CANCELLED"
                job.current_stage = "cancelled"
            else:
                job.cancel_requested = True
                job.current_stage = "cancel_requested"
                job.stage_detail = "Cancellation will take effect after the current model request returns"
            job.updated_at = time.time()
        self._save_state()
        self.sync_to_firestore(job)
        return job

    def retry_job(self, job_id: str) -> QueueJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status not in {"FAILED", "TIMED_OUT"}:
                raise ValueError(f"Only failed or timed-out jobs can be retried (status={job.status}).")
            if job.attempt_count >= job.max_attempts:
                raise ValueError(f"Retry limit reached ({job.max_attempts} total attempts).")
            if not Path(job.file_path).is_file():
                raise FileNotFoundError("The uploaded answer sheet is no longer available for retry.")
            job.status = "PENDING"
            job.current_stage = "queued"
            job.stage_detail = "Retry queued; completed OCR/preprocessing stages may be reused"
            job.error_message = ""
            job.error_code = ""
            job.cancel_requested = False
            job.result = None
            job.updated_at = time.time()
        self._save_state()
        self.sync_to_firestore(job)
        return job

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

            if next_job.cancel_requested:
                self.update_job_status(
                    next_job.job_id,
                    "CANCELLED",
                    current_stage="cancelled",
                    stage_detail="Cancelled before processing started",
                )
                continue

            # Process job
            _logger.info("Starting evaluation job %s for %s", next_job.job_id, next_job.hall_ticket)
            self.update_job_status(
                next_job.job_id,
                "PROCESSING",
                current_stage="extracting_pages",
                stage_detail="Preparing the answer-sheet pages",
            )
            with self._lock:
                current = self._jobs.get(next_job.job_id)
                if current:
                    current.attempt_count += 1
                    current.updated_at = time.time()
            self._save_state()

            try:
                if self._processor_callback:
                    outcome = self._processor_callback(next_job)
                    if hasattr(outcome, "model_dump"):
                        result = outcome.model_dump()
                        self.update_job_status(
                            next_job.job_id,
                            outcome.status,
                            score=outcome.total_score,
                            max_score=outcome.max_total,
                            current_stage="completed",
                            stage_detail="Evaluation result is ready",
                            result=result,
                        )
                    elif isinstance(outcome, tuple) and len(outcome) == 3:
                        result_status, score, max_score = outcome
                        self.update_job_status(
                            next_job.job_id,
                            result_status,
                            score=score,
                            max_score=max_score,
                            current_stage="completed",
                            stage_detail="Evaluation result is ready",
                        )
                    else:
                        raise TypeError("Evaluation worker returned an unsupported result.")
                else:
                    raise RuntimeError("No evaluation worker is configured.")
            except JobCancelled as e:
                self.update_job_status(
                    next_job.job_id,
                    "CANCELLED",
                    error_message=str(e),
                    error_code="CANCELLED",
                    current_stage="cancelled",
                )
            except Exception as e:
                teacher_review_required = bool(
                    getattr(e, "requires_teacher_validation", False)
                    or getattr(e, "teacher_validation_required", False)
                )
                is_timeout = isinstance(e, TimeoutError) or "timed out" in str(e).lower() or "timeout" in str(e).lower()
                status = (
                    "NEEDS_REVIEW" if teacher_review_required
                    else "TIMED_OUT" if is_timeout
                    else "FAILED"
                )
                error_code = (
                    "MODEL_REVIEW_REQUIRED" if teacher_review_required
                    else "MODEL_TIMEOUT" if is_timeout
                    else type(e).__name__
                )
                _logger.exception("Evaluation job %s ended with %s", next_job.job_id, status)
                self.update_job_status(
                    next_job.job_id,
                    status,
                    error_message=str(e),
                    error_code=error_code,
                    current_stage="teacher_validation" if teacher_review_required else "failed",
                    stage_detail=(
                        "No model met the task requirements; teacher validation is required"
                        if teacher_review_required
                        else "Evaluation stopped before producing a complete result"
                    ),
                )

            # Small pause between papers to let unified memory settle
            time.sleep(0.5)


queue_manager = BatchQueueManager()


class JobCancelled(Exception):
    """Raised between evaluation stages when a cancellation was requested."""
