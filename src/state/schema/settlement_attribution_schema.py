# Created: 2026-06-12
# Last reused or audited: 2026-06-21
# Authority basis: operator skill-vs-luck law 2026-06-12 ("wu预测92不是结算在92就算赢了
#   说明这是一单完全运气获胜跟我们的系统无关 ... 昨天3单全部刚好踩在结算哪一个温度上就已经
#   说明问题") — a LUCKY win masquerades as system health and poisons the learning loop; the
#   >51% settlement win-rate goal must count SKILL not luck.
#   Schema pattern reused from src/state/schema/edli_live_profit_audit_schema.py
#   (CREATE IF NOT EXISTS + _COLUMN_MIGRATIONS forward-only ALTER + ensure_table).
#   CHECK-rebuild migration pattern reused from src/state/schema/no_trade_events_schema.py
#   (guarded table rebuild for stale enum CHECK constraints under INV-37).
#   Registry-declared in architecture/db_table_ownership.yaml (db: world, world_class,
#   created_by init_schema) — REMEMBER the unregistered-table-crash-loops-daemons incident:
#   a world.db table absent from the registry FATALs assert_db_matches_registry at boot.
#   - 2026-06-21 (immutable decision-q certificate authority for settlement skill
#     attribution, lifecycle-alpha): added the 6th category UNATTRIBUTABLE_Q_MISSING
#     to the category CHECK so a position whose immutable decision-q certificate is
#     unresolvable can never be persisted as SKILL/LUCK. Existing live tables carry
#     the old 5-value CHECK, so a guarded rebuild upgrades the constraint in place.
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
        'SKILL_WIN', 'LUCKY_WIN', 'SKILL_LOSS', 'MISCALIBRATED_LOSS', 'STALE_DECISION',
        'UNATTRIBUTABLE_Q_MISSING'
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
    -- LX-E packet (2026-07-13): a hold-to-settlement world-grade P&L label, NEVER
    -- actual chain-realized wallet P&L (the name says what it is). Replaces the
    -- removed writeback_settlement_pnl_to_audit, which used to write this same
    -- value into edli_live_profit_audit.pnl_usd — a forbidden world-grade/
    -- chain-money collapse. NULL when the fee/economics inputs needed to compute
    -- it are unresolvable (never fabricated).
    world_grade_pnl_usd REAL,
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

# LX-E packet (2026-07-13, docs/rebuild/local_ledger_excision_2026-07-12.md Round-2
# delta adjudication "mutable learning receipts"): persist_grade's
# ON CONFLICT(position_id) DO UPDATE can silently replace an earlier analytical
# result. Before any such overwrite, the CURRENT row's full pre-image is archived
# here as a JSON snapshot (whole-row copy — the smaller schema change, matching
# edli_live_profit_audit_supersessions). settlement_attribution itself stays the
# single-row-per-position "current" table (unchanged read contract for every
# downstream script); this table is the permanent, append-only history.
CREATE_SUPERSESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS settlement_attribution_supersessions (
    supersession_id TEXT NOT NULL PRIMARY KEY,
    position_id TEXT NOT NULL,
    prior_row_json TEXT NOT NULL,
    superseded_by TEXT,
    superseded_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1)
)
"""

CREATE_SUPERSESSIONS_POSITION_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_settlement_attribution_supersessions_position
    ON settlement_attribution_supersessions(position_id, superseded_at)
"""


# Forward-only column migrations (mirrors edli_live_profit_audit_schema pattern).
_COLUMN_MIGRATIONS: dict[str, str] = {
    "world_grade_pnl_usd": "ALTER TABLE settlement_attribution ADD COLUMN world_grade_pnl_usd REAL",
}


# Every category the current grader can emit. The DB-layer CHECK must accept all
# of these; an existing live table created under an older enum carries a stale
# CHECK and is rebuilt in place by ``_rebuild_stale_category_check`` below.
_CATEGORY_VALUES: tuple[str, ...] = (
    "SKILL_WIN", "LUCKY_WIN", "SKILL_LOSS", "MISCALIBRATED_LOSS",
    "STALE_DECISION", "UNATTRIBUTABLE_Q_MISSING",
)


def _rebuild_stale_category_check(conn: sqlite3.Connection) -> None:
    """Upgrade a stale category CHECK on an existing settlement_attribution table.

    The category CHECK is a table-level constraint — SQLite cannot ALTER it in
    place, so a table created under an older enum (e.g. the original 5-value set
    without UNATTRIBUTABLE_Q_MISSING) must be rebuilt to accept the new value.

    Idempotent: if every current category already appears in the live table SQL,
    returns immediately without touching the table (the hot path stays a no-op).
    The rebuild copies all existing rows by an EXPLICIT common column list (never
    ``SELECT *``): a column added since this table was created (e.g.
    ``world_grade_pnl_usd``, LX-E packet) may land at a different physical
    position on an ALTER-upgraded old table than in the freshly-created ``_new``
    table's CREATE-declared order, and a positional ``SELECT *`` copy would then
    silently shift values into the wrong columns. Runs under a SAVEPOINT for
    atomicity.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='settlement_attribution'"
    ).fetchone()
    table_sql = str(row[0] if row else "")
    if not table_sql:
        return  # table absent — CREATE path handles it
    if all(value in table_sql for value in _CATEGORY_VALUES):
        return  # CHECK already current — nothing to do

    conn.execute("SAVEPOINT settlement_attribution_check_rebuild")
    try:
        conn.execute("DROP TABLE IF EXISTS settlement_attribution_new")
        conn.execute(
            CREATE_SETTLEMENT_ATTRIBUTION_SQL.replace(
                "settlement_attribution", "settlement_attribution_new", 1
            )
        )
        old_cols = {
            str(r[1]) for r in conn.execute(
                "PRAGMA table_info(settlement_attribution)"
            ).fetchall()
        }
        new_cols = [
            str(r[1]) for r in conn.execute(
                "PRAGMA table_info(settlement_attribution_new)"
            ).fetchall()
        ]
        common_cols = [c for c in new_cols if c in old_cols]
        col_list = ", ".join(common_cols)
        pre_count = conn.execute(
            "SELECT COUNT(*) FROM settlement_attribution"
        ).fetchone()[0]
        conn.execute(
            f"INSERT INTO settlement_attribution_new ({col_list}) "
            f"SELECT {col_list} FROM settlement_attribution"
        )
        post_count = conn.execute(
            "SELECT COUNT(*) FROM settlement_attribution_new"
        ).fetchone()[0]
        if post_count != pre_count:
            raise RuntimeError(
                "settlement_attribution rebuild dropped rows "
                f"({pre_count} -> {post_count}); aborting"
            )
        conn.execute("DROP TABLE settlement_attribution")
        conn.execute(
            "ALTER TABLE settlement_attribution_new RENAME TO settlement_attribution"
        )
        conn.execute("RELEASE settlement_attribution_check_rebuild")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT settlement_attribution_check_rebuild")
        raise


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create the settlement_attribution table + indexes (idempotent).

    Called by init_schema (world.db) on every boot. Forward-only: CREATE IF NOT
    EXISTS + ALTER ADD COLUMN for any migration entry whose column is absent +
    a guarded rebuild that upgrades a stale category CHECK in place.
    """
    conn.execute(CREATE_SETTLEMENT_ATTRIBUTION_SQL)
    # Column ALTERs run BEFORE the category-CHECK rebuild: the rebuild's
    # `INSERT INTO new SELECT * FROM old` copies positionally, so an old table
    # missing a column added since (e.g. world_grade_pnl_usd) must be upgraded to
    # the CURRENT column set first, or the copy's column count would mismatch the
    # freshly-created `_new` table (which always has every current column).
    existing = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_info(settlement_attribution)"
        ).fetchall()
    }
    for column, ddl in _COLUMN_MIGRATIONS.items():
        if column not in existing:
            conn.execute(ddl)
    _rebuild_stale_category_check(conn)
    conn.execute(CREATE_CATEGORY_INDEX_SQL)
    conn.execute(CREATE_CITY_INDEX_SQL)
    conn.execute(CREATE_SETTLED_INDEX_SQL)
    conn.execute(CREATE_SUPERSESSIONS_SQL)
    conn.execute(CREATE_SUPERSESSIONS_POSITION_INDEX_SQL)
