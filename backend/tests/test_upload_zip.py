"""Answer sheets uploaded together in one zip file."""

import io
import time
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from tests.conftest import signed_in_client
from tests.test_gemini_queue import RUBRIC, gemini, manager, pdf_bytes  # noqa: F401 (fixtures)

import config
from routes import upload as upload_route
from services import sheet_zip

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
       b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef"
       b"\x00\x00\x00\x00IEND\xaeB`\x82")


def make_zip(entries, password_flag=False):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            if name.endswith("/"):
                archive.writestr(name, "")
            else:
                archive.writestr(name, data)
    data = buffer.getvalue()
    if password_flag:
        # Mark every entry as encrypted, as a password-protected zip is.
        data = data.replace(b"PK\x03\x04\x14\x00\x00\x00", b"PK\x03\x04\x14\x00\x01\x00")
        data = data.replace(b"PK\x01\x02\x14\x03\x14\x00\x00\x00", b"PK\x01\x02\x14\x03\x14\x00\x01\x00")
    return data


def test_each_sheet_inside_is_named_by_its_file_and_extras_are_left_out():
    data = make_zip({
        "class10/": "",
        "class10/Student_1.pdf": pdf_bytes(),
        "class10/Student_2.PNG": PNG,
        "class10/notes.docx": b"word",
        "__MACOSX/class10/._Student_1.pdf": b"junk",
        ".DS_Store": b"junk",
    })
    contents = sheet_zip.read_sheets(data, "class10.zip")
    assert [(s.filename, s.extension) for s in contents.sheets] == [("Student_1.pdf", ".pdf"), ("Student_2.PNG", ".png")]
    assert contents.skipped == ["class10/notes.docx (not a PDF, JPG or PNG)"]


def test_two_sheets_with_one_name_keep_their_folder():
    contents = sheet_zip.read_sheets(make_zip({"A/Ravi.pdf": pdf_bytes(), "B/Ravi.pdf": pdf_bytes() + b"x"}), "x.zip")
    assert [s.filename for s in contents.sheets] == ["A_Ravi.pdf", "B_Ravi.pdf"]


@pytest.mark.parametrize("data, words", [
    (b"PK\x03\x04 not really a zip", "not a working zip"),
    (make_zip({"readme.txt": b"hi"}), "no PDF, JPG or PNG"),
])
def test_a_zip_with_no_usable_sheets_is_refused_clearly(data, words):
    with pytest.raises(HTTPException) as error:
        sheet_zip.read_sheets(data, "x.zip")
    assert error.value.status_code == 400 and words in error.value.detail


def test_password_protected_zip_is_refused():
    with pytest.raises(HTTPException) as error:
        sheet_zip.read_sheets(make_zip({"s.pdf": pdf_bytes()}, password_flag=True), "x.zip")
    assert "password" in error.value.detail


def test_too_many_sheets_or_too_big_unpacked_is_refused(monkeypatch):
    data = make_zip({f"s{i}.pdf": pdf_bytes() for i in range(3)})
    monkeypatch.setattr(config, "MAX_ZIP_SHEETS", 2)
    with pytest.raises(HTTPException, match="at most 2"):
        sheet_zip.read_sheets(data, "x.zip")
    monkeypatch.setattr(config, "MAX_ZIP_SHEETS", 300)
    monkeypatch.setattr(config, "MAX_ZIP_UNPACKED_MB", 0.0001)
    with pytest.raises(HTTPException, match="Split it"):
        sheet_zip.read_sheets(data, "x.zip")


def test_a_sheet_over_the_one_file_limit_is_left_out(monkeypatch):
    big = pdf_bytes() + b"0" * 4096
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0.002)
    contents = sheet_zip.read_sheets(make_zip({"big.pdf": big, "ok.png": PNG}), "x.zip")
    assert [s.filename for s in contents.sheets] == ["ok.png"]
    assert "limit" in contents.skipped[0]


def use_test_rubric(manager, monkeypatch):  # noqa: F811
    monkeypatch.setattr(manager, "start_worker", lambda worker: None)
    monkeypatch.setattr(
        upload_route, "_load_rubric_bundle",
        lambda exam: ([upload_route.QuestionRubric(**RUBRIC)], 2.0, "1", "print"),
    )


def test_zip_upload_queues_every_sheet_and_reports_what_was_left_out(manager, gemini, monkeypatch):  # noqa: F811
    use_test_rubric(manager, monkeypatch)
    client = signed_in_client()
    exam = f"z-{time.time_ns()}"
    data = make_zip({"Student_1.pdf": pdf_bytes() + b"1", "Student_2.png": PNG, "marks.xlsx": b"x"})
    body = client.post(
        "/upload",
        data={"exam_id": exam, "student_meta": '[{"hall_ticket": "the_zip", "name": ""}, {"hall_ticket": "HT4", "name": "Asha"}]'},
        files=[("files", ("class10.zip", data, "application/zip")),
               ("files", ("Student_4.pdf", pdf_bytes() + b"4", "application/pdf"))],
    ).json()
    assert body["queued"] == 3
    assert body["skipped"] == ["class10.zip: marks.xlsx (not a PDF, JPG or PNG)"]
    jobs = {j.hall_ticket: j for j in manager.get_exam_jobs(exam)}
    # The zip's own entry names no student; the file chosen after it keeps its roll number and name.
    assert set(jobs) == {"Student_1", "Student_2", "HT4"} and jobs["HT4"].student_name == "Asha"


def test_a_sheet_from_a_zip_is_queued_exactly_as_the_same_sheet_uploaded_alone(manager, gemini, monkeypatch):  # noqa: F811
    use_test_rubric(manager, monkeypatch)
    client = signed_in_client()
    sheet = pdf_bytes() + b"same sheet"
    alone, zipped = f"a-{time.time_ns()}", f"b-{time.time_ns()}"
    client.post("/upload", data={"exam_id": alone}, files=[("files", ("Ravi_101.pdf", sheet, "application/pdf"))])
    client.post("/upload", data={"exam_id": zipped},
                files=[("files", ("class.zip", make_zip({"class/Ravi_101.pdf": sheet}), "application/zip"))])
    (a,), (b,) = manager.get_exam_jobs(alone), manager.get_exam_jobs(zipped)
    same = ("hall_ticket", "student_name", "file_sha256", "file_ext", "mime_type", "original_filename",
            "rubric_version", "rubric_fingerprint", "rubric_total_marks", "rubric_data")
    assert {k: getattr(a, k) for k in same} == {k: getattr(b, k) for k in same}
    assert Path(a.file_path).read_bytes() == Path(b.file_path).read_bytes() == sheet


def test_a_damaged_sheet_in_a_zip_stops_the_upload_as_a_damaged_sheet_alone_does(manager, gemini, monkeypatch):  # noqa: F811
    use_test_rubric(manager, monkeypatch)
    client = signed_in_client()
    broken = b"%PDF-1.4 broken"
    alone = client.post("/upload", data={"exam_id": f"c-{time.time_ns()}"},
                        files=[("files", ("S.pdf", broken, "application/pdf"))])
    zipped = client.post("/upload", data={"exam_id": f"d-{time.time_ns()}"},
                         files=[("files", ("z.zip", make_zip({"S.pdf": broken}), "application/zip"))])
    assert alone.status_code == zipped.status_code == 400
    assert alone.json()["detail"] == zipped.json()["detail"]
