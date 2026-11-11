"""A local copy of each uploaded sheet, so the review screen can always show its pages.

File names come from a hash, so an exam or student name can never reach another folder.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import config


def _base(exam_id: str, hall_ticket: str) -> Path:
    name = hashlib.sha256(f"{exam_id}\0{hall_ticket}".encode()).hexdigest()[:32]
    return Path(config.QUEUE_DATA_DIR) / "sheets" / name


def save(exam_id: str, hall_ticket: str, extension: str, data: bytes) -> None:
    base = _base(exam_id, hall_ticket)
    base.parent.mkdir(parents=True, exist_ok=True)
    (base.parent / f"{base.name}{extension}").write_bytes(data)


def load(exam_id: str, hall_ticket: str) -> tuple[bytes, str] | None:
    """Return (file bytes, extension), or None if no copy is kept here."""
    base = _base(exam_id, hall_ticket)
    for extension in (".pdf", ".png", ".jpg", ".jpeg"):
        path = base.parent / f"{base.name}{extension}"
        if path.is_file():
            return path.read_bytes(), extension
    return None


def delete(exam_id: str, hall_ticket: str) -> None:
    base = _base(exam_id, hall_ticket)
    for extension in (".pdf", ".png", ".jpg", ".jpeg"):
        (base.parent / f"{base.name}{extension}").unlink(missing_ok=True)
