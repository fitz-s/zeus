# Created: 2026-07-16
# Last reused/audited: 2026-10-01 (receipt order: exact UTC microseconds)
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

Append-only, no update path anywhere, ever. Revisions of one
(city, station, source, source-clock) are ordered by RECEIPT as exact integer
UTC microseconds (``RECEIPT_US_SQL``, computed from the stored text), then
``id`` only when receipts are exactly equal (the later commit wins).
``julianday`` is not used: it keeps ~1 ms and collapses distinct receipts.
Admission and every revision-selecting reader share that ordering
(``RECEIPT_ORDER_DESC_SQL``). A receipt at or after the receipt-latest
revision is suppressed only when it repeats that revision's value/unit, so
re-polls are free and A -> B -> A appends all three. A receipt older than the
receipt-latest revision is late evidence: it is kept under exact idempotency
(the unique index), never compared with an unrelated neighbour. Thus an
out-of-order 68@:10, 69@:12, 68@:11, 68@:13 still reads 68. Timestamps are
normalized to UTC ISO-8601; naive or unparseable clocks are rejected.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def receipt_us_sql(column: str) -> str:
    """Exact UTC microseconds of an aware ISO-8601 text column, as SQL.

    ``unixepoch`` resolves any offset/separator to whole UTC seconds; the
    fraction digits (truncated to six, as ``datetime.fromisoformat`` does) add
    the microseconds. Equal to ``receipt_us`` for every accepted format.
    """
    frac = f"substr({column}, 21)"
    digits = f"(length({frac}) - length(ltrim({frac}, '0123456789')))"
    return (
        f"(unixepoch({column}) * 1000000 + CASE WHEN substr({column}, 20, 1) = '.' "
        f"THEN CAST(substr(substr({frac}, 1, {digits}) || '000000', 1, 6) AS INTEGER) "
        "ELSE 0 END)"
    )


def receipt_us(value: datetime) -> int:
    """Exact UTC microseconds of an aware datetime (the Python twin)."""
    if value.tzinfo is None:
        raise ValueError(f"receipt clock is naive: {value!r}")
    return (value - _EPOCH) // timedelta(microseconds=1)


RECEIPT_US_SQL = receipt_us_sql("fetched_at_utc")
# One revision ordering for admission and readers: exact receipt, then commit.
RECEIPT_ORDER_DESC_SQL = f"{RECEIPT_US_SQL} DESC, id DESC"


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
    ON observation_prints(
        city, station_id, source_channel, publish_ts_utc, value_native, fetched_at_utc
    )
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


_IDENTITY_INDEX_COLUMNS = (
    "city", "station_id", "source_channel", "publish_ts_utc",
    "value_native", "fetched_at_utc",
)


def _ensure_identity_index(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA index_info(ux_observation_prints_identity)").fetchall()
    columns = tuple(str(row[2]) for row in rows)
    if columns and columns != _IDENTITY_INDEX_COLUMNS:
        # The legacy five-column index cannot represent A -> B -> A source
        # corrections. Dropping/recreating only the index preserves every row.
        conn.execute("DROP INDEX ux_observation_prints_identity")
    conn.execute(CREATE_UNIQUE_INDEX_SQL)


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    _ensure_identity_index(conn)
    conn.execute(CREATE_CITY_PUBLISH_INDEX_SQL)
    conn.execute(CREATE_NO_UPDATE_TRIGGER_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)


def _utc_iso(name: str, value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"observation_prints {name} is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"observation_prints {name} is naive: {value!r}")
    if parsed.utcoffset() == timedelta(0) and str(value).endswith("+00:00"):
        return str(value)  # already canonical; keep stored identities byte-stable
    return parsed.astimezone(timezone.utc).isoformat()


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
    """Append one published reading; True iff a row was inserted.

    Suppresses only a receipt no older than the receipt-latest revision of the
    same source clock that repeats its value/unit; see the module docstring.
    """
    value = float(value_native)
    publish_ts_utc = _utc_iso("publish_ts_utc", publish_ts_utc)
    fetched_at_utc = _utc_iso("fetched_at_utc", fetched_at_utc)
    cur = conn.execute(
        f"""
        INSERT OR IGNORE INTO observation_prints (
            city, station_id, source_channel, publish_ts_utc,
            value_native, unit, fetched_at_utc, raw_report, schema_version
        )
        SELECT ?, ?, ?, ?, ?, ?, ?, ?, 1
         WHERE NOT EXISTS (
            SELECT 1
              FROM (
                SELECT value_native, unit, fetched_at_utc
                  FROM observation_prints
                 WHERE city = ?
                   AND station_id = ?
                   AND source_channel = ?
                   AND publish_ts_utc = ?
                 ORDER BY {RECEIPT_ORDER_DESC_SQL}
                 LIMIT 1
              ) AS latest
             WHERE ? >= {receipt_us_sql("latest.fetched_at_utc")}
               AND latest.value_native = ?
               AND latest.unit = ?
         )
        """,
        (
            city, station_id, source_channel, publish_ts_utc,
            value, unit, fetched_at_utc, raw_report,
            city, station_id, source_channel, publish_ts_utc,
            receipt_us(datetime.fromisoformat(fetched_at_utc)), value, unit,
        ),
    )
    return cur.rowcount > 0
