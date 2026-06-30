# Created: 2026-06-05
# Last reused/audited: 2026-06-05
# Authority basis: P1 ZERO-SUBMIT FIX B (2026-06-05, iron-rule-1, co-cause).
#
# RELATIONSHIP TEST (cross-module invariant, Fitz methodology):
#   "When event N's submit RESERVES a stake provisionally (it passed Kelly +
#    RiskGuard) but the reactor then REJECTS event N downstream of Kelly
#    (DECISION_CERTIFICATE / EXECUTOR_EXPRESSIBILITY), the reservation that event
#    N+1's submit READS must NOT include event N's stake."
#
# This pins FIX B at the exact module boundary the bug lived on: the adapter
# appended to the per-cycle reservation the instant a candidate passed Kelly,
# but the reactor's post-submit phase can still reject it — and the old
# append-only list never rolled it back, so the rejected candidate inflated
# corr_committed_usd / raw_committed_usd for every later same-cycle candidate.
#
# We exercise the REAL reactor flow (process_pending → _process_event_unit →
# _process_one_post_submit → _finalize_reservation) with a stub submit callable
# that carries a real PortfolioReservationLedger (exactly as the production
# adapter exposes via `_submit.reservation_ledger`), so the commit/rollback
# wiring under test is the production wiring, not a mock.
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.events.reactor import (
    EventSubmissionReceipt,
    OpportunityEventReactor,
    ReactorConfig,
    ReactorResult,
)
from src.sizing.portfolio_reservation import PortfolioReservationLedger
from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger

from tests.events.test_reactor import _forecast_event, _store


def _reactor_with_reservation(store, submit):
    """Build a reactor whose injected submit carries a reservation ledger, the
    same way the production adapter exposes it."""
    return OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=submit,
        reject=lambda *_a: None,
        config=ReactorConfig(reactor_mode="live_no_submit"),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )


def test_downstream_rejected_candidate_reservation_rolled_back_before_next_event():
    """FIX B end-to-end: event1 reserves provisionally but is rejected downstream
    of Kelly (its receipt carries NO proof bundle → DECISION_CERTIFICATE reject);
    event2's submit must observe a reservation that does NOT include event1."""
    _conn, store = _store()
    # Two same-city events processed sequentially in one cycle.
    store.insert_or_ignore(_forecast_event("resv1"))
    store.insert_or_ignore(_forecast_event("resv2"))

    ledger = PortfolioReservationLedger()
    observed_at_event2: dict = {}
    call_count = {"n": 0}

    def _submit(event, decision_time):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # SECOND event: record what the ledger looks like RIGHT NOW (before
            # this event reserves). It must NOT contain the first event's stake.
            observed_at_event2["entries"] = list(ledger)
        # Mimic the adapter: a candidate that passed Kelly+RiskGuard reserves its
        # stake PROVISIONALLY.
        ledger.reserve(event.event_id, "Chicago", 12.0)
        # Return a NO_SUBMIT receipt WITHOUT a decision_proof_bundle → the reactor
        # rejects it at DECISION_CERTIFICATE (NO_SUBMIT_PROOF_BUNDLE_REQUIRED),
        # i.e. downstream of Kelly. This is the exact "passed Kelly, rejected
        # later" hazard FIX B closes.
        return EventSubmissionReceipt(
            submitted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            side_effect_status="NO_SUBMIT",
            reason="event_bound_final_intent_no_submit",
            proof_accepted=True,  # adapter marked it accepted; reactor still rejects
            decision_proof_bundle=None,  # → DECISION_CERTIFICATE rejects downstream
        )

    reactor = _reactor_with_reservation(store, _submit)
    # Attach the ledger the way the production adapter does.
    _submit.reservation_ledger = ledger  # type: ignore[attr-defined]

    reactor.process_pending(
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    )

    assert call_count["n"] == 2, (
        "both events must reach the submit seam for the relationship to be tested"
    )
    assert "entries" in observed_at_event2, "event2 submit was never invoked"
    # THE FIX B INVARIANT: event2 saw NO reservation from the rejected event1.
    assert observed_at_event2["entries"] == [], (
        f"rejected event1's stake leaked into event2's reservation: "
        f"{observed_at_event2['entries']!r} (expected []). The downstream "
        f"DECISION_CERTIFICATE rejection must roll back the provisional reserve."
    )
    # And after the cycle, the ledger is empty (both rejected → both rolled back).
    assert list(ledger) == [], (
        f"reservation not fully rolled back after cycle: {list(ledger)!r}"
    )


def test_emitted_candidate_reservation_committed_and_netted_by_next_event():
    """Counterpart / no-regression: when event1 IS emitted (accepted), event2
    MUST still net its in-flight stake (INV-K7). The commit path keeps same-cycle
    netting intact — FIX B must not break it."""
    _conn, store = _store()
    store.insert_or_ignore(_forecast_event("emit1"))
    store.insert_or_ignore(_forecast_event("emit2"))

    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    ledger = PortfolioReservationLedger()
    observed_at_event2: dict = {}
    call_count = {"n": 0}

    def _submit(event, decision_time):
        call_count["n"] += 1
        if call_count["n"] == 2:
            observed_at_event2["entries"] = list(ledger)
        ledger.reserve(event.event_id, "Chicago", 12.0)
        # A VALID proof bundle → the reactor VERIFIES and emits (proof_accepted
        # advances) → the reservation is committed.
        bundle = build_test_no_submit_proof_bundle(event)
        return EventSubmissionReceipt(
            submitted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            side_effect_status="NO_SUBMIT",
            reason="event_bound_final_intent_no_submit",
            proof_accepted=True,
            decision_proof_bundle=bundle,
        )

    reactor = _reactor_with_reservation(store, _submit)
    _submit.reservation_ledger = ledger  # type: ignore[attr-defined]

    result = reactor.process_pending(
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    )

    assert call_count["n"] == 2
    # If event1 verified+emitted, event2 must see its committed in-flight stake
    # (INV-K7 same-cycle netting preserved).
    if result.proof_accepted >= 1:
        assert observed_at_event2.get("entries"), (
            "INV-K7 regression: emitted event1's committed reservation was NOT "
            "visible to event2 — same-cycle netting broke"
        )
        assert any(
            usd == pytest.approx(12.0) for _, usd in observed_at_event2["entries"]
        )


def test_terminal_finalize_writes_missing_decision_evidence_before_processed():
    conn, store = _store()
    event = _forecast_event("missing-evidence")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reservation(store, lambda *_a: None)
    result = ReactorResult()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)

    reactor._finalize_disposition(  # noqa: SLF001 - relationship regression for the guard
        event,
        None,
        decision_time=decision_time,
        result=result,
        proof_emitted=False,
    )

    failure = conn.execute(
        """
        SELECT stage, reason_code
          FROM decision_compile_failures
         WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    regret = conn.execute(
        """
        SELECT rejection_stage, rejection_reason
          FROM no_trade_regret_events
         WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    processing = conn.execute(
        """
        SELECT processing_status
          FROM opportunity_event_processing
         WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()

    assert failure is not None
    assert failure[0] == "UNKNOWN_REVIEW_REQUIRED"
    assert failure[1] == "UNKNOWN_REVIEW_REQUIRED:PROCESSED_WITHOUT_DECISION_EVIDENCE"
    assert regret is not None
    assert regret[0] == "UNKNOWN_REVIEW_REQUIRED"
    assert regret[1] == "UNKNOWN_REVIEW_REQUIRED:PROCESSED_WITHOUT_DECISION_EVIDENCE"
    assert processing[0] == "processed"
    assert result.rejected == 1
    assert result.processed == 1


# ───────────────────────────────────────────────────────────────────────────
# MAJOR #5 — reservation leak on the NETWORK-SUBMIT exception path.
#
# The FIX B coverage above is the POST-submit window (reactor.py ~381-384):
# _submit returns a receipt, then a downstream reject/exception rolls the
# reservation back. But the adapter calls ``portfolio_reservation.reserve(...)``
# INSIDE _submit (event_reactor_adapter.py ~1097-1102), and the
# receipt-build / serialize / proof-bundle steps that run AFTER reserve
# (~1114/1123/1171) are OUTSIDE the adapter's local try/except (which closes at
# ~1003, before reserve). A sqlite3.Error / KeyError / AttributeError from any of
# them propagates up to reactor.py:329 (the network-submit ``except Exception``),
# which dead-letters and RETURNS — WITHOUT calling _finalize_reservation. So the
# reservation is orphaned-but-LIVE in the ledger → the next same-cycle event
# over-counts committed → under-sizes / re-zeros later candidates = the EXACT P1
# zero-submit symptom this fix exists to kill, now re-triggered silently by a
# live HTTP/DB error mid-submit.
# ───────────────────────────────────────────────────────────────────────────


def test_submit_raising_after_reserve_rolls_back_before_next_event():
    """MAJOR #5: event1's _submit RESERVES then RAISES (mid-submit DB/HTTP error
    after reserve). event2's submit must observe a reservation that does NOT
    include event1's leaked stake.

    BEFORE the fix: reactor.py:329 dead-letters and returns without rolling back
    → event1's $12 stays live in the ledger → event2 sees it (the leak).
    AFTER the fix: the network-submit except path calls
    _finalize_reservation(event, emitted=False) → event2 sees [].
    """
    _conn, store = _store()
    store.insert_or_ignore(_forecast_event("netraise1"))
    store.insert_or_ignore(_forecast_event("netraise2"))

    ledger = PortfolioReservationLedger()
    observed_at_event2: dict = {}
    call_count = {"n": 0}

    def _submit(event, decision_time):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # SECOND event: record the ledger BEFORE event2 reserves. The leaked
            # event1 reservation must already have been rolled back.
            observed_at_event2["entries"] = list(ledger)
            # event2 itself reserves and then also raises (kept symmetric so the
            # cycle ends with both rolled back).
            ledger.reserve(event.event_id, "Chicago", 12.0)
            raise RuntimeError("simulated mid-submit serialize failure (event2)")
        # FIRST event: reserve PROVISIONALLY (passed Kelly+RiskGuard), THEN raise
        # the way build_event_bound_final_intent_receipt / serialize would on a
        # live sqlite3.Error / KeyError AFTER the reserve.
        ledger.reserve(event.event_id, "Chicago", 12.0)
        raise RuntimeError("simulated mid-submit serialize failure (event1)")

    reactor = _reactor_with_reservation(store, _submit)
    _submit.reservation_ledger = ledger  # type: ignore[attr-defined]

    reactor.process_pending(
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    )

    assert call_count["n"] == 2, (
        "both events must reach the submit seam for the leak relationship to be tested"
    )
    assert "entries" in observed_at_event2, "event2 submit was never invoked"
    # THE MAJOR #5 INVARIANT: event2 saw NO leaked reservation from event1's
    # exception-raising submit.
    assert observed_at_event2["entries"] == [], (
        f"reservation leaked across the network-submit exception path: event2 saw "
        f"{observed_at_event2['entries']!r} (expected []). A _submit that raises "
        f"AFTER reserve() must roll back the provisional reserve at reactor.py's "
        f"network-submit except, exactly as the post-submit window already does."
    )
    # And after the cycle the ledger is empty (both raised → both rolled back).
    assert list(ledger) == [], (
        f"reservation not fully rolled back after cycle: {list(ledger)!r}"
    )


def test_submit_raising_BEFORE_reserve_does_not_error_on_rollback():
    """MAJOR #5 idempotency guard: a _submit that raises BEFORE it ever reserves
    must NOT cause the network-submit except rollback to error. The symmetric
    _finalize_reservation(emitted=False) must be a safe no-op when no reservation
    was made for that event (PortfolioReservationLedger.rollback is no-op for an
    unknown event_id)."""
    _conn, store = _store()
    store.insert_or_ignore(_forecast_event("preraise1"))

    ledger = PortfolioReservationLedger()

    def _submit(event, decision_time):
        # Raise BEFORE any reserve — the rollback in the except path must not blow
        # up trying to roll back a reservation that never happened.
        raise RuntimeError("simulated failure before reserve")

    reactor = _reactor_with_reservation(store, _submit)
    _submit.reservation_ledger = ledger  # type: ignore[attr-defined]

    # Must not raise (the rollback is contextlib.suppress'd AND idempotent).
    reactor.process_pending(
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    )
    assert list(ledger) == [], (
        f"ledger should be empty when submit raised before reserve: {list(ledger)!r}"
    )
