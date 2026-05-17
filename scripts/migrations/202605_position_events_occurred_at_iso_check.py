# Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=never
# Purpose: Add CHECK (occurred_at LIKE '____-__-__T%' OR occurred_at = 'QUARANTINE')
#   to position_events.occurred_at, and backfill 3 sentinel rows whose
#   occurred_at='unknown_entered_at' with the occurred_at of the immediately
#   following ENTRY_ORDER_FILLED event for the same position.
#
# Background: position_events has append-only triggers (no UPDATE/DELETE).
#   SQLite cannot ADD CHECK in-place. Pattern: CREATE new table with CHECK,
#   INSERT ... SELECT with CASE substitution for sentinels, DROP old, RENAME.
#   All triggers, UNIQUE constraints, and CHECK constraints are recreated on the
#   new table.
#
# Timestamp source: chain_verified_at does not exist in position_current.
#   recorded_at does not exist in position_events.  The CHAIN_SYNCED sentinel
#   rows carry reason='pending_fill_rescued', meaning the chain sync happened
#   AT the fill-rescue pass.  The best available proxy is the occurred_at of
#   the ENTRY_ORDER_FILLED event that follows CHAIN_SYNCED in sequence order.
#   This was verified against live data 2026-05-17:
#     c30f28a5-d4e → ENTRY_ORDER_FILLED 2026-05-16T06:40:21.097343+00:00
#     bf0a16f5-f95 → ENTRY_ORDER_FILLED 2026-05-17T10:11:52.337500+00:00
#     6d8abfb4-b87 → ENTRY_ORDER_FILLED 2026-05-17T12:41:38.355482+00:00
#   Karachi position c30f28a5-d4e: DAY0_WINDOW_ENTERED at 19:00 is AFTER
#   the fill-time substitute (06:40), so temporal ordering is preserved.
#
# Authority: OPS_FORENSICS.md §F8 + PLAN.md WAVE-4 §F8
# Depends on: fix/migration-runner-2026-05-17 (def up(conn) runner interface)
from __future__ import annotations

import json
import sqlite3

# sentinel string that becomes impossible post-migration
_SENTINEL = "unknown_entered_at"

# New CHECK constraint value — used as idempotency signal
_CHECK_FRAGMENT = "LIKE '____-__-__T%' OR occurred_at = 'QUARANTINE'"

# DDL for position_events_v3 with the CHECK constraint
_NEW_TABLE_DDL = """
CREATE TABLE position_events_v3 (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version >= 1),
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 1),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'POSITION_OPEN_INTENT',
        'ENTRY_ORDER_POSTED',
        'ENTRY_ORDER_FILLED',
        'ENTRY_ORDER_VOIDED',
        'ENTRY_ORDER_REJECTED',
        'DAY0_WINDOW_ENTERED',
        'CHAIN_SYNCED',
        'CHAIN_SIZE_CORRECTED',
        'CHAIN_QUARANTINED',
        'MONITOR_REFRESHED',
        'EXIT_INTENT',
        'EXIT_ORDER_POSTED',
        'EXIT_ORDER_FILLED',
        'EXIT_ORDER_VOIDED',
        'EXIT_ORDER_REJECTED',
        'SETTLED',
        'ADMIN_VOIDED',
        'MANUAL_OVERRIDE_APPLIED'
    )),
    occurred_at TEXT NOT NULL
        CHECK (occurred_at LIKE '____-__-__T%' OR occurred_at = 'QUARANTINE'),
    phase_before TEXT CHECK (phase_before IS NULL OR phase_before IN (
        'pending_entry',
        'active',
        'day0_window',
        'pending_exit',
        'economically_closed',
        'settled',
        'voided',
        'quarantined',
        'admin_closed'
    )),
    phase_after TEXT CHECK (phase_after IS NULL OR phase_after IN (
        'pending_entry',
        'active',
        'day0_window',
        'pending_exit',
        'economically_closed',
        'settled',
        'voided',
        'quarantined',
        'admin_closed'
    )),
    strategy_key TEXT NOT NULL CHECK (strategy_key IN (
        'settlement_capture',
        'shoulder_sell',
        'center_buy',
        'opening_inertia'
    )),
    decision_id TEXT,
    snapshot_id TEXT,
    order_id TEXT,
    command_id TEXT,
    caused_by TEXT,
    idempotency_key TEXT UNIQUE,
    venue_status TEXT,
    source_module TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    env TEXT NOT NULL DEFAULT 'live',
    UNIQUE(position_id, sequence_no)
)
"""

_TRIGGERS = [
    """CREATE TRIGGER trg_position_events_no_update
BEFORE UPDATE ON position_events
BEGIN
    SELECT RAISE(FAIL, 'position_events is append-only');
END""",
    """CREATE TRIGGER trg_position_events_no_delete
BEFORE DELETE ON position_events
BEGIN
    SELECT RAISE(FAIL, 'position_events is append-only');
END""",
    """CREATE TRIGGER trg_position_events_require_env
BEFORE INSERT ON position_events
WHEN NEW.env IS NULL OR TRIM(NEW.env) = ''
BEGIN
    SELECT RAISE(FAIL, 'position_events.env is required');
END""",
]


def _is_already_applied(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='position_events' "
        "AND sql LIKE ?",
        (f"%{_CHECK_FRAGMENT}%",),
    ).fetchone()
    return row is not None


def _build_sentinel_map(conn: sqlite3.Connection) -> dict[str, str]:
    """For each sentinel row, find the occurred_at of the next ENTRY_ORDER_FILLED."""
    sentinels = conn.execute(
        "SELECT position_id, sequence_no FROM position_events WHERE occurred_at = ?",
        (_SENTINEL,),
    ).fetchall()
    result: dict[str, str] = {}
    for position_id, sentinel_seq in sentinels:
        row = conn.execute(
            """SELECT occurred_at FROM position_events
               WHERE position_id = ?
                 AND event_type = 'ENTRY_ORDER_FILLED'
                 AND sequence_no > ?
               ORDER BY sequence_no ASC
               LIMIT 1""",
            (position_id, sentinel_seq),
        ).fetchone()
        if row and row[0] and row[0] != _SENTINEL:
            result[position_id] = row[0]
        else:
            # Fallback: use the most recent non-sentinel occurred_at for this position
            fallback = conn.execute(
                """SELECT occurred_at FROM position_events
                   WHERE position_id = ?
                     AND occurred_at != ?
                   ORDER BY sequence_no DESC
                   LIMIT 1""",
                (position_id, _SENTINEL),
            ).fetchone()
            result[position_id] = fallback[0] if fallback else "QUARANTINE"
    return result


def up(conn: sqlite3.Connection) -> None:
    """Apply F8: rebuild position_events with occurred_at CHECK + backfill sentinels."""
    if _is_already_applied(conn):
        print("202605_position_events_occurred_at_iso_check: already applied, skipping")
        return

    # Confirm sentinel count before proceeding
    sentinel_count = conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE occurred_at = ?", (_SENTINEL,)
    ).fetchone()[0]
    print(f"  sentinel rows to backfill: {sentinel_count}")

    sentinel_map = _build_sentinel_map(conn)
    print(f"  sentinel→timestamp map: {json.dumps(sentinel_map, indent=2)}")

    # PRAGMA foreign_keys must be set OUTSIDE the transaction (SQLite docs)
    conn.execute("PRAGMA foreign_keys = OFF")

    conn.execute("BEGIN IMMEDIATE")
    try:
        # Step 1: create new table (strip trailing semicolon — conn.execute not executescript)
        conn.execute(_NEW_TABLE_DDL.strip().rstrip(";"))

        # Step 2: copy rows, substituting sentinels with looked-up timestamps.
        # Python-side substitution avoids SQLite CASE f-string quoting issues.
        rows = conn.execute(
            "SELECT event_id, position_id, event_version, sequence_no, event_type,"
            " occurred_at, phase_before, phase_after, strategy_key,"
            " decision_id, snapshot_id, order_id, command_id, caused_by,"
            " idempotency_key, venue_status, source_module, payload_json, env"
            " FROM position_events"
        ).fetchall()

        insert_sql = (
            "INSERT INTO position_events_v3 VALUES"
            " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        for row in rows:
            row = list(row)
            # column index 5 = occurred_at
            if row[5] == _SENTINEL:
                row[5] = sentinel_map.get(row[1], "QUARANTINE")
            conn.execute(insert_sql, row)

        # Step 3: drop old, rename new
        conn.execute("DROP TABLE position_events")
        conn.execute("ALTER TABLE position_events_v3 RENAME TO position_events")

        # Step 4: recreate triggers
        for trigger_ddl in _TRIGGERS:
            conn.execute(trigger_ddl)

        # Step 5: foreign_key_check before commit
        violations = list(conn.execute("PRAGMA foreign_key_check"))
        if violations:
            conn.execute("ROLLBACK")
            raise RuntimeError(
                f"foreign_key_check returned {len(violations)} violations after "
                f"position_events rebuild: {violations[:5]!r}"
            )

        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    conn.execute("PRAGMA foreign_keys = ON")

    # Verify no sentinels remain
    remaining = conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE occurred_at = ?", (_SENTINEL,)
    ).fetchone()[0]
    if remaining:
        raise RuntimeError(
            f"Post-migration: {remaining} sentinel rows still present — migration incomplete"
        )

    print(
        f"202605_position_events_occurred_at_iso_check: applied — "
        f"{sentinel_count} sentinels backfilled, CHECK constraint active"
    )
