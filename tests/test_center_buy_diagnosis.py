from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.state.db import get_connection, init_schema

from scripts.diagnose_center_buy_failure import diagnose_center_buy


def _create_outcome_fact_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outcome_fact (
            position_id TEXT PRIMARY KEY,
            strategy_key TEXT,
            entered_at TEXT,
            exited_at TEXT,
            settled_at TEXT,
            exit_reason TEXT,
            admin_exit_reason TEXT,
            decision_snapshot_id TEXT,
            pnl REAL,
            outcome INTEGER,
            hold_duration_hours REAL,
            monitor_count INTEGER,
            chain_corrections_count INTEGER
        )
        """
    )
    conn.commit()


def _insert_trade_decision(
    conn,
    *,
    trade_id: int,
    runtime_trade_id: str,
    strategy: str,
    status: str,
    entry_price: float,
    p_posterior: float,
    edge: float,
    timestamp: str,
):
    conn.execute(
        """
        INSERT INTO trade_decisions (
            trade_id, market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, strategy, edge_source, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_id,
            f"m-{runtime_trade_id}",
            "39-40°F",
            "buy_yes",
            1.0,
            entry_price,
            timestamp,
            p_posterior,
            p_posterior,
            edge,
            p_posterior - 0.01,
            p_posterior + 0.01,
            0.0,
            status,
            strategy,
            strategy,
            runtime_trade_id,
        ),
    )


def test_diagnose_center_buy_ignores_other_strategies_and_dedupes_latest_trade_decisions(tmp_path):
    db_path = tmp_path / "zeus.db"
    positions_path = tmp_path / "positions-paper.json"
    conn = get_connection(db_path)
    init_schema(conn)
    _create_outcome_fact_table(conn)

    conn.execute(
        """
        INSERT INTO outcome_fact (position_id, strategy_key, settled_at, exit_reason, pnl, outcome)
        VALUES
            ('cb-1', 'center_buy', '2026-04-02T00:00:00Z', 'settlement', -1.0, 0),
            ('cb-2', 'center_buy', '2026-04-03T00:00:00Z', 'settlement', 2.0, 1),
            ('oi-1', 'opening_inertia', '2026-04-04T00:00:00Z', 'settlement', 5.0, 1)
        """
    )
    _insert_trade_decision(
        conn,
        trade_id=1,
        runtime_trade_id="cb-1",
        strategy="center_buy",
        status="day0_window",
        entry_price=0.005,
        p_posterior=0.04,
        edge=0.03,
        timestamp="2026-04-01T00:00:00Z",
    )
    _insert_trade_decision(
        conn,
        trade_id=2,
        runtime_trade_id="cb-1",
        strategy="center_buy",
        status="exited",
        entry_price=0.005,
        p_posterior=0.04,
        edge=0.03,
        timestamp="2026-04-01T01:00:00Z",
    )
    _insert_trade_decision(
        conn,
        trade_id=3,
        runtime_trade_id="cb-2",
        strategy="center_buy",
        status="unresolved_ghost",
        entry_price=0.03,
        p_posterior=0.08,
        edge=0.05,
        timestamp="2026-04-02T01:00:00Z",
    )
    _insert_trade_decision(
        conn,
        trade_id=4,
        runtime_trade_id="oi-1",
        strategy="opening_inertia",
        status="exited",
        entry_price=0.2,
        p_posterior=0.6,
        edge=0.4,
        timestamp="2026-04-03T01:00:00Z",
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS position_events_legacy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            runtime_trade_id TEXT,
            position_state TEXT,
            order_id TEXT,
            decision_snapshot_id TEXT,
            city TEXT,
            target_date TEXT,
            market_id TEXT,
            bin_label TEXT,
            direction TEXT,
            strategy TEXT,
            edge_source TEXT,
            source TEXT,
            details_json TEXT,
            timestamp TEXT,
            env TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_events_legacy (
            event_type, runtime_trade_id, strategy, edge_source, details_json, timestamp, env
        ) VALUES
            ('ORDER_REJECTED', 'cb-r1', 'center_buy', 'center_buy', '{}', '2026-04-01T00:00:00Z', 'paper'),
            ('ORDER_REJECTED', 'oi-r1', 'opening_inertia', 'opening_inertia', '{}', '2026-04-01T00:00:00Z', 'paper')
        """
    )
    conn.commit()
    conn.close()

    positions_path.write_text(
        json.dumps(
            {
                "positions": [
                    {"trade_id": "x1", "strategy_key": "center_buy"},
                    {"trade_id": "x2", "strategy_key": "opening_inertia"},
                ],
                "recent_exits": [],
            }
        ),
        encoding="utf-8",
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    report = diagnose_center_buy(conn, positions_json_path=positions_path)
    conn.close()

    assert report["strategy_key"] == "center_buy"
    assert report["settled_summary"]["count"] == 2
    assert report["settled_summary"]["pnl_total"] == 1.0
    assert report["settled_summary"]["latest_status_counts"] == {
        "exited": 1,
        "unresolved_ghost": 1,
    }
    assert report["latest_trade_decision_summary"]["count"] == 2
    assert report["legacy_event_summary"]["ORDER_REJECTED"] == 1
    assert report["open_positions_json_count"] == 1


def test_diagnose_center_buy_flags_all_low_price_losses_and_missing_trade_decision(tmp_path):
    db_path = tmp_path / "zeus.db"
    positions_path = tmp_path / "positions-paper.json"
    conn = get_connection(db_path)
    init_schema(conn)
    _create_outcome_fact_table(conn)

    conn.execute(
        """
        INSERT INTO outcome_fact (position_id, strategy_key, settled_at, exit_reason, pnl, outcome)
        VALUES
            ('cb-1', 'center_buy', '2026-04-02T00:00:00Z', 'settlement', -1.0, 0),
            ('cb-2', 'center_buy', '2026-04-03T00:00:00Z', 'settlement', -1.2, 0)
        """
    )
    _insert_trade_decision(
        conn,
        trade_id=1,
        runtime_trade_id="cb-1",
        strategy="center_buy",
        status="day0_window",
        entry_price=0.006,
        p_posterior=0.04,
        edge=0.03,
        timestamp="2026-04-01T00:00:00Z",
    )
    conn.commit()
    conn.close()

    positions_path.write_text(json.dumps({"positions": [], "recent_exits": []}), encoding="utf-8")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    report = diagnose_center_buy(conn, positions_json_path=positions_path)
    conn.close()

    assert report["settled_summary"]["price_bucket_counts"]["<=0.01"] == 1
    assert report["settled_summary"]["latest_status_counts"]["missing_trade_decision"] == 1
    assert "all_settled_losses_are_ultra_low_price_tail_bets" in report["diagnosis_hypotheses"]
    assert "some_settlements_are_missing_latest_trade_decision_context" in report["diagnosis_hypotheses"]
