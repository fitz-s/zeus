# Created: 2026-08-24
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md item 3
#   (candidate-set provenance) + tier0_selection_lift_preregistration_2026-08-24.md
#   (frozen consumer — the data requirements section this table satisfies).
"""tier0_candidate_set_provenance schema owner.

Append-only per-auction-candidate provenance: for every completed global
single-order auction cut that selects a real winner, one row per evaluated
candidate (selected and rejected alike). Sole writer:
src.engine.global_batch_runtime._persist_tier0_candidate_set (called from
_store_global_auction_receipt, same trade-DB connection and transaction as
the existing global auction receipt write — K1/INV-37 single-DB write).

Column names/types match the frozen interface contract documented in
scripts/selection_lift_report.py (written 2026-08-24 against this table
before it existed) so that loader's SELECT and Candidate construction work
unchanged. Extra columns beyond that contract (family_key, bin_id, token_id,
action, p0_source, rejection_reason, decision_at_utc, selection_epoch_identity,
created_at) are additional provenance the contract does not read but does not
break on either — sqlite ignores unselected columns.

``settled_y`` is always written NULL here (side settlement is unknown at
decision time); a later settlement-join process, not part of this table's
writer, is responsible for ever populating it. Its presence and NULL default
matches the frozen preregistration's "NULL if unsettled" contract.
"""

from __future__ import annotations

import sqlite3


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tier0_candidate_set_provenance (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    selection_epoch_identity TEXT NOT NULL,
    decision_at_utc TEXT NOT NULL,
    city_date_group_id TEXT NOT NULL,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    family_key TEXT NOT NULL,
    bin_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    token_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('BUY', 'SELL')),
    p0 REAL,
    p0_source TEXT,
    lead_bucket TEXT,
    eligible INTEGER NOT NULL CHECK (eligible IN (0, 1)),
    rejection_reason TEXT,
    selected INTEGER NOT NULL CHECK (selected IN (0, 1)),
    market_key TEXT NOT NULL,
    settled_y INTEGER CHECK (settled_y IN (0, 1) OR settled_y IS NULL),
    created_at TEXT NOT NULL,
    UNIQUE (selection_epoch_identity, candidate_id)
)
"""

CREATE_GROUP_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_tier0_candidate_set_group
    ON tier0_candidate_set_provenance (city_date_group_id)
"""

CREATE_EPOCH_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_tier0_candidate_set_epoch
    ON tier0_candidate_set_provenance (selection_epoch_identity)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_GROUP_INDEX_SQL)
    conn.execute(CREATE_EPOCH_INDEX_SQL)
