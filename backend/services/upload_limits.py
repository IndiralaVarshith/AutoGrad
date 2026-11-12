"""The size limit on each uploaded file."""

from __future__ import annotations

from fastapi import HTTPException, UploadFile

import config


async def read_limited(upload: UploadFile) -> bytes:
    limit = int(config.MAX_UPLOAD_MB * 1024 * 1024)
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            413,
            f"'{upload.filename}' is over the {config.MAX_UPLOAD_MB:g} MB limit for one file. "
            "Scan it at a lower resolution.",
        )
    return data


_SIGNATURES = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
}


def actual_extension(data: bytes, claimed: str, filename: str) -> str:
    """The file type from what is inside the file, not from its name.

    A text file renamed to .pdf would otherwise be sent to Gemini as a PDF, and refused.
    """
    for extension, signatures in _SIGNATURES.items():
        # Some PDF writers put a few bytes before the %PDF- header.
        head = data[:1024] if extension == ".pdf" else data[:16]
        if any(signature in head if extension == ".pdf" else head.startswith(signature) for signature in signatures):
            return ".jpg" if extension == ".jpeg" else extension
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    if text.strip() and "\x00" not in text:
        return ".txt"
    raise HTTPException(
        400,
        f"'{filename}' is not really a {claimed.lstrip('.').upper() or 'supported'} file, so it cannot be "
        "read. Open it and save or export it again as a PDF, JPG, PNG or TXT file.",
    )
