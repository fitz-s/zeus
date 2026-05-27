"""EDLI live realized-edge audit schema owner."""

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
    live_cap_notional REAL,
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
    post_fill_mark REAL,
    settlement_outcome TEXT,
    realized_edge REAL,
    edge_value_usd REAL,
    pnl_usd REAL,
    reject_reason TEXT,
    expected_edge_source_certificate_hash TEXT,
    cost_basis_source_certificate_hash TEXT,
    fill_source_event_hash TEXT,
    settlement_source_event_hash TEXT,
    promotion_eligible INTEGER NOT NULL DEFAULT 0 CHECK (promotion_eligible IN (0,1)),
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

CREATE_PROMOTION_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_live_profit_audit_promotion
    ON edli_live_profit_audit(promotion_eligible, order_lifecycle_state, created_at)
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
    "promotion_eligible": "ALTER TABLE edli_live_profit_audit ADD COLUMN promotion_eligible INTEGER NOT NULL DEFAULT 0 CHECK (promotion_eligible IN (0,1))",
    "edge_value_usd": "ALTER TABLE edli_live_profit_audit ADD COLUMN edge_value_usd REAL",
}


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_AUDIT_SQL)
    existing = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(edli_live_profit_audit)").fetchall()
    }
    for column, ddl in _COLUMN_MIGRATIONS.items():
        if column not in existing:
            conn.execute(ddl)
    conn.execute(CREATE_AGGREGATE_INDEX_SQL)
    conn.execute(CREATE_STATE_INDEX_SQL)
    conn.execute(CREATE_PROMOTION_INDEX_SQL)
