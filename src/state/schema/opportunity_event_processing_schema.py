"""EDLI opportunity_event_processing schema owner."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS opportunity_event_processing (
    consumer_name TEXT NOT NULL,
    event_id TEXT NOT NULL,
    processing_status TEXT NOT NULL CHECK (processing_status IN (
        'pending','processing','processed','failed','dead_letter','expired','ignored'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    processed_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (consumer_name, event_id)
)
"""

CREATE_STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_opportunity_event_processing_status
    ON opportunity_event_processing(consumer_name, processing_status, updated_at)
"""

CREATE_PENDING_RETRY_FLOOR_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_opportunity_event_processing_pending_retry_floor
    ON opportunity_event_processing(consumer_name, processing_status, claimed_at, updated_at, event_id)
"""

CREATE_STALE_CLAIM_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_opportunity_event_processing_stale_claim
    ON opportunity_event_processing(consumer_name, processing_status, claimed_at, event_id)
    WHERE claimed_at IS NOT NULL
"""


# A compact active-only projection of the immutable event type onto mutable
# processing state. The immutable opportunity_events log remains the type
# authority; this table exists solely so recurring control-plane reads do not
# have to intersect append-only event history with the 2M+ row processing
# table. The guards and maintenance triggers below keep it transactionally identical to active
# processing rows written after installation.
ACTIVE_PROJECTION_SEED_MAX_ROWS = 10_000


class ActiveProjectionSeedError(RuntimeError):
    """The typed projection cannot safely become an authoritative reader."""


CREATE_EVENT_TYPE_PROJECTION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS opportunity_event_processing_type_projection (
    consumer_name TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    processing_status TEXT NOT NULL CHECK (processing_status IN ('pending', 'processing')),
    claimed_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (consumer_name, event_id)
)
"""

CREATE_EVENT_TYPE_PENDING_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_event_processing_type_pending
    ON opportunity_event_processing_type_projection(
        consumer_name, event_type, processing_status, updated_at, event_id
    )
    WHERE processing_status = 'pending'
"""

CREATE_EVENT_TYPE_STALE_CLAIM_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_event_processing_type_stale_claim
    ON opportunity_event_processing_type_projection(
        consumer_name, event_type, processing_status, claimed_at, event_id
    )
    WHERE processing_status = 'processing' AND claimed_at IS NOT NULL
"""

# A completed receipt makes the full, bounded active-set seed explicit. Its
# legacy ``next_rowid`` field is retained for additive compatibility only; it
# is not an authority cursor and readers never drain historical rowids.
CREATE_EVENT_TYPE_BACKFILL_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS opportunity_event_processing_type_backfill (
    consumer_name TEXT NOT NULL PRIMARY KEY,
    next_rowid INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    seeded_active_count INTEGER,
    seed_high_water_rowid INTEGER
)
"""

CREATE_EVENT_TYPE_PROJECTION_INSERT_GUARD_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_event_processing_type_projection_insert_guard
BEFORE INSERT ON opportunity_event_processing
WHEN NEW.consumer_name = 'edli_reactor_v1'
 AND NEW.processing_status IN ('pending', 'processing')
 AND NOT EXISTS (SELECT 1 FROM opportunity_events WHERE event_id = NEW.event_id)
BEGIN
    SELECT RAISE(ABORT, 'ACTIVE_PROCESSING_REQUIRES_APPEND_ONLY_EVENT');
END
"""

CREATE_EVENT_TYPE_PROJECTION_UPDATE_GUARD_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_event_processing_type_projection_update_guard
BEFORE UPDATE OF processing_status, claimed_at, updated_at ON opportunity_event_processing
WHEN NEW.consumer_name = 'edli_reactor_v1'
 AND NEW.processing_status IN ('pending', 'processing')
 AND NOT EXISTS (SELECT 1 FROM opportunity_events WHERE event_id = NEW.event_id)
BEGIN
    SELECT RAISE(ABORT, 'ACTIVE_PROCESSING_REQUIRES_APPEND_ONLY_EVENT');
END
"""

CREATE_EVENT_TYPE_PROJECTION_INSERT_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_event_processing_type_projection_insert
AFTER INSERT ON opportunity_event_processing
WHEN NEW.processing_status IN ('pending', 'processing')
BEGIN
    INSERT OR REPLACE INTO opportunity_event_processing_type_projection (
        consumer_name, event_id, event_type, processing_status, claimed_at, updated_at
    )
    SELECT NEW.consumer_name, NEW.event_id, e.event_type,
           NEW.processing_status, NEW.claimed_at, NEW.updated_at
      FROM opportunity_events e
     WHERE e.event_id = NEW.event_id;
END
"""

CREATE_EVENT_TYPE_PROJECTION_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_event_processing_type_projection_update
AFTER UPDATE OF processing_status, claimed_at, updated_at ON opportunity_event_processing
BEGIN
    DELETE FROM opportunity_event_processing_type_projection
     WHERE consumer_name = NEW.consumer_name
       AND event_id = NEW.event_id;
    INSERT OR REPLACE INTO opportunity_event_processing_type_projection (
        consumer_name, event_id, event_type, processing_status, claimed_at, updated_at
    )
    SELECT NEW.consumer_name, NEW.event_id, e.event_type,
           NEW.processing_status, NEW.claimed_at, NEW.updated_at
      FROM opportunity_events e
     WHERE e.event_id = NEW.event_id
       AND NEW.processing_status IN ('pending', 'processing');
END
"""


def _ensure_receipt_columns(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_info(opportunity_event_processing_type_backfill)"
        )
    }
    for column in ("seeded_active_count", "seed_high_water_rowid"):
        if column not in columns:
            conn.execute(
                "ALTER TABLE opportunity_event_processing_type_backfill "
                f"ADD COLUMN {column} INTEGER"
            )


def _active_processing_count(conn: sqlite3.Connection, *, consumer_name: str) -> int:
    """Count only the two active states as exact status-index seeks."""
    return sum(
        int(
            conn.execute(
                """
                SELECT COUNT(*)
                  FROM opportunity_event_processing
                       INDEXED BY idx_opportunity_event_processing_status
                 WHERE consumer_name = ? AND processing_status = ?
                """,
                (consumer_name, status),
            ).fetchone()[0]
        )
        for status in ("pending", "processing")
    )


def _active_processing_high_water(
    conn: sqlite3.Connection, *, consumer_name: str
) -> int:
    """Read active rowid extrema without widening to terminal history."""
    return max(
        int(
            conn.execute(
                """
                SELECT COALESCE(MAX(rowid), 0)
                  FROM opportunity_event_processing
                       INDEXED BY idx_opportunity_event_processing_status
                 WHERE consumer_name = ? AND processing_status = ?
                """,
                (consumer_name, status),
            ).fetchone()[0]
        )
        for status in ("pending", "processing")
    )


def assert_active_projection_ready(
    conn: sqlite3.Connection,
    *,
    consumer_name: str,
    max_active_rows: int = ACTIVE_PROJECTION_SEED_MAX_ROWS,
) -> tuple[int, int]:
    """Return the verified seed receipt or raise before typed authority reads."""
    row = conn.execute(
        """
        SELECT seeded_active_count, seed_high_water_rowid
          FROM opportunity_event_processing_type_backfill
         WHERE consumer_name = ?
           AND completed_at IS NOT NULL
           AND seeded_active_count IS NOT NULL
           AND seed_high_water_rowid IS NOT NULL
        """,
        (consumer_name,),
    ).fetchone()
    if row is None:
        raise ActiveProjectionSeedError(
            f"ACTIVE_PROJECTION_UNSEEDED:{consumer_name}"
        )
    active_count = _active_processing_count(conn, consumer_name=consumer_name)
    limit = max(1, int(max_active_rows))
    if active_count > limit:
        raise ActiveProjectionSeedError(
            f"ACTIVE_PROJECTION_ACTIVE_LIMIT_EXCEEDED:{consumer_name}:{active_count}>{limit}"
        )
    projected_pending = int(
        conn.execute(
            """
            SELECT COUNT(*)
              FROM opportunity_event_processing_type_projection
                   INDEXED BY idx_event_processing_type_pending
             WHERE consumer_name = ? AND processing_status = 'pending'
            """,
            (consumer_name,),
        ).fetchone()[0]
    )
    projected_processing = int(
        conn.execute(
            """
            SELECT COUNT(*)
              FROM opportunity_event_processing_type_projection
                   INDEXED BY idx_event_processing_type_stale_claim
             WHERE consumer_name = ?
               AND processing_status = 'processing'
               AND claimed_at IS NOT NULL
            """,
            (consumer_name,),
        ).fetchone()[0]
    )
    projected_count = projected_pending + projected_processing
    if active_count != projected_count:
        raise ActiveProjectionSeedError(
            f"ACTIVE_PROJECTION_COUNT_MISMATCH:{consumer_name}:{projected_count}!={active_count}"
        )
    return int(row[0]), int(row[1])


def projection_seed_is_ready(conn: sqlite3.Connection, *, consumer_name: str) -> bool:
    try:
        assert_active_projection_ready(conn, consumer_name=consumer_name)
    except ActiveProjectionSeedError:
        return False
    return True


def seed_active_event_type_projection(
    conn: sqlite3.Connection,
    *,
    consumer_name: str,
    seeded_at: str | None = None,
    max_active_rows: int = ACTIVE_PROJECTION_SEED_MAX_ROWS,
) -> tuple[int, int]:
    """Seed the full active set before the projection can become authoritative.

    SCOPE: pending/processing rows for exactly ``consumer_name``. DRAIN: none;
    all active debt is seeded at once from the status index. RESET: terminal
    updates remove projection rows and later re-open updates reinsert them.
    A too-large set or active orphan fails closed rather than exposing a partial
    projection as complete truth.
    """
    limit = max(1, int(max_active_rows))
    now = seeded_at or datetime.now(timezone.utc).isoformat()
    try:
        return assert_active_projection_ready(conn, consumer_name=consumer_name)
    except ActiveProjectionSeedError as exc:
        if not str(exc).startswith("ACTIVE_PROJECTION_UNSEEDED:"):
            raise
    active_count = _active_processing_count(conn, consumer_name=consumer_name)
    if active_count > limit:
        raise ActiveProjectionSeedError(
            f"ACTIVE_PROJECTION_SEED_LIMIT_EXCEEDED:{consumer_name}:{active_count}>{limit}"
        )
    orphan = None
    for status in ("pending", "processing"):
        orphan = conn.execute(
            """
            SELECT p.event_id
              FROM opportunity_event_processing AS p
                   INDEXED BY idx_opportunity_event_processing_status
              LEFT JOIN opportunity_events AS e
                   INDEXED BY sqlite_autoindex_opportunity_events_1
                ON e.event_id = p.event_id
             WHERE p.consumer_name = ?
               AND p.processing_status = ?
               AND e.event_id IS NULL
             LIMIT 1
            """,
            (consumer_name, status),
        ).fetchone()
        if orphan is not None:
            break
    if orphan is not None:
        raise ActiveProjectionSeedError(
            f"ACTIVE_PROCESSING_MISSING_APPEND_ONLY_EVENT:{consumer_name}:{orphan[0]}"
        )
    conn.execute(
        "DELETE FROM opportunity_event_processing_type_projection WHERE consumer_name = ?",
        (consumer_name,),
    )
    for status in ("pending", "processing"):
        conn.execute(
            """
            INSERT INTO opportunity_event_processing_type_projection (
                consumer_name, event_id, event_type, processing_status, claimed_at, updated_at
            )
            SELECT p.consumer_name, p.event_id, e.event_type,
                   p.processing_status, p.claimed_at, p.updated_at
              FROM opportunity_event_processing AS p
                   INDEXED BY idx_opportunity_event_processing_status
              JOIN opportunity_events AS e
                   INDEXED BY sqlite_autoindex_opportunity_events_1
                ON e.event_id = p.event_id
             WHERE p.consumer_name = ? AND p.processing_status = ?
            """,
            (consumer_name, status),
        )
    projected_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM opportunity_event_processing_type_projection WHERE consumer_name = ?",
            (consumer_name,),
        ).fetchone()[0]
    )
    if projected_count != active_count:
        raise ActiveProjectionSeedError(
            f"ACTIVE_PROJECTION_SEED_COUNT_MISMATCH:{consumer_name}:{projected_count}!={active_count}"
        )
    high_water = _active_processing_high_water(conn, consumer_name=consumer_name)
    conn.execute(
        """
        INSERT INTO opportunity_event_processing_type_backfill (
            consumer_name, next_rowid, completed_at,
            seeded_active_count, seed_high_water_rowid
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(consumer_name) DO UPDATE SET
            next_rowid = excluded.next_rowid,
            completed_at = excluded.completed_at,
            seeded_active_count = excluded.seeded_active_count,
            seed_high_water_rowid = excluded.seed_high_water_rowid
        """,
        (consumer_name, high_water, now, active_count, high_water),
    )
    return active_count, high_water


def install_active_event_type_projection(
    conn: sqlite3.Connection,
    *,
    consumer_name: str,
    seeded_at: str | None = None,
    max_active_rows: int = ACTIVE_PROJECTION_SEED_MAX_ROWS,
) -> tuple[int, int]:
    """Atomically install DDL and complete the authoritative active seed."""
    conn.execute("SAVEPOINT edli_active_projection_install")
    try:
        ensure_table(conn)
        result = seed_active_event_type_projection(
            conn,
            consumer_name=consumer_name,
            seeded_at=seeded_at,
            max_active_rows=max_active_rows,
        )
        conn.execute("RELEASE SAVEPOINT edli_active_projection_install")
        return result
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT edli_active_projection_install")
        conn.execute("RELEASE SAVEPOINT edli_active_projection_install")
        raise


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_STATUS_INDEX_SQL)
    conn.execute(CREATE_PENDING_RETRY_FLOOR_INDEX_SQL)
    conn.execute(CREATE_STALE_CLAIM_INDEX_SQL)
    conn.execute(CREATE_EVENT_TYPE_PROJECTION_TABLE_SQL)
    conn.execute(CREATE_EVENT_TYPE_PENDING_INDEX_SQL)
    conn.execute(CREATE_EVENT_TYPE_STALE_CLAIM_INDEX_SQL)
    conn.execute(CREATE_EVENT_TYPE_BACKFILL_TABLE_SQL)
    _ensure_receipt_columns(conn)
    conn.execute(CREATE_EVENT_TYPE_PROJECTION_INSERT_GUARD_TRIGGER_SQL)
    conn.execute(CREATE_EVENT_TYPE_PROJECTION_UPDATE_GUARD_TRIGGER_SQL)
    conn.execute(CREATE_EVENT_TYPE_PROJECTION_INSERT_TRIGGER_SQL)
    conn.execute(CREATE_EVENT_TYPE_PROJECTION_UPDATE_TRIGGER_SQL)
