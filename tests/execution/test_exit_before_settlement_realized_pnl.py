# Created: 2026-07-07
# Last audited: 2026-07-07
# Authority basis: root-cause finding on truth-path PnL-booking gap (~91% of
#   recently-settled positions carry NULL/0.0 position_current.realized_pnl_usd).
"""Bug A antibody: exit-before-settlement fills must durably book realized P&L.

BUG #128 (2026-06-02, see tests/state/test_bug128_realized_pnl_durable.py) made
the settlement/economic-close write path durable for a REAL in-memory Position
object: compute_settlement_close / compute_economic_close set Position.pnl,
and build_position_current_projection -> _settled_economics_value(position,
"pnl") copies it into position_current.realized_pnl_usd.

But two restart/reconcile-time repair writers build a SimpleNamespace
*stand-in* for a Position directly from the raw position_current row instead
of routing through the real in-memory Position object:
  - src.execution.command_recovery._append_exit_filled_projection
  - src.execution.exchange_reconcile._ensure_exit_fill_position_event

Both set "last_exit_at" and "exit_price" on the stand-in (so
_has_realized_close(position) is True -- this is NOT an open/legacy row) but
never set a "pnl" attribute. _settled_economics_value(position, "pnl") does
getattr(position, "pnl", None) -> None, so the write persists a real
exit_price but a NULL realized_pnl_usd. compute_settlement_close later sees
was_economically_closed=True and deliberately skips recomputing pnl, so the
position settles with realized_pnl_usd frozen at 0.0 forever -- Zeus is blind
to a huge share of its own realized wins/losses.

Fix: mirror src.state.portfolio._compute_realized_pnl's formula
(round(effective_shares * exit_price - effective_cost_basis_usd, 2), guarded
by entry_price > 0) using the fill/current economics already available at the
SimpleNamespace call site, and add it as an explicit "pnl" key.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest


def _world_conn() -> sqlite3.Connection:
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _exit_fill_current_row(
    *,
    position_id: str,
    direction: str,
    shares: float,
    cost_basis_usd: float,
    entry_price: float,
) -> dict:
    """Shaped like the dict _exit_pending_projection_candidates hands to
    _append_exit_filled_projection as `current` (the raw position_current row)."""
    return {
        "position_id": position_id,
        "phase": "pending_exit",
        "market_id": f"mkt-{position_id}",
        "city": "Karachi",
        "cluster": "Karachi",
        "target_date": "2026-07-01",
        "bin_label": "30-35",
        "direction": direction,
        "unit": "F",
        "shares": shares,
        "cost_basis_usd": cost_basis_usd,
        "entry_price": entry_price,
        "chain_state": "synced",
        "strategy_key": "test_strategy",
        "order_id": "",
        "exit_reason": "",
        "temperature_metric": "high",
    }


class TestCommandRecoveryExitFillRealizedPnl:
    """src.execution.command_recovery._append_exit_filled_projection"""

    def test_buy_no_exit_fill_projects_realized_pnl(self):
        from src.execution.command_recovery import _append_exit_filled_projection

        conn = _world_conn()
        current = _exit_fill_current_row(
            position_id="pos-cr-buyno",
            direction="buy_no",
            shares=10.0,
            cost_basis_usd=3.0,
            entry_price=0.30,
        )
        candidate = {
            "cmd_command_id": "cmd-cr-buyno",
            "cmd_venue_order_id": "ord-cr-buyno",
            "fill_filled_size": "10.0",
            "fill_avg_price": "0.55",
            "cmd_decision_id": "dec-cr-buyno",
            "cmd_price": "0.55",
            "cmd_updated_at": "2026-07-01T00:00:00+00:00",
        }

        _append_exit_filled_projection(
            conn,
            candidate=candidate,
            current=current,
            occurred_at="2026-07-01T00:05:00+00:00",
        )

        row = conn.execute(
            "SELECT phase, realized_pnl_usd, exit_price FROM position_current WHERE position_id = ?",
            ("pos-cr-buyno",),
        ).fetchone()
        assert row is not None
        assert row["phase"] == "economically_closed"
        assert row["exit_price"] == pytest.approx(0.55)
        # pnl = shares * fill_price - cost_basis_usd = 10*0.55 - 3.0 = 2.5
        assert row["realized_pnl_usd"] is not None, (
            "BUG A: exit-before-settlement fill must project realized_pnl_usd, got NULL"
        )
        assert row["realized_pnl_usd"] == pytest.approx(2.5), (
            f"expected realized_pnl_usd 2.5, got {row['realized_pnl_usd']}"
        )
        conn.close()

    def test_buy_yes_exit_fill_projects_realized_pnl(self):
        """Same formula must hold for buy_yes: entry_price/exit_price are always
        native to the held direction (see Position docstring INVARIANT), so no
        separate sign handling is required for buy_no vs buy_yes."""
        from src.execution.command_recovery import _append_exit_filled_projection

        conn = _world_conn()
        current = _exit_fill_current_row(
            position_id="pos-cr-buyyes",
            direction="buy_yes",
            shares=5.0,
            cost_basis_usd=3.0,
            entry_price=0.60,
        )
        candidate = {
            "cmd_command_id": "cmd-cr-buyyes",
            "cmd_venue_order_id": "ord-cr-buyyes",
            "fill_filled_size": "5.0",
            "fill_avg_price": "0.20",
            "cmd_decision_id": "dec-cr-buyyes",
            "cmd_price": "0.20",
            "cmd_updated_at": "2026-07-01T00:00:00+00:00",
        }

        _append_exit_filled_projection(
            conn,
            candidate=candidate,
            current=current,
            occurred_at="2026-07-01T00:05:00+00:00",
        )

        row = conn.execute(
            "SELECT realized_pnl_usd, exit_price FROM position_current WHERE position_id = ?",
            ("pos-cr-buyyes",),
        ).fetchone()
        assert row is not None
        assert row["exit_price"] == pytest.approx(0.20)
        # pnl = shares * fill_price - cost_basis_usd = 5*0.20 - 3.0 = -2.0
        assert row["realized_pnl_usd"] == pytest.approx(-2.0), (
            f"expected realized_pnl_usd -2.0, got {row['realized_pnl_usd']}"
        )
        conn.close()


class TestExchangeReconcileExitFillRealizedPnl:
    """src.execution.exchange_reconcile._ensure_exit_fill_position_event"""

    @staticmethod
    def _insert_active_position(
        conn: sqlite3.Connection,
        *,
        position_id: str,
        direction: str,
        shares: float,
        cost_basis_usd: float,
        entry_price: float,
    ) -> None:
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, trade_id, market_id, city, cluster,
                target_date, bin_label, direction, unit, shares,
                cost_basis_usd, entry_price, strategy_key, chain_state,
                token_id, no_token_id, condition_id, order_id, order_status,
                updated_at, temperature_metric
            ) VALUES (
                ?, 'active', ?, ?, 'Karachi', 'Karachi',
                '2026-07-01', '30-35', ?, 'F', ?,
                ?, ?, 'test_strategy', 'synced',
                ?, ?, 'cond-1', 'ord-entry', 'filled',
                '2026-07-01T00:00:00+00:00', 'high'
            )
            """,
            (
                position_id, position_id, f"mkt-{position_id}",
                direction, shares,
                cost_basis_usd, entry_price,
                f"tok-{position_id}", f"tok-{position_id}-no",
            ),
        )
        conn.commit()

    def test_buy_no_exit_fill_projects_realized_pnl(self):
        from src.execution.exchange_reconcile import _ensure_exit_fill_position_event

        conn = _world_conn()
        self._insert_active_position(
            conn,
            position_id="pos-xr-buyno",
            direction="buy_no",
            shares=10.0,
            cost_basis_usd=3.0,
            entry_price=0.30,
        )
        command = {
            "intent_kind": "EXIT",
            "side": "SELL",
            "position_id": "pos-xr-buyno",
            "command_id": "cmd-xr-buyno",
            "decision_id": "dec-xr-buyno",
            "price": 0.55,
            "created_at": "2026-07-01T00:00:00+00:00",
        }

        _ensure_exit_fill_position_event(
            conn,
            command=command,
            venue_order_id="ord-xr-buyno",
            filled_size="10.0",
            fill_price="0.55",
            observed_at=datetime(2026, 7, 1, 0, 5, tzinfo=timezone.utc),
            command_event="FILL_CONFIRMED",
        )

        row = conn.execute(
            "SELECT phase, realized_pnl_usd, exit_price FROM position_current WHERE position_id = ?",
            ("pos-xr-buyno",),
        ).fetchone()
        assert row is not None
        assert row["phase"] == "economically_closed"
        assert row["exit_price"] == pytest.approx(0.55)
        # pnl = shares * fill_price - cost_basis_usd = 10*0.55 - 3.0 = 2.5
        assert row["realized_pnl_usd"] is not None, (
            "BUG A: exit-before-settlement fill must project realized_pnl_usd, got NULL"
        )
        assert row["realized_pnl_usd"] == pytest.approx(2.5), (
            f"expected realized_pnl_usd 2.5, got {row['realized_pnl_usd']}"
        )
        conn.close()
