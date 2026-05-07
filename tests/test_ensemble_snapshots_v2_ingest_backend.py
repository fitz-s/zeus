# Created: 2026-05-07
# Last reused/audited: 2026-05-07
# Authority basis: docs/operations/TIGGE_DOWNLOAD_SPEC_v3_2026_05_07.md §3 Phase 0 #5
#                  + critic v2 reject for spec v3 (A1 BLOCKER).
"""Test ``ingest_backend`` schema migration + writer wiring.

Covers
------
- Migration is idempotent (running twice does not raise / does not double-add).
- Fresh DBs created via ``apply_v2_schema`` already have ``ingest_backend``.
- Pre-2026-05-07 historical rows (legacy, no ingest_backend value supplied)
  default to ``'unknown'``.
- New writes that pass an explicit value (``'ecds'`` / ``'webapi'``) tag
  the row correctly.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.migrate_ensemble_snapshots_v2_add_ingest_backend import (  # noqa: E402
    COLUMN_NAME,
    TABLE_NAME,
    migrate,
)
from src.state.schema.v2_schema import apply_v2_schema  # noqa: E402


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def test_fresh_v2_schema_has_ingest_backend() -> None:
    """Fresh DBs built from canonical v2_schema.py carry the column."""
    conn = sqlite3.connect(":memory:")
    apply_v2_schema(conn)
    assert COLUMN_NAME in _column_names(conn, TABLE_NAME)
    conn.close()


def test_migration_idempotent_on_fresh_db() -> None:
    """Running migration twice on a fresh DB is a no-op the second time."""
    conn = sqlite3.connect(":memory:")
    apply_v2_schema(conn)
    # Fresh schema already has the column → migration is a no-op.
    result1 = migrate(conn, dry_run=False)
    assert result1["applied"] is False
    assert result1["reason"] == "column_already_present"

    # Second invocation also a no-op.
    result2 = migrate(conn, dry_run=False)
    assert result2["applied"] is False
    assert result2["reason"] == "column_already_present"
    conn.close()


def _build_pre_migration_db() -> sqlite3.Connection:
    """Construct a DB that LOOKS like pre-2026-05-07 (no ingest_backend column)."""
    conn = sqlite3.connect(":memory:")
    # Minimal pre-migration schema mirroring the canonical CREATE block but
    # OMITTING ingest_backend. Sufficient for migration test (does not need
    # to mirror every column the real schema has).
    conn.execute(
        """
        CREATE TABLE ensemble_snapshots_v2 (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            data_version TEXT NOT NULL,
            issue_time TEXT,
            members_json TEXT NOT NULL DEFAULT '[]',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO ensemble_snapshots_v2 "
        "(city, target_date, temperature_metric, data_version, issue_time) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Tokyo", "2026-04-01", "high", "tigge_mx2t6_local_calendar_day_max_v1",
         "2026-04-01T00:00:00+00:00"),
    )
    conn.commit()
    return conn


def test_migration_adds_column_legacy_rows_unknown() -> None:
    """ALTER applies on a pre-migration DB and old rows default to 'unknown'."""
    conn = _build_pre_migration_db()
    assert COLUMN_NAME not in _column_names(conn, TABLE_NAME)

    result = migrate(conn, dry_run=False)
    assert result["applied"] is True
    assert COLUMN_NAME in _column_names(conn, TABLE_NAME)

    # Pre-existing row carries the default.
    rows = conn.execute(
        f"SELECT {COLUMN_NAME} FROM {TABLE_NAME}"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "unknown"
    conn.close()


def test_migration_idempotent_after_alter() -> None:
    """Second migration call on an already-migrated DB is a no-op."""
    conn = _build_pre_migration_db()
    first = migrate(conn, dry_run=False)
    assert first["applied"] is True

    second = migrate(conn, dry_run=False)
    assert second["applied"] is False
    assert second["reason"] == "column_already_present"
    conn.close()


def test_dry_run_does_not_modify_db() -> None:
    """--dry-run reports the SQL but does not apply it."""
    conn = _build_pre_migration_db()
    assert COLUMN_NAME not in _column_names(conn, TABLE_NAME)

    result = migrate(conn, dry_run=True)
    assert result["applied"] is False
    assert result["reason"] == "dry_run"
    assert COLUMN_NAME not in _column_names(conn, TABLE_NAME)
    conn.close()


def test_explicit_ingest_backend_values_round_trip() -> None:
    """A row inserted with ``ingest_backend='ecds'`` reads back ``'ecds'``."""
    conn = sqlite3.connect(":memory:")
    apply_v2_schema(conn)

    # Insert two rows, one ecds, one webapi.
    conn.execute(
        f"""
        INSERT INTO {TABLE_NAME}
            (city, target_date, temperature_metric, physical_quantity,
             observation_field, available_at, fetch_time, lead_hours,
             members_json, model_version, data_version, ingest_backend)
        VALUES (?, ?, 'high', 'temp_max', 'high_temp', ?, ?, 24.0, '[]',
                'ecmwf_ens', 'tigge_mx2t6_local_calendar_day_max_v1', ?)
        """,
        ("Tokyo", "2026-05-08",
         "2026-05-07T00:00:00+00:00", "2026-05-07T00:01:00+00:00", "ecds"),
    )
    conn.execute(
        f"""
        INSERT INTO {TABLE_NAME}
            (city, target_date, temperature_metric, physical_quantity,
             observation_field, available_at, fetch_time, lead_hours,
             members_json, model_version, data_version, ingest_backend)
        VALUES (?, ?, 'high', 'temp_max', 'high_temp', ?, ?, 24.0, '[]',
                'ecmwf_ens', 'tigge_mx2t6_local_calendar_day_max_v1', ?)
        """,
        ("Tokyo", "2026-05-09",
         "2026-05-07T00:00:00+00:00", "2026-05-07T00:01:00+00:00", "webapi"),
    )
    conn.commit()
    rows = conn.execute(
        f"SELECT target_date, ingest_backend FROM {TABLE_NAME} ORDER BY target_date"
    ).fetchall()
    assert rows == [
        ("2026-05-08", "ecds"),
        ("2026-05-09", "webapi"),
    ]
    conn.close()


def test_default_ingest_backend_when_omitted() -> None:
    """A row inserted without specifying ingest_backend defaults to 'unknown'."""
    conn = sqlite3.connect(":memory:")
    apply_v2_schema(conn)
    conn.execute(
        f"""
        INSERT INTO {TABLE_NAME}
            (city, target_date, temperature_metric, physical_quantity,
             observation_field, available_at, fetch_time, lead_hours,
             members_json, model_version, data_version)
        VALUES (?, ?, 'high', 'temp_max', 'high_temp', ?, ?, 24.0, '[]',
                'ecmwf_ens', 'tigge_mx2t6_local_calendar_day_max_v1')
        """,
        ("Tokyo", "2026-05-10",
         "2026-05-07T00:00:00+00:00", "2026-05-07T00:01:00+00:00"),
    )
    conn.commit()
    row = conn.execute(
        f"SELECT ingest_backend FROM {TABLE_NAME}"
    ).fetchone()
    assert row[0] == "unknown"
    conn.close()
