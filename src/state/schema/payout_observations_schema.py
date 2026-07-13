# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T1 (GATED
#   verdict) + docs/rebuild/census_local_ledger/census_chain_sources.md
#   ("Resolution payouts — NEEDS NEW INGESTER") + wave-1.5 repair
#   (docs/rebuild/consult_answers/local_ledger_excision_wave1_local_verifier_2026-07-13.md
#   "MINOR" — LX-T1 CHECK gap).

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
UNKNOWN, NEVER a fabricated zero payout). UNKNOWN also covers a partial RPC
observation, so either numeric component may be retained when the other is
missing; a complete payout tuple cannot still be named UNKNOWN. The table
CHECK constraint ties this grammar to payout_denominator/payout_numerator.

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

Wave-1.5 tightening: the ``state = 'UNKNOWN'`` CHECK branch used to impose no
constraint on the payout columns at all, so the DB permitted an UNKNOWN row
carrying a complete payout tuple. Tightened to require at least one missing
component whenever ``state = 'UNKNOWN'`` while preserving partial chain facts.
Because a SQLite
CHECK cannot be ALTERed, ``ensure_table`` upgrades an existing
pre-tightening table via ``_rebuild_stale_unknown_check``: a guarded
SAVEPOINT rebuild that preserves rows when any exist (mirrors
src/state/schema/exit_timing_attribution_schema.py's category-CHECK rebuild
idiom), or a plain DROP+CREATE when the table is provably empty (this table
is new this wave, so the empty case is the common one).

INV-37: caller supplies conn; never auto-opens.
"""

from __future__ import annotations

import sqlite3

TABLE_NAME = "payout_observations"

# Present in CREATE_TABLE_SQL's CHECK clause iff the UNKNOWN branch has
# already been tightened to require an incomplete payout tuple — used by
# _rebuild_stale_unknown_check to decide whether an existing on-disk table
# needs a rebuild. Kept as a literal substring of CREATE_TABLE_SQL below (not
# a separate hand-maintained copy) so the two can never drift apart silently;
# see the assertion at module import time.
_UNKNOWN_TIGHTENED_MARKER = (
    "(state = 'UNKNOWN' AND (payout_numerator IS NULL OR payout_denominator IS NULL))"
)

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
        (state = 'UNKNOWN' AND (payout_numerator IS NULL OR payout_denominator IS NULL))
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

assert _UNKNOWN_TIGHTENED_MARKER in CREATE_TABLE_SQL, (
    "_UNKNOWN_TIGHTENED_MARKER drifted from CREATE_TABLE_SQL's CHECK clause — "
    "keep them in sync or _rebuild_stale_unknown_check will never detect "
    "'already tightened' and will rebuild on every boot."
)

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


def _rebuild_stale_unknown_check(conn: sqlite3.Connection) -> None:
    """Upgrade a pre-tightening payout_observations table in place.

    No-op when the table doesn't exist yet (CREATE_TABLE_SQL below creates it
    fresh, already tightened) or already carries the tightened CHECK.
    Otherwise: if the table is provably empty, DROP+CREATE is simplest and
    equally safe (no rows to preserve, no risk of a rebuild-copy hitting a
    legacy row that violates the new invariant). If rows exist, rebuild via
    the repo's guarded-SAVEPOINT copy idiom (mirrors
    exit_timing_attribution_schema._rebuild_stale_category_check): a legacy
    UNKNOWN row carrying a complete payout tuple will make the copy's INSERT
    raise a CHECK violation. Partial rows remain truthful UNKNOWN observations
    and survive byte-for-byte.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='payout_observations'"
    ).fetchone()
    table_sql = str(row[0] if row else "")
    if not table_sql:
        return
    if _UNKNOWN_TIGHTENED_MARKER in table_sql:
        return

    count = conn.execute("SELECT COUNT(*) FROM payout_observations").fetchone()[0]

    conn.execute("SAVEPOINT payout_observations_unknown_check_rebuild")
    try:
        if count == 0:
            conn.execute("DROP TABLE payout_observations")
            conn.execute(CREATE_TABLE_SQL)
        else:
            conn.execute("DROP TABLE IF EXISTS payout_observations_new")
            conn.execute(CREATE_TABLE_SQL.replace(TABLE_NAME, f"{TABLE_NAME}_new"))
            conn.execute(
                "INSERT INTO payout_observations_new SELECT * FROM payout_observations"
            )
            post_count = conn.execute(
                "SELECT COUNT(*) FROM payout_observations_new"
            ).fetchone()[0]
            if post_count != count:
                raise RuntimeError(
                    "payout_observations rebuild dropped rows "
                    f"({count} -> {post_count}); aborting"
                )
            conn.execute("DROP TABLE payout_observations")
            legacy_alter = bool(
                conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
            )
            conn.execute("PRAGMA legacy_alter_table = ON")
            try:
                conn.execute(
                    "ALTER TABLE payout_observations_new RENAME TO payout_observations"
                )
            finally:
                conn.execute(
                    f"PRAGMA legacy_alter_table = {'ON' if legacy_alter else 'OFF'}"
                )
        conn.execute("RELEASE payout_observations_unknown_check_rebuild")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT payout_observations_unknown_check_rebuild")
        raise


def ensure_table(conn: sqlite3.Connection) -> None:
    """Idempotent DDL for payout_observations. Callable against any trade-DB conn.

    INV-37: caller supplies conn; never auto-opens.
    """

    conn.execute(CREATE_TABLE_SQL)
    _rebuild_stale_unknown_check(conn)
    conn.execute(CREATE_ACTIVE_LOOKUP_INDEX_SQL)
    conn.execute(CREATE_LOOKUP_INDEX_SQL)
    conn.execute(CREATE_GUARDED_UPDATE_TRIGGER_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)


__all__ = ["TABLE_NAME", "CREATE_TABLE_SQL", "ensure_table"]
