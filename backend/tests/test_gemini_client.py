"""Request budget tests. A fake transport and clock: nothing here can reach Google."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from google.genai import errors

import config
from services import gemini_client
from services.gemini_client import (
    BadModelResponse,
    DailyLimitReached,
    GeminiCallFailed,
    GeminiClient,
    RunCapReached,
    split_pages,
)

SECRET = "AIza-not-a-real-key"


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def reply(text='{"ok": true}', input_tokens=100, output_tokens=10, thoughts=0):
    return SimpleNamespace(
        text=text,
        parsed=None,
        usage_metadata=SimpleNamespace(
            prompt_token_count=input_tokens,
            candidates_token_count=output_tokens,
            thoughts_token_count=thoughts,
        ),
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
    )


def quota_error(message="Too many requests. Please retry in 34s."):
    return errors.ClientError(429, {"error": {"code": 429, "message": message, "status": "RESOURCE_EXHAUSTED"}})


class Transport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.models = []
        self.sent_at = []
        self.time = None

    def __call__(self, model, contents, generation_config):
        self.models.append(model)
        if self.time:
            self.sent_at.append(self.time.now)
        outcome = self.outcomes.pop(0) if self.outcomes else reply()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def make(tmp_path):
    def build(*outcomes):
        fake_time = FakeTime()
        transport = Transport(*outcomes)
        transport.time = fake_time
        client = GeminiClient(
            transport, clock=fake_time.clock, sleep=fake_time.sleep, usage_dir=tmp_path
        )
        return client, transport, fake_time

    return build


def ask(client, run, **extra):
    return client.generate(run, kind="sheet", sheet_id="sheet-1", contents="x", **extra)


def log_lines(tmp_path):
    return [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]


@pytest.mark.parametrize(
    "pages,expected",
    [
        (0, []),
        (1, [(0, 1)]),
        (12, [(0, 12)]),
        (13, [(0, 7), (7, 13)]),
        (40, [(0, 20), (20, 40)]),
    ],
)
def test_one_request_per_sheet_two_only_over_twelve_pages(pages, expected):
    assert split_pages(pages, 12) == expected


def test_calls_are_spaced_under_the_per_minute_limit(make, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_RPM_LIMIT", 12)
    client, transport, _ = make()
    run = client.new_run(call_cap=30)
    for _ in range(30):
        ask(client, run)
    gaps = [b - a for a, b in zip(transport.sent_at, transport.sent_at[1:])]
    assert min(gaps) >= 5.0 - 1e-9
    for index, sent in enumerate(transport.sent_at):
        in_window = [t for t in transport.sent_at[index:] if t - sent < 60]
        assert len(in_window) <= 12


def test_calls_wait_when_tokens_would_pass_the_per_minute_limit(make, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_RPM_LIMIT", 60)
    monkeypatch.setattr(config, "GEMINI_TPM_LIMIT", 1000)
    client, transport, _ = make(reply(input_tokens=900), reply(input_tokens=900))
    run = client.new_run()
    ask(client, run)
    ask(client, run)
    assert transport.sent_at[1] - transport.sent_at[0] >= 60.0


def test_429_retries_with_googles_delay_then_succeeds(make, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_RPM_LIMIT", 6000)
    client, transport, fake_time = make(quota_error(), reply())
    run = client.new_run()
    result = ask(client, run, response_schema={"type": "object"})
    assert result.parsed == {"ok": True}
    assert 35.0 in fake_time.slept
    assert run.calls == 2  # Every attempt counts against the cap.
    assert run.per_sheet["sheet-1"].calls == 2


def test_429_without_a_delay_doubles_and_stays_capped(make, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_RPM_LIMIT", 6000)
    monkeypatch.setattr(config, "GEMINI_RETRY_ATTEMPTS", 4)
    monkeypatch.setattr(config, "GEMINI_RETRY_BASE_SECONDS", 5.0)
    bare = quota_error("Resource exhausted.")
    client, transport, fake_time = make(bare, bare, bare, bare)
    run = client.new_run()
    with pytest.raises(GeminiCallFailed):
        ask(client, run)
    assert len(transport.models) == 4
    waits = [s for s in fake_time.slept if s >= 5]
    assert len(waits) == 3
    assert waits[0] < waits[1] < waits[2]


def test_daily_quota_429_stops_at_once_and_blocks_later_calls(make, tmp_path):
    daily = quota_error("Quota exceeded: GenerateRequestsPerDayPerProjectPerModel-FreeTier. Please retry in 30s.")
    client, transport, _ = make(daily)
    run = client.new_run()
    with pytest.raises(DailyLimitReached):
        ask(client, run)
    with pytest.raises(DailyLimitReached):
        ask(client, run)
    assert len(transport.models) == 1
    # A restarted process still knows.
    restarted = GeminiClient(Transport(), usage_dir=tmp_path)
    with pytest.raises(DailyLimitReached):
        restarted.generate(restarted.new_run(), kind="sheet", sheet_id="s", contents="x")


def test_daily_count_survives_restart_and_resets_at_midnight_pacific(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_RPD_LIMIT", 2)
    monkeypatch.setattr(config, "GEMINI_RPM_LIMIT", 6000)
    moment = {"now": datetime(2026, 11, 6, 7, 30, tzinfo=timezone.utc)}  # 23:30 Pacific, 5 Nov

    def build():
        return GeminiClient(
            Transport(), sleep=lambda _s: None, wall_clock=lambda: moment["now"], usage_dir=tmp_path
        )

    first = build()
    ask(first, first.new_run())
    second = build()
    ask(second, second.new_run())
    third = build()
    with pytest.raises(DailyLimitReached):
        ask(third, third.new_run())
    moment["now"] += timedelta(hours=1)  # 00:30 Pacific, 6 Nov
    ask(third, third.new_run())
    assert third.daily_counts()["counts"][config.GEMINI_MODEL] == 1


def test_hard_cap_stops_calls_and_retries(make, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_RPM_LIMIT", 6000)
    client, transport, _ = make(quota_error(), quota_error())
    run = client.new_run(call_cap=2)
    with pytest.raises(RunCapReached):
        ask(client, run)
    assert len(transport.models) == 2


def test_every_call_uses_the_one_configured_model(make, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_RPM_LIMIT", 6000)
    client, transport, _ = make()
    run = client.new_run()
    for _ in range(3):
        ask(client, run)
    assert transport.models == [config.GEMINI_MODEL] * 3 and run.calls == 3


def test_other_client_errors_are_not_retried(make):
    client, transport, _ = make(errors.ClientError(400, {"error": {"message": "bad"}}))
    with pytest.raises(GeminiCallFailed):
        ask(client, client.new_run())
    assert len(transport.models) == 1


def test_unusable_json_is_not_sent_again(make):
    client, transport, _ = make(reply(text="not json"))
    with pytest.raises(BadModelResponse):
        ask(client, client.new_run(), response_schema={"type": "object"})
    assert len(transport.models) == 1


def test_a_reply_cut_off_at_the_limit_is_told_apart_from_bad_json(make):
    from services.gemini_client import ReplyCutOff

    cut = reply(text='{"ok": tr', output_tokens=16000, thoughts=369)
    cut.candidates = [SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"))]
    client, transport, _ = make(cut)
    with pytest.raises(ReplyCutOff) as raised:
        ask(client, client.new_run(), response_schema={"type": "object"})
    assert isinstance(raised.value, BadModelResponse) and "16369 tokens" in str(raised.value)
    assert len(transport.models) == 1


def test_log_states_tokens_and_rupee_cost_per_sheet(make, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setattr(config, "GEMINI_USD_INR", 100.0)
    client, _, _ = make(reply(input_tokens=1_000_000, output_tokens=500_000, thoughts=500_000))
    run = client.new_run("run-a")
    ask(client, run)
    # 1M input at $0.30, 1M output (with thinking) at $2.50.
    line = log_lines(tmp_path)[0]
    assert line["run_id"] == "run-a" and line["sheet"] == "sheet-1"
    assert line["notional_cost_usd"] == pytest.approx(2.80)
    assert line["notional_cost_inr"] == pytest.approx(280.0)
    summary = client.run_summary(run)
    assert summary["per_sheet"]["sheet-1"]["notional_cost_inr"] == pytest.approx(280.0)
    assert summary["totals"]["calls"] == 1
    basis = summary["pricing_basis"]
    assert basis["usd_inr"] == 100.0 and basis["usd_inr_source"] and basis["prices_as_of"]
    assert "Rs 0" in basis["note"]


def test_unknown_price_is_reported_as_unknown_not_zero(make, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-no-price")
    client, _, _ = make()
    run = client.new_run()
    ask(client, run)
    assert log_lines(tmp_path)[0]["notional_cost_inr"] is None
    assert client.run_summary(run)["per_sheet"]["sheet-1"]["notional_cost_inr"] is None


def test_key_and_content_never_reach_the_log_or_errors(make, tmp_path):
    leak = errors.ClientError(400, {"error": {"message": f"bad key {SECRET}"}})
    client, _, _ = make(leak)
    with pytest.raises(GeminiCallFailed) as caught:
        client.generate(client.new_run(), kind="sheet", sheet_id="s", contents="STUDENT TEXT")
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None
    logged = (tmp_path / "calls.jsonl").read_text()
    assert SECRET not in logged and "STUDENT TEXT" not in logged


def test_real_calls_are_blocked_under_pytest(tmp_path):
    client = GeminiClient(usage_dir=tmp_path, sleep=lambda _s: None)
    with pytest.raises(GeminiCallFailed):
        client.generate(client.new_run(), kind="sheet", sheet_id="s", contents="x")
    assert client._transport is None


def test_key_comes_only_from_the_named_setting(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_KEY_ENV_NAME", "SOME_SLOT")
    monkeypatch.setenv("SOME_SLOT", SECRET)
    monkeypatch.setenv("GEMINI_API_KEY", "other")
    assert gemini_client._load_api_key() == SECRET
    monkeypatch.delenv("SOME_SLOT")
    with pytest.raises(GeminiCallFailed) as caught:
        gemini_client._load_api_key()
    assert "SOME_SLOT" in str(caught.value)
