# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase6_evidence_ladder/PHASE_6_PLAN.md §T2+T3
#                  + §Schema-Bump-Summary (T2+T3 in same PR = single bump N→N+1)
"""Phase 6 T2+T3 — DDL for shadow_experiments, evidence_tier_assignments,
and regret_decompositions tables (world DB).

Single-bump approach: T2 and T3 tables land in the same schema version bump
per plan §Schema-Bump-Summary. All three tables are created here.

INV-37: caller supplies conn; never auto-opens.
"""
from __future__ import annotations

import sqlite3


# ---------------------------------------------------------------------------
# T2 tables: shadow_experiments + evidence_tier_assignments + index
# ---------------------------------------------------------------------------

CREATE_SHADOW_EXPERIMENTS_SQL = """
CREATE TABLE IF NOT EXISTS shadow_experiments (
    experiment_id  TEXT PRIMARY KEY,
    strategy_id    TEXT NOT NULL,
    config_hash    TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    closed_at      TEXT,
    cohort_tag     TEXT NOT NULL,
    immutable      INTEGER NOT NULL DEFAULT 1
        CHECK (immutable IN (0, 1))
)
"""

CREATE_EVIDENCE_TIER_ASSIGNMENTS_SQL = """
CREATE TABLE IF NOT EXISTS evidence_tier_assignments (
    strategy_id    TEXT NOT NULL,
    tier           INTEGER NOT NULL,
    assigned_at    TEXT NOT NULL,
    rationale      TEXT,
    operator_ref   TEXT,
    verdict_reason TEXT
)
"""

CREATE_IDX_ETA_STRATEGY_ASSIGNED_SQL = """
CREATE INDEX IF NOT EXISTS idx_eta_strategy_assigned
    ON evidence_tier_assignments (strategy_id, assigned_at DESC)
"""

# ---------------------------------------------------------------------------
# T3 table: regret_decompositions
# ---------------------------------------------------------------------------

CREATE_REGRET_DECOMPOSITIONS_SQL = """
CREATE TABLE IF NOT EXISTS regret_decompositions (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id                   TEXT NOT NULL
        REFERENCES shadow_experiments(experiment_id),
    decision_event_id               TEXT NOT NULL,
    forecast_error_usd              REAL,
    observation_error_usd           REAL,
    quote_error_usd                 REAL,
    non_fill_error_usd              REAL,
    fee_error_usd                   REAL,
    timing_error_usd                REAL,
    settlement_ambiguity_error_usd  REAL,
    total_regret_usd                REAL NOT NULL,
    computed_at                     TEXT NOT NULL
)
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create Phase 6 evidence tables if they do not exist.

    Idempotent (IF NOT EXISTS). Called from db.py init_schema during daemon boot.

    Tables created:
      - shadow_experiments      (T2 — world DB)
      - evidence_tier_assignments (T2 — world DB) + idx_eta_strategy_assigned
      - regret_decompositions   (T3 — world DB)

    INV-37: caller provides conn; never auto-opens.
    """
    conn.execute(CREATE_SHADOW_EXPERIMENTS_SQL)
    conn.execute(CREATE_EVIDENCE_TIER_ASSIGNMENTS_SQL)
    conn.execute(CREATE_IDX_ETA_STRATEGY_ASSIGNED_SQL)
    conn.execute(CREATE_REGRET_DECOMPOSITIONS_SQL)
