# Created: 2026-07-20
# Last reused/audited: 2026-07-20
# Authority basis: review blocker C1 (docs scratchpad FIX_PLAN.md §C1) — the
#   global-auction Window-B finalize must NOT take an UNBOUNDED world-writer
#   mutex acquire on the decision-reactor thread after a venue call may have
#   begun. A stalled world-DB writer previously froze the whole reactor
#   indefinitely. On timeout the possibly-side-effecting command is retained for
#   the off-thread edli_command_recovery reconciler, never blocked on.
"""Behavioral antibody for reactor post-side-effect finalize bounding (C1).

FAILS on pre-fix head: the side-effect-possible finalize (wait_ms is None) took
an unconditional ``mutex.acquire()`` with no timeout, so with the world-writer
mutex held the worker thread never returns and ``done.wait`` times out.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from src.events import reactor as reactor_mod
from src.events.reactor import (
    EventSubmissionReceipt,
    OpportunityEventReactor,
    ReactorResult,
    _POST_SUBMIT_WORLD_WRITE_LOCK_MUST_SETTLE,
    _POST_SUBMIT_WORLD_WRITE_LOCK_RETRY,
)
from src.sizing.portfolio_reservation import PortfolioReservationLedger

# Small monotonic budget so a bounded acquire resolves fast; the worker join
# timeout below is far larger, so a bounded return always beats it while an
# UNBOUNDED (pre-fix) acquire never does.
_BUDGET_MS = 100
_JOIN_TIMEOUT_S = 5.0


class _StallStubReactor:
    """Minimal carrier for ``_finalize_deferred_event_unit``'s timeout path.

    The side-effect-possible timeout branch returns before touching any reactor
    collaborator except the reservation ledger (via ``_finalize_reservation``),
    so this lightweight stub keeps the antibody focused on the acquire bound and
    off the reactor's heavy construction graph.
    """

    _finalize_reservation = OpportunityEventReactor._finalize_reservation

    def __init__(self, ledger: PortfolioReservationLedger) -> None:
        self._submit = SimpleNamespace(reservation_ledger=ledger)


def _finalize_in_worker(stub, event, receipt, *, wait_ms):
    """Run the real finalize on a worker thread; return (done_event, box)."""
    result = ReactorResult()
    box: dict[str, object] = {"result": result, "ret": None, "elapsed": None}
    done = threading.Event()

    def _run() -> None:
        started = time.monotonic()
        try:
            box["ret"] = OpportunityEventReactor._finalize_deferred_event_unit(
                stub,
                event,
                receipt,
                decision_time=datetime.now(timezone.utc),
                result=result,
                wait_ms=wait_ms,
            )
        finally:
            box["elapsed"] = time.monotonic() - started
            done.set()

    threading.Thread(target=_run, daemon=True).start()
    return done, box


def test_side_effect_possible_finalize_returns_within_budget_when_writer_stalled(
    monkeypatch,
):
    """venue_call_started=True + stalled world writer -> bounded return, must-settle
    lane, reservation RETAINED (never un-reserve possibly-committed capital)."""
    monkeypatch.setenv("ZEUS_REACTOR_CLAIM_BUSY_TIMEOUT_MS", str(_BUDGET_MS))
    stall_lock = threading.Lock()
    monkeypatch.setattr(reactor_mod, "world_write_mutex", lambda: stall_lock)

    ledger = PortfolioReservationLedger()
    ledger.reserve("evt-c1", "TOKYO", 16.0)
    stub = _StallStubReactor(ledger)
    event = SimpleNamespace(event_id="evt-c1")
    receipt = EventSubmissionReceipt(
        submitted=False,
        event_id="evt-c1",
        venue_call_started=True,  # a venue call may have begun -> side-effect-possible
    )

    # Stall the world writer for the whole finalize attempt.
    assert stall_lock.acquire(timeout=1.0)
    try:
        # wait_ms=None is exactly how process_pending finalizes a side-effect
        # -possible winner (reactor.py: wait_ms=None if side_effect_possible).
        done, box = _finalize_in_worker(stub, event, receipt, wait_ms=None)
        completed = done.wait(timeout=_JOIN_TIMEOUT_S)
    finally:
        stall_lock.release()

    # Pre-fix head: the unbounded acquire never returns while the lock is held,
    # so `completed` is False and this antibody fails (as required).
    assert completed, (
        "reactor finalize did not return within the join window while the world "
        "writer was stalled: the post-side-effect acquire is UNBOUNDED (C1)"
    )
    result = box["result"]
    assert box["ret"] is False  # winner left unfinalized -> caller stops, retries
    assert _POST_SUBMIT_WORLD_WRITE_LOCK_MUST_SETTLE in result.rejection_reasons
    assert result.retried == 1
    # RETAIN: possibly-committed in-flight capital must NOT be un-reserved on the
    # must-settle handoff (portfolio_reservation.py SAFETY DIRECTION).
    assert list(ledger) == [("TOKYO", 16.0)]
    # Returned well within budget, not near the join timeout.
    assert box["elapsed"] is not None and box["elapsed"] < _JOIN_TIMEOUT_S / 2


def test_no_submit_loser_finalize_still_rolls_back_and_retries(monkeypatch):
    """Contrast: a NO_SUBMIT loser under a stalled writer keeps the pre-C1 path —
    bounded acquire, plain retry reason, provisional reservation rolled back."""
    monkeypatch.setenv("ZEUS_REACTOR_CLAIM_BUSY_TIMEOUT_MS", str(_BUDGET_MS))
    stall_lock = threading.Lock()
    monkeypatch.setattr(reactor_mod, "world_write_mutex", lambda: stall_lock)

    ledger = PortfolioReservationLedger()
    ledger.reserve("evt-loser", "OSAKA", 8.0)
    stub = _StallStubReactor(ledger)
    event = SimpleNamespace(event_id="evt-loser")
    receipt = EventSubmissionReceipt(
        submitted=False,
        event_id="evt-loser",
        # no venue call, NO_SUBMIT default -> not side-effect-possible
    )

    assert stall_lock.acquire(timeout=1.0)
    try:
        # A loser is finalized with an explicit bounded wait_ms (never None).
        done, box = _finalize_in_worker(stub, event, receipt, wait_ms=_BUDGET_MS)
        completed = done.wait(timeout=_JOIN_TIMEOUT_S)
    finally:
        stall_lock.release()

    assert completed
    result = box["result"]
    assert box["ret"] is False
    assert _POST_SUBMIT_WORLD_WRITE_LOCK_RETRY in result.rejection_reasons
    assert _POST_SUBMIT_WORLD_WRITE_LOCK_MUST_SETTLE not in result.rejection_reasons
    assert result.retried == 1
    # No side effect -> the provisional reserve is rolled back so it can't inflate
    # a later sibling's committed capital.
    assert list(ledger) == []
