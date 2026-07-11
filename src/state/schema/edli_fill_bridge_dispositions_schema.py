# Created: 2026-06-12
# Last reused or audited: 2026-07-11
# Authority basis: fill-bridge retry-spiral incident 2026-06-12 — durable
#   per-aggregate disposition table that prevents settled-market infinite
#   retry. T1 excision (2026-07-11): a formerly permanent bridge-failure
#   terminal disposition excluded confirmed on-chain fills from all future
#   scans forever with no release path (8 live rows = potentially
#   unmaterialised real money). Replaced with bounded decaying retry that
#   never terminates eligibility; the retired CHECK literal and disposition
#   value are dropped from both the constraint and any live row carrying it.
"""Schema owner for edli_fill_bridge_dispositions.

One row per EDLI aggregate the _edli_durable_fill_bridge_scan has seen.
Two terminal disposition classes:

  SETTLED_MARKET_FILL_BOOKED
      The aggregate's market was already settled when the bridge attempted
      to materialise position_current.  The fill is booked for accounting
      purposes; no position lifecycle exists (it is over).  Excluded from
      all future candidate scans (permanent terminal routing — legitimate
      accounting truth, not a failure state).

  UNRECOVERABLE_MANUAL_REVIEW
      An operator or script has explicitly diagnosed the aggregate as
      structurally unrecoverable (e.g. a payload shape current code can
      never parse) and called mark_unrecoverable_manual_review. NEVER
      written by the automatic scan/retry loop. Stops wasted automatic
      retry attempts WITHOUT excluding the row from operator visibility:
      every scan pass that encounters it logs a WARNING with the row's age,
      so it keeps surfacing instead of silently vanishing.

A row with disposition NULL is accumulating: the bridge has failed on it
before but retry eligibility never terminates. attempt_count is retry-cadence
evidence, not a path to exclusion (see ``is_retry_eligible`` /
``_retry_backoff_seconds`` in src.events.edli_position_bridge) — a poison
aggregate is retried less and less often (bounded per-cycle cost), never
excluded, because a confirmed on-chain fill is truth that must materialise
unless a human has separately confirmed it never can.

Additional columns:
  attempt_count   — running total of failed attempts; drives retry backoff.
  last_error      — last exception message; updated on every failed attempt.
  updated_at      — wall-clock timestamp of the last state change; the retry
                    backoff clock (and the manual-review age report) reads
                    from here.
"""
from __future__ import annotations

import sqlite3


# The complete set of terminal disposition values the CHECK constraint
# admits today. A row's disposition is either NULL (accumulating, retried on
# a decaying backoff cadence forever, always automatic) or one of these:
#   SETTLED_MARKET_FILL_BOOKED    accounting truth (see the module docstring).
#   UNRECOVERABLE_MANUAL_REVIEW   an operator/script's explicit diagnosis
#                                 that automatic retry can never succeed;
#                                 written by exactly one function
#                                 (mark_unrecoverable_manual_review in
#                                 src.events.edli_position_bridge), never by
#                                 the automatic scan/retry loop.
# Retired terminal values are never re-added here — they are dropped from
# the CHECK and any row still carrying one is drained back to NULL by
# ``_ensure_disposition_check_current`` below.
_ALLOWED_TERMINAL_DISPOSITIONS = ("SETTLED_MARKET_FILL_BOOKED", "UNRECOVERABLE_MANUAL_REVIEW")

_ALLOWED_DISPOSITIONS_SQL = ", ".join(f"'{v}'" for v in _ALLOWED_TERMINAL_DISPOSITIONS)

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS edli_fill_bridge_dispositions (
    aggregate_id  TEXT PRIMARY KEY,
    disposition   TEXT
        CHECK (disposition IS NULL OR disposition IN ({_ALLOWED_DISPOSITIONS_SQL})),
    reason        TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
)
"""

_ALLOWED_DISPOSITIONS_CASE_SQL = (
    "CASE WHEN disposition IN (" + _ALLOWED_DISPOSITIONS_SQL + ") THEN disposition ELSE NULL END"
)


def ensure_table(conn: sqlite3.Connection) -> None:
    """Idempotent DDL for edli_fill_bridge_dispositions (zeus-world.db / any init_schema conn)."""
    conn.execute(CREATE_TABLE_SQL)
    # Nullable last_error column: added here to support legacy DBs that had an
    # earlier version of this table (pure safety; fresh DBs already have it).
    _ensure_column(conn, "last_error", "TEXT")
    _ensure_column(conn, "attempt_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_disposition_nullable(conn)
    _ensure_disposition_check_current(conn)


def _ensure_disposition_nullable(conn: sqlite3.Connection) -> None:
    """Rebuild the table when a legacy DB still carries ``disposition TEXT NOT NULL``.

    Idempotent DDL (the IF-NOT-EXISTS form above) is unable to relax constraints
    on a pre-existing table, so a live DB created under the original
    two-terminal-states DDL silently keeps NOT NULL while the code writes
    NULL-disposition accumulating rows. That made every `_increment_failure_count`
    insert fail, freezing attempt_count at 1 and defeating retry-cadence
    evidence entirely (infinite retry storm, 2026-06-12). SQLite cannot ALTER
    a column constraint; the sanctioned path is a rebuild.
    """
    notnull = {
        str(row[1]): bool(row[3])
        for row in conn.execute("PRAGMA table_info(edli_fill_bridge_dispositions)").fetchall()
    }
    if not notnull.get("disposition", False):
        return
    conn.execute("DROP TABLE IF EXISTS edli_fill_bridge_dispositions_rebuild")
    conn.execute(
        CREATE_TABLE_SQL.replace(
            "IF NOT EXISTS edli_fill_bridge_dispositions",
            "edli_fill_bridge_dispositions_rebuild",
        )
    )
    conn.execute(
        f"""
        INSERT INTO edli_fill_bridge_dispositions_rebuild
            (aggregate_id, disposition, reason, attempt_count, last_error, created_at, updated_at)
        SELECT
            aggregate_id,
            {_ALLOWED_DISPOSITIONS_CASE_SQL},
            reason, attempt_count, last_error, created_at, updated_at
        FROM edli_fill_bridge_dispositions
        """
    )
    conn.execute("DROP TABLE edli_fill_bridge_dispositions")
    conn.execute(
        "ALTER TABLE edli_fill_bridge_dispositions_rebuild RENAME TO edli_fill_bridge_dispositions"
    )


def _ensure_disposition_check_current(conn: sqlite3.Connection) -> None:
    """Rebuild the table when its CHECK constraint has drifted from
    ``_ALLOWED_TERMINAL_DISPOSITIONS`` (SQLite cannot ALTER a CHECK).

    A DDL retired a terminal disposition value (T1, 2026-07-11): a formerly
    permanent bridge-failure state made a confirmed on-chain fill invisible
    forever after N failed attempts, with no release path. Any row still
    carrying a disposition value outside the current allowed set is drained
    back to an accumulating row (disposition NULL) so the fixed scanner
    re-drives it under the decaying retry cadence instead of leaving it
    stranded on a retired terminal state.
    """
    try:
        sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='edli_fill_bridge_dispositions'"
        ).fetchone()
    except sqlite3.Error:
        return
    if sql_row is None:
        return
    table_sql = str(sql_row[0] if not isinstance(sql_row, sqlite3.Row) else sql_row["sql"])
    # Exact-fragment match (not mere substring containment of the allowed-set
    # literal): a legacy CHECK carrying an additional retired value still
    # contains "'SETTLED_MARKET_FILL_BOOKED'" as a substring but NOT this
    # closed "IN (...)" fragment, since a retired literal sits before the
    # closing paren.
    if f"IN ({_ALLOWED_DISPOSITIONS_SQL})" in table_sql:
        return  # CHECK already matches the current allowed set — no-op.
    conn.execute("DROP TABLE IF EXISTS edli_fill_bridge_dispositions_rebuild")
    conn.execute(
        CREATE_TABLE_SQL.replace(
            "IF NOT EXISTS edli_fill_bridge_dispositions",
            "edli_fill_bridge_dispositions_rebuild",
        )
    )
    conn.execute(
        f"""
        INSERT INTO edli_fill_bridge_dispositions_rebuild
            (aggregate_id, disposition, reason, attempt_count, last_error, created_at, updated_at)
        SELECT
            aggregate_id,
            {_ALLOWED_DISPOSITIONS_CASE_SQL},
            reason, attempt_count, last_error, created_at, updated_at
        FROM edli_fill_bridge_dispositions
        """
    )
    conn.execute("DROP TABLE edli_fill_bridge_dispositions")
    conn.execute(
        "ALTER TABLE edli_fill_bridge_dispositions_rebuild RENAME TO edli_fill_bridge_dispositions"
    )


def _ensure_column(conn: sqlite3.Connection, column_name: str, column_sql: str) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(edli_fill_bridge_dispositions)").fetchall()}
    if column_name not in cols:
        try:
            conn.execute(f"ALTER TABLE edli_fill_bridge_dispositions ADD COLUMN {column_name} {column_sql}")
        except sqlite3.OperationalError:
            pass
