# Created: 2026-06-05
# Last reused/audited: 2026-06-05
# Authority basis: P1 ZERO-SUBMIT FIX B (2026-06-05, iron-rule-1, co-cause) —
#   portfolio_reservation must reflect ONLY candidates the reactor EMITTED this
#   cycle; a candidate that passes Kelly+RiskGuard but is rejected downstream
#   (DECISION_CERTIFICATE / EXECUTOR_EXPRESSIBILITY) must NOT inflate
#   corr_committed_usd / raw_committed_usd for later same-cycle candidates.
"""Relationship tests for the per-cycle in-flight reservation lifecycle.

FIX B (co-cause of the P1 zero-submit defect): the reactor adapter appended to
``portfolio_reservation`` the moment a candidate passed Kelly+RiskGuard — BEFORE
the DECISION_CERTIFICATE compile that can still REJECT it. The list was
append-only (no pop/rollback), so a candidate rejected DOWNSTREAM of Kelly still
inflated ``corr_committed_usd`` / ``raw_committed_usd`` for every later
same-cycle candidate, compounding the budget exhaustion.

INVARIANT (the relationship under test): after a reactor cycle,
``Σ reservation == Σ stakes of candidates the reactor ACTUALLY EMITTED`` — never
includes a candidate rejected after Kelly. Events are processed STRICTLY
SEQUENTIALLY (``for event in events`` in ``process_pending``), each fully
resolving (accept → commit / reject → rollback) before the next event's
``_submit`` reads the reservation, so a provisional reserve is always finalized
before the next read.
"""

from __future__ import annotations

import pytest

from src.sizing.portfolio_reservation import PortfolioReservationLedger


# ── Controller-boundary invariant ────────────────────────────────────────────

def test_committed_reservation_is_visible_to_next_candidate():
    """A reserved+committed stake is visible (counted) to the next candidate via
    the iterable read interface (drop-in for the old list[tuple[str,float]])."""
    led = PortfolioReservationLedger()
    led.reserve("ev1", "New York City", 12.0)
    led.commit("ev1")
    assert list(led) == [("New York City", 12.0)]
    # raw sum (the read site in the adapter) sees the committed stake
    assert sum(usd for _, usd in led) == pytest.approx(12.0)


def test_rolled_back_reservation_is_invisible_to_next_candidate():
    """THE FIX B INVARIANT: a candidate that reserved (passed Kelly+RiskGuard)
    but was rejected downstream must roll back — its stake must NOT be counted
    for the next same-cycle candidate."""
    led = PortfolioReservationLedger()
    # event1 passes Kelly+RiskGuard → provisional reserve.
    led.reserve("ev1", "New York City", 12.0)
    # ... then rejected at DECISION_CERTIFICATE downstream → rollback.
    led.rollback("ev1")
    # event2 must see ZERO committed from event1.
    assert list(led) == []
    assert sum(usd for _, usd in led) == pytest.approx(0.0)


def test_provisional_reserve_is_netted_before_finalization():
    """Between reserve and commit/rollback, the provisional stake IS visible —
    this preserves the INV-K7 same-cycle netting the accepted-bet path requires
    (the next event must net an in-flight sibling even pre-commit, since events
    resolve sequentially)."""
    led = PortfolioReservationLedger()
    led.reserve("ev1", "New York City", 12.0)
    # Provisional (not yet committed) — still visible for in-flight netting.
    assert sum(usd for _, usd in led) == pytest.approx(12.0)
    led.commit("ev1")
    assert sum(usd for _, usd in led) == pytest.approx(12.0)


def test_mixed_cycle_only_emitted_candidates_counted():
    """A full mini-cycle: ev1 emitted (commit), ev2 rejected (rollback), ev3
    emitted (commit). Σ reservation == ev1+ev3 only."""
    led = PortfolioReservationLedger()
    led.reserve("ev1", "New York City", 10.0); led.commit("ev1")
    led.reserve("ev2", "New York City", 7.0); led.rollback("ev2")
    led.reserve("ev3", "Chicago", 5.0); led.commit("ev3")
    total = sum(usd for _, usd in led)
    assert total == pytest.approx(15.0), (
        f"reservation counted a rejected candidate: Σ={total} (expected 15.0 = "
        f"ev1+ev3, NOT 22.0 which would include the rejected ev2)"
    )
    cities = sorted(c for c, _ in led)
    assert cities == ["Chicago", "New York City"]


def test_rollback_only_affects_named_event():
    """Rollback of one event must not disturb other reserved events."""
    led = PortfolioReservationLedger()
    led.reserve("ev1", "New York City", 10.0); led.commit("ev1")
    led.reserve("ev2", "New York City", 7.0)
    led.rollback("ev2")
    led.reserve("ev3", "New York City", 5.0); led.commit("ev3")
    assert sum(usd for _, usd in led) == pytest.approx(15.0)


def test_committed_is_immune_to_later_rollback():
    """A COMMITTED (emitted) stake can never be retroactively un-reserved — an
    emitted bet is real in-flight capital. ``rollback`` only removes a still-
    PROVISIONAL reservation. This is the safe direction (never under-count
    capital that is actually in flight)."""
    led = PortfolioReservationLedger()
    led.reserve("ev1", "New York City", 10.0)
    led.commit("ev1")
    led.rollback("ev1")  # late/spurious rollback after commit: IGNORED
    assert sum(usd for _, usd in led) == pytest.approx(10.0)


def test_finalize_unknown_event_is_safe():
    """Idempotency / defensive: commit or rollback of an event that never
    reserved must be a no-op (the reactor may call rollback on a reject path
    where Kelly failed before any reserve)."""
    led = PortfolioReservationLedger()
    led.reserve("ev1", "New York City", 10.0)
    led.commit("ev1")
    led.commit("ev1")  # double-commit: no-op
    led.rollback("never_seen")  # unknown event: no-op
    led.commit("also_never_seen")  # unknown event: no-op
    assert sum(usd for _, usd in led) == pytest.approx(10.0)
