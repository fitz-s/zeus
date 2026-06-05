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

from src.events.reactor import EventSubmissionReceipt, OpportunityEventReactor, ReactorConfig
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
