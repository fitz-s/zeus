# Created: 2026-06-11
# Last reused or audited: 2026-06-12
# Authority basis: operator directive 2026-06-11 ~16:30Z — stale-decision-vs-fresh-book
#   races were TERMINAL. Live evidence: Miami 16:22:35Z cleared EVERY gate and aborted at
#   JIT recapture (SUBMIT_ABORTED_PRICE_MOVED: recaptured all-in 0.5136 > max 0.5025 +
#   0.0100 tolerance) — terminally consumed though EV at the NEW price was still strongly
#   positive (q_lcb 0.6776). NYC 16:22:33Z failed the certificate layer with
#   "PreSubmitRevalidated requires would_cross_book=false" (maker flavor of the SAME race).
#   Both now classify TRANSIENT → requeue → next cycle RE-DECIDES with a fresh
#   book (never resubmits the same envelope; no venue order was placed in either reason).
#
#   REWRITTEN 2026-06-12 (operator law "no caps"; "重试次数不是市场事实"): the old
#   MAX_EXECUTABLE_SNAPSHOT_RETRIES=8 attempt-cap terminalization is DELETED. A
#   transient requeues INDEFINITELY until an EVENT HORIZON fires (timeliness floor
#   past / operator disarm), labeled MONEY_PATH_HORIZON_EXPIRED — never an attempt
#   count. The cap-based tests below are rewritten to the horizon design; the
#   classifier pins and the riskguard-requeue antibodies are preserved.
"""RELATIONSHIP tests across the boundary

    adapter submit receipt reason -> reactor._reject_or_retry_post_submit
    -> _is_transient_money_path_reason -> requeue (horizon-bounded) vs terminal consume

The cross-module invariant: a price-race abort (taker PRICE_MOVED / maker
would_cross_book) must NOT terminally consume the opportunity — the event requeues
(NO attempt cap; only an EVENT HORIZON terminalizes) so the next cycle re-decides
at the fresh price. Every OTHER certificate-build failure stays terminal.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.events.event_store import EventStore
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.events.reactor import (
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


def test_pre_submit_book_authority_gap_is_transient():
    assert _is_transient_money_path_reason(
        "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:PRE_SUBMIT_BOOK_AUTHORITY_MISSING"
    )
    assert _is_transient_money_path_reason(
        "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:PRE_SUBMIT_BOOK_AUTHORITY_STALE"
    )
    assert not _is_transient_money_path_reason(
        "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:PRE_SUBMIT_BOOK_AUTHORITY_INCOMPLETE"
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
# Chicago 2026-06-05 strictly-past-in-tz boundary is 2026-06-06T05:00Z; this is
# after it, so the timeliness horizon has expired for the test event.
_DT_HORIZON_PAST = datetime(2026, 6, 7, 0, 0, tzinfo=timezone.utc)


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


def test_price_moved_requeues_indefinitely_until_timeliness_horizon():
    """ANTIBODY (operator law 2026-06-12, REWRITTEN from the cap test): a
    persistently moving book is NOT terminalized by an attempt count. The event
    requeues across many cycles (far past the old cap of 8) while it is still
    timely, and only dead-letters when its EVENT HORIZON (timeliness floor) has
    passed — labeled MONEY_PATH_HORIZON_EXPIRED, never an attempt count."""
    conn, store = _store()
    event = _event("snap-horizon")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _PRICE_MOVED_REASON)

    # 20 timely cycles (>2x the old cap): the event requeues, never dead-letters.
    for i in range(20):
        result = reactor.process_pending(decision_time=_DT, limit=10)
        assert result.dead_lettered == 0, f"cycle {i}: a timely transient must never dead-letter by count"
        assert _status(conn, event.event_id) == "pending", f"cycle {i}: still pending (no cap)"

    # The timeliness horizon passes. Drive the requeue disposition at the past
    # decision_time (in production fetch_pending's read floor + archive sweep also
    # reclaim it; the explicit terminal here is the honest WHY label).
    from src.events.reactor import ReactorResult

    reactor._transient_requeue_reasons[event.event_id] = _PRICE_MOVED_REASON
    res = ReactorResult()
    reactor._finalize_disposition(
        event,
        "RETRY_EXECUTABLE_SNAPSHOT_PENDING",
        decision_time=_DT_HORIZON_PAST,
        result=res,
    )
    assert res.dead_lettered == 1
    assert _status(conn, event.event_id) == "dead_letter"


def test_horizon_dead_letter_carries_honest_horizon_and_cause():
    """ANTIBODY (REWRITTEN from the exhaustion-label test): when a transient
    terminalizes at its EVENT HORIZON the dead-letter must carry the horizon
    (MARKET_VENUE_CLOSED — the F1 12:00-UTC venue close, which precedes the
    local-day floor for Chicago 2026-06-05 at the 2026-06-07 decision_time) AND the
    last honest transient cause — never an EXECUTABLE_SNAPSHOT_BLOCKED mask and
    never an attempt count."""
    conn, store = _store()
    event = _event("snap-honest-label")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _PRICE_MOVED_REASON)

    reactor.process_pending(decision_time=_DT, limit=10)  # one timely requeue
    assert _status(conn, event.event_id) == "pending"

    from src.events.reactor import ReactorResult

    reactor._transient_requeue_reasons[event.event_id] = _PRICE_MOVED_REASON
    res = ReactorResult()
    reactor._finalize_disposition(
        event,
        "RETRY_EXECUTABLE_SNAPSHOT_PENDING",
        decision_time=_DT_HORIZON_PAST,
        result=res,
    )

    row = conn.execute(
        "SELECT failure_stage, error_message FROM event_dead_letters WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row is not None
    failure_stage, error_message = row[0], row[1]
    assert failure_stage == "MONEY_PATH_HORIZON_EXPIRED", (
        "horizon terminal must be labeled MONEY_PATH_HORIZON_EXPIRED, never the "
        f"old count-based MONEY_PATH_TRANSIENT_EXHAUSTED: got {failure_stage!r}"
    )
    assert "MARKET_VENUE_CLOSED" in (error_message or ""), error_message
    assert "SUBMIT_ABORTED_PRICE_MOVED" in (error_message or ""), (
        "the dead-letter must carry the last transient cause: "
        f"got {error_message!r}"
    )
    assert "attempt" not in (error_message or "").lower(), (
        "no attempt count may appear in the terminal evidence"
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


def test_pre_submit_db_lock_error_requeues_without_regret():
    """A pre-venue DB lock has no venue side effect and must retry next cycle.

    Ordinary PRE_SUBMIT_ERROR receipts remain visible terminal rejections in the
    companion test above; this pins the narrower sqlite-lock transient path.
    """
    from datetime import datetime, timezone

    from src.decision_kernel import claims
    from src.decision_kernel.certificate import build_certificate

    now = datetime(2026, 6, 19, 11, 13, tzinfo=timezone.utc)
    receipt_cert = build_certificate(
        certificate_type=claims.EXECUTION_RECEIPT,
        semantic_key="execution-receipt:evt-pse-lock",
        claim_type=claims.EXECUTION_RECEIPT,
        mode="LIVE",
        decision_time=now,
        source_available_at=now,
        agent_received_at=now,
        persisted_at=now,
        payload={
            "status": "PRE_SUBMIT_ERROR",
            "reason_code": "pre_submit_db_locked_transient: database is locked",
        },
        parent_edges=(),
        parent_certificates=(),
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )

    conn, store = _store()
    event = _event("snap-pse-lock")
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
            reason="pre_submit_db_locked_transient: database is locked",
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

    reactor._decision_certificate_ledger = _RecorderLedger()

    result = reactor.process_pending(decision_time=_DT, limit=10)

    assert result.retried == 1
    assert result.rejected == 0
    assert result.proof_accepted == 0
    assert _status(conn, event.event_id) == "pending"
    assert conn.execute("SELECT count(*) FROM no_trade_regret_events").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# RiskGuard-block requeue antibodies (2026-06-12 riskguard-storm incident):
# transient risk_state writer gaps (daemon-restart boot windows, the
# chain_confirmed_zero poison-row crash, dependency_db_locked) fail the gate
# closed to RED and used to TERMINALLY consume every pending event — 1100+
# events burned in one day while risk truth was GREEN. A riskguard block must
# requeue (nothing submits while blocked) and only exhaust to a terminal
# label after the bounded retries.
# ---------------------------------------------------------------------------

def _reactor_with_riskguard(conn, store, gate) -> OpportunityEventReactor:
    return OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=gate,
        final_intent_submit=lambda _event, _dt: True,
        reject=lambda _e, _s, _r: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )


def test_riskguard_block_requeues_not_terminal():
    conn, store = _store()
    event = _event("snap-rg-1")
    store.insert_or_ignore(event)
    reactor = _reactor_with_riskguard(conn, store, lambda _e: False)

    result = reactor.process_pending(decision_time=_DT)

    assert result.retried == 1
    assert result.rejected == 0
    assert _status(conn, event.event_id) == "pending"
    # No terminal regret row was written for the requeue.
    n = conn.execute(
        "SELECT COUNT(*) FROM no_trade_regret_events WHERE rejection_reason='RISK_GUARD_BLOCKED'"
    ).fetchone()[0]
    assert n == 0


def test_riskguard_recovery_processes_requeued_event():
    """A transient RED storm ends; the surviving event processes normally."""
    conn, store = _store()
    event = _event("snap-rg-2")
    store.insert_or_ignore(event)
    blocked = {"value": True}
    reactor = _reactor_with_riskguard(conn, store, lambda _e: not blocked["value"])

    reactor.process_pending(decision_time=_DT)
    assert _status(conn, event.event_id) == "pending"

    blocked["value"] = False
    result = reactor.process_pending(decision_time=_DT)
    assert result.processed == 1
    assert _status(conn, event.event_id) == "processed"


def test_riskguard_block_requeues_indefinitely_then_horizon_terminal():
    """A sustained genuine RED halt requeues with NO attempt cap (nothing submits
    while blocked) and terminates only at the EVENT HORIZON — carrying the honest
    riskguard cause in a MONEY_PATH_HORIZON_EXPIRED label, never an attempt count."""
    conn, store = _store()
    event = _event("snap-rg-3")
    store.insert_or_ignore(event)
    reactor = _reactor_with_riskguard(conn, store, lambda _e: False)

    # 15 timely cycles (>old cap): requeues, never dead-letters by count.
    for i in range(15):
        result = reactor.process_pending(decision_time=_DT)
        assert result.dead_lettered == 0, f"cycle {i}: a timely riskguard block must not dead-letter by count"
        assert _status(conn, event.event_id) == "pending"

    from src.events.reactor import ReactorResult

    reactor._transient_requeue_reasons[event.event_id] = "RISK_GUARD_BLOCKED"
    res = ReactorResult()
    reactor._finalize_disposition(
        event,
        "RETRY_EXECUTABLE_SNAPSHOT_PENDING",
        decision_time=_DT_HORIZON_PAST,
        result=res,
    )

    assert _status(conn, event.event_id) == "dead_letter"
    row = conn.execute(
        "SELECT rejection_reason FROM no_trade_regret_events ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    # _DT_HORIZON_PAST (2026-06-07) is past BOTH horizons for Chicago 2026-06-05;
    # the venue-close floor (b, F1 12:00-UTC of target_date) is EARLIER than the
    # local-day floor (a) and so fires first. The load-bearing invariant for THIS
    # test is unchanged: a horizon terminal fires and the honest riskguard cause
    # survives in the MONEY_PATH_HORIZON_EXPIRED label, never an attempt count.
    assert row[0] == "MONEY_PATH_HORIZON_EXPIRED:MARKET_VENUE_CLOSED:RISK_GUARD_BLOCKED", (
        f"the riskguard cause must survive into the horizon label: got {row[0]!r}"
    )
    assert "attempt" not in row[0].lower()
