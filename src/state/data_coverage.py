"""K2 data-coverage ledger: immune-system memory for live data ingestion.

This module is the single API for recording and querying which
(data_table × city × data_source × target_date × sub_key) rows exist in Zeus's
four upstream data tables (observations, observation_instants, solar_daily,
forecasts). It does NOT write to those data tables directly; instead, live
appenders call `record_written` after a successful data-table INSERT, and the
hole scanner calls `find_pending_fills` to decide what to re-fetch.

Why a separate module from src/state/db.py:
- db.py owns the schema (the CREATE TABLE lives there in init_schema).
- This module owns the *semantics*: type-safe enums, upsert helpers, scanner
  query. Callers never build raw SQL against data_coverage — they go through
  these functions, which makes the four-state state machine (WRITTEN →
  LEGITIMATE_GAP / FAILED / MISSING → retry → WRITTEN) auditable in one file.

Distinct from `availability_fact` table (also in db.py): that table logs
runtime outages at the cycle/candidate/order/chain level. This table indexes
which physical data rows have been ingested into the 4 backfillable tables.
Two different observability surfaces; must not conflate them.

Design note on sub_key:
Most data tables key on (city, source, target_date). Forecasts adds a
`lead_days` dimension, so per-lead coverage needs one row per lead. `sub_key`
is the escape hatch: empty string for daily tables, str(lead_days) for
forecasts. This keeps the schema single-table while preserving per-lead
tracking granularity.
"""
from __future__ import annotations

import enum
import sqlite3
from datetime import date, datetime, timezone
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# Type-safe enums — prevent string typos at call sites
# ---------------------------------------------------------------------------


class DataTable(str, enum.Enum):
    """The four physical data tables the hole scanner watches."""

    OBSERVATIONS = "observations"
    OBSERVATION_INSTANTS = "observation_instants"
    SOLAR_DAILY = "solar_daily"
    FORECASTS = "forecasts"


class CoverageStatus(str, enum.Enum):
    """State of one expected (city, source, target_date) row.

    State machine:
    - WRITTEN: data row exists in the data_table; coverage is confirmed.
    - LEGITIMATE_GAP: no row exists AND none will ever exist because the data
      source legitimately lacks it (HKO '#' incomplete flag, UKMO pre-2024-08-04,
      new-city onboard date). Scanner must NOT re-attempt.
    - FAILED: fetch attempt failed with transient error (HTTP 429, connection
      reset); `retry_after` gates the next attempt.
    - MISSING: scanner detected this expected row is absent and has not been
      attempted yet (or a prior FAILED has exceeded retry_after). Live
      appenders or scanner will pick it up on next tick.
    """

    WRITTEN = "WRITTEN"
    LEGITIMATE_GAP = "LEGITIMATE_GAP"
    FAILED = "FAILED"
    MISSING = "MISSING"


# Canonical reason strings — callers should use these constants, not free-form
# text, so the scanner and operator dashboards can aggregate reliably.
class CoverageReason:
    # LEGITIMATE_GAP reasons — permanent, scanner must not retry.
    HKO_INCOMPLETE_FLAG = "HKO_INCOMPLETE_FLAG"  # HKO returned "#" for the day
    HKO_UNAVAILABLE_FLAG = "HKO_UNAVAILABLE_FLAG"  # HKO returned "***"
    UKMO_PRE_START = "UKMO_PRE_START"  # before 2024-08-04 UKMO availability window
    CITY_NOT_YET_ONBOARDED = "CITY_NOT_YET_ONBOARDED"  # target_date < city.onboarded_at
    SOURCE_NOT_PUBLISHED_YET = "SOURCE_NOT_PUBLISHED_YET"  # HKO month not yet published
    GUARD_REJECTED = "GUARD_REJECTED"  # IngestionGuard raised — deterministic
    # Guard rejections are LEGITIMATE_GAP, not FAILED: the rejection is
    # deterministic (the guard will raise the same way on retry with the
    # same data). If a future guard-code change makes a previously-rejected
    # row newly acceptable, a separate re-ingest pass (not the scanner's
    # retry loop) is the right mechanism — the coverage ledger should not
    # be a retry loop for permanent states.

    # FAILED reasons — transient, retry after embargo.
    AUTH_ERROR = "AUTH_ERROR"
    HTTP_429 = "HTTP_429"
    HTTP_5XX = "HTTP_5XX"
    NETWORK_ERROR = "NETWORK_ERROR"
    PARSE_ERROR = "PARSE_ERROR"

    # MISSING reasons — scanner-detected holes awaiting first fetch.
    SCANNER_DETECTED = "SCANNER_DETECTED"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_target_date(target_date: date | str) -> str:
    if isinstance(target_date, date):
        return target_date.isoformat()
    # already a string — trust it, but validate shape cheaply
    if len(target_date) < 10 or target_date[4] != "-" or target_date[7] != "-":
        raise ValueError(f"target_date must be ISO YYYY-MM-DD, got {target_date!r}")
    return target_date


# ---------------------------------------------------------------------------
# Upsert helpers — one per terminal state
# ---------------------------------------------------------------------------


#: State-transition rules encoded as a WHERE clause on the ON CONFLICT DO
#: UPDATE. Without this guard, the upsert would unconditionally overwrite
#: `status`, which lets FAILED downgrade WRITTEN (reviewer S1b on
#: hourly_instants_append's chunk-failure path) and lets a subtle
#: parser-bug WRITTEN overwrite a pre-pinned LEGITIMATE_GAP (reviewer
#: S1a on forecasts_append's UKMO pre-retro edge case). The WHERE clause
#: below is the single root-cause fix for both findings.
#:
#: Allowed transitions (old → new):
#:   MISSING         → anything (MISSING is the placeholder state)
#:   FAILED          → WRITTEN (success), LEGITIMATE_GAP (upstream gives
#:                     up), FAILED (refresh retry_after). NOT MISSING.
#:   WRITTEN         → WRITTEN (refresh fetched_at), LEGITIMATE_GAP
#:                     (upstream retroactively invalidates the day —
#:                     HKO "C"→"#" flip). NOT FAILED, NOT MISSING.
#:   LEGITIMATE_GAP  → LEGITIMATE_GAP (refresh reason/fetched_at).
#:                     Terminal for all other inputs — if HKO/UKMO/the
#:                     scanner has said "this row will never exist", a
#:                     later success or failure cannot un-say it. A
#:                     code change that makes previously-pinned rows
#:                     newly fetchable requires an explicit re-ingest
#:                     pass, not the upsert path.
#:
#: Truth table under the WHERE clause below:
#:   old\new        WRITTEN  LEGIT_GAP  FAILED  MISSING
#:   MISSING        ✓        ✓          ✓       ✓
#:   FAILED         ✓        ✓          ✓       ✗
#:   WRITTEN        ✓        ✓          ✗       ✗
#:   LEGIT_GAP      ✗        ✓          ✗       ✗
_UPSERT_SQL = """
INSERT INTO data_coverage (
    data_table, city, data_source, target_date, sub_key,
    status, reason, fetched_at, expected_at, retry_after
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (data_table, city, data_source, target_date, sub_key)
DO UPDATE SET
    status = excluded.status,
    reason = excluded.reason,
    fetched_at = excluded.fetched_at,
    expected_at = COALESCE(excluded.expected_at, data_coverage.expected_at),
    retry_after = excluded.retry_after
WHERE
    -- WRITTEN wins over MISSING, FAILED, and other WRITTEN; never
    -- overwrites LEGITIMATE_GAP.
    (excluded.status = 'WRITTEN'
     AND data_coverage.status != 'LEGITIMATE_GAP')
    -- LEGITIMATE_GAP is the terminal authoritative state — always wins.
    OR (excluded.status = 'LEGITIMATE_GAP')
    -- Anything can overwrite MISSING (the placeholder state).
    OR (data_coverage.status = 'MISSING')
    -- FAILED can refresh retry_after on FAILED, but cannot overwrite
    -- WRITTEN or LEGITIMATE_GAP.
    OR (excluded.status = 'FAILED'
        AND data_coverage.status NOT IN ('WRITTEN', 'LEGITIMATE_GAP'))
"""


def record_written(
    conn: sqlite3.Connection,
    *,
    data_table: DataTable,
    city: str,
    data_source: str,
    target_date: date | str,
    sub_key: str = "",
    expected_at: date | str | None = None,
) -> None:
    """Mark one (table × city × source × date) row as successfully ingested.

    Called by live appenders immediately after the data-table INSERT succeeds
    in the same transaction. Flips any prior MISSING/FAILED row to WRITTEN.
    Idempotent — calling multiple times for the same row is a no-op (last
    fetched_at wins).
    """
    conn.execute(
        _UPSERT_SQL,
        (
            data_table.value,
            city,
            data_source,
            _coerce_target_date(target_date),
            sub_key,
            CoverageStatus.WRITTEN.value,
            None,
            _now_utc_iso(),
            _coerce_target_date(expected_at) if expected_at else None,
            None,
        ),
    )


def record_legitimate_gap(
    conn: sqlite3.Connection,
    *,
    data_table: DataTable,
    city: str,
    data_source: str,
    target_date: date | str,
    reason: str,
    sub_key: str = "",
) -> None:
    """Pin a row as a permanent legitimate gap so the scanner stops re-attempting.

    Use when the data source confirms this row will never exist: HKO "#"
    incomplete flag, UKMO pre-start date, city.onboarded_at > target_date,
    source API confirms this period is unpublished.

    `reason` must be one of `CoverageReason.*` constants to keep aggregation
    stable.
    """
    conn.execute(
        _UPSERT_SQL,
        (
            data_table.value,
            city,
            data_source,
            _coerce_target_date(target_date),
            sub_key,
            CoverageStatus.LEGITIMATE_GAP.value,
            reason,
            _now_utc_iso(),
            None,
            None,
        ),
    )


def record_failed(
    conn: sqlite3.Connection,
    *,
    data_table: DataTable,
    city: str,
    data_source: str,
    target_date: date | str,
    reason: str,
    retry_after: datetime,
    sub_key: str = "",
) -> None:
    """Record a transient fetch failure with an embargo before the next retry.

    Scanner and live appenders must check `retry_after` before re-attempting
    a FAILED row, otherwise a tight retry loop on a rate-limited API would
    hammer the upstream and burn quota.
    """
    if retry_after.tzinfo is None:
        raise ValueError("retry_after must be a timezone-aware UTC datetime")
    conn.execute(
        _UPSERT_SQL,
        (
            data_table.value,
            city,
            data_source,
            _coerce_target_date(target_date),
            sub_key,
            CoverageStatus.FAILED.value,
            reason,
            _now_utc_iso(),
            None,
            retry_after.astimezone(timezone.utc).isoformat(),
        ),
    )


def record_missing(
    conn: sqlite3.Connection,
    *,
    data_table: DataTable,
    city: str,
    data_source: str,
    target_date: date | str,
    sub_key: str = "",
    reason: str = CoverageReason.SCANNER_DETECTED,
) -> None:
    """Record that a scanner has detected an expected row as absent.

    Only used by `hole_scanner.py`. Live appenders never write MISSING — they
    either write WRITTEN on success or FAILED on error. MISSING is exclusively
    the scanner's signal that an expected row has never been attempted (or
    a prior FAILED has exceeded retry_after).
    """
    conn.execute(
        _UPSERT_SQL,
        (
            data_table.value,
            city,
            data_source,
            _coerce_target_date(target_date),
            sub_key,
            CoverageStatus.MISSING.value,
            reason,
            _now_utc_iso(),
            None,
            None,
        ),
    )


# ---------------------------------------------------------------------------
# Query helpers — scanner + operator dashboard
# ---------------------------------------------------------------------------


def find_pending_fills(
    conn: sqlite3.Connection,
    *,
    data_table: DataTable,
    city: Optional[str] = None,
    data_source: Optional[str] = None,
    max_rows: int = 10_000,
) -> list[sqlite3.Row]:
    """Return rows the hole scanner should try to fill now.

    A row is pending if:
      - status = MISSING, OR
      - status = FAILED AND retry_after <= now (retry embargo expired)

    Excludes WRITTEN (already done) and LEGITIMATE_GAP (never retry).

    Ordered by target_date ASC so the oldest holes are filled first — this
    keeps calibration pairs contiguous in time rather than leaving random
    gaps in the middle of the historical series.
    """
    now_iso = _now_utc_iso()
    clauses = ["data_table = ?"]
    params: list[object] = [data_table.value]
    if city is not None:
        clauses.append("city = ?")
        params.append(city)
    if data_source is not None:
        clauses.append("data_source = ?")
        params.append(data_source)
    clauses.append(
        "(status = 'MISSING' OR (status = 'FAILED' AND retry_after <= ?))"
    )
    params.append(now_iso)

    sql = (
        f"SELECT * FROM data_coverage WHERE {' AND '.join(clauses)} "
        f"ORDER BY target_date ASC, city ASC, sub_key ASC LIMIT ?"
    )
    params.append(max_rows)
    return conn.execute(sql, params).fetchall()


def count_by_status(
    conn: sqlite3.Connection,
    *,
    data_table: Optional[DataTable] = None,
) -> dict[str, int]:
    """Return {status: count} across the full coverage table or one data_table.

    Used by operator dashboard and startup health check to answer "is my DB
    in a healthy state?" in one query.
    """
    if data_table is None:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM data_coverage GROUP BY status"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM data_coverage WHERE data_table = ? GROUP BY status",
            (data_table.value,),
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def coverage_summary(
    conn: sqlite3.Connection,
    *,
    data_table: DataTable,
) -> list[sqlite3.Row]:
    """Per-(city, data_source) row-count breakdown across statuses.

    Returns one row per (city, data_source) with WRITTEN/LEGITIMATE_GAP/
    FAILED/MISSING counts. Used by `hole_scanner.py --report` and by the
    operator dashboard to spot "which city on which source has the most
    holes right now".
    """
    return conn.execute(
        """
        SELECT city, data_source,
               SUM(CASE WHEN status='WRITTEN'        THEN 1 ELSE 0 END) AS n_written,
               SUM(CASE WHEN status='LEGITIMATE_GAP' THEN 1 ELSE 0 END) AS n_legit_gap,
               SUM(CASE WHEN status='FAILED'         THEN 1 ELSE 0 END) AS n_failed,
               SUM(CASE WHEN status='MISSING'        THEN 1 ELSE 0 END) AS n_missing
        FROM data_coverage
        WHERE data_table = ?
        GROUP BY city, data_source
        ORDER BY city, data_source
        """,
        (data_table.value,),
    ).fetchall()


def bulk_record_written(
    conn: sqlite3.Connection,
    *,
    data_table: DataTable,
    rows: Iterable[tuple[str, str, str, str]],
) -> int:
    """Record WRITTEN for many (city, data_source, target_date, sub_key) at once.

    For use during backfill refactor: when a backfill script writes a batch of
    N rows to the data table in one commit, it can call this helper once to
    record coverage for all of them instead of N individual calls.

    Returns the number of rows upserted.
    """
    now_iso = _now_utc_iso()
    payload = [
        (
            data_table.value,
            city,
            data_source,
            target_date,
            sub_key,
            CoverageStatus.WRITTEN.value,
            None,
            now_iso,
            None,
            None,
        )
        for city, data_source, target_date, sub_key in rows
    ]
    if not payload:
        return 0
    conn.executemany(_UPSERT_SQL, payload)
    return len(payload)
