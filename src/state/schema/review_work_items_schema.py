# Created: 2026-07-11
# Last reused or audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md "Consult adjudication"
#   (adopted target shape); src/contracts/review_work_item.py

"""Schema owner for review_work_items — the owner-local ReviewWorkItem table.

One shape, instantiated PER OWNING DB (adjudication): trade DB first (this packet;
T2/T4/T5/T2b consume it there). ``ensure_table`` takes a bare ``conn`` and creates the
identical shape regardless of which physical DB it is called against — a later packet
calls this SAME function against a forecasts/world connection when a forecasts- or
world-owned fact needs its own local work-item table. Nothing here is trade-DB-specific.

Columns map 1:1 to src.contracts.review_work_item.ReviewWorkItem, with ``family_key``
flattened into four columns (family_city/family_target_date/family_temperature_metric/
family_market_family_id, all NULL together when a work item is not family-scoped) so the
family-block read path (blocked_family_keys / open_items_by_family in
src/state/review_work_items.py) can index and filter directly instead of parsing a blob.

``reason_code`` is a plain TEXT column with NO CHECK constraint tying it to
ReviewReasonCode's current members — SQLite cannot ALTER a CHECK, and a closed CHECK
here would force a table-rebuild migration every time a later excision packet adds a
reason code, recreating exactly the churn this excision removes elsewhere (T1's dropped
disposition CHECK). Reason-code validity is enforced in Python at construction
(ReviewWorkItem.__post_init__). ``status`` IS CHECK-constrained: OPEN/RESOLVED/SUPERSEDED
is a genuinely closed, stable lifecycle (unlike the extensible reason vocabulary).

Partial unique index on (owner_table, subject_id, reason_code, authority_revision)
WHERE status='OPEN' is the idempotent-open guarantee (INSERT OR IGNORE via
src.state.review_work_items.open_work_item): two callers opening the "same" work item
concurrently converge on one row, enforced by SQLite itself, not by application locking.

INV-37: caller supplies conn; never auto-opens.
"""

from __future__ import annotations

import sqlite3

TABLE_NAME = "review_work_items"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS review_work_items (
    work_id                     TEXT PRIMARY KEY,
    owner_domain                TEXT NOT NULL,
    owner_table                 TEXT NOT NULL,
    subject_id                  TEXT NOT NULL,
    reason_code                 TEXT NOT NULL,
    authority_revision          INTEGER NOT NULL DEFAULT 0,
    evidence_refs_json          TEXT NOT NULL DEFAULT '[]',
    evidence_hash               TEXT NOT NULL DEFAULT '',
    first_seen_at               TEXT NOT NULL,
    last_seen_at                TEXT NOT NULL,
    family_city                 TEXT,
    family_target_date          TEXT,
    family_temperature_metric   TEXT,
    family_market_family_id     TEXT,
    exposure_bound_usd          REAL,
    unbounded                   INTEGER NOT NULL DEFAULT 0 CHECK (unbounded IN (0, 1)),
    attempt_count                INTEGER NOT NULL DEFAULT 0,
    next_attempt_at             TEXT NOT NULL,
    priority                    INTEGER NOT NULL DEFAULT 100,
    last_error_class            TEXT NOT NULL DEFAULT '',
    last_error_detail           TEXT NOT NULL DEFAULT '',
    status                      TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'RESOLVED', 'SUPERSEDED')),
    resolver_identity           TEXT NOT NULL DEFAULT '',
    resolution_evidence         TEXT NOT NULL DEFAULT '',
    resolved_at                 TEXT,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,
    CHECK (
        (unbounded = 1 AND exposure_bound_usd IS NULL)
        OR (unbounded = 0 AND exposure_bound_usd IS NOT NULL AND exposure_bound_usd >= 0)
    )
)
"""

# Idempotent-open guarantee: only ONE OPEN row may exist per
# (owner_table, subject_id, reason_code, authority_revision).
CREATE_UNIQUE_OPEN_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_review_work_items_open_identity
    ON review_work_items(owner_table, subject_id, reason_code, authority_revision)
    WHERE status = 'OPEN'
"""

# Due-work scheduler: status='OPEN' AND next_attempt_at<=? ORDER BY priority, next_attempt_at.
CREATE_DUE_WORK_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_review_work_items_due_work
    ON review_work_items(status, next_attempt_at)
"""

# Family-block read path: open_items_by_family / blocked_family_keys filter on
# (status, family_city, family_target_date, family_temperature_metric).
CREATE_FAMILY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_review_work_items_family
    ON review_work_items(status, family_city, family_target_date, family_temperature_metric)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Idempotent DDL for review_work_items. Callable against any owning-DB conn.

    INV-37: caller supplies conn; never auto-opens.
    """

    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_UNIQUE_OPEN_INDEX_SQL)
    conn.execute(CREATE_DUE_WORK_INDEX_SQL)
    conn.execute(CREATE_FAMILY_INDEX_SQL)


__all__ = ["TABLE_NAME", "CREATE_TABLE_SQL", "ensure_table"]
