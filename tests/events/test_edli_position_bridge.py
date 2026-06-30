# Created: 2026-06-01
# Last reused or audited: 2026-06-17
# Authority basis: DEFECT-1 capital-recoverability bridge. An EDLI FILL_CONFIRMED
#   must materialise a canonical position_current row (the seam audited as
#   missing), idempotently, chain-reconcilable by token, summing partial fills.
"""TDD for src.events.edli_position_bridge.

Fitz #3 relationship tests: these verify a CROSS-MODULE invariant — what holds
when the EDLI execution lane's confirmed fill flows into the legacy
position_current lifecycle:

  1. RED contract: a confirmed EDLI fill, absent the bridge, leaves NO
     position_current row (the audited stuck-capital gap).
  2. GREEN: the bridge materialises exactly one correct row.
  3. Idempotency: a replayed fill UPDATEs the same row, never duplicates.
  4. Relationship: EDLI fill economics == position_current shares/cost_basis.
  5. Relationship: chain_reconciliation matches the bridged row BY TOKEN and
     populates chain_shares (proven for the legacy Shanghai position).
  6. Forward-proof DEFECT-4: two partial UserTradeObserved → summed shares,
     size-weighted price.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.contracts.semantic_types import EntryMethod
from src.events.edli_position_bridge import (
    EdliPositionBridgeError,
    edli_bridge_position_id,
    materialize_position_current_from_edli_fill,
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

CONDITION_ID = "0xcondition_edli_bridge_1"
ELECTED_NO_TOKEN = "token_no_99887766"
ELECTED_YES_TOKEN = "token_yes_11223344"
FINAL_INTENT_ID = "intent-edli-1"
EXECUTION_COMMAND_ID = "execcmd-edli-1"
EVENT_ID = "evt-edli-1"
VENUE_ORDER_ID = "venue-order-1"


def test_qkernel_spine_is_registered_live_entry_method():
    assert EntryMethod.from_value(EntryMethod.QKERNEL_SPINE.value) is EntryMethod.QKERNEL_SPINE


def test_edli_events_table_prefers_trade_main_when_world_copy_is_stale(tmp_path):
    from src.events.edli_position_bridge import _edli_events_table
    from src.state.db import init_schema

    world_path = tmp_path / "zeus-world.db"
    world_conn = sqlite3.connect(world_path)
    world_conn.row_factory = sqlite3.Row
    init_schema(world_conn)
    _insert_edli_event(
        world_conn,
        aggregate_id="stale-world-aggregate",
        sequence=1,
        event_type="DecisionProofAccepted",
        payload={"event_id": "stale-event", "final_intent_id": "stale-intent"},
        occurred_at="2026-06-28T12:47:09+00:00",
    )
    world_conn.commit()
    world_conn.close()

    trade_conn = sqlite3.connect(":memory:")
    trade_conn.row_factory = sqlite3.Row
    init_schema(trade_conn)
    trade_conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
    _insert_edli_event(
        trade_conn,
        aggregate_id="current-trade-aggregate",
        sequence=1,
        event_type="DecisionProofAccepted",
        payload={"event_id": "current-event", "final_intent_id": "current-intent"},
        occurred_at="2026-06-29T20:01:58+00:00",
    )

    assert _edli_events_table(trade_conn) == "edli_live_order_events"


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
    occurred_at: str = "2026-06-01T12:00:00+00:00",
) -> None:
    """Raw-insert an edli_live_order_events row (mirrors the real producer).

    The bridge reads event_type + payload_json only, so we seed those directly
    and keep the strict append-law chain (which couples to the whole submit
    pipeline) out of the bridge's unit contract.
    """
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
            occurred_at,
            "2026-06-01T12:00:01+00:00",
        ),
    )


def _insert_decision_certificate(
    conn: sqlite3.Connection,
    *,
    certificate_id: str,
    certificate_type: str,
    certificate_hash: str,
    payload: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO decision_certificates (
            certificate_id, certificate_type, schema_version,
            canonicalization_version, semantic_key, claim_type, mode,
            decision_time, authority_id, authority_version, algorithm_id,
            algorithm_version, payload_json, payload_hash, certificate_hash,
            verifier_status, created_at
        ) VALUES (?, ?, 1, 'v1', ?, 'actionable_trade', 'LIVE',
                  '2026-06-01T11:59:59+00:00', 'test-authority', 'v1',
                  'test-algorithm', 'v1', ?, ?, ?, 'VERIFIED',
                  '2026-06-01T12:00:00+00:00')
        """,
        (
            certificate_id,
            certificate_type,
            f"sk:{certificate_id}",
            json.dumps(payload, sort_keys=True, default=str),
            f"ph:{certificate_id}",
            certificate_hash,
        ),
    )


def _seed_confirmed_buy_no_aggregate(
    conn: sqlite3.Connection,
    aggregate_id: str = "agg-edli-buyno-1",
    *,
    fills: list[tuple[float, float, float]] | None = None,
) -> str:
    """Seed a realistic CONFIRMED buy_no aggregate.

    fills: list of (filled_size, avg_fill_price, fees). Default = single FOK
    full fill of 16.75 @ 0.42.
    """
    if fills is None:
        fills = [(16.75, 0.42, 0.03)]
    pre_submit = {
        "event_id": EVENT_ID,
        "event_type": "FORECAST_SNAPSHOT_READY",
        "final_intent_id": FINAL_INTENT_ID,
        "strategy_key": "opening_inertia",
        "condition_id": CONDITION_ID,
        "token_id": ELECTED_NO_TOKEN,  # elected NATIVE token == no_token for buy_no
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
        "executable_snapshot_id": "exec-snap-1",
    }
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=1, event_type="PreSubmitRevalidated", payload=pre_submit, source_authority="engine_adapter")
    _insert_edli_event(
        conn, aggregate_id=aggregate_id, sequence=2, event_type="ExecutionCommandCreated",
        payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "execution_command_id": EXECUTION_COMMAND_ID},
        source_authority="engine_adapter",
    )
    seq = 3
    for (size, price, fees) in fills:
        _insert_edli_event(
            conn, aggregate_id=aggregate_id, sequence=seq, event_type="UserTradeObserved",
            payload={
                "event_id": EVENT_ID,
                "final_intent_id": FINAL_INTENT_ID,
                "trade_status": "CONFIRMED",
                "fill_authority_state": "FILL_CONFIRMED",
                "venue_order_id": VENUE_ORDER_ID,
                "filled_size": size,
                "avg_fill_price": price,
                "fees": fees,
            },
            source_authority="user_channel",
        )
        seq += 1
    return aggregate_id


def _position_current_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM position_current").fetchall()


# --------------------------------------------------------------------------- #
# 1. RED: confirmed fill, no bridge → no position_current row
# --------------------------------------------------------------------------- #

def test_red_confirmed_fill_produces_no_position_current_without_bridge(conn):
    """The audited gap: EDLI fill writes event-log only; position_current empty."""
    _seed_confirmed_buy_no_aggregate(conn)
    assert _position_current_rows(conn) == [], "PRECONDITION: EDLI fill alone must not create position_current"


# --------------------------------------------------------------------------- #
# 2. GREEN: bridge materialises exactly one correct row
# --------------------------------------------------------------------------- #

def test_green_bridge_materializes_one_correct_position(conn):
    aggregate_id = _seed_confirmed_buy_no_aggregate(conn)
    result = materialize_position_current_from_edli_fill(
        conn,
        aggregate_id,
        now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert result is not None
    assert result["created"] is True

    rows = _position_current_rows(conn)
    assert len(rows) == 1, "exactly one position_current row"
    row = rows[0]
    assert row["position_id"] == edli_bridge_position_id(aggregate_id)
    assert row["phase"] == "active"
    assert row["direction"] == "buy_no"
    assert row["condition_id"] == CONDITION_ID
    # Token placement: buy_no → elected token on no_token_id (chain-match key).
    assert row["no_token_id"] == ELECTED_NO_TOKEN
    assert (row["token_id"] or "") == ""
    assert abs(row["shares"] - 16.75) < 1e-9
    assert abs(row["entry_price"] - 0.42) < 1e-9
    assert abs(row["cost_basis_usd"] - (16.75 * 0.42)) < 1e-6
    assert row["fill_authority"] == "venue_confirmed_full"
    assert row["order_status"] == "filled"
    assert row["entry_method"] == "ens_member_counting"
    assert row["strategy_key"] == "opening_inertia"
    fact = conn.execute(
        """
        SELECT position_id, order_role, strategy_key, fill_price, shares, terminal_exec_status
        FROM execution_fact
        WHERE intent_id = ?
        """,
        (FINAL_INTENT_ID,),
    ).fetchone()
    assert fact is not None
    assert fact["position_id"] == row["position_id"]
    assert fact["order_role"] == "entry"
    assert fact["strategy_key"] == "opening_inertia"
    assert fact["fill_price"] == pytest.approx(0.42)
    assert fact["shares"] == pytest.approx(16.75)
    assert fact["terminal_exec_status"] == "filled"

    # One canonical entry-event chain exists.
    ev = conn.execute(
        "SELECT event_type FROM position_events WHERE position_id = ? ORDER BY sequence_no",
        (row["position_id"],),
    ).fetchall()
    assert [r[0] for r in ev] == ["POSITION_OPEN_INTENT", "ENTRY_ORDER_POSTED", "ENTRY_ORDER_FILLED"]


def test_bridge_relinks_venue_command_decision_id_to_canonical_position(conn):
    aggregate_id = _seed_confirmed_buy_no_aggregate(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
        """,
        (
            "cmd-short-1",
            "snap-1",
            "env-1",
            "stale-short-position",
            EXECUTION_COMMAND_ID,
            "idem-bridge-command-link-1",
            "ENTRY",
            CONDITION_ID,
            ELECTED_NO_TOKEN,
            "BUY",
            16.75,
            0.42,
            VENUE_ORDER_ID,
            "FILLED",
            "2026-06-01T11:59:58+00:00",
            "2026-06-01T11:59:58+00:00",
        ),
    )

    result = materialize_position_current_from_edli_fill(
        conn,
        aggregate_id,
        now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )

    assert result is not None
    canonical_position_id = result["position_id"]
    command = conn.execute(
        "SELECT position_id, updated_at FROM venue_commands WHERE command_id = 'cmd-short-1'"
    ).fetchone()
    assert command["position_id"] == canonical_position_id
    assert command["updated_at"] == "2026-06-01T12:00:00+00:00"

    fact = conn.execute(
        "SELECT command_id, posted_at FROM execution_fact WHERE intent_id = ?",
        (FINAL_INTENT_ID,),
    ).fetchone()
    assert fact["command_id"] == "cmd-short-1"
    assert fact["posted_at"] == "2026-06-01T11:59:58+00:00"

    provenance = conn.execute(
        """
        SELECT event_type, payload_json, source
          FROM provenance_envelope_events
         WHERE subject_type = 'command'
           AND subject_id = 'cmd-short-1'
           AND event_type = 'POSITION_LINK_REPAIRED'
        """
    ).fetchone()
    assert provenance is not None
    assert provenance["source"] == "WS_USER"
    assert "stale-short-position" in provenance["payload_json"]
    assert canonical_position_id in provenance["payload_json"]


def test_green_bridge_buy_yes_places_token_on_token_id(conn):
    aggregate_id = "agg-edli-buyyes-1"
    pre_submit = {
        "event_id": EVENT_ID, "event_type": "FORECAST_SNAPSHOT_READY",
        "final_intent_id": FINAL_INTENT_ID, "strategy_key": "center_buy", "condition_id": CONDITION_ID,
        "token_id": ELECTED_YES_TOKEN, "side": "BUY", "direction": "buy_yes",
        "native_token_side": "YES", "outcome_label": "YES", "city": "Tokyo",
        "target_date": "2026-06-02", "bin_label": "28-30", "metric": "high", "unit": "C", "q_live": 0.6,
    }
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=1, event_type="PreSubmitRevalidated", payload=pre_submit)
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=2, event_type="ExecutionCommandCreated",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "execution_command_id": EXECUTION_COMMAND_ID})
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=3, event_type="UserTradeObserved",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "trade_status": "CONFIRMED",
                                "fill_authority_state": "FILL_CONFIRMED", "venue_order_id": VENUE_ORDER_ID,
                                "filled_size": 5.0, "avg_fill_price": 0.5, "fees": 0.01}, source_authority="user_channel")
    materialize_position_current_from_edli_fill(conn, aggregate_id)
    row = _position_current_rows(conn)[0]
    assert row["direction"] == "buy_yes"
    assert row["token_id"] == ELECTED_YES_TOKEN
    assert (row["no_token_id"] or "") == ""
    assert row["strategy_key"] == "center_buy"


def test_bridge_projects_qkernel_authority_into_position_current(conn):
    aggregate_id = "agg-edli-qkernel-buyyes-1"
    actionable_hash = "hash-actionable-qkernel-1"
    _insert_decision_certificate(
        conn,
        certificate_id="cert-actionable-qkernel-1",
        certificate_type="ActionableTradeCertificate",
        certificate_hash=actionable_hash,
        payload={
            "q_live": 0.0,
            "q_lcb_5pct": 0.0,
            "qkernel_execution_economics": {
                "side": "YES",
                "payoff_q_point": 0.1507234,
                "payoff_q_lcb": 0.1374248,
                "edge_lcb": 0.1311266,
                "optimal_delta_u": 0.0209995,
            },
        },
    )
    pre_submit = {
        "event_id": EVENT_ID,
        "event_type": "FORECAST_SNAPSHOT_READY",
        "final_intent_id": FINAL_INTENT_ID,
        "strategy_key": "center_buy",
        "condition_id": CONDITION_ID,
        "token_id": ELECTED_YES_TOKEN,
        "side": "BUY",
        "direction": "buy_yes",
        "native_token_side": "YES",
        "outcome_label": "YES",
        "city": "Tokyo",
        "target_date": "2026-06-26",
        "bin_label": "22C",
        "metric": "low",
        "unit": "C",
        "q_live": 0.0,
        "expected_edge_source_certificate_hash": actionable_hash,
    }
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=1, event_type="PreSubmitRevalidated", payload=pre_submit)
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=2, event_type="ExecutionCommandCreated",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "execution_command_id": EXECUTION_COMMAND_ID})
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=3, event_type="UserTradeObserved",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "trade_status": "CONFIRMED",
                                "fill_authority_state": "FILL_CONFIRMED", "venue_order_id": VENUE_ORDER_ID,
                                "filled_size": 314.8, "avg_fill_price": 0.005, "fees": 0.0}, source_authority="user_channel")

    materialize_position_current_from_edli_fill(conn, aggregate_id)

    row = _position_current_rows(conn)[0]
    assert row["direction"] == "buy_yes"
    assert row["entry_method"] == EntryMethod.QKERNEL_SPINE.value
    assert row["p_posterior"] == pytest.approx(0.1507234)
    assert row["entry_ci_width"] == pytest.approx(2.0 * (0.1507234 - 0.1374248))


def test_bridge_projects_qkernel_authority_from_decision_audit_when_cert_unreadable(conn):
    aggregate_id = "agg-edli-qkernel-audit-only-1"
    pre_submit = {
        "event_id": EVENT_ID,
        "event_type": "FORECAST_SNAPSHOT_READY",
        "final_intent_id": FINAL_INTENT_ID,
        "strategy_key": "center_buy",
        "condition_id": CONDITION_ID,
        "token_id": ELECTED_YES_TOKEN,
        "side": "BUY",
        "direction": "buy_yes",
        "native_token_side": "YES",
        "outcome_label": "YES",
        "city": "Tokyo",
        "target_date": "2026-06-26",
        "bin_label": "22C",
        "metric": "low",
        "unit": "C",
        "q_live": 0.0,
        "expected_edge_source_certificate_hash": "missing-actionable-cert",
    }
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=1, event_type="PreSubmitRevalidated", payload=pre_submit)
    _insert_edli_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=2,
        event_type="DecisionProofAccepted",
        payload={
            "event_id": EVENT_ID,
            "final_intent_id": FINAL_INTENT_ID,
            "decision_audit": {
                "q_live": 0.18105161173018375,
                "q_lcb_5pct": 0.01935548685529438,
                "qkernel_execution_economics": {
                    "side": "YES",
                    "payoff_q_point": 0.1507234,
                    "payoff_q_lcb": 0.1374248,
                },
            },
        },
    )
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=3, event_type="ExecutionCommandCreated",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "execution_command_id": EXECUTION_COMMAND_ID})
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=4, event_type="UserTradeObserved",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "trade_status": "CONFIRMED",
                                "fill_authority_state": "FILL_CONFIRMED", "venue_order_id": VENUE_ORDER_ID,
                                "filled_size": 314.8, "avg_fill_price": 0.005, "fees": 0.0}, source_authority="user_channel")

    materialize_position_current_from_edli_fill(conn, aggregate_id)

    row = _position_current_rows(conn)[0]
    assert row["entry_method"] == EntryMethod.QKERNEL_SPINE.value
    assert row["p_posterior"] == pytest.approx(0.1507234)
    assert row["entry_ci_width"] == pytest.approx(2.0 * (0.1507234 - 0.1374248))


def test_durable_fill_bridge_repairs_incomplete_existing_projection(conn):
    from src.ingest.price_channel_ingest import _edli_durable_fill_bridge_scan

    aggregate_id = "agg-edli-qkernel-repair-existing-1"
    pre_submit = {
        "event_id": EVENT_ID,
        "event_type": "FORECAST_SNAPSHOT_READY",
        "final_intent_id": FINAL_INTENT_ID,
        "strategy_key": "center_buy",
        "condition_id": CONDITION_ID,
        "token_id": ELECTED_YES_TOKEN,
        "side": "BUY",
        "direction": "buy_yes",
        "native_token_side": "YES",
        "outcome_label": "YES",
        "city": "Tokyo",
        "target_date": "2026-06-26",
        "bin_label": "22C",
        "metric": "low",
        "unit": "C",
        "q_live": 0.0,
    }
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=1, event_type="PreSubmitRevalidated", payload=pre_submit)
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=2, event_type="ExecutionCommandCreated",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "execution_command_id": EXECUTION_COMMAND_ID})
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=3, event_type="UserTradeObserved",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "trade_status": "CONFIRMED",
                                "fill_authority_state": "FILL_CONFIRMED", "venue_order_id": VENUE_ORDER_ID,
                                "filled_size": 314.8, "avg_fill_price": 0.005, "fees": 0.0}, source_authority="user_channel")
    materialize_position_current_from_edli_fill(conn, aggregate_id)
    row = _position_current_rows(conn)[0]
    assert row["p_posterior"] == 0.0
    assert row["entry_method"] == "ens_member_counting"

    _insert_edli_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=4,
        event_type="DecisionProofAccepted",
        payload={
            "event_id": EVENT_ID,
            "final_intent_id": FINAL_INTENT_ID,
            "decision_audit": {
                "qkernel_execution_economics": {
                    "side": "YES",
                    "payoff_q_point": 0.1507234,
                    "payoff_q_lcb": 0.1374248,
                },
            },
        },
    )

    _edli_durable_fill_bridge_scan(
        conn,
        now=datetime(2026, 6, 25, 14, 40, tzinfo=timezone.utc),
        already_bridged_repair_limit=10,
    )

    repaired = _position_current_rows(conn)[0]
    assert repaired["entry_method"] == EntryMethod.QKERNEL_SPINE.value
    assert repaired["p_posterior"] == pytest.approx(0.1507234)


def test_durable_fill_bridge_prioritizes_incomplete_open_projection_over_healthy_existing(
    conn,
):
    from src.ingest.price_channel_ingest import _edli_durable_fill_bridge_scan

    healthy_aggregate = "agg-000-healthy-before-incomplete"
    healthy_token = "token-healthy-before-incomplete"
    _insert_edli_event(
        conn,
        aggregate_id=healthy_aggregate,
        sequence=1,
        event_type="PreSubmitRevalidated",
        payload={
            "event_id": "evt-healthy-before-incomplete",
            "event_type": "FORECAST_SNAPSHOT_READY",
            "final_intent_id": "intent-healthy-before-incomplete",
            "strategy_key": "center_buy",
            "condition_id": "0xhealthy-before-incomplete",
            "token_id": healthy_token,
            "side": "BUY",
            "direction": "buy_yes",
            "native_token_side": "YES",
            "outcome_label": "YES",
            "city": "Tokyo",
            "target_date": "2026-06-26",
            "bin_label": "21C",
            "metric": "low",
            "unit": "C",
            "q_live": 0.61,
        },
    )
    _insert_edli_event(
        conn,
        aggregate_id=healthy_aggregate,
        sequence=2,
        event_type="UserTradeObserved",
        payload={
            "event_id": "evt-healthy-before-incomplete",
            "final_intent_id": "intent-healthy-before-incomplete",
            "trade_status": "CONFIRMED",
            "fill_authority_state": "FILL_CONFIRMED",
            "venue_order_id": "venue-healthy-before-incomplete",
            "filled_size": 5.0,
            "avg_fill_price": 0.61,
            "fees": 0.0,
        },
        source_authority="user_channel",
    )
    materialize_position_current_from_edli_fill(conn, healthy_aggregate)
    healthy_position_id = edli_bridge_position_id(healthy_aggregate)
    conn.execute(
        """
        UPDATE position_current
           SET p_posterior = 0.61,
               entry_method = ?
         WHERE position_id = ?
        """,
        (EntryMethod.QKERNEL_SPINE.value, healthy_position_id),
    )

    incomplete_aggregate = "agg-999-incomplete-open"
    _insert_edli_event(
        conn,
        aggregate_id=incomplete_aggregate,
        sequence=1,
        event_type="PreSubmitRevalidated",
        payload={
            "event_id": EVENT_ID,
            "event_type": "FORECAST_SNAPSHOT_READY",
            "final_intent_id": FINAL_INTENT_ID,
            "strategy_key": "center_buy",
            "condition_id": CONDITION_ID,
            "token_id": ELECTED_YES_TOKEN,
            "side": "BUY",
            "direction": "buy_yes",
            "native_token_side": "YES",
            "outcome_label": "YES",
            "city": "Tokyo",
            "target_date": "2026-06-26",
            "bin_label": "22C",
            "metric": "low",
            "unit": "C",
            "q_live": 0.0,
        },
    )
    _insert_edli_event(
        conn,
        aggregate_id=incomplete_aggregate,
        sequence=2,
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": EVENT_ID,
            "final_intent_id": FINAL_INTENT_ID,
            "execution_command_id": EXECUTION_COMMAND_ID,
        },
    )
    _insert_edli_event(
        conn,
        aggregate_id=incomplete_aggregate,
        sequence=3,
        event_type="UserTradeObserved",
        payload={
            "event_id": EVENT_ID,
            "final_intent_id": FINAL_INTENT_ID,
            "trade_status": "CONFIRMED",
            "fill_authority_state": "FILL_CONFIRMED",
            "venue_order_id": VENUE_ORDER_ID,
            "filled_size": 314.8,
            "avg_fill_price": 0.005,
            "fees": 0.0,
        },
        source_authority="user_channel",
    )
    materialize_position_current_from_edli_fill(conn, incomplete_aggregate)
    _insert_edli_event(
        conn,
        aggregate_id=incomplete_aggregate,
        sequence=4,
        event_type="DecisionProofAccepted",
        payload={
            "event_id": EVENT_ID,
            "final_intent_id": FINAL_INTENT_ID,
            "decision_audit": {
                "qkernel_execution_economics": {
                    "side": "YES",
                    "payoff_q_point": 0.1507234,
                    "payoff_q_lcb": 0.1374248,
                },
            },
        },
    )

    _edli_durable_fill_bridge_scan(
        conn,
        now=datetime(2026, 6, 25, 14, 40, tzinfo=timezone.utc),
        already_bridged_repair_limit=1,
    )

    repaired = conn.execute(
        """
        SELECT p_posterior, entry_method
          FROM position_current
         WHERE position_id = ?
        """,
        (edli_bridge_position_id(incomplete_aggregate),),
    ).fetchone()
    assert repaired["entry_method"] == EntryMethod.QKERNEL_SPINE.value
    assert repaired["p_posterior"] == pytest.approx(0.1507234)


def test_durable_fill_bridge_repairs_command_linked_short_position_projection(conn):
    from src.ingest.price_channel_ingest import _edli_durable_fill_bridge_scan

    aggregate_id = "agg-999-command-linked-short-position"
    short_position_id = "short-pos-live"
    pre_submit = {
        "event_id": EVENT_ID,
        "event_type": "FORECAST_SNAPSHOT_READY",
        "final_intent_id": FINAL_INTENT_ID,
        "strategy_key": "center_buy",
        "condition_id": CONDITION_ID,
        "token_id": ELECTED_YES_TOKEN,
        "side": "BUY",
        "direction": "buy_yes",
        "native_token_side": "YES",
        "outcome_label": "YES",
        "city": "Tokyo",
        "target_date": "2026-06-26",
        "bin_label": "22C",
        "metric": "low",
        "unit": "C",
        "q_live": 0.0,
    }
    _insert_edli_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=1,
        event_type="PreSubmitRevalidated",
        payload=pre_submit,
    )
    _insert_edli_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=2,
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": EVENT_ID,
            "final_intent_id": FINAL_INTENT_ID,
            "execution_command_id": EXECUTION_COMMAND_ID,
        },
    )
    _insert_edli_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=3,
        event_type="UserTradeObserved",
        payload={
            "event_id": EVENT_ID,
            "final_intent_id": FINAL_INTENT_ID,
            "trade_status": "CONFIRMED",
            "fill_authority_state": "FILL_CONFIRMED",
            "venue_order_id": VENUE_ORDER_ID,
            "filled_size": 314.8,
            "avg_fill_price": 0.005,
            "fees": 0.0,
        },
        source_authority="user_channel",
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price,
            p_posterior, entry_ci_width, entry_method, strategy_key,
            condition_id, token_id, no_token_id, order_id, order_status,
            temperature_metric, fill_authority, chain_state, chain_shares,
            updated_at
        ) VALUES (?, 'active', ?, 'Tokyo', 'Tokyo', '2026-06-26', '22C',
                  'buy_yes', 'C', ?, 314.8, ?, 0.005,
                  0.0, 0.0, 'ens_member_counting', 'center_buy',
                  ?, ?, NULL, ?, 'filled',
                  'low', 'venue_confirmed_full', 'synced', 314.8,
                  '2026-06-25T13:08:55+00:00')
        """,
        (
            short_position_id,
            CONDITION_ID,
            314.8 * 0.005,
            314.8 * 0.005,
            CONDITION_ID,
            ELECTED_YES_TOKEN,
            VENUE_ORDER_ID,
        ),
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
        """,
        (
            "cmd-short-position-live",
            "snap-short-position-live",
            "env-short-position-live",
            short_position_id,
            EXECUTION_COMMAND_ID,
            "idem-short-position-live",
            "ENTRY",
            CONDITION_ID,
            ELECTED_YES_TOKEN,
            "BUY",
            314.8,
            0.005,
            VENUE_ORDER_ID,
            "FILLED",
            "2026-06-25T13:08:55+00:00",
            "2026-06-25T13:08:55+00:00",
        ),
    )
    _insert_edli_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=4,
        event_type="DecisionProofAccepted",
        payload={
            "event_id": EVENT_ID,
            "final_intent_id": FINAL_INTENT_ID,
            "decision_audit": {
                "qkernel_execution_economics": {
                    "side": "YES",
                    "payoff_q_point": 0.1507234,
                    "payoff_q_lcb": 0.1374248,
                },
            },
        },
    )

    _edli_durable_fill_bridge_scan(
        conn,
        now=datetime(2026, 6, 25, 14, 40, tzinfo=timezone.utc),
        already_bridged_repair_limit=1,
    )

    repaired = conn.execute(
        """
        SELECT p_posterior, entry_ci_width, entry_method
          FROM position_current
         WHERE position_id = ?
        """,
        (short_position_id,),
    ).fetchone()
    assert repaired["entry_method"] == EntryMethod.QKERNEL_SPINE.value
    assert repaired["p_posterior"] == pytest.approx(0.1507234)
    assert repaired["entry_ci_width"] == pytest.approx(2.0 * (0.1507234 - 0.1374248))


def test_bridge_allows_day0_buy_no_settlement_capture(conn):
    aggregate_id = "agg-edli-day0-buyno-1"
    pre_submit = {
        "event_id": EVENT_ID,
        "event_type": "DAY0_EXTREME_UPDATED",
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
        "q_live": 0.55,
        "executable_snapshot_id": "exec-snap-1",
    }
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=1, event_type="PreSubmitRevalidated", payload=pre_submit)
    _insert_edli_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=2,
        event_type="ExecutionCommandCreated",
        payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "execution_command_id": EXECUTION_COMMAND_ID},
    )
    _insert_edli_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=3,
        event_type="UserTradeObserved",
        payload={
            "event_id": EVENT_ID,
            "final_intent_id": FINAL_INTENT_ID,
            "trade_status": "CONFIRMED",
            "fill_authority_state": "FILL_CONFIRMED",
            "venue_order_id": VENUE_ORDER_ID,
            "filled_size": 5.0,
            "avg_fill_price": 0.5,
        },
        source_authority="user_channel",
    )

    result = materialize_position_current_from_edli_fill(conn, aggregate_id)

    assert result is not None
    row = _position_current_rows(conn)[0]
    assert row["strategy_key"] == "settlement_capture"
    assert row["direction"] == "buy_no"


# --------------------------------------------------------------------------- #
# 3. Idempotency: replayed fill → still one row, UPDATEd not duplicated
# --------------------------------------------------------------------------- #

def test_idempotent_replay_keeps_one_row(conn):
    aggregate_id = _seed_confirmed_buy_no_aggregate(conn)
    r1 = materialize_position_current_from_edli_fill(conn, aggregate_id)
    assert r1["created"] is True
    r2 = materialize_position_current_from_edli_fill(conn, aggregate_id)
    assert r2["created"] is False, "replay must UPDATE, not re-create"

    rows = _position_current_rows(conn)
    assert len(rows) == 1, "replay must not duplicate position_current"
    # Entry events must NOT be duplicated (append-only unique key).
    ev = conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type='POSITION_OPEN_INTENT'",
        (rows[0]["position_id"],),
    ).fetchone()[0]
    assert ev == 1, "POSITION_OPEN_INTENT must exist exactly once after replay"


def test_same_order_duplicate_aggregate_absorbs_existing_open_row(conn):
    first_aggregate = _seed_confirmed_buy_no_aggregate(conn, aggregate_id="agg-edli-same-order-a")
    first = materialize_position_current_from_edli_fill(conn, first_aggregate)
    assert first["created"] is True

    second_aggregate = _seed_confirmed_buy_no_aggregate(conn, aggregate_id="agg-edli-same-order-b")
    second = materialize_position_current_from_edli_fill(conn, second_aggregate)

    assert second["created"] is False
    assert second["position_id"] == first["position_id"]
    rows = _position_current_rows(conn)
    assert len(rows) == 1
    assert rows[0]["position_id"] == first["position_id"]
    assert rows[0]["shares"] == pytest.approx(16.75)
    audit = conn.execute(
        "SELECT event_type, payload_json FROM position_events "
        "WHERE position_id = ? ORDER BY sequence_no DESC LIMIT 1",
        (first["position_id"],),
    ).fetchone()
    assert audit["event_type"] == "MANUAL_OVERRIDE_APPLIED"
    assert "agg-edli-same-order-b" not in audit["payload_json"]

    before_count = conn.execute(
        """
        SELECT COUNT(*) FROM position_events
         WHERE position_id = ?
           AND event_type = 'MANUAL_OVERRIDE_APPLIED'
           AND source_module = 'src.events.edli_position_bridge'
        """,
        (first["position_id"],),
    ).fetchone()[0]

    replay = materialize_position_current_from_edli_fill(conn, second_aggregate)
    after_count = conn.execute(
        """
        SELECT COUNT(*) FROM position_events
         WHERE position_id = ?
           AND event_type = 'MANUAL_OVERRIDE_APPLIED'
           AND source_module = 'src.events.edli_position_bridge'
        """,
        (first["position_id"],),
    ).fetchone()[0]

    assert replay["created"] is False
    assert replay["position_id"] == first["position_id"]
    assert after_count == before_count


def test_same_order_duplicate_preserves_chain_corrected_size(conn):
    first_aggregate = _seed_confirmed_buy_no_aggregate(conn, aggregate_id="agg-edli-chain-a")
    first = materialize_position_current_from_edli_fill(conn, first_aggregate)
    assert first["created"] is True

    second_aggregate = _seed_confirmed_buy_no_aggregate(conn, aggregate_id="agg-edli-chain-b")
    second = materialize_position_current_from_edli_fill(conn, second_aggregate)
    assert second["position_id"] == first["position_id"]

    before_count = conn.execute(
        """
        SELECT COUNT(*) FROM position_events
         WHERE position_id = ?
           AND event_type = 'MANUAL_OVERRIDE_APPLIED'
           AND source_module = 'src.events.edli_position_bridge'
        """,
        (first["position_id"],),
    ).fetchone()[0]

    conn.execute(
        """
        UPDATE position_current
           SET shares = 5.13,
               cost_basis_usd = 3.6936,
               size_usd = 3.6936,
               entry_price = 0.72,
               chain_state = 'synced',
               chain_shares = 5.13,
               chain_avg_price = 0.72,
               chain_cost_basis_usd = 3.6936
         WHERE position_id = ?
        """,
        (first["position_id"],),
    )

    replay = materialize_position_current_from_edli_fill(conn, second_aggregate)
    row = conn.execute(
        "SELECT shares, cost_basis_usd, size_usd, entry_price, chain_state, chain_shares "
        "FROM position_current WHERE position_id = ?",
        (first["position_id"],),
    ).fetchone()
    after_count = conn.execute(
        """
        SELECT COUNT(*) FROM position_events
         WHERE position_id = ?
           AND event_type = 'MANUAL_OVERRIDE_APPLIED'
           AND source_module = 'src.events.edli_position_bridge'
        """,
        (first["position_id"],),
    ).fetchone()[0]

    assert replay["created"] is False
    assert replay["position_id"] == first["position_id"]
    assert row["chain_state"] == "synced"
    assert row["chain_shares"] == pytest.approx(5.13)
    assert row["shares"] == pytest.approx(5.13)
    assert row["cost_basis_usd"] == pytest.approx(3.6936)
    assert row["size_usd"] == pytest.approx(3.6936)
    assert row["entry_price"] == pytest.approx(0.72)
    assert after_count == before_count


# --------------------------------------------------------------------------- #
# 4. No confirmed fill → nothing to bridge (None)
# --------------------------------------------------------------------------- #

def test_no_confirmed_fill_returns_none(conn):
    aggregate_id = "agg-edli-pending-1"
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=1, event_type="PreSubmitRevalidated",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "condition_id": CONDITION_ID,
                                "token_id": ELECTED_NO_TOKEN, "side": "BUY", "direction": "buy_no"})
    # MATCHED but not CONFIRMED — pending finality, not a confirmed fill.
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=2, event_type="ExecutionCommandCreated",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "execution_command_id": EXECUTION_COMMAND_ID})
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=3, event_type="UserTradeObserved",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "trade_status": "MATCHED",
                                "fill_authority_state": "MATCHED_PENDING_FINALITY", "venue_order_id": VENUE_ORDER_ID,
                                "filled_size": 5.0, "avg_fill_price": 0.5}, source_authority="user_channel")
    assert materialize_position_current_from_edli_fill(conn, aggregate_id) is None
    assert _position_current_rows(conn) == []


# --------------------------------------------------------------------------- #
# 5. Relationship: EDLI audit filled_size == position_current shares
# --------------------------------------------------------------------------- #

def test_relationship_audit_filled_size_equals_position_shares(conn):
    aggregate_id = _seed_confirmed_buy_no_aggregate(conn, fills=[(16.75, 0.42, 0.03)])
    result = materialize_position_current_from_edli_fill(conn, aggregate_id)
    row = _position_current_rows(conn)[0]
    # The bridge's summed filled_size IS the value the EDLI profit-audit would
    # record (both read the same UserTradeObserved economics). Cross-module
    # invariant: position shares == realised fill size.
    assert abs(row["shares"] - result["shares"]) < 1e-12
    assert abs(row["shares"] - 16.75) < 1e-9
    assert abs(row["cost_basis_usd"] - 16.75 * 0.42) < 1e-6


# --------------------------------------------------------------------------- #
# 6. Forward-proof DEFECT-4: two partial fills sum (size-weighted price)
# --------------------------------------------------------------------------- #

def test_forward_proof_two_partial_fills_sum(conn):
    # 10 @ 0.40 and 6 @ 0.50 → 16 shares, cost 4.0+3.0=7.0, vwap 0.4375.
    aggregate_id = _seed_confirmed_buy_no_aggregate(
        conn, aggregate_id="agg-edli-partials-1", fills=[(10.0, 0.40, 0.02), (6.0, 0.50, 0.01)],
    )
    result = materialize_position_current_from_edli_fill(conn, aggregate_id)
    row = _position_current_rows(conn)[0]
    assert abs(row["shares"] - 16.0) < 1e-9
    assert abs(row["cost_basis_usd"] - 7.0) < 1e-9
    assert abs(row["entry_price"] - (7.0 / 16.0)) < 1e-9
    assert abs(result["fees"] - 0.03) < 1e-12


# --------------------------------------------------------------------------- #
# 7. Relationship: chain_reconciliation matches the bridged row BY TOKEN
# --------------------------------------------------------------------------- #

def test_relationship_chain_reconciliation_matches_bridged_row_by_token(conn):
    """Proven for legacy Shanghai: chain reconcile matches by token + sets
    chain_shares. The bridged buy_no row must reconcile the same way."""
    from src.state.chain_reconciliation import reconcile, ChainPosition
    from src.state.db import query_portfolio_loader_view

    aggregate_id = _seed_confirmed_buy_no_aggregate(conn)
    materialize_position_current_from_edli_fill(conn, aggregate_id)
    conn.commit()

    # Load the canonical portfolio (DB-first) — same path the live loader uses.
    snapshot = query_portfolio_loader_view(conn)
    assert snapshot["status"] in ("ok", "partial_stale"), snapshot["status"]
    # Reconstruct Positions from the loader rows the way load_portfolio does.
    portfolio = _portfolio_from_loader(snapshot)
    assert len(portfolio.positions) == 1
    pos = portfolio.positions[0]
    # The chain-match token for a buy_no position is no_token_id.
    match_token = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
    assert match_token == ELECTED_NO_TOKEN

    # Chain returns the elected token with the filled size → must SYNC + set chain_shares.
    chain_positions = [ChainPosition(token_id=ELECTED_NO_TOKEN, size=16.75, avg_price=0.42, cost=16.75 * 0.42, condition_id=CONDITION_ID)]
    stats = reconcile(portfolio, chain_positions, conn=conn)
    conn.commit()

    # chain_shares populated on the bridged row (the stuck-capital cure).
    chain_shares = conn.execute(
        "SELECT chain_shares FROM position_current WHERE position_id = ?",
        (edli_bridge_position_id(aggregate_id),),
    ).fetchone()[0]
    assert chain_shares is not None
    assert abs(float(chain_shares) - 16.75) < 1e-6
    assert stats.get("voided", 0) == 0, "a chain-backed bridged position must NOT be voided"


# --------------------------------------------------------------------------- #
# 8. DEFECT-2: bridged position is EXITABLE by the legacy path
# --------------------------------------------------------------------------- #

def test_defect2_bridged_position_is_exit_eligible_via_legacy_path(conn):
    """The legacy exit lane (_execute_monitoring_phase) manages a position iff
    it loads from position_current as an ACTIVE, tradable-exposure position.

    Proves the bridged row satisfies every precondition the legacy exit path
    requires, so capital is never stuck:
      - loads as a real Position (not synthetic) from the canonical loader;
      - phase 'active' (not in INACTIVE_RUNTIME_STATES);
      - has_tradable_exposure() True (fill_authority is fill-grade);
      - carries the orderbook token the exit lane queries.
    """
    from src.state.db import query_portfolio_loader_view
    from src.state.portfolio import (
        has_tradable_exposure,
        has_verified_trade_fill,
        INACTIVE_RUNTIME_STATES,
    )

    aggregate_id = _seed_confirmed_buy_no_aggregate(conn)
    materialize_position_current_from_edli_fill(conn, aggregate_id)
    conn.commit()

    snapshot = query_portfolio_loader_view(conn)
    assert snapshot["status"] in ("ok", "partial_stale")
    portfolio = _portfolio_from_loader(snapshot)
    assert len(portfolio.positions) == 1
    pos = portfolio.positions[0]

    # ACTIVE / managed (not terminal).
    assert pos.state not in INACTIVE_RUNTIME_STATES
    # The exit lane will manage it: real capital at risk + verified fill.
    assert has_tradable_exposure(pos) is True
    assert has_verified_trade_fill(pos) is True
    # The orderbook query token (no_token_id for buy_no) is present.
    orderbook_token = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
    assert orderbook_token == ELECTED_NO_TOKEN
    assert pos.shares > 0
    assert pos.condition_id == CONDITION_ID  # redeem needs condition_id


# --------------------------------------------------------------------------- #
# WALL-D: relationship test — bridged position + fresh chain observation →
# chain_shares populated via _append_canonical_chain_observation_if_available
# (the no-size-mismatch branch added by task #56).
#
# RED baseline: after bridge materialisation, chain_shares in position_current
# is NULL/0.0 (never set by the bridge itself — chain_state='local_only').
# GREEN: one reconcile cycle with a matching chain observation populates it.
# Uses _position_from_projection_row (the real daemon load path) to ensure the
# full DB round-trip is covered, not just the in-memory position graph.
# --------------------------------------------------------------------------- #

def test_wall_d_bridged_position_chain_shares_null_before_reconcile(conn):
    """RED baseline: bridge materialises position_current with chain_shares NULL/0.

    The bridge sets chain_state='local_only' (no chain observation yet).
    position_current.chain_shares must be NULL (or 0.0, indistinguishable from
    NULL in the DB projection) — chain_shares is NOT set by the bridge itself.
    This is the stuck-capital gap: without reconcile, chain grading is blind.
    """
    aggregate_id = _seed_confirmed_buy_no_aggregate(conn)
    materialize_position_current_from_edli_fill(conn, aggregate_id)
    conn.commit()

    raw = conn.execute(
        "SELECT chain_shares, chain_state FROM position_current WHERE position_id = ?",
        (edli_bridge_position_id(aggregate_id),),
    ).fetchone()
    # chain_state='local_only' — chain observation not yet arrived.
    assert raw["chain_state"] == "local_only"
    # chain_shares is NULL or 0.0 (stored as REAL 0.0 from the Position default;
    # logically equivalent to "not yet chain-observed" for the reconciler).
    # In either case it is NOT the authoritative chain value.
    assert raw["chain_shares"] in (None, 0.0), (
        f"Expected NULL/0.0 (not yet chain-observed) but got {raw['chain_shares']}"
    )


def test_wall_d_bridged_position_chain_shares_populated_after_reconcile(conn):
    """GREEN: bridged position + matching chain observation → chain_shares populated.

    This is the RELATIONSHIP TEST demanded by Wall-D:
      bridge fill → position_current (phase=active, chain_state=local_only)
      reconcile (chain returns elected NO token with fill size, no-size-mismatch)
      → _append_canonical_chain_observation_if_available fires
      → position_current.chain_shares = chain.size (16.75)

    Uses _position_from_projection_row (the real daemon load path) via
    query_portfolio_loader_view + PortfolioState construction to prove the
    full DB round-trip: bridge write → DB → load → reconcile → DB write.
    """
    from src.state.chain_reconciliation import reconcile, ChainPosition
    from src.state.db import query_portfolio_loader_view
    from src.state.portfolio import Position, PortfolioState

    aggregate_id = _seed_confirmed_buy_no_aggregate(conn)
    materialize_position_current_from_edli_fill(conn, aggregate_id)
    conn.commit()

    # Load via the real DB-first path (same as _position_from_projection_row in daemon).
    snapshot = query_portfolio_loader_view(conn)
    assert snapshot["status"] in ("ok", "partial_stale")
    assert len(snapshot["positions"]) == 1

    # Build Position exactly as _position_from_projection_row does (matches daemon load).
    row = dict(snapshot["positions"][0])
    from src.state.portfolio import _position_from_projection_row
    pos = _position_from_projection_row(row, current_mode="live")
    assert pos.chain_state == "local_only"
    # chain_shares from DB (NULL → 0.0 via float(row.get("chain_shares") or 0.0)).
    assert pos.chain_shares == 0.0, f"pre-reconcile chain_shares must be 0.0, got {pos.chain_shares}"
    # no_token_id is the chain-match key for buy_no.
    assert pos.no_token_id == ELECTED_NO_TOKEN

    from src.state.portfolio import PortfolioState
    portfolio = PortfolioState(positions=[pos], bankroll=1000.0, daily_baseline_total=1000.0, weekly_baseline_total=1000.0)

    # Reconcile: chain API returns the elected NO token with the fill size.
    # chain.size == pos.shares (16.75) → no-size-mismatch path → observation write.
    chain_positions = [ChainPosition(
        token_id=ELECTED_NO_TOKEN, size=16.75, avg_price=0.42,
        cost=16.75 * 0.42, condition_id=CONDITION_ID,
    )]
    stats = reconcile(portfolio, chain_positions, conn=conn)
    conn.commit()

    # The canonical write must have fired (chain_observation_persisted counter).
    assert stats.get("chain_observation_persisted", 0) >= 1, (
        "expected _append_canonical_chain_observation_if_available to write at least once"
    )
    assert stats.get("voided", 0) == 0, "chain-backed position must NOT be voided"

    # position_current.chain_shares is now the chain value (NOT NULL/0.0).
    row_after = conn.execute(
        "SELECT chain_shares, chain_state, chain_seen_at FROM position_current WHERE position_id = ?",
        (edli_bridge_position_id(aggregate_id),),
    ).fetchone()
    assert row_after["chain_shares"] is not None, "chain_shares must be populated after reconcile"
    assert abs(float(row_after["chain_shares"]) - 16.75) < 1e-6, (
        f"chain_shares must equal chain.size=16.75, got {row_after['chain_shares']}"
    )
    assert row_after["chain_state"] == "synced"
    assert row_after["chain_seen_at"], "chain_seen_at must be set after observation write"


# --------------------------------------------------------------------------- #
# 9. INV-37: cross-DB ATTACH wiring (the production connection topology).
#    EDLI events live on world.db; position_current is authoritative on trade.db.
#    The bridge must read world.edli_live_order_events and write trade
#    position_current on ONE trade-connection-with-world-ATTACHed (no independent
#    connection). This proves the runtime wiring, not just the single-conn path.
# --------------------------------------------------------------------------- #

def test_inv37_cross_db_attach_bridge(tmp_path):
    import src.state.db as db_module
    from src.state.db import init_schema

    world_path = tmp_path / "zeus-world.db"
    trade_path = tmp_path / "zeus_trades.db"

    # Build both DBs with the full schema (world owns EDLI tables; trade owns
    # position_current / position_events — init_schema creates both sets).
    for p in (world_path, trade_path):
        c = sqlite3.connect(str(p))
        init_schema(c)
        c.commit()
        c.close()

    # Seed EDLI events on the WORLD db (their authoritative home).
    aggregate_id = "agg-edli-inv37-1"
    wc = sqlite3.connect(str(world_path))
    wc.row_factory = sqlite3.Row
    _seed_confirmed_buy_no_aggregate(wc, aggregate_id=aggregate_id)
    wc.commit()
    wc.close()

    # Open the TRADE db and ATTACH world (the production INV-37 topology:
    # get_trade_connection_with_world_required). The bridge reads
    # world.edli_live_order_events and writes trade position_current — SAME conn.
    orig_w = db_module.ZEUS_WORLD_DB_PATH
    try:
        db_module.ZEUS_WORLD_DB_PATH = world_path
        conn = sqlite3.connect(str(trade_path))
        conn.row_factory = sqlite3.Row
        conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))

        # Bridge reads world.edli_live_order_events, writes trade.position_current.
        result = materialize_position_current_from_edli_fill(conn, aggregate_id)
        conn.commit()

        assert result is not None and result["created"] is True
        # position_current row landed on the TRADE db (not world).
        rows = conn.execute("SELECT position_id, no_token_id, shares FROM position_current").fetchall()
        assert len(rows) == 1
        assert rows[0]["no_token_id"] == ELECTED_NO_TOKEN
        assert abs(rows[0]["shares"] - 16.75) < 1e-9
        # The world.db must NOT have received a position_current write through
        # this path (trade is authoritative). The world copy is the ghost shell.
        world_rows = conn.execute("SELECT COUNT(*) FROM world.position_current").fetchone()[0]
        assert world_rows == 0, "bridge must write trade.position_current, never world's ghost shell"
        conn.close()
    finally:
        db_module.ZEUS_WORLD_DB_PATH = orig_w


def _portfolio_from_loader(snapshot):
    """Reconstruct a PortfolioState from query_portfolio_loader_view output.

    Mirrors the subset of load_portfolio's DB-first reconstruction needed to
    exercise chain reconciliation on the bridged row.
    """
    from src.state.portfolio import Position, PortfolioState

    positions = []
    for prow in snapshot["positions"]:
        d = dict(prow)
        # Map loader columns onto Position; phase 'active' → HOLDING runtime state.
        positions.append(
            Position(
                trade_id=d["trade_id"],
                market_id=d.get("market_id") or "",
                city=d.get("city") or "",
                cluster=d.get("cluster") or "",
                target_date=d.get("target_date") or "",
                bin_label=d.get("bin_label") or "",
                direction=d.get("direction") or "buy_no",
                unit=d.get("unit") or "F",
                size_usd=float(d.get("size_usd") or 0.0),
                entry_price=float(d.get("entry_price") or 0.0),
                shares=float(d.get("shares") or 0.0),
                cost_basis_usd=float(d.get("cost_basis_usd") or 0.0),
                token_id=d.get("token_id") or "",
                no_token_id=d.get("no_token_id") or "",
                condition_id=d.get("condition_id") or "",
                env=d.get("env") or "live",
                state="holding",
                strategy_key=d.get("strategy_key") or "settlement_capture",
                entry_fill_verified=True,
                fill_authority=d.get("fill_authority") or "venue_confirmed_full",
            )
        )
    return PortfolioState(positions=positions, bankroll=1000.0, daily_baseline_total=1000.0, weekly_baseline_total=1000.0)


# --------------------------------------------------------------------------- #
# FIX #96: position_id collision-resistance relationship tests
# --------------------------------------------------------------------------- #

# Real brute-force collision pair found under the old 28-bit scheme:
#   ('edli' + sha256_hex)[:11]  for both  'agg-1508' and 'agg-12351'  → 'edlid75be65'
# These two DISTINCT aggregate_ids map to the SAME old short id, which would
# cause ON CONFLICT(position_id) DO UPDATE to SILENTLY MERGE two distinct
# position_current rows — corrupting shares/cost_basis.
_COLLISION_AGG_A = "agg-1508"
_COLLISION_AGG_B = "agg-12351"


def test_position_id_old_scheme_would_collide():
    """RED baseline: confirm the 28-bit truncation merges 'agg-1508' and
    'agg-12351' to the same 11-char id.  This test is not marked xfail — it
    documents the vulnerability of the old scheme and will pass forever
    (old_id() is a local helper, not the production function).
    """
    import hashlib

    def _old_id(aggregate_id: str) -> str:
        digest = hashlib.sha256(str(aggregate_id).encode("utf-8")).hexdigest()
        return ("edli" + digest)[:11]

    id_a = _old_id(_COLLISION_AGG_A)
    id_b = _old_id(_COLLISION_AGG_B)
    # Both must collide under the old scheme — this IS the bug.
    assert id_a == id_b, (
        f"Expected 28-bit collision but got distinct ids: {id_a!r} vs {id_b!r}"
    )
    assert id_a == "edlid75be65"


def test_position_id_distinct_for_known_collision_pair():
    """GREEN (FIX #96): the production edli_bridge_position_id must produce
    DISTINCT ids for the known collision pair that was identical under the
    old 28-bit scheme.  Would have FAILED before this fix.
    """
    id_a = edli_bridge_position_id(_COLLISION_AGG_A)
    id_b = edli_bridge_position_id(_COLLISION_AGG_B)
    assert id_a != id_b, (
        f"Collision regression: 'agg-1508' and 'agg-12351' produce same id {id_a!r}"
    )
    # Width: 4 literal "edli" + 64 hex chars = 68 chars
    assert len(id_a) == 68
    assert len(id_b) == 68
    assert id_a.startswith("edli")
    assert id_b.startswith("edli")


def test_two_distinct_fills_create_two_distinct_position_current_rows(conn):
    """Relationship test (FIX #96): two CONFIRMED fills with DISTINCT aggregate_ids
    that would have collided under the old 28-bit scheme MUST create TWO distinct
    position_current rows — no silent merge via ON CONFLICT DO UPDATE.

    Uses the brute-force-found collision pair ('agg-1508', 'agg-12351') so the
    test directly exercises the pre-fix vulnerability.  Under the old scheme
    both produced 'edlid75be65' (28 bits), causing the second
    materialize_position_current_from_edli_fill to overwrite the first row.
    """
    # Seed two full aggregates with distinct condition/token ids (each needs a
    # PreSubmitRevalidated event for identity resolution).
    for i, aggregate_id in enumerate((_COLLISION_AGG_A, _COLLISION_AGG_B)):
        cond = f"0xcond-collision-{i}"
        token = f"token-yes-collision-{i}"
        pre_submit = {
            "event_id": f"evt-{aggregate_id}",
            "event_type": "FORECAST_SNAPSHOT_READY",
            "final_intent_id": f"intent-{aggregate_id}",
            "strategy_key": "center_buy",
            "condition_id": cond,
            "token_id": token,
            "side": "BUY",
            "direction": "buy_yes",
            "native_token_side": "YES",
            "outcome_label": "YES",
            "city": "Shanghai",
            "target_date": "2026-06-02",
            "bin_label": "30-32",
            "metric": "high",
            "unit": "C",
            "market_id": cond,
            "q_live": 0.55,
            "executable_snapshot_id": f"snap-{aggregate_id}",
        }
        _insert_edli_event(
            conn, aggregate_id=aggregate_id, sequence=1,
            event_type="PreSubmitRevalidated", payload=pre_submit,
        )
        _insert_edli_event(
            conn, aggregate_id=aggregate_id, sequence=2,
            event_type="UserTradeObserved",
            payload={
                "event_id": f"evt-{aggregate_id}",
                "final_intent_id": f"intent-{aggregate_id}",
                "fill_authority_state": "FILL_CONFIRMED",
                "trade_status": "CONFIRMED",
                "venue_order_id": f"vord-{aggregate_id}",
                "filled_size": 10.0,
                "avg_fill_price": 0.55,
                "fees": 0.01,
            },
        )
        materialize_position_current_from_edli_fill(conn, aggregate_id)

    rows = conn.execute("SELECT position_id FROM position_current ORDER BY position_id").fetchall()
    ids = [r[0] for r in rows]
    assert len(ids) == 2, (
        f"Expected 2 distinct position_current rows; got {len(ids)}: {ids}"
    )
    assert ids[0] != ids[1], "Silent merge: two distinct fills produced one position_current row"
    # Each id must be the full-width 68-char form
    for pid in ids:
        assert len(pid) == 68, f"Expected 68-char position_id, got {len(pid)!r}: {pid!r}"
        assert pid.startswith("edli")
