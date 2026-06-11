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
