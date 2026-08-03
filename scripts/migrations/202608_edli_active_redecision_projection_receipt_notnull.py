# Lifecycle: created=2026-08-02; last_reviewed=2026-08-02; last_reused=never
# Purpose: Repair SQLite's nullable TEXT PRIMARY KEY legacy shape for the EDLI
#   active-projection seed receipt without touching projection or event truth.
# Authority: post-deploy Hotfix B boot-registry repair.
"""Atomically rebuild only the EDLI active-projection receipt with NOT NULL PK.

SQLite accepts NULL in a non-INTEGER ``TEXT PRIMARY KEY`` unless ``NOT NULL``
is declared explicitly. The preceding 202608 migration is already ledgered in
production,
so this later migration preserves its receipt rows while correcting only that
column constraint. There is intentionally no ``down``: restoring the old
nullable shape would violate the current world registry and block boot again.
"""
from __future__ import annotations

import sqlite3

TARGET_DB = "world"

_TABLE = "opportunity_event_processing_type_backfill"
_NEW_TABLE = "opportunity_event_processing_type_backfill_notnull_new"
_COLUMNS = (
    "consumer_name",
    "next_rowid",
    "completed_at",
    "seeded_active_count",
    "seed_high_water_rowid",
)
_COLUMNS_SQL = ", ".join(_COLUMNS)
_CREATE_NEW_TABLE_SQL = f"""
CREATE TABLE {_NEW_TABLE} (
    consumer_name TEXT NOT NULL PRIMARY KEY,
    next_rowid INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    seeded_active_count INTEGER,
    seed_high_water_rowid INTEGER
)
"""
_EXPECTED_BASE_COLUMNS = (
    ("consumer_name", "TEXT", 1),
    ("next_rowid", "INTEGER", 1),
    ("completed_at", "TEXT", 0),
    ("seeded_active_count", "INTEGER", 0),
    ("seed_high_water_rowid", "INTEGER", 0),
)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _receipt_table_shape(conn: sqlite3.Connection) -> str:
    rows = conn.execute(f"PRAGMA table_info({_TABLE})").fetchall()
    shape = tuple(
        (str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5]))
        for row in rows
    )
    expected_new = tuple(
        (name, type_name, notnull, 1 if index == 0 else 0)
        for index, (name, type_name, notnull) in enumerate(_EXPECTED_BASE_COLUMNS)
    )
    expected_legacy = tuple(
        (name, type_name, 0 if index == 0 else notnull, 1 if index == 0 else 0)
        for index, (name, type_name, notnull) in enumerate(_EXPECTED_BASE_COLUMNS)
    )
    if shape == expected_new:
        return "notnull"
    if shape == expected_legacy:
        return "legacy_nullable"
    raise RuntimeError(f"EDLI_BACKFILL_RECEIPT_SCHEMA_UNEXPECTED:{shape!r}")


def _assert_receipt_columns_present(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({_TABLE})")
    }
    if columns != set(_COLUMNS):
        raise RuntimeError(f"EDLI_BACKFILL_RECEIPT_SCHEMA_UNEXPECTED:{columns!r}")


def _assert_receipt_integrity(conn: sqlite3.Connection, *, table_name: str) -> int:
    total = int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
    null_count = int(
        conn.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE consumer_name IS NULL"
        ).fetchone()[0]
    )
    if null_count:
        raise RuntimeError(f"EDLI_BACKFILL_RECEIPT_NULL_CONSUMER:{null_count}")
    duplicate = conn.execute(
        f"""
        SELECT consumer_name, COUNT(*)
          FROM {table_name}
         GROUP BY consumer_name
        HAVING COUNT(*) > 1
         LIMIT 1
        """
    ).fetchone()
    if duplicate is not None:
        raise RuntimeError(
            f"EDLI_BACKFILL_RECEIPT_DUPLICATE_CONSUMER:{duplicate[0]}:{duplicate[1]}"
        )
    return total


def _copy_receipt_rows(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        INSERT INTO {_NEW_TABLE} ({_COLUMNS_SQL})
        SELECT {_COLUMNS_SQL} FROM {_TABLE}
        """
    )


def up(conn: sqlite3.Connection) -> None:
    """Make the receipt PK explicit without changing any receipt value."""
    if not _table_exists(conn, _TABLE):
        raise RuntimeError(f"EDLI_BACKFILL_RECEIPT_TABLE_MISSING:{_TABLE}")
    if _table_exists(conn, _NEW_TABLE):
        raise RuntimeError(f"EDLI_BACKFILL_RECEIPT_REBUILD_RESIDUE:{_NEW_TABLE}")
    _assert_receipt_columns_present(conn)
    _assert_receipt_integrity(conn, table_name=_TABLE)
    if _receipt_table_shape(conn) == "notnull":
        return

    conn.execute("SAVEPOINT edli_backfill_receipt_notnull")
    try:
        source_count = _assert_receipt_integrity(conn, table_name=_TABLE)
        conn.execute(_CREATE_NEW_TABLE_SQL)
        _copy_receipt_rows(conn)
        copied_count = _assert_receipt_integrity(conn, table_name=_NEW_TABLE)
        if copied_count != source_count:
            raise RuntimeError(
                f"EDLI_BACKFILL_RECEIPT_COPY_COUNT_MISMATCH:{copied_count}!={source_count}"
            )
        conn.execute(f"DROP TABLE {_TABLE}")
        conn.execute(f"ALTER TABLE {_NEW_TABLE} RENAME TO {_TABLE}")
        if _receipt_table_shape(conn) != "notnull":
            raise RuntimeError("EDLI_BACKFILL_RECEIPT_NOTNULL_REBUILD_FAILED")
        if _assert_receipt_integrity(conn, table_name=_TABLE) != source_count:
            raise RuntimeError("EDLI_BACKFILL_RECEIPT_POST_SWAP_COUNT_MISMATCH")
        conn.execute("RELEASE SAVEPOINT edli_backfill_receipt_notnull")
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT edli_backfill_receipt_notnull")
        conn.execute("RELEASE SAVEPOINT edli_backfill_receipt_notnull")
        raise
