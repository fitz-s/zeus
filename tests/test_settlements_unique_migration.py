# Created: 2026-04-24
# Last reused/audited: 2026-04-28
# Authority basis: REOPEN-2 data-readiness-tail UNIQUE migration
# (docs/operations/task_2026-04-23_midstream_remediation/); closure of
# forensic-audit C3+C4 — settlements UNIQUE(city, target_date) blocks
# dual-track. Migration rebuilds to UNIQUE(city, target_date,
# temperature_metric). Applied to live DB 2026-04-24.

"""Antibody for REOPEN-2 settlements UNIQUE migration.

Pre-REOPEN-2 schema: `UNIQUE(city, target_date)` — a HIGH row for any
(city, target_date) structurally blocks inserting a LOW row for the
same (city, target_date). This breaks dual-track at the schema level
and is a pre-flip BLOCKER for DR-33-C (harvester flag flip).

Post-REOPEN-2 schema: `UNIQUE(city, target_date, temperature_metric)`
— dual-track inserts succeed; old same-metric collisions still fail
correctly.

This test file pins:
1. Fresh-DB path: `init_schema()` creates settlements with new UNIQUE.
2. Legacy-DB path: a pre-existing settlements table with old UNIQUE
   gets rebuilt in-place by `init_schema()`; data is preserved.
3. Idempotency: re-running `init_schema()` after migration is a no-op.
4. Dual-track insert: HIGH + LOW rows for same (city, target_date)
   both commit.
5. Same-metric collision still fires: inserting the same
   (city, target_date, metric) twice hits UNIQUE rejection.
6. Triggers survive the rebuild: all three settlements_* triggers
   are present after migration.
7. NULL-metric scaffold inserts are rejected before they can bypass
   SQLite UNIQUE semantics.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from src.state.db import init_schema

_HARVESTER_LIVE_SETTLEMENT_COLUMNS = {
    "pm_bin_lo",
    "pm_bin_hi",
    "unit",
    "settlement_source_type",
}


def _fresh() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


def _settlement_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(settlements)")}


def _seed_legacy_pre_reopen2_schema(conn: sqlite3.Connection) -> None:
    """Build a settlements table as it existed pre-REOPEN-2 (with old
    UNIQUE(city, target_date) constraint + no INV-14 columns yet)."""
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
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
            UNIQUE(city, target_date)
        )
        """
    )


def _seed_legacy_post_reopen2_without_harvester_live_columns(
    conn: sqlite3.Connection,
) -> None:
    """Build a post-REOPEN-2 settlements table that predates DR-33 harvester
    bin/source-family columns.

    This shape already has UNIQUE(city, target_date, temperature_metric), so
    the table-rebuild migration must not be required for the four nullable
    harvester-live columns. The generic ALTER loop must add them.
    """
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


def _insert_verified_row(
    conn: sqlite3.Connection, *, city: str, target_date: str, metric: str = "high"
) -> None:
    conn.execute(
        """
        INSERT INTO settlements (
            city, target_date, winning_bin, settlement_value,
            settlement_source, settled_at, authority, provenance_json,
            temperature_metric, physical_quantity, observation_field, data_version
        ) VALUES (?, ?, '15-16', 15.0, 'test', '2026-04-23T00:00:00',
                  'VERIFIED', '{}', ?,
                  'daily_maximum_air_temperature', 'high_temp',
                  'wu_icao_history_v1')
        """,
        (city, target_date, metric),
    )


# ---------------------------------------------------------------------------
# Fresh-DB path
# ---------------------------------------------------------------------------


def test_fresh_db_has_new_unique_constraint():
    conn = _fresh()
    try:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='settlements' AND type='table'"
        ).fetchone()[0]
        assert "UNIQUE(city, target_date, temperature_metric)" in sql
    finally:
        conn.close()


def test_fresh_db_has_harvester_live_schema_parity_columns():
    """Fresh schema must accept harvester live settlement writes.

    `src.execution.harvester._write_settlement_truth()` inserts
    pm_bin_lo/pm_bin_hi/unit/settlement_source_type. Fresh DBs must carry the
    same nullable columns as legacy live DBs and rebuilt REOPEN-2 DBs.
    """
    conn = _fresh()
    try:
        assert _HARVESTER_LIVE_SETTLEMENT_COLUMNS <= _settlement_columns(conn)
    finally:
        conn.close()


def test_fresh_db_dual_track_insert_works():
    conn = _fresh()
    try:
        _insert_verified_row(conn, city="paris", target_date="2026-04-23", metric="high")
        _insert_verified_row(conn, city="paris", target_date="2026-04-23", metric="low")
        rows = conn.execute(
            "SELECT temperature_metric FROM settlements WHERE city='paris' ORDER BY temperature_metric"
        ).fetchall()
        assert rows == [("high",), ("low",)]
    finally:
        conn.close()


def test_fresh_db_same_metric_collision_still_rejected():
    """Two HIGH rows for same (city, target_date) must still UNIQUE-fail."""
    conn = _fresh()
    try:
        _insert_verified_row(conn, city="paris", target_date="2026-04-23", metric="high")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_verified_row(conn, city="paris", target_date="2026-04-23", metric="high")
    finally:
        conn.close()


def test_fresh_db_null_metric_insert_rejected():
    """The post-audit trigger closes SQLite's NULL-NULL UNIQUE hole."""
    conn = _fresh()
    try:
        with pytest.raises(
            sqlite3.IntegrityError, match="temperature_metric must be non-null"
        ):
            conn.execute(
                """
                INSERT INTO settlements (city, target_date, authority)
                VALUES ('paris', '2026-04-23', 'UNVERIFIED')
                """
            )
    finally:
        conn.close()


def test_onboard_cities_no_longer_writes_partial_settlement_scaffolds():
    """City onboarding must not create provenance-empty settlement placeholders."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "onboard_cities.py"
    text = script.read_text()
    assert "INSERT OR IGNORE INTO settlements" not in text
    assert "INSERT INTO settlements" not in text


# ---------------------------------------------------------------------------
# Legacy-DB migration path
# ---------------------------------------------------------------------------


def test_legacy_db_gets_migrated_to_new_unique():
    """A DB seeded with the old schema + some legacy rows gets rebuilt in
    place by init_schema; data is preserved + new UNIQUE is in place."""
    conn = sqlite3.connect(":memory:")
    try:
        _seed_legacy_pre_reopen2_schema(conn)
        # Seed 3 legacy rows (mix of authorities)
        conn.execute(
            "INSERT INTO settlements (city, target_date, authority, winning_bin, settlement_value) "
            "VALUES ('london', '2026-04-20', 'VERIFIED', '15-16', 15.0)"
        )
        conn.execute(
            "INSERT INTO settlements (city, target_date, authority, winning_bin, settlement_value) "
            "VALUES ('berlin', '2026-04-21', 'VERIFIED', '20-21', 20.0)"
        )
        conn.execute(
            "INSERT INTO settlements (city, target_date, authority) "
            "VALUES ('nyc', '2026-04-22', 'QUARANTINED')"
        )
        conn.commit()
        pre_count = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
        assert pre_count == 3

        init_schema(conn)

        post_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='settlements' AND type='table'"
        ).fetchone()[0]
        assert "UNIQUE(city, target_date, temperature_metric)" in post_sql
        post_count = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
        assert post_count == 3, f"row count drift: pre=3 post={post_count}"
        # Legacy cities preserved
        cities = sorted(r[0] for r in conn.execute("SELECT city FROM settlements"))
        assert cities == ["berlin", "london", "nyc"]
    finally:
        conn.close()


def test_legacy_db_migration_idempotent():
    """Running init_schema twice after the migration must not re-migrate
    or raise."""
    conn = sqlite3.connect(":memory:")
    try:
        _seed_legacy_pre_reopen2_schema(conn)
        conn.execute(
            "INSERT INTO settlements (city, target_date, authority, winning_bin, settlement_value) "
            "VALUES ('london', '2026-04-20', 'VERIFIED', '15-16', 15.0)"
        )
        conn.commit()

        init_schema(conn)
        count_after_first = conn.execute(
            "SELECT COUNT(*) FROM settlements"
        ).fetchone()[0]

        # Second call — no-op
        init_schema(conn)
        count_after_second = conn.execute(
            "SELECT COUNT(*) FROM settlements"
        ).fetchone()[0]
        assert count_after_first == count_after_second == 1
    finally:
        conn.close()


def test_legacy_new_unique_schema_gets_harvester_live_columns_via_alter():
    """Legacy DBs that already have the new UNIQUE still need nullable
    harvester-live columns.

    This pins the non-rebuild ALTER path: a DB can have the REOPEN-2
    UNIQUE(city,target_date,temperature_metric) shape while still lacking
    pm_bin_lo/pm_bin_hi/unit/settlement_source_type.
    """
    conn = sqlite3.connect(":memory:")
    try:
        _seed_legacy_post_reopen2_without_harvester_live_columns(conn)
        conn.execute(
            """
            INSERT INTO settlements (
                city, target_date, authority, temperature_metric,
                physical_quantity, observation_field, data_version,
                provenance_json
            )
            VALUES (
                'london', '2026-04-20', 'VERIFIED', 'high',
                'mx2t6_local_calendar_day_max', 'high_temp',
                'wu_icao_history_v1', '{}'
            )
            """
        )
        conn.commit()
        pre_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='settlements' AND type='table'"
        ).fetchone()[0]
        assert "UNIQUE(city, target_date, temperature_metric)" in pre_sql
        assert not (_HARVESTER_LIVE_SETTLEMENT_COLUMNS <= _settlement_columns(conn))

        init_schema(conn)

        post_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='settlements' AND type='table'"
        ).fetchone()[0]
        assert "UNIQUE(city, target_date, temperature_metric)" in post_sql
        assert _HARVESTER_LIVE_SETTLEMENT_COLUMNS <= _settlement_columns(conn)
        post_count = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
        assert post_count == 1
    finally:
        conn.close()


def test_triggers_survive_table_rebuild():
    """After the REOPEN-2 table-rebuild, all three settlements_* triggers
    must be re-installed (the rebuild drops the old table + its triggers;
    init_schema's trigger reinstall blocks MUST run AFTER the migration)."""
    conn = sqlite3.connect(":memory:")
    try:
        _seed_legacy_pre_reopen2_schema(conn)
        init_schema(conn)
        trigs = sorted(
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='settlements'"
            )
        )
        assert "settlements_authority_monotonic" in trigs
        assert "settlements_verified_insert_integrity" in trigs
        assert "settlements_verified_update_integrity" in trigs
    finally:
        conn.close()


def test_migration_preserves_authority_groups():
    """Pre/post row counts per authority must match."""
    conn = sqlite3.connect(":memory:")
    try:
        _seed_legacy_pre_reopen2_schema(conn)
        # Seed 2 VERIFIED + 1 QUARANTINED
        conn.execute("INSERT INTO settlements (city, target_date, authority) VALUES ('a', '2026-01-01', 'VERIFIED')")
        conn.execute("INSERT INTO settlements (city, target_date, authority) VALUES ('b', '2026-01-02', 'VERIFIED')")
        conn.execute("INSERT INTO settlements (city, target_date, authority) VALUES ('c', '2026-01-03', 'QUARANTINED')")
        conn.commit()
        pre_groups = dict(conn.execute("SELECT authority, COUNT(*) FROM settlements GROUP BY authority").fetchall())

        init_schema(conn)

        post_groups = dict(conn.execute("SELECT authority, COUNT(*) FROM settlements GROUP BY authority").fetchall())
        assert pre_groups == post_groups
    finally:
        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
