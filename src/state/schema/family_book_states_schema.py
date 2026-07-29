# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   redesign after deep-review NO-GO (see PLAN.md "2026-07-29 redesign" section):
#   FamilyBook.book_hash (src/execution/family_book.py compute_book_hash) hashes
#   captured_at_utc, and the live bridge sets captured_at_utc=decision_time
#   (src/engine/qkernel_spine_bridge.py:1787), so it can never serve as a
#   content-dedup key -- every decision cycle produces a distinct book_hash even
#   for a byte-identical book. This table's content_hash is a SEPARATE,
#   versioned, timestamp-free hash over exactly the persisted manifest.
"""family_book_states -- immutable, content-addressed manifest of a decided
family's order-book surface.

MANIFEST, not a ladder duplication: each bin entry references the existing
``executable_market_snapshots`` row (immutable, append-only, excluded from the
one archival/delete tool in the repo -- see PLAN.md STEP 0) that the family-book
builder already read off the proof, rather than re-serializing the ladder.

``content_hash`` covers ONLY the fields whose change means the observable book
actually changed (per-bin ``raw_orderbook_hash`` + venue identity/execution
metadata) -- it deliberately EXCLUDES the per-bin ``executable_snapshot_id``
and ``source_captured_at`` (those change on every fresh capture even when the
book's content is byte-identical, which is exactly the failure this table
replaces). ``canonical_payload`` stores ONLY those same content-identity
fields (round-3 review X3: a first-seen ``executable_snapshot_id``/
``source_captured_at`` here would be silently reused by every LATER
observation of the same content, misrepresenting that observation's actual
capture provenance) -- per-observation snapshot identity/capture time lives
on EACH ``family_book_observations`` row instead
(``source_manifest_json``), never here.

EVIDENCE ONLY -- never decision authority.
"""

from __future__ import annotations

import sqlite3

HASH_VERSION = 1
PAYLOAD_SCHEMA_VERSION = 1
SCHEMA_VERSION = 1

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS family_book_states (
    state_id               TEXT PRIMARY KEY,
    family_id              TEXT NOT NULL,
    content_hash            TEXT NOT NULL,
    hash_version            INTEGER NOT NULL,
    topology_hash           TEXT NOT NULL,
    complete_book           INTEGER NOT NULL CHECK (complete_book IN (0, 1)),
    canonical_payload       TEXT NOT NULL,
    payload_schema_version  INTEGER NOT NULL,
    first_seen_decision_time TEXT NOT NULL,
    schema_version          INTEGER NOT NULL
)
"""

# The dedup key: an unchanged book (same content_hash for this family) writes
# zero new rows. Targeted ON CONFLICT DO NOTHING (not a statement-wide INSERT
# OR IGNORE) so an unrelated PK collision or NOT NULL violation surfaces as a
# real integrity error instead of masquerading as a dedup hit.
CREATE_UNIQUE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_family_book_states_identity
    ON family_book_states(family_id, content_hash)
"""

CREATE_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_family_book_states_no_update
BEFORE UPDATE ON family_book_states
BEGIN
    SELECT RAISE(ABORT, 'family_book_states is append-only');
END
"""

CREATE_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_family_book_states_no_delete
BEFORE DELETE ON family_book_states
BEGIN
    SELECT RAISE(ABORT, 'family_book_states is append-only');
END
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_UNIQUE_INDEX_SQL)
    conn.execute(CREATE_NO_UPDATE_TRIGGER_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)


def insert_state(
    conn: sqlite3.Connection,
    *,
    state_id: str,
    family_id: str,
    content_hash: str,
    topology_hash: str,
    complete_book: bool,
    canonical_payload: str,
    first_seen_decision_time: str,
    hash_version: int = HASH_VERSION,
    payload_schema_version: int = PAYLOAD_SCHEMA_VERSION,
) -> bool:
    """Insert one state row. Returns True iff newly inserted (content changed).

    Targeted ``ON CONFLICT(family_id, content_hash) DO NOTHING`` -- only the
    intended dedup key is swallowed; any other integrity violation (a NULL in
    a NOT NULL column, a state_id collision) raises normally.
    """
    cur = conn.execute(
        """
        INSERT INTO family_book_states (
            state_id, family_id, content_hash, hash_version, topology_hash,
            complete_book, canonical_payload, payload_schema_version,
            first_seen_decision_time, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(family_id, content_hash) DO NOTHING
        """,
        (
            state_id, family_id, content_hash, hash_version, topology_hash,
            int(complete_book), canonical_payload, payload_schema_version,
            first_seen_decision_time, SCHEMA_VERSION,
        ),
    )
    return cur.rowcount > 0
