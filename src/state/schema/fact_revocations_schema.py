# Created: 2026-07-12
# Last reused or audited: 2026-07-12
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md DIQ packet
#   (Consult adjudication, GPT-5.6 Pro, "DIQ CONDITIONAL"); supersedes
#   src/state/schema/decision_integrity_quarantine_schema.py (PR-E 2026-05-22).

"""fact_revocations — the owner-local fact-revocation record table.

Quarantine excision DIQ packet (docs/rebuild/quarantine_excision_2026-07-11.md
"decision_integrity_quarantine side-table" finding): replaces the single
trade-DB-only ``decision_integrity_quarantine`` side-table with the SAME shape
(``fact_revocations``) instantiated LOCALLY in every physical DB that owns a
revocable table, per the adjudicated owner-local law ("the reshape is NOT a
boolean column: it is a precisely-named revocation record per owning DB").

The revoked tables span all three physical DBs (src/state/domains.py):
  - trade DB (zeus_trades.db):      opportunity_fact
  - world DB (zeus-world.db):       decision_certificates, decision_events,
                                     probability_trace_fact,
                                     selection_family_fact,
                                     selection_hypothesis_fact
  - forecasts DB (zeus-forecasts.db): calibration_pairs

Each physical DB gets its OWN ``fact_revocations`` table via this module's
``ensure_table`` (co-located with the facts it tags, same-transaction writes).
Shape is unchanged from the predecessor table: UNIQUE(table_name, row_id,
reason_code) preserves reason multiplicity (one row may carry multiple
coexisting revocation reasons); meta_json carries the audit payload verbatim.

INV-37: caller supplies conn; never auto-opens.
"""

from __future__ import annotations

import sqlite3

# Schema version bump owned by db.py's per-DB SCHEMA_VERSION (this module does
# not stamp its own version — mirrors review_work_items_schema.py precedent).

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fact_revocations (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name               TEXT NOT NULL,
    row_id                   TEXT NOT NULL,
    reason_code              TEXT NOT NULL,
    forecast_snapshot_id     TEXT,
    recorded_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
    meta_json                TEXT NOT NULL DEFAULT '{}',
    UNIQUE(table_name, row_id, reason_code)
)
"""

CREATE_INDEX_TABLE_ROW_SQL = """
CREATE INDEX IF NOT EXISTS idx_fact_revocations_table_row
    ON fact_revocations(table_name, row_id)
"""

CREATE_INDEX_REASON_SQL = """
CREATE INDEX IF NOT EXISTS idx_fact_revocations_reason
    ON fact_revocations(reason_code, recorded_at)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create fact_revocations + indices on ``conn``'s main schema if absent.

    Idempotent (IF NOT EXISTS). Called from:
      1. db.py init_schema_trade_only / init_schema_world_only /
         init_schema_forecasts (daemon boot, one call per owning DB).
      2. tests: in-memory / tempfile DB setup.

    Owner-local: always creates on ``conn``'s MAIN schema — callers writing a
    revocation into an ATTACHed (non-main) schema must ensure the table exists
    on THAT connection's main (open a connection rooted at the owning DB, or
    use the qualified-schema DDL helper in the writer script) rather than
    relying on this function to reach across an ATTACH alias.

    INV-37: caller provides conn; never auto-opens.
    """
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_INDEX_TABLE_ROW_SQL)
    conn.execute(CREATE_INDEX_REASON_SQL)


def ensure_table_in_schema(conn: sqlite3.Connection, schema: str) -> None:
    """Create fact_revocations + indices in an ATTACHed ``schema`` (not main).

    Used only by cross-DB writer scripts that must create the revocation
    table on an ATTACHed alias before writing into it (e.g. a world-main
    connection with the trade DB ATTACHed as 'trade', writing an
    opportunity_fact revocation). ``schema`` must already be ATTACHed.
    """
    if not schema or not all(ch.isalnum() or ch == "_" for ch in schema):
        raise ValueError(f"unsafe sqlite schema identifier: {schema!r}")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.fact_revocations (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name               TEXT NOT NULL,
            row_id                   TEXT NOT NULL,
            reason_code              TEXT NOT NULL,
            forecast_snapshot_id     TEXT,
            recorded_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            meta_json                TEXT NOT NULL DEFAULT '{{}}',
            UNIQUE(table_name, row_id, reason_code)
        )
        """
    )
    # SQLite: schema prefix goes on the index NAME only; the ON-clause table
    # name must be unqualified (SQLite resolves it via the index's schema).
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {schema}.idx_fact_revocations_table_row "
        "ON fact_revocations(table_name, row_id)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {schema}.idx_fact_revocations_reason "
        "ON fact_revocations(reason_code, recorded_at)"
    )
