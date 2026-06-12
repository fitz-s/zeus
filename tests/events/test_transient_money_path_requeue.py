# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 ~16:30Z — stale-decision-vs-fresh-book
#   races were TERMINAL. Live evidence: Miami 16:22:35Z cleared EVERY gate and aborted at
#   JIT recapture (SUBMIT_ABORTED_PRICE_MOVED: recaptured all-in 0.5136 > max 0.5025 +
#   0.0100 tolerance) — terminally consumed though EV at the NEW price was still strongly
#   positive (q_lcb 0.6776). NYC 16:22:33Z failed the certificate layer with
#   "PreSubmitRevalidated requires would_cross_book=false" (maker flavor of the SAME race).
#   Both now classify TRANSIENT → bounded requeue → next cycle RE-DECIDES with a fresh
#   book (never resubmits the same envelope; no venue order was placed in either reason).
"""RELATIONSHIP tests across the boundary

    adapter submit receipt reason -> reactor._reject_or_retry_post_submit
    -> _is_transient_money_path_reason -> requeue (bounded) vs terminal consume

The cross-module invariant: a price-race abort (taker PRICE_MOVED / maker
would_cross_book) must NOT terminally consume the opportunity — the event requeues
(bounded by MAX_EXECUTABLE_SNAPSHOT_RETRIES → dead-letter) so the next cycle
re-decides at the fresh price. Every OTHER certificate-build failure stays terminal.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.events.event_store import EventStore
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.events.reactor import (
    MAX_EXECUTABLE_SNAPSHOT_RETRIES,
    EventSubmissionReceipt,
    OpportunityEventReactor,
    _is_transient_money_path_reason,
)
from src.state.db import init_schema
from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger

_PRICE_MOVED_REASON = (
    "SUBMIT_ABORTED_PRICE_MOVED: recaptured all-in cost 0.513552 exceeds "
    "max_acceptable_price 0.502495 + bounded tolerance 0.010000"
)
_WOULD_CROSS_REASON = (
    "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:PreSubmitRevalidated requires would_cross_book=false"
)
_OTHER_CERT_REASON = "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:SOME_OTHER_ASSERTION_FAILED"


# ---------------------------------------------------------------------------
# Classifier unit pins
# ---------------------------------------------------------------------------

def test_price_moved_is_transient():
    assert _is_transient_money_path_reason(_PRICE_MOVED_REASON)


def test_would_cross_book_certificate_failure_is_transient():
    assert _is_transient_money_path_reason(_WOULD_CROSS_REASON)


def test_other_certificate_failures_stay_terminal():
    assert not _is_transient_money_path_reason(_OTHER_CERT_REASON)


def test_db_lock_certificate_failure_still_transient():
    assert _is_transient_money_path_reason(
        "EDLI_LIVE_CERTIFICATE_BUILD_FAILED: database is locked"
    )


def test_executable_snapshot_stale_still_transient():
    assert _is_transient_money_path_reason("EXECUTABLE_SNAPSHOT_STALE")


def test_empty_reason_not_transient():
    assert not _is_transient_money_path_reason(None)
    assert not _is_transient_money_path_reason("")


# ---------------------------------------------------------------------------
# Reactor-level relationship tests
# ---------------------------------------------------------------------------

def _payload(snapshot_id: str) -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-06-05",
        metric="high",
        source_id="ecmwf-open-data",
        source_run_id="run-1",
        cycle="00",
        track="ens",
        snapshot_id=snapshot_id,
        snapshot_hash=snapshot_id,
        captured_at="2026-06-04T04:10:00+00:00",
        available_at="2026-06-04T04:15:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="COMMITTED",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )


def _event(snapshot_id: str):
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"Chicago|2026-06-05|high|{snapshot_id}",
        source="forecast",
        observed_at="2026-06-04T04:10:00+00:00",
        available_at="2026-06-04T04:15:00+00:00",
        received_at="2026-06-04T04:16:00+00:00",
        causal_snapshot_id=snapshot_id,
        payload=_payload(snapshot_id),
        priority=100,
    )


def _reactor_with_reason(conn, store, reason: str) -> OpportunityEventReactor:
    """Reactor whose submit returns a receipt that fails the money-path proof with
    ``reason`` — the exact route the live PRICE_MOVED / would_cross_book receipts
    took (receipt reaches _receipt_money_path_blocker, the blocker surfaces
    ``receipt.reason``, and _reject_or_retry_post_submit classifies it)."""

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city="Chicago",
            target_date="2026-06-05",
            metric="high",
            trade_score_positive=False,  # money-path blocker fires -> receipt.reason
            reason=reason,
        )

    return OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda _e, _s, _r: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )


def _store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn, EventStore(conn)


def _status(conn, event_id: str) -> str:
    return conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event_id,),
    ).fetchone()[0]


_DT = datetime(2026, 6, 4, 18, 10, tzinfo=timezone.utc)


def test_price_moved_requeues_not_terminal():
    """ANTIBODY: a PRICE_MOVED abort leaves the event PENDING (retried), never
    'processed' — the next cycle re-decides at the fresh price."""
    conn, store = _store()
    event = _event("snap-pm")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _PRICE_MOVED_REASON)

    result = reactor.process_pending(decision_time=_DT, limit=10)

    assert result.retried == 1
    assert result.processed == 0
    assert _status(conn, event.event_id) == "pending", (
        "PRICE_MOVED must requeue (transient), not terminally consume the opportunity"
    )


def test_would_cross_book_requeues_not_terminal():
    """ANTIBODY: the maker flavor (post-only limit crossed because the book moved)
    requeues for a fresh-book re-decision, same as the taker PRICE_MOVED."""
    conn, store = _store()
    event = _event("snap-wcb")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _WOULD_CROSS_REASON)

    result = reactor.process_pending(decision_time=_DT, limit=10)

    assert result.retried == 1
    assert _status(conn, event.event_id) == "pending"


def test_other_certificate_failure_stays_terminal():
    """Other certificate build failures keep today's terminal-consume semantics."""
    conn, store = _store()
    event = _event("snap-other")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _OTHER_CERT_REASON)

    result = reactor.process_pending(decision_time=_DT, limit=10)

    assert result.retried == 0
    assert _status(conn, event.event_id) == "processed"


def test_price_moved_retries_bounded_then_dead_letter():
    """ANTIBODY (bound): the requeue is capped by MAX_EXECUTABLE_SNAPSHOT_RETRIES —
    a persistently moving book dead-letters instead of retrying forever."""
    conn, store = _store()
    event = _event("snap-cap")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _PRICE_MOVED_REASON)

    for _ in range(MAX_EXECUTABLE_SNAPSHOT_RETRIES + 2):
        reactor.process_pending(decision_time=_DT, limit=10)
        if _status(conn, event.event_id) == "dead_letter":
            break

    assert _status(conn, event.event_id) == "dead_letter", (
        f"after {MAX_EXECUTABLE_SNAPSHOT_RETRIES}+ attempts the event must dead-letter, "
        "not retry unbounded"
    )
    attempts = store.attempt_count(event.event_id)
    assert attempts >= MAX_EXECUTABLE_SNAPSHOT_RETRIES


def test_transient_exhaustion_dead_letter_carries_honest_category():
    """ANTIBODY (external review 2026-06-11): money-path transients share the
    executable-snapshot retry disposition, so exhaustion used to dead-letter as
    EXECUTABLE_SNAPSHOT_BLOCKED / 'snapshot not captured' — masking the actual
    submit-race category and hiding the churn from the ledgers. The terminal
    dead-letter row must carry the LAST transient reason; the generic snapshot
    label is reserved for genuinely uncapturable snapshots."""
    conn, store = _store()
    event = _event("snap-honest-label")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _PRICE_MOVED_REASON)

    for _ in range(MAX_EXECUTABLE_SNAPSHOT_RETRIES + 2):
        reactor.process_pending(decision_time=_DT, limit=10)
        if _status(conn, event.event_id) == "dead_letter":
            break

    row = conn.execute(
        "SELECT failure_stage, error_message FROM event_dead_letters WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row is not None
    failure_stage, error_message = row[0], row[1]
    assert failure_stage == "MONEY_PATH_TRANSIENT_EXHAUSTED", (
        "transient exhaustion must NOT be labeled EXECUTABLE_SNAPSHOT_BLOCKED: "
        f"got {failure_stage!r}"
    )
    assert "SUBMIT_ABORTED_PRICE_MOVED" in (error_message or ""), (
        "the dead-letter must carry the last transient reason: "
        f"got {error_message!r}"
    )


_MODE_FLIPPED_REASON = (
    "SUBMIT_ABORTED_MODE_FLIPPED:SUBMIT_ABORTED_MODE_FLIPPED:proof_mode=MAKER:"
    "fresh_mode=TAKER:fresh_bid=0.73:fresh_ask=0.77"
)


def test_mode_flipped_is_transient():
    # Third flavor of the stale-decision-vs-fresh-book race (live 2026-06-11
    # 17:23:33Z: four cities priced MAKER into an empty ask; the book grew a live
    # ask by submit; P0-1 refused the stale-mode plan and the events were
    # terminally consumed while the fresh ask carried +6..+19% conservative EV).
    # The requeue re-decides fresh and prices TAKER from the start.
    assert _is_transient_money_path_reason(_MODE_FLIPPED_REASON)


def test_mode_flipped_no_submit_state_requeues_not_consumed():
    """ANTIBODY (live 2026-06-11 17:30:20Z, Busan x2): MODE_FLIPPED arrives as a
    VERIFIED NO_SUBMIT *state* (P0-1), not a rejection — it bypassed the
    _reject_or_retry_post_submit classifier and was terminally consumed as
    proof_accepted while the fresh ask carried +6.7% conservative EV
    (q_lcb 0.828 vs ask 0.77). The NO_SUBMIT branch must classify transient
    reasons BEFORE persisting the receipt: the event requeues PENDING and the
    aborted attempt writes no receipt."""
    conn, store = _store()
    event = _event("snap-mf")
    store.insert_or_ignore(event)

    def _submit(ev, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=ev.event_id,
            causal_snapshot_id=ev.causal_snapshot_id,
            city="Busan",
            target_date="2026-06-13",
            metric="high",
            side_effect_status="NO_SUBMIT",
            trade_score_positive=True,
            reason=(
                "SUBMIT_ABORTED_MODE_FLIPPED:SUBMIT_ABORTED_MODE_FLIPPED:"
                "proof_mode=MAKER:fresh_mode=TAKER:fresh_bid=0.64:fresh_ask=0.77"
            ),
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda _e, _s, _r: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )
    result = reactor.process_pending(decision_time=_DT, limit=10)

    assert result.retried == 1
    assert result.proof_accepted == 0, (
        "a transient-reason NO_SUBMIT receipt must NOT count as an accepted proof"
    )
    assert _status(conn, event.event_id) == "pending"
    n_receipts = conn.execute("SELECT count(*) FROM edli_no_submit_receipts").fetchone()[0]
    assert n_receipts == 0, "the aborted attempt must not persist a receipt"


def test_pre_submit_error_terminal_is_visible_rejection_not_silent_proof():
    """ANTIBODY (live 2026-06-12 00:52-01:13Z): five maker final intents died
    ExecutionReceipt status=PRE_SUBMIT_ERROR (executor pre-venue guard) and were
    silently counted proof_accepted — no regret row, no dead letter; the wall
    was only discoverable by reading certificate payloads. A failed-without-
    side-effect terminal (REJECTED / PRE_SUBMIT_ERROR) must route through the
    regret ledger with the executor's reason and never count as an accepted
    proof. TIMEOUT/POST_SUBMIT_UNKNOWN stay proof_accepted (reconcile owns
    possible live orders).

    Scope: this pins the ROUTING relationship (terminal status -> regret, not
    proof_accepted). Certificate-graph persistence/verification is owned by
    tests/decision_kernel — the ledger here is a recorder so the fixture does
    not have to reconstruct the full live certificate parent graph."""
    from datetime import datetime, timezone

    from src.decision_kernel import claims
    from src.decision_kernel.certificate import build_certificate

    now = datetime(2026, 6, 12, 1, 13, tzinfo=timezone.utc)
    receipt_cert = build_certificate(
        certificate_type=claims.EXECUTION_RECEIPT,
        semantic_key="execution-receipt:evt-pse",
        claim_type=claims.EXECUTION_RECEIPT,
        mode="LIVE",
        decision_time=now,
        source_available_at=now,
        agent_received_at=now,
        persisted_at=now,
        payload={"status": "PRE_SUBMIT_ERROR", "reason_code": "EXECUTOR_PRE_VENUE_REJECTED:test"},
        parent_edges=(),
        parent_certificates=(),
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )

    conn, store = _store()
    event = _event("snap-pse")
    store.insert_or_ignore(event)

    def _submit(ev, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=ev.event_id,
            causal_snapshot_id=ev.causal_snapshot_id,
            city="Chicago",
            target_date="2026-06-05",
            metric="high",
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="fam-1",
            fdr_hypothesis_count=22,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=10.0,
            kelly_cost_basis_id="cost_basis:abc",
            final_intent_id="intent-1",
            side_effect_status="PRE_SUBMIT_ERROR",
            reason="EXECUTOR_PRE_VENUE_REJECTED:FinalExecutionIntent event_id does not match executable snapshot",
            decision_proof_bundle=(receipt_cert,),
        )

    from src.events.reactor import ReactorConfig

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda _e, _s, _r: None,
        # Live posture: with submit DISABLED an earlier expressibility check
        # consumes LIVE terminal statuses; the silent-proof hole exists only on
        # the real-submit path.
        config=ReactorConfig(reactor_mode="live", real_order_submit_enabled=True),
        regret_ledger=NoTradeRegretLedger(conn),
    )

    class _RecorderLedger:
        def __init__(self):
            self.persisted = []

        def persist_all(self, certificates):
            self.persisted.extend(certificates)

        def persist_failures(self, failures):
            pass

    recorder = _RecorderLedger()
    reactor._decision_certificate_ledger = recorder

    result = reactor.process_pending(decision_time=_DT, limit=10)

    assert recorder.persisted, "the execution receipt certificates must still be persisted"

    assert result.proof_accepted == 0, (
        "a PRE_SUBMIT_ERROR terminal must NOT count as an accepted proof"
    )
    assert result.rejected >= 1
    row = conn.execute(
        "SELECT rejection_stage, rejection_reason FROM no_trade_regret_events LIMIT 1"
    ).fetchone()
    assert row is not None, "the rejection must be visible in the regret ledger"
    assert row["rejection_stage"] == "EXECUTION_RECEIPT"
    assert "EXECUTOR_PRE_VENUE_REJECTED" in row["rejection_reason"]
