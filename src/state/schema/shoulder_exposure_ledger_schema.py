# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T3

"""Phase 3 T3 — CREATE TABLE DDL for shoulder_exposure_ledger (world DB).

Per plan §2 T3:
  - New table under SCHEMA_VERSION 22→23 bump.
  - Columns: shoulder_side, weather_system_cluster, city, target_date, source,
    regime, notional_usd, decision_event_id (FK-like to decision_events),
    observed_at, schema_version CHECK (22, 23).
  - AUTOINCREMENT integer PK (append-only log); decision_event_id + shoulder_side
    form a FK-like join key with decision_events (no UNIQUE constraint enforced).

INV-37: caller supplies conn; never auto-opens.
"""

from __future__ import annotations

import sqlite3

# Schema version stamped into each row; bump in sync with db.py SCHEMA_VERSION.
SCHEMA_VERSION = 23

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS shoulder_exposure_ledger (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    shoulder_side           TEXT NOT NULL CHECK (shoulder_side IN ('sell', 'buy')),
    weather_system_cluster  TEXT NOT NULL,
    city                    TEXT NOT NULL,
    target_date             TEXT NOT NULL,
    source                  TEXT NOT NULL,
    regime                  TEXT NOT NULL,
    notional_usd            REAL NOT NULL,
    decision_event_id       TEXT NOT NULL,
    observed_at             TEXT NOT NULL,
    schema_version          INTEGER NOT NULL CHECK (schema_version IN (22, 23, 24, 25, 26))
)
"""

CREATE_INDEX_CLUSTER_SIDE_SQL = """
CREATE INDEX IF NOT EXISTS idx_shoulder_exposure_ledger_cluster_side
    ON shoulder_exposure_ledger(weather_system_cluster, shoulder_side)
"""

CREATE_INDEX_CITY_SQL = """
CREATE INDEX IF NOT EXISTS idx_shoulder_exposure_ledger_city
    ON shoulder_exposure_ledger(city, target_date)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create shoulder_exposure_ledger table + indices if they do not exist.

    Idempotent (IF NOT EXISTS). Called from:
      1. db.py init_schema (daemon boot, world DB) — Phase 3 T3 production pass
      2. tests: in-memory world DB setup for cluster cap + readiness report tests.

    INV-37: caller provides conn; never auto-opens.
    """
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_INDEX_CLUSTER_SIDE_SQL)
    conn.execute(CREATE_INDEX_CITY_SQL)
