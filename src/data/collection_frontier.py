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
_DIAGNOSTIC = "diagnostic"


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
        return _DIAGNOSTIC
    return _LIVE


@dataclass(frozen=True)
class FrontierRow:
    """One source partition's temporal frontier + the single live-blocker reason."""

    source_id: str
    track: str
    calendar_id: str
    role: str                                   # live / backfill / diagnostic / derived
    family: str                                 # forecast / observation / market_topology / ... (PR #329 C)
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

    # Coverage aggregate of the latest USABLE run (PR review #329 R3 F10) — lets --explain show
    # WHERE the holes are, not just the single blocker label.
    latest_source_run_id: Optional[str] = None
    coverage_total: int = 0
    coverage_ready: int = 0
    coverage_blocked: int = 0
    coverage_expired: int = 0
    coverage_partial: int = 0


# ---- blocker reasons (spec §10 distinguishes these) ----
_BLOCK_OK = "OK"
_BLOCK_NOT_RELEASED = "NOT_RELEASED"
_BLOCK_STALE = "STALE_SOURCE"
_BLOCK_DOWN = "SOURCE_DOWN"
_BLOCK_PARTIAL = "PARTIAL_RUN"
_BLOCK_READINESS = "READINESS_BLOCKED"
_BLOCK_NOT_LIVE = "NOT_LIVE_AUTHORIZED"
_BLOCK_UNKNOWN = "UNKNOWN_BLOCKED"
_BLOCK_SHORT_HORIZON = "SHORT_HORIZON_ONLY"     # latest cycle is a 06/18 short horizon (not live-authorized)
_BLOCK_COVERAGE_UNKNOWN = "COVERAGE_UNKNOWN"    # no per-target coverage proof — cannot assert OK

_ACTION = {
    _BLOCK_OK: "none",
    _BLOCK_NOT_RELEASED: "wait until safe_fetch_not_before",
    _BLOCK_STALE: "investigate fetch path; source past freshness ceiling",
    _BLOCK_DOWN: "check source_health; provider/endpoint failing",
    _BLOCK_PARTIAL: "wait for complete run or confirm shorter-horizon target",
    _BLOCK_READINESS: "inspect readiness_state / source_run_coverage reason_codes",
    _BLOCK_NOT_LIVE: "none — source is not live-eligible by design",
    _BLOCK_UNKNOWN: "no source_run for this partition — confirm scheduler ran",
    _BLOCK_SHORT_HORIZON: "latest cycle is a 06/18 short horizon (not live-authorized); wait for 00/12 full",
    _BLOCK_COVERAGE_UNKNOWN: "no per-target coverage rows — cannot prove all targets ready",
}


def _classify(
    role: str,
    now: datetime,
    safe_fetch_not_before: Optional[datetime],
    have_attempt: bool,
    usable_fresh: bool,
    usable_coverage: _CoverageSummary,
    attempt_status: Optional[str],
    attempt_completeness: Optional[str],
    attempt_track: Optional[str],
    attempt_freshness: str,
    partial_policy: str,
    health_consecutive_failures: Optional[int],
) -> str:
    """Blocker classification on the latest-USABLE vs latest-ATTEMPTED model (PR review #329 R3).

    OK is determined by the latest USABLE full-horizon SUCCESS run that is fresh AND has complete,
    non-expired, all-ready per-target coverage (scoped to THAT run's source_run_id). A newer
    FAILED / short-horizon / not-released ATTEMPT does NOT hide an older usable cycle. When no
    usable fresh run exists, the blocker reflects the latest attempt's best diagnosis.
    """
    if role != _LIVE:
        return _BLOCK_NOT_LIVE
    if not have_attempt:
        return _BLOCK_UNKNOWN

    # A usable, fresh full-horizon run exists → OK iff its coverage proves all targets ready.
    if usable_fresh:
        cov = usable_coverage
        if cov.total == 0:
            return _BLOCK_COVERAGE_UNKNOWN
        if cov.ready == cov.total:
            return _BLOCK_OK
        return _BLOCK_READINESS

    # No usable fresh full run — diagnose from the latest attempt.
    if safe_fetch_not_before is not None and now < safe_fetch_not_before:
        return _BLOCK_NOT_RELEASED
    st = (attempt_status or "").strip().upper()
    comp = (attempt_completeness or "").strip().upper()
    if st in ("NOT_RELEASED", "SKIPPED_NOT_RELEASED") or comp == "NOT_RELEASED":
        return _BLOCK_READINESS
    if health_consecutive_failures is not None and health_consecutive_failures >= 3:
        return _BLOCK_DOWN
    if attempt_freshness == "EXPIRED":
        return _BLOCK_STALE
    if (attempt_track or "").endswith("_short_horizon"):
        return _BLOCK_SHORT_HORIZON
    if st == "FAILED" or comp in ("MISSING", "HORIZON_OUT_OF_RANGE"):
        return _BLOCK_READINESS
    if comp in ("PARTIAL", "INCOMPLETE") and partial_policy == "BLOCK_LIVE":
        return _BLOCK_PARTIAL
    # Have an attempt but no usable full run proven — cannot assert OK.
    return _BLOCK_COVERAGE_UNKNOWN


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


def _like_escape(s: str) -> str:
    r"""Escape SQL LIKE wildcards. Calendar tracks/source_ids contain underscores (mx2t6_high,
    ecmwf_open_data) which are LIKE single-char wildcards — must be escaped (ESCAPE '\')."""
    return s.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")


def _track_match_sql(column: str, *, has_release_key: bool) -> str:
    """SQL fragment matching a calendar track against the REAL written track.

    Real OpenData rows write the HORIZON-EXPANDED track (mx2t6_high_full_horizon), NOT the bare
    calendar track (mx2t6_high) — PR review #329 F1. Match: exact OR prefix. ``source_run`` and
    ``source_run_coverage`` ALSO carry ``release_calendar_key`` ('source_id:track:horizon'), so
    include it there; ``readiness_state`` has NO such column, so match on track only.
    """
    base = f"({column} = ? OR {column} LIKE ? ESCAPE '\\'"
    if has_release_key:
        return base + " OR release_calendar_key LIKE ? ESCAPE '\\')"
    return base + ")"


def _track_match_params(source_id: str, track: str, *, has_release_key: bool) -> tuple[str, ...]:
    et = _like_escape(track)
    if has_release_key:
        return (track, f"{et}\\_%", f"{_like_escape(source_id)}:{et}:%")
    return (track, f"{et}\\_%")


def _latest_source_run(
    conn: sqlite3.Connection, source_id: str, track: str
) -> Optional[sqlite3.Row]:
    """Latest source_run for (source_id, calendar track) by source_issue_time then recorded_at.

    Matches the real horizon-expanded track / release_calendar_key, not the bare calendar track.
    """
    return _safe_fetchone(
        conn,
        f"""
        SELECT source_run_id, source_id, track, release_calendar_key, source_issue_time,
               source_release_time, source_available_at, fetch_started_at, captured_at,
               imported_at, target_local_date, completeness_status, status, recorded_at
        FROM source_run
        WHERE source_id = ? AND {_track_match_sql('track', has_release_key=True)}
        ORDER BY COALESCE(source_issue_time, '') DESC, COALESCE(recorded_at, '') DESC
        LIMIT 1
        """,
        (source_id, *_track_match_params(source_id, track, has_release_key=True)),
    )


def _latest_usable_full_run(
    conn: sqlite3.Connection, source_id: str, track: str
) -> Optional[sqlite3.Row]:
    """Latest SUCCESS, full-horizon source_run for (source_id, calendar track) — the live-usable
    candidate (PR review #329 R3 F1). Excludes short-horizon (06/18, not live-authorized) and
    non-success runs, so a newer FAILED/short attempt cannot hide an older fresh usable cycle.
    """
    return _safe_fetchone(
        conn,
        f"""
        SELECT source_run_id, track, source_issue_time, source_release_time, source_available_at,
               captured_at, completeness_status, status
        FROM source_run
        WHERE source_id = ? AND {_track_match_sql('track', has_release_key=True)}
          AND UPPER(status) IN ('SUCCESS', 'OK', 'COMPLETE')
          AND track NOT LIKE '%\\_short\\_horizon' ESCAPE '\\'
        ORDER BY COALESCE(source_issue_time, '') DESC, COALESCE(recorded_at, '') DESC
        LIMIT 1
        """,
        (source_id, *_track_match_params(source_id, track, has_release_key=True)),
    )


def _latest_readiness(
    conn: sqlite3.Connection, source_id: str, track: str, target_local_date: Optional[str]
) -> Optional[sqlite3.Row]:
    """Latest readiness_state for (source_id, calendar track [, target_local_date]).

    Track match is prefix/key aware (readiness rows also carry the horizon-expanded track).
    """
    if target_local_date:
        return _safe_fetchone(
            conn,
            f"""
            SELECT status, expires_at, computed_at FROM readiness_state
            WHERE source_id = ? AND {_track_match_sql('track', has_release_key=False)} AND target_local_date = ?
            ORDER BY COALESCE(computed_at, '') DESC LIMIT 1
            """,
            (source_id, *_track_match_params(source_id, track, has_release_key=False), target_local_date),
        )
    return _safe_fetchone(
        conn,
        f"""
        SELECT status, expires_at, computed_at FROM readiness_state
        WHERE source_id = ? AND {_track_match_sql('track', has_release_key=False)}
        ORDER BY COALESCE(computed_at, '') DESC LIMIT 1
        """,
        (source_id, *_track_match_params(source_id, track, has_release_key=False)),
    )


@dataclass(frozen=True)
class _CoverageSummary:
    """Per-target readiness aggregate from source_run_coverage for ONE source_run_id."""

    total: int
    ready: int
    blocked: int
    expired: int
    partial: int


def _coverage_summary(
    conn: sqlite3.Connection, source_run_id: Optional[str], now: datetime
) -> _CoverageSummary:
    """Aggregate source_run_coverage readiness for a SPECIFIC source_run_id (PR review #329 R3 B).

    Scoped to ONE run id — NOT all historical/short/expired rows for the track (which produced
    both false-OK from an old all-ready run and false-block from an old BLOCKED row). A target is
    READY only if readiness=LIVE_ELIGIBLE AND completeness=COMPLETE AND expires_at>now (R3 F3);
    an expired LIVE_ELIGIBLE counts as expired (blocked), never ready.
    """
    if not source_run_id:
        return _CoverageSummary(0, 0, 0, 0, 0)
    try:
        cur = conn.execute(
            """
            SELECT readiness_status, completeness_status, expires_at
            FROM source_run_coverage WHERE source_run_id = ?
            """,
            (source_run_id,),
        )
        rows = list(cur.fetchall())
    except sqlite3.OperationalError as exc:
        if "no such" in str(exc).lower():
            return _CoverageSummary(0, 0, 0, 0, 0)
        raise

    ready = expired = partial = blocked = 0
    for r in rows:
        rs = str(r["readiness_status"]).upper()
        comp = str(r["completeness_status"]).upper()
        exp = _parse_iso(r["expires_at"])
        if rs == "LIVE_ELIGIBLE" and comp == "COMPLETE":
            if exp is not None and exp <= now:
                expired += 1
            else:
                ready += 1
        elif comp in ("PARTIAL", "INCOMPLETE"):
            partial += 1
        else:
            blocked += 1
    return _CoverageSummary(total=len(rows), ready=ready, blocked=blocked,
                            expired=expired, partial=partial)


# ---------------------------------------------------------------------------
# Non-forecast family federation (PR #329 review C). The forecast frontier above is calendar /
# source_run driven. Other live data families have their OWN truth tables and semantics — the
# frontier FEDERATES over them rather than forcing the source_run model onto everything.
#
# Per-family event-time probe: (table, event_time_column). Event time is the family's SOURCE/EVENT
# plane (target_date / settled_at / created_at), NEVER a write-time column — same load-bearing rule
# as the forecast frontier (a row written now for an old event must not read as fresh). Families
# whose truth lives outside the forecasts connection (executable_market snapshots in the trade DB,
# venue_user_ws state, solar, diagnostics) have NO probe here: they degrade to an honest
# COVERAGE_UNKNOWN row (the family is COVERED — it appears — but its freshness is unproven from
# this connection) rather than fabricating a freshness number. Non-forecast rows report PRESENCE +
# event age, not calendar-grade staleness; a per-family freshness policy is future work.
_FAMILY_EVENT_PROBE: dict[str, tuple[str, str]] = {
    "observation": ("observations", "target_date"),
    "market_topology": ("market_events", "created_at"),
    "settlement": ("settlements", "settled_at"),
}


def _family_latest_event(conn: sqlite3.Connection, table: str, col: str) -> Optional[datetime]:
    """MAX(event_time) for a family table; None if table/col missing or empty (fail-closed)."""
    row = _safe_fetchone(conn, f"SELECT MAX({col}) FROM {table}", ())  # noqa: S608 (fixed identifiers)
    # index access (not row["..."]) so it works whether or not the caller set row_factory=Row.
    return _parse_iso(row[0]) if row else None


def _registry_family_role(job_role: str) -> str:
    """Map a registry job.role onto a frontier role bucket."""
    if job_role in ("live", "settlement"):
        return _LIVE
    if job_role == "backfill":
        return _BACKFILL
    if job_role == "derived":
        return "derived"
    return _DIAGNOSTIC


def _family_frontier_rows(
    conn: sqlite3.Connection, now: datetime, role_filter: Optional[str]
) -> list[FrontierRow]:
    """One FrontierRow per (family, source_id) for every NON-forecast family in the registry.

    Coverage federation (PR #329 C): the report can no longer call itself a control plane while
    excluding observation/market/venue/settlement truth. Each row reports the latest event-time
    present for that family (where probeable) + a presence-based blocker; families without a probe
    degrade to COVERAGE_UNKNOWN, never a fabricated freshness.
    """
    from src.data.source_job_registry import JOB_REGISTRY

    seen: set[tuple[str, str]] = set()
    rows: list[FrontierRow] = []
    for job in JOB_REGISTRY.values():
        fam = job.family
        if fam is None or fam == "forecast":
            continue
        role = _registry_family_role(job.role)
        if role_filter and role != role_filter:
            continue
        for source_id in (job.all_source_ids or ((job.source_id,) if job.source_id else (job.job_id,))):
            key = (fam, source_id)
            if key in seen:
                continue
            seen.add(key)

            probe = _FAMILY_EVENT_PROBE.get(fam)
            latest_event = _family_latest_event(conn, *probe) if probe else None
            age = (now - latest_event).total_seconds() if latest_event else None

            if role != _LIVE:
                blocker = _BLOCK_NOT_LIVE
            elif latest_event is not None:
                blocker = _BLOCK_OK              # family IS producing data (presence); age reported
            else:
                blocker = _BLOCK_COVERAGE_UNKNOWN  # no probe / no rows — freshness unproven here

            rows.append(FrontierRow(
                source_id=source_id, track=fam, calendar_id=f"{fam}:{source_id}", role=role,
                family=fam, target_local_date=None,
                source_issue_time=latest_event,   # event-time plane (NOT write time)
                source_release_time=None, safe_fetch_not_before=None,
                latest_attempt_at=None, latest_success_at=latest_event,
                captured_at=None, imported_at=None,
                completeness_status=None, readiness_status=None, readiness_expires_at=None,
                freshness_state=("UNKNOWN" if latest_event is None else "CURRENT"),
                freshness_age_seconds=age,
                live_blocker=blocker, operator_action=_ACTION[blocker],
            ))
    return rows


def compute_frontier(
    *,
    role_filter: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
    now: Optional[datetime] = None,
) -> list[FrontierRow]:
    """Compute the collection frontier for every calendar entry (one row per source/track).

    READ-ONLY. ``role_filter`` limits to a frontier role. ``conn`` (a forecasts
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

            # Latest USABLE full-horizon SUCCESS run (separate from latest ATTEMPT above).
            usable = _latest_usable_full_run(conn, source_id, track) if role == _LIVE else None
            usable_issue = _parse_iso(usable["source_issue_time"]) if usable else None
            usable_fresh = bool(
                usable_issue is not None
                and policy.freshness_state((now - usable_issue).total_seconds()) != "EXPIRED"
            )
            coverage = (
                _coverage_summary(conn, usable["source_run_id"], now)
                if (usable and usable_fresh) else _CoverageSummary(0, 0, 0, 0, 0)
            )
            blocker = _classify(
                role=role, now=now, safe_fetch_not_before=safe_fetch,
                have_attempt=run is not None,
                usable_fresh=usable_fresh,
                usable_coverage=coverage,
                attempt_status=(run["status"] if run else None),
                attempt_completeness=completeness,
                attempt_track=(run["track"] if run else None),
                attempt_freshness=freshness,
                partial_policy=policy.partial_policy.value,
                health_consecutive_failures=cf,
            )

            rows.append(FrontierRow(
                source_id=source_id, track=track, calendar_id=calendar_id, role=role,
                family="forecast",
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
                latest_source_run_id=(usable["source_run_id"] if usable else None),
                coverage_total=coverage.total, coverage_ready=coverage.ready,
                coverage_blocked=coverage.blocked, coverage_expired=coverage.expired,
                coverage_partial=coverage.partial,
            ))

        # Non-forecast family federation (PR #329 C): observation / market_topology / settlement /
        # executable_market / venue_user_ws / solar / diagnostic. Appended so the frontier spans
        # ALL live data families, not just the forecast release calendar.
        rows.extend(_family_frontier_rows(conn, now, role_filter))
    finally:
        if own_conn:
            conn.close()

    return rows
