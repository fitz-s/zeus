# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) — D1/D2 "re-decide held exposure". The consult's [HIGH] concurrency
#   finding: there is no family-level lifecycle lease covering the whole
#   redecision -> (fill-up | exit -> counter-entry) transaction, so duplicate EDLI
#   events or a SUBMIT_UNKNOWN_SIDE_EFFECT can race into a second order (the
#   2026-06-16 double-rest class). This table IS that lease: a durable, UNIQUE
#   active row per weather family while a rebalance is in flight.
#   Registry-declared in architecture/db_table_ownership.yaml (db: world,
#   world_class, created_by init_schema) — an unregistered world.db table FATALs
#   assert_db_matches_registry at boot, so the registry entry lands in the SAME change.
"""family_rebalance_intents schema owner — the family-rebalance lifecycle lease.

One ACTIVE row per (env, city, target_date, metric) family while a re-decide-held-
exposure operation (FILL_UP or SHIFT_BIN) is in flight. The partial UNIQUE index on
family_key over the non-terminal statuses is the concurrency guard: a second
concurrent EDLI redecision for the same family fails to INSERT (IntegrityError) and
must no-op rather than emit a second order. Released only on a terminal status
(COMPLETE / ABORTED / EXIT_ONLY_COMPLETE).

Sole writer: src/strategy/family_rebalance.py (the lease manager). This is a
lifecycle-coordination ledger — never a venue command, order truth, or settlement
truth (those remain in venue_commands / venue_order_facts / settlement tables).
"""

from __future__ import annotations

import sqlite3


# Non-terminal statuses: while the lease is in any of these, the family is held by
# exactly one active rebalance (the partial-unique index enforces it).
_ACTIVE_STATUSES: tuple[str, ...] = (
    "PLANNED",
    "EXIT_SUBMITTED",
    "EXIT_UNKNOWN",
    "EXIT_PARTIAL",
    "ENTRY_SUBMITTED",
    "ENTRY_UNKNOWN",
    "ENTRY_PARTIAL",
    "REVIEW_REQUIRED",
)
_TERMINAL_STATUSES: tuple[str, ...] = (
    "COMPLETE",
    "ABORTED",
    "EXIT_ONLY_COMPLETE",
)
_ALL_STATUSES: tuple[str, ...] = _ACTIVE_STATUSES + _TERMINAL_STATUSES

_STATUS_CHECK = ", ".join(f"'{s}'" for s in _ALL_STATUSES)
_ACTIVE_IN = ", ".join(f"'{s}'" for s in _ACTIVE_STATUSES)

CREATE_FAMILY_REBALANCE_INTENTS_SQL = f"""
CREATE TABLE IF NOT EXISTS family_rebalance_intents (
    intent_id TEXT NOT NULL PRIMARY KEY,
    event_id TEXT,
    family_key TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('FILL_UP', 'SHIFT_BIN')),
    held_position_id TEXT,
    held_token_id TEXT,
    held_bin_id TEXT,
    selected_token_id TEXT,
    selected_bin_id TEXT,
    q_entry_lcb REAL,
    q_current_lcb REAL,
    target_total_exposure_usd REAL,
    current_exposure_usd REAL,
    pending_exposure_usd REAL,
    delta_entry_usd REAL,
    old_exit_command_id TEXT,
    new_entry_command_id TEXT,
    status TEXT NOT NULL CHECK (status IN ({_STATUS_CHECK})),
    generation INTEGER NOT NULL DEFAULT 1,
    abort_reason TEXT,
    idempotency_key TEXT,
    evidence_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1)
)
"""

# THE concurrency guard: at most one ACTIVE rebalance per family. A second concurrent
# acquire on the same family violates this and raises IntegrityError (caught by the
# manager -> no-op, no second order). Partial index over non-terminal statuses so a
# COMPLETED/ABORTED lease never blocks the next legitimate rebalance.
CREATE_ACTIVE_FAMILY_UNIQUE_INDEX_SQL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS uq_family_rebalance_active
    ON family_rebalance_intents(family_key)
    WHERE status IN ({_ACTIVE_IN})
"""

CREATE_STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_family_rebalance_status
    ON family_rebalance_intents(status, updated_at)
"""

_COLUMN_MIGRATIONS: dict[str, str] = {}


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create the family_rebalance_intents table + indexes (idempotent).

    Called by init_schema (world.db) on every boot. Forward-only: CREATE IF NOT
    EXISTS + the partial-unique active-family index + a status index.
    """
    conn.execute(CREATE_FAMILY_REBALANCE_INTENTS_SQL)
    existing = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_info(family_rebalance_intents)"
        ).fetchall()
    }
    for column, ddl in _COLUMN_MIGRATIONS.items():
        if column not in existing:
            conn.execute(ddl)
    conn.execute(CREATE_ACTIVE_FAMILY_UNIQUE_INDEX_SQL)
    conn.execute(CREATE_STATUS_INDEX_SQL)
