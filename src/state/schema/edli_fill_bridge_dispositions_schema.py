# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: fill-bridge retry-spiral incident 2026-06-12 — durable
#   per-aggregate disposition table that prevents settled-market infinite retry
#   and quarantines persistently-failing aggregates after N attempts.
"""Schema owner for edli_fill_bridge_dispositions.

One row per EDLI aggregate that the _edli_durable_fill_bridge_scan has
terminally routed.  Two disposition classes:

  SETTLED_MARKET_FILL_BOOKED
      The aggregate's market was already settled when the bridge attempted
      to materialise position_current.  The fill is booked for accounting
      purposes; no position lifecycle exists (it is over).  Excluded from
      all future candidate scans (permanent terminal routing).

  QUARANTINED_BRIDGE_FAILURE
      The bridge raised a non-transient exception on N consecutive attempts.
      The aggregate is parked here so it does not starve new real fills.
      One ERROR log is emitted at quarantine time; the row carries the last
      error string for operator inspection.

Additional columns:
  attempt_count   — running total of failed attempts before terminal disposition.
  last_error      — last exception message; updated on every failed attempt while
                    still below the quarantine threshold.
  updated_at      — wall-clock timestamp of the last state change.
"""
from __future__ import annotations

import sqlite3


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS edli_fill_bridge_dispositions (
    aggregate_id  TEXT PRIMARY KEY,
    disposition   TEXT
        CHECK (disposition IS NULL OR disposition IN ('SETTLED_MARKET_FILL_BOOKED', 'QUARANTINED_BRIDGE_FAILURE')),
    reason        TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Idempotent DDL for edli_fill_bridge_dispositions (zeus-world.db / any init_schema conn)."""
    conn.execute(CREATE_TABLE_SQL)
    # Nullable last_error column: added here to support legacy DBs that had an
    # earlier version of this table (pure safety; fresh DBs already have it).
    _ensure_column(conn, "last_error", "TEXT")
    _ensure_column(conn, "attempt_count", "INTEGER NOT NULL DEFAULT 0")


def _ensure_column(conn: sqlite3.Connection, column_name: str, column_sql: str) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(edli_fill_bridge_dispositions)").fetchall()}
    if column_name not in cols:
        try:
            conn.execute(f"ALTER TABLE edli_fill_bridge_dispositions ADD COLUMN {column_name} {column_sql}")
        except sqlite3.OperationalError:
            pass
