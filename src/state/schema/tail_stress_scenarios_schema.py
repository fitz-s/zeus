# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T2 + 04_PHASE_3_SHOULDER.md §"Schema impact" + Phase 3 T3 (2026-05-21): CHECK extended to 23

"""Phase 3 T2 — CREATE TABLE DDL for tail_stress_scenarios (world DB).

Per 04_PHASE_3_SHOULDER.md §"Schema impact":
  - New table under SCHEMA_VERSION 17→18 bump (v17 claimed by PR #253).
  - PK matches DecisionNaturalKey (market_slug, temperature_metric, target_date,
    observation_time, decision_seq) for FK-like joins with decision_events.
  - columns: scenarios JSON (ordered array of scenario_id strings),
    max_loss_pct REAL, tail_probability_stressed REAL, schema_version CHECK (17, 18).

INV-37: caller supplies conn; never auto-opens.
"""

from __future__ import annotations

import sqlite3

# Schema version stamped into each row; bump in sync with db.py SCHEMA_VERSION.
SCHEMA_VERSION = 23

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tail_stress_scenarios (
    market_slug             TEXT NOT NULL,
    temperature_metric      TEXT NOT NULL,
    target_date             TEXT NOT NULL,
    observation_time        TEXT NOT NULL,
    decision_seq            INTEGER NOT NULL,
    scenarios               TEXT NOT NULL,
    max_loss_pct            REAL NOT NULL,
    tail_probability_stressed REAL NOT NULL,
    schema_version          INTEGER NOT NULL CHECK (schema_version IN (17, 18, 19, 20, 21, 22, 23)),
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""

CREATE_INDEX_MARKET_DATE_SQL = """
CREATE INDEX IF NOT EXISTS idx_tail_stress_scenarios_market_date
    ON tail_stress_scenarios(market_slug, target_date)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create tail_stress_scenarios table + index if they do not exist.

    Idempotent (IF NOT EXISTS). Called from:
      1. db.py init_schema (daemon boot, world DB) — Phase 3 T2 production pass
      2. scripts/migrate_no_trade_events_rebuild_phase3_t2.py — operator one-shot migration
    """
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_INDEX_MARKET_DATE_SQL)
