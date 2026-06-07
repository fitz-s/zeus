# Created: 2026-06-01
# Last reused/audited: 2026-06-01
# Authority basis: MF-2 (capital-real over-materialization). A single 100-share
#   fill emitted by the venue as THREE UserTradeObserved legs (MATCHED -> MINED ->
#   CONFIRMED), all sharing one trade_id and each filled_size=100, must NOT be
#   summed by the position bridge into a 3.0x phantom 300-share position. The
#   bridge (_aggregate_fill_economics / _confirmed_fill_payloads) must keep exactly
#   ONE leg per DISTINCT trade_id (preferring CONFIRMED) and sum only across
#   DISTINCT trade_ids — collapsing re-reports of one fill while preserving genuine
#   multi-partial-fill summing.
"""MF-3 RELATIONSHIP tests for the EDLI fill-bridge trade_id dedup.

These verify the cross-module invariant at the seam where the EDLI execution
lane's lifecycle re-reports (MATCHED/MINED/CONFIRMED legs of ONE venue fill,
each carrying the same trade_id and full filled_size — the production shape
proven at tests/test_user_channel_ingest.py:920-926) flow into the canonical
position_current materialisation:

  - position TRUTH (sum-of-legs) and PnL TRUTH (latest leg, live_profit_audit.py
    :308 _latest_lifecycle) must AGREE on a multi-status fill: shares == one
    leg's filled_size, NOT the sum of the re-reported legs.
  - Genuine multi-partial fills (TWO DISTINCT trade_ids on one aggregate) must
    still SUM (the DEFECT-4 forward-proof must survive the dedup).

The legs are seeded with the SAME fill_authority_state mapping the live producer
applies (src.events.live_order_reconcile._fill_authority_state): MATCHED/MINED ->
MATCHED_PENDING_FINALITY, CONFIRMED -> FILL_CONFIRMED.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.events.edli_position_bridge import (
    EdliPositionBridgeError,
    edli_bridge_position_id,
    materialize_position_current_from_edli_fill,
)

CONDITION_ID = "0xcondition_mf2_dedup_1"
ELECTED_NO_TOKEN = "token_no_mf2_dedup"
FINAL_INTENT_ID = "intent-mf2-1"
EXECUTION_COMMAND_ID = "execcmd-mf2-1"
EVENT_ID = "evt-mf2-1"
VENUE_ORDER_ID = "venue-order-mf2-1"


# Mirror the live producer mapping (src.events.live_order_reconcile._fill_authority_state)
# so the seeded legs carry the exact fill_authority_state the bridge gates on.
_FILL_AUTHORITY_STATE = {
    "MATCHED": "MATCHED_PENDING_FINALITY",
    "MINED": "MATCHED_PENDING_FINALITY",
    "CONFIRMED": "FILL_CONFIRMED",
}


@pytest.fixture()
def conn() -> sqlite3.Connection:
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


def _insert_edli_event(
    conn: sqlite3.Connection,
    *,
    aggregate_id: str,
    sequence: int,
    event_type: str,
    payload: dict,
    source_authority: str = "engine_adapter",
) -> None:
    """Raw-insert an edli_live_order_events row (mirrors the real producer)."""
    payload_json = json.dumps(payload, sort_keys=True, default=str)
    event_hash = f"{aggregate_id}:{sequence}:{event_type}"
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            f"edli_evt:{event_hash}",
            aggregate_id,
            sequence,
            event_type,
            None if sequence == 1 else f"{aggregate_id}:{sequence-1}",
            event_hash,
            payload_json,
            f"ph:{event_hash}",
            source_authority,
            "2026-06-01T12:00:00+00:00",
            "2026-06-01T12:00:01+00:00",
        ),
    )


def _seed_identity(conn: sqlite3.Connection, aggregate_id: str) -> None:
    pre_submit = {
        "event_id": EVENT_ID,
        "final_intent_id": FINAL_INTENT_ID,
        "condition_id": CONDITION_ID,
        "token_id": ELECTED_NO_TOKEN,
        "side": "BUY",
        "direction": "buy_no",
        "native_token_side": "NO",
        "outcome_label": "NO",
        "city": "Shanghai",
        "target_date": "2026-06-02",
        "bin_label": "30-32",
        "metric": "high",
        "unit": "C",
        "market_id": CONDITION_ID,
        "q_live": 0.55,
        "executable_snapshot_id": "exec-snap-mf2",
    }
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=1, event_type="PreSubmitRevalidated", payload=pre_submit)
    _insert_edli_event(
        conn, aggregate_id=aggregate_id, sequence=2, event_type="ExecutionCommandCreated",
        payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "execution_command_id": EXECUTION_COMMAND_ID},
    )


def _seed_trade_leg(
    conn: sqlite3.Connection,
    aggregate_id: str,
    sequence: int,
    *,
    trade_status: str,
    trade_id: str,
    filled_size: float,
    price: float,
    fees: float,
) -> None:
    """Seed ONE UserTradeObserved leg in the exact live-producer payload shape.

    The live ingestor (_trade_payload) carries trade_id + filled_size +
    avg_fill_price + fees; live_order_reconcile.append_user_trade_observed then
    stamps trade_status + fill_authority_state. We replicate both.
    """
    _insert_edli_event(
        conn, aggregate_id=aggregate_id, sequence=sequence, event_type="UserTradeObserved",
        payload={
            "event_id": EVENT_ID,
            "final_intent_id": FINAL_INTENT_ID,
            "trade_status": trade_status,
            "fill_authority_state": _FILL_AUTHORITY_STATE[trade_status],
            "venue_order_id": VENUE_ORDER_ID,
            "trade_id": trade_id,
            "filled_size": filled_size,
            "avg_fill_price": price,
            "fees": fees,
        },
        source_authority="user_channel",
    )


# --------------------------------------------------------------------------- #
# 1. RED: one fill, three lifecycle re-reports sharing one trade_id, each
#    filled_size=100 -> bridge must yield shares == 100, NOT 300.
# --------------------------------------------------------------------------- #

def test_bridge_rejects_fill_materialization_without_market_unit(conn):
    aggregate_id = "agg-mf2-missing-unit"
    _insert_edli_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=1,
        event_type="PreSubmitRevalidated",
        payload={
            "event_id": EVENT_ID,
            "final_intent_id": FINAL_INTENT_ID,
            "condition_id": CONDITION_ID,
            "token_id": ELECTED_NO_TOKEN,
            "side": "BUY",
            "direction": "buy_no",
            "native_token_side": "NO",
            "outcome_label": "NO",
            "city": "Shanghai",
            "target_date": "2026-06-02",
            "bin_label": "30-32",
            "metric": "high",
            "market_id": CONDITION_ID,
            "q_live": 0.55,
            "executable_snapshot_id": "exec-snap-mf2",
        },
    )
    _insert_edli_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=2,
        event_type="ExecutionCommandCreated",
        payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "execution_command_id": EXECUTION_COMMAND_ID},
    )
    _seed_trade_leg(
        conn,
        aggregate_id,
        3,
        trade_status="CONFIRMED",
        trade_id="trade-missing-unit",
        filled_size=100.0,
        price=0.40,
        fees=0.20,
    )

    with pytest.raises(EdliPositionBridgeError, match="EDLI_BRIDGE_MARKET_IDENTITY_MISSING: unit"):
        materialize_position_current_from_edli_fill(conn, aggregate_id)


def test_red_same_trade_id_multistatus_legs_do_not_triple_count(conn):
    """ONE venue fill re-reported MATCHED -> MINED -> CONFIRMED (same trade_id,
    each filled_size=100) must materialise shares == 100 (the fill once), not
    300. This is the MF-2 phantom-over-materialization defect.
    """
    aggregate_id = "agg-mf2-same-tradeid"
    _seed_identity(conn, aggregate_id)
    # Production shape: same trade_id 'trade-ws', each leg full filled_size=100.
    _seed_trade_leg(conn, aggregate_id, 3, trade_status="MATCHED", trade_id="trade-ws", filled_size=100.0, price=0.40, fees=0.0)
    _seed_trade_leg(conn, aggregate_id, 4, trade_status="MINED", trade_id="trade-ws", filled_size=100.0, price=0.40, fees=0.0)
    _seed_trade_leg(conn, aggregate_id, 5, trade_status="CONFIRMED", trade_id="trade-ws", filled_size=100.0, price=0.40, fees=0.40)

    result = materialize_position_current_from_edli_fill(conn, aggregate_id)
    assert result is not None

    # The relationship: position shares == the single real fill (100), so it
    # AGREES with PnL truth (live_profit_audit reads the latest single leg).
    assert result["shares"] == pytest.approx(100.0), (
        f"phantom over-materialization: 3 re-reports of one 100-share fill summed to "
        f"{result['shares']} (expected 100.0 — the fill counted ONCE)"
    )
    assert result["cost_basis_usd"] == pytest.approx(100.0 * 0.40)

    row = conn.execute(
        "SELECT shares, cost_basis_usd FROM position_current WHERE position_id = ?",
        (edli_bridge_position_id(aggregate_id),),
    ).fetchone()
    assert row["shares"] == pytest.approx(100.0), (
        f"position_current.shares={row['shares']} — a real $40 fill must be 100 shares, not 300"
    )
    assert row["cost_basis_usd"] == pytest.approx(40.0)


# --------------------------------------------------------------------------- #
# 2. COMPANION: two DISTINCT trade_ids (genuine partial fills) on one aggregate
#    must still SUM (the DEFECT-4 forward-proof survives the dedup).
# --------------------------------------------------------------------------- #

def test_distinct_trade_ids_genuine_partials_still_sum(conn):
    """Two DISTINCT trade_ids on one aggregate are two real partial fills and
    must SUM: 100 @ 0.40 + 150 @ 0.40 = 250 shares. The dedup collapses only
    re-reports of ONE trade_id, never distinct fills.
    """
    aggregate_id = "agg-mf2-distinct-tradeids"
    _seed_identity(conn, aggregate_id)
    # Partial fill A: trade_id 'trade-A', confirmed at 100 shares.
    _seed_trade_leg(conn, aggregate_id, 3, trade_status="CONFIRMED", trade_id="trade-A", filled_size=100.0, price=0.40, fees=0.20)
    # Partial fill B: a DIFFERENT trade_id 'trade-B', confirmed at 150 shares.
    _seed_trade_leg(conn, aggregate_id, 4, trade_status="CONFIRMED", trade_id="trade-B", filled_size=150.0, price=0.40, fees=0.30)

    result = materialize_position_current_from_edli_fill(conn, aggregate_id)
    assert result is not None
    assert result["shares"] == pytest.approx(250.0), (
        f"distinct trade_ids must SUM (100 + 150 = 250), got {result['shares']}"
    )
    assert result["cost_basis_usd"] == pytest.approx(250.0 * 0.40)

    row = conn.execute(
        "SELECT shares FROM position_current WHERE position_id = ?",
        (edli_bridge_position_id(aggregate_id),),
    ).fetchone()
    assert row["shares"] == pytest.approx(250.0)


# --------------------------------------------------------------------------- #
# 3. COMPANION: distinct trade_ids EACH re-reported multi-status must collapse
#    per trade_id THEN sum across distinct ids: (100 once) + (150 once) = 250.
# --------------------------------------------------------------------------- #

def test_distinct_trade_ids_each_multistatus_collapse_then_sum(conn):
    """Two genuine partial fills, EACH re-reported MATCHED -> CONFIRMED with the
    same per-fill trade_id and full size. Dedup collapses each id's re-reports,
    then sums across the two distinct ids: 100 + 150 = 250 (not 500).
    """
    aggregate_id = "agg-mf2-distinct-multistatus"
    _seed_identity(conn, aggregate_id)
    # Fill A re-reported twice (same trade_id 'trade-A', each 100).
    _seed_trade_leg(conn, aggregate_id, 3, trade_status="MATCHED", trade_id="trade-A", filled_size=100.0, price=0.40, fees=0.0)
    _seed_trade_leg(conn, aggregate_id, 4, trade_status="CONFIRMED", trade_id="trade-A", filled_size=100.0, price=0.40, fees=0.20)
    # Fill B re-reported twice (same trade_id 'trade-B', each 150).
    _seed_trade_leg(conn, aggregate_id, 5, trade_status="MATCHED", trade_id="trade-B", filled_size=150.0, price=0.40, fees=0.0)
    _seed_trade_leg(conn, aggregate_id, 6, trade_status="CONFIRMED", trade_id="trade-B", filled_size=150.0, price=0.40, fees=0.30)

    result = materialize_position_current_from_edli_fill(conn, aggregate_id)
    assert result is not None
    assert result["shares"] == pytest.approx(250.0), (
        f"per-id collapse then cross-id sum must be 100 + 150 = 250, got {result['shares']}"
    )
