# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md Round-2 delta
#   adjudication (edli_live_profit_audit ruling, §(c)) + LX-0R schema definition list.
#   Rehomes the position/command -> decision-certificate-hash link OFF the
#   settlement-time (condition_id, direction) -> latest edli_live_profit_audit row
#   inference (14 (condition,direction) pairs map to >1 distinct certificate hash;
#   "ORDER BY created_at DESC LIMIT 1" there is a guess). The permanent link is
#   written HERE, at decision/command creation time (src/state/venue_command_repo.py
#   insert_command), never inferred after the fact.
"""position_decision_attribution schema owner — the permanent decision-certificate link.

One row per COMMAND. ENTRY records its actionable certificate; later commands reuse
that exact position-entry certificate so every nonterminal venue action remains
anchored to an extant decision proof.

Append-only law: UNIQUE(command_id, position_id); a command's attribution is never
overwritten by a later call.

Two distinct resolutions, both explicit (never a silent NULL guess):
  ATTRIBUTED      — decision_certificate_hash is the exact hash resolved either at
                     live command-creation time (source=LIVE_DECISION) or via the
                     EXACT command_id -> edli_live_profit_audit.execution_command_id
                     backfill join (source=BACKFILL).
  UNATTRIBUTABLE  — no exact link could be established (backfill only): the position
                     predates command-level audit tracking, or the exact command_id
                     join returned zero or more than one distinct certificate hash.
                     decision_certificate_hash is NULL; resolution_reason names why.

Absence of a row entirely means the position predates this table's existence AND the
one-time backfill has not (yet) covered it — the reader falls back to the legacy
(condition_id, direction) inference for those, logging the fallback. A present
UNATTRIBUTABLE row is never guessed around.
"""

from __future__ import annotations

import sqlite3


CREATE_POSITION_DECISION_ATTRIBUTION_SQL = """
CREATE TABLE IF NOT EXISTS position_decision_attribution (
    attribution_id TEXT NOT NULL PRIMARY KEY,
    position_id TEXT NOT NULL,
    command_id TEXT,
    decision_certificate_hash TEXT,
    resolution TEXT NOT NULL CHECK (resolution IN ('ATTRIBUTED', 'UNATTRIBUTABLE')),
    resolution_reason TEXT,
    source TEXT NOT NULL CHECK (source IN ('LIVE_DECISION', 'BACKFILL')),
    intent_kind TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    UNIQUE(command_id, position_id),
    CHECK (
        (resolution = 'ATTRIBUTED' AND command_id IS NOT NULL
         AND decision_certificate_hash IS NOT NULL)
        OR
        (resolution = 'UNATTRIBUTABLE' AND decision_certificate_hash IS NULL)
    )
)
"""

CREATE_COMMAND_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_position_decision_attribution_command
    ON position_decision_attribution(command_id)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create position_decision_attribution + indexes (idempotent)."""
    conn.execute(CREATE_POSITION_DECISION_ATTRIBUTION_SQL)
    conn.execute(CREATE_COMMAND_INDEX_SQL)
