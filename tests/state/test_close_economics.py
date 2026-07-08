# Created: 2026-07-08
# Last reused or audited: 2026-07-08
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R0-a
#                  docs/rebuild/whole_system_first_principles_2026-07-07.md §2.6
"""R0-a antibody: close-economics unification.

Two things under test:

1. src.state.close_economics.compute_realized_pnl_usd is the single formula
   every terminal position-close path uses (unit-level coverage of the
   formula + entry_price guard behavior).
2. src.state.projection.upsert_position_current fails loudly
   (MissingRealizedPnlOnCloseError) when a position transitions into an
   economically_closed/settled phase for the FIRST time without a
   realized_pnl_usd value -- the structural backstop that makes "a close path
   forgets the pnl key" (Bug A/B, 2026-07-07) impossible to ship silently
   again, even for a close path that does not yet exist. A hypothetical sixth
   close path that builds a bare projection dict and skips
   close_economics.compute_realized_pnl_usd is exercised directly here to
   prove the backstop, independent of any of the five known close paths.

Existing regression coverage for the five known close paths themselves lives
in:
  - tests/state/test_bug128_realized_pnl_durable.py (paths #1/#2: in-memory
    Position economic-close / settlement-close via portfolio.py)
  - tests/execution/test_exit_before_settlement_realized_pnl.py (paths #3/#4:
    command_recovery / exchange_reconcile SimpleNamespace rebuilds)
  - tests/test_reconcile_chain_mirror.py (path #5: chain_mirror_reconciler)
"""
from __future__ import annotations

import sqlite3

import pytest

from src.state.close_economics import compute_realized_pnl_usd


class TestComputeRealizedPnlUsd:
    def test_basic_formula(self):
        # shares * exit_price - cost_basis_usd
        assert compute_realized_pnl_usd(
            shares=10.0, exit_price=0.55, cost_basis_usd=3.0, entry_price=0.30
        ) == pytest.approx(2.5)

    def test_negative_pnl(self):
        assert compute_realized_pnl_usd(
            shares=5.0, exit_price=0.20, cost_basis_usd=3.0, entry_price=0.60
        ) == pytest.approx(-2.0)

    def test_binary_settlement_won(self):
        # settlement is the special case exit_price=1.0
        assert compute_realized_pnl_usd(
            shares=10.0, exit_price=1.0, cost_basis_usd=4.0
        ) == pytest.approx(6.0)

    def test_binary_settlement_lost(self):
        # settlement is the special case exit_price=0.0
        assert compute_realized_pnl_usd(
            shares=10.0, exit_price=0.0, cost_basis_usd=3.2
        ) == pytest.approx(-3.2)

    def test_entry_price_guard_zero_returns_zero(self):
        assert compute_realized_pnl_usd(
            shares=10.0, exit_price=0.9, cost_basis_usd=5.0, entry_price=0.0
        ) == 0.0

    def test_entry_price_guard_negative_returns_zero(self):
        assert compute_realized_pnl_usd(
            shares=10.0, exit_price=0.9, cost_basis_usd=5.0, entry_price=-1.0
        ) == 0.0

    def test_no_entry_price_guard_when_omitted(self):
        # chain_mirror_reconciler's pre-existing behavior: no entry_price
        # check at all when the caller does not supply one.
        assert compute_realized_pnl_usd(
            shares=10.0, exit_price=1.0, cost_basis_usd=4.0
        ) == pytest.approx(6.0)

    def test_rounds_to_cents(self):
        result = compute_realized_pnl_usd(
            shares=3.0, exit_price=0.333333, cost_basis_usd=0.5, entry_price=0.1
        )
        assert result == round(3.0 * 0.333333 - 0.5, 2)


def _world_conn() -> sqlite3.Connection:
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _base_open_projection(position_id: str, *, phase: str = "active") -> dict:
    from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS

    projection = {col: None for col in CANONICAL_POSITION_CURRENT_COLUMNS}
    projection.update(
        {
            "position_id": position_id,
            "phase": phase,
            "trade_id": position_id,
            "market_id": f"mkt-{position_id}",
            "city": "Karachi",
            "cluster": "Karachi",
            "target_date": "2026-07-01",
            "bin_label": "30-35",
            "direction": "buy_yes",
            "unit": "F",
            "size_usd": 5.0,
            "shares": 10.0,
            "cost_basis_usd": 5.0,
            "entry_price": 0.5,
            "strategy_key": "test_strategy",
            "chain_state": "synced",
            "condition_id": "cond-1",
            "order_id": "ord-1",
            "order_status": "filled",
            "updated_at": "2026-07-01T00:00:00+00:00",
            "temperature_metric": "high",
        }
    )
    return projection


class TestMissingRealizedPnlOnCloseGuard:
    """Structural backstop: upsert_position_current must refuse a first-time
    close-phase write with no realized_pnl_usd, regardless of which close
    path (known or hypothetical/future) produced the projection."""

    def test_hypothetical_bare_close_path_without_pnl_raises_loudly(self):
        """A sixth close path that never heard of close_economics -- builds a
        bare projection dict, sets phase=economically_closed, forgets
        realized_pnl_usd -- must fail loudly at the single write funnel
        instead of silently persisting NULL (the exact shape of Bug A/B)."""
        from src.state.projection import (
            MissingRealizedPnlOnCloseError,
            upsert_position_current,
        )

        conn = _world_conn()
        # Position must exist first in a non-absorbing phase (this is the
        # FIRST transition into a close phase).
        upsert_position_current(conn, _base_open_projection("pos-bare-close"))

        bad_projection = _base_open_projection("pos-bare-close", phase="economically_closed")
        bad_projection["last_exit_at"] = "2026-07-01T00:05:00+00:00"
        bad_projection["exit_price"] = 0.9
        # realized_pnl_usd deliberately left None -- the bug shape.
        assert bad_projection["realized_pnl_usd"] is None

        with pytest.raises(MissingRealizedPnlOnCloseError) as exc_info:
            upsert_position_current(conn, bad_projection)
        assert exc_info.value.position_id == "pos-bare-close"
        assert exc_info.value.phase == "economically_closed"
        conn.close()

    def test_hypothetical_close_path_with_pnl_succeeds(self):
        """The same hypothetical path, but routed through
        close_economics.compute_realized_pnl_usd first, must write through
        cleanly -- proving the guard checks presence, not provenance
        gymnastics."""
        from src.state.projection import upsert_position_current

        conn = _world_conn()
        upsert_position_current(conn, _base_open_projection("pos-good-close"))

        good_projection = _base_open_projection("pos-good-close", phase="settled")
        good_projection["last_exit_at"] = "2026-07-01T00:05:00+00:00"
        good_projection["exit_price"] = 0.9
        good_projection["settled_at"] = "2026-07-01T00:05:00+00:00"
        good_projection["realized_pnl_usd"] = compute_realized_pnl_usd(
            shares=10.0, exit_price=0.9, cost_basis_usd=5.0, entry_price=0.5
        )

        upsert_position_current(conn, good_projection)
        row = conn.execute(
            "SELECT phase, realized_pnl_usd FROM position_current WHERE position_id = ?",
            ("pos-good-close",),
        ).fetchone()
        assert row["phase"] == "settled"
        assert row["realized_pnl_usd"] == pytest.approx(4.0)
        conn.close()

    def test_re_write_of_already_absorbing_row_is_not_re_checked(self):
        """A size-correction-style re-write of an ALREADY-settled row (e.g.
        legacy data predating the Bug A/B fix, still NULL) must not start
        raising on an unrelated re-write -- only the first transition into
        the close phase is guarded."""
        from src.state.projection import upsert_position_current

        conn = _world_conn()
        # Seed a position directly as an already-settled legacy row with
        # NULL realized_pnl_usd (bypassing the guard via raw SQL, simulating
        # a pre-fix historical row).
        seed = _base_open_projection("pos-legacy-settled", phase="settled")
        seed["realized_pnl_usd"] = 1.23  # first write must satisfy the guard
        upsert_position_current(conn, seed)
        # Now simulate the legacy NULL state directly (as if it predated the
        # fix) via raw UPDATE, bypassing the write funnel -- this is the
        # historical condition the backfill script targets, not something a
        # current close path can produce.
        conn.execute(
            "UPDATE position_current SET realized_pnl_usd = NULL WHERE position_id = ?",
            ("pos-legacy-settled",),
        )

        # A re-write that keeps phase=settled (e.g. a size correction) must
        # not raise even though realized_pnl_usd is still NULL, because the
        # existing phase is already absorbing.
        rewrite = _base_open_projection("pos-legacy-settled", phase="settled")
        rewrite["realized_pnl_usd"] = None
        rewrite["chain_shares"] = 9.5
        upsert_position_current(conn, rewrite)

        row = conn.execute(
            "SELECT phase, realized_pnl_usd FROM position_current WHERE position_id = ?",
            ("pos-legacy-settled",),
        ).fetchone()
        assert row["phase"] == "settled"
        assert row["realized_pnl_usd"] is None
        conn.close()
