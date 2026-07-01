"""Read-only EDLI reactor queue evidence."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any


EDLI_REACTOR_CONSUMER = "edli_reactor_v1"
EDLI_REACTOR_PROCESSING_LEASE_SECONDS = 300.0


def collect_edli_queue_evidence(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    launched_floor: datetime | None = None,
    consumer_name: str = EDLI_REACTOR_CONSUMER,
    processing_lease_seconds: float = EDLI_REACTOR_PROCESSING_LEASE_SECONDS,
) -> dict[str, Any]:
    """Summarize claimable EDLI work and post-launch queue progress.

    Progress intentionally excludes pending rows whose ``claimed_at`` is a future
    retry floor.  A reactor has moved work only when it creates a processing
    claim after launch, or writes a terminal ``processed_at`` after launch.
    """

    now_utc = _ensure_utc(now)
    stale_cutoff = now_utc - timedelta(seconds=float(processing_lease_seconds))
    launched_utc = _ensure_utc(launched_floor) if launched_floor is not None else None
    if launched_utc is None:
        launched_utc = datetime.max.replace(tzinfo=timezone.utc)
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN processing_status = 'pending' THEN 1 ELSE 0 END)
                AS pending_count,
            SUM(CASE WHEN processing_status = 'processing' THEN 1 ELSE 0 END)
                AS processing_count,
            SUM(
                CASE
                WHEN processing_status = 'pending'
                 AND (claimed_at IS NULL OR claimed_at <= ?)
                THEN 1 ELSE 0 END
            ) AS claimable_pending_count,
            SUM(
                CASE
                WHEN processing_status = 'processing'
                 AND claimed_at IS NOT NULL
                 AND claimed_at <= ?
                THEN 1 ELSE 0 END
            ) AS stale_processing_count,
            MIN(
                CASE
                WHEN processing_status = 'processing'
                 AND claimed_at IS NOT NULL
                 AND claimed_at <= ?
                THEN claimed_at ELSE NULL END
            ) AS oldest_stale_claimed_at,
            SUM(
                CASE
                WHEN processing_status = 'processing'
                 AND claimed_at IS NOT NULL
                 AND claimed_at >= ?
                THEN 1
                WHEN processing_status IN ('processed','failed','dead_letter','expired')
                 AND processed_at IS NOT NULL
                 AND processed_at >= ?
                THEN 1
                ELSE 0 END
            ) AS claim_or_terminal_after_launch_count
          FROM opportunity_event_processing
         WHERE consumer_name = ?
           AND processing_status IN (
                'pending','processing','processed','failed',
                'dead_letter','expired'
           )
        """,
        (
            now_utc.isoformat(),
            stale_cutoff.isoformat(),
            stale_cutoff.isoformat(),
            launched_utc.isoformat(),
            launched_utc.isoformat(),
            consumer_name,
        ),
    ).fetchone()
    evidence = {
        "consumer_name": consumer_name,
        "pending_count": _row_int(row, "pending_count"),
        "processing_count": _row_int(row, "processing_count"),
        "claimable_pending_count": _row_int(row, "claimable_pending_count"),
        "stale_processing_count": _row_int(row, "stale_processing_count"),
        "oldest_stale_claimed_at": _row_str(row, "oldest_stale_claimed_at") or None,
        "claim_or_terminal_after_launch_count": _row_int(
            row, "claim_or_terminal_after_launch_count"
        ),
        "processing_lease_seconds": float(processing_lease_seconds),
    }
    evidence["claimable_work_count"] = (
        evidence["claimable_pending_count"] + evidence["stale_processing_count"]
    )
    return evidence


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _row_int(row: sqlite3.Row | tuple | None, key: str) -> int:
    if row is None:
        return 0
    try:
        return int(row[key] or 0)
    except (KeyError, IndexError, TypeError, ValueError):
        return 0


def _row_str(row: sqlite3.Row | tuple | None, key: str) -> str:
    if row is None:
        return ""
    try:
        return str(row[key] or "")
    except (KeyError, IndexError, TypeError, ValueError):
        return ""
