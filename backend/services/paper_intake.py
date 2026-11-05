"""Question paper review list: what the teacher should look at first.

Nothing here changes a question. Marks only ever come from what the paper prints.
"""

from __future__ import annotations

import re
from typing import Any

_FIGURE_RE = re.compile(r"\b(graph|chart|figure|diagram|map|table)s?\b", re.IGNORECASE)


def _figure_page(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    return page if page > 0 else None


def apply_review_items(questions: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Mark figure questions and list what the teacher should look at first.

    These do not stop a paper being saved. The wording is never changed here.
    """
    items: list[dict[str, str]] = []
    by_wording: dict[str, list[str]] = {}

    for q in questions:
        label = f"Q{q.get('question_number')}{q.get('sub_part') or ''}"
        text = str(q.get("question_text", ""))
        # The model's flag can be wrong, so the printed wording is checked as well.
        q["has_figure"] = q.get("has_figure") is True or bool(_FIGURE_RE.search(text))
        q["figure_page"] = _figure_page(q.get("figure_page")) if q["has_figure"] else None
        if q["has_figure"]:
            where = f" on page {q['figure_page']}" if q["figure_page"] else ""
            items.append({
                "question": label,
                "reason": (
                    f"Uses a graph, table or figure{where}. Check the wording against the "
                    "paper and remove any values copied from the figure."
                ),
            })
        wording = " ".join(text.split()).casefold()
        if wording:
            by_wording.setdefault(wording, []).append(label)

    for labels in by_wording.values():
        if len(labels) > 1:
            items.append({
                "question": ", ".join(labels),
                "reason": "These have identical wording. Check whether the paper really prints it twice.",
            })
    return items
