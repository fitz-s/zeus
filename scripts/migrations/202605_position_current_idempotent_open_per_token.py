# Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=never
# Purpose: F109 — install a partial UNIQUE INDEX on position_current(token_id)
#   restricted to OPEN_EXPOSURE_PHASES so the writer can never insert a second
#   live row for the same token. This is the schema-level antibody for the
#   non-idempotent position-open defect (see docs F109 trace).
#
# Authority: docs/operations/task_2026-05-17_post_karachi_remediation/F109_*.md
#            + this branch's docs/operations/task_2026-05-17_f109_fix/TRACE.md
#
# DEPLOY ORDER (mandatory):
#   1. boot-time consolidator (src/state/position_duplicate_consolidator.py)
#      runs first and reduces any token to <=1 OPEN_EXPOSURE_PHASES row
#   2. THIS migration then applies the partial UNIQUE INDEX
#   3. writer-side idempotency check (src/state/projection.py) becomes
#      the second line of defense; the UNIQUE INDEX is the hard floor
#
# Idempotency: the partial index uses IF NOT EXISTS so repeated up() is safe.
# Pre-flight guard: if duplicates still exist at index-creation time, log and
# raise — DO NOT auto-create the index over inconsistent data. This protects
# against accidental deploy-order reversal.
from __future__ import annotations

import sqlite3

TARGET_DB = "trade"

_INDEX_NAME = "ux_position_current_open_per_token"
_OPEN_PHASES = ("pending_entry", "active", "day0_window", "pending_exit", "unknown")
_PHASE_LIST_SQL = ", ".join(f"'{p}'" for p in _OPEN_PHASES)
_INDEX_DDL = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME} "
    f"ON position_current(token_id) "
    f"WHERE phase IN ({_PHASE_LIST_SQL}) AND token_id IS NOT NULL"
)


def _index_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (_INDEX_NAME,),
    ).fetchone()
    return row is not None


def _enumerate_duplicates(conn: sqlite3.Connection) -> list[tuple[str, int, str]]:
    """Return tokens that currently hold >1 OPEN_EXPOSURE_PHASES row.

    Each tuple: (token_id, row_count, comma-separated position_ids).
    """
    rows = conn.execute(
        f"""
        SELECT token_id, COUNT(*) AS n, GROUP_CONCAT(position_id, ',') AS ids
          FROM position_current
         WHERE phase IN ({_PHASE_LIST_SQL}) AND token_id IS NOT NULL
         GROUP BY token_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    return [(str(r[0]), int(r[1]), str(r[2] or "")) for r in rows]


def up(conn: sqlite3.Connection) -> None:
    """Apply F109: install partial UNIQUE INDEX on (token_id) for open phases.

    Pre-flight: refuses to create the index if duplicates still exist; the
    consolidator must have run first. Idempotent on re-application.
    """
    if _index_exists(conn):
        print(
            f"202605_position_current_idempotent_open_per_token: "
            f"index {_INDEX_NAME} already present, skipping"
        )
        return

    duplicates = _enumerate_duplicates(conn)
    if duplicates:
        details = "; ".join(
            f"token={token[-12:]} n={n} ids={ids}" for token, n, ids in duplicates
        )
        raise RuntimeError(
            f"F109 migration aborted: {len(duplicates)} token(s) still hold "
            f"multiple open-phase rows. Run "
            f"src.state.position_duplicate_consolidator.consolidate(conn) first. "
            f"Details: {details}"
        )

    conn.execute(_INDEX_DDL)
    print(
        f"202605_position_current_idempotent_open_per_token: "
        f"index {_INDEX_NAME} created (partial UNIQUE on token_id "
        f"WHERE phase IN OPEN_EXPOSURE_PHASES)"
    )
