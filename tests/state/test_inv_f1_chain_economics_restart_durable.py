# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: PR #355 Copilot SEV-1 finding — F1 chain economics restart durability
"""
F1 critic SEV-1 antibody (PR #355 Copilot finding):
Chain economics MUST survive daemon restart.

Build a balance-only rescued position with fill_authority='venue_position_observed'
+ chain_avg_price + chain_cost_basis_usd + chain_shares, project to position_current,
close the connection, reopen the DB fresh, load via the loader path, assert:

1. position.fill_authority == 'venue_position_observed' (NOT FILL_AUTHORITY_NONE)
2. position.chain_avg_price preserved
3. position.chain_cost_basis_usd preserved
4. position.chain_shares preserved
5. position.has_chain_observed_authority == True
6. position.effective_exposure().source_authority == 'venue_position_observed'
7. position.effective_shares == chain_shares (not submitted_shares)
"""
from __future__ import annotations

import sqlite3
import tempfile
import os

import pytest


_DUMMY_TS = "2026-05-27T12:00:00+00:00"
_TRADE_ID = "restart-durable-test-pos"


def _setup_db_on_disk(path: str) -> sqlite3.Connection:
    """Open a fresh on-disk DB, run init_schema, return connection."""
    from src.state.db import init_schema

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _write_balance_only_position_current(conn: sqlite3.Connection) -> None:
    """Write a balance-only rescued position_current row with chain economics."""
    from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS, ordered_values

    payload = {
        "position_id": _TRADE_ID,
        "phase": "active",
        "trade_id": _TRADE_ID,
        "market_id": "mkt-restart",
        "city": "ATL",
        "cluster": "ATL",
        "target_date": "2026-06-01",
        "bin_label": "60-65",
        "direction": "buy_yes",
        "unit": "F",
        # Submitted / projected entry economics (NOT chain-aggregate):
        "size_usd": 10.0,
        "shares": 100.0,           # submitted shares — must NOT become effective_shares
        "cost_basis_usd": 10.0,
        "entry_price": 0.10,
        "p_posterior": 0.62,
        "last_monitor_prob": None,
        "last_monitor_edge": None,
        "last_monitor_market_price": None,
        "decision_snapshot_id": "snap-restart",
        "entry_method": "ens_member_counting",
        "strategy_key": "settlement_capture",
        "edge_source": "ensemble",
        "discovery_mode": "opening_hunt",
        "chain_state": "synced",
        "token_id": "t-restart-yes",
        "no_token_id": "t-restart-no",
        "condition_id": "c-restart",
        "order_id": "ord-restart",
        "order_status": "CONFIRMED",
        "updated_at": _DUMMY_TS,
        "temperature_metric": "high",
        # F1 authority:
        "fill_authority": "venue_position_observed",
        "recovery_authority": "chain_balance_only",
        # Chain-observed economics (authoritative):
        "chain_shares": 25.0,
        "chain_avg_price": 0.44,
        "chain_cost_basis_usd": 11.0,
        "chain_seen_at": _DUMMY_TS,
        "chain_absence_at": "",
    }
    conn.execute(
        f"""
        INSERT OR REPLACE INTO position_current ({", ".join(CANONICAL_POSITION_CURRENT_COLUMNS)})
        VALUES ({", ".join(["?"] * len(CANONICAL_POSITION_CURRENT_COLUMNS))})
        """,
        ordered_values(payload, CANONICAL_POSITION_CURRENT_COLUMNS),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Core restart-durability test
# ---------------------------------------------------------------------------


def test_chain_economics_survive_restart() -> None:
    """Balance-only rescued position must come back from loader with
    fill_authority='venue_position_observed' and chain economics intact.

    Pre-fix: _position_current_effective_entry_economics() unconditionally
    returned FILL_AUTHORITY_NONE on the no-fill-hint path, overwriting the
    row's fill_authority='venue_position_observed'. This caused:
      - position.has_chain_observed_authority == False
      - effective_shares fell back to submitted 100 shares, not chain 25
      - effective_exposure() used submitted economics (wrong)
    """
    from src.state.db import query_portfolio_loader_view
    from src.state.portfolio import (
        FILL_AUTHORITY_VENUE_POSITION_OBSERVED,
        _position_from_projection_row,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "world.db")

        # --- Write phase (simulates daemon run that rescued the position) ---
        conn_write = _setup_db_on_disk(db_path)
        _write_balance_only_position_current(conn_write)
        conn_write.close()

        # --- Read phase (simulates daemon restart loading the portfolio) ---
        conn_read = sqlite3.connect(db_path)
        conn_read.row_factory = sqlite3.Row

        snapshot = query_portfolio_loader_view(conn_read)
        conn_read.close()

    assert snapshot["status"] == "ok", f"loader returned non-ok: {snapshot['status']}"
    assert len(snapshot["positions"]) == 1, "expected 1 position from loader"

    row = snapshot["positions"][0]

    # Build Position exactly as the runtime daemon does on restart:
    pos = _position_from_projection_row(row, current_mode="live")

    # 1. fill_authority must survive — NOT downgraded to FILL_AUTHORITY_NONE
    assert pos.fill_authority == FILL_AUTHORITY_VENUE_POSITION_OBSERVED, (
        f"fill_authority={pos.fill_authority!r} — expected 'venue_position_observed'. "
        f"Pre-fix: loader overrides row fill_authority with FILL_AUTHORITY_NONE."
    )

    # 2. chain_avg_price preserved
    assert pos.chain_avg_price == pytest.approx(0.44), (
        f"chain_avg_price={pos.chain_avg_price!r} — expected 0.44"
    )

    # 3. chain_cost_basis_usd preserved
    assert pos.chain_cost_basis_usd == pytest.approx(11.0), (
        f"chain_cost_basis_usd={pos.chain_cost_basis_usd!r} — expected 11.0"
    )

    # 4. chain_shares preserved
    assert pos.chain_shares == pytest.approx(25.0), (
        f"chain_shares={pos.chain_shares!r} — expected 25.0"
    )

    # 5. has_chain_observed_authority must be True
    assert pos.has_chain_observed_authority is True, (
        f"has_chain_observed_authority={pos.has_chain_observed_authority!r} — "
        f"False means fill_authority was not preserved, so chain routing is void."
    )

    # 6. effective_exposure().source_authority must be 'venue_position_observed'
    exposure = pos.effective_exposure()
    assert exposure.source_authority == "venue_position_observed", (
        f"effective_exposure().source_authority={exposure.source_authority!r} — "
        f"expected 'venue_position_observed'. F1 exit/risk routing is broken post-restart."
    )

    # 7. effective_shares routes to chain_shares (25), NOT submitted shares (100)
    assert pos.effective_shares == pytest.approx(25.0), (
        f"effective_shares={pos.effective_shares!r} — expected chain 25.0, "
        f"got submitted 100.0. F1 partial-exit fix is silently void post-restart."
    )


# ---------------------------------------------------------------------------
# Targeted unit test: the helper itself must honour row fill_authority
# ---------------------------------------------------------------------------


def test_effective_entry_economics_honours_row_fill_authority() -> None:
    """Unit test for _position_current_effective_entry_economics:
    when the row carries fill_authority='venue_position_observed', the
    returned dict must preserve that value — not override with FILL_AUTHORITY_NONE.

    This is the smallest antibody for the exact no-fill-hint bug.
    """
    from src.state.db import (
        _position_current_effective_entry_economics,
        FILL_AUTHORITY_NONE,
    )

    # Simulate a row dict as returned by the loader SELECT.
    row = {
        "size_usd": 10.0,
        "shares": 100.0,
        "cost_basis_usd": 10.0,
        "entry_price": 0.10,
        "phase": "entered",
        "fill_authority": "venue_position_observed",
        "chain_avg_price": 0.44,
        "chain_cost_basis_usd": 11.0,
        "chain_shares": 25.0,
    }

    result = _position_current_effective_entry_economics(row, fill_hint=None)

    assert result["fill_authority"] == "venue_position_observed", (
        f"fill_authority={result['fill_authority']!r} — "
        f"helper must NOT downgrade row fill_authority to FILL_AUTHORITY_NONE. "
        f"Pre-fix: always returned FILL_AUTHORITY_NONE on the no-fill-hint path."
    )
    assert result["fill_authority"] != FILL_AUTHORITY_NONE, (
        "helper returned FILL_AUTHORITY_NONE — this is the pre-fix bug"
    )


def test_effective_entry_economics_uses_chain_cost_without_fill_hint() -> None:
    """A chain-synced row must not display zero exposure just because the
    linked execution_fact fill hint is absent.

    This guards the Helsinki-style state: chain/position_current know the real
    shares and cost, while the legacy audit bridge was synthesized with
    size_usd=0. The read model must use canonical chain economics.
    """
    from src.state.db import _position_current_effective_entry_economics

    row = {
        "size_usd": 0.0,
        "shares": 0.0,
        "cost_basis_usd": 0.0,
        "entry_price": 0.0,
        "phase": "active",
        "fill_authority": "venue_confirmed_full",
        "chain_avg_price": 0.65,
        "chain_cost_basis_usd": 2.3205,
        "chain_shares": 3.57,
    }

    result = _position_current_effective_entry_economics(row, fill_hint=None)

    assert result["effective_cost_basis_usd"] == pytest.approx(2.3205)
    assert result["pnl_cost_basis_usd"] == pytest.approx(2.3205)
    assert result["effective_shares"] == pytest.approx(3.57)
    assert result["effective_entry_price"] == pytest.approx(0.65)
    assert result["entry_economics_authority"] == "corrected_executable_cost_basis"
    assert result["entry_economics_source"] == "position_current_chain_observed"
    assert result["fill_authority"] == "venue_confirmed_full"
    assert result["entry_fill_verified"] is True


def test_effective_entry_economics_none_row_fill_authority_still_defaults() -> None:
    """Regression guard: legacy rows with NULL fill_authority still get
    FILL_AUTHORITY_NONE (no behavioural change for the legacy path)."""
    from src.state.db import (
        _position_current_effective_entry_economics,
        FILL_AUTHORITY_NONE,
    )

    row = {
        "size_usd": 10.0,
        "shares": 50.0,
        "cost_basis_usd": 10.0,
        "entry_price": 0.20,
        "phase": "entered",
        "fill_authority": None,   # legacy NULL
        "chain_avg_price": None,
        "chain_cost_basis_usd": None,
        "chain_shares": 0.0,
    }

    result = _position_current_effective_entry_economics(row, fill_hint=None)

    assert result["fill_authority"] == FILL_AUTHORITY_NONE, (
        f"legacy NULL fill_authority must still default to FILL_AUTHORITY_NONE, "
        f"got {result['fill_authority']!r}"
    )
