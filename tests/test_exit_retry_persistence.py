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
    assert reloaded.exit_state == "retry_pending"


def test_pending_exit_retry_state_recovers_when_projection_has_no_exit_state_field():
    """Live position_current persists retry fields but not an exit_state column."""
    reloaded = _position_from_projection_row(
        current_mode="live",
        row={
            "trade_id": "retry-no-exit-state",
            "market_id": "m1",
            "city": "Chengdu",
            "cluster": "Chengdu",
            "target_date": "2026-06-19",
            "bin_label": "b",
            "direction": "buy_no",
            "unit": "C",
            "temperature_metric": "high",
            "phase": "pending_exit",
            "strategy_key": "settlement_capture",
            "env": "live",
            "chain_state": "synced",
            "exit_retry_count": 2,
            "next_exit_retry_at": "2026-06-18T07:04:40+00:00",
        },
    )

    assert reloaded.state == "pending_exit"
    assert reloaded.exit_retry_count == 2
    assert reloaded.next_exit_retry_at == "2026-06-18T07:04:40+00:00"
    assert reloaded.exit_state == "retry_pending"


def test_channel_not_ready_ws_gap_does_not_consume_retry_budget():
    """ANTIBODY (2026-06-23 exit-execution diagnosis): a TRANSIENT submit-channel
    gap (user-channel WS disconnect → m5_reconcile_required) must NOT march a
    still-sellable position to backoff_exhausted/admin-close. The exit must keep
    retrying so it can sell once the channel reconnects — the operator's "react to
    reversal, sell before the market notices" mandate. Pre-fix, ws_gap rejections
    burned the bounded exit-retry budget and abandoned correct reversal exits.
    """
    from src.execution.exit_lifecycle import _mark_exit_retry, MAX_EXIT_RETRIES

    pos = _position(exit_retry_count=0)
    err = "ws_gap=DISCONNECTED:websocket_disconnect;m5_reconcile_required=True"
    for _ in range(MAX_EXIT_RETRIES + 5):
        _mark_exit_retry(
            pos,
            reason=f"CI_SEPARATED_REVERSAL [SELL_ERROR: {err}]",
            error=err,
            conn=None,
        )
    assert pos.exit_retry_count == 0, (
        "a transient ws_gap/m5_reconcile rejection must NOT consume the bounded "
        "exit-retry budget; the position is still sellable once the channel returns"
    )
    assert pos.exit_state == "retry_pending", (
        "channel-not-ready exit must stay retry_pending forever, never "
        "backoff_exhausted (which abandons a sellable reversal exit)"
    )
    assert pos.next_exit_retry_at is not None


def test_genuine_error_still_consumes_budget_to_backoff():
    """Control: a genuine (non-channel) rejection still marches to backoff_exhausted
    after MAX_EXIT_RETRIES — the fix must not disable the real terminal path."""
    from src.execution.exit_lifecycle import _mark_exit_retry, MAX_EXIT_RETRIES

    pos = _position(exit_retry_count=0)
    for _ in range(MAX_EXIT_RETRIES):
        _mark_exit_retry(pos, reason="X", error="some_persistent_rejection", conn=None)
    assert pos.exit_retry_count == MAX_EXIT_RETRIES
    assert pos.exit_state == "backoff_exhausted"


def test_market_end_is_terminal_not_channel_not_ready():
    """A market-ended SELL snapshot is genuinely un-sellable (settles), so it must
    REMAIN budget-consuming — it must NOT be reclassified as a transient retry
    (that would retry forever on a settled market)."""
    from src.execution.exit_lifecycle import _mark_exit_retry, MAX_EXIT_RETRIES

    pos = _position(exit_retry_count=0)
    for _ in range(MAX_EXIT_RETRIES):
        _mark_exit_retry(
            pos,
            reason="DAY0 [SELL_ERROR: executable_snapshot_market_end]",
            error="executable_snapshot_market_end",
            conn=None,
        )
    assert pos.exit_retry_count == MAX_EXIT_RETRIES
    assert pos.exit_state == "backoff_exhausted"


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
