"""A local copy of each uploaded sheet, so the review screen can always show its pages.

Deleting a sheet removes this copy and, outside the emulator, its Firebase Storage copy.

File names come from a hash, so an exam or student name can never reach another folder.
"""

from __future__ import annotations

import hashlib
import io
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


# ---------------------------------------------------------------------------
# A lasting copy of each page, kept in Firestore.
#
# A host such as Render's free plan wipes the local disk whenever the server sleeps,
# restarts or redeploys, and the project has no Storage bucket. Each page is kept as a
# JPEG in its own Firestore document, so the review screen can still show it later.
# ---------------------------------------------------------------------------

PAGES_COLLECTION = "sheet_pages"
# A Firestore document holds at most 1 MiB, with room left for the other fields.
_PAGE_LIMIT_BYTES = 900_000
_PAGE_LONG_SIDE = 1800


def _page_id(exam_id: str, hall_ticket: str, page_number: int) -> str:
    return f"{_base(exam_id, hall_ticket).name}_{page_number}"


def _page_images(data: bytes, extension: str) -> list:
    """Every page as a PIL image, in order."""
    from PIL import Image

    if extension == ".pdf":
        import fitz

        pages = []
        with fitz.open(stream=data, filetype="pdf") as document:
            for page in document:
                longest = max(page.rect.width, page.rect.height) or 1
                scale = min(_PAGE_LONG_SIDE / longest, 2.5)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                pages.append(Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples))
        return pages
    with Image.open(io.BytesIO(data)) as image:
        return [image.convert("RGB")]


def _as_jpeg(image) -> bytes:
    """The page as a JPEG that fits in one Firestore document."""
    image = image.copy()
    image.thumbnail((_PAGE_LONG_SIDE, _PAGE_LONG_SIDE))
    for quality in (80, 70, 60, 50, 40):
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        if output.tell() <= _PAGE_LIMIT_BYTES:
            return output.getvalue()
    image.thumbnail((image.width // 2, image.height // 2))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=50, optimize=True)
    return output.getvalue()


def save_pages(exam_id: str, hall_ticket: str, extension: str, data: bytes) -> int:
    """Keep every page in Firestore. Returns how many pages were saved; 0 without Firestore."""
    db = config.get_firestore()
    if db is None:
        return 0
    pages = _page_images(data, extension.lower())
    collection = db.collection(PAGES_COLLECTION)
    for number, image in enumerate(pages, start=1):
        collection.document(_page_id(exam_id, hall_ticket, number)).set({
            "exam_id": exam_id,
            "hall_ticket": hall_ticket,
            "page_number": number,
            "page_count": len(pages),
            "content_type": "image/jpeg",
            "data": _as_jpeg(image),
        })
    # A re-upload with fewer pages must not leave the old sheet's last pages behind.
    for extra in collection.where("exam_id", "==", exam_id).where("hall_ticket", "==", hall_ticket).stream():
        if int((extra.to_dict() or {}).get("page_number", 0)) > len(pages):
            extra.reference.delete()
    return len(pages)


def load_page(exam_id: str, hall_ticket: str, page_number: int) -> bytes | None:
    """The kept JPEG of one page, or None if there is none."""
    db = config.get_firestore()
    if db is None:
        return None
    doc = db.collection(PAGES_COLLECTION).document(_page_id(exam_id, hall_ticket, page_number)).get()
    if not doc.exists:
        return None
    data = (doc.to_dict() or {}).get("data")
    return bytes(data) if data else None


def delete_pages(exam_id: str, hall_ticket: str | None = None) -> int:
    """Delete the kept pages of one sheet, or of a whole exam. Returns how many went."""
    db = config.get_firestore()
    if db is None:
        return 0
    query = db.collection(PAGES_COLLECTION).where("exam_id", "==", exam_id)
    if hall_ticket is not None:
        query = query.where("hall_ticket", "==", hall_ticket)
    removed = 0
    for doc in query.stream():
        doc.reference.delete()
        removed += 1
    return removed


def delete_cloud_copies(exam_id: str, hall_ticket: str | None = None) -> int:
    """Delete the lasting copies of one sheet, or of a whole exam: the Firestore pages
    and the Firebase Storage files.

    The emulator never uses Storage. Returns how many went.
    """
    removed = delete_pages(exam_id, hall_ticket)
    bucket = config.get_storage_bucket()
    if bucket is None:
        return removed
    prefix = f"exams/{exam_id}/" + (f"sheets/{hall_ticket}/" if hall_ticket else "")
    blobs = list(bucket.list_blobs(prefix=prefix))
    for blob in blobs:
        blob.delete()
    return removed + len(blobs)
