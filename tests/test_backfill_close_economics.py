# Created: 2026-07-08
# Last reused or audited: 2026-07-08
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R0-a
"""R0-a backfill script antibody: scripts/backfill_close_economics.py._plan_backfill.

Covers the three row shapes the backfill must handle:
  1. exit_price already durable (Bug A shape) -> compute pnl directly.
  2. exit_price NULL on a settled row (Bug B / chain-mirror shape) -> grade
     against settlement_outcomes via grade_bin, derive exit_price 1.0/0.0.
  3. Unrecoverable rows (no settlement match, ungradeable bin, or an
     economically_closed row with NULL exit_price and no chain fallback) ->
     excluded with a reason, never guessed.

Also asserts the script never writes without --apply (dry-run is read-only:
_plan_backfill takes read-only connections and performs no INSERT/UPDATE).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from src.state.db import init_schema, init_schema_trade_only

from backfill_close_economics import _plan_backfill  # noqa: E402


@pytest.fixture
def trades_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_schema_trade_only(conn)
    yield conn
    conn.close()


@pytest.fixture
def forecasts_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE settlement_outcomes (
            city TEXT, target_date TEXT, temperature_metric TEXT,
            winning_bin TEXT, authority TEXT, settlement_value REAL,
            settlement_source TEXT, market_slug TEXT
        )
        """
    )
    yield conn
    conn.close()


def _insert_position(conn: sqlite3.Connection, **overrides) -> None:
    base = dict(
        position_id="pos-1",
        phase="settled",
        city="manila",
        target_date="2026-07-04",
        bin_label="33°C",
        direction="buy_yes",
        chain_shares=None,
        shares=10.0,
        cost_basis_usd=4.0,
        entry_price=0.4,
        exit_price=None,
        realized_pnl_usd=None,
        temperature_metric="high",
        strategy_key="test_strategy",
        updated_at="2026-07-04T00:00:00+00:00",
    )
    base.update(overrides)
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, city, target_date, bin_label,
            direction, chain_shares, shares, cost_basis_usd, entry_price,
            exit_price, realized_pnl_usd, temperature_metric, strategy_key, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            base["position_id"], base["phase"], base["position_id"], base["city"],
            base["target_date"], base["bin_label"], base["direction"],
            base["chain_shares"], base["shares"], base["cost_basis_usd"],
            base["entry_price"], base["exit_price"], base["realized_pnl_usd"],
            base["temperature_metric"], base["strategy_key"], base["updated_at"],
        ),
    )
    conn.commit()


def _insert_settlement(conn: sqlite3.Connection, **overrides) -> None:
    base = dict(
        city="manila", target_date="2026-07-04", temperature_metric="high",
        winning_bin="33°C", authority="VERIFIED", settlement_value=1.0,
        settlement_source="test", market_slug="manila-2026-07-04",
    )
    base.update(overrides)
    conn.execute(
        """
        INSERT INTO settlement_outcomes (
            city, target_date, temperature_metric, winning_bin, authority,
            settlement_value, settlement_source, market_slug
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(base[k] for k in (
            "city", "target_date", "temperature_metric", "winning_bin",
            "authority", "settlement_value", "settlement_source", "market_slug",
        )),
    )
    conn.commit()


class TestBackfillPlan:
    def test_bug_a_shape_exit_price_present_computes_pnl(self, trades_conn, forecasts_conn):
        _insert_position(
            trades_conn,
            position_id="pos-bug-a",
            phase="economically_closed",
            exit_price=0.9,
            shares=10.0,
            cost_basis_usd=5.0,
            entry_price=0.5,
        )
        planned, excluded = _plan_backfill(trades_conn, forecasts_conn)
        assert excluded == []
        assert len(planned) == 1
        item = planned[0]
        assert item["position_id"] == "pos-bug-a"
        assert item["source"] == "exit_price_column"
        assert item["exit_price"] == pytest.approx(0.9)
        assert item["realized_pnl_usd"] == pytest.approx(4.0)  # 10*0.9 - 5.0

    def test_bug_b_shape_settled_no_exit_price_grades_against_settlement(
        self, trades_conn, forecasts_conn
    ):
        _insert_position(
            trades_conn,
            position_id="pos-bug-b-win",
            phase="settled",
            exit_price=None,
            shares=10.0,
            cost_basis_usd=4.0,
            city="manila",
            target_date="2026-07-04",
            bin_label="33°C",
            direction="buy_yes",
        )
        _insert_settlement(forecasts_conn, winning_bin="33°C")
        planned, excluded = _plan_backfill(trades_conn, forecasts_conn)
        assert excluded == []
        assert len(planned) == 1
        item = planned[0]
        assert item["source"] == "settlement_outcomes_grade"
        assert item["exit_price"] == 1.0
        assert item["realized_pnl_usd"] == pytest.approx(6.0)  # 10*1 - 4

    def test_bug_b_shape_loser_grades_zero(self, trades_conn, forecasts_conn):
        _insert_position(
            trades_conn,
            position_id="pos-bug-b-lose",
            phase="settled",
            exit_price=None,
            shares=10.0,
            cost_basis_usd=3.2,
            direction="buy_no",
            bin_label="33°C",
        )
        _insert_settlement(forecasts_conn, winning_bin="33°C")
        planned, excluded = _plan_backfill(trades_conn, forecasts_conn)
        assert excluded == []
        item = planned[0]
        assert item["exit_price"] == 0.0
        assert item["realized_pnl_usd"] == pytest.approx(-3.2)

    def test_no_settlement_match_is_excluded_not_guessed(self, trades_conn, forecasts_conn):
        _insert_position(
            trades_conn,
            position_id="pos-no-match",
            phase="settled",
            exit_price=None,
            city="karachi",
            target_date="2026-07-04",
        )
        # forecasts_conn has no settlement_outcomes rows at all.
        planned, excluded = _plan_backfill(trades_conn, forecasts_conn)
        assert planned == []
        assert len(excluded) == 1
        assert excluded[0]["position_id"] == "pos-no-match"
        assert "settlement_outcomes" in excluded[0]["reason"]

    def test_ungradeable_bin_is_excluded_not_guessed(self, trades_conn, forecasts_conn):
        _insert_position(
            trades_conn,
            position_id="pos-ungradeable",
            phase="settled",
            exit_price=None,
            bin_label="not-a-comparable-bin",
        )
        _insert_settlement(forecasts_conn, winning_bin="33°C")
        planned, excluded = _plan_backfill(trades_conn, forecasts_conn)
        assert planned == []
        assert len(excluded) == 1
        assert "UNGRADEABLE" in excluded[0]["reason"]

    def test_economically_closed_no_exit_price_is_excluded(self, trades_conn, forecasts_conn):
        _insert_position(
            trades_conn,
            position_id="pos-ec-no-exit",
            phase="economically_closed",
            exit_price=None,
        )
        planned, excluded = _plan_backfill(trades_conn, forecasts_conn)
        assert planned == []
        assert len(excluded) == 1
        assert "NULL exit_price" in excluded[0]["reason"]

    def test_already_booked_rows_are_not_replanned(self, trades_conn, forecasts_conn):
        _insert_position(
            trades_conn,
            position_id="pos-already-booked",
            phase="settled",
            exit_price=1.0,
            realized_pnl_usd=5.5,
        )
        planned, excluded = _plan_backfill(trades_conn, forecasts_conn)
        assert planned == []
        assert excluded == []

    def test_dry_run_never_writes(self, trades_conn, forecasts_conn):
        _insert_position(
            trades_conn,
            position_id="pos-dry-run",
            phase="economically_closed",
            exit_price=0.9,
        )
        before = dict(
            trades_conn.execute(
                "SELECT realized_pnl_usd FROM position_current WHERE position_id='pos-dry-run'"
            ).fetchone()
        )
        _plan_backfill(trades_conn, forecasts_conn)
        after = dict(
            trades_conn.execute(
                "SELECT realized_pnl_usd FROM position_current WHERE position_id='pos-dry-run'"
            ).fetchone()
        )
        assert before == after == {"realized_pnl_usd": None}
