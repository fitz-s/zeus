# Created: 2026-05-23
# Last reused or audited: 2026-07-12
# Authority basis: docs/archive/2026-Q2/operations_historical/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-E (MINOR finding);
#   docs/rebuild/quarantine_excision_2026-07-11.md DIQ packet (owner-local reshape).
# Lifecycle: created=2026-05-23; last_reviewed=2026-07-12; last_reused=never
# Purpose: Cross-DB integration tests for the owner-local revocation write path and
#          reader exclusion. Simulates the K1 DB split (separate world/trade/forecasts
#          in-memory DBs) with ATTACH as production does.
#          Supersedes tests/test_decision_integrity_quarantine_crossdb.py.

"""Cross-DB integration tests — owner-local reshape (DIQ packet).

Three scenarios verified:
  1. writer_local (decision_events is world-owned): revoke_decision_events_
     for_noncontributing_forecast writes LOCALLY into world's own
     fact_revocations table — no cross-DB ATTACH needed (INVERTS the
     predecessor's always-trade write; decision_events lives in world,
     src/state/domains.py, so its owner-local revocation record does too).
  2. writer_crossdb (opportunity_fact is trade-owned): the SAME world-main
     connection, with trade ATTACHed, writes opportunity_fact's revocation
     into 'trade.fact_revocations' via target_schema="trade" — this is the
     one table in the world CLI pass that still needs cross-DB ATTACH.
  3. reader_crossdb: evidence_report.build_evidence_report auto-ATTACHes
     trade DB and applies opportunity_fact revocation exclusion across the
     DB boundary (opportunity_fact's revocation record is trade-owned
     regardless of which DB the reader's connection is rooted at).

Both use separate tempfile-backed SQLite DBs to reproduce K1 split fidelity.
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.state.fact_revocation import (
    REASON_NON_CONTRIBUTING,
    _de_natural_pk_hash,
    revoke_decision_events_for_noncontributing_forecast,
    revoke_decisions_for_noncontributing_forecast,
)
from src.state.schema.fact_revocations_schema import ensure_table


# ---------------------------------------------------------------------------
# Cross-DB fixture: three separate SQLite files (world / trade / forecasts)
# ---------------------------------------------------------------------------

@pytest.fixture()
def three_dbs(tmp_path):
    """Create separate world, trade, forecasts SQLite DBs in a temp dir.

    world DB: opportunity_fact, decision_events, fact_revocations (owner-local
      instance for decision_events + other world-owned tables)
    forecasts DB: ensemble_snapshots
    trade DB: fact_revocations (owner-local instance for opportunity_fact; empty
      initially)

    Returns (world_path, trade_path, forecasts_path).
    """
    world_path = tmp_path / "zeus-world.db"
    trade_path = tmp_path / "zeus_trades.db"
    forecasts_path = tmp_path / "zeus-forecasts.db"

    # --- forecasts DB ---
    fconn = sqlite3.connect(str(forecasts_path))
    fconn.execute("""
        CREATE TABLE ensemble_snapshots (
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
        "INSERT INTO ensemble_snapshots "
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
    # decision_events is world-owned: its owner-local fact_revocations
    # instance lives HERE, not on trade.
    ensure_table(wconn)
    wconn.commit()
    wconn.close()

    # --- trade DB ---
    tconn = sqlite3.connect(str(trade_path))
    # opportunity_fact's owner-local fact_revocations instance.
    ensure_table(tconn)
    tconn.commit()
    tconn.close()

    yield world_path, trade_path, forecasts_path


# ---------------------------------------------------------------------------
# Test 1: world-owned revocation writes LOCALLY (no ATTACH needed)
# ---------------------------------------------------------------------------

def test_writer_local_world_owned_table(three_dbs):
    """revoke_decision_events writes into world's OWN fact_revocations table
    (decision_events is world-owned; owner-local, target_schema="main" default)."""
    world_path, trade_path, forecasts_path = three_dbs

    # Open world conn; ATTACH forecasts only (for snapshot join) — NO trade ATTACH.
    wconn = sqlite3.connect(str(world_path))
    wconn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_path),))

    result = revoke_decision_events_for_noncontributing_forecast(wconn)
    wconn.commit()

    assert result["newly_revoked"] == 1, f"Expected 1, got {result}"

    # Verify row was written into WORLD's own fact_revocations, not trade.
    count = wconn.execute(
        "SELECT COUNT(*) FROM fact_revocations WHERE table_name='decision_events'"
    ).fetchone()[0]
    wconn.close()
    assert count == 1, f"Expected revocation row in world DB, got {count}"

    tconn = sqlite3.connect(str(trade_path))
    trade_count = tconn.execute(
        "SELECT COUNT(*) FROM fact_revocations WHERE table_name='decision_events'"
    ).fetchone()[0]
    tconn.close()
    assert trade_count == 0, "decision_events revocation must NOT land in trade DB"

    # Verify row_id is the natural PK hash (not decision_event_id directly).
    expected_hash = _de_natural_pk_hash(
        "XDB-high-ge30", "high", "2026-05-22", "2026-05-22T10:00:00", 1
    )
    wconn2 = sqlite3.connect(str(world_path))
    row_id = wconn2.execute(
        "SELECT row_id FROM fact_revocations WHERE table_name='decision_events'"
    ).fetchone()[0]
    wconn2.close()
    assert row_id == expected_hash, f"row_id mismatch: got {row_id}, want {expected_hash}"


# ---------------------------------------------------------------------------
# Test 2: trade-owned revocation (opportunity_fact) still crosses DBs via ATTACH
# ---------------------------------------------------------------------------

def test_writer_crossdb_attach_trade_owned_table(three_dbs):
    """revoke_decisions_for_noncontributing_forecast (opportunity_fact) writes to
    trade DB via ATTACH + target_schema='trade' — the one table in the world
    CLI pass still requiring a cross-DB write (opportunity_fact is trade-owned)."""
    world_path, trade_path, forecasts_path = three_dbs

    wconn = sqlite3.connect(str(world_path))
    wconn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_path),))
    wconn.execute("ATTACH DATABASE ? AS trade", (str(trade_path),))

    result = revoke_decisions_for_noncontributing_forecast(wconn, target_schema="trade")
    wconn.commit()
    wconn.close()

    assert result["newly_revoked"] == 1, f"Expected 1, got {result}"

    tconn = sqlite3.connect(str(trade_path))
    count = tconn.execute(
        "SELECT COUNT(*) FROM fact_revocations WHERE table_name='opportunity_fact'"
    ).fetchone()[0]
    tconn.close()
    assert count == 1, f"Expected revocation row in trade DB, got {count}"

    wconn2 = sqlite3.connect(str(world_path))
    world_count = wconn2.execute(
        "SELECT COUNT(*) FROM fact_revocations WHERE table_name='opportunity_fact'"
    ).fetchone()[0]
    wconn2.close()
    assert world_count == 0, "opportunity_fact revocation must NOT land in world DB"


# ---------------------------------------------------------------------------
# Test 3: reader crosses DBs to read trade-owned opportunity_fact revocations
# ---------------------------------------------------------------------------

def test_reader_crossdb_attach(three_dbs, monkeypatch):
    """build_evidence_report auto-ATTACHes trade DB and excludes revoked decisions."""
    from src.analysis.evidence_report import build_evidence_report

    world_path, trade_path, forecasts_path = three_dbs

    # First: revoke xdb-dec-1 in trade DB (opportunity_fact entry — trade-owned).
    now = datetime.now(timezone.utc).isoformat()
    tconn = sqlite3.connect(str(trade_path))
    tconn.execute(
        """INSERT INTO fact_revocations
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
    monkeypatch.setattr(_state_db, "_zeus_trade_db_path", lambda: str(trade_path))

    # Open world conn (no ATTACH — evidence_report must do it automatically).
    wconn = sqlite3.connect(str(world_path))
    wconn.row_factory = sqlite3.Row

    report = build_evidence_report(
        "xdb_strat", 0, conn=wconn, breakeven_win_rate=0.52
    )
    wconn.close()

    assert report.n_decisions == 0, (
        f"Expected 0 decisions (revoked), got {report.n_decisions}"
    )


# ---------------------------------------------------------------------------
# Test 4 (GREEN): _run_world_tables --apply routes each table to its owning DB
# ---------------------------------------------------------------------------

def test_run_world_tables_owner_local_green(three_dbs, monkeypatch):
    """Prove that _run_world_tables (owner-local, DIQ packet):
      1. Writes decision_events revocations LOCALLY into world's own fact_revocations.
      2. Writes opportunity_fact revocations into trade DB (cross-DB via ATTACH).
      3. build_evidence_report correctly excludes revoked rows (n_decisions=0).
    """
    import sys
    from pathlib import Path as _Path

    _SCRIPTS = _Path(__file__).parent.parent / "scripts"
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    import importlib
    import revoke_bad_forecast_decisions as _qbd
    importlib.reload(_qbd)  # ensure we have the patched version

    from src.analysis.evidence_report import build_evidence_report

    world_path, trade_path, forecasts_path = three_dbs

    # Apply revocation via the CLI runner (apply=True).
    results = _qbd._run_world_tables(
        world_path, trade_path, forecasts_path, dry_run=False
    )
    assert results["opportunity_fact"]["newly_revoked"] == 1
    assert results["decision_events"]["newly_revoked"] == 1

    # 1. decision_events revocation lands in WORLD, not trade.
    wconn_check = sqlite3.connect(str(world_path))
    world_de_count = wconn_check.execute(
        "SELECT COUNT(*) FROM fact_revocations WHERE table_name='decision_events'"
    ).fetchone()[0]
    wconn_check.close()
    assert world_de_count == 1, "decision_events revocation must land in world DB"

    # 2. opportunity_fact revocation lands in TRADE, not world.
    tconn = sqlite3.connect(str(trade_path))
    trade_of_count = tconn.execute(
        "SELECT COUNT(*) FROM fact_revocations WHERE table_name='opportunity_fact'"
    ).fetchone()[0]
    tconn.close()
    assert trade_of_count == 1, f"Expected 1 revocation row in trade DB, got {trade_of_count}"

    # 3. evidence_report exclusion fires correctly (n_decisions=0).
    import src.state.db as _state_db
    monkeypatch.setattr(_state_db, "_zeus_trade_db_path", lambda: str(trade_path))

    wconn = sqlite3.connect(str(world_path))
    wconn.row_factory = sqlite3.Row

    report = build_evidence_report(
        "xdb_strat", 0, conn=wconn, breakeven_win_rate=0.52
    )
    wconn.close()

    # GREEN: owner-local write lands correctly per-DB; trade-attached read
    # exclusion fires for opportunity_fact -> n_decisions=0.
    assert report.n_decisions == 0, (
        f"GREEN scenario: expected 0 decisions (excluded via trade revocation), got {report.n_decisions}"
    )
