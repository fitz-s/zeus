# Created: 2026-05-18
# Last reused or audited: 2026-05-20
# Authority basis: /tmp/inv37_wave2_plan.md; PR-S4b §3; architecture/db_table_ownership.yaml
# Lifecycle: created=2026-05-18; last_reviewed=2026-05-19; last_reused=2026-05-19
# Purpose: Antibody tests verifying INV-37 wave-2 writers route to canonical DB (not caller conn)
# Reuse: import and call individual test functions; fixtures are session-scoped
"""Antibody tests for INV-37 writer schema qualification — wave-2 (log_opportunity_fact,
log_market_source_contract_topology_facts, append_source_contract_audit_events).

INV-37: cross-DB writes must land in the canonical owner DB, not the caller-supplied conn.

Wave-2 writers fixed:
  - log_opportunity_fact            → zeus_trades.db  (opportunity_fact)
  - log_market_source_contract_topology_facts → zeus-world.db (market_topology_state)
  - append_source_contract_audit_events       → zeus-world.db (source_contract_audit_events)

Each writer now opens its own internal connection and ignores the passed conn.
These tests verify:
  1. Rows land in the canonical DB (COUNT > 0 after write + close + reopen).
  2. Rows do NOT land in the caller-supplied foreign conn.
  3. Sed-break contract: if get_trade_connection / get_world_connection is NOT called
     internally, this test fails — proving the test catches what it claims.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures: ephemeral trade + world DBs with the relevant tables
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_trade_db(tmp_path):
    """Minimal zeus_trades.db with opportunity_fact table."""
    db_path = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS opportunity_fact (
            decision_id TEXT PRIMARY KEY,
            candidate_id TEXT,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            direction TEXT,
            strategy_key TEXT,
            discovery_mode TEXT,
            entry_method TEXT,
            snapshot_id TEXT,
            p_raw REAL,
            p_cal REAL,
            p_market REAL,
            alpha REAL,
            best_edge REAL,
            ci_width REAL,
            rejection_stage TEXT,
            rejection_reason_json TEXT,
            availability_status TEXT,
            should_trade INTEGER,
            recorded_at TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def tmp_world_db(tmp_path):
    """Minimal zeus-world.db with market_topology_state + source_contract_audit_events."""
    db_path = tmp_path / "zeus-world.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS market_topology_state (
            topology_id TEXT,
            scope_key TEXT UNIQUE,
            market_family TEXT,
            condition_id TEXT,
            status TEXT,
            source_contract_status TEXT,
            authority_status TEXT,
            event_id TEXT,
            question_id TEXT,
            city_id TEXT,
            city_timezone TEXT,
            target_local_date TEXT,
            temperature_metric TEXT,
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            token_ids_json TEXT,
            bin_topology_hash TEXT,
            gamma_captured_at TEXT,
            gamma_updated_at TEXT,
            source_contract_reason TEXT,
            expires_at TEXT,
            provenance_json TEXT,
            recorded_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS source_contract_audit_events (
            audit_id TEXT PRIMARY KEY,
            checked_at_utc TEXT NOT NULL,
            scan_authority TEXT NOT NULL,
            report_status TEXT,
            severity TEXT NOT NULL,
            event_id TEXT NOT NULL,
            slug TEXT,
            title TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_contract_status TEXT NOT NULL,
            source_contract_reason TEXT,
            configured_source_family TEXT,
            configured_station_id TEXT,
            observed_source_family TEXT,
            observed_station_id TEXT,
            resolution_sources_json TEXT,
            source_contract_json TEXT,
            payload_hash TEXT,
            created_at TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def tmp_foreign_db(tmp_path):
    """A 'wrong' DB (simulates caller passing forecasts conn) — no target tables."""
    db_path = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS ensemble_snapshots_v2 (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Fake domain objects for log_opportunity_fact
# ---------------------------------------------------------------------------

class _FakeEdge:
    direction = "buy_yes"
    p_model = 0.6
    p_market = 0.55
    edge = 0.05
    ci_upper = 0.65
    ci_lower = 0.55
    bin = type("Bin", (), {"label": "above 30"})()


class _FakeCandidate:
    city = "Dubai"
    target_date = "2026-05-18"
    discovery_mode = "live"
    event_id = "evt-w2-001"


class _FakeDecision:
    decision_id = "dec-w2-001"
    decision_snapshot_id = "snap-w2-001"
    strategy_key = "settlement_capture"
    availability_status = "available"
    p_raw = None
    p_cal = None
    p_market = None
    alpha = None
    edge = _FakeEdge()
    selected_method = "market_order"
    entry_method = "market_order"


# ---------------------------------------------------------------------------
# Test: log_opportunity_fact routes to trade DB
# ---------------------------------------------------------------------------

def test_opportunity_fact_lands_in_trade_db(tmp_trade_db, tmp_foreign_db):
    """Row must appear in zeus_trades.db, NOT in the caller-supplied foreign conn."""
    from src.state.db import log_opportunity_fact

    foreign_conn = sqlite3.connect(str(tmp_foreign_db))

    with patch("src.state.db.get_trade_connection") as mock_trade:
        trade_conn = sqlite3.connect(str(tmp_trade_db))
        mock_trade.return_value = trade_conn

        result = log_opportunity_fact(
            foreign_conn,
            candidate=_FakeCandidate(),
            decision=_FakeDecision(),
            should_trade=True,
            rejection_stage="none",
            rejection_reasons=None,
            recorded_at="2026-05-18T12:00:00+00:00",
        )
        # trade_conn is closed by the function's finally block

    foreign_conn.close()

    assert result.get("status") == "written", f"Expected written, got {result}"

    # Verify row landed in trade DB (reopen after function closed it)
    trade_check = sqlite3.connect(str(tmp_trade_db))
    count = trade_check.execute(
        "SELECT COUNT(*) FROM opportunity_fact WHERE decision_id='dec-w2-001'"
    ).fetchone()[0]
    trade_check.close()
    assert count == 1, f"Expected 1 row in zeus_trades.db opportunity_fact, got {count}"

    # Verify row did NOT land in foreign DB
    foreign_check = sqlite3.connect(str(tmp_foreign_db))
    tables = {r[0] for r in foreign_check.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    foreign_check.close()
    assert "opportunity_fact" not in tables, "opportunity_fact must NOT exist on foreign DB"


def test_opportunity_fact_reuses_verified_trade_connection(tmp_trade_db):
    """A live trade-DB caller conn must not self-deadlock through a second writer."""
    from src.state.db import log_opportunity_fact

    trade_conn = sqlite3.connect(str(tmp_trade_db))

    with (
        patch("src.state.db._zeus_trade_db_path", return_value=tmp_trade_db),
        patch("src.state.db.get_trade_connection") as mock_trade,
    ):
        mock_trade.side_effect = AssertionError("must reuse the verified trade connection")
        result = log_opportunity_fact(
            trade_conn,
            candidate=_FakeCandidate(),
            decision=_FakeDecision(),
            should_trade=True,
            rejection_stage="none",
            rejection_reasons=None,
            recorded_at="2026-05-18T12:00:00+00:00",
        )

    count = trade_conn.execute(
        "SELECT COUNT(*) FROM opportunity_fact WHERE decision_id='dec-w2-001'"
    ).fetchone()[0]
    trade_conn.close()

    assert result.get("status") == "written"
    assert count == 1


def test_opportunity_fact_sed_break(tmp_trade_db, tmp_foreign_db):
    """Sed-break: unsafe caller conns still route through get_trade_connection()."""
    from src.state.db import log_opportunity_fact
    import inspect

    src = inspect.getsource(log_opportunity_fact)
    assert "get_trade_connection" in src, (
        "sed-break: log_opportunity_fact must call get_trade_connection() for unsafe conns. "
        "If this assertion fails, the INV-37 wave-2 fix was reverted."
    )


# ---------------------------------------------------------------------------
# Test: log_market_source_contract_topology_facts routes to world DB
# ---------------------------------------------------------------------------

def _make_market(slug: str, city: str, target_date: str, condition_id: str) -> dict:
    return {
        "slug": slug,
        "event_id": f"evt-{slug}",
        "city": city,
        "target_date": target_date,
        "temperature_metric": "high",
        "data_version": "gamma_source_contract_v1",
        "source_contract": {
            "status": "MATCH",
            "reason": "station verified",
            "resolution_sources": ["NOAA"],
        },
        "outcomes": [
            {
                "condition_id": condition_id,
                "question_id": f"q-{condition_id}",
                "token_id": f"tok-yes-{condition_id}",
                "no_token_id": f"tok-no-{condition_id}",
            }
        ],
    }


def test_market_topology_facts_lands_in_world_db(tmp_world_db, tmp_foreign_db):
    """Row must appear in zeus-world.db market_topology_state, NOT in foreign conn."""
    from src.state.db import log_market_source_contract_topology_facts

    foreign_conn = sqlite3.connect(str(tmp_foreign_db))

    with patch("src.state.db.get_world_connection") as mock_world:
        world_conn = sqlite3.connect(str(tmp_world_db))
        mock_world.return_value = world_conn

        result = log_market_source_contract_topology_facts(
            foreign_conn,
            markets=[_make_market("dubai-high-2026-05-18", "Dubai", "2026-05-18", "cond-001")],
            recorded_at="2026-05-18T12:00:00+00:00",
            scan_authority="VERIFIED",
        )
        # world_conn is closed by the function's finally block

    foreign_conn.close()

    assert result.get("status") == "written", f"Expected written, got {result}"

    world_check = sqlite3.connect(str(tmp_world_db))
    count = world_check.execute(
        "SELECT COUNT(*) FROM market_topology_state"
    ).fetchone()[0]
    world_check.close()
    assert count >= 1, f"Expected >=1 row in zeus-world.db market_topology_state, got {count}"

    foreign_check = sqlite3.connect(str(tmp_foreign_db))
    tables = {r[0] for r in foreign_check.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    foreign_check.close()
    assert "market_topology_state" not in tables, (
        "market_topology_state must NOT exist on foreign DB"
    )


def test_market_topology_facts_sed_break(tmp_world_db, tmp_foreign_db):
    """Sed-break: log_market_source_contract_topology_facts must call get_world_connection."""
    from src.state.db import log_market_source_contract_topology_facts
    import inspect

    src = inspect.getsource(log_market_source_contract_topology_facts)
    assert "get_world_connection" in src, (
        "sed-break: log_market_source_contract_topology_facts must call get_world_connection() "
        "internally. If this assertion fails, the INV-37 wave-2 fix was reverted."
    )


# ---------------------------------------------------------------------------
# Test: append_source_contract_audit_events routes to world DB
# ---------------------------------------------------------------------------

def _make_audit_report(event_id: str) -> dict:
    return {
        "checked_at_utc": "2026-05-18T12:00:00Z",
        "authority": "VERIFIED",
        "status": "OK",
        "events": [
            {
                "event_id": event_id,
                "slug": event_id,
                "city": "Dubai",
                "target_date": "2026-05-18",
                "temperature_metric": "high",
                "severity": "OK",
                "source_contract": {
                    "status": "MATCH",
                    "reason": "station verified",
                    "resolution_sources": ["NOAA"],
                },
            }
        ],
    }


def test_audit_events_land_in_world_db(tmp_world_db, tmp_foreign_db):
    """Row must appear in zeus-world.db source_contract_audit_events, NOT in foreign conn."""
    from src.state.db import append_source_contract_audit_events

    foreign_conn = sqlite3.connect(str(tmp_foreign_db))

    with patch("src.state.db.get_world_connection") as mock_world:
        world_conn = sqlite3.connect(str(tmp_world_db))
        mock_world.return_value = world_conn

        result = append_source_contract_audit_events(
            foreign_conn,
            report=_make_audit_report("evt-audit-w2-001"),
        )
        # world_conn is closed by the function's finally block

    foreign_conn.close()

    assert result.get("status") == "written", f"Expected written, got {result}"

    world_check = sqlite3.connect(str(tmp_world_db))
    count = world_check.execute(
        "SELECT COUNT(*) FROM source_contract_audit_events"
    ).fetchone()[0]
    world_check.close()
    assert count == 1, f"Expected 1 row in zeus-world.db source_contract_audit_events, got {count}"

    foreign_check = sqlite3.connect(str(tmp_foreign_db))
    tables = {r[0] for r in foreign_check.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    foreign_check.close()
    assert "source_contract_audit_events" not in tables, (
        "source_contract_audit_events must NOT exist on foreign DB"
    )


def test_audit_events_sed_break(tmp_world_db, tmp_foreign_db):
    """Sed-break: append_source_contract_audit_events must call get_world_connection."""
    from src.state.db import append_source_contract_audit_events
    import inspect

    src = inspect.getsource(append_source_contract_audit_events)
    assert "get_world_connection" in src, (
        "sed-break: append_source_contract_audit_events must call get_world_connection() "
        "internally. If this assertion fails, the INV-37 wave-2 fix was reverted."
    )
