# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: operator "Zeus Data Ingest + Collection Efficiency Refactor" spec §7
#   (Watermarks) + §"Backfill efficiency"; docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR5).
"""Source partition watermarks — PR5 (in-memory, read-only).

A watermark answers: for a (source_id, track), what is the last partition we ATTEMPTED, the
last we SUCCEEDED on, and the last NON-EMPTY one — so backfill/catch-up can ask "what is the
next partition to repair?" instead of rescanning a wide window every tick.

PR5 computes watermarks IN MEMORY from ``source_run`` (read-only). A persisted watermark table
would be forecast-class → SCHEMA_FORECASTS_VERSION bump → live daemon schema gate, deferred
to the operator-gated table PR (PR2b family).

Correctness rule (shared with the frontier): a partition's recency is its SOURCE/EVENT identity
(target_local_date / source_issue_time), NEVER the write time — a catch-up writing a fresh
``captured_at`` for an old partition must not advance the "successful" watermark past where the
source data actually is.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class SourceWatermark:
    """Backfill/catch-up cursor state for one (source_id, track), derived from source_run."""

    source_id: str
    track: str
    last_attempted_partition: Optional[str]    # latest target_local_date with ANY run
    last_successful_partition: Optional[str]   # latest target_local_date with an OK run
    last_non_empty_partition: Optional[str]    # latest with observed members/rows > 0
    attempted_count: int
    successful_count: int


def _safe_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
    try:
        cur = conn.execute(sql, params)
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return []
        raise
    return list(cur.fetchall())


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")


def compute_watermark(conn: sqlite3.Connection, source_id: str, track: str) -> SourceWatermark:
    """Derive the watermark for (source_id, calendar track) from source_run (read-only).

    Partition identity is the SOURCE identity, ordered NOT by write time — a late/backfilled
    write for an old partition cannot advance the successful watermark (PR review #329 F3).

    OpenData source-level source_run rows carry NO target_local_date (that lives in
    source_run_coverage); they are keyed by source_issue_time (the cycle). So the partition is
    COALESCE(target_local_date, source_issue_time, source_cycle_time) — forcing
    target_local_date IS NOT NULL (the prior version) made the watermark EMPTY for the single
    most important live forecast producer. The track match is prefix/key aware because real
    rows write the horizon-expanded track (mx2t6_high_full_horizon), not the bare calendar track.
    """
    conn.row_factory = sqlite3.Row
    et = _like_escape(track)
    rows = _safe_rows(
        conn,
        """
        SELECT COALESCE(target_local_date, source_issue_time, source_cycle_time) AS partition_key,
               status, observed_members
        FROM source_run
        WHERE source_id = ?
          AND (track = ? OR track LIKE ? ESCAPE '\\' OR release_calendar_key LIKE ? ESCAPE '\\')
          AND COALESCE(target_local_date, source_issue_time, source_cycle_time) IS NOT NULL
        ORDER BY partition_key ASC
        """,
        (source_id, track, f"{et}\\_%", f"{_like_escape(source_id)}:{et}:%"),
    )

    attempted = [r["partition_key"] for r in rows]
    ok = {"ok", "complete", "success"}
    successful = [r["partition_key"] for r in rows if str(r["status"]).lower() in ok]

    def _nonempty(r: sqlite3.Row) -> bool:
        m = r["observed_members"]
        try:
            return m is not None and int(m) > 0
        except (TypeError, ValueError):
            return False

    non_empty = [r["partition_key"] for r in rows if _nonempty(r)]

    return SourceWatermark(
        source_id=source_id,
        track=track,
        last_attempted_partition=attempted[-1] if attempted else None,
        last_successful_partition=successful[-1] if successful else None,
        last_non_empty_partition=non_empty[-1] if non_empty else None,
        attempted_count=len(attempted),
        successful_count=len(successful),
    )
