"""Answer sheets sent together in one zip file.

Each PDF, JPG or PNG inside the zip is one student's sheet, named by its file name, the same as
a sheet uploaded on its own. Folders inside the zip are allowed. Anything else in the zip (Mac
"__MACOSX" folders, hidden files, Word files, zips inside the zip) is left out and listed, so the
teacher can see what was not marked.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from fastapi import HTTPException

import config

SHEET_TYPES = {".pdf", ".jpg", ".jpeg", ".png"}


@dataclass
class ZipSheet:
    filename: str  # the name the sheet is known by: its own file name, with its folder if two share a name
    data: bytes
    extension: str


@dataclass
class ZipContents:
    sheets: list[ZipSheet] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def is_zip(filename: str, data: bytes) -> bool:
    return filename.lower().endswith(".zip") or data[:4] == b"PK\x03\x04"


def read_sheets(data: bytes, zip_name: str) -> ZipContents:
    """The answer sheets inside a zip file. Raises a 400 the teacher can act on when it cannot be used."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise HTTPException(400, f"'{zip_name}' is not a working zip file. Make the zip again and upload it.") from error

    per_file = int(config.MAX_UPLOAD_MB * 1024 * 1024)
    total_limit = int(config.MAX_ZIP_UNPACKED_MB * 1024 * 1024)
    contents = ZipContents()
    entries = []
    with archive:
        for info in archive.infolist():
            path = PurePosixPath(info.filename.replace("\\", "/"))
            if info.is_dir():
                continue
            if any(part.startswith(".") or part == "__MACOSX" for part in path.parts):
                continue  # Mac and system extras, never a student's sheet
            extension = path.suffix.lower()
            if extension not in SHEET_TYPES:
                contents.skipped.append(f"{path.as_posix()} (not a PDF, JPG or PNG)")
                continue
            if info.flag_bits & 0x1:
                raise HTTPException(
                    400, f"'{zip_name}' is password-protected. Make the zip again without a password.")
            if info.file_size > per_file:
                contents.skipped.append(f"{path.as_posix()} (over the {config.MAX_UPLOAD_MB:g} MB limit for one sheet)")
                continue
            entries.append((info, path, extension))

        if len(entries) > config.MAX_ZIP_SHEETS:
            raise HTTPException(
                400, f"'{zip_name}' holds {len(entries)} sheets. Upload at most {config.MAX_ZIP_SHEETS} at a time.")
        if sum(info.file_size for info, _, _ in entries) > total_limit:
            raise HTTPException(
                400, f"'{zip_name}' unpacks to more than {config.MAX_ZIP_UNPACKED_MB:g} MB. Split it into smaller zips.")

        # Two sheets with the same name in different folders keep their folder name, so neither is lost.
        stems: dict[str, int] = {}
        for _, path, _ in entries:
            stems[path.stem.lower()] = stems.get(path.stem.lower(), 0) + 1
        for info, path, extension in entries:
            try:
                with archive.open(info) as handle:
                    body = handle.read(per_file + 1)  # the size in the zip header can lie
            except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as error:
                contents.skipped.append(f"{path.as_posix()} (could not be unpacked: {error})")
                continue
            if len(body) > per_file:
                contents.skipped.append(f"{path.as_posix()} (over the {config.MAX_UPLOAD_MB:g} MB limit for one sheet)")
                continue
            if not body:
                contents.skipped.append(f"{path.as_posix()} (empty)")
                continue
            name = path.name
            if stems[path.stem.lower()] > 1 and len(path.parts) > 1:
                name = "_".join(path.parts[-2:])
            contents.sheets.append(ZipSheet(name, body, ".jpg" if extension == ".jpeg" else extension))

    if not contents.sheets:
        raise HTTPException(400, f"'{zip_name}' has no PDF, JPG or PNG answer sheets inside.")
    return contents
