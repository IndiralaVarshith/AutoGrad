"""The file type comes from what is inside the file, not from its name."""

import pytest
from fastapi import HTTPException

from services.upload_limits import actual_extension


def test_text_named_pdf_is_read_as_text():
    assert actual_extension(b"Data Science Examination\n1. Which ...", ".pdf", "Question.pdf") == ".txt"


def test_real_files_keep_their_type():
    assert actual_extension(b"%PDF-1.7\n...", ".pdf", "a.pdf") == ".pdf"
    assert actual_extension(b"\x89PNG\r\n\x1a\n....", ".png", "a.png") == ".png"
    assert actual_extension(b"\xff\xd8\xff\xe0....", ".jpeg", "a.jpeg") == ".jpg"


def test_photo_named_pdf_is_sent_as_a_photo():
    assert actual_extension(b"\xff\xd8\xff\xe0....", ".pdf", "scan.pdf") == ".jpg"


def test_unknown_binary_gets_a_clear_message():
    with pytest.raises(HTTPException) as error:
        actual_extension(b"PK\x03\x04\x00\x00\xff\xfe binary", ".pdf", "paper.pdf")
    assert error.value.status_code == 400
    assert "not really a PDF" in error.value.detail
