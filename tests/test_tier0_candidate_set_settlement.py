# Created: 2026-08-27
# Last reused/audited: 2026-08-27
# Authority basis: reversal_plan_tier0_2026-08-24 items 3 and 7;
#   tier0_selection_lift_preregistration_2026-08-24 frozen data contract.
"""Tier-0 candidate sets receive exact canonical settlement labels."""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from src.execution.post_trade_capital import (
    _apply_tier0_candidate_settlement_labels,
    _tier0_candidate_settlement_labels,
)

_ROOT = Path(__file__).resolve().parent.parent


def _forecast_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE market_events (
            condition_id TEXT, city TEXT, target_date TEXT,
            temperature_metric TEXT, range_low REAL, range_high REAL
        );
        CREATE TABLE settlement_outcomes (
            city TEXT, target_date TEXT, temperature_metric TEXT,
            settlement_value REAL, settlement_unit TEXT, authority TEXT
        );
        """
    )
    return conn


def _trade_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE tier0_candidate_set_provenance (
            row_id INTEGER PRIMARY KEY, settled_y INTEGER
        )
        """
    )
    return conn


def test_labels_use_verified_point_range_and_shoulder_bounds_for_both_sides():
    conn = _forecast_conn()
    conn.executemany(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        (
            ("point", "Taipei", "2026-08-26", "high", 30.0, 30.0),
            ("below", "Taipei", "2026-08-26", "high", None, 29.0),
            ("above", "Taipei", "2026-08-26", "high", 31.0, None),
        ),
    )
    conn.execute(
        "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?)",
        ("Taipei", "2026-08-26", "high", 30.0, "C", "VERIFIED"),
    )
    candidates = [
        {
            "row_id": row_id,
            "market_key": market,
            "city": "Taipei",
            "target_date": "2026-08-26",
            "side": side,
        }
        for row_id, market, side in (
            (1, "point", "YES"),
            (2, "point", "NO"),
            (3, "below", "YES"),
            (4, "below", "NO"),
            (5, "above", "YES"),
            (6, "above", "NO"),
        )
    ]

    labels, stats = _tier0_candidate_settlement_labels(conn, candidates)

    assert labels == [(1, 1), (2, 0), (3, 0), (4, 1), (5, 0), (6, 1)]
    assert stats == {
        "candidate_rows": 6,
        "verified_market_labels": 3,
        "labels_ready": 6,
        "pending_rows": 0,
        "ambiguous_markets": 0,
        "invalid_truth_rows": 0,
        "invalid_candidate_rows": 0,
    }

    fahrenheit = _forecast_conn()
    fahrenheit.execute(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        ("range", "Austin", "2026-08-26", "high", 64.0, 65.0),
    )
    fahrenheit.execute(
        "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?)",
        ("Austin", "2026-08-26", "high", 65.0, "F", "VERIFIED"),
    )
    range_candidates = [
        {
            "row_id": row_id,
            "market_key": "range",
            "city": "Austin",
            "target_date": "2026-08-26",
            "side": side,
        }
        for row_id, side in ((7, "YES"), (8, "NO"))
    ]

    labels, stats = _tier0_candidate_settlement_labels(
        fahrenheit,
        range_candidates,
    )

    assert labels == [(7, 1), (8, 0)]
    assert stats["verified_market_labels"] == 1
    assert stats["invalid_truth_rows"] == 0


def test_unverified_or_unit_inconsistent_truth_never_labels_a_candidate():
    conn = _forecast_conn()
    conn.executemany(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        (
            ("unverified", "Taipei", "2026-08-26", "high", 30.0, 30.0),
            ("wrong-unit", "Taipei", "2026-08-26", "low", 25.0, 25.0),
        ),
    )
    conn.executemany(
        "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?)",
        (
            ("Taipei", "2026-08-26", "high", 30.0, "C", "UNVERIFIED"),
            ("Taipei", "2026-08-26", "low", 25.0, "F", "VERIFIED"),
        ),
    )
    candidates = [
        {
            "row_id": 1,
            "market_key": "unverified",
            "city": "Taipei",
            "target_date": "2026-08-26",
            "side": "YES",
        },
        {
            "row_id": 2,
            "market_key": "wrong-unit",
            "city": "Taipei",
            "target_date": "2026-08-26",
            "side": "YES",
        },
    ]

    labels, stats = _tier0_candidate_settlement_labels(conn, candidates)

    assert labels == []
    assert stats["pending_rows"] == 2
    assert stats["invalid_truth_rows"] == 1


def test_apply_is_idempotent_and_refolds_a_canonical_correction():
    conn = _trade_conn()
    conn.executemany(
        "INSERT INTO tier0_candidate_set_provenance VALUES (?,?)",
        ((1, None), (2, 0), (3, 1)),
    )
    conn.commit()

    stats = _apply_tier0_candidate_settlement_labels(
        conn,
        ((1, 1), (2, 0), (3, 0), (99, 1)),
    )

    assert stats == {"filled": 1, "corrected": 1, "unchanged": 1, "missing": 1}
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT row_id, settled_y "
            "FROM tier0_candidate_set_provenance ORDER BY row_id"
        ).fetchall()
    ] == [(1, 1), (2, 0), (3, 0)]


def test_post_trade_daemon_runs_fold_every_five_minutes_after_harvester():
    source = (_ROOT / "src/ingest/post_trade_capital_daemon.py").read_text()
    tree = ast.parse(source)
    calls = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_job"
        ):
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        job_id = keywords.get("id")
        if isinstance(job_id, ast.Constant) and job_id.value == (
            "tier0_candidate_settlement_fold"
        ):
            calls.append(keywords)
    assert len(calls) == 1
    keywords = calls[0]
    assert isinstance(keywords["minutes"], ast.Constant)
    assert keywords["minutes"].value == 5
    assert isinstance(keywords["max_instances"], ast.Constant)
    assert keywords["max_instances"].value == 1
    assert isinstance(keywords["coalesce"], ast.Constant)
    assert keywords["coalesce"].value is True
    assert "next_run_time" in keywords
