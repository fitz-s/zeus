# Created: 2026-05-25
# Last reused/audited: 2026-05-25
# Authority basis: PR332 full-live split verdict; live-order aggregate substrate PR A.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.events.live_order_aggregate import LiveOrderAggregateError, LiveOrderAggregateLedger
from src.state.db import init_schema


NOW = datetime(2026, 5, 25, 18, tzinfo=timezone.utc)


def test_live_order_aggregate_appends_and_projects_sequence():
    ledger = LiveOrderAggregateLedger(_conn())

    first = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    second = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="LiveCapReserved",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "usage_id": "usage-1"},
        occurred_at=NOW,
        source_authority="live_cap_ledger",
        expected_parent_event_hash=first.event_hash,
    )

    projection = ledger.get_projection("event-1:intent-1")
    assert second.event_sequence == 2
    assert second.parent_event_hash == first.event_hash
    assert projection.current_state == "LIVE_CAP_RESERVED"
    assert projection.last_sequence == 2
    assert projection.final_intent_id == "intent-1"


def test_live_order_projection_rebuilds_from_append_only_events():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload(),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    live_cap = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="LiveCapReserved",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "usage_id": "usage-1"},
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )
    pre_submit = ledger.latest_event_of_type("event-1:intent-1", "PreSubmitRevalidated")
    assert pre_submit is not None
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "pre_submit_event_hash": pre_submit.event_hash,
            "live_cap_reserved_event_hash": live_cap.event_hash,
        },
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    conn.execute("DELETE FROM edli_live_order_projection WHERE aggregate_id = ?", ("event-1:intent-1",))
    rebuilt = ledger.rebuild_projection("event-1:intent-1")

    assert rebuilt.current_state == "EXECUTION_COMMAND_CREATED"
    assert rebuilt.last_sequence == 4


def test_live_order_aggregate_rejects_parent_hash_mismatch():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="parent hash mismatch"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="LiveCapReserved",
            payload={"event_id": "event-1", "final_intent_id": "intent-1"},
            occurred_at=NOW,
            source_authority="live_cap_ledger",
            expected_parent_event_hash="wrong",
        )


def test_live_order_aggregate_rejects_event_id_drift_before_append():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="event_id drift"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="SubmitPlanBuilt",
            payload={"event_id": "event-2", "final_intent_id": "intent-1"},
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_live_order_events_table_is_append_only():
    ledger = LiveOrderAggregateLedger(_conn())
    event = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.conn.execute(
            "UPDATE edli_live_order_events SET event_type = 'SubmitPlanBuilt' WHERE aggregate_event_id = ?",
            (event.aggregate_event_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.conn.execute(
            "DELETE FROM edli_live_order_events WHERE aggregate_event_id = ?",
            (event.aggregate_event_id,),
        )


def test_live_order_submit_unknown_stays_pending_until_reconcile():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed_command_with_submit_attempt(ledger)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitUnknown",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "venue_order_id": "venue-unknown",
        },
        occurred_at=NOW,
        source_authority="existing_executor",
    )
    assert ledger.get_projection("event-1:intent-1").pending_reconcile is True

    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="Reconciled",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "pending_reconcile": False},
        occurred_at=NOW,
        source_authority="explicit_reconcile",
    )

    projection = ledger.get_projection("event-1:intent-1")
    assert projection.current_state == "RECONCILED"
    assert projection.pending_reconcile is False


def test_live_order_cap_transition_pending_reconcile_sets_projection_pending():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitPlanBuilt",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    pre_submit = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload(),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    live_cap = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="LiveCapReserved",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "usage_id": "usage-1"},
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "pre_submit_event_hash": pre_submit.event_hash,
            "live_cap_reserved_event_hash": live_cap.event_hash,
        },
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAttempted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "cmd-1"},
        occurred_at=NOW,
        source_authority="existing_executor",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitUnknown",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "venue_order_id": "venue-unknown",
        },
        occurred_at=NOW,
        source_authority="existing_executor",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="CapTransitioned",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "to_status": "PENDING_RECONCILE",
            "projection_status": "RESERVED",
            "transition_reason": "SUBMIT_TIMEOUT",
            "execution_receipt_hash": "receipt-hash",
        },
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )

    projection = ledger.get_projection("event-1:intent-1")
    assert projection.pending_reconcile is True
    assert projection.current_state == "PENDING_RECONCILE"


def test_execution_command_requires_pre_submit_revalidation():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="PreSubmitRevalidated"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="ExecutionCommandCreated",
            payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "cmd-1"},
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"quote_age_ms": 1001, "max_quote_age_ms": 1000}, "quote_age_ms exceeds"),
        ({"would_cross_book": True}, "would_cross_book=false"),
        ({"tick_aligned": False}, "tick_aligned=true"),
        ({"size_ok": False}, "size_ok=true"),
        ({"heartbeat_status": "EXPIRED"}, "heartbeat_status=OK"),
        ({"user_ws_status": "GAP"}, "user_ws_status=OK"),
        ({"venue_connectivity_status": "DOWN"}, "venue_connectivity_status=OK"),
        ({"balance_allowance_status": "INSUFFICIENT"}, "balance_allowance_status=OK"),
        ({"book_authority_id": ""}, "book_authority_id"),
        ({"time_in_force": "FOK"}, "GTC/GTD"),
    ],
)
def test_pre_submit_revalidation_failures_block_command(override, message):
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match=message):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(**override),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_execution_command_final_intent_must_match_pre_submit_revalidation():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload(final_intent_id="intent-1"),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    live_cap = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="LiveCapReserved",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "usage_id": "usage-1"},
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )
    pre_submit = ledger.latest_event_of_type("event-1:intent-1", "PreSubmitRevalidated")
    assert pre_submit is not None

    with pytest.raises(LiveOrderAggregateError, match="final_intent_id must match"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="ExecutionCommandCreated",
            payload={
                "event_id": "event-1",
                "final_intent_id": "intent-2",
                "execution_command_id": "cmd-1",
                "pre_submit_event_hash": pre_submit.event_hash,
                "live_cap_reserved_event_hash": live_cap.event_hash,
            },
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_execution_command_binds_pre_submit_and_live_cap_event_hashes():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    pre_submit = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload(),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    live_cap = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="LiveCapReserved",
        payload={"event_id": "event-1", "final_intent_id": "intent-2", "usage_id": "usage-1"},
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )

    with pytest.raises(LiveOrderAggregateError, match="live cap reservation"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="ExecutionCommandCreated",
            payload={
                "event_id": "event-1",
                "final_intent_id": "intent-1",
                "execution_command_id": "cmd-1",
                "pre_submit_event_hash": pre_submit.event_hash,
                "live_cap_reserved_event_hash": live_cap.event_hash,
            },
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_init_schema_creates_live_order_aggregate_tables():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert {"edli_live_order_events", "edli_live_order_projection"} <= tables


def _pre_submit_payload(**overrides):
    payload = {
        "event_id": "event-1",
        "final_intent_id": "intent-1",
        "condition_id": "condition-1",
        "token_id": "token-yes",
        "side": "BUY",
        "direction": "buy_yes",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "post_only": True,
        "checked_at": "2026-05-25T18:00:00+00:00",
        "quote_seen_at": "2026-05-25T17:59:59.950000+00:00",
        "quote_age_ms": 50,
        "max_quote_age_ms": 1000,
        "book_hash": "book-hash-1",
        "current_best_bid": 0.41,
        "current_best_ask": 0.43,
        "limit_price": 0.40,
        "would_cross_book": False,
        "tick_size": 0.01,
        "tick_aligned": True,
        "min_order_size": 5.0,
        "size_ok": True,
        "neg_risk": False,
        "heartbeat_status": "OK",
        "user_ws_status": "OK",
        "venue_connectivity_status": "OK",
        "balance_allowance_status": "OK",
        "book_authority_id": "execution_feasibility_evidence",
        "book_captured_at": "2026-05-25T17:59:59.950000+00:00",
        "heartbeat_authority_id": "heartbeat_supervisor",
        "heartbeat_checked_at": "2026-05-25T18:00:00+00:00",
        "user_ws_authority_id": "ws_gap_guard",
        "user_ws_checked_at": "2026-05-25T18:00:00+00:00",
        "venue_connectivity_authority_id": "polymarket_public_orderbook",
        "venue_connectivity_checked_at": "2026-05-25T18:00:00+00:00",
        "balance_allowance_authority_id": "polymarket_wallet_readonly",
        "balance_allowance_checked_at": "2026-05-25T18:00:00+00:00",
        "expected_edge_source_certificate_hash": "actionable-hash-1",
        "cost_basis_source_certificate_hash": "cost-hash-1",
    }
    payload.update(overrides)
    return payload


def _seed_command_with_submit_attempt(ledger: LiveOrderAggregateLedger) -> None:
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitPlanBuilt",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    pre_submit = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload(),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    live_cap = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="LiveCapReserved",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "usage_id": "usage-1"},
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "pre_submit_event_hash": pre_submit.event_hash,
            "live_cap_reserved_event_hash": live_cap.event_hash,
        },
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAttempted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "cmd-1"},
        occurred_at=NOW,
        source_authority="existing_executor",
    )


# --------------------------------------------------------------------------- #
# H2_E2E (REAUDIT_0_1.md §2/§4): the projection's typed posterior trace is
# populated from the REAL DecisionProofAccepted event payload (the decision_audit
# block event_reactor_adapter.py:2818-2819 writes), via the production
# rebuild_projection path — NOT a manual column write. Relationship test: when
# the aggregate's DecisionProofAccepted payload carries a decision_audit posterior
# trace, the projection row must reconstruct it WITHOUT JSON_EXTRACT.
# --------------------------------------------------------------------------- #

def _decision_proof_payload_with_audit(*, posterior_id, probability_authority):
    """Mirror the production DecisionProofAccepted payload shape (decision_audit
    block carries the receipt's posterior trace — see _live_decision_audit_payload
    in event_reactor_adapter.py)."""
    return {
        "event_id": "event-1",
        "final_intent_id": "intent-1",
        "no_submit_certificate_count": 3,
        "no_submit_receipt_event_id": "evt-receipt-1",
        "decision_audit": {
            "schema": "edli_live_decision_audit_v1",
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "posterior_id": posterior_id,
            "probability_authority": probability_authority,
            "q_lcb_calibration_source": "FORECAST_BOOTSTRAP",
        },
    }


def test_projection_populates_posterior_trace_from_decision_audit_payload():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    # Production append → rebuild_projection runs inside append_event. The
    # posterior trace is read from the decision_audit payload, not injected.
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload=_decision_proof_payload_with_audit(
            posterior_id=4242, probability_authority="replacement_0_1"
        ),
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    # Reconstruct the live-order aggregate -> posterior link in SQL (typed
    # columns, NO JSON_EXTRACT).
    row = conn.execute(
        """
        SELECT posterior_id, probability_authority
        FROM edli_live_order_projection
        WHERE aggregate_id = ?
          AND probability_authority = 'replacement_0_1'
          AND posterior_id IS NOT NULL
        """,
        ("event-1:intent-1",),
    ).fetchone()
    assert row is not None, (
        "edli_live_order_projection.posterior_id must be populated by the "
        "production rebuild from the decision_audit payload — dead-column antibody"
    )
    assert row["posterior_id"] == 4242
    assert row["probability_authority"] == "replacement_0_1"


def test_projection_posterior_trace_is_sticky_across_later_events():
    """COALESCE proof: a later event whose payload lacks a decision_audit block
    (e.g. a reconcile re-projection) must NOT clear the posterior link set by
    DecisionProofAccepted."""
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload=_decision_proof_payload_with_audit(
            posterior_id=777, probability_authority="replacement_0_1"
        ),
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload(),  # no decision_audit block
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    row = conn.execute(
        "SELECT posterior_id, probability_authority FROM edli_live_order_projection WHERE aggregate_id = ?",
        ("event-1:intent-1",),
    ).fetchone()
    assert row["posterior_id"] == 777
    assert row["probability_authority"] == "replacement_0_1"


def test_projection_posterior_trace_null_for_canonical_order():
    """Observability only: a canonical order (no decision_audit posterior block)
    leaves the typed columns NULL — never changes order state, excluded from the
    replacement_0_1 query."""
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},  # no audit
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    row = conn.execute(
        "SELECT posterior_id, probability_authority FROM edli_live_order_projection WHERE aggregate_id = ?",
        ("event-1:intent-1",),
    ).fetchone()
    assert row["posterior_id"] is None
    assert row["probability_authority"] is None
    excluded = conn.execute(
        "SELECT * FROM edli_live_order_projection WHERE probability_authority = 'replacement_0_1'"
    ).fetchall()
    assert excluded == []


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn
