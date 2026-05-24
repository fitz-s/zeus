# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: operator "Zeus Data Ingest + Collection Efficiency Refactor" spec §10
#   (Data Collection Frontier Report); docs/operations/current/plans/data_temporal_kernel/PLAN.md;
#   src/data/source_time.py (TemporalPolicy); config/source_release_calendar.yaml.
"""In-memory data-collection temporal frontier — PR2 of the Data Temporal Kernel program.

Answers the operator's core question in one place: "what is the latest USABLE data for
this source right now, and if it's not usable, WHY?" — a temporal debugger over the
collection plane.

PR2 is READ-ONLY and computes the frontier IN MEMORY from existing surfaces. It does NOT
persist a table: a persisted ``source_time_frontier`` would be forecast-class and bump
``SCHEMA_FORECASTS_VERSION`` (which the live daemon gates on, SystemExit on mismatch) —
that is an operator-gated migration deferred to PR2b. Nothing here changes live behaviour
or writes any DB/file.

Inputs (all existing):
  * config/source_release_calendar.yaml  -> TemporalPolicy (safe_fetch, freshness ladder)
  * zeus-forecasts.db: source_run, readiness_state, source_run_coverage, job_run, data_coverage
  * state/source_health.json, state/daemon-heartbeat-ingest.json (best-effort diagnostics)

THE load-bearing correctness rule (spec §"Backfill can look fresh"): freshness age is measured
on the SOURCE/EVENT-time plane (source_issue_time), NEVER on a write-time plane (captured_at/
imported_at). A backfill written seconds ago for a week-old cycle must report STALE, not CURRENT.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.config import state_path
from src.data.source_time import TemporalPolicy, load_temporal_policy

_CALENDAR_PATH = Path(__file__).resolve().parents[2] / "config" / "source_release_calendar.yaml"

# Role a source plays, derived from its TemporalPolicy authority axes.
_LIVE = "live"
_BACKFILL = "backfill"
_SHADOW = "shadow"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string to an aware UTC datetime; None on failure/empty."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _role_of(policy: TemporalPolicy) -> str:
    if policy.backfill_only:
        return _BACKFILL
    if not policy.live_authorization:
        return _SHADOW
    return _LIVE


@dataclass(frozen=True)
class FrontierRow:
    """One source partition's temporal frontier + the single live-blocker reason."""

    source_id: str
    track: str
    calendar_id: str
    role: str                                   # live / backfill / shadow
    target_local_date: Optional[str]

    source_issue_time: Optional[datetime]
    source_release_time: Optional[datetime]
    safe_fetch_not_before: Optional[datetime]

    latest_attempt_at: Optional[datetime]       # source_run.fetch_started_at / job_run.started_at
    latest_success_at: Optional[datetime]       # latest source_run with status ok
    captured_at: Optional[datetime]             # WRITE-time (diagnostic only, never freshness)
    imported_at: Optional[datetime]             # WRITE-time (diagnostic only, never freshness)

    completeness_status: Optional[str]
    readiness_status: Optional[str]
    readiness_expires_at: Optional[datetime]

    freshness_state: str                        # CURRENT / DEGRADED / EXPIRED / UNKNOWN
    freshness_age_seconds: Optional[float]      # measured on source/event time

    live_blocker: str                           # OK / NOT_RELEASED / STALE_SOURCE / ...
    operator_action: str

    health_consecutive_failures: Optional[int] = None
    health_last_success_at: Optional[datetime] = None
    health_degraded_since: Optional[datetime] = None


# ---- blocker reasons (spec §10 distinguishes these) ----
_BLOCK_OK = "OK"
_BLOCK_NOT_RELEASED = "NOT_RELEASED"
_BLOCK_STALE = "STALE_SOURCE"
_BLOCK_DOWN = "SOURCE_DOWN"
_BLOCK_PARTIAL = "PARTIAL_RUN"
_BLOCK_READINESS = "READINESS_BLOCKED"
_BLOCK_NOT_LIVE = "NOT_LIVE_AUTHORIZED"
_BLOCK_UNKNOWN = "UNKNOWN_BLOCKED"

_ACTION = {
    _BLOCK_OK: "none",
    _BLOCK_NOT_RELEASED: "wait until safe_fetch_not_before",
    _BLOCK_STALE: "investigate fetch path; source past freshness ceiling",
    _BLOCK_DOWN: "check source_health; provider/endpoint failing",
    _BLOCK_PARTIAL: "wait for complete run or confirm shorter-horizon target",
    _BLOCK_READINESS: "inspect readiness_state reason_codes",
    _BLOCK_NOT_LIVE: "none — shadow/backfill source, not live-eligible by design",
    _BLOCK_UNKNOWN: "no source_run for this partition — confirm scheduler ran",
}


def _classify(
    role: str,
    now: datetime,
    safe_fetch_not_before: Optional[datetime],
    have_run: bool,
    completeness_status: Optional[str],
    partial_policy: str,
    readiness_status: Optional[str],
    readiness_expires_at: Optional[datetime],
    freshness_state: str,
    health_consecutive_failures: Optional[int],
) -> str:
    """Single-reason blocker classification, fail-closed ordering.

    Non-live sources are NOT_LIVE_AUTHORIZED (informational, not a fault). For live sources,
    the order surfaces the EARLIEST upstream cause: not-released < no-run < source-down <
    stale < partial < readiness.
    """
    if role != _LIVE:
        return _BLOCK_NOT_LIVE
    if safe_fetch_not_before is not None and now < safe_fetch_not_before:
        return _BLOCK_NOT_RELEASED
    if not have_run:
        return _BLOCK_UNKNOWN
    if health_consecutive_failures is not None and health_consecutive_failures >= 3:
        return _BLOCK_DOWN
    if freshness_state == "EXPIRED":
        return _BLOCK_STALE
    if completeness_status and completeness_status.lower() in ("partial", "incomplete") \
            and partial_policy == "BLOCK_LIVE":
        return _BLOCK_PARTIAL
    if readiness_status and readiness_status.upper() not in ("READY", "LIVE_ELIGIBLE", "OK"):
        return _BLOCK_READINESS
    if readiness_expires_at is not None and now >= readiness_expires_at:
        return _BLOCK_READINESS
    return _BLOCK_OK


def _load_health() -> dict[str, dict[str, Any]]:
    """Best-effort read of state/source_health.json, indexed by source_id. {} on any failure."""
    try:
        raw = json.loads(state_path("source_health.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(raw, dict):
        # Either {source_id: {...}} or {"sources": {source_id: {...}}}/{"sources": [..]}
        if "sources" in raw and isinstance(raw["sources"], dict):
            return {str(k): v for k, v in raw["sources"].items() if isinstance(v, dict)}
        if "sources" in raw and isinstance(raw["sources"], list):
            return {str(s.get("source_id")): s for s in raw["sources"] if isinstance(s, dict)}
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    if isinstance(raw, list):
        return {str(s.get("source_id")): s for s in raw if isinstance(s, dict)}
    return {}


def _safe_fetchone(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> Optional[sqlite3.Row]:
    """Run a SELECT, returning None if the table does not exist (fresh/empty DB).

    Missing table = no data = fail-closed UNKNOWN_BLOCKED downstream, never a traceback.
    """
    try:
        cur = conn.execute(sql, params)
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return None
        raise
    row: Optional[sqlite3.Row] = cur.fetchone()
    return row


def _latest_source_run(
    conn: sqlite3.Connection, source_id: str, track: str
) -> Optional[sqlite3.Row]:
    """Latest source_run for (source_id, track) by source_issue_time then recorded_at."""
    return _safe_fetchone(
        conn,
        """
        SELECT source_id, track, source_issue_time, source_release_time, source_available_at,
               fetch_started_at, captured_at, imported_at, target_local_date,
               completeness_status, status, recorded_at
        FROM source_run
        WHERE source_id = ? AND track = ?
        ORDER BY COALESCE(source_issue_time, '') DESC, COALESCE(recorded_at, '') DESC
        LIMIT 1
        """,
        (source_id, track),
    )


def _latest_readiness(
    conn: sqlite3.Connection, source_id: str, track: str, target_local_date: Optional[str]
) -> Optional[sqlite3.Row]:
    if target_local_date:
        return _safe_fetchone(
            conn,
            """
            SELECT status, expires_at, computed_at FROM readiness_state
            WHERE source_id = ? AND track = ? AND target_local_date = ?
            ORDER BY COALESCE(computed_at, '') DESC LIMIT 1
            """,
            (source_id, track, target_local_date),
        )
    return _safe_fetchone(
        conn,
        """
        SELECT status, expires_at, computed_at FROM readiness_state
        WHERE source_id = ? AND track = ?
        ORDER BY COALESCE(computed_at, '') DESC LIMIT 1
        """,
        (source_id, track),
    )


def compute_frontier(
    *,
    role_filter: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
    now: Optional[datetime] = None,
) -> list[FrontierRow]:
    """Compute the collection frontier for every calendar entry (one row per source/track).

    READ-ONLY. ``role_filter`` limits to 'live'/'backfill'/'shadow'. ``conn`` (a forecasts
    connection) and ``now`` are injectable for tests; otherwise opened/derived here.
    """
    from src.data.source_time import _calendar_index

    now = now or _utcnow()
    health = _load_health()

    # Single mtime-cached calendar parse shared with load_temporal_policy (no per-row re-read).
    entries: list[dict[str, Any]] = list(_calendar_index().values())

    own_conn = conn is None
    if own_conn:
        from src.state.db import get_forecasts_connection

        conn = get_forecasts_connection()
    assert conn is not None
    conn.row_factory = sqlite3.Row

    rows: list[FrontierRow] = []
    try:
        for entry in entries:
            calendar_id = str(entry.get("calendar_id") or "")
            policy = load_temporal_policy(calendar_id)
            role = _role_of(policy)
            if role_filter and role != role_filter:
                continue

            source_id, track = policy.source_id, str(entry.get("track", ""))
            run = _latest_source_run(conn, source_id, track)

            issue = _parse_iso(run["source_issue_time"]) if run else None
            release = _parse_iso(run["source_release_time"]) if run else None
            safe_fetch = policy.safe_fetch_not_before(issue) if issue else None
            target_date = run["target_local_date"] if run else None
            completeness = run["completeness_status"] if run else None
            captured = _parse_iso(run["captured_at"]) if run else None
            imported = _parse_iso(run["imported_at"]) if run else None
            attempt = _parse_iso(run["fetch_started_at"]) if run else None
            success = _parse_iso(run["source_available_at"]) if run and (
                str(run["status"]).lower() in ("ok", "complete", "success")) else None

            # Freshness measured on SOURCE/EVENT time (issue), NEVER on write time.
            age: Optional[float]
            freshness: str
            if issue is not None:
                age = (now - issue).total_seconds()
                freshness = policy.freshness_state(age)
            else:
                age, freshness = None, "UNKNOWN"

            readiness = _latest_readiness(conn, source_id, track, target_date)
            readiness_status = readiness["status"] if readiness else None
            readiness_expires = _parse_iso(readiness["expires_at"]) if readiness else None

            h = health.get(source_id, {})
            cf = h.get("consecutive_failures")
            cf = int(cf) if isinstance(cf, (int, float)) else None

            blocker = _classify(
                role=role, now=now, safe_fetch_not_before=safe_fetch,
                have_run=run is not None, completeness_status=completeness,
                partial_policy=policy.partial_policy.value,
                readiness_status=readiness_status, readiness_expires_at=readiness_expires,
                freshness_state=freshness, health_consecutive_failures=cf,
            )

            rows.append(FrontierRow(
                source_id=source_id, track=track, calendar_id=calendar_id, role=role,
                target_local_date=target_date,
                source_issue_time=issue, source_release_time=release,
                safe_fetch_not_before=safe_fetch,
                latest_attempt_at=attempt, latest_success_at=success,
                captured_at=captured, imported_at=imported,
                completeness_status=completeness,
                readiness_status=readiness_status, readiness_expires_at=readiness_expires,
                freshness_state=freshness, freshness_age_seconds=age,
                live_blocker=blocker, operator_action=_ACTION[blocker],
                health_consecutive_failures=cf,
                health_last_success_at=_parse_iso(h.get("last_success_at")),
                health_degraded_since=_parse_iso(h.get("degraded_since")),
            ))
    finally:
        if own_conn:
            conn.close()

    return rows
