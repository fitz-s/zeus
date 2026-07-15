# Created: 2026-07-12
# Last audited: 2026-07-12
# Authority basis: live DB evidence — ~27 position_current rows in the last 7d
#   had a correctly booked realized_pnl_usd/exit_price at economic close
#   (EXIT_ORDER_FILLED, phase=economically_closed) silently clobbered to 0.0
#   when the harvester later settled the underlying market.
"""Bug B antibody: a position that exits BEFORE settlement must keep its
booked realized P&L / exit_price through the settlement reload+reproject.

BUG #128 made the write path durable (position_current.realized_pnl_usd /
exit_price persist through build_position_current_projection). R0-a
(close-economics unification, 2026-07-08) made the exit-before-settlement
FILL writers (command_recovery / exchange_reconcile) book the correct
realized_pnl_usd/exit_price at economic close (see
tests/execution/test_exit_before_settlement_realized_pnl.py).

But the READ side never closed the loop: `_position_from_projection_row`
(src/state/portfolio.py) builds the runtime `Position` from an explicit
field-by-field payload dict, and that dict never maps the row's
`realized_pnl_usd` column onto `Position.pnl` (names differ, so the generic
"copy any remaining dataclass field with the same name" loop at the bottom of
the function can't match it either) — nor does it map `exit_price`. Depending
on daemon restart timing, ANY economically_closed row gets reloaded with
Position.pnl=0.0 / Position.exit_price=0.0 (dataclass defaults) regardless of
what was durably booked.

`compute_settlement_close` correctly refuses to recompute pnl/exit_price for
a `was_economically_closed` position (it trusts the already-closed values) —
but it does still stamp `pos.last_exit_at = now`. `_has_realized_close`
(src/engine/lifecycle_events.py) uses non-empty `last_exit_at` as the "this
position has real close economics" proxy, so once `last_exit_at` is stamped,
`_settled_economics_value` happily projects the DEFAULTED pnl=0.0/
exit_price=0.0 as if it were real economics, and the SETTLED write clobbers
the correctly-booked values with zero.

Fix: hydrate `pnl` <- row `realized_pnl_usd` and `exit_price` <- row
`exit_price` in `_position_from_projection_row` (only when the row's value is
not NULL, so BUG #128's NULL-vs-0.0 distinction for OPEN positions is
preserved), and extend `query_portfolio_loader_view`
(src/state/db.py) to actually select those columns (it did not select them at
all before this fix).
"""
from __future__ import annotations

import sqlite3

import pytest


def _world_conn() -> sqlite3.Connection:
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _insert_position_current_row(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    phase: str,
    direction: str,
    shares: float,
    cost_basis_usd: float,
    entry_price: float,
    realized_pnl_usd: float | None,
    exit_price: float | None,
) -> None:
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster,
            target_date, bin_label, direction, unit, shares,
            cost_basis_usd, entry_price, strategy_key, chain_state,
            token_id, no_token_id, condition_id, order_id, order_status,
            updated_at, temperature_metric, realized_pnl_usd, exit_price,
            exit_reason, fill_authority
        ) VALUES (
            ?, ?, ?, ?, 'Milan', 'Milan',
            '2026-07-08', '30-35', ?, 'F', ?,
            ?, ?, 'test_strategy', 'synced',
            ?, ?, 'cond-1', 'ord-entry', 'filled',
            '2026-07-08T12:00:00+00:00', 'high', ?, ?,
            ?, 'venue_confirmed_full'
        )
        """,
        (
            position_id, phase, position_id, f"mkt-{position_id}",
            direction, shares,
            cost_basis_usd, entry_price,
            f"tok-{position_id}", f"tok-{position_id}-no",
            realized_pnl_usd, exit_price,
            "EOD_MOVE" if phase == "economically_closed" else None,
        ),
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type, occurred_at,
            phase_before, phase_after, strategy_key, source_module, env,
            payload_json
        ) VALUES (
            ?, ?, 1, 'ENTRY_ORDER_FILLED', '2026-07-08T00:00:00+00:00',
            'pending_entry', 'active', 'test_strategy', 'test', 'test',
            '{}'
        )
        """,
        (f"{position_id}:entry:1", position_id),
    )
    conn.commit()


def _load_single_position(conn: sqlite3.Connection, position_id: str):
    from src.state.db import query_portfolio_loader_view
    from src.state.portfolio import _position_from_projection_row

    snapshot = query_portfolio_loader_view(conn)
    rows = [r for r in snapshot["positions"] if r["position_id"] == position_id]
    assert len(rows) == 1, f"expected exactly one loader row for {position_id}, got {len(rows)}"
    return _position_from_projection_row(rows[0], current_mode="live")


class TestBookedCloseEconomicsSurviveSettlementReload:
    def test_economically_closed_pnl_and_exit_price_hydrate_from_row(self):
        """Loader-level check: the reload must NOT default a booked close's
        pnl/exit_price to 0.0."""
        conn = _world_conn()
        _insert_position_current_row(
            conn,
            position_id="pos-eco-hydrate",
            phase="economically_closed",
            direction="buy_no",
            shares=20.0,
            cost_basis_usd=10.0,
            entry_price=0.50,
            realized_pnl_usd=-6.16,
            exit_price=0.27,
        )
        pos = _load_single_position(conn, "pos-eco-hydrate")
        assert pos.pnl == pytest.approx(-6.16), (
            f"BUG B: economically_closed reload must hydrate booked pnl -6.16, got {pos.pnl}"
        )
        assert pos.exit_price == pytest.approx(0.27), (
            f"BUG B: economically_closed reload must hydrate booked exit_price 0.27, got {pos.exit_price}"
        )
        conn.close()

    def test_settlement_after_reload_preserves_booked_realized_pnl(self):
        """End-to-end antibody: reload an economically_closed position through
        the real loader, run it through the settlement close path, and assert
        the re-projected position_current row + SETTLED event payload still
        carry the ORIGINALLY BOOKED pnl/exit_price -- not 0.0."""
        from src.engine.lifecycle_events import build_settlement_canonical_write
        from src.state.db import append_many_and_project
        from src.state.portfolio import PortfolioState, compute_settlement_close

        conn = _world_conn()
        _insert_position_current_row(
            conn,
            position_id="pos-eco-settle",
            phase="economically_closed",
            direction="buy_no",
            shares=20.0,
            cost_basis_usd=10.0,
            entry_price=0.50,
            realized_pnl_usd=-6.16,
            exit_price=0.27,
        )
        pos = _load_single_position(conn, "pos-eco-settle")
        assert pos.state == "economically_closed"

        portfolio = PortfolioState(positions=[pos])
        # Settlement price is whatever the market ultimately resolved to --
        # irrelevant to this position's already-booked economics, since it
        # exited before settlement. Use a value that would produce a
        # DIFFERENT (wrong) pnl if the bug recomputed from it.
        closed = compute_settlement_close(portfolio, "pos-eco-settle", 1.0, "SETTLEMENT")
        assert closed is not None
        assert closed.pnl == pytest.approx(-6.16), (
            f"in-memory settled Position must keep booked pnl -6.16, got {closed.pnl}"
        )
        assert closed.exit_price == pytest.approx(0.27), (
            f"in-memory settled Position must keep booked exit_price 0.27, got {closed.exit_price}"
        )

        events, projection = build_settlement_canonical_write(
            closed,
            winning_bin=closed.bin_label,
            won=False,
            # BUY NO would win this binary settlement even though this
            # position had already exited at a booked loss.
            outcome=1,
            sequence_no=2,
            phase_before="economically_closed",
            settlement_value=1.0,
        )
        append_many_and_project(conn, events, projection)

        row = conn.execute(
            "SELECT phase, realized_pnl_usd, exit_price FROM position_current WHERE position_id = ?",
            ("pos-eco-settle",),
        ).fetchone()
        assert row is not None
        assert row["phase"] == "settled"
        assert row["realized_pnl_usd"] == pytest.approx(-6.16), (
            "BUG B: settlement reprojection clobbered booked realized_pnl_usd "
            f"-6.16 -> {row['realized_pnl_usd']}"
        )
        assert row["exit_price"] == pytest.approx(0.27), (
            "BUG B: settlement reprojection clobbered booked exit_price "
            f"0.27 -> {row['exit_price']}"
        )

        settled_events = [e for e in events if e["event_type"] == "SETTLED"]
        assert len(settled_events) == 1
        import json

        settled_payload = json.loads(settled_events[0]["payload_json"])
        assert settled_payload["pnl"] == pytest.approx(-6.16), (
            "BUG B: SETTLED event payload must carry booked pnl -6.16, got "
            f"{settled_payload['pnl']}"
        )
        assert settled_payload["exit_price"] == pytest.approx(0.27)

        conn.close()

    def test_direct_settlement_from_active_still_computes_from_settlement_price(self):
        """Regression: a position settled directly from active/day0_window
        (never economically closed first) must still compute its pnl from the
        settlement price exactly as before this fix."""
        from src.engine.lifecycle_events import build_settlement_canonical_write
        from src.state.db import append_many_and_project
        from src.state.portfolio import PortfolioState, compute_settlement_close

        conn = _world_conn()
        _insert_position_current_row(
            conn,
            position_id="pos-active-settle",
            phase="active",
            direction="buy_yes",
            shares=9.0,
            cost_basis_usd=3.60,
            entry_price=0.40,
            realized_pnl_usd=None,
            exit_price=None,
        )
        pos = _load_single_position(conn, "pos-active-settle")
        assert pos.state == "entered"
        assert pos.pnl == 0.0
        assert pos.exit_price == 0.0

        portfolio = PortfolioState(positions=[pos])
        closed = compute_settlement_close(portfolio, "pos-active-settle", 1.0, "SETTLEMENT")
        assert closed is not None
        # settlement_price=1.0 (winning), 9 shares @ 0.40 cost=3.60 -> pnl=5.40
        assert closed.pnl == pytest.approx(5.40)
        assert closed.exit_price == pytest.approx(1.0)

        events, projection = build_settlement_canonical_write(
            closed,
            winning_bin=closed.bin_label,
            won=True,
            outcome=1,
            sequence_no=2,
            phase_before="active",
            settlement_value=1.0,
        )
        append_many_and_project(conn, events, projection)

        row = conn.execute(
            "SELECT realized_pnl_usd, exit_price FROM position_current WHERE position_id = ?",
            ("pos-active-settle",),
        ).fetchone()
        assert row["realized_pnl_usd"] == pytest.approx(5.40)
        assert row["exit_price"] == pytest.approx(1.0)
        conn.close()

    def test_open_position_still_projects_null_economics(self):
        """BUG #128 regression: an OPEN (active) position must still reload
        with pnl/exit_price effectively unset and project NULL realized
        economics -- hydration must be gated on the row's value being
        non-NULL, not unconditional."""
        conn = _world_conn()
        _insert_position_current_row(
            conn,
            position_id="pos-open",
            phase="active",
            direction="buy_yes",
            shares=5.0,
            cost_basis_usd=2.0,
            entry_price=0.40,
            realized_pnl_usd=None,
            exit_price=None,
        )
        pos = _load_single_position(conn, "pos-open")
        assert pos.pnl == 0.0
        assert pos.exit_price == 0.0
        assert str(getattr(pos, "last_exit_at", "")) == "", (
            "an OPEN position must not carry a close timestamp after reload"
        )

        from src.engine.lifecycle_events import build_position_current_projection

        projection = build_position_current_projection(pos)
        assert projection["realized_pnl_usd"] is None, (
            "BUG #128 regression: OPEN position must project NULL realized_pnl_usd, "
            f"got {projection['realized_pnl_usd']}"
        )
        assert projection["exit_price"] is None
        conn.close()
