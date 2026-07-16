# Created: 2026-07-16
# Last reused/audited: 2026-07-16
# Authority basis: day0 defects 1-5 (Paris 2026-07-14 monotonicity regression,
#   WU-backfill-frozen hour buckets, climatology-band self-blinding, HKO
#   accumulator never folding its own spot read, Seoul binary exclusion where
#   margin-absorption already existed) — operator directive: observations are
#   a PUBLICATION STREAM; the information-faithful store is an APPEND-ONLY
#   ledger of published readings, keyed on the source's own publication
#   clock. Every prior defect in this family was some derived-state surface
#   (an hour bucket, an in-process cache, a single "current" row) discarding
#   a reading it had already seen. This table is the ledger those derived
#   views should have been VIEWS over from the start; it lands BESIDE
#   observation_instants (which stays as-is — no rewrite) and feeds the day0
#   fact reduction as one more absorbing-direction fact.
"""observation_prints — append-only ledger of published station readings.

Every row is one reading as published by its source, at the source's OWN
publication clock (never our fetch wall-clock — see day0_fast_obs.py's
existing publication-clock law for why that distinction matters). Extremes
over this ledger (MAX/MIN per city/local-day) are DERIVED, computed at read
time by ``_latest_authorized_day0_fact`` — this table stores no aggregate,
only the raw prints.

Append-only, no update path anywhere, ever: UNIQUE(city, station_id,
source_channel, publish_ts_utc, value_native) + INSERT OR IGNORE makes a
duplicate fetch of the same already-seen reading a free no-op, never a
mutation. A genuinely later, different reading for the same nominal
publish_ts_utc (rare — a source republishing a correction) is a DIFFERENT
row (the uniqueness key includes value_native), not an overwrite of the old
one — the ledger keeps both, and the derived-extreme reduction picks the
correct one via the absorbing-direction law, never trusts a value because it
arrived last.
"""

from __future__ import annotations

import sqlite3


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS observation_prints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    station_id TEXT NOT NULL,
    source_channel TEXT NOT NULL,
    publish_ts_utc TEXT NOT NULL,
    value_native REAL NOT NULL,
    unit TEXT NOT NULL,
    fetched_at_utc TEXT NOT NULL,
    raw_report TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
)
"""

CREATE_UNIQUE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_observation_prints_identity
    ON observation_prints(city, station_id, source_channel, publish_ts_utc, value_native)
"""

# Backs the day0 fact-reduction's per-(city, local day) MAX/MIN scan.
CREATE_CITY_PUBLISH_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_observation_prints_city_publish
    ON observation_prints(city, publish_ts_utc)
"""

CREATE_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_observation_prints_no_update
BEFORE UPDATE ON observation_prints
BEGIN
    SELECT RAISE(ABORT, 'observation_prints is append-only');
END
"""

CREATE_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_observation_prints_no_delete
BEFORE DELETE ON observation_prints
BEGIN
    SELECT RAISE(ABORT, 'observation_prints is append-only');
END
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_UNIQUE_INDEX_SQL)
    conn.execute(CREATE_CITY_PUBLISH_INDEX_SQL)
    conn.execute(CREATE_NO_UPDATE_TRIGGER_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)


def append_print(
    conn: sqlite3.Connection,
    *,
    city: str,
    station_id: str,
    source_channel: str,
    publish_ts_utc: str,
    value_native: float,
    unit: str,
    fetched_at_utc: str,
    raw_report: str | None = None,
) -> bool:
    """Append one published reading. Returns True if a new row was inserted,
    False if it was already present (INSERT OR IGNORE — append-only dedup,
    never a mutation)."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO observation_prints (
            city, station_id, source_channel, publish_ts_utc,
            value_native, unit, fetched_at_utc, raw_report, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            city, station_id, source_channel, publish_ts_utc,
            float(value_native), unit, fetched_at_utc, raw_report,
        ),
    )
    return cur.rowcount > 0
