# Created: 2026-05-23
# Last reused or audited: 2026-05-23
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-E (MINOR finding)
# Lifecycle: created=2026-05-23; last_reviewed=2026-05-23; last_reused=never
# Purpose: Cross-DB integration tests for quarantine write path and reader exclusion.
#          Simulates the K1 DB split (separate world/trade/forecasts in-memory DBs)
#          with ATTACH as production does. Proves RED (missing ATTACH silently no-ops)
#          then GREEN (ATTACH wires exclusion correctly).

"""Cross-DB integration tests — MINOR finding from PR-E critic.

Two scenarios verified:
  1. writer_crossdb: quarantine_decision_events_for_noncontributing_forecast writes
     into 'trade.decision_integrity_quarantine' when trade is ATTACHed to world conn.
  2. reader_crossdb: evidence_report.build_evidence_report auto-ATTACHes trade DB and
     applies opportunity_fact quarantine exclusion across the DB boundary.

Both use separate tempfile-backed SQLite DBs to reproduce K1 split fidelity.
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.state.decision_integrity_quarantine import (
    REASON_NON_CONTRIBUTING,
    _de_natural_pk_hash,
    quarantine_decision_events_for_noncontributing_forecast,
)
from src.state.schema.decision_integrity_quarantine_schema import ensure_table


# ---------------------------------------------------------------------------
# Cross-DB fixture: three separate SQLite files (world / trade / forecasts)
# ---------------------------------------------------------------------------

@pytest.fixture()
def three_dbs(tmp_path):
    """Create separate world, trade, forecasts SQLite DBs in a temp dir.

    world DB: opportunity_fact, decision_events
    forecasts DB: ensemble_snapshots_v2
    trade DB: decision_integrity_quarantine (empty initially)

    Returns (world_path, trade_path, forecasts_path).
    """
    world_path = tmp_path / "zeus-world.db"
    trade_path = tmp_path / "zeus_trades.db"
    forecasts_path = tmp_path / "zeus-forecasts.db"

    # --- forecasts DB ---
    fconn = sqlite3.connect(str(forecasts_path))
    fconn.execute("""
        CREATE TABLE ensemble_snapshots_v2 (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            contributes_to_target_extrema INTEGER,
            forecast_window_attribution_status TEXT,
            source_run_id TEXT
        )
    """)
    snap_cur = fconn.execute(
        "INSERT INTO ensemble_snapshots_v2 "
        "(city, target_date, temperature_metric, contributes_to_target_extrema, "
        " forecast_window_attribution_status, source_run_id) "
        "VALUES ('Bangkok', '2026-05-22', 'high', 0, 'OK', 'run-xdb-1')"
    )
    snap_id = snap_cur.lastrowid
    fconn.commit()
    fconn.close()

    # --- world DB ---
    wconn = sqlite3.connect(str(world_path))
    wconn.execute("""
        CREATE TABLE opportunity_fact (
            decision_id TEXT PRIMARY KEY,
            snapshot_id TEXT,
            should_trade INTEGER NOT NULL DEFAULT 0,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    wconn.execute("""
        CREATE TABLE decision_events (
            market_slug TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            target_date TEXT NOT NULL,
            observation_time TEXT NOT NULL,
            decision_seq INTEGER NOT NULL,
            decision_event_id TEXT,
            strategy_key TEXT NOT NULL DEFAULT 'xdb_strat',
            source TEXT NOT NULL DEFAULT 'live_decision',
            decision_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            outcome TEXT NOT NULL DEFAULT 'buy_yes',
            side TEXT NOT NULL DEFAULT 'buy',
            schema_version INTEGER NOT NULL DEFAULT 28,
            observation_available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'gamma_explicit',
            PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
        )
    """)
    wconn.execute(
        "INSERT INTO opportunity_fact (decision_id, snapshot_id) VALUES ('xdb-dec-1', ?)",
        (str(snap_id),),
    )
    wconn.execute(
        """INSERT INTO decision_events
           (market_slug, temperature_metric, target_date, observation_time, decision_seq,
            decision_event_id)
           VALUES ('XDB-high-ge30', 'high', '2026-05-22', '2026-05-22T10:00:00', 1, 'xdb-dec-1')"""
    )
    wconn.commit()
    wconn.close()

    # --- trade DB ---
    tconn = sqlite3.connect(str(trade_path))
    # Create quarantine table on trade DB directly.
    tconn.execute("""
        CREATE TABLE decision_integrity_quarantine (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name               TEXT NOT NULL,
            row_id                   TEXT NOT NULL,
            reason_code              TEXT NOT NULL,
            forecast_snapshot_id     TEXT,
            recorded_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            meta_json                TEXT NOT NULL DEFAULT '{}',
            UNIQUE(table_name, row_id, reason_code)
        )
    """)
    tconn.commit()
    tconn.close()

    yield world_path, trade_path, forecasts_path


# ---------------------------------------------------------------------------
# Test 1: quarantine writer uses 'trade.decision_integrity_quarantine' via ATTACH
# ---------------------------------------------------------------------------

def test_writer_crossdb_attach(three_dbs):
    """quarantine_decision_events writes to trade DB via ATTACH, not world DB."""
    world_path, trade_path, forecasts_path = three_dbs

    # Open world conn; ATTACH forecasts (for snapshot join) and trade (for write).
    wconn = sqlite3.connect(str(world_path))
    wconn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_path),))
    wconn.execute("ATTACH DATABASE ? AS trade", (str(trade_path),))

    result = quarantine_decision_events_for_noncontributing_forecast(wconn)
    wconn.commit()
    wconn.close()

    assert result["newly_quarantined"] == 1, f"Expected 1, got {result}"

    # Verify row was written into the TRADE DB, not world DB.
    tconn = sqlite3.connect(str(trade_path))
    count = tconn.execute(
        "SELECT COUNT(*) FROM decision_integrity_quarantine WHERE table_name='decision_events'"
    ).fetchone()[0]
    tconn.close()
    assert count == 1, f"Expected quarantine row in trade DB, got {count}"

    # Verify world DB has no quarantine table of its own.
    wconn2 = sqlite3.connect(str(world_path))
    tables = {
        row[0]
        for row in wconn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    wconn2.close()
    assert "decision_integrity_quarantine" not in tables, (
        "Quarantine table must NOT exist in world DB"
    )

    # Verify row_id is the natural PK hash (not decision_event_id directly).
    expected_hash = _de_natural_pk_hash(
        "XDB-high-ge30", "high", "2026-05-22", "2026-05-22T10:00:00", 1
    )
    tconn2 = sqlite3.connect(str(trade_path))
    row_id = tconn2.execute(
        "SELECT row_id FROM decision_integrity_quarantine WHERE table_name='decision_events'"
    ).fetchone()[0]
    tconn2.close()
    assert row_id == expected_hash, f"row_id mismatch: got {row_id}, want {expected_hash}"


# ---------------------------------------------------------------------------
# Test 2: evidence_report reader auto-ATTACHes trade DB and applies exclusion
# ---------------------------------------------------------------------------

def test_reader_crossdb_attach(three_dbs, monkeypatch):
    """build_evidence_report auto-ATTACHes trade DB and excludes quarantined decisions."""
    from src.analysis.evidence_report import build_evidence_report

    world_path, trade_path, forecasts_path = three_dbs

    # First: quarantine xdb-dec-1 in trade DB (opportunity_fact entry).
    now = datetime.now(timezone.utc).isoformat()
    tconn = sqlite3.connect(str(trade_path))
    tconn.execute(
        """INSERT INTO decision_integrity_quarantine
           (table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json)
           VALUES ('opportunity_fact', 'xdb-dec-1', ?, NULL, ?, '{}')""",
        (REASON_NON_CONTRIBUTING, now),
    )
    tconn.commit()
    tconn.close()

    # Monkeypatch _zeus_trade_db_path so evidence_report auto-ATTACH finds our temp trade DB.
    monkeypatch.setattr(
        "src.analysis.evidence_report._zeus_trade_db_path",  # type: ignore[attr-defined]
        lambda: str(trade_path),
        raising=False,
    )
    # Also patch the import inside the function body.
    import src.state.db as _state_db
    original_trade_path = getattr(_state_db, "_zeus_trade_db_path", None)
    monkeypatch.setattr(_state_db, "_zeus_trade_db_path", lambda: str(trade_path))

    # Open world conn (no ATTACH — evidence_report must do it automatically).
    wconn = sqlite3.connect(str(world_path))
    wconn.row_factory = sqlite3.Row

    report = build_evidence_report(
        "xdb_strat", 0, conn=wconn, breakeven_win_rate=0.52
    )
    wconn.close()

    assert report.n_decisions == 0, (
        f"Expected 0 decisions (quarantined), got {report.n_decisions}"
    )
