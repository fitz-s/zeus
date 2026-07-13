# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T1 (GATED
#   verdict) + docs/rebuild/census_local_ledger/census_chain_sources.md
#   ("Resolution payouts — NEEDS NEW INGESTER").

"""Schema owner for payout_observations — the append-only ConditionalTokens
payout observation log (LX-T1).

One row per (condition_id, outcome_index) observation, written by
src.ingest.payout_observer. Mirrors the review_work_items_schema.py shape:
``ensure_table`` takes a bare ``conn`` and is idempotent, trade-DB-owned.

Rows are immutable facts EXCEPT the ``superseded_by`` pointer, which may
transition exactly once from NULL to a later row's ``id`` when a fresh
observation supersedes it (value changed, or a reorg produced a different
block_hash for a resolved condition). This is a forward-only bookkeeping
pointer, not an edit of the observation itself — the substantive columns
(payout_numerator/payout_denominator/state/block_number/block_hash/
observed_at/source) can never change once written. Enforced at the DB level
by ``payout_observations_guarded_update`` (BEFORE UPDATE trigger) and
``payout_observations_no_delete`` (BEFORE DELETE trigger) so the invariant
holds regardless of which Python path writes the row.

``state`` is one of UNKNOWN / UNRESOLVED / RESOLVED_ZERO / RESOLVED_NONZERO
(LX-T1 adjudication: missing/timeout/partial/unparsable data classifies
UNKNOWN, NEVER a fabricated zero payout). The table CHECK constraint ties
state to payout_denominator/payout_numerator so an inconsistent row (e.g.
state=RESOLVED_NONZERO with payout_numerator=0) cannot be inserted at all.

There is deliberately NO unique index enforcing "at most one active row per
group" at the schema level: the natural write order is read-prior ->
INSERT-new -> UPDATE-prior.superseded_by=new.id, and a partial unique index
on ``superseded_by IS NULL`` would reject the INSERT before the prior row can
be closed out (both rows transiently share superseded_by IS NULL between the
INSERT and the UPDATE). Atomicity is instead the whole-transaction property:
src.ingest.payout_observer.append_observation performs both statements in the
SAME caller-supplied transaction, so a crash between them rolls back both
(never partially superseded). The "current" observation for a
(condition_id, outcome_index) is `ORDER BY id DESC LIMIT 1`; superseded_by
is kept as a queryable audit trail (which fact replaced which), not the
selection mechanism.

INV-37: caller supplies conn; never auto-opens.
"""

from __future__ import annotations

import sqlite3

TABLE_NAME = "payout_observations"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS payout_observations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id        TEXT NOT NULL,
    outcome_index       INTEGER NOT NULL,
    payout_numerator    INTEGER,
    payout_denominator  INTEGER,
    state               TEXT NOT NULL CHECK (state IN (
        'UNKNOWN', 'UNRESOLVED', 'RESOLVED_ZERO', 'RESOLVED_NONZERO'
    )),
    block_number        INTEGER,
    block_hash          TEXT,
    observed_at         TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT 'chain_rpc',
    superseded_by       INTEGER REFERENCES payout_observations(id),
    CHECK (
        (state = 'UNKNOWN')
        OR (state = 'UNRESOLVED' AND payout_denominator = 0)
        OR (
            state IN ('RESOLVED_ZERO', 'RESOLVED_NONZERO')
            AND payout_denominator IS NOT NULL AND payout_denominator > 0
            AND payout_numerator IS NOT NULL
            AND (
                (state = 'RESOLVED_ZERO' AND payout_numerator = 0)
                OR (state = 'RESOLVED_NONZERO' AND payout_numerator > 0)
            )
        )
    )
)
"""

# "Current" observation for a group = ORDER BY id DESC LIMIT 1 (see module
# docstring for why this isn't a unique-partial-index invariant instead).
CREATE_ACTIVE_LOOKUP_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_payout_observations_active_lookup
    ON payout_observations(condition_id, outcome_index, superseded_by)
"""

# Sweep read path: "give me every condition's current observations."
CREATE_LOOKUP_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_payout_observations_condition
    ON payout_observations(condition_id, outcome_index, id)
"""

# Only a one-time NULL -> non-NULL superseded_by transition is legal; every
# other column is frozen once written.
CREATE_GUARDED_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS payout_observations_guarded_update
BEFORE UPDATE ON payout_observations
FOR EACH ROW
WHEN NOT (
    OLD.superseded_by IS NULL
    AND NEW.superseded_by IS NOT NULL
    AND NEW.condition_id IS OLD.condition_id
    AND NEW.outcome_index IS OLD.outcome_index
    AND NEW.payout_numerator IS OLD.payout_numerator
    AND NEW.payout_denominator IS OLD.payout_denominator
    AND NEW.state IS OLD.state
    AND NEW.block_number IS OLD.block_number
    AND NEW.block_hash IS OLD.block_hash
    AND NEW.observed_at = OLD.observed_at
    AND NEW.source = OLD.source
)
BEGIN
    SELECT RAISE(ABORT, 'payout_observations rows are immutable except a one-time superseded_by transition');
END
"""

CREATE_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS payout_observations_no_delete
BEFORE DELETE ON payout_observations
BEGIN
    SELECT RAISE(ABORT, 'payout_observations is append-only (delete forbidden)');
END
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Idempotent DDL for payout_observations. Callable against any trade-DB conn.

    INV-37: caller supplies conn; never auto-opens.
    """

    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_ACTIVE_LOOKUP_INDEX_SQL)
    conn.execute(CREATE_LOOKUP_INDEX_SQL)
    conn.execute(CREATE_GUARDED_UPDATE_TRIGGER_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)


__all__ = ["TABLE_NAME", "CREATE_TABLE_SQL", "ensure_table"]
