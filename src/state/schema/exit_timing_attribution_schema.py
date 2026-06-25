# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 +
#   operator mandate "sell before market notice and gain is ALSO a good trade" +
#   "EVERY real chain decision audited with reality". The entry-skill grader
#   (settlement_attribution) scores settlement payoff vs the ENTRY decision-q and
#   ignores exit proceeds, so a skillful reversal exit grades identically to a held
#   loss. This table is the SECOND, orthogonal attribution axis (exit timing).
#   Schema pattern reused from src/state/schema/settlement_attribution_schema.py
#   (CREATE IF NOT EXISTS + _COLUMN_MIGRATIONS forward-only ALTER + guarded
#   category-CHECK rebuild + ensure_table). Registry-declared in
#   architecture/db_table_ownership.yaml (db: world, world_class, created_by
#   init_schema) — an unregistered world.db table FATALs assert_db_matches_registry
#   at boot (the crash-loop incident), so the registry entry lands in the SAME change.
"""exit_timing_attribution schema owner — the exit-decision grade ledger.

One row per CLOSED (exited) position, graded by comparing the realized exit
proceeds against the counterfactual hold-to-settlement value for the shares it
closed:

    would_have_settled_value_usd = closed_shares * settlement_payoff_per_share
    net_exit_value_usd           = closed_shares * avg_exit_price - exit_fees_usd
    exit_alpha_usd               = net_exit_value_usd - would_have_settled_value_usd

This is ENTRY-INDEPENDENT (entry_cost cancels), so it composes with
settlement_attribution without double-counting:
    realized_closed_lot_pnl = hold_counterfactual_pnl + exit_alpha_usd

Sole writer: src/analysis/exit_timing_attribution.py (the exit-timing pass of the
settlement attribution job). APPEND/UPSERT-only audit evidence — never a venue
command, order truth, settlement truth, or calibration training input.
"""

from __future__ import annotations

import sqlite3


# Every exit-timing category grade_exit_timing() can emit. The CHECK pins the typed
# enum at the DB layer (an antibody, not a comment).
_CATEGORY_VALUES: tuple[str, ...] = (
    "SKILLFUL_REVERSAL_EXIT",
    "PREMATURE_EXIT_COST",
    "LUCKY_EXIT_SAVED_LOSS",
    "NEUTRAL_EXIT",
    "ADMIN_OR_RISK_EXIT_VALUE_DELTA",
    "EXIT_UNATTRIBUTABLE_SETTLEMENT_MISSING",
    "EXIT_UNATTRIBUTABLE_PROCEEDS_MISSING",
    "EXIT_UNATTRIBUTABLE_Q_MISSING",
)

CREATE_EXIT_TIMING_ATTRIBUTION_SQL = """
CREATE TABLE IF NOT EXISTS exit_timing_attribution (
    attribution_id TEXT NOT NULL PRIMARY KEY,
    position_id TEXT NOT NULL,
    condition_id TEXT,
    city TEXT,
    target_date TEXT,
    temperature_metric TEXT,
    direction TEXT,
    category TEXT NOT NULL CHECK (category IN (
        'SKILLFUL_REVERSAL_EXIT', 'PREMATURE_EXIT_COST', 'LUCKY_EXIT_SAVED_LOSS',
        'NEUTRAL_EXIT', 'ADMIN_OR_RISK_EXIT_VALUE_DELTA',
        'EXIT_UNATTRIBUTABLE_SETTLEMENT_MISSING', 'EXIT_UNATTRIBUTABLE_PROCEEDS_MISSING',
        'EXIT_UNATTRIBUTABLE_Q_MISSING'
    )),
    -- realized exit vs counterfactual hold (the exit-timing alpha).
    closed_shares REAL,
    avg_exit_price REAL,
    exit_fees_usd REAL,
    exit_reason TEXT,
    exit_q_authority_present INTEGER CHECK (exit_q_authority_present IN (0, 1)),
    settlement_won INTEGER CHECK (settlement_won IN (0, 1)),
    net_exit_value_usd REAL,
    would_have_settled_value_usd REAL,
    exit_alpha_usd REAL,                 -- NULL only when value itself is unprovable
    is_skillful INTEGER NOT NULL CHECK (is_skillful IN (0, 1)),
    counts_in_skill_denominator INTEGER NOT NULL CHECK (counts_in_skill_denominator IN (0, 1)),
    rationale TEXT,
    graded_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    UNIQUE(position_id)
)
"""

CREATE_CATEGORY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_exit_timing_attribution_category
    ON exit_timing_attribution(category, graded_at)
"""

CREATE_CITY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_exit_timing_attribution_city
    ON exit_timing_attribution(city, target_date, temperature_metric)
"""


# Forward-only column migrations (mirrors settlement_attribution_schema pattern).
_COLUMN_MIGRATIONS: dict[str, str] = {}


def _rebuild_stale_category_check(conn: sqlite3.Connection) -> None:
    """Upgrade a stale category CHECK on an existing exit_timing_attribution table.

    Idempotent: no-op when every current category already appears in the live SQL.
    The rebuild copies all rows via SELECT * under a SAVEPOINT (no column dropped).
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='exit_timing_attribution'"
    ).fetchone()
    table_sql = str(row[0] if row else "")
    if not table_sql:
        return
    if all(value in table_sql for value in _CATEGORY_VALUES):
        return

    conn.execute("SAVEPOINT exit_timing_attribution_check_rebuild")
    try:
        conn.execute("DROP TABLE IF EXISTS exit_timing_attribution_new")
        conn.execute(
            CREATE_EXIT_TIMING_ATTRIBUTION_SQL.replace(
                "exit_timing_attribution", "exit_timing_attribution_new", 1
            )
        )
        pre_count = conn.execute(
            "SELECT COUNT(*) FROM exit_timing_attribution"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO exit_timing_attribution_new "
            "SELECT * FROM exit_timing_attribution"
        )
        post_count = conn.execute(
            "SELECT COUNT(*) FROM exit_timing_attribution_new"
        ).fetchone()[0]
        if post_count != pre_count:
            raise RuntimeError(
                "exit_timing_attribution rebuild dropped rows "
                f"({pre_count} -> {post_count}); aborting"
            )
        conn.execute("DROP TABLE exit_timing_attribution")
        conn.execute(
            "ALTER TABLE exit_timing_attribution_new RENAME TO exit_timing_attribution"
        )
        conn.execute("RELEASE exit_timing_attribution_check_rebuild")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT exit_timing_attribution_check_rebuild")
        raise


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create the exit_timing_attribution table + indexes (idempotent).

    Called by init_schema (world.db) on every boot. Forward-only: CREATE IF NOT
    EXISTS + ALTER ADD COLUMN for any migration entry whose column is absent + a
    guarded rebuild that upgrades a stale category CHECK in place.
    """
    conn.execute(CREATE_EXIT_TIMING_ATTRIBUTION_SQL)
    _rebuild_stale_category_check(conn)
    existing = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_info(exit_timing_attribution)"
        ).fetchall()
    }
    for column, ddl in _COLUMN_MIGRATIONS.items():
        if column not in existing:
            conn.execute(ddl)
    conn.execute(CREATE_CATEGORY_INDEX_SQL)
    conn.execute(CREATE_CITY_INDEX_SQL)
