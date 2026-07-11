# Created: 2026-07-11
# Last reused or audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md "Consult adjudication"
#   BLOCKER-1; src/contracts/entry_exposure_obligation.py

"""Schema owner for entry_exposure_obligations (trade DB, SIBLING to review_work_items).

Why a sibling table and not a row inside review_work_items (packet's own call,
justified here per the task): an EntryExposureObligation is a per-command EXPOSURE
FACT (BLOCKER-1's conservative-bound leg), not a review/retry queue entry — it has no
retry cadence, no CAS authority-revision resolution, no operator resolution contract,
and its natural key (``command_id``) is unconditionally unique, not scoped to
``(owner_table, subject_id, reason_code, authority_revision)`` the way a review work
item is. Folding it into review_work_items would force the two accounting queries
(``total_open_obligation_usd``, ``has_unbounded_obligation``) to filter out review-queue
rows by reason_code on every call, and would force every review-item query to filter out
exposure rows — two unrelated access patterns sharing one table recreates exactly the
"extra WHERE clause everywhere" coupling this excision removes elsewhere. A sibling
table keeps both shapes minimal and both SUM/EXISTS aggregate queries index-clean.

``family_key`` is flattened into four columns (same convention as
review_work_items_schema.py) so a later packet's family-risk exposure math can filter by
family without parsing a blob.

INV-37: caller supplies conn; never auto-opens.
"""

from __future__ import annotations

import sqlite3

TABLE_NAME = "entry_exposure_obligations"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS entry_exposure_obligations (
    command_id                  TEXT PRIMARY KEY,
    owner_domain                TEXT NOT NULL,
    token_id                    TEXT NOT NULL DEFAULT '',
    condition_id                TEXT NOT NULL DEFAULT '',
    shares                      REAL,
    cost_basis_usd               REAL,
    unbounded                   INTEGER NOT NULL DEFAULT 0 CHECK (unbounded IN (0, 1)),
    family_city                 TEXT,
    family_target_date          TEXT,
    family_temperature_metric   TEXT,
    family_market_family_id     TEXT,
    status                      TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'RESOLVED')),
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,
    resolved_at                 TEXT,
    CHECK (
        (unbounded = 1 AND shares IS NULL AND cost_basis_usd IS NULL)
        OR (
            unbounded = 0 AND shares IS NOT NULL AND cost_basis_usd IS NOT NULL
            AND shares >= 0 AND cost_basis_usd >= 0
        )
    )
)
"""

# total_open_obligation_usd / has_unbounded_obligation both filter on (status, unbounded).
CREATE_STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_entry_exposure_obligations_status_unbounded
    ON entry_exposure_obligations(status, unbounded)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Idempotent DDL for entry_exposure_obligations.

    INV-37: caller supplies conn; never auto-opens.
    """

    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_STATUS_INDEX_SQL)


__all__ = ["TABLE_NAME", "CREATE_TABLE_SQL", "ensure_table"]
