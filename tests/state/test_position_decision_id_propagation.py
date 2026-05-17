# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: WAVE_2_PLAN.md §WAVE-B #27 F7-follow-up — Position.decision_id propagation to exit execution_fact
"""
F7-follow-up antibody: Position.decision_id must propagate to exit-side execution_fact row.

Bug: c30f28a5-d4e:exit had decision_id=NULL because Position had no decision_id field,
so log_exit_lifecycle_event could not forward it to log_execution_fact.
"""

from __future__ import annotations

import pytest

from src.state.db import (
    get_connection,
    init_schema,
    log_exit_lifecycle_event,
)
from src.state.portfolio import Position


def _make_test_position(trade_id: str, decision_id: str | None = "test-dec-001") -> Position:
    """Minimal Position instance for testing exit-side plumbing."""
    return Position(
        trade_id=trade_id,
        market_id="test-market-001",
        city="TestCity",
        cluster="Test",
        target_date="2026-06-01",
        bin_label="70-80°F",
        direction="buy_yes",
        temperature_metric="high",
        env="test",
        state="holding",
        decision_id=decision_id,
    )


def test_exit_execution_fact_carries_decision_id(tmp_path):
    """
    F7-follow-up: log_exit_lifecycle_event must forward Position.decision_id
    to execution_fact for exit rows.
    """
    conn = get_connection(tmp_path / "f7_followup.db")
    init_schema(conn)

    pos = _make_test_position("trade-f7-001", decision_id="test-dec-001")
    assert pos.decision_id == "test-dec-001", "Position must carry decision_id"

    log_exit_lifecycle_event(
        conn,
        pos,
        event_type="EXIT_ORDER_POSTED",
        status="sell_placed",
        timestamp="2026-05-17T12:00:00Z",
    )
    conn.commit()

    row = conn.execute(
        "SELECT decision_id FROM execution_fact WHERE intent_id = ? AND order_role = 'exit'",
        (f"{pos.trade_id}:exit",),
    ).fetchone()
    assert row is not None, "exit execution_fact row must exist after EXIT_ORDER_POSTED"
    assert row["decision_id"] == "test-dec-001", (
        f"F7-follow-up: exit execution_fact.decision_id must carry Position.decision_id, "
        f"got {row['decision_id']!r}"
    )
    conn.close()


def test_exit_execution_fact_null_decision_id_when_position_has_none(tmp_path):
    """When Position.decision_id is None, exit execution_fact.decision_id may be NULL (pre-existing positions)."""
    conn = get_connection(tmp_path / "f7_followup_null.db")
    init_schema(conn)

    pos = _make_test_position("trade-f7-002", decision_id=None)
    assert pos.decision_id is None

    log_exit_lifecycle_event(
        conn,
        pos,
        event_type="EXIT_ORDER_POSTED",
        status="sell_placed",
        timestamp="2026-05-17T12:00:00Z",
    )
    conn.commit()

    row = conn.execute(
        "SELECT decision_id FROM execution_fact WHERE intent_id = ? AND order_role = 'exit'",
        (f"{pos.trade_id}:exit",),
    ).fetchone()
    assert row is not None, "exit execution_fact row must exist"
    # NULL is acceptable when no decision_id was available (legacy / pre-fix positions)
    assert row["decision_id"] is None
    conn.close()
