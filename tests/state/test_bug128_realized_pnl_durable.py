# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: BUG #128 (SEV1) — realized P&L has no durable DB column.
"""BUG #128 antibody: realized P&L must survive the in-memory Portfolio object.

Pre-fix, `position_current` (state/zeus_trades.db) carried NO realized_pnl /
exit_price / settlement_price / settled_at column. The compute at
`src/state/portfolio.py::_compute_realized_pnl` is correct
(``round(effective_shares * exit_price - effective_cost_basis_usd, 2)``) but the
result lived ONLY on the in-memory ``Position.pnl`` attribute and in
``positions.json recent_exits[].pnl``. The canonical settlement/economic-close
projection (``build_position_current_projection``) did NOT copy these fields into
durable columns, so a filled+settled order left no queryable P&L record — losing
``positions.json`` meant permanent P&L loss.

GOAL#36 requires each filled order be e2e-correctness-checked AFTER the fact,
which is impossible without a durable, queryable realized-P&L row.

The fix adds nullable columns (realized_pnl_usd, exit_price, settlement_price,
settled_at, exit_reason) to ``position_current`` and populates them through the
CANONICAL write path (``build_position_current_projection`` →
``append_many_and_project``), so EVERY close path persists P&L automatically.

Relationship invariant under test (Fitz Constraint: test the cross-module
boundary, not the function): when ``compute_settlement_close`` /
``compute_economic_close`` (Portfolio in-memory) hand a closed Position to the
canonical write path (DB projection), the realized P&L MUST cross the boundary
into durable ``position_current`` columns and remain readable after the
in-memory object is discarded.
"""
from __future__ import annotations

import sqlite3

import pytest


def _setup_world_db() -> sqlite3.Connection:
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _make_filled_position(
    *,
    trade_id: str,
    city: str,
    entry_price: float,
    shares: float,
    cost_basis_usd: float,
):
    """A trade-verified, filled position ready to settle.

    fill_authority=venue_confirmed_full so effective_shares /
    effective_cost_basis_usd route through the fill economics (shares_filled,
    filled_cost_basis_usd) — the live settlement path.
    """
    from src.state.portfolio import (
        ENTRY_ECONOMICS_AVG_FILL_PRICE,
        FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        Position,
    )

    return Position(
        trade_id=trade_id,
        market_id=f"m-{trade_id}",
        city=city,
        cluster=city,
        target_date="2026-06-02",
        bin_label="60-65",
        direction="buy_yes",
        unit="F",
        env="test",
        token_id=f"t-{trade_id}",
        no_token_id=f"t-{trade_id}-no",
        condition_id=f"c-{trade_id}",
        order_id=f"ord-{trade_id}",
        state="entered",
        chain_state="synced",
        entered_at="2026-06-01T00:00:00+00:00",
        entry_price=entry_price,
        size_usd=cost_basis_usd,
        cost_basis_usd=cost_basis_usd,
        shares=shares,
        entry_price_avg_fill=entry_price,
        shares_filled=shares,
        filled_cost_basis_usd=cost_basis_usd,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        decision_snapshot_id=f"snap-{trade_id}",
        entry_method="ens_member_counting",
        strategy_key="settlement_capture",
    )


def _project_settlement(conn, pos, *, won: bool, settlement_price: float) -> None:
    """Settle the position in the in-memory portfolio, then write it through the
    canonical settlement path into position_current."""
    from src.engine.lifecycle_events import build_settlement_canonical_write
    from src.state.db import append_many_and_project
    from src.state.portfolio import (
        PortfolioState,
        compute_settlement_close,
    )

    portfolio = PortfolioState(positions=[pos])
    closed = compute_settlement_close(
        portfolio, pos.trade_id, settlement_price, "SETTLEMENT"
    )
    assert closed is not None

    events, projection = build_settlement_canonical_write(
        closed,
        winning_bin=closed.bin_label,
        won=won,
        outcome=1 if won else 0,
        sequence_no=1,
        phase_before="active",
        settlement_value=settlement_price,
    )
    append_many_and_project(conn, events, projection)
    return closed


def test_settled_realized_pnl_is_durable_after_object_discard() -> None:
    """RED→GREEN: after settling a WINNING position and projecting it through the
    canonical write path, the realized P&L, exit_price, settlement_price,
    settled_at, and exit_reason MUST be readable from position_current AFTER the
    in-memory Position is discarded.

    Winning settlement pays $1/share. Fixture: entry 9 shares @ $0.40 = $3.60
    cost; settlement_price=1.0 → pnl = round(9*1.0 - 3.60, 2) = +5.40.
    """
    conn = _setup_world_db()
    pos = _make_filled_position(
        trade_id="bug128-win",
        city="KAR",
        entry_price=0.40,
        shares=9.0,
        cost_basis_usd=3.60,
    )
    closed = _project_settlement(conn, pos, won=True, settlement_price=1.0)
    expected_pnl = closed.pnl
    assert expected_pnl == pytest.approx(5.40), (
        f"sanity: in-memory pnl should be +5.40, got {expected_pnl}"
    )

    # Discard the in-memory object entirely.
    del pos
    del closed

    row = conn.execute(
        """
        SELECT phase, realized_pnl_usd, exit_price, settlement_price,
               settled_at, exit_reason
          FROM position_current
         WHERE position_id = ?
        """,
        ("bug128-win",),
    ).fetchone()
    assert row is not None, "settlement must project a position_current row"

    assert row["phase"] == "settled"
    assert row["realized_pnl_usd"] is not None, (
        "BUG #128: realized P&L is NOT durable — position_current.realized_pnl_usd is NULL"
    )
    assert row["realized_pnl_usd"] == pytest.approx(5.40), (
        f"durable realized_pnl_usd must match computed pnl 5.40, got {row['realized_pnl_usd']}"
    )
    assert row["exit_price"] == pytest.approx(1.0), (
        f"durable exit_price must be settlement price 1.0, got {row['exit_price']}"
    )
    assert row["settlement_price"] == pytest.approx(1.0), (
        f"durable settlement_price must be 1.0, got {row['settlement_price']}"
    )
    assert row["settled_at"] is not None and str(row["settled_at"]).strip() != "", (
        "durable settled_at must be populated on a settled row"
    )
    assert row["exit_reason"] == "SETTLEMENT", (
        f"durable exit_reason must be SETTLEMENT, got {row['exit_reason']}"
    )

    conn.close()


def test_settled_losing_position_persists_negative_pnl() -> None:
    """A losing settlement (settlement_price=0.0) must durably persist the
    realized loss equal to -cost_basis, not silently drop it."""
    conn = _setup_world_db()
    pos = _make_filled_position(
        trade_id="bug128-loss",
        city="LON",
        entry_price=0.40,
        shares=10.35,
        cost_basis_usd=4.14,
    )
    closed = _project_settlement(conn, pos, won=False, settlement_price=0.0)
    expected_pnl = closed.pnl
    # 10.35*0.0 - 4.14 = -4.14
    assert expected_pnl == pytest.approx(-4.14)

    del pos
    del closed

    row = conn.execute(
        "SELECT realized_pnl_usd, exit_price, settlement_price FROM position_current WHERE position_id = ?",
        ("bug128-loss",),
    ).fetchone()
    assert row is not None
    assert row["realized_pnl_usd"] == pytest.approx(-4.14), (
        f"losing realized P&L must persist as -4.14, got {row['realized_pnl_usd']}"
    )
    assert row["settlement_price"] == pytest.approx(0.0)

    conn.close()


def test_economic_close_persists_realized_pnl_durable() -> None:
    """The exit-close (economically_closed) path must ALSO persist realized P&L
    durably — it shares build_position_current_projection, so a single fix covers
    both settlement and exit close.

    Entry 8 shares @ $0.50 = $4.00 cost; exit @ $0.75 →
    pnl = round(8*0.75 - 4.00, 2) = +2.00.
    """
    from src.engine.lifecycle_events import build_economic_close_canonical_write
    from src.state.db import append_many_and_project
    from src.state.portfolio import PortfolioState, compute_economic_close

    conn = _setup_world_db()
    pos = _make_filled_position(
        trade_id="bug128-econ",
        city="CHI",
        entry_price=0.50,
        shares=8.0,
        cost_basis_usd=4.00,
    )
    # compute_economic_close requires pending_exit runtime phase.
    pos.exit_state = "exit_intent"
    portfolio = PortfolioState(positions=[pos])
    closed = compute_economic_close(portfolio, pos.trade_id, 0.75, "shoulder_sell")
    assert closed is not None
    assert closed.pnl == pytest.approx(2.00)

    events, projection = build_economic_close_canonical_write(
        closed, sequence_no=1, phase_before="pending_exit"
    )
    append_many_and_project(conn, events, projection)

    del pos
    del closed

    row = conn.execute(
        "SELECT phase, realized_pnl_usd, exit_price, exit_reason FROM position_current WHERE position_id = ?",
        ("bug128-econ",),
    ).fetchone()
    assert row is not None
    assert row["phase"] == "economically_closed"
    assert row["realized_pnl_usd"] == pytest.approx(2.00), (
        f"economic-close realized P&L must persist as +2.00, got {row['realized_pnl_usd']}"
    )
    assert row["exit_price"] == pytest.approx(0.75)
    assert row["exit_reason"] == "shoulder_sell"

    conn.close()
