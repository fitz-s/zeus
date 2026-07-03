# Created: 2026-07-02
# Last reused/audited: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 "rate-limit budget + cancel-priority" — W2.3 packet (inert, no call site).
"""Tests for src.venue.rate_budget: shared token-bucket venue call budget with
cancel-priority.

Covers: budget exhaustion, cancel-preempts-submit ordering, Retry-After
honored (header + default fallback), refill arithmetic, and concurrent-grant
safety under real thread concurrency (the daemon runs a 20+2 worker pool).
"""

from __future__ import annotations

import threading

import pytest

from src.venue.rate_budget import (
    BudgetDecision,
    RateBudgetConfig,
    RequestClass,
    RetryInstruction,
    VenueRateBudget,
    parse_retry_after_seconds,
    retry_instruction_from_exception,
    retry_instruction_from_response,
)


class FakeClock:
    """Deterministic injectable clock — no test ever sleeps."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


# ---------------------------------------------------------------------------
# Budget exhaustion
# ---------------------------------------------------------------------------


def test_submit_granted_until_reserve_floor_then_deferred():
    clock = FakeClock()
    config = RateBudgetConfig(capacity_tokens=3.0, rate_per_sec=1.0, cancel_reserve_tokens=1.0)
    budget = VenueRateBudget(config, clock=clock)

    # capacity=3, reserve=1 -> SUBMIT may take tokens down to 1, i.e. 2 grants.
    first = budget.try_acquire(RequestClass.SUBMIT)
    second = budget.try_acquire(RequestClass.SUBMIT)
    third = budget.try_acquire(RequestClass.SUBMIT)

    assert first.decision is BudgetDecision.GRANTED
    assert second.decision is BudgetDecision.GRANTED
    assert third.decision is BudgetDecision.DEFERRED
    assert third.wait_seconds > 0


def test_deferred_result_reports_positive_wait_seconds():
    clock = FakeClock()
    config = RateBudgetConfig(capacity_tokens=1.0, rate_per_sec=2.0, cancel_reserve_tokens=0.0)
    budget = VenueRateBudget(config, clock=clock)

    granted = budget.try_acquire(RequestClass.CANCEL)
    deferred = budget.try_acquire(RequestClass.CANCEL)

    assert granted.decision is BudgetDecision.GRANTED
    assert deferred.decision is BudgetDecision.DEFERRED
    # need 1 full token at rate=2/s -> 0.5s
    assert deferred.wait_seconds == pytest.approx(0.5)


def test_exhausted_budget_recovers_after_waiting_the_reported_duration():
    clock = FakeClock()
    config = RateBudgetConfig(capacity_tokens=1.0, rate_per_sec=1.0, cancel_reserve_tokens=0.0)
    budget = VenueRateBudget(config, clock=clock)

    budget.try_acquire(RequestClass.CANCEL)
    deferred = budget.try_acquire(RequestClass.CANCEL)
    assert deferred.decision is BudgetDecision.DEFERRED

    clock.advance(deferred.wait_seconds)
    recovered = budget.try_acquire(RequestClass.CANCEL)
    assert recovered.decision is BudgetDecision.GRANTED


# ---------------------------------------------------------------------------
# Cancel-preempts-submit ordering (the design law)
# ---------------------------------------------------------------------------


def test_cancel_still_granted_after_submit_hits_the_reserve_floor():
    clock = FakeClock()
    config = RateBudgetConfig(capacity_tokens=2.0, rate_per_sec=0.0001, cancel_reserve_tokens=1.0)
    budget = VenueRateBudget(config, clock=clock)

    # Drain to the reserve floor with submits.
    assert budget.try_acquire(RequestClass.SUBMIT).decision is BudgetDecision.GRANTED
    # Second submit would breach the reserve -> DEFERRED, even though a raw
    # token remains (that token is reserved for CANCEL only).
    submit_blocked = budget.try_acquire(RequestClass.SUBMIT)
    assert submit_blocked.decision is BudgetDecision.DEFERRED

    # CANCEL draws the reserved token that SUBMIT was refused.
    cancel_result = budget.try_acquire(RequestClass.CANCEL)
    assert cancel_result.decision is BudgetDecision.GRANTED


def test_submit_never_dips_below_cancel_reserve_even_under_repeated_pressure():
    clock = FakeClock()
    config = RateBudgetConfig(capacity_tokens=5.0, rate_per_sec=1e-9, cancel_reserve_tokens=2.0)
    budget = VenueRateBudget(config, clock=clock)

    granted_submits = 0
    for _ in range(10):
        result = budget.try_acquire(RequestClass.SUBMIT)
        if result.decision is BudgetDecision.GRANTED:
            granted_submits += 1

    # capacity=5, reserve=2 -> at most 3 submits ever granted with zero refill.
    assert granted_submits == 3
    # The 2 reserved tokens are still available to CANCEL.
    assert budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.GRANTED
    assert budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.GRANTED
    assert budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.DEFERRED


def test_zero_reserve_treats_both_classes_identically():
    clock = FakeClock()
    config = RateBudgetConfig(capacity_tokens=1.0, rate_per_sec=1e-9, cancel_reserve_tokens=0.0)
    budget = VenueRateBudget(config, clock=clock)

    assert budget.try_acquire(RequestClass.SUBMIT).decision is BudgetDecision.GRANTED
    assert budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.DEFERRED


# ---------------------------------------------------------------------------
# Retry-After honored
# ---------------------------------------------------------------------------


def test_parse_retry_after_seconds_numeric():
    assert parse_retry_after_seconds("30") == 30.0
    assert parse_retry_after_seconds("0") == 0.0
    assert parse_retry_after_seconds(" 12.5 ") == 12.5


def test_parse_retry_after_seconds_invalid_or_missing():
    assert parse_retry_after_seconds(None) is None
    assert parse_retry_after_seconds("") is None
    assert parse_retry_after_seconds("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert parse_retry_after_seconds("-5") is None


def test_retry_instruction_from_response_honors_header():
    instruction = retry_instruction_from_response(
        RequestClass.SUBMIT, status_code=429, headers={"Retry-After": "42"}
    )
    assert instruction == RetryInstruction(RequestClass.SUBMIT, 42.0, "header")


def test_retry_instruction_from_response_falls_back_to_default_without_header():
    instruction = retry_instruction_from_response(
        RequestClass.CANCEL, status_code=429, headers=None, default_backoff_seconds=15.0
    )
    assert instruction == RetryInstruction(RequestClass.CANCEL, 15.0, "default")


def test_retry_instruction_from_response_none_when_not_429():
    assert retry_instruction_from_response(RequestClass.SUBMIT, status_code=200) is None
    assert retry_instruction_from_response(RequestClass.SUBMIT, status_code=None) is None


class _FakeHttpResponse:
    def __init__(self, status_code: int, headers: dict[str, str]):
        self.status_code = status_code
        self.headers = headers


class _FakeHttpStatusError(Exception):
    def __init__(self, response: _FakeHttpResponse):
        super().__init__("429 Too Many Requests")
        self.response = response


def test_retry_instruction_from_exception_matches_httpx_shape():
    exc = _FakeHttpStatusError(_FakeHttpResponse(429, {"Retry-After": "7"}))
    instruction = retry_instruction_from_exception(RequestClass.CANCEL, exc)
    assert instruction == RetryInstruction(RequestClass.CANCEL, 7.0, "header")


def test_retry_instruction_from_exception_returns_none_without_response_attr():
    assert retry_instruction_from_exception(RequestClass.SUBMIT, RuntimeError("boom")) is None


def test_note_429_response_engages_cooldown_that_denies_further_acquires():
    clock = FakeClock()
    config = RateBudgetConfig(capacity_tokens=5.0, rate_per_sec=1.0, cancel_reserve_tokens=0.0)
    budget = VenueRateBudget(config, clock=clock)

    instruction = budget.note_429_response(
        RequestClass.SUBMIT, status_code=429, headers={"Retry-After": "10"}
    )
    assert instruction is not None
    assert instruction.retry_after_seconds == 10.0

    # SUBMIT is denied by the cooldown even though tokens are available.
    result = budget.try_acquire(RequestClass.SUBMIT)
    assert result.decision is BudgetDecision.DENIED
    assert result.wait_seconds == pytest.approx(10.0)

    # CANCEL is unaffected — the cooldown is per-class (matches Polymarket's
    # separate POST /order vs DELETE /order limits).
    assert budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.GRANTED


def test_cooldown_expires_after_the_reported_wait():
    clock = FakeClock()
    budget = VenueRateBudget(clock=clock)
    budget.note_rate_limited(RequestClass.SUBMIT, 5.0)

    assert budget.try_acquire(RequestClass.SUBMIT).decision is BudgetDecision.DENIED
    clock.advance(5.0)
    assert budget.try_acquire(RequestClass.SUBMIT).decision is BudgetDecision.GRANTED


def test_note_rate_limited_extends_but_never_shortens_an_active_cooldown():
    clock = FakeClock()
    budget = VenueRateBudget(clock=clock)

    budget.note_rate_limited(RequestClass.CANCEL, 10.0)
    budget.note_rate_limited(RequestClass.CANCEL, 2.0)  # shorter — must not shorten
    clock.advance(3.0)
    assert budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.DENIED

    clock.advance(10.0)
    assert budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.GRANTED


def test_note_429_exception_applies_the_parsed_backoff():
    clock = FakeClock()
    budget = VenueRateBudget(clock=clock)
    exc = _FakeHttpStatusError(_FakeHttpResponse(429, {}))

    instruction = budget.note_429_exception(RequestClass.SUBMIT, exc)
    assert instruction is not None
    assert instruction.source == "default"
    assert budget.try_acquire(RequestClass.SUBMIT).decision is BudgetDecision.DENIED


# ---------------------------------------------------------------------------
# Refill arithmetic
# ---------------------------------------------------------------------------


def test_refill_accumulates_linearly_with_elapsed_time():
    clock = FakeClock()
    config = RateBudgetConfig(capacity_tokens=10.0, rate_per_sec=2.0, cancel_reserve_tokens=0.0)
    budget = VenueRateBudget(config, clock=clock)

    for _ in range(10):
        budget.try_acquire(RequestClass.CANCEL)
    assert budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.DEFERRED

    clock.advance(1.0)  # +2 tokens at rate=2/s
    assert budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.GRANTED
    assert budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.GRANTED
    assert budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.DEFERRED


def test_refill_never_exceeds_capacity():
    clock = FakeClock()
    config = RateBudgetConfig(capacity_tokens=3.0, rate_per_sec=1.0, cancel_reserve_tokens=0.0)
    budget = VenueRateBudget(config, clock=clock)

    clock.advance(1000.0)  # huge idle gap
    granted = 0
    for _ in range(5):
        if budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.GRANTED:
            granted += 1
    assert granted == 3  # capped at capacity_tokens, not idle_time * rate


def test_zero_elapsed_time_grants_no_extra_tokens():
    clock = FakeClock()
    config = RateBudgetConfig(capacity_tokens=1.0, rate_per_sec=100.0, cancel_reserve_tokens=0.0)
    budget = VenueRateBudget(config, clock=clock)

    assert budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.GRANTED
    # No clock advance -> no refill.
    assert budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.DEFERRED


# ---------------------------------------------------------------------------
# Concurrent-grant safety (daemon runs a 20+2 worker thread pool)
# ---------------------------------------------------------------------------


def test_concurrent_acquires_never_oversell_the_bucket():
    clock = FakeClock()
    config = RateBudgetConfig(capacity_tokens=50.0, rate_per_sec=1e-9, cancel_reserve_tokens=0.0)
    budget = VenueRateBudget(config, clock=clock)

    granted_count = 0
    lock = threading.Lock()

    def worker():
        nonlocal granted_count
        result = budget.try_acquire(RequestClass.SUBMIT)
        if result.decision is BudgetDecision.GRANTED:
            with lock:
                granted_count += 1

    threads = [threading.Thread(target=worker) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # With zero refill, capacity=50 caps grants at exactly 50 regardless of
    # 200 threads racing the lock — no over-reserve, no double-spend.
    assert granted_count == 50
    snapshot = budget.snapshot()
    assert snapshot[RequestClass.SUBMIT.value]["granted"] == 50
    assert snapshot[RequestClass.SUBMIT.value]["deferred"] == 150


def test_concurrent_mixed_classes_respect_reserve_floor_under_race():
    clock = FakeClock()
    config = RateBudgetConfig(capacity_tokens=30.0, rate_per_sec=1e-9, cancel_reserve_tokens=10.0)
    budget = VenueRateBudget(config, clock=clock)

    submit_granted = 0
    cancel_granted = 0
    lock = threading.Lock()

    def submit_worker():
        nonlocal submit_granted
        if budget.try_acquire(RequestClass.SUBMIT).decision is BudgetDecision.GRANTED:
            with lock:
                submit_granted += 1

    def cancel_worker():
        nonlocal cancel_granted
        if budget.try_acquire(RequestClass.CANCEL).decision is BudgetDecision.GRANTED:
            with lock:
                cancel_granted += 1

    threads = [threading.Thread(target=submit_worker) for _ in range(100)]
    threads += [threading.Thread(target=cancel_worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # SUBMIT can never win more than (capacity - reserve) = 20 grants, no
    # matter how the 100 submit threads race against the 20 cancel threads.
    assert submit_granted <= 20
    # CANCEL can always claim at least the reserved floor (10 tokens) even
    # if every submit thread wins the race for the general pool first —
    # the reserve is the guarantee, not a fixed split of the race outcome.
    assert cancel_granted >= 10
    assert submit_granted + cancel_granted <= 30


# ---------------------------------------------------------------------------
# Config validation + settings loading
# ---------------------------------------------------------------------------


def test_config_rejects_reserve_larger_than_capacity():
    with pytest.raises(ValueError):
        RateBudgetConfig(capacity_tokens=5.0, cancel_reserve_tokens=10.0)


def test_config_rejects_non_positive_rate():
    with pytest.raises(ValueError):
        RateBudgetConfig(rate_per_sec=0.0)


def test_from_settings_falls_back_to_defaults_when_key_absent():
    config = RateBudgetConfig.from_settings({"execution": {"order_type": "limit_only"}})
    assert config == RateBudgetConfig()


def test_from_settings_reads_override_block():
    config = RateBudgetConfig.from_settings(
        {
            "execution": {
                "venue_rate_budget": {
                    "capacity_tokens": 40.0,
                    "rate_per_sec": 5.0,
                    "cancel_reserve_tokens": 8.0,
                    "default_429_backoff_seconds": 20.0,
                }
            }
        }
    )
    assert config == RateBudgetConfig(
        capacity_tokens=40.0, rate_per_sec=5.0, cancel_reserve_tokens=8.0, default_429_backoff_seconds=20.0
    )


def test_from_settings_missing_execution_key_falls_back():
    config = RateBudgetConfig.from_settings({})
    assert config == RateBudgetConfig()
