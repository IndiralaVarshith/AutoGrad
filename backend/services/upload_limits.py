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
