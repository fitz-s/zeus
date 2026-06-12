# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator skill-vs-luck law 2026-06-12 ("wu预测92不是结算在92就算赢了
#   说明这是一单完全运气获胜跟我们的系统无关 ... 昨天3单全部刚好踩在结算哪一个温度上就已经
#   说明问题") — a LUCKY win masquerades as system health and poisons the learning loop; the
#   >51% settlement win-rate goal must count SKILL not luck.
#   Schema pattern reused from src/state/schema/edli_live_profit_audit_schema.py
#   (CREATE IF NOT EXISTS + _COLUMN_MIGRATIONS forward-only ALTER + ensure_table).
#   Registry-declared in architecture/db_table_ownership.yaml (db: world, world_class,
#   created_by init_schema) — REMEMBER the unregistered-table-crash-loops-daemons incident:
#   a world.db table absent from the registry FATALs assert_db_matches_registry at boot.
"""settlement_attribution schema owner — the skill-vs-luck grade ledger.

One row per SETTLED position, graded into a typed skill category by comparing
THREE quantities:
  (1) our position direction + traded bin,
  (2) our decision-time q AND the freshest data available at settlement-eve,
  (3) the settled outcome + the market's final price.

The grade separates a SKILL win (won AND our fresh data supported it) from a
LUCKY win (won BUT our own freshest data disagreed — the Denver-if-92 shape),
and a MISCALIBRATED loss (lost AND the market priced the settled bin a large
factor above our q AND the market was right — the 06-12 three-loss shape) from
an honest variance loss. A born-stale decision gets its own brand regardless of
outcome.

Sole writer: src/analysis/settlement_skill_attribution.py (the attribution job).
This table is APPEND/UPSERT-only audit evidence — never a venue command, order
truth, settlement truth, or calibration training input.
"""

from __future__ import annotations

import sqlite3


# One canonical CREATE. The category CHECK pins the typed enum at the DB layer so
# an unknown grade can never be persisted (an antibody, not a comment).
CREATE_SETTLEMENT_ATTRIBUTION_SQL = """
CREATE TABLE IF NOT EXISTS settlement_attribution (
    attribution_id TEXT NOT NULL PRIMARY KEY,
    position_id TEXT NOT NULL,
    condition_id TEXT,
    city TEXT,
    target_date TEXT,
    temperature_metric TEXT,
    direction TEXT,
    traded_bin_label TEXT,
    category TEXT NOT NULL CHECK (category IN (
        'SKILL_WIN', 'LUCKY_WIN', 'SKILL_LOSS', 'MISCALIBRATED_LOSS', 'STALE_DECISION'
    )),
    won INTEGER NOT NULL CHECK (won IN (0, 1)),
    counts_as_skill_win INTEGER NOT NULL CHECK (counts_as_skill_win IN (0, 1)),
    -- Quantity 1: our position economics at decision time.
    avg_fill_price REAL,
    q_live REAL,
    q_lcb_5pct REAL,
    q_in_bin REAL,                       -- our probability the settle lands IN the traded bin
    market_in_bin_prob REAL,            -- market-implied prob settle lands IN bin (from fill price)
    market_q_ratio REAL,                -- market_in_bin_prob / q_in_bin (the "large factor")
    -- Quantity 2a: decision-time posterior provenance.
    decision_posterior_id TEXT,
    decision_posterior_computed_at TEXT,
    decision_posterior_age_hours REAL,
    -- Quantity 2b: freshest data available at settlement-eve.
    fresh_posterior_id TEXT,
    fresh_posterior_computed_at TEXT,
    fresh_q_supports_position INTEGER CHECK (fresh_q_supports_position IN (0, 1)),
    fresh_q_in_bin REAL,
    fresh_input_identity TEXT,           -- e.g. "forecast_posteriors:<id>" or raw cycle id
    fresh_input_age_hours REAL,
    -- Quantity 3: settlement + market truth.
    settled_value REAL,
    settlement_unit TEXT,
    settled_in_bin INTEGER CHECK (settled_in_bin IN (0, 1)),
    settled_at TEXT,
    -- Staleness provenance.
    freshness_budget_hours REAL,
    fresher_cycle_existed_at_decision INTEGER CHECK (fresher_cycle_existed_at_decision IN (0, 1)),
    -- Derivation note for any data-derived threshold (no bare magic numbers).
    large_factor_threshold REAL,
    derivation_note TEXT,
    rationale TEXT,
    graded_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    UNIQUE(position_id)
)
"""

CREATE_CATEGORY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_settlement_attribution_category
    ON settlement_attribution(category, graded_at)
"""

CREATE_CITY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_settlement_attribution_city
    ON settlement_attribution(city, target_date, temperature_metric)
"""

CREATE_SETTLED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_settlement_attribution_settled
    ON settlement_attribution(settled_at)
"""


# Forward-only column migrations (mirrors edli_live_profit_audit_schema pattern).
# Empty today (the table is new); future column additions land here, never as a
# table rebuild, so a live DB whose migration lagged a deploy is upgraded in place.
_COLUMN_MIGRATIONS: dict[str, str] = {}


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create the settlement_attribution table + indexes (idempotent).

    Called by init_schema (world.db) on every boot. Forward-only: CREATE IF NOT
    EXISTS + ALTER ADD COLUMN for any migration entry whose column is absent.
    """
    conn.execute(CREATE_SETTLEMENT_ATTRIBUTION_SQL)
    existing = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_info(settlement_attribution)"
        ).fetchall()
    }
    for column, ddl in _COLUMN_MIGRATIONS.items():
        if column not in existing:
            conn.execute(ddl)
    conn.execute(CREATE_CATEGORY_INDEX_SQL)
    conn.execute(CREATE_CITY_INDEX_SQL)
    conn.execute(CREATE_SETTLED_INDEX_SQL)
