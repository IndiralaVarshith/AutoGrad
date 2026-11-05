from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator


_logger = logging.getLogger("autograde.timing")


def log_timing(stage: str, started_at: float, **fields: Any) -> None:
    event = {
        "event": "evaluation_stage_timing",
        "stage": stage,
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
        **fields,
    }
    _logger.info("%s", json.dumps(event, sort_keys=True, default=str))


@contextmanager
def timed_stage(
    stage: str,
    *,
    job_id: str | None = None,
    on_stage: Callable[[str, str], None] | None = None,
    detail: str = "",
    **fields: Any,
) -> Iterator[None]:
    if on_stage is not None:
        on_stage(stage, detail)
    started_at = time.perf_counter()
    try:
        yield
    finally:
        log_timing(stage, started_at, job_id=job_id, detail=detail, **fields)
