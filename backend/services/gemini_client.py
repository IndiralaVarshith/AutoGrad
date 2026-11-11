"""The only route to Gemini on the Gemini-only path.

Enforces the request budget: pacing under the per-minute limits, backoff on 429,
a hard cap on calls per run, a separate cap for the stronger review model, a daily
count that survives restarts, and a per-sheet log of calls, tokens and notional cost.

The API key is read here and never logged. Log lines hold counts and identifiers
only, never prompt or answer content.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import config

BLOCK_REAL_CALLS_ENV = "AUTOGRAD_BLOCK_REAL_GEMINI"
_PACIFIC = ZoneInfo("America/Los_Angeles")
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_RETRY_DELAY = re.compile(r"retry(?:Delay)?['\":\s]*(?:in\s+)?(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


class GeminiError(Exception):
    """Base class. Callers flag the work for the teacher; they never guess a mark."""


class RunCapReached(GeminiError):
    """The hard cap on calls for this run, or for the review model, was reached."""


class DailyLimitReached(GeminiError):
    """The model's daily allowance is used up. Resume after midnight Pacific."""


class GeminiCallFailed(GeminiError):
    """The call failed, after any retries. transient: worth trying again later."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class BadModelResponse(GeminiError):
    """Gemini answered, but not with usable JSON. Not retried: one request per sheet."""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_known: bool = True

    def add(self, input_tokens: int, output_tokens: int, cost_usd: float | None) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        if cost_usd is None:
            self.cost_known = False
        else:
            self.cost_usd += cost_usd

    def as_dict(self, usd_inr: float) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "notional_cost_usd": round(self.cost_usd, 6) if self.cost_known else None,
            "notional_cost_inr": round(self.cost_usd * usd_inr, 4) if self.cost_known else None,
        }


@dataclass
class RunBudget:
    """Counts one run's calls. Every attempt that reaches Google counts, retries included."""

    run_id: str
    call_cap: int
    review_call_cap: int
    calls: int = 0
    review_calls: int = 0
    per_sheet: dict[str, Usage] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class GeminiResult:
    parsed: Any
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    finish_reason: str = ""


def split_pages(page_count: int, pages_per_request: int | None = None) -> list[tuple[int, int]]:
    """Page ranges (start, end) for a sheet: one request, or two if it is over the limit."""
    limit = pages_per_request or config.GEMINI_PAGES_PER_REQUEST
    if page_count <= 0:
        return []
    if page_count <= limit:
        return [(0, page_count)]
    middle = (page_count + 1) // 2
    return [(0, middle), (middle, page_count)]


def _sdk_transport(api_key: str) -> Callable[..., Any]:
    from google import genai

    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": config.GEMINI_REQUEST_TIMEOUT_MS},
    )

    def send(model: str, contents: Any, generation_config: Any) -> Any:
        return client.models.generate_content(
            model=model, contents=contents, config=generation_config
        )

    return send


def _load_api_key() -> str:
    key = os.getenv(config.GEMINI_KEY_ENV_NAME, "").strip()
    if not key:
        raise GeminiCallFailed(f"{config.GEMINI_KEY_ENV_NAME} is not set.")
    return key


def _status_of(error: Exception) -> int | None:
    code = getattr(error, "code", None)
    return code if isinstance(code, int) else None


def _describe(error: Exception) -> str:
    """Error text with no way of carrying the key: type and status only."""
    return f"{type(error).__name__} status={_status_of(error)}"


def _is_daily_quota(error: Exception) -> bool:
    return _status_of(error) == 429 and re.search(
        r"per.?day|PerDay|RPD", str(error), re.IGNORECASE
    ) is not None


def _is_network_error(error: Exception) -> bool:
    return _status_of(error) is None and type(error).__name__ in {
        "ConnectError", "ConnectTimeout", "ReadTimeout", "ReadError", "WriteTimeout",
        "RemoteProtocolError", "TimeoutException", "PoolTimeout", "TimeoutError",
        "ConnectionError",
    }


class GeminiClient:
    def __init__(
        self,
        transport: Callable[..., Any] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        usage_dir: Path | str | None = None,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._wall_clock = wall_clock
        self._usage_dir = Path(usage_dir or config.GEMINI_USAGE_DIR)
        self._pace_lock = threading.Lock()
        self._file_lock = threading.Lock()
        self._sent_at: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()
        self._next_slot = 0.0
        self._largest_call_tokens = 0

    # ------------------------------------------------------------------ runs

    def new_run(
        self,
        run_id: str | None = None,
        *,
        call_cap: int | None = None,
        review_call_cap: int | None = None,
    ) -> RunBudget:
        return RunBudget(
            run_id=run_id or uuid.uuid4().hex[:12],
            call_cap=config.GEMINI_RUN_CALL_CAP if call_cap is None else call_cap,
            review_call_cap=(
                config.GEMINI_REVIEW_CALL_CAP if review_call_cap is None else review_call_cap
            ),
        )

    def run_summary(self, run: RunBudget) -> dict[str, Any]:
        total = Usage()
        for usage in run.per_sheet.values():
            total.calls += usage.calls
            total.add(usage.input_tokens, usage.output_tokens, usage.cost_usd if usage.cost_known else None)
        rate = config.GEMINI_USD_INR
        return {
            "run_id": run.run_id,
            "calls": run.calls,
            "review_calls": run.review_calls,
            "call_cap": run.call_cap,
            "review_call_cap": run.review_call_cap,
            "totals": total.as_dict(rate),
            "per_sheet": {name: usage.as_dict(rate) for name, usage in run.per_sheet.items()},
            "pricing_basis": self.pricing_basis(),
        }

    @staticmethod
    def pricing_basis() -> dict[str, Any]:
        return {
            "note": (
                "Notional cost: what these tokens would cost on the paid tier. "
                "The actual charge is Rs 0 while the key's project has no billing."
            ),
            "usd_per_million_tokens": config.GEMINI_PRICES_USD_PER_MILLION,
            "prices_as_of": config.GEMINI_PRICES_AS_OF,
            "usd_inr": config.GEMINI_USD_INR,
            "usd_inr_source": config.GEMINI_USD_INR_SOURCE,
        }

    # ----------------------------------------------------------------- calls

    def generate(
        self,
        run: RunBudget,
        *,
        kind: str,
        sheet_id: str,
        contents: Any,
        response_schema: Any = None,
        system_instruction: str | None = None,
        review: bool = False,
        estimated_tokens: int = 0,
        max_output_tokens: int | None = None,
        model: str | None = None,
    ) -> GeminiResult:
        """One logical request. Retries only when no answer came back."""
        model = model or (config.GEMINI_REVIEW_MODEL if review else config.GEMINI_MODEL)
        if review and not model:
            raise RunCapReached("No stronger review model is configured.")
        generation_config = self._generation_config(
            response_schema, system_instruction, max_output_tokens
        )
        last_error: Exception | None = None
        for attempt in range(1, config.GEMINI_RETRY_ATTEMPTS + 1):
            self._reserve_call(run, review)
            self._check_daily(model, review)
            self._wait_for_slot(estimated_tokens)
            self._count_daily(model)
            try:
                response = self._send(model, contents, generation_config)
            except Exception as error:  # noqa: BLE001 - classified just below
                last_error = error
                self._log(run, kind, sheet_id, model, attempt, _describe(error), 0, 0, None)
                self._usage_for(run, sheet_id).calls += 1
                if _is_daily_quota(error):
                    self._mark_daily_exhausted(model)
                    raise DailyLimitReached(f"Daily quota used up for {model}.") from None
                status = _status_of(error)
                if status not in _RETRYABLE_STATUS and not _is_network_error(error):
                    raise GeminiCallFailed(_describe(error)) from None
                if attempt < config.GEMINI_RETRY_ATTEMPTS:
                    self._sleep(self._backoff_seconds(attempt, error))
                continue
            return self._finish(run, kind, sheet_id, model, attempt, response, response_schema)
        raise GeminiCallFailed(
            f"Gave up after {config.GEMINI_RETRY_ATTEMPTS} attempts: "
            f"{_describe(last_error) if last_error else 'unknown'}",
            transient=True,
        )

    @staticmethod
    def _generation_config(
        response_schema: Any, system_instruction: str | None, max_output_tokens: int | None
    ) -> Any:
        from google.genai import types

        options: dict[str, Any] = {
            "temperature": 0,
            "max_output_tokens": max_output_tokens or config.GEMINI_MAX_OUTPUT_TOKENS,
        }
        if system_instruction:
            options["system_instruction"] = system_instruction
        if response_schema is not None:
            options["response_mime_type"] = "application/json"
            options["response_schema"] = response_schema
        return types.GenerateContentConfig(**options)

    def _send(self, model: str, contents: Any, generation_config: Any) -> Any:
        if self._transport is None:
            if os.getenv(BLOCK_REAL_CALLS_ENV):
                raise GeminiCallFailed("Real Gemini calls are blocked in tests.")
            self._transport = _sdk_transport(_load_api_key())
        return self._transport(model, contents, generation_config)

    def _finish(
        self,
        run: RunBudget,
        kind: str,
        sheet_id: str,
        model: str,
        attempt: int,
        response: Any,
        response_schema: Any,
    ) -> GeminiResult:
        usage_meta = getattr(response, "usage_metadata", None)
        input_tokens = int(getattr(usage_meta, "prompt_token_count", 0) or 0)
        # Thinking tokens are charged at the output rate.
        output_tokens = int(getattr(usage_meta, "candidates_token_count", 0) or 0) + int(
            getattr(usage_meta, "thoughts_token_count", 0) or 0
        )
        cost = self.cost_usd(model, input_tokens, output_tokens)
        usage = self._usage_for(run, sheet_id)
        usage.calls += 1
        usage.add(input_tokens, output_tokens, cost)
        self._record_tokens(input_tokens + output_tokens)
        finish_reason = self._finish_reason(response)
        self._log(
            run, kind, sheet_id, model, attempt, f"ok finish={finish_reason}",
            input_tokens, output_tokens, cost,
        )
        text = getattr(response, "text", None) or ""
        parsed = None
        if response_schema is not None:
            parsed = getattr(response, "parsed", None)
            if parsed is None:
                try:
                    parsed = json.loads(text)
                except (TypeError, ValueError):
                    raise BadModelResponse(
                        f"No usable JSON from {model} (finish={finish_reason})."
                    ) from None
        return GeminiResult(parsed, text, model, input_tokens, output_tokens, finish_reason)

    @staticmethod
    def _finish_reason(response: Any) -> str:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return "NO_CANDIDATE"
        reason = getattr(candidates[0], "finish_reason", None)
        return str(getattr(reason, "name", reason) or "")

    @staticmethod
    def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
        price = config.GEMINI_PRICES_USD_PER_MILLION.get(model)
        if price is None:
            return None  # Unknown price: report as unknown, never as zero.
        return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000

    # ---------------------------------------------------------------- budget

    @staticmethod
    def _usage_for(run: RunBudget, sheet_id: str) -> Usage:
        with run.lock:
            return run.per_sheet.setdefault(sheet_id, Usage())

    @staticmethod
    def _reserve_call(run: RunBudget, review: bool) -> None:
        with run.lock:
            if run.calls >= run.call_cap:
                raise RunCapReached(f"Run cap of {run.call_cap} calls reached.")
            if review and run.review_calls >= run.review_call_cap:
                raise RunCapReached(f"Review cap of {run.review_call_cap} calls reached.")
            run.calls += 1
            if review:
                run.review_calls += 1

    def _wait_for_slot(self, estimated_tokens: int) -> None:
        """Hold the call until both per-minute limits allow it. Claims the slot under lock."""
        with self._pace_lock:
            spacing = 60.0 / config.GEMINI_RPM_LIMIT
            while True:
                now = self._clock()
                while self._sent_at and now - self._sent_at[0] >= 60.0:
                    self._sent_at.popleft()
                while self._tokens and now - self._tokens[0][0] >= 60.0:
                    self._tokens.popleft()
                wait = max(0.0, self._next_slot - now)
                if len(self._sent_at) >= config.GEMINI_RPM_LIMIT:
                    wait = max(wait, 60.0 - (now - self._sent_at[0]))
                expected = estimated_tokens or self._largest_call_tokens
                used = sum(count for _, count in self._tokens)
                if self._tokens and used + expected > config.GEMINI_TPM_LIMIT:
                    wait = max(wait, 60.0 - (now - self._tokens[0][0]))
                if wait <= 0:
                    self._sent_at.append(now)
                    self._next_slot = now + spacing
                    return
                self._sleep(wait)

    def _record_tokens(self, count: int) -> None:
        with self._pace_lock:
            self._tokens.append((self._clock(), count))
            self._largest_call_tokens = max(self._largest_call_tokens, count)

    @staticmethod
    def _backoff_seconds(attempt: int, error: Exception) -> float:
        """Google's own retry delay when it gives one, else doubling with jitter."""
        match = _RETRY_DELAY.search(str(error))
        if match:
            return min(float(match.group(1)) + 1.0, config.GEMINI_RETRY_MAX_WAIT_SECONDS)
        base = config.GEMINI_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
        return min(base + random.uniform(0, 1), config.GEMINI_RETRY_MAX_WAIT_SECONDS)

    # ----------------------------------------------------------- daily count

    def _pacific_day(self) -> str:
        return self._wall_clock().astimezone(_PACIFIC).date().isoformat()

    def _daily_path(self) -> Path:
        return self._usage_dir / "daily_counts.json"

    def _read_daily(self) -> dict[str, Any]:
        try:
            state = json.loads(self._daily_path().read_text())
        except (OSError, ValueError):
            state = {}
        if state.get("day") != self._pacific_day():
            state = {"day": self._pacific_day(), "counts": {}, "exhausted": []}
        return state

    def _write_daily(self, state: dict[str, Any]) -> None:
        self._usage_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._daily_path().with_suffix(".tmp")
        temporary.write_text(json.dumps(state))
        temporary.replace(self._daily_path())

    def _check_daily(self, model: str, review: bool) -> None:
        limit = config.GEMINI_REVIEW_RPD_LIMIT if review else config.GEMINI_RPD_LIMIT
        with self._file_lock:
            state = self._read_daily()
        if model in state["exhausted"] or state["counts"].get(model, 0) >= limit:
            raise DailyLimitReached(
                f"Daily allowance used up for {model} ({limit} set). Resume after midnight Pacific."
            )

    def _count_daily(self, model: str) -> None:
        with self._file_lock:
            state = self._read_daily()
            state["counts"][model] = state["counts"].get(model, 0) + 1
            self._write_daily(state)

    def _mark_daily_exhausted(self, model: str) -> None:
        with self._file_lock:
            state = self._read_daily()
            if model not in state["exhausted"]:
                state["exhausted"].append(model)
            self._write_daily(state)

    def daily_counts(self) -> dict[str, Any]:
        with self._file_lock:
            return self._read_daily()

    # --------------------------------------------------------------- logging

    def _log(
        self,
        run: RunBudget,
        kind: str,
        sheet_id: str,
        model: str,
        attempt: int,
        outcome: str,
        input_tokens: int,
        output_tokens: int,
        cost: float | None,
    ) -> None:
        record = {
            "at": self._wall_clock().isoformat(),
            "run_id": run.run_id,
            "kind": kind,
            "sheet": sheet_id,
            "model": model,
            "attempt": attempt,
            "outcome": outcome,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "notional_cost_usd": None if cost is None else round(cost, 6),
            "notional_cost_inr": None if cost is None else round(cost * config.GEMINI_USD_INR, 4),
            "usd_inr": config.GEMINI_USD_INR,
        }
        with self._file_lock:
            self._usage_dir.mkdir(parents=True, exist_ok=True)
            with (self._usage_dir / "calls.jsonl").open("a") as handle:
                handle.write(json.dumps(record) + "\n")


_shared_client: GeminiClient | None = None
_shared_lock = threading.Lock()


def get_client() -> GeminiClient:
    """One client per process, so pacing holds across queue and instant mode."""
    global _shared_client
    with _shared_lock:
        if _shared_client is None:
            _shared_client = GeminiClient()
        return _shared_client
