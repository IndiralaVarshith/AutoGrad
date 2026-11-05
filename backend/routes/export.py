"""
AutoGrade — Export Routes

GET /export/{exam_id} — Generates Excel (.xlsx) report using openpyxl
Columns: Hall Ticket | Name | Q1 | Q2 ... | Total | Max Total | Flagged Qs | Avg Confidence | Status
"""

from __future__ import annotations

import io
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from config import get_firestore
from services.marks_validator import safe_int_qnum


router = APIRouter(tags=["Export"])


# =========================================================
# GET /export/{exam_id}
# =========================================================

@router.get("/export/{exam_id}")
async def export_exam_results(exam_id: str, include_not_final: bool = False):
    """
    Generate and stream an Excel (.xlsx) file containing all student results.
    Dynamically identifies all questions and builds tabular columns.
    """
    db = get_firestore()
    if db is None:
        raise HTTPException(status_code=503, detail="Firestore is unavailable.")

    try:
        docs = (
            db.collection("exams")
            .document(exam_id)
            .collection("results")
            .stream()
        )
        papers = [doc.to_dict() for doc in docs]
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Failed to fetch results: {error}")

    if not papers:
        raise HTTPException(status_code=404, detail=f"No results found for exam {exam_id}.")

    not_final = [
        str(paper.get("hall_ticket", "")) for paper in papers if paper.get("finality_status") != "FINAL"
    ]
    if not_final and not include_not_final:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"{len(not_final)} of {len(papers)} sheets are not final. A teacher must "
                    "approve them first. You can download a copy clearly marked NOT FINAL."
                ),
                "not_final": not_final,
            },
        )

    # One column per question and sub-part. Sub-parts must not overwrite each other.
    question_keys = set()
    for paper in papers:
        for q in paper.get("questions", []):
            if "question_number" in q:
                question_keys.add(
                    (safe_int_qnum(q["question_number"], 0), str(q.get("sub_part") or "").upper())
                )
    sorted_q_keys = sorted(question_keys)

    # Initialize workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "NOT FINAL" if not_final else "Final marks"

    # Header styling
    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    thin_border = Border(
        left=Side(style="thin", color="D1D5DB"),
        right=Side(style="thin", color="D1D5DB"),
        top=Side(style="thin", color="D1D5DB"),
        bottom=Side(style="thin", color="D1D5DB"),
    )

    # Build Header Row
    headers = ["Hall Ticket", "Name"]
    for q_num, sub_part in sorted_q_keys:
        headers.append(f"Q{q_num}{sub_part}")
    headers.extend(["Total", "Max Total", "Flagged Qs", "Avg Confidence", "Status", "Final?"])

    ws.append(headers)

    # Style Header
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    # Status fills
    status_fills = {
        "COMPLETE": PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
        "NEEDS_REVIEW": PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
        "PROCESSING": PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid"),
    }

    # Data Rows
    for row_idx, paper in enumerate(papers, start=2):
        hall_ticket = paper.get("hall_ticket", "")
        name = paper.get("student_name", "")
        total_score = paper.get("total_score", 0.0)
        max_total = paper.get("max_total", 0.0)
        flagged_count = paper.get("flagged_count", 0)
        avg_confidence = paper.get("avg_confidence", 0.0)
        status = paper.get("status", "COMPLETE")

        # Map scores per question
        q_map = {}
        for q in paper.get("questions", []):
            routing = q.get("routing")
            key = (safe_int_qnum(q.get("question_number", 0), 0), str(q.get("sub_part") or "").upper())
            q_map[key] = (
                "Needs review" if routing in ("FLAGGED", "MANUAL")
                else "Not attempted" if routing == "NOT_ATTEMPTED"
                else q.get("final_score", 0.0)
            )
        is_final = paper.get("finality_status") == "FINAL"

        row_data = [hall_ticket, name]
        for key in sorted_q_keys:
            row_data.append(q_map.get(key, "-"))
        row_data.extend([
            total_score,
            max_total,
            flagged_count,
            f"{avg_confidence:.2%}" if isinstance(avg_confidence, (int, float)) else str(avg_confidence),
            status,
            "FINAL" if is_final else "NOT FINAL",
        ])

        ws.append(row_data)

        # Style data row
        for col_idx in range(1, len(row_data) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = thin_border
            cell.alignment = Alignment(
                horizontal="left" if col_idx <= 2 else "center",
                vertical="center"
            )
            # Highlight status column
            if col_idx == len(row_data) - 1:
                if status in status_fills:
                    cell.fill = status_fills[status]
            if col_idx == len(row_data) and not is_final:
                cell.fill = status_fills["NEEDS_REVIEW"]

    # Auto-adjust column widths
    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            val_str = str(cell.value or "")
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    # Save to memory buffer
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    suffix = "_NOT_FINAL" if not_final else ""
    filename = f"AutoGrade_Results_{exam_id}{suffix}.xlsx"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )
