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


def test_pre_submit_rejected_terminates_without_venue_submit_attempt():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed_command_without_submit_attempt(ledger)

    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitRejected",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "execution_receipt_hash": "receipt-hash",
            "reason_code": "duplicate_entry_same_token:token-yes",
            "submit_status": "PRE_SUBMIT_ERROR",
            "venue_call_started": False,
            "venue_ack_received": False,
            "pre_submit_rejection": True,
        },
        occurred_at=NOW,
        source_authority="existing_executor",
    )

    projection = ledger.get_projection("event-1:intent-1")
    assert projection.current_state == "SUBMIT_REJECTED"
    assert projection.pending_reconcile is False


def test_non_pre_submit_rejected_requires_venue_submit_attempt():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed_command_without_submit_attempt(ledger)

    with pytest.raises(LiveOrderAggregateError, match="VenueSubmitAttempted"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="SubmitRejected",
            payload={
                "event_id": "event-1",
                "final_intent_id": "intent-1",
                "execution_command_id": "cmd-1",
                "execution_receipt_hash": "receipt-hash",
                "reason_code": "venue_rejected",
                "submit_status": "REJECTED",
                "venue_call_started": True,
                "venue_ack_received": False,
                "pre_submit_rejection": False,
            },
            occurred_at=NOW,
            source_authority="existing_executor",
        )


def test_live_order_aggregate_allows_new_submit_attempt_after_new_command():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed_command_with_submit_attempt(ledger)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitRejected",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "execution_receipt_hash": "receipt-hash-1",
            "reason_code": "venue_rejected",
            "submit_status": "REJECTED",
            "venue_call_started": True,
            "venue_ack_received": True,
            "pre_submit_rejection": False,
        },
        occurred_at=NOW,
        source_authority="existing_executor",
    )
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
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "usage_id": "usage-2"},
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-2",
            "pre_submit_event_hash": pre_submit.event_hash,
            "live_cap_reserved_event_hash": live_cap.event_hash,
        },
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAttempted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "cmd-2"},
        occurred_at=NOW,
        source_authority="existing_executor",
    )

    with pytest.raises(LiveOrderAggregateError, match="current command"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="VenueSubmitAttempted",
            payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "cmd-2"},
            occurred_at=NOW,
            source_authority="existing_executor",
        )


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


def test_cap_consumed_does_not_hide_acknowledged_live_order_projection():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed_command_with_submit_attempt(ledger)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAcknowledged",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "venue_order_id": "venue-live-1",
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
            "to_status": "CONSUMED",
            "execution_receipt_hash": "receipt-hash",
        },
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )

    projection = ledger.get_projection("event-1:intent-1")
    assert projection.current_state == "VENUE_SUBMIT_ACKED"
    assert projection.last_event_type == "CapTransitioned"
    assert projection.venue_order_id == "venue-live-1"
    assert projection.pending_reconcile is False


def test_order_lifecycle_projected_terminal_no_fill_closes_projection():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed_command_with_submit_attempt(ledger)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAcknowledged",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "venue_order_id": "venue-live-1",
        },
        occurred_at=NOW,
        source_authority="existing_executor",
    )

    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="OrderLifecycleProjected",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "venue_order_id": "venue-live-1",
            "order_lifecycle_state": "TERMINAL_NO_FILL",
            "exposure_created": False,
            "pending_reconcile": False,
        },
        occurred_at=NOW,
        source_authority="explicit_reconcile",
    )

    projection = ledger.get_projection("event-1:intent-1")
    assert projection.current_state == "TERMINAL_NO_FILL"
    assert projection.last_event_type == "OrderLifecycleProjected"
    assert projection.pending_reconcile is False
    assert projection.venue_order_id == "venue-live-1"


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
        ({"q_lcb_5pct": 0.72, "q_live": 0.70}, "q_lcb_5pct <= q_live"),
        ({"q_lcb_5pct": 0.39, "limit_price": 0.40}, "positive submit q_lcb-minus-limit"),
        ({"expected_edge": 0.25}, "expected_edge exceeds"),
        ({"size": 0.0}, "positive size"),
        ({"min_entry_price": -0.01}, "min_entry_price"),
        ({"min_entry_price": 0.05}, "min_entry_price below live floor"),
        ({"limit_price": 0.09, "min_entry_price": 0.10}, "entry price below strategy floor"),
        ({"min_expected_profit_usd": 10.0}, "expected profit below"),
        ({"min_submit_edge_density": 0.75}, "submit edge density below"),
        ({"expected_edge_source_certificate_hash": ""}, "expected_edge_source_certificate_hash"),
        ({"cost_basis_source_certificate_hash": ""}, "cost_basis_source_certificate_hash"),
        ({"qkernel_execution_economics": None}, "qkernel_execution_economics"),
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


def test_pre_submit_rejects_lucknow_negative_submit_edge():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="positive submit q_lcb-minus-limit"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(
                q_live=0.12,
                q_lcb_5pct=0.11,
                limit_price=0.12,
                expected_edge=0.04,
                min_entry_price=0.10,
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_allows_exact_strategy_entry_floor_for_day0_authority():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    event = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_day0_pre_submit_payload(
            limit_price=0.10,
            q_live=0.50,
            q_lcb_5pct=0.20,
            expected_edge=0.10,
            size=10.0,
            min_entry_price=0.10,
            min_expected_profit_usd=0.50,
            min_submit_edge_density=0.05,
            current_best_bid=0.09,
            current_best_ask=0.11,
            qkernel_execution_economics={
                **_pre_submit_payload()["qkernel_execution_economics"],
                "payoff_q_point": 0.50,
                "payoff_q_lcb": 0.20,
                "cost": 0.10,
                "edge_lcb": 0.10,
                "selection_guard_q_safe": 0.20,
            },
        ),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    assert event.event_type == "PreSubmitRevalidated"


def test_pre_submit_rejects_weak_qkernel_without_selection_guard():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    weak_economics = dict(_pre_submit_payload()["qkernel_execution_economics"])
    weak_economics.pop("selection_guard_basis")

    with pytest.raises(LiveOrderAggregateError, match="selection_guard_basis"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(qkernel_execution_economics=weak_economics),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_low_price_yes_below_live_floor():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="entry price below strategy floor"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(
                q_live=0.118809820736935,
                q_lcb_5pct=0.100708440505795,
                limit_price=0.07,
                expected_edge=0.0307084405057945,
                size=50.0,
                min_entry_price=0.10,
                min_expected_profit_usd=1.0,
                min_submit_edge_density=0.05,
                current_best_bid=0.06,
                current_best_ask=0.08,
                qkernel_execution_economics={
                    "source": "qkernel_spine",
                    "route_id": "DIRECT_YES:b23@proof",
                    "route_type": "direct",
                    "side": "YES",
                "payoff_q_point": 0.118809820736935,
                "payoff_q_lcb": 0.100708440505795,
                "cost": 0.07,
                "edge_lcb": 0.0307084405057945,
                "delta_u_at_min": 0.00009152233738979263,
                "optimal_stake_usd": 1.4412832709285736,
                "optimal_delta_u": 0.000411243383550307,
                    "false_edge_rate": 0.01,
                    "direction_law_ok": True,
                    "coherence_allows": True,
                    "selection_guard_basis": "SELECTION_BETA_95",
                    "selection_guard_abstained": False,
                    "selection_guard_q_safe": 0.100708440505795,
                },
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_qkernel_low_price_yes_below_roi_frontier_floor():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="qkernel roi frontier not useful"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(
                strategy_key="center_buy",
                q_live=0.12180248510788458,
                q_lcb_5pct=0.06052567908958011,
                limit_price=0.031,
                expected_edge=0.020510409830349664,
                size=46.49,
                min_entry_price=0.02,
                min_expected_profit_usd=0.05,
                min_submit_edge_density=0.02,
                current_best_bid=0.02,
                current_best_ask=0.04,
                qkernel_execution_economics={
                    **_pre_submit_payload()["qkernel_execution_economics"],
                    "route_id": "DIRECT_YES:b34@proof",
                    "side": "YES",
                    "payoff_q_point": 0.12180248510788458,
                    "payoff_q_lcb": 0.06052567908958011,
                    "cost": 0.04001526925923045,
                    "edge_lcb": 0.020510409830349664,
                    "delta_u_at_min": 0.00009152233738979263,
                    "optimal_stake_usd": 1.4412832709285736,
                    "optimal_delta_u": 0.0006333828915951036,
                    "selection_guard_q_safe": 0.06052567908958011,
                },
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_nonpositive_qkernel_delta_u_at_min():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    economics = {
        **_pre_submit_payload()["qkernel_execution_economics"],
        "delta_u_at_min": -0.01,
    }

    with pytest.raises(LiveOrderAggregateError, match="delta_u_at_min"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(qkernel_execution_economics=economics),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_accepts_center_buy_yes_above_micro_tail_floor():
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
            payload=_pre_submit_payload(
                strategy_key="center_buy",
                q_live=0.25,
                q_lcb_5pct=0.15,
                limit_price=0.024,
                expected_edge=0.126,
                size=30.0,
            min_entry_price=0.02,
            min_expected_profit_usd=1.0,
            min_submit_edge_density=0.05,
            current_best_bid=0.014,
            current_best_ask=0.034,
            qkernel_execution_economics={
                "source": "qkernel_spine",
                    "route_id": "DIRECT_YES:b24@proof",
                    "route_type": "direct",
                    "side": "YES",
                    "payoff_q_point": 0.25,
                    "payoff_q_lcb": 0.15,
                    "cost": 0.024,
                    "edge_lcb": 0.126,
                "delta_u_at_min": 0.01,
                "optimal_stake_usd": 30.0,
                "optimal_delta_u": 0.01,
                "false_edge_rate": 0.01,
                "direction_law_ok": True,
                "coherence_allows": True,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                    "selection_guard_q_safe": 0.15,
                },
            ),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )


def test_pre_submit_rejects_direct_qkernel_yes_below_strategy_floor_even_when_roi_clear():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="entry price below strategy floor"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(
                strategy_key="center_buy",
                q_live=0.82,
                q_lcb_5pct=0.72,
                limit_price=0.006,
                expected_edge=0.714,
                size=1497.78,
                min_entry_price=0.10,
                min_expected_profit_usd=1.0,
                min_submit_edge_density=0.05,
                current_best_bid=0.001,
                current_best_ask=0.007,
                qkernel_execution_economics={
                    "source": "qkernel_spine",
                    "route_id": "DIRECT_YES:b20@proof",
                    "route_type": "direct",
                    "side": "YES",
                    "payoff_q_point": 0.82,
                    "payoff_q_lcb": 0.72,
                    "cost": 0.006,
                    "edge_lcb": 0.714,
                    "delta_u_at_min": 0.01,
                    "optimal_stake_usd": 1497.78,
                    "optimal_delta_u": 0.01,
                    "false_edge_rate": 0.01,
                    "direction_law_ok": True,
                    "coherence_allows": True,
                    "selection_guard_basis": "SELECTION_BETA_95",
                    "selection_guard_abstained": False,
                    "selection_guard_q_safe": 0.72,
                },
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_accepts_day0_observation_authority_with_qkernel():
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
        payload=_day0_pre_submit_payload(),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    pre_submit = ledger.latest_event_of_type("event-1:intent-1", "PreSubmitRevalidated")
    assert pre_submit is not None
    assert pre_submit.payload["event_type"] == "DAY0_EXTREME_UPDATED"
    assert pre_submit.payload["qkernel_execution_economics"]["source"] == "qkernel_spine"
    assert pre_submit.payload["_edli_q_source"] == "day0_remaining_day"


def test_pre_submit_rejects_day0_missing_remaining_window_probability_authority():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="remaining-window probability authority required"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_day0_pre_submit_payload(
                day0_probability_authority=None,
                _edli_q_source=None,
                _edli_day0_q_mode=None,
                _edli_day0_remaining_models=None,
                _edli_day0_lcb_transform=None,
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_day0_hard_fact_probability_authority():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="hard-fact calibration"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_day0_pre_submit_payload(
                day0_probability_authority={
                    **_day0_probability_authority(),
                    "authority": "DAY0_LIVE_OBSERVATION_HARD_FACT",
                },
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_day0_missing_observation_authority():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="day0 observation authority required"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_day0_pre_submit_payload(live_authority_status=None),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_day0_missing_qkernel_economics():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="requires qkernel_execution_economics"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_day0_pre_submit_payload(qkernel_execution_economics=None),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_jeddah_qkernel_payoff_mismatched_submit_lcb():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="payoff_q_lcb mismatches submit q_lcb_5pct"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(
                side="BUY",
                direction="buy_no",
                token_id="token-no",
                q_live=0.986261171798223,
                q_lcb_5pct=0.986261171798223,
                limit_price=0.98,
                expected_edge=0.005,
                size=200.0,
                min_submit_edge_density=0.0,
                qkernel_execution_economics={
                    "source": "qkernel_spine",
                    "route_id": "DIRECT_NO:b24@proof",
                    "side": "NO",
                    "payoff_q_point": 0.986261171798223,
                    "payoff_q_lcb": 0.998678563135879,
                    "cost": 0.98,
                    "edge_lcb": 0.018678563135879,
                    "delta_u_at_min": 0.001,
                    "optimal_stake_usd": 200.0,
                    "optimal_delta_u": 0.001,
                    "false_edge_rate": 0.01,
                    "direction_law_ok": True,
                    "coherence_allows": True,
                    "selection_guard_basis": "SELECTION_BETA_95",
                    "selection_guard_abstained": False,
                    "selection_guard_q_safe": 0.998678563135879,
                },
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
            )


def test_pre_submit_rejects_jeddah_micro_edge_density_even_when_positive_edge():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="submit edge density below strategy floor"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(
                side="BUY",
                direction="buy_no",
                token_id="token-no",
                q_live=0.986261171798223,
                q_lcb_5pct=0.986261171798223,
                limit_price=0.98,
                size=21.99,
                expected_edge=0.005,
                min_expected_profit_usd=0.05,
                min_submit_edge_density=0.02,
            ),
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
        "size": 10.0,
        "q_live": 0.70,
        "q_lcb_5pct": 0.60,
        "expected_edge": 0.10,
        "selection_authority_applied": "qkernel_spine",
        "min_entry_price": 0.10,
        "min_expected_profit_usd": 1.0,
        "min_submit_edge_density": 0.05,
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
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "route_id": "DIRECT_YES:b20@proof",
            "route_type": "direct",
            "side": "YES",
            "payoff_q_point": 0.70,
            "payoff_q_lcb": 0.60,
            "cost": 0.40,
            "edge_lcb": 0.20,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": 10.0,
            "optimal_delta_u": 0.01,
            "false_edge_rate": 0.02,
            "direction_law_ok": True,
            "coherence_allows": True,
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 0.60,
        },
    }
    payload.update(overrides)
    return payload


def _day0_lcb_transform(condition_id: str = "condition-1", q_lcb: float = 0.60):
    return {
        "yes_lcb_by_condition": {condition_id: q_lcb},
        "no_lcb_by_condition": {condition_id: 0.20},
        "mask": [1.0],
        "absorbing_yes_conditions": [],
        "absorbing_no_conditions": [],
        "staleness_suppressed_conditions": [],
        "immature_finite_yes_suppressed_conditions": [],
        "day0_exit_authority_status": "mature",
        "day0_exit_authority_reason": "day0_high_extreme_post_peak",
        "rounded_extreme": 20.0,
        "metric": "high",
    }


def _day0_probability_authority(condition_id: str = "condition-1", q_lcb: float = 0.60):
    return {
        "q_source": "day0_remaining_day",
        "q_mode": "remaining_day",
        "remaining_models": 3,
        "remaining_model_names": ["ecmwf", "gfs", "icon"],
        "remaining_source_cycle_time_utc": "2026-05-25T12:00:00+00:00",
        "remaining_capture_times_utc": ["2026-05-25T12:20:00+00:00"],
        "exit_authority_status": "mature",
        "exit_authority_reason": "day0_high_extreme_post_peak",
        "observed_extreme_native": 20.0,
        "rounded_value": 20,
        "observation_time": "2026-05-25T17:30:00+00:00",
        "observation_available_at": "2026-05-25T17:35:00+00:00",
        "lcb_transform": _day0_lcb_transform(condition_id, q_lcb),
    }


def _day0_pre_submit_payload(**overrides):
    condition_id = str(overrides.get("condition_id") or "condition-1")
    q_lcb = float(overrides.get("q_lcb_5pct") or 0.60)
    day0_probability = _day0_probability_authority(condition_id, q_lcb)
    payload = _pre_submit_payload(
        event_type="DAY0_EXTREME_UPDATED",
        source_match_status="MATCH",
        local_date_status="MATCH",
        station_match_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
        live_authority_status="live",
        raw_value=20.0,
        rounded_value=20,
        high_so_far=20.0,
        observation_time="2026-05-25T17:30:00+00:00",
        observation_available_at="2026-05-25T17:35:00+00:00",
        day0_probability_authority=day0_probability,
        _edli_q_source="day0_remaining_day",
        _edli_day0_q_mode="remaining_day",
        _edli_day0_remaining_models=3,
        _edli_day0_remaining_model_names=["ecmwf", "gfs", "icon"],
        _edli_day0_remaining_source_cycle_time_utc="2026-05-25T12:00:00+00:00",
        _edli_day0_remaining_capture_times_utc=["2026-05-25T12:20:00+00:00"],
        _edli_day0_exit_authority_status="mature",
        _edli_day0_exit_authority_reason="day0_high_extreme_post_peak",
        _edli_day0_lcb_transform=day0_probability["lcb_transform"],
    )
    payload.update(overrides)
    return payload


def _seed_command_with_submit_attempt(ledger: LiveOrderAggregateLedger) -> None:
    _seed_command_without_submit_attempt(ledger)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAttempted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "cmd-1"},
        occurred_at=NOW,
        source_authority="existing_executor",
    )


def _seed_command_without_submit_attempt(ledger: LiveOrderAggregateLedger) -> None:
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
