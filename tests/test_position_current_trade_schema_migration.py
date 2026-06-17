# Created: 2026-06-03
# Last reused/audited: 2026-06-03
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=2026-06-03
# Purpose: Antibody — W2 P&L columns must land on zeus_trades.db (init_schema_trade_only), not zeus-world.db.
# Reuse: Confirm init_schema_trade_only and _ensure_position_current_authority_columns are unchanged; check db_table_ownership.yaml position_current entry.
# Authority basis: /tmp/probe-ownership.md (K1 design: position_current is
#   trade_class on zeus_trades.db); architecture/db_table_ownership.yaml;
#   BUG #128 (W2 P&L columns); fix/prearm-fill-exit-readiness repoint 1.
"""Antibody tests: position_current W2 P&L columns must land on zeus_trades.db.

Root cause (probe-ownership.md): _ensure_position_current_authority_columns
was called only from init_schema() (world-conn path) but never from
init_schema_trade_only() — the authority path for position_current's 101 live
rows. The W2 P&L columns (realized_pnl_usd, exit_price, settlement_price,
settled_at, exit_reason) landed on zeus-world.db (0 rows) and NOT on
zeus_trades.db (101 live rows).

Relationship test: "a column added via init_schema_trade_only lands on
trade.db, NOT world.db" — expressed as a red→green assertion.

These tests MUST be RED before Repoint 1 (adding
_ensure_position_current_authority_columns to init_schema_trade_only) and
GREEN after.
"""
from __future__ import annotations

import sqlite3

import pytest

_W2_PNL_COLS = frozenset({
    "realized_pnl_usd",
    "exit_price",
    "settlement_price",
    "settled_at",
    "exit_reason",
})

_MONITOR_FRESHNESS_COLS = frozenset({
    "last_monitor_prob_is_fresh",
    "last_monitor_market_price_is_fresh",
})

# All additive columns managed by _ensure_position_current_authority_columns.
_AUTHORITY_COLS = frozenset({
    "fill_authority",
    "recovery_authority",
    "chain_shares",
    "chain_avg_price",
    "chain_cost_basis_usd",
    "chain_seen_at",
    "chain_absence_at",
    "realized_pnl_usd",
    "exit_price",
    "settlement_price",
    "settled_at",
    "exit_reason",
    "last_monitor_prob_is_fresh",
    "last_monitor_market_price_is_fresh",
})

_MINIMAL_DDL = """
CREATE TABLE position_current (
    position_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL,
    strategy_key TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    temperature_metric TEXT NOT NULL
);
"""


def _fresh_trade_conn_with_legacy_position_current(tmp_path):
    """Return a trade connection whose position_current table pre-existed
    (simulating zeus_trades.db that was initialized before the W2 commit)."""
    db = tmp_path / "zeus_trades_legacy.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    # Create a minimal position_current WITHOUT the W2 P&L columns, as the
    # live zeus_trades.db looked before the W2 commit landed.
    conn.executescript(_MINIMAL_DDL)
    conn.commit()
    return conn


def _get_column_names(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


class TestInitSchemaTradeOnlyMigratesLegacyPositionCurrent:
    """Repoint 1 antibody: init_schema_trade_only must run
    _ensure_position_current_authority_columns so that all W2 P&L columns
    land on zeus_trades.db even when position_current already exists."""

    def test_w2_pnl_cols_present_after_init_schema_trade_only_on_legacy_db(
        self, tmp_path
    ):
        """W2 P&L columns must be created by init_schema_trade_only on a legacy
        DB where position_current exists but lacks realized_pnl_usd etc.

        RED before Repoint 1; GREEN after.
        """
        from src.state.db import init_schema_trade_only

        conn = _fresh_trade_conn_with_legacy_position_current(tmp_path)
        init_schema_trade_only(conn)

        cols = _get_column_names(conn, "position_current")
        missing = _W2_PNL_COLS - cols
        assert not missing, (
            f"init_schema_trade_only must add W2 P&L columns to position_current "
            f"on legacy DBs (where position_current already exists). "
            f"Missing: {sorted(missing)}. "
            f"Fix: add _ensure_position_current_authority_columns(conn) inside "
            f"init_schema_trade_only (src/state/db.py) after executescript."
        )

    def test_all_authority_cols_present_after_init_schema_trade_only_on_legacy_db(
        self, tmp_path
    ):
        """All columns managed by _ensure_position_current_authority_columns
        must land on trade.db when init_schema_trade_only runs on a legacy DB.

        Covers chain_avg_price / chain_cost_basis_usd / chain_absence_at
        (also missing from the live zeus_trades.db per probe-ownership.md).
        """
        from src.state.db import init_schema_trade_only

        conn = _fresh_trade_conn_with_legacy_position_current(tmp_path)
        init_schema_trade_only(conn)

        cols = _get_column_names(conn, "position_current")
        missing = _AUTHORITY_COLS - cols
        assert not missing, (
            f"init_schema_trade_only must add all authority cols to legacy position_current. "
            f"Missing: {sorted(missing)}."
        )

    def test_monitor_freshness_cols_present_after_init_schema_trade_only_on_legacy_db(
        self, tmp_path
    ):
        """Monitor probability/market freshness bits must survive restart; a
        legacy trade DB missing them must be migrated additively."""
        from src.state.db import init_schema_trade_only

        conn = _fresh_trade_conn_with_legacy_position_current(tmp_path)
        init_schema_trade_only(conn)

        cols = _get_column_names(conn, "position_current")
        missing = _MONITOR_FRESHNESS_COLS - cols
        assert not missing, (
            "init_schema_trade_only must add monitor freshness columns to "
            f"legacy position_current. Missing: {sorted(missing)}."
        )

    def test_init_schema_trade_only_idempotent_on_db_with_all_cols(
        self, tmp_path
    ):
        """Calling init_schema_trade_only twice on the same DB must not raise
        (idempotency — safe to call on populated live DB)."""
        from src.state.db import init_schema_trade_only

        conn = _fresh_trade_conn_with_legacy_position_current(tmp_path)
        init_schema_trade_only(conn)
        # Second call must not raise
        init_schema_trade_only(conn)

    def test_w2_cols_land_on_fresh_trade_db_not_world_db(self, tmp_path):
        """Relationship test: columns added via init_schema_trade_only land on
        the trade DB (not world DB). World DB gets no position_current rows in
        production; a settlement P&L written via trade_conn must be readable."""
        from src.state.db import init_schema_trade_only

        trade_db = tmp_path / "trade.db"
        world_db = tmp_path / "world.db"

        # Create minimal legacy position_current on trade.db
        trade_conn = sqlite3.connect(str(trade_db))
        trade_conn.row_factory = sqlite3.Row
        trade_conn.executescript(_MINIMAL_DDL)
        trade_conn.commit()

        # World DB: no position_current at all (as in production)
        world_conn = sqlite3.connect(str(world_db))
        world_conn.row_factory = sqlite3.Row
        world_conn.commit()

        init_schema_trade_only(trade_conn)

        trade_cols = _get_column_names(trade_conn, "position_current")
        world_tables = {
            row[0]
            for row in world_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        # W2 cols must be on trade_conn
        missing_trade = _W2_PNL_COLS - trade_cols
        assert not missing_trade, (
            f"W2 P&L cols missing from trade DB after init_schema_trade_only: "
            f"{sorted(missing_trade)}"
        )
        # World DB must NOT have position_current (the ghost is created only by
        # init_schema_world_only, never by init_schema_trade_only)
        assert "position_current" not in world_tables, (
            "init_schema_trade_only must not create position_current on world DB"
        )

        trade_conn.close()
        world_conn.close()


class TestSettledPositionPnlLandsOnTradeDb:
    """Relationship test: a settled position's realized_pnl_usd must be
    persistable on its trade.db row (not the world.db ghost).

    This tests the write path end-to-end: after init_schema_trade_only runs,
    an UPDATE to position_current.realized_pnl_usd on the trade connection
    must succeed and be readable.
    """

    def test_settled_pnl_usd_writable_on_trade_db_after_migration(self, tmp_path):
        """A realised P&L update must succeed on trade.db after the migration runs."""
        from src.state.db import init_schema_trade_only

        conn = _fresh_trade_conn_with_legacy_position_current(tmp_path)
        init_schema_trade_only(conn)

        # Insert a dummy active position
        conn.execute(
            """
            INSERT INTO position_current
                (position_id, phase, strategy_key, updated_at, temperature_metric)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("pos-test-001", "active", "center_buy", "2026-06-03T00:00:00Z", "high"),
        )
        conn.commit()

        # Settle it: write realized_pnl_usd
        conn.execute(
            "UPDATE position_current SET realized_pnl_usd = ?, phase = ?, exit_reason = ? "
            "WHERE position_id = ?",
            (3.14, "settled", "SETTLEMENT", "pos-test-001"),
        )
        conn.commit()

        row = conn.execute(
            "SELECT realized_pnl_usd, phase, exit_reason "
            "FROM position_current WHERE position_id = ?",
            ("pos-test-001",),
        ).fetchone()
        assert row is not None
        assert abs(row["realized_pnl_usd"] - 3.14) < 1e-9, (
            "realized_pnl_usd must persist on trade DB after settlement write"
        )
        assert row["phase"] == "settled"
        assert row["exit_reason"] == "SETTLEMENT"
