# Created: 2026-07-22
# Last reused/audited: 2026-07-22
# Authority basis: operator-directed single-live-semantics extinction pass.
"""DDL for the offline regret-decomposition evidence table."""

from __future__ import annotations

import sqlite3


CREATE_REGRET_DECOMPOSITIONS_SQL = """
CREATE TABLE IF NOT EXISTS regret_decompositions (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id                   TEXT,
    strategy_id                     TEXT NOT NULL DEFAULT '',
    cohort_tag                      TEXT NOT NULL DEFAULT '',
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


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create or minimally upgrade the offline evidence table."""
    conn.execute(CREATE_REGRET_DECOMPOSITIONS_SQL)
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(regret_decompositions)").fetchall()
    }
    if "strategy_id" not in columns:
        conn.execute(
            "ALTER TABLE regret_decompositions "
            "ADD COLUMN strategy_id TEXT NOT NULL DEFAULT ''"
        )
    if "cohort_tag" not in columns:
        conn.execute(
            "ALTER TABLE regret_decompositions "
            "ADD COLUMN cohort_tag TEXT NOT NULL DEFAULT ''"
        )
