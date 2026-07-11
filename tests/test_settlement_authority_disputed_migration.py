# Created: 2026-07-11
# Last reused/audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md §T2b — settlements
# and observations authority tier QUARANTINED -> DISPUTED rename. Verification bar
# item 7: "Migration test proves: 92-row-class data migrates, CHECK rejects old
# literal, monotonic trigger still enforces evidence-backed DISPUTED->VERIFIED."

"""Antibody for the T2b settlements/observations authority-tier rebuild migration.

Pre-T2b schema: `authority TEXT ... CHECK (authority IN ('VERIFIED', 'UNVERIFIED',
'QUARANTINED'))` on both `settlements` and `observations` (world.db) and
`settlement_outcomes` (forecasts.db, via v2_schema.py). SQLite cannot ALTER a CHECK
constraint, so `src.state.db._migrate_authority_tier_disputed` rebuilds each table
in place: new CHECK allows 'DISPUTED' instead of 'QUARANTINED', and every existing
'QUARANTINED' row is CASE-mapped to 'DISPUTED' in the same INSERT SELECT (the two
cannot be split into separate steps — the old CHECK would reject 'DISPUTED' before
the rebuild, the new CHECK would reject 'QUARANTINED' after).

This test file pins:
1. Fresh-DB path: `init_schema()` / `apply_canonical_schema()` create the DISPUTED
   CHECK directly; no legacy literal ever appears.
2. Legacy-DB path (settlements): a pre-existing table with the old CHECK and a
   92-row-class QUARANTINED backlog is rebuilt in place; every row's authority is
   remapped to DISPUTED, VERIFIED rows are untouched, and total row count is
   preserved exactly (no data loss).
3. CHECK rejects the old literal after migration (an INSERT of 'QUARANTINED' fails).
4. The `settlements_authority_monotonic` trigger survives the rebuild and still
   enforces the evidence-backed release path: DISPUTED->VERIFIED requires a
   non-empty text `provenance_json.reactivated_by`.
5. Idempotency: re-running the migration after it has already applied is a no-op.
6. The same rebuild-and-migrate behavior holds for `observations` (world.db) and
   `settlement_outcomes` (forecasts.db, via `apply_canonical_schema`).
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.state.db import _migrate_authority_tier_disputed, init_schema
from src.state.schema.v2_schema import apply_canonical_schema


def _legacy_settlements_conn(n_verified: int = 8, n_quarantined: int = 92) -> sqlite3.Connection:
    """A pre-T2b settlements table (old CHECK) seeded with a 92-row-class backlog."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            market_slug TEXT,
            winning_bin TEXT,
            settlement_value REAL,
            settlement_source TEXT,
            settled_at TEXT,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            pm_bin_lo REAL,
            pm_bin_hi REAL,
            unit TEXT,
            settlement_source_type TEXT,
            temperature_metric TEXT
                CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT
                CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp')),
            data_version TEXT,
            provenance_json TEXT,
            UNIQUE(city, target_date, temperature_metric)
        )
        """
    )
    for i in range(n_verified):
        conn.execute(
            "INSERT INTO settlements (city, target_date, temperature_metric, authority, "
            "winning_bin, settlement_value) VALUES (?, ?, 'high', 'VERIFIED', ?, ?)",
            (f"VerifiedCity{i}", f"2026-0{(i % 6) + 1}-01", f"{i}-{i+1}", float(i)),
        )
    for i in range(n_quarantined):
        conn.execute(
            "INSERT INTO settlements (city, target_date, temperature_metric, authority, "
            "provenance_json) VALUES (?, ?, 'high', 'QUARANTINED', ?)",
            (
                f"DisputedCity{i}",
                f"2026-0{(i % 6) + 1}-02",
                json.dumps({"quarantine_reason": "harvester_live_no_obs"}),
            ),
        )
    conn.commit()
    return conn


def test_fresh_db_declares_disputed_check_directly():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='settlements' AND type='table'"
    ).fetchone()[0]
    assert "'DISPUTED'" in sql
    assert "QUARANTINED" not in sql


def test_legacy_settlements_92_row_class_migrates_and_preserves_count():
    conn = _legacy_settlements_conn(n_verified=8, n_quarantined=92)
    pre_total = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    assert pre_total == 100

    _migrate_authority_tier_disputed(conn, "settlements")

    post_total = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    assert post_total == pre_total, "migration must not lose or duplicate rows"

    verified_count = conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE authority='VERIFIED'"
    ).fetchone()[0]
    disputed_count = conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE authority='DISPUTED'"
    ).fetchone()[0]
    assert verified_count == 8
    assert disputed_count == 92

    no_longer_quarantined = conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE authority='QUARANTINED'"
    ).fetchone()[0]
    assert no_longer_quarantined == 0


def test_migrated_check_rejects_old_literal():
    conn = _legacy_settlements_conn(n_verified=1, n_quarantined=1)
    _migrate_authority_tier_disputed(conn, "settlements")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
        conn.execute(
            "INSERT INTO settlements (city, target_date, temperature_metric, authority) "
            "VALUES ('X', '2026-09-01', 'high', 'QUARANTINED')"
        )


def test_migrated_check_accepts_disputed_literal():
    conn = _legacy_settlements_conn(n_verified=1, n_quarantined=1)
    _migrate_authority_tier_disputed(conn, "settlements")
    conn.execute(
        "INSERT INTO settlements (city, target_date, temperature_metric, authority) "
        "VALUES ('X', '2026-09-01', 'high', 'DISPUTED')"
    )  # must not raise


def test_migration_is_idempotent():
    conn = _legacy_settlements_conn(n_verified=3, n_quarantined=5)
    _migrate_authority_tier_disputed(conn, "settlements")
    first_total = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]

    _migrate_authority_tier_disputed(conn, "settlements")  # second call: no-op
    second_total = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]

    assert first_total == second_total == 8


def test_monotonic_trigger_survives_rebuild_and_still_gates_reactivation():
    """After the rebuild, DISPUTED->VERIFIED still requires a non-empty
    provenance_json.reactivated_by (the trigger is reinstalled by init_schema,
    which runs the migration before the trigger DROP+CREATE block)."""
    conn = _legacy_settlements_conn(n_verified=1, n_quarantined=1)
    init_schema(conn)  # runs the T2b migration, then reinstalls the trigger

    trigger_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='settlements_authority_monotonic'"
    ).fetchone()[0]
    assert "'DISPUTED'" in trigger_sql
    assert "QUARANTINED" not in trigger_sql

    # Reactivation WITHOUT reactivated_by is rejected (S2.2's separate
    # verified-integrity trigger also requires settlement_value + winning_bin,
    # so both are supplied here to isolate the monotonic-trigger assertion).
    with pytest.raises(sqlite3.IntegrityError, match="authority transition forbidden"):
        conn.execute(
            "UPDATE settlements SET authority='VERIFIED', settlement_value=20.0, "
            "winning_bin='20-21' WHERE authority='DISPUTED'"
        )

    # Reactivation WITH a non-empty reactivated_by succeeds.
    conn.execute(
        "UPDATE settlements SET authority='VERIFIED', settlement_value=20.0, "
        "winning_bin='20-21', provenance_json=? WHERE authority='DISPUTED'",
        (json.dumps({"reactivated_by": "operator:fitz"}),),
    )
    row = conn.execute(
        "SELECT authority FROM settlements WHERE city LIKE 'DisputedCity%'"
    ).fetchone()
    assert row[0] == "VERIFIED"


def test_observations_table_migrates_alongside_settlements():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            high_temp REAL,
            low_temp REAL,
            unit TEXT NOT NULL,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            UNIQUE(city, target_date, source)
        )
        """
    )
    conn.execute(
        "INSERT INTO observations (city, target_date, source, unit, authority) "
        "VALUES ('Paris', '2026-01-01', 'wu_icao_history', 'C', 'QUARANTINED')"
    )
    conn.commit()

    _migrate_authority_tier_disputed(conn, "observations")

    row = conn.execute("SELECT authority FROM observations").fetchone()
    assert row[0] == "DISPUTED"
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
        conn.execute(
            "INSERT INTO observations (city, target_date, source, unit, authority) "
            "VALUES ('Paris', '2026-01-02', 'wu_icao_history', 'C', 'QUARANTINED')"
        )


def test_settlement_outcomes_migrates_via_apply_canonical_schema():
    """settlement_outcomes (forecasts.db, v2_schema.py) gets the same rebuild —
    this is the table scripts/drain_settlement_disputes.py operates on."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE settlement_outcomes (
            settlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
            settled_at TEXT,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            provenance_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(city, target_date, temperature_metric)
        )
        """
    )
    conn.execute(
        "INSERT INTO settlement_outcomes (city, target_date, temperature_metric, authority) "
        "VALUES ('Helsinki', '2026-02-01', 'high', 'QUARANTINED')"
    )
    conn.commit()

    apply_canonical_schema(conn)

    row = conn.execute("SELECT authority FROM settlement_outcomes").fetchone()
    assert row[0] == "DISPUTED"
