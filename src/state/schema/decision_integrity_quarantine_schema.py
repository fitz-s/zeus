# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-E

"""PR-E — CREATE TABLE DDL for decision_integrity_quarantine (trade DB).

Per §PR-E:
  - New table under SCHEMA_VERSION 31→32 bump.
  - Tags opportunity_fact rows (and future fact tables) whose forecast snapshot
    had contributes_to_target_extrema=0 or attribution UNKNOWN.
  - NON-destructive: tag only, never delete source rows.
  - UNIQUE(table_name, row_id, reason_code) enforces idempotency at the DB level.

DB placement: zeus_trades.db (same DB as opportunity_fact).

INV-37: caller supplies conn; never auto-opens.
"""

from __future__ import annotations

import sqlite3

# Schema version stamped into SCHEMA_VERSION in db.py at the PR-E bump.
SCHEMA_VERSION = 32

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS decision_integrity_quarantine (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name               TEXT NOT NULL,
    row_id                   TEXT NOT NULL,
    reason_code              TEXT NOT NULL,
    forecast_snapshot_id     TEXT,
    recorded_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    meta_json                TEXT NOT NULL DEFAULT '{}',
    UNIQUE(table_name, row_id, reason_code)
)
"""

CREATE_INDEX_TABLE_ROW_SQL = """
CREATE INDEX IF NOT EXISTS idx_decision_integrity_quarantine_table_row
    ON decision_integrity_quarantine(table_name, row_id)
"""

CREATE_INDEX_REASON_SQL = """
CREATE INDEX IF NOT EXISTS idx_decision_integrity_quarantine_reason
    ON decision_integrity_quarantine(reason_code, recorded_at)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create decision_integrity_quarantine table + indices if they do not exist.

    Idempotent (IF NOT EXISTS). Called from:
      1. db.py init_schema (daemon boot, trade DB) — PR-E production pass.
      2. tests: in-memory trade DB setup.

    INV-37: caller provides conn; never auto-opens.
    """
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_INDEX_TABLE_ROW_SQL)
    conn.execute(CREATE_INDEX_REASON_SQL)
