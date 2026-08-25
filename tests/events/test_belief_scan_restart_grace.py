# Created: 2026-08-24
# Last reused/audited: 2026-08-24
# Authority basis: live incident 2026-08-24 (post-restart belief_scan livelock-by-deadline,
#   ~24.5min zero-auction gap 02:34:40Z-02:59:05Z in zeus_trades.db decision_log). A post-restart
#   cold-cache window against the 96GB world + 79GB forecasts DB pair produced 10 straight
#   belief_scan deadline_interrupted defers before the OS page cache re-warmed on its own. This
#   pins the restart-grace budget widening added in response (reactor.py
#   _edli_belief_scan_grace_defer_threshold / _edli_redecision_screen_budget_seconds /
#   _edli_belief_scan_consecutive_defers).
"""belief_scan restart-grace: budget widens after N consecutive defers, reverts on completion.

These are unit tests against the pure budget-decision function and its module-level defer
counter -- NOT a full run_edli_continuous_redecision_screen_cycle integration test. The counter
is module state that run_edli_continuous_redecision_screen_cycle increments/resets around real
belief_scan receipts (reactor.py); these tests simulate that state transition directly, which is
enough to pin the budget-widening contract without standing up a full world/trade DB cycle.
"""
from __future__ import annotations

import pytest

import src.events.reactor as reactor


@pytest.fixture(autouse=True)
def _reset_defer_counter():
    """Isolate tests from module-global defer-counter state (and any env override)."""
    original = reactor._edli_belief_scan_consecutive_defers
    reactor._edli_belief_scan_consecutive_defers = 0
    yield
    reactor._edli_belief_scan_consecutive_defers = original


def test_grace_defer_threshold_defaults_to_three():
    assert reactor._edli_belief_scan_grace_defer_threshold() == 3


def test_grace_defer_threshold_env_override(monkeypatch):
    monkeypatch.setenv("ZEUS_EDLI_BELIEF_SCAN_GRACE_DEFER_THRESHOLD", "5")
    assert reactor._edli_belief_scan_grace_defer_threshold() == 5


def test_grace_defer_threshold_malformed_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("ZEUS_EDLI_BELIEF_SCAN_GRACE_DEFER_THRESHOLD", "not-a-number")
    assert reactor._edli_belief_scan_grace_defer_threshold() == 3


def test_budget_stays_at_default_below_grace_threshold():
    reactor._edli_belief_scan_consecutive_defers = 2  # threshold is 3 (default)
    assert reactor._edli_redecision_screen_budget_seconds({}) == pytest.approx(
        reactor.DEFAULT_EDLI_REDECISION_SCREEN_BUDGET_SECONDS
    )


def test_three_consecutive_defers_widen_budget_to_max():
    reactor._edli_belief_scan_consecutive_defers = 3
    assert reactor._edli_redecision_screen_budget_seconds({}) == pytest.approx(
        reactor.MAX_EDLI_REDECISION_SCREEN_BUDGET_SECONDS
    )


def test_budget_stays_widened_past_threshold():
    reactor._edli_belief_scan_consecutive_defers = 10  # the live incident's actual streak
    assert reactor._edli_redecision_screen_budget_seconds({}) == pytest.approx(
        reactor.MAX_EDLI_REDECISION_SCREEN_BUDGET_SECONDS
    )


def test_completed_receipt_resets_counter_and_budget_reverts():
    """Simulates the reactor.py call-site behavior: a belief_scan 'completed' receipt
    resets _edli_belief_scan_consecutive_defers to 0 (reactor.py, right after the
    completed-receipt log call in run_edli_continuous_redecision_screen_cycle)."""
    reactor._edli_belief_scan_consecutive_defers = 3
    assert reactor._edli_redecision_screen_budget_seconds({}) == pytest.approx(
        reactor.MAX_EDLI_REDECISION_SCREEN_BUDGET_SECONDS
    )

    # The belief_scan stage completes -> the reset the reactor performs inline.
    reactor._edli_belief_scan_consecutive_defers = 0

    assert reactor._edli_redecision_screen_budget_seconds({}) == pytest.approx(
        reactor.DEFAULT_EDLI_REDECISION_SCREEN_BUDGET_SECONDS
    )


def test_counter_resets_on_completion_between_defers():
    """A completion between two defer streaks must not let the streaks accumulate
    across it -- each streak starts counting from zero after any completion."""
    # First streak: 2 defers, short of threshold.
    reactor._edli_belief_scan_consecutive_defers = 2
    assert reactor._edli_redecision_screen_budget_seconds({}) == pytest.approx(
        reactor.DEFAULT_EDLI_REDECISION_SCREEN_BUDGET_SECONDS
    )

    # A completion resets it.
    reactor._edli_belief_scan_consecutive_defers = 0

    # Second streak: 2 more defers. If the counter had NOT reset, this would be 4
    # (>= threshold) and incorrectly trigger grace.
    reactor._edli_belief_scan_consecutive_defers = 2
    assert reactor._edli_redecision_screen_budget_seconds({}) == pytest.approx(
        reactor.DEFAULT_EDLI_REDECISION_SCREEN_BUDGET_SECONDS
    )


def test_grace_widened_budget_respects_operator_configured_ceiling_above_max():
    """A configured value above MAX is already clamped to MAX regardless of grace
    state -- grace only raises the floor toward MAX, it never exceeds it."""
    reactor._edli_belief_scan_consecutive_defers = 3
    budget = reactor._edli_redecision_screen_budget_seconds(
        {"continuous_redecision_screen_budget_seconds": 500.0}
    )
    assert budget == pytest.approx(reactor.MAX_EDLI_REDECISION_SCREEN_BUDGET_SECONDS)


def test_reactor_job_is_non_reentrant_so_grace_cannot_overlap_itself():
    """The grace budget (85s) can exceed the 60s scheduler cadence only because the
    edli_event_reactor job is registered non-reentrant (max_instances=1,
    coalesce=True in src/main.py's _register_edli_live_jobs, a nested function
    inside main() and not independently importable) -- an overlapping trigger
    during a grace-widened cycle is skipped, never run concurrently. Pin that
    registration by source inspection so a future change can't silently
    invalidate this budget's safety argument without failing a test."""
    import inspect
    import src.main as main_mod

    src = inspect.getsource(main_mod)
    job_call_start = src.index('id="edli_event_reactor"')
    # add_job(...) call site is a small localized block; a generous window keeps
    # this robust to reformatting without accidentally matching an unrelated job.
    window = src[job_call_start - 400 : job_call_start + 400]
    assert "add_job" in window
    assert "max_instances=1" in window
    assert "coalesce=True" in window
