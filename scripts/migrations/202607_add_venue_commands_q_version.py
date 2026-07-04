# Lifecycle: created=2026-07-04; last_reviewed=2026-07-04; last_reused=never
# Purpose: SCH-W1.2-ORDER-STATE live trade-DB migration for the additive
#   venue_commands.q_version decision-basis stamp.
#
# Migration semantic policy:
#   DB target: state/zeus_trades.db only (single-DB, Domain.TRADE).
#   Tables touched:
#     venue_commands — ADD COLUMN q_version TEXT, nullable, write-once at
#       command creation by src/state/venue_command_repo.py::insert_command.
#   Schema fingerprint: already represented in src/state/db.py DDL and the
#   schema packet; live DBs created before W1.2 need this additive bridge.
#   Reversibility: down() is NOT provided. The nullable column is inert without
#   writers, and rollback is a code revert that leaves the unused column.
#   Idempotent: safe to re-run; no-op when venue_commands is absent or already
#   has q_version.
# Authority basis: docs/rebuild/schema_packets/w1_2_order_state_extension_schema_packet_2026-07-02.md
#   §1 `venue_commands.q_version` additive column.
"""Add venue_commands.q_version to the live trade DB.

Runner interface: def up(conn: sqlite3.Connection) -> None
"""
from __future__ import annotations

import sqlite3

TARGET_DB = "trade"

_TABLE = "venue_commands"
_COLUMN = "q_version"


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()[0]
        > 0
    )


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def up(conn: sqlite3.Connection) -> None:
    if not _has_table(conn, _TABLE):
        conn.commit()
        return
    if _has_column(conn, _TABLE, _COLUMN):
        conn.commit()
        return

    conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {_COLUMN} TEXT")
    conn.commit()
