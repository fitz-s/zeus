"""Antibody: exchange_reconcile must NOT fabricate-zero chain holdings on a
venue underfill.

Review blocker C2 class. The removed ``_EXIT_FULL_CLOSE_DUST_TOLERANCE``
(``Decimal("0.011")``) let ``_ensure_exit_fill_position_event`` treat a sell that
covered the EXIT command but left an on-chain residual (holding > command) as a
full economic close, after which it fabricated chain_shares / chain_avg_price /
chain_cost_basis_usd to zero with NO chain-confirmed-zero proof.

The fix refuses the economic close unless the exact fill covers the exact
on-record holding (``max(command, shares, chain_shares)``); an underfill of the
holding preserves the residual for the chain-confirmed-zero reconciler.

The underfill assertions FAIL on pre-fix (the close projection runs and zeroes
chain_shares) and pass after; the full-fill case still closes and zeroes.
"""

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.execution import exchange_reconcile


NOW = datetime(2026, 6, 19, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    init_collateral_schema(c)
    yield c
    c.close()


def _seed_pending_exit_position(conn, *, position_id, shares, chain_shares):
    shares_d = Decimal(shares)
    chain_d = Decimal(chain_shares)
    price = Decimal("0.12")
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, bin_label, direction, unit,
            shares, cost_basis_usd, entry_price, p_posterior,
            chain_shares, chain_avg_price, chain_cost_basis_usd, chain_state,
            strategy_key, token_id, order_id, order_status, exit_reason,
            temperature_metric, updated_at
        ) VALUES (?, 'pending_exit', 'Hong Kong', '2026-06-19', '21C', 'buy_no', 'F',
                  ?, ?, ?, 0.5,
                  ?, ?, ?, 'synced',
                  'opening_inertia', 'tok-exit', 'ord-exit', 'sell_placed',
                  'DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES', 'low', ?)
        """,
        (
            position_id,
            str(shares_d),
            str(shares_d * price),
            str(price),
            str(chain_d),
            str(price),
            str(chain_d * price),
            NOW.isoformat(),
        ),
    )
    conn.commit()


def _exit_command(position_id, *, command_size):
    return {
        "command_id": "cmd-exit",
        "position_id": position_id,
        "intent_kind": "EXIT",
        "side": "SELL",
        "size": str(command_size),
    }


def _exit_filled_event_count(conn, position_id):
    return conn.execute(
        """
        SELECT COUNT(*) AS n FROM position_events
         WHERE position_id = ? AND event_type = 'EXIT_ORDER_FILLED'
        """,
        (position_id,),
    ).fetchone()["n"]


def _chain_shares(conn, position_id):
    row = conn.execute(
        "SELECT chain_shares FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    return Decimal(str(row["chain_shares"]))


class TestUnderfillDoesNotFabricateZeroChain:
    @pytest.mark.parametrize("delta", ["0.001", "0.010", "0.011", "0.012"])
    def test_underfill_of_holding_preserves_chain_residual(self, conn, delta):
        holding = Decimal("1.000")
        filled = holding - Decimal(delta)
        _seed_pending_exit_position(
            conn, position_id="pos-exit", shares="1.000", chain_shares="1.000"
        )
        # EXIT command fully fills (command_size == filled) but below the holding.
        exchange_reconcile._ensure_exit_fill_position_event(
            conn,
            command=_exit_command("pos-exit", command_size=filled),
            venue_order_id="ord-exit",
            filled_size=str(filled),
            fill_price="0.10",
            observed_at=NOW,
            command_event="FILL_CONFIRMED",
        )
        # underfill of the holding -> NOT a full close -> chain residual survives,
        # no fabricated-zero, no economic-close event.
        assert _exit_filled_event_count(conn, "pos-exit") == 0
        assert _chain_shares(conn, "pos-exit") == holding

    def test_full_fill_of_holding_closes_and_zeroes_chain(self, conn):
        _seed_pending_exit_position(
            conn, position_id="pos-exit", shares="1.000", chain_shares="1.000"
        )
        exchange_reconcile._ensure_exit_fill_position_event(
            conn,
            command=_exit_command("pos-exit", command_size="1.000"),
            venue_order_id="ord-exit",
            filled_size="1.000",
            fill_price="0.10",
            observed_at=NOW,
            command_event="FILL_CONFIRMED",
        )
        # the fill covers the entire on-record holding -> genuine full close.
        assert _exit_filled_event_count(conn, "pos-exit") == 1
        assert _chain_shares(conn, "pos-exit") == Decimal("0")
