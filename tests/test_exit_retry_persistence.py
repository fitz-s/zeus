# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: exit_pending_missing infinite-loop incident 2026-06-12
#   (HK 06-09: 724 identical EXIT_ORDER_REJECTED events, retry_count pinned at
#   0 because the field lived only on the in-memory Position and every
#   load_portfolio() reset it) + /tmp/exit_retry_loop_rootcause.md Fix B.
"""ANTIBODY: exit-retry backoff state survives the projection round-trip.

The chain-truth gate's bounded backoff (_mark_exit_retry -> exit_retry_count,
next_exit_retry_at -> MAX_EXIT_RETRIES -> backoff_exhausted -> persisted admin
close) is only bounded if the counter SURVIVES daemon cycles. Pre-fix the
counter was projected nowhere: position_current had no columns for it, the
loader never read it, and every cycle restarted the count at zero — the
terminal state was unreachable by construction.
"""
from __future__ import annotations

import sqlite3

from src.engine.lifecycle_events import build_position_current_projection
from src.state.db import init_schema, query_portfolio_loader_view
from src.state.portfolio import Position, _position_from_projection_row
from src.state.projection import upsert_position_current


def _position(**kw) -> Position:
    defaults = dict(
        trade_id="t-retry-1",
        market_id="m1",
        city="Hong Kong",
        cluster="Hong Kong",
        target_date="2026-06-09",
        bin_label="Will the highest temperature in Hong Kong be 31°C on June 9?",
        direction="buy_no",
        unit="C",
        temperature_metric="high",
        condition_id="0x" + "ab" * 32,
        token_id="tok-yes-1",
        no_token_id="tok-no-1",
        strategy_key="settlement_capture",
        env="live",
        state="pending_exit",
        chain_state="exit_pending_missing",
        exit_state="retry_pending",
        exit_retry_count=4,
        next_exit_retry_at="2026-06-12T13:00:00+00:00",
        entry_price=0.93,
        p_posterior=0.9,
        shares=19.0,
        size_usd=17.67,
        cost_basis_usd=17.67,
        entered_at="2026-06-09T15:00:00+00:00",
    )
    defaults.update(kw)
    return Position(**defaults)


def test_projection_carries_exit_retry_state():
    proj = build_position_current_projection(_position())
    assert proj["exit_retry_count"] == 4
    assert proj["next_exit_retry_at"] == "2026-06-12T13:00:00+00:00"


def test_exit_retry_state_survives_db_round_trip():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    pos = _position()
    upsert_position_current(conn, build_position_current_projection(pos))
    conn.commit()

    view = query_portfolio_loader_view(conn)
    row = next(r for r in view["positions"] if r["trade_id"] == pos.trade_id)
    assert int(row["exit_retry_count"] or 0) == 4
    assert row["next_exit_retry_at"] == "2026-06-12T13:00:00+00:00"

    reloaded = _position_from_projection_row(row, current_mode="live")
    assert reloaded.exit_retry_count == 4
    assert reloaded.next_exit_retry_at == "2026-06-12T13:00:00+00:00"


def test_legacy_row_without_retry_columns_defaults_to_zero():
    """Rows written before the migration load as count 0 / no cooldown."""
    reloaded = _position_from_projection_row(
        current_mode="live",
        row={
            "trade_id": "legacy-1",
            "market_id": "m1",
            "city": "Hong Kong",
            "cluster": "Hong Kong",
            "target_date": "2026-06-09",
            "bin_label": "b",
            "direction": "buy_no",
            "unit": "C",
            "temperature_metric": "high",
            "phase": "pending_exit",
            "strategy_key": "settlement_capture",
            "env": "live",
            "chain_state": "unknown",
        }
    )
    assert reloaded.exit_retry_count == 0
    assert reloaded.next_exit_retry_at is None
