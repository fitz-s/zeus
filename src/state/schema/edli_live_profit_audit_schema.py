"""EDLI live order audit schema owner (decision economics vs realized fill).

# Created: 2026-05-26
# Last audited: 2026-07-26
# Authority basis: LX-T3 (docs/rebuild/local_ledger_excision_2026-07-12.md round-2
#   delta) + 2026-07-26 operator law "the learning-curve machinery is not
#   first-principles — decouple it".

Column-shape law for this table (see src/events/live_profit_audit.py for why):
  - ``fill_alpha_gap`` / ``fill_alpha_gap_usd`` are EXECUTION QUALITY (q_live vs
    the price we paid). They carry no settlement term and are not P&L. Their
    former names ``realized_edge`` / ``edge_value_usd`` lied about that and are
    retired here.
  - ``settlement_outcome`` / ``pnl_usd`` are frozen legacy: nothing has written
    them since the LX-T3 grading writeback was removed (fe5afb2d2). They stay on
    disk as the historical 99-row corpus and are absent from every writer.
  - ``learning_eligible`` is retired: a learning-admission gate whose predicate
    compared a fill against our own belief, with no consumer anywhere. Learning
    admission belongs to SettlementResolution, which grades against settlement.
"""

from __future__ import annotations

import sqlite3


CREATE_AUDIT_SQL = """
CREATE TABLE IF NOT EXISTS edli_live_profit_audit (
    audit_id TEXT NOT NULL PRIMARY KEY,
    event_id TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    final_intent_id TEXT,
    execution_command_id TEXT,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    direction TEXT,
    side TEXT,
    q_live REAL,
    q_lcb_5pct REAL,
    expected_cost_basis REAL,
    expected_fee REAL,
    expected_spread_cost REAL,
    visible_depth_fill_lcb REAL,
    order_policy TEXT,
    native_token_side TEXT,
    expected_edge REAL,
    kelly_size_usd REAL,
    quote_seen_at TEXT,
    quote_age_ms INTEGER,
    best_bid REAL,
    best_ask REAL,
    limit_price REAL,
    order_type TEXT,
    time_in_force TEXT,
    venue_order_id TEXT,
    order_lifecycle_state TEXT NOT NULL,
    avg_fill_price REAL,
    filled_size REAL,
    fees REAL,
    fill_alpha_gap REAL,
    fill_alpha_gap_usd REAL,
    -- FROZEN LEGACY. No writer names these two. They hold the 99 settlement
    -- grades written before the LX-T3 grading writeback was removed (fe5afb2d2)
    -- and are the only surviving copy: the settlement_attribution rehome covers
    -- 0 of their 93 condition_ids with a world_grade_pnl_usd today. Physical
    -- retirement stays R7, after that corpus is rehomed. Declared so the fresh
    -- schema matches the live table; never written, never read as authority.
    settlement_outcome TEXT,
    pnl_usd REAL,
    reject_reason TEXT,
    expected_edge_source_certificate_hash TEXT,
    cost_basis_source_certificate_hash TEXT,
    fill_source_event_hash TEXT,
    settlement_source_event_hash TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    UNIQUE(aggregate_id, execution_command_id, order_lifecycle_state)
)
"""

CREATE_AGGREGATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_live_profit_audit_aggregate
    ON edli_live_profit_audit(aggregate_id, created_at)
"""

CREATE_STATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_live_profit_audit_state
    ON edli_live_profit_audit(order_lifecycle_state, created_at)
"""

# LX-E packet (2026-07-13, docs/rebuild/local_ledger_excision_2026-07-12.md Round-2
# delta adjudication "mutable learning receipts"): insert_record's
# ON CONFLICT(audit_id) DO UPDATE can silently replace an earlier analytical result,
# destroying the precise corpus that produced a historical model decision. Before
# any such overwrite, the CURRENT row's full pre-image is archived here as a JSON
# snapshot (a whole-row copy, not a mirrored column set — the smaller schema
# change: one generic table instead of duplicating every column definition).
# edli_live_profit_audit itself stays the single-row-per-natural-key "current" view
# (its read contract is unchanged — no downstream reader needs updating); this
# table is the permanent, append-only, never-updated history of every version that
# preceded it.
CREATE_SUPERSESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS edli_live_profit_audit_supersessions (
    supersession_id TEXT NOT NULL PRIMARY KEY,
    audit_id TEXT NOT NULL,
    prior_row_json TEXT NOT NULL,
    superseded_by TEXT,
    superseded_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1)
)
"""

CREATE_SUPERSESSIONS_AUDIT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_live_profit_audit_supersessions_audit
    ON edli_live_profit_audit_supersessions(audit_id, superseded_at)
"""


_COLUMN_MIGRATIONS = {
    "expected_fee": "ALTER TABLE edli_live_profit_audit ADD COLUMN expected_fee REAL",
    "expected_spread_cost": "ALTER TABLE edli_live_profit_audit ADD COLUMN expected_spread_cost REAL",
    "visible_depth_fill_lcb": "ALTER TABLE edli_live_profit_audit ADD COLUMN visible_depth_fill_lcb REAL",
    "order_policy": "ALTER TABLE edli_live_profit_audit ADD COLUMN order_policy TEXT",
    "native_token_side": "ALTER TABLE edli_live_profit_audit ADD COLUMN native_token_side TEXT",
    "expected_edge_source_certificate_hash": "ALTER TABLE edli_live_profit_audit ADD COLUMN expected_edge_source_certificate_hash TEXT",
    "cost_basis_source_certificate_hash": "ALTER TABLE edli_live_profit_audit ADD COLUMN cost_basis_source_certificate_hash TEXT",
    "fill_source_event_hash": "ALTER TABLE edli_live_profit_audit ADD COLUMN fill_source_event_hash TEXT",
    "settlement_source_event_hash": "ALTER TABLE edli_live_profit_audit ADD COLUMN settlement_source_event_hash TEXT",
    # Nullable, no DEFAULT, no CHECK: an ADD COLUMN carrying a CHECK forces a
    # full-table scan at boot, and this table lives in a 93GB live DB.
    "fill_alpha_gap": "ALTER TABLE edli_live_profit_audit ADD COLUMN fill_alpha_gap REAL",
    "fill_alpha_gap_usd": "ALTER TABLE edli_live_profit_audit ADD COLUMN fill_alpha_gap_usd REAL",
}

# Columns retired 2026-07-26 (see module docstring). Renames carry the historical
# values across so the execution-quality corpus survives under its honest name;
# the drops remove a gate that had no consumer and a size column no writer ever
# filled. All are metadata-only operations on SQLite >= 3.35 — no row rewrite.
_COLUMN_RENAMES = (
    ("realized_edge", "fill_alpha_gap"),
    ("edge_value_usd", "fill_alpha_gap_usd"),
)
_RETIRED_COLUMNS = (
    "learning_eligible",   # belief-confirming learning gate, zero consumers
    "live_cap_notional",   # 0/5368 non-null; the tiny_live cap it mirrored is deleted
    "post_fill_mark",      # 0/5368 non-null; no event or certificate carries it
)
_RETIRED_INDEXES = (
    "idx_edli_live_profit_audit_promotion",
    "idx_edli_live_profit_audit_learning",
)


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_AUDIT_SQL)

    def _columns() -> set[str]:
        return {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(edli_live_profit_audit)").fetchall()
        }

    existing = _columns()
    # Rename before ADD: an already-renamed DB skips both, and a legacy DB gets
    # its values carried over instead of a fresh empty column beside them.
    for old, new in _COLUMN_RENAMES:
        if old in existing and new not in existing:
            conn.execute(
                f"ALTER TABLE edli_live_profit_audit RENAME COLUMN {old} TO {new}"
            )
            existing = _columns()
    for column, ddl in _COLUMN_MIGRATIONS.items():
        if column not in existing:
            conn.execute(ddl)
    for index in _RETIRED_INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {index}")
    for column in _RETIRED_COLUMNS:
        if column in existing:
            conn.execute(f"ALTER TABLE edli_live_profit_audit DROP COLUMN {column}")

    conn.execute(CREATE_AGGREGATE_INDEX_SQL)
    conn.execute(CREATE_STATE_INDEX_SQL)
    conn.execute(CREATE_SUPERSESSIONS_SQL)
    conn.execute(CREATE_SUPERSESSIONS_AUDIT_INDEX_SQL)
