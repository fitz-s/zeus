# Created: 2026-07-12
# Last reused or audited: 2026-07-12
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md DIQ packet.
# Purpose: Fixture-DB tests for scripts/migrate_decision_integrity_quarantine_to_fact_revocations.py
#          — the 3-DB backfill from the predecessor central table to owner-local
#          fact_revocations, including the parity-failure abort path.

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import migrate_decision_integrity_quarantine_to_fact_revocations as migration  # noqa: E402


_OLD_DDL = """
CREATE TABLE decision_integrity_quarantine (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name               TEXT NOT NULL,
    row_id                   TEXT NOT NULL,
    reason_code              TEXT NOT NULL,
    forecast_snapshot_id     TEXT,
    recorded_at              TEXT NOT NULL,
    meta_json                TEXT NOT NULL DEFAULT '{}',
    UNIQUE(table_name, row_id, reason_code)
)
"""


@pytest.fixture()
def three_conns():
    trade_conn = sqlite3.connect(":memory:")
    world_conn = sqlite3.connect(":memory:")
    forecasts_conn = sqlite3.connect(":memory:")
    trade_conn.execute(_OLD_DDL)
    trade_conn.commit()
    yield trade_conn, world_conn, forecasts_conn
    trade_conn.close()
    world_conn.close()
    forecasts_conn.close()


def _seed_old_rows(trade_conn: sqlite3.Connection) -> None:
    rows = [
        ("opportunity_fact", "dec-1", "QUARANTINED_NON_CONTRIBUTING_FORECAST_EXTREMA",
         None, "2026-05-22T00:00:00Z", json.dumps({"source": "test"})),
        ("calibration_pairs", "1", "QUARANTINED_NON_CONTRIBUTING_FORECAST_EXTREMA",
         "42", "2026-05-22T00:00:01Z", json.dumps({"source": "test"})),
        ("decision_certificates", "cert-hash-1", "QUARANTINED_INVALID_LIVE_ACTIONABLE_CERTIFICATE",
         None, "2026-05-22T00:00:02Z", json.dumps({"certificate_id": "cert-1"})),
        ("decision_events", "de_pk_abc123", "QUARANTINED_NON_CONTRIBUTING_FORECAST_EXTREMA",
         "7", "2026-05-22T00:00:03Z", json.dumps({"natural_pk": {"market_slug": "m"}})),
        ("probability_trace_fact", "trace-1", "QUARANTINED_NON_CONTRIBUTING_FORECAST_EXTREMA",
         "7", "2026-05-22T00:00:04Z", "{}"),
        ("selection_family_fact", "fam-1", "QUARANTINED_NON_CONTRIBUTING_FORECAST_EXTREMA",
         "7", "2026-05-22T00:00:05Z", "{}"),
        ("selection_hypothesis_fact", "hyp-1", "QUARANTINED_NON_CONTRIBUTING_FORECAST_EXTREMA",
         "7", "2026-05-22T00:00:06Z", "{}"),
    ]
    for row in rows:
        trade_conn.execute(
            "INSERT INTO decision_integrity_quarantine "
            "(table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            row,
        )
    trade_conn.commit()


def test_dry_run_writes_nothing(three_conns):
    trade_conn, world_conn, forecasts_conn = three_conns
    _seed_old_rows(trade_conn)

    result = migration.run_migration(
        trade_conn=trade_conn, world_conn=world_conn, forecasts_conn=forecasts_conn, apply=False
    )

    assert result["status"] == "DRY_RUN_OK"
    assert result["planned_rows"] == {"trade": 1, "world": 5, "forecasts": 1}
    # Old table untouched; no new tables created.
    assert migration._table_exists(trade_conn, "decision_integrity_quarantine")
    assert not migration._table_exists(world_conn, "fact_revocations")
    assert not migration._table_exists(forecasts_conn, "fact_revocations")


def test_apply_distributes_rows_to_owning_db_and_drops_old_table(three_conns):
    trade_conn, world_conn, forecasts_conn = three_conns
    _seed_old_rows(trade_conn)

    result = migration.run_migration(
        trade_conn=trade_conn, world_conn=world_conn, forecasts_conn=forecasts_conn, apply=True
    )

    assert result["status"] == "MIGRATED"
    assert result["inserted"] == {"trade": 1, "world": 5, "forecasts": 1}

    # Old table dropped.
    assert not migration._table_exists(trade_conn, "decision_integrity_quarantine")

    # opportunity_fact landed in trade.
    trade_rows = trade_conn.execute(
        "SELECT table_name, row_id, reason_code FROM fact_revocations"
    ).fetchall()
    assert trade_rows == [("opportunity_fact", "dec-1", "REVOKED_NON_CONTRIBUTING_FORECAST_EXTREMA")]

    # world-owned tables landed in world (5 rows: decision_certificates, decision_events,
    # probability_trace_fact, selection_family_fact, selection_hypothesis_fact).
    world_count = world_conn.execute("SELECT COUNT(*) FROM fact_revocations").fetchone()[0]
    assert world_count == 5
    cert_row = world_conn.execute(
        "SELECT reason_code FROM fact_revocations WHERE table_name='decision_certificates'"
    ).fetchone()
    assert cert_row[0] == "REVOKED_INVALID_LIVE_ACTIONABLE_CERTIFICATE"

    # calibration_pairs landed in forecasts.
    forecasts_rows = forecasts_conn.execute(
        "SELECT table_name, row_id, forecast_snapshot_id FROM fact_revocations"
    ).fetchall()
    assert forecasts_rows == [("calibration_pairs", "1", "42")]


def test_idempotent_rerun_reports_already_migrated(three_conns):
    trade_conn, world_conn, forecasts_conn = three_conns
    _seed_old_rows(trade_conn)

    migration.run_migration(
        trade_conn=trade_conn, world_conn=world_conn, forecasts_conn=forecasts_conn, apply=True
    )
    result2 = migration.run_migration(
        trade_conn=trade_conn, world_conn=world_conn, forecasts_conn=forecasts_conn, apply=True
    )

    assert result2["status"] == "ALREADY_MIGRATED"
    # No duplicate rows introduced.
    world_count = world_conn.execute("SELECT COUNT(*) FROM fact_revocations").fetchone()[0]
    assert world_count == 5


def test_parity_failure_aborts_and_leaves_old_table_untouched(three_conns):
    """A pre-existing, DIFFERENT-payload row in the new table for the same key
    causes INSERT OR IGNORE to silently skip the real backfill row — parity
    must catch this (payload-hash mismatch) and abort before any DROP."""
    trade_conn, world_conn, forecasts_conn = three_conns
    _seed_old_rows(trade_conn)

    migration.ensure_table(world_conn)
    world_conn.execute(
        "INSERT INTO fact_revocations (table_name, row_id, reason_code, meta_json) "
        "VALUES ('decision_certificates', 'cert-hash-1', "
        "'REVOKED_INVALID_LIVE_ACTIONABLE_CERTIFICATE', '{\"corrupted\": true}')"
    )
    world_conn.commit()

    with pytest.raises(migration.ParityError):
        migration.run_migration(
            trade_conn=trade_conn, world_conn=world_conn, forecasts_conn=forecasts_conn, apply=True
        )

    # Old table survives; nothing was dropped.
    assert migration._table_exists(trade_conn, "decision_integrity_quarantine")
    old_count = trade_conn.execute("SELECT COUNT(*) FROM decision_integrity_quarantine").fetchone()[0]
    assert old_count == 7


def test_unmapped_table_name_raises_instead_of_dropping_rows(three_conns):
    trade_conn, world_conn, forecasts_conn = three_conns
    trade_conn.execute(
        "INSERT INTO decision_integrity_quarantine "
        "(table_name, row_id, reason_code, recorded_at, meta_json) "
        "VALUES ('some_future_table', 'row-1', 'QUARANTINED_NON_CONTRIBUTING_FORECAST_EXTREMA', "
        "'2026-05-22T00:00:00Z', '{}')"
    )
    trade_conn.commit()

    with pytest.raises(ValueError, match="unmapped table_name"):
        migration.run_migration(
            trade_conn=trade_conn, world_conn=world_conn, forecasts_conn=forecasts_conn, apply=True
        )

    assert migration._table_exists(trade_conn, "decision_integrity_quarantine")


def test_reason_multiplicity_preserved_no_collapsing(three_conns):
    """One certificate hash carrying TWO distinct reason codes must produce
    TWO rows in the new table, never collapsed into one."""
    trade_conn, world_conn, forecasts_conn = three_conns
    trade_conn.execute(
        "INSERT INTO decision_integrity_quarantine "
        "(table_name, row_id, reason_code, recorded_at, meta_json) "
        "VALUES ('decision_certificates', 'multi-hash', "
        "'QUARANTINED_INVALID_LIVE_ACTIONABLE_CERTIFICATE', '2026-05-22T00:00:00Z', '{}')"
    )
    trade_conn.execute(
        "INSERT INTO decision_integrity_quarantine "
        "(table_name, row_id, reason_code, recorded_at, meta_json) "
        "VALUES ('decision_certificates', 'multi-hash', "
        "'QUARANTINED_INVALID_LIVE_MONEY_PARENT_MODE', '2026-05-22T00:00:01Z', '{}')"
    )
    trade_conn.commit()

    result = migration.run_migration(
        trade_conn=trade_conn, world_conn=world_conn, forecasts_conn=forecasts_conn, apply=True
    )

    assert result["status"] == "MIGRATED"
    rows = world_conn.execute(
        "SELECT reason_code FROM fact_revocations WHERE row_id='multi-hash' ORDER BY reason_code"
    ).fetchall()
    assert [r[0] for r in rows] == [
        "REVOKED_INVALID_LIVE_ACTIONABLE_CERTIFICATE",
        "REVOKED_INVALID_LIVE_MONEY_PARENT_MODE",
    ]
