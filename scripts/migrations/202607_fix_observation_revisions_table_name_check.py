# Lifecycle: created=2026-07-16; last_reviewed=2026-07-16; last_reused=2026-07-16
# Purpose: rebuild observation_revisions with the post-consolidation CHECK.
#   The live DB predates the 202605 v2→canonical consolidation, so its CHECK
#   still reads table_name IN ('observation_instants_v2', 'observations').
#   Every writer since the consolidation inserts table_name =
#   'observation_instants' via INSERT OR IGNORE — the stale CHECK silently
#   swallows the row. Observed blast radius: MAX(recorded_at) = 2026-05-28;
#   zero revision audit rows (quarantine AND monotone-widening provenance)
#   have landed since. SQLite cannot ALTER a CHECK, so this is a rebuild:
#   rename → recreate with the canonical DDL (v2_schema.py) → copy with
#   table_name repointed v2→canonical (completing the consolidation's step 6,
#   which the stale CHECK made impossible to apply in place) → drop → indexes.
# Authority basis: day0 defect-ledger campaign 2026-07-16; silent-drop found
#   while verifying backfill_widened_observation_instants audit rows.
"""Rebuild observation_revisions so canonical table_name passes the CHECK.

Runner interface: def up(conn: sqlite3.Connection) -> None
"""
from __future__ import annotations

import sqlite3

TARGET_DB = "world"

_TABLE = "observation_revisions"
_OLD = "observation_revisions_stale_check_tmp"

_CANONICAL_DDL = """
    CREATE TABLE observation_revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        table_name TEXT NOT NULL
            CHECK (table_name IN ('observation_instants', 'observations')),
        city TEXT NOT NULL,
        target_date TEXT,
        source TEXT NOT NULL,
        utc_timestamp TEXT,
        natural_key_json TEXT NOT NULL DEFAULT '{}',
        existing_row_id INTEGER,
        existing_payload_hash TEXT,
        incoming_payload_hash TEXT NOT NULL,
        reason TEXT NOT NULL,
        writer TEXT NOT NULL,
        existing_row_json TEXT NOT NULL,
        incoming_row_json TEXT NOT NULL,
        recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
    )
"""

_COLUMNS = (
    "id, table_name, city, target_date, source, utc_timestamp, "
    "natural_key_json, existing_row_id, existing_payload_hash, "
    "incoming_payload_hash, reason, writer, existing_row_json, "
    "incoming_row_json, recorded_at"
)


def _table_check_sql(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return str(row[0]) if row and row[0] else ""


def up(conn: sqlite3.Connection) -> None:
    # Precondition: 202607_drop_world_collateral_unsettled_ghost dropped the
    # world-side collateral_unsettled_proceeds ghost TABLE but left the
    # trg_reservations_no_overreserve TRIGGER (on the legacy_archived, empty
    # world-side collateral_reservations) dangling against it. A dangling
    # trigger makes EVERY ALTER TABLE on the DB fail schema re-parse, so it
    # blocks this rebuild — and any INSERT into that legacy table would crash
    # with "no such table" anyway. The healthy trigger+tables live on the
    # trade DB (trade-class); the world copy is a remnant. Drop it.
    trigger_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' "
        "AND name='trg_reservations_no_overreserve'"
    ).fetchone()
    if trigger_row and "collateral_unsettled_proceeds" in str(trigger_row[0]):
        ghost_absent = (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name='collateral_unsettled_proceeds'"
            ).fetchone()[0]
            == 0
        )
        if ghost_absent:
            conn.execute("DROP TRIGGER trg_reservations_no_overreserve")

    ddl = _table_check_sql(conn, _TABLE)
    if not ddl:
        return  # table absent (fresh DB path creates it correctly)
    if "'observation_instants_v2'" not in ddl:
        return  # CHECK already canonical — idempotent no-op

    conn.execute("SAVEPOINT obs_revisions_check_rebuild")
    try:
        conn.execute(f"ALTER TABLE {_TABLE} RENAME TO {_OLD}")
        conn.execute(_CANONICAL_DDL)
        # Copy everything, repointing the pre-consolidation table_name to the
        # canonical name (the consolidation migration's step 6, finally
        # applicable now that the CHECK admits the canonical value).
        conn.execute(
            f"""
            INSERT INTO {_TABLE} ({_COLUMNS})
            SELECT id,
                   CASE table_name
                       WHEN 'observation_instants_v2' THEN 'observation_instants'
                       ELSE table_name
                   END,
                   city, target_date, source, utc_timestamp,
                   natural_key_json, existing_row_id, existing_payload_hash,
                   incoming_payload_hash, reason, writer, existing_row_json,
                   incoming_row_json, recorded_at
            FROM {_OLD}
            """
        )
        old_n = conn.execute(f"SELECT COUNT(*) FROM {_OLD}").fetchone()[0]
        new_n = conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0]
        if old_n != new_n:
            raise RuntimeError(
                f"OBS_REVISIONS_REBUILD_ROW_MISMATCH:old={old_n}:new={new_n}"
            )
        conn.execute(f"DROP TABLE {_OLD}")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_observation_revisions_obs_lookup
                ON observation_revisions(table_name, city, source, utc_timestamp, recorded_at)
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_observation_revisions_payload
                ON observation_revisions(
                    table_name, city, source, target_date, utc_timestamp,
                    incoming_payload_hash, reason
                )
            """
        )
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT obs_revisions_check_rebuild")
        conn.execute("RELEASE SAVEPOINT obs_revisions_check_rebuild")
        raise
    conn.execute("RELEASE SAVEPOINT obs_revisions_check_rebuild")
    conn.commit()
