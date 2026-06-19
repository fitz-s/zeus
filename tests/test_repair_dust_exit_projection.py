# Lifecycle: created=2026-06-18; last_reviewed=2026-06-18; last_reused=2026-06-18
# Purpose: Regression tests for dust pending-exit projection repair.
# Reuse: pytest tests/test_repair_dust_exit_projection.py
# Authority basis: AGENTS.md position/execution truth gate.

from __future__ import annotations

import json
import sqlite3

from scripts import repair_dust_exit_projection as repair
from src.state.db import init_schema
from src.state.ledger import append_many_and_project
from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS


def _projection(position_id: str, *, order_status: str = "filled") -> dict:
    projection = {col: None for col in CANONICAL_POSITION_CURRENT_COLUMNS}
    projection.update(
        {
            "position_id": position_id,
            "phase": "pending_exit",
            "trade_id": position_id,
            "market_id": "market-1",
            "city": "Qingdao",
            "target_date": "2026-06-19",
            "temperature_metric": "high",
            "bin_label": "Will the highest temperature in Qingdao be 24C on June 19?",
            "direction": "buy_no",
            "unit": "C",
            "size_usd": 0.001,
            "shares": 0.01,
            "cost_basis_usd": 0.001,
            "entry_price": 0.74,
            "strategy_key": "center_buy",
            "token_id": "token-no",
            "no_token_id": "token-no",
            "condition_id": "condition-1",
            "order_id": "order-1",
            "order_status": order_status,
            "updated_at": "2026-06-18T10:41:43+00:00",
            "chain_state": "synced",
            "chain_shares": 0.01,
            "chain_avg_price": 0.10,
            "chain_cost_basis_usd": 0.001,
            "chain_seen_at": "2026-06-18T10:41:43+00:00",
            "exit_reason": "EXIT_CHAIN_DUST_STILL_HELD",
        }
    )
    return projection


def _seed_dust_backoff(conn: sqlite3.Connection, position_id: str = "dust-pos") -> None:
    projection = _projection(position_id)
    event = {
        "event_id": f"{position_id}:initial-backoff",
        "position_id": position_id,
        "event_version": 1,
        "sequence_no": 1,
        "event_type": "EXIT_ORDER_REJECTED",
        "occurred_at": "2026-06-18T10:41:43+00:00",
        "phase_before": "pending_exit",
        "phase_after": "pending_exit",
        "strategy_key": "center_buy",
        "decision_id": None,
        "snapshot_id": None,
        "order_id": "order-1",
        "command_id": None,
        "caused_by": "test",
        "idempotency_key": f"{position_id}:initial-backoff",
        "venue_status": "backoff_exhausted",
        "source_module": "test",
        "env": "live",
        "payload_json": json.dumps(
            {
                "status": "backoff_exhausted",
                "exit_reason": "EXIT_CHAIN_DUST_STILL_HELD",
            },
            sort_keys=True,
        ),
    }
    append_many_and_project(conn, [event], projection)


def test_repair_candidates_require_existing_backoff_evidence() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    try:
        _seed_dust_backoff(conn)
        candidates = repair.repair_candidates(conn)

        assert len(candidates) == 1
        assert candidates[0].position_id == "dust-pos"
        assert candidates[0].backoff_events == 1
    finally:
        conn.close()


def test_apply_repair_updates_projection_and_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    try:
        _seed_dust_backoff(conn)
        candidate = repair.repair_candidates(conn)[0]

        first = repair.apply_repair(conn, candidate, occurred_at="2026-06-18T11:00:00+00:00")
        second = repair.apply_repair(conn, candidate, occurred_at="2026-06-18T11:01:00+00:00")

        row = conn.execute(
            "SELECT order_status, updated_at FROM position_current WHERE position_id = 'dust-pos'"
        ).fetchone()
        events = conn.execute(
            """
            SELECT COUNT(*) AS count
              FROM position_events
             WHERE position_id = 'dust-pos'
               AND event_type = 'EXIT_ORDER_REJECTED'
               AND idempotency_key = 'dust-pos:dust_backoff_projection_reload_repair'
            """
        ).fetchone()
        assert first == "event_appended_and_projection_repaired"
        assert second == "projection_refreshed"
        assert row["order_status"] == "backoff_exhausted"
        assert row["updated_at"] == "2026-06-18T11:01:00+00:00"
        assert events["count"] == 1
    finally:
        conn.close()
