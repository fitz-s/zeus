# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/archive/2026-Q2/operations_historical/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-E
# Lifecycle: created=2026-05-22; last_reviewed=2026-05-22; last_reused=never
# Purpose: Unit tests for decision_integrity_quarantine — quarantine logic, idempotency, ensure_table.
# Reuse: Run when quarantine_decisions_for_noncontributing_forecast, ensure_table, or decision_integrity_quarantine schema changes.

"""PR-E — Tests for decision_integrity_quarantine tooling.

Tests quarantine_decisions_for_noncontributing_forecast against an in-memory
SQLite DB with both opportunity_fact and ensemble_snapshots co-located
(no ATTACH needed — auto-detected by the function).

Invariants verified:
  1. Only contributes=0 rows are quarantined; contributes=1 rows are skipped.
  2. UNKNOWN attribution rows are quarantined.
  3. The function is idempotent (run twice → no duplicates).
  4. decision_integrity_quarantine table created via ensure_table is idempotent.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.state.decision_integrity_quarantine import (
    DECISION_CERTIFICATES_TABLE,
    REASON_INVALID_LIVE_ACTIONABLE,
    REASON_NON_CONTRIBUTING,
    TARGET_TABLE,
    quarantine_decisions_for_noncontributing_forecast,
    quarantine_invalid_live_actionable_certificates,
)
from src.state.schema.decision_integrity_quarantine_schema import ensure_table


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mem_db():
    """In-memory SQLite with opportunity_fact + ensemble_snapshots + quarantine table.

    No ATTACH needed — both tables in same DB; the function auto-detects
    absence of 'forecasts' schema and uses bare table name.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Create ensemble_snapshots (minimal columns needed for quarantine join).
    conn.execute("""
        CREATE TABLE ensemble_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            contributes_to_target_extrema INTEGER,
            forecast_window_attribution_status TEXT
        )
    """)

    # Create opportunity_fact (minimal columns needed).
    conn.execute("""
        CREATE TABLE opportunity_fact (
            decision_id TEXT PRIMARY KEY,
            snapshot_id TEXT,
            should_trade INTEGER NOT NULL DEFAULT 0,
            recorded_at TEXT NOT NULL
        )
    """)

    # Create quarantine table.
    ensure_table(conn)

    conn.commit()
    yield conn
    conn.close()


def _insert_snapshot(conn, *, contributes: int | None, attribution: str | None) -> int:
    """Insert one ensemble_snapshots row; return snapshot_id."""
    cur = conn.execute(
        """
        INSERT INTO ensemble_snapshots
            (city, target_date, temperature_metric,
             contributes_to_target_extrema, forecast_window_attribution_status)
        VALUES ('Taipei', '2026-05-22', 'high', ?, ?)
        """,
        (contributes, attribution),
    )
    conn.commit()
    return cur.lastrowid


def _insert_opportunity(conn, *, decision_id: str, snapshot_id: int) -> None:
    """Insert one opportunity_fact row referencing the given snapshot."""
    conn.execute(
        """
        INSERT INTO opportunity_fact (decision_id, snapshot_id, should_trade, recorded_at)
        VALUES (?, ?, 0, ?)
        """,
        (decision_id, str(snapshot_id), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def _quarantine_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM decision_integrity_quarantine").fetchone()[0]


def _quarantined_ids(conn) -> set[str]:
    rows = conn.execute(
        "SELECT row_id FROM decision_integrity_quarantine WHERE table_name=? AND reason_code=?",
        (TARGET_TABLE, REASON_NON_CONTRIBUTING),
    ).fetchall()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_ensure_table_idempotent(mem_db):
    """ensure_table can be called multiple times without error."""
    ensure_table(mem_db)
    ensure_table(mem_db)
    count = mem_db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='decision_integrity_quarantine'"
    ).fetchone()[0]
    assert count == 1


def test_contributes_zero_is_quarantined(mem_db):
    """opportunity_fact row with contributes=0 snapshot is tagged."""
    snap_id = _insert_snapshot(mem_db, contributes=0, attribution="OK")
    _insert_opportunity(mem_db, decision_id="dec-bad-1", snapshot_id=snap_id)

    result = quarantine_decisions_for_noncontributing_forecast(mem_db)

    assert result["candidates_found"] == 1
    assert result["newly_quarantined"] == 1
    assert result["already_quarantined"] == 0
    assert "dec-bad-1" in _quarantined_ids(mem_db)


def test_contributes_one_is_not_quarantined(mem_db):
    """opportunity_fact row with contributes=1 snapshot is skipped."""
    snap_id = _insert_snapshot(mem_db, contributes=1, attribution="OK")
    _insert_opportunity(mem_db, decision_id="dec-good-1", snapshot_id=snap_id)

    result = quarantine_decisions_for_noncontributing_forecast(mem_db)

    assert result["candidates_found"] == 0
    assert result["newly_quarantined"] == 0
    assert _quarantine_count(mem_db) == 0


def test_unknown_attribution_is_quarantined(mem_db):
    """opportunity_fact row with attribution=UNKNOWN snapshot is tagged (even contributes=1)."""
    snap_id = _insert_snapshot(mem_db, contributes=1, attribution="UNKNOWN")
    _insert_opportunity(mem_db, decision_id="dec-unknown-1", snapshot_id=snap_id)

    result = quarantine_decisions_for_noncontributing_forecast(mem_db)

    assert result["candidates_found"] == 1
    assert result["newly_quarantined"] == 1
    assert "dec-unknown-1" in _quarantined_ids(mem_db)


def test_mixed_only_bad_rows_quarantined(mem_db):
    """With one contributes=0 and one contributes=1, only the bad one is tagged."""
    bad_snap = _insert_snapshot(mem_db, contributes=0, attribution="OK")
    good_snap = _insert_snapshot(mem_db, contributes=1, attribution="OK")

    _insert_opportunity(mem_db, decision_id="dec-bad", snapshot_id=bad_snap)
    _insert_opportunity(mem_db, decision_id="dec-good", snapshot_id=good_snap)

    result = quarantine_decisions_for_noncontributing_forecast(mem_db)

    assert result["candidates_found"] == 1
    assert result["newly_quarantined"] == 1
    ids = _quarantined_ids(mem_db)
    assert "dec-bad" in ids
    assert "dec-good" not in ids


def test_idempotent_no_duplicates(mem_db):
    """Running quarantine twice produces no duplicate rows."""
    snap_id = _insert_snapshot(mem_db, contributes=0, attribution="OK")
    _insert_opportunity(mem_db, decision_id="dec-idem", snapshot_id=snap_id)

    result1 = quarantine_decisions_for_noncontributing_forecast(mem_db)
    result2 = quarantine_decisions_for_noncontributing_forecast(mem_db)

    assert result1["newly_quarantined"] == 1
    # Second run: candidate found but already quarantined — no new rows.
    assert result2["candidates_found"] == 1
    assert result2["newly_quarantined"] == 0
    assert result2["already_quarantined"] == 1
    # Exactly one row in the table — no duplicates.
    assert _quarantine_count(mem_db) == 1


def test_dry_run_writes_nothing(mem_db):
    """dry_run=True returns counts but writes no rows."""
    snap_id = _insert_snapshot(mem_db, contributes=0, attribution="OK")
    _insert_opportunity(mem_db, decision_id="dec-dry", snapshot_id=snap_id)

    result = quarantine_decisions_for_noncontributing_forecast(mem_db, dry_run=True)

    assert result["candidates_found"] == 1
    assert result["dry_run"] is True
    assert _quarantine_count(mem_db) == 0


def test_null_contributes_is_not_quarantined(mem_db):
    """NULL contributes_to_target_extrema (legacy snapshot) is NOT quarantined.

    Aligns with the live reader gate (PR-A), which only acts when
    contributes_to_target_extrema is EXPLICITLY set; legacy NULL rows passed
    through live unblocked and were not bug-affected, so the cleanup must not
    quarantine them (scope = exactly the decisions the bug touched).
    """
    snap_id = _insert_snapshot(mem_db, contributes=None, attribution="OK")
    _insert_opportunity(mem_db, decision_id="dec-null", snapshot_id=snap_id)

    result = quarantine_decisions_for_noncontributing_forecast(mem_db)

    assert result["candidates_found"] == 0
    assert "dec-null" not in _quarantined_ids(mem_db)


def test_no_snapshot_id_skipped(mem_db):
    """opportunity_fact rows with NULL snapshot_id are not tagged (no join key)."""
    conn = mem_db
    conn.execute(
        "INSERT INTO opportunity_fact (decision_id, snapshot_id, should_trade, recorded_at) VALUES (?, NULL, 0, ?)",
        ("dec-no-snap", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    result = quarantine_decisions_for_noncontributing_forecast(conn)

    assert result["candidates_found"] == 0
    assert _quarantine_count(conn) == 0


def _valid_actionable_payload() -> dict:
    return {
        "event_id": "event-1",
        "event_type": "FORECAST_SNAPSHOT_READY",
        "causal_snapshot_id": "snap-1",
        "family_id": "family-1",
        "candidate_id": "candidate-1",
        "condition_id": "condition-1",
        "token_id": "yes-1",
        "direction": "buy_yes",
        "strategy_key": "center_buy",
        "executable_snapshot_id": "exec-1",
        "q_live": 0.7,
        "q_lcb_5pct": 0.6,
        "c_fee_adjusted": 0.4,
        "c_cost_95pct": 0.45,
        "p_fill_lcb": 0.1,
        "trade_score": 0.2,
        "action_score": 0.2,
        "selection_authority_applied": "qkernel_spine",
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "side": "YES",
            "payoff_q_point": 0.7,
            "payoff_q_lcb": 0.6,
            "cost": 0.4,
            "edge_lcb": 0.2,
            "optimal_delta_u": 0.01,
            "false_edge_rate": 0.01,
            "direction_law_ok": True,
            "coherence_allows": True,
        },
        "fdr_family_id": "family-1",
        "kelly_decision_id": "kelly-1",
        "kelly_size_usd": 3.0,
        "risk_decision_id": "risk-1",
        "live_cap_usage_id": "cap-1",
        "final_intent_id": "intent-1",
        "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
        "native_quote_available": True,
        "submitted": False,
    }


def test_invalid_live_actionable_quarantine_tags_bad_verified_cert(mem_db):
    conn = mem_db
    conn.execute(
        """
        CREATE TABLE decision_certificates (
            certificate_id TEXT PRIMARY KEY,
            certificate_hash TEXT NOT NULL,
            certificate_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            verifier_status TEXT NOT NULL,
            decision_time TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    payload = _valid_actionable_payload()
    payload["q_live"] = 0.005
    payload["q_lcb_5pct"] = 0.003
    payload["qkernel_execution_economics"]["payoff_q_point"] = 0.22
    payload["qkernel_execution_economics"]["payoff_q_lcb"] = 0.05
    payload["qkernel_execution_economics"]["direction_law_ok"] = False
    conn.execute(
        """
        INSERT INTO decision_certificates (
            certificate_id, certificate_hash, certificate_type, mode,
            verifier_status, decision_time, payload_json
        ) VALUES ('cert-1', 'hash-1', 'ActionableTradeCertificate', 'LIVE',
                  'VERIFIED', ?, ?)
        """,
        (datetime.now(timezone.utc).isoformat(), json.dumps(payload)),
    )
    conn.commit()

    dry = quarantine_invalid_live_actionable_certificates(conn, dry_run=True)
    applied = quarantine_invalid_live_actionable_certificates(conn)
    applied_again = quarantine_invalid_live_actionable_certificates(conn)

    assert dry["candidates_found"] == 1
    assert dry["dry_run"] is True
    assert applied["newly_quarantined"] == 1
    assert applied_again["newly_quarantined"] == 0
    row = conn.execute(
        """
        SELECT row_id
          FROM decision_integrity_quarantine
         WHERE table_name = ?
           AND reason_code = ?
        """,
        (DECISION_CERTIFICATES_TABLE, REASON_INVALID_LIVE_ACTIONABLE),
    ).fetchone()
    assert row[0] == "hash-1"
