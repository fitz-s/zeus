# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Persist + read the source_time_frontier authority table (idempotent, backfill-safe).
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + src/data/collection_frontier.py before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: operator "Zeus Data Ingest + Collection Efficiency Refactor" spec §D
#   (persisted frontier); docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR #329 D);
#   src/state/db.py::_create_source_time_frontier (SCHEMA_FORECASTS_VERSION 7).
"""Persisted source-time frontier store — PR #329 review D.

The in-memory frontier (src.data.collection_frontier.compute_frontier) is the COMPUTE; this is
the persistence + read-back so live health reads a stored authority rather than recomputing from
scratch in ad-hoc scripts.

Two load-bearing rules:

  * IDEMPOTENT BY (source_id, family, partition_key) — re-persisting the same partition UPDATEs in
    place, never appends. partition_key is the source/event partition (issue time / target /
    'latest'), so a daemon that re-runs a tick does not multiply rows.

  * BACKFILL CANNOT REFRESH LIVE AUTHORITY — a write whose authority_tier is not
    DERIVED_FROM_DISSEMINATION (i.e. BACKFILL / RECONSTRUCTED / UNVERIFIED) must NOT overwrite an
    existing row that already holds live authority. A backfill written *seconds ago* for an old
    cycle must never replace the live frontier (the temporal twin of the freshness rule). Enforced
    in the UPSERT's WHERE clause, not in caller discipline.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from src.data.collection_frontier import FrontierRow

_LIVE_AUTHORITY = "DERIVED_FROM_DISSEMINATION"


def authority_tier_for_role(role: str) -> str:
    """Map a frontier role onto a persisted authority tier. Only 'live' earns live authority."""
    if role == "live":
        return _LIVE_AUTHORITY
    if role == "backfill":
        return "BACKFILL"
    return "UNVERIFIED"


def _partition_key(row: FrontierRow) -> str:
    """The source/event partition a frontier row pins to (never a write-time value)."""
    if row.source_issue_time is not None:
        return row.source_issue_time.astimezone(timezone.utc).isoformat()
    if row.target_local_date:
        return str(row.target_local_date)
    return "latest"


@dataclass(frozen=True)
class PersistResult:
    considered: int
    written: int          # inserted or updated
    skipped_backfill_over_live: int


def persist_frontier(
    conn: sqlite3.Connection,
    rows: Iterable[FrontierRow],
    *,
    now: Optional[datetime] = None,
    data_version: int = 0,
) -> PersistResult:
    """UPSERT frontier rows into source_time_frontier. Idempotent + batch-safe + backfill-guarded.

    Returns counts. A row whose authority is not live is NOT written over an existing live row
    (the skip is counted, not an error). Live always wins; same-or-higher authority refreshes.
    """
    now = now or datetime.now(timezone.utc)
    computed_at = now.isoformat()
    considered = written = skipped = 0

    for row in rows:
        considered += 1
        pk = _partition_key(row)
        auth = authority_tier_for_role(row.role)
        let = row.source_issue_time.astimezone(timezone.utc).isoformat() if row.source_issue_time else None

        cur = conn.execute(
            """
            INSERT INTO source_time_frontier (
                source_id, family, partition_key, track, role, latest_event_time,
                freshness_state, live_blocker, authority_tier, computed_at, data_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, family, partition_key) DO UPDATE SET
                track            = excluded.track,
                role             = excluded.role,
                latest_event_time= excluded.latest_event_time,
                freshness_state  = excluded.freshness_state,
                live_blocker     = excluded.live_blocker,
                authority_tier   = excluded.authority_tier,
                computed_at      = excluded.computed_at,
                data_version     = excluded.data_version
            WHERE excluded.authority_tier = ?
               OR source_time_frontier.authority_tier != ?
            """,
            (
                row.source_id, row.family, pk, row.track, row.role, let,
                row.freshness_state, row.live_blocker, auth, computed_at, int(data_version),
                _LIVE_AUTHORITY, _LIVE_AUTHORITY,
            ),
        )
        # rowcount: 1 = inserted or updated; 0 = ON CONFLICT update suppressed by the WHERE guard
        # (a backfill/reconstructed write that would have clobbered a live row).
        if cur.rowcount and cur.rowcount > 0:
            written += 1
        else:
            skipped += 1
    conn.commit()
    return PersistResult(considered=considered, written=written, skipped_backfill_over_live=skipped)


def read_persisted_frontier(
    conn: sqlite3.Connection, *, family: Optional[str] = None
) -> list[dict[str, object]]:
    """Read back the persisted frontier (optionally one family). Empty list if the table is absent."""
    sql = (
        "SELECT source_id, family, partition_key, track, role, latest_event_time, "
        "freshness_state, live_blocker, authority_tier, computed_at, data_version "
        "FROM source_time_frontier"
    )
    params: tuple[object, ...] = ()
    if family is not None:
        sql += " WHERE family = ?"
        params = (family,)
    sql += " ORDER BY family, source_id, partition_key"
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return []
        raise
