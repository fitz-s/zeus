# Lifecycle: created=2026-10-01; last_reviewed=2026-10-01; last_reused=never
# Purpose: Add the nullable data_coverage.evidence_json column that binds a
#   versioned NOAA WRH absence proof to its coverage row.
#
# Migration semantic policy:
#   DB target: state/zeus-world.db only (data_coverage is WORLD-class).
#   Tables touched:
#     data_coverage — ADD COLUMN evidence_json TEXT, nullable, unconstrained:
#       O(1) metadata-only ALTER; no existing row is read or rewritten.
#   Existing rows keep evidence_json NULL. A NULL proof authorizes nothing:
#     noaa_absence_witness() requires the current proof version, so a legacy
#     reason-only SOURCE_CONFIRMED_EMPTY row stays FAILED with an expired
#     embargo and is re-fetched (revalidated) by the observation catch-up.
#   Schema fingerprint: fresh DBs receive the same column from init_schema.
#   Reversibility: no down(); the nullable column is inert without readers.
#   Idempotent: no-op when data_coverage is absent or already has the column.
# Authority basis: round-2 external review §3/§4 [HIGH] absence-proof
#   migration and durability (src/data/settlement_observation_selection.py).
"""Add data_coverage.evidence_json to a world DB.

Runner interface: def up(conn: sqlite3.Connection) -> None
"""
from __future__ import annotations

import sqlite3

TARGET_DB = "world"

_TABLE = "data_coverage"
_COLUMN = "evidence_json"


def up(conn: sqlite3.Connection) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)
    ).fetchone()
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({_TABLE})")}
    if exists and _COLUMN not in columns:
        conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {_COLUMN} TEXT")
    conn.commit()
