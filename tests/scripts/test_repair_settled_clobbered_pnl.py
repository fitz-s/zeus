# Created: 2026-07-12
# Last reused or audited: 2026-07-12
# Authority basis: live DB evidence — position_current rows whose booked
#   close economics were clobbered to 0.0 by settlement reprojection (Bug B,
#   see tests/state/test_settlement_preserves_booked_close_economics.py).
"""scripts/repair_settled_clobbered_pnl.py._plan_repair antibody.

Covers:
  1. A clobbered settled row (realized_pnl_usd=0.0) with an EXIT_ORDER_FILLED
     event (phase_after=economically_closed, numeric fill_price) -> planned
     repair with the recomputed value.
  2. A row without any such fill evidence -> excluded with a reason, never
     guessed.
  3. A chain-only-prefixed position_id -> excluded from scope entirely.
  4. A row whose recomputed pnl is within $0.005 of 0.0 -> reported as a
     no-op, not planned.
  5. Dry-run performs no writes.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from src.state.db import init_schema, init_schema_trade_only

from repair_settled_clobbered_pnl import _plan_repair  # noqa: E402


@pytest.fixture
def trades_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_schema_trade_only(conn)
    yield conn
    conn.close()


def _insert_position(conn: sqlite3.Connection, **overrides) -> None:
    base = dict(
        position_id="pos-1",
        phase="settled",
        city="manila",
        target_date="2026-07-04",
        bin_label="33°C",
        direction="buy_no",
        chain_shares=None,
        shares=20.0,
        cost_basis_usd=10.0,
        entry_price=0.5,
        exit_price=0.0,
        realized_pnl_usd=0.0,
        temperature_metric="high",
        strategy_key="test_strategy",
        updated_at="2026-07-08T12:00:00+00:00",
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


def _insert_exit_fill_event(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    sequence_no: int,
    fill_price,
    phase_after: str = "economically_closed",
) -> None:
    payload = json.dumps({"fill_price": fill_price, "exit_price": fill_price, "pnl": None})
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type, occurred_at,
            phase_before, phase_after, strategy_key, source_module, env,
            payload_json
        ) VALUES (
            ?, ?, ?, 'EXIT_ORDER_FILLED', '2026-07-08T00:00:00+00:00',
            'pending_exit', ?, 'test_strategy', 'test', 'test', ?
        )
        """,
        (f"{position_id}:exit:{sequence_no}", position_id, sequence_no, phase_after, payload),
    )
    conn.commit()


class TestRepairPlan:
    def test_clobbered_row_with_fill_evidence_plans_repair(self, trades_conn):
        _insert_position(
            trades_conn,
            position_id="pos-clobbered",
            shares=20.0,
            cost_basis_usd=10.0,
            entry_price=0.50,
        )
        _insert_exit_fill_event(
            trades_conn, position_id="pos-clobbered", sequence_no=1, fill_price=0.27
        )
        planned, excluded, noops = _plan_repair(trades_conn)
        assert excluded == []
        assert noops == []
        assert len(planned) == 1
        item = planned[0]
        assert item["position_id"] == "pos-clobbered"
        assert item["exit_price"] == pytest.approx(0.27)
        # 20*0.27 - 10.0 = -4.60
        assert item["realized_pnl_usd"] == pytest.approx(-4.60)
        assert item["source"] == "exit_order_filled_event"

    def test_row_without_fill_evidence_is_excluded(self, trades_conn):
        _insert_position(trades_conn, position_id="pos-no-evidence")
        planned, excluded, noops = _plan_repair(trades_conn)
        assert planned == []
        assert noops == []
        assert len(excluded) == 1
        assert excluded[0]["position_id"] == "pos-no-evidence"
        assert "EXIT_ORDER_FILLED" in excluded[0]["reason"]

    def test_fill_event_with_wrong_phase_after_is_not_evidence(self, trades_conn):
        """An EXIT_ORDER_FILLED event that never reached economically_closed
        (e.g. still pending_exit) is not proof of a booked close."""
        _insert_position(trades_conn, position_id="pos-wrong-phase")
        _insert_exit_fill_event(
            trades_conn,
            position_id="pos-wrong-phase",
            sequence_no=1,
            fill_price=0.30,
            phase_after="pending_exit",
        )
        planned, excluded, noops = _plan_repair(trades_conn)
        assert planned == []
        assert len(excluded) == 1

    def test_chain_only_prefixed_position_is_excluded_from_scope(self, trades_conn):
        _insert_position(trades_conn, position_id="chain-only-token-abc")
        _insert_exit_fill_event(
            trades_conn, position_id="chain-only-token-abc", sequence_no=1, fill_price=0.27
        )
        planned, excluded, noops = _plan_repair(trades_conn)
        assert planned == []
        assert excluded == []
        assert noops == []

    def test_near_zero_recomputed_value_is_noop_not_planned(self, trades_conn):
        """shares=10, cost_basis=2.7, fill_price=0.27 -> recomputed pnl = 0.0 exactly."""
        _insert_position(
            trades_conn,
            position_id="pos-near-zero",
            shares=10.0,
            cost_basis_usd=2.7,
            entry_price=0.27,
        )
        _insert_exit_fill_event(
            trades_conn, position_id="pos-near-zero", sequence_no=1, fill_price=0.27
        )
        planned, excluded, noops = _plan_repair(trades_conn)
        assert planned == []
        assert excluded == []
        assert len(noops) == 1
        assert noops[0]["position_id"] == "pos-near-zero"

    def test_already_nonzero_realized_pnl_is_out_of_scope(self, trades_conn):
        """The scan only targets realized_pnl_usd = 0.0 -- a row with a
        genuinely nonzero booked value (already correct) must not be
        touched."""
        _insert_position(
            trades_conn,
            position_id="pos-already-correct",
            realized_pnl_usd=-6.16,
            exit_price=0.27,
        )
        planned, excluded, noops = _plan_repair(trades_conn)
        assert planned == []
        assert excluded == []
        assert noops == []

    def test_dry_run_never_writes(self, trades_conn):
        _insert_position(trades_conn, position_id="pos-dry-run")
        _insert_exit_fill_event(
            trades_conn, position_id="pos-dry-run", sequence_no=1, fill_price=0.27
        )
        before = dict(
            trades_conn.execute(
                "SELECT realized_pnl_usd, exit_price FROM position_current WHERE position_id='pos-dry-run'"
            ).fetchone()
        )
        _plan_repair(trades_conn)
        after = dict(
            trades_conn.execute(
                "SELECT realized_pnl_usd, exit_price FROM position_current WHERE position_id='pos-dry-run'"
            ).fetchone()
        )
        assert before == after == {"realized_pnl_usd": 0.0, "exit_price": 0.0}
