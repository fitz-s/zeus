# Created: 2026-05-03
# Last reused/audited: 2026-05-23
# Authority basis: docs/operations/task_2026-05-14_data_daemon_live_efficiency/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md
#   Phase 3 evaluator consumes producer readiness without hot-path entry-readiness writes.
#   P0 follow-up (2026-05-23): docs/operations/task_2026-05-22_forecast_bundle_layer_fix/SPEC.md
#   §1 full-bundle-layer selection + §2 NULL fail-closed.
"""Executable forecast reader for V4 source-linked ensemble snapshots."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.config import settings
from src.data.forecast_extrema_authority import (
    ForecastExtremaEligibility,
    LEGACY_NULL_PASSTHROUGH_VALIDATION,
    POSITIVE_ATTRIBUTION_STATUS_SQL_IN_LIST,
    classify_forecast_extrema_authority,
)
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY
from src.data.forecast_target_contract import ForecastTargetScope
from src.state.readiness_repo import get_entry_readiness

UTC = timezone.utc
# SQL ORDER BY fragment that ranks FULL_CONTRIBUTOR rows first.
# Derived from POSITIVE_ATTRIBUTION_STATUS_SQL_IN_LIST so the SQL predicate
# stays in sync with classify_forecast_extrema_authority() automatically.
_EXTREMA_RANK_ORDER_BY = (
    "(CASE WHEN COALESCE(contributes_to_target_extrema,0)=1"
    f" AND COALESCE(forecast_window_attribution_status,'') IN {POSITIVE_ATTRIBUTION_STATUS_SQL_IN_LIST}"
    " AND COALESCE(boundary_ambiguous,0)=0 THEN 0 ELSE 1 END)"
    " ASC, source_cycle_time DESC, available_at DESC, snapshot_id DESC"
)
SOURCE_TRANSPORT = "ensemble_snapshots_db_reader"
WORLD_SCHEMA = "world"
FORECASTS_SCHEMA = "forecasts"
FORECAST_AUTHORITY_TABLES = frozenset({
    "ensemble_snapshots",
    "readiness_state",
    "source_run",
    "source_run_coverage",
})
FORECASTS_OWNED_TABLES = frozenset({
    "ensemble_snapshots",
    "readiness_state",
    "source_run",
    "source_run_coverage",
})


@dataclass(frozen=True)
class ExecutableForecastSnapshot:
    snapshot_id: int
    city: str
    target_local_date: date
    temperature_metric: str
    data_version: str
    members: tuple[float | None, ...]
    source_id: str
    source_transport: str
    source_run_id: str
    release_calendar_key: str
    source_cycle_time: str
    source_release_time: str
    source_available_at: str
    issue_time: str
    valid_time: str
    available_at: str
    fetch_time: str
    manifest_hash: str | None
    members_unit: str
    local_day_start_utc: str | None
    step_horizon_hours: float | None
    first_member_observed_time: str
    run_complete_time: str
    raw_orderbook_hash_transition_delta_ms: int | None


@dataclass(frozen=True)
class ExecutableForecastEvidence:
    forecast_source_id: str
    forecast_data_version: str
    source_transport: str
    source_run_id: str
    release_calendar_key: str
    coverage_id: str
    producer_readiness_id: str
    entry_readiness_id: str | None
    source_cycle_time: str
    source_issue_time: str | None
    source_release_time: str
    source_available_at: str
    fetch_started_at: str | None
    fetch_finished_at: str | None
    captured_at: str
    input_snapshot_ids: tuple[int, ...]
    raw_payload_hash: str | None
    manifest_hash: str | None
    target_local_date: str
    target_window_start_utc: str
    target_window_end_utc: str
    city_timezone: str
    required_steps: tuple[int, ...]
    observed_steps: tuple[int, ...]
    expected_members: int
    observed_members: int
    source_run_status: str
    source_run_completeness_status: str
    coverage_completeness_status: str
    coverage_readiness_status: str | None
    # P0 follow-up §2: validation tokens recorded by the bundle layer (e.g.
    # forecast_extrema_authority_legacy_null_passthrough when a legacy NULL row
    # passes through).  The evaluator appends these to its applied_validations.
    applied_validations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutableForecastBundle:
    snapshot: ExecutableForecastSnapshot
    evidence: ExecutableForecastEvidence

    def to_ens_result(self) -> dict[str, Any]:
        evidence_hash = self.evidence.raw_payload_hash or self.evidence.manifest_hash
        target_day_valid_window = _target_day_valid_window_from_coverage(
            self.evidence.target_window_start_utc,
            self.evidence.target_window_end_utc,
        )
        return {
            "period_extrema_members": list(self.snapshot.members),
            "period_extrema_source": "local_calendar_day_member_extrema",
            "executable_snapshot_id": self.snapshot.snapshot_id,
            "members_unit": self.snapshot.members_unit,
            "times": [self.snapshot.valid_time],
            "n_members": len(self.snapshot.members),
            "model": "ecmwf_ens",
            "source_id": self.evidence.forecast_source_id,
            "data_version": self.evidence.forecast_data_version,
            "source_transport": self.evidence.source_transport,
            "source_run_id": self.evidence.source_run_id,
            "release_calendar_key": self.evidence.release_calendar_key,
            "coverage_id": self.evidence.coverage_id,
            "producer_readiness_id": self.evidence.producer_readiness_id,
            "entry_readiness_id": self.evidence.entry_readiness_id,
            "forecast_source_role": "entry_primary",
            "degradation_level": "OK",
            "authority_tier": "FORECAST",
            "raw_payload_hash": evidence_hash,
            "manifest_hash": self.evidence.manifest_hash,
            "issue_time": self.evidence.source_issue_time or self.evidence.source_cycle_time,
            "valid_time": self.evidence.target_local_date,
            "target_day_valid_window": target_day_valid_window,
            "target_window_start_utc": target_day_valid_window[0],
            "target_window_end_utc": target_day_valid_window[1],
            "fetch_time": self.evidence.captured_at,
            "available_at": self.evidence.source_available_at,
            "first_member_observed_time": (
                self.snapshot.first_member_observed_time
                or self.evidence.fetch_started_at
                or self.evidence.captured_at
            ),
            "run_complete_time": (
                self.snapshot.run_complete_time
                or self.evidence.fetch_finished_at
                or self.evidence.captured_at
            ),
            "raw_orderbook_hash_transition_delta_ms": (
                self.snapshot.raw_orderbook_hash_transition_delta_ms
            ),
            "source_run_status": self.evidence.source_run_status,
            "source_run_completeness_status": self.evidence.source_run_completeness_status,
            "coverage_completeness_status": self.evidence.coverage_completeness_status,
            "coverage_readiness_status": self.evidence.coverage_readiness_status,
            "executable_forecast_evidence": self.evidence,
            "extrema_authority_applied_validations": list(self.evidence.applied_validations),
        }


@dataclass(frozen=True)
class ExecutableForecastReadResult:
    status: str
    reason_code: str
    snapshot: ExecutableForecastSnapshot | None = None

    @property
    def ok(self) -> bool:
        return self.status == "LIVE_ELIGIBLE"


@dataclass(frozen=True)
class ExecutableForecastBundleResult:
    status: str
    reason_code: str
    bundle: ExecutableForecastBundle | None = None

    @property
    def ok(self) -> bool:
        return self.status == "LIVE_ELIGIBLE"


def _target_day_valid_window_from_coverage(
    target_window_start_utc: str,
    target_window_end_utc: str,
) -> tuple[str, str]:
    start = _parse_utc(target_window_start_utc)
    end = _parse_utc(target_window_end_utc)
    if start is None or end is None:
        return ("", "")
    last_observed_hour = end - timedelta(hours=1) if end > start else end
    return (start.isoformat(), last_observed_hour.isoformat())


def _parse_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _members(value: object) -> tuple[float | None, ...]:
    if not isinstance(value, str):
        raise ValueError("members_json must be a JSON string")
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("members_json must contain a list")
    members: list[float | None] = []
    for item in parsed:
        members.append(None if item is None else float(item))
    return tuple(members)


def _json_list(value: object) -> tuple[Any, ...]:
    if not isinstance(value, str) or not value:
        return ()
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return ()
    return tuple(parsed)


def _int_tuple(value: object) -> tuple[int, ...]:
    return tuple(int(item) for item in _json_list(value))


def _readiness_reasons(value: object) -> tuple[str, ...]:
    reasons = _json_list(value)
    return tuple(str(reason) for reason in reasons if str(reason))


def _schema_attached(conn: sqlite3.Connection, schema: str) -> bool:
    return schema == "main" or any(row[1] == schema for row in conn.execute("PRAGMA database_list"))


_TABLE_EXISTS_SQL = {
    "main": "SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
    WORLD_SCHEMA: "SELECT 1 FROM world.sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
    FORECASTS_SCHEMA: "SELECT 1 FROM forecasts.sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
}


def _table_exists(conn: sqlite3.Connection, *, schema: str, table: str) -> bool:
    if schema not in {"main", WORLD_SCHEMA, FORECASTS_SCHEMA} or table not in FORECAST_AUTHORITY_TABLES:
        raise ValueError("unsupported executable forecast authority table")
    if not _schema_attached(conn, schema):
        return False
    row = conn.execute(_TABLE_EXISTS_SQL[schema], (table,)).fetchone()
    return row is not None


def _authority_table(conn: sqlite3.Connection, table: str) -> str | None:
    """Resolve the authoritative forecast table for a trade/world connection.

    When ``forecasts`` is attached it is the forecast authority store and we
    intentionally do not fall back to stale world/main rows if a forecast-owned
    table is missing there.
    """

    if table not in FORECAST_AUTHORITY_TABLES:
        raise ValueError("unsupported executable forecast authority table")
    forecasts_attached = _schema_attached(conn, FORECASTS_SCHEMA)
    world_attached = _schema_attached(conn, WORLD_SCHEMA)
    if forecasts_attached and table in FORECASTS_OWNED_TABLES:
        if not _table_exists(conn, schema=FORECASTS_SCHEMA, table=table):
            return None
        return f"{FORECASTS_SCHEMA}.{table}"
    if world_attached and _table_exists(conn, schema=WORLD_SCHEMA, table=table):
        return f"{WORLD_SCHEMA}.{table}"
    if not forecasts_attached and not world_attached:
        return table
    return None


def _source_run_coverage_by_id_sql(table: str) -> str:
    if table == f"{FORECASTS_SCHEMA}.source_run_coverage":
        return "SELECT * FROM forecasts.source_run_coverage WHERE coverage_id = ?"
    if table == f"{WORLD_SCHEMA}.source_run_coverage":
        return "SELECT * FROM world.source_run_coverage WHERE coverage_id = ?"
    if table == "source_run_coverage":
        return "SELECT * FROM source_run_coverage WHERE coverage_id = ?"
    raise ValueError("unsupported source_run_coverage authority table")


def _source_run_coverage_by_id(conn: sqlite3.Connection, coverage_id: str) -> dict[str, Any] | None:
    table = _authority_table(conn, "source_run_coverage")
    if table is None:
        return None
    row = conn.execute(
        _source_run_coverage_by_id_sql(table),
        (coverage_id,),
    ).fetchone()
    return dict(row) if row else None


def _source_run_coverages_for_scope_sql(table: str) -> str:
    # Enumerate ALL coverage rows for the (city/metric/source/data_version)
    # scope — NOT LIMIT 1.  The source_run_coverage UNIQUE constraint includes
    # source_run_id, so a single target local-day carries one coverage per
    # forecast cycle (00Z, 12Z, …).  The single-path reader resolved exactly
    # one (latest) coverage; this enumeration is the basis for full
    # bundle-layer selection (P0 follow-up §1).  Newest-first ordering only
    # sets a deterministic input order; the authoritative ranking is applied
    # by ``_bundle_rank`` after extrema-authority classification.
    if table == f"{FORECASTS_SCHEMA}.source_run_coverage":
        prefix = "SELECT * FROM forecasts.source_run_coverage"
    elif table == f"{WORLD_SCHEMA}.source_run_coverage":
        prefix = "SELECT * FROM world.source_run_coverage"
    elif table == "source_run_coverage":
        prefix = "SELECT * FROM source_run_coverage"
    else:
        raise ValueError("unsupported source_run_coverage authority table")
    return (
        prefix
        + """
        WHERE city_id = ?
          AND city_timezone = ?
          AND target_local_date = ?
          AND temperature_metric = ?
          AND source_id = ?
          AND source_transport = ?
          AND data_version = ?
        ORDER BY computed_at DESC, recorded_at DESC
        """
    )


def _source_run_coverages_for_scope(
    conn: sqlite3.Connection,
    *,
    city_id: str,
    city_timezone: str,
    target_local_date: date,
    temperature_metric: str,
    source_id: str,
    source_transport: str,
    data_version: str,
) -> list[dict[str, Any]]:
    table = _authority_table(conn, "source_run_coverage")
    if table is None:
        return []
    rows = conn.execute(
        _source_run_coverages_for_scope_sql(table),
        (
            city_id,
            city_timezone,
            target_local_date.isoformat(),
            temperature_metric,
            source_id,
            source_transport,
            data_version,
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def _source_run_by_id_sql(table: str) -> str:
    if table == f"{FORECASTS_SCHEMA}.source_run":
        return "SELECT * FROM forecasts.source_run WHERE source_run_id = ?"
    if table == f"{WORLD_SCHEMA}.source_run":
        return "SELECT * FROM world.source_run WHERE source_run_id = ?"
    if table == "source_run":
        return "SELECT * FROM source_run WHERE source_run_id = ?"
    raise ValueError("unsupported source_run authority table")


def _source_run_by_id(conn: sqlite3.Connection, source_run_id: str) -> dict[str, Any] | None:
    table = _authority_table(conn, "source_run")
    if table is None:
        return None
    row = conn.execute(
        _source_run_by_id_sql(table),
        (source_run_id,),
    ).fetchone()
    return dict(row) if row else None


def _is_live_readiness(row: dict[str, Any], *, now_utc: datetime) -> str | None:
    if row.get("status") != "LIVE_ELIGIBLE":
        return (_readiness_reasons(row.get("reason_codes_json")) or ("READINESS_NOT_LIVE_ELIGIBLE",))[0]
    expires_at = _parse_utc(row.get("expires_at"))
    if expires_at is None:
        return "READINESS_EXPIRY_MISSING"
    if expires_at <= now_utc.astimezone(UTC):
        return "READINESS_EXPIRED"
    return None


def _latest_producer_readiness(
    conn: sqlite3.Connection,
    *,
    city_id: str,
    city_timezone: str,
    target_local_date: date,
    temperature_metric: str,
    data_version: str,
    source_id: str,
    track: str,
) -> dict[str, Any] | None:
    table = _authority_table(conn, "readiness_state")
    if table is None:
        return None
    if table == f"{FORECASTS_SCHEMA}.readiness_state":
        sql = """
        SELECT * FROM forecasts.readiness_state
        WHERE scope_type = 'city_metric'
          AND strategy_key = ?
          AND city_id = ?
          AND city_timezone = ?
          AND target_local_date = ?
          AND temperature_metric = ?
          AND data_version = ?
          AND source_id = ?
          AND track = ?
        ORDER BY computed_at DESC, recorded_at DESC
        LIMIT 1
        """
    elif table == f"{WORLD_SCHEMA}.readiness_state":
        sql = """
        SELECT * FROM world.readiness_state
        WHERE scope_type = 'city_metric'
          AND strategy_key = ?
          AND city_id = ?
          AND city_timezone = ?
          AND target_local_date = ?
          AND temperature_metric = ?
          AND data_version = ?
          AND source_id = ?
          AND track = ?
        ORDER BY computed_at DESC, recorded_at DESC
        LIMIT 1
        """
    elif table == "readiness_state":
        sql = """
        SELECT * FROM readiness_state
        WHERE scope_type = 'city_metric'
          AND strategy_key = ?
          AND city_id = ?
          AND city_timezone = ?
          AND target_local_date = ?
          AND temperature_metric = ?
          AND data_version = ?
          AND source_id = ?
          AND track = ?
        ORDER BY computed_at DESC, recorded_at DESC
        LIMIT 1
        """
    else:
        raise ValueError("unsupported readiness_state authority table")
    row = conn.execute(
        sql,
        (
            PRODUCER_READINESS_STRATEGY_KEY,
            city_id,
            city_timezone,
            target_local_date.isoformat(),
            temperature_metric,
            data_version,
            source_id,
            track,
        ),
    ).fetchone()
    return dict(row) if row else None


def _snapshot_query_sql(table: str, *, source_run_id_present: bool) -> str:
    if table == f"{FORECASTS_SCHEMA}.ensemble_snapshots":
        if source_run_id_present:
            return f"""
            -- Phase B7 (REMEDIATION_PLAN_2026-05-03.md): IS NOT NULL filters
            -- on source_run_id/release_calendar_key/source_cycle_time/
            -- source_release_time/source_available_at removed so legacy rows
            -- with missing linkage land here and are rejected by the post-check
            -- with FORECAST_SOURCE_LINKAGE_MISSING (reachable reason code)
            -- instead of being silently filtered as NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET.
            SELECT * FROM forecasts.ensemble_snapshots
            WHERE city = ?
              AND target_date = ?
              AND temperature_metric = ?
              AND dataset_id = ?
              AND source_id = ?
              AND source_transport = ?
              AND source_run_id = ?
            ORDER BY {_EXTREMA_RANK_ORDER_BY}
            LIMIT 1
            """
        return f"""
            -- Phase B7 (REMEDIATION_PLAN_2026-05-03.md): IS NOT NULL filters
            -- on source_run_id/release_calendar_key/source_cycle_time/
            -- source_release_time/source_available_at removed so legacy rows
            -- with missing linkage land here and are rejected by the post-check
            -- with FORECAST_SOURCE_LINKAGE_MISSING (reachable reason code)
            -- instead of being silently filtered as NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET.
            SELECT * FROM forecasts.ensemble_snapshots
            WHERE city = ?
              AND target_date = ?
              AND temperature_metric = ?
              AND dataset_id = ?
              AND source_id = ?
              AND source_transport = ?
            ORDER BY {_EXTREMA_RANK_ORDER_BY}
            LIMIT 1
            """
    elif table == f"{WORLD_SCHEMA}.ensemble_snapshots":
        if source_run_id_present:
            return f"""
            -- Phase B7 (REMEDIATION_PLAN_2026-05-03.md): IS NOT NULL filters
            -- on source_run_id/release_calendar_key/source_cycle_time/
            -- source_release_time/source_available_at removed so legacy rows
            -- with missing linkage land here and are rejected by the post-check
            -- with FORECAST_SOURCE_LINKAGE_MISSING (reachable reason code)
            -- instead of being silently filtered as NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET.
            SELECT * FROM world.ensemble_snapshots
            WHERE city = ?
              AND target_date = ?
              AND temperature_metric = ?
              AND dataset_id = ?
              AND source_id = ?
              AND source_transport = ?
              AND source_run_id = ?
            ORDER BY {_EXTREMA_RANK_ORDER_BY}
            LIMIT 1
            """
        return f"""
            -- Phase B7 (REMEDIATION_PLAN_2026-05-03.md): IS NOT NULL filters
            -- on source_run_id/release_calendar_key/source_cycle_time/
            -- source_release_time/source_available_at removed so legacy rows
            -- with missing linkage land here and are rejected by the post-check
            -- with FORECAST_SOURCE_LINKAGE_MISSING (reachable reason code)
            -- instead of being silently filtered as NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET.
            SELECT * FROM world.ensemble_snapshots
            WHERE city = ?
              AND target_date = ?
              AND temperature_metric = ?
              AND dataset_id = ?
              AND source_id = ?
              AND source_transport = ?
            ORDER BY {_EXTREMA_RANK_ORDER_BY}
            LIMIT 1
            """
    elif table == "ensemble_snapshots":
        if source_run_id_present:
            return f"""
            -- Phase B7 (REMEDIATION_PLAN_2026-05-03.md): IS NOT NULL filters
            -- on source_run_id/release_calendar_key/source_cycle_time/
            -- source_release_time/source_available_at removed so legacy rows
            -- with missing linkage land here and are rejected by the post-check
            -- with FORECAST_SOURCE_LINKAGE_MISSING (reachable reason code)
            -- instead of being silently filtered as NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET.
            SELECT * FROM ensemble_snapshots
            WHERE city = ?
              AND target_date = ?
              AND temperature_metric = ?
              AND dataset_id = ?
              AND source_id = ?
              AND source_transport = ?
              AND source_run_id = ?
            ORDER BY {_EXTREMA_RANK_ORDER_BY}
            LIMIT 1
            """
        return f"""
            -- Phase B7 (REMEDIATION_PLAN_2026-05-03.md): IS NOT NULL filters
            -- on source_run_id/release_calendar_key/source_cycle_time/
            -- source_release_time/source_available_at removed so legacy rows
            -- with missing linkage land here and are rejected by the post-check
            -- with FORECAST_SOURCE_LINKAGE_MISSING (reachable reason code)
            -- instead of being silently filtered as NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET.
            SELECT * FROM ensemble_snapshots
            WHERE city = ?
              AND target_date = ?
              AND temperature_metric = ?
              AND dataset_id = ?
              AND source_id = ?
              AND source_transport = ?
            ORDER BY {_EXTREMA_RANK_ORDER_BY}
            LIMIT 1
            """
    else:
        raise ValueError("unsupported ensemble_snapshots authority table")


def _coverage_for_producer(
    conn: sqlite3.Connection,
    *,
    producer: dict[str, Any],
) -> dict[str, Any] | None:
    dependency = producer.get("dependency_json")
    try:
        dependency_json = json.loads(dependency) if isinstance(dependency, str) else {}
    except json.JSONDecodeError:
        dependency_json = {}
    coverage_id = dependency_json.get("coverage_id")
    if coverage_id:
        return _source_run_coverage_by_id(conn, str(coverage_id))
    return None


def _row_is_source_linked(row: dict[str, Any]) -> bool:
    return all(
        row.get(field)
        for field in (
            "source_id",
            "source_transport",
            "source_run_id",
            "release_calendar_key",
            "source_cycle_time",
            "source_release_time",
            "source_available_at",
        )
    )


def read_executable_forecast_snapshot(
    conn: sqlite3.Connection,
    *,
    scope: ForecastTargetScope,
    source_id: str,
    source_transport: str = SOURCE_TRANSPORT,
    source_run_id: str | None = None,
    now_utc: datetime | None = None,
) -> ExecutableForecastReadResult:
    table = _authority_table(conn, "ensemble_snapshots")
    if table is None:
        return ExecutableForecastReadResult("BLOCKED", "NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET")
    params: list[Any] = [
        scope.city_name,
        scope.target_local_date.isoformat(),
        scope.temperature_metric,
        scope.data_version,
        source_id,
        source_transport,
    ]
    if source_run_id is not None:
        params.append(source_run_id)
    rows = [
        dict(row)
        for row in conn.execute(
            _snapshot_query_sql(table, source_run_id_present=source_run_id is not None),
            tuple(params),
        ).fetchall()
    ]
    if not rows:
        return ExecutableForecastReadResult("BLOCKED", "NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET")
    row = rows[0]
    if not _row_is_source_linked(row):
        return ExecutableForecastReadResult("BLOCKED", "FORECAST_SOURCE_LINKAGE_MISSING")
    if row.get("authority") != "VERIFIED":
        return ExecutableForecastReadResult("BLOCKED", "EXECUTABLE_FORECAST_AUTHORITY_NOT_VERIFIED")
    if row.get("causality_status") != "OK" or int(row.get("boundary_ambiguous") or 0) != 0:
        return ExecutableForecastReadResult("BLOCKED", "EXECUTABLE_FORECAST_CAUSALITY_NOT_OK")
    # P0 extrema authority (classifier-driven, §1/§2): block NON_CONTRIBUTOR and
    # UNKNOWN.  UNKNOWN now includes a NULL contributes flag on a CURRENT
    # data_version (fail-closed — a live mx2t3 row with missing provenance must
    # not pass).  A NULL flag on a LEGACY data_version classifies as
    # LEGACY_NULL_PASSTHROUGH and is allowed through (prior behavior preserved).
    # data_version is read from the row by the classifier.
    _extrema_auth = classify_forecast_extrema_authority(row)
    if _extrema_auth.eligibility == ForecastExtremaEligibility.NON_CONTRIBUTOR:
        return ExecutableForecastReadResult(
            "BLOCKED", "EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA"
        )
    if _extrema_auth.eligibility == ForecastExtremaEligibility.UNKNOWN:
        return ExecutableForecastReadResult(
            "BLOCKED", "EXECUTABLE_FORECAST_EXTREMA_AUTHORITY_UNKNOWN"
        )
    if now_utc is not None:
        if now_utc.tzinfo is None or now_utc.utcoffset() is None:
            return ExecutableForecastReadResult("UNKNOWN_BLOCKED", "READ_NOW_INVALID")
        available_at = _parse_utc(row.get("available_at"))
        if available_at is None or available_at > now_utc.astimezone(UTC):
            return ExecutableForecastReadResult("BLOCKED", "EXECUTABLE_FORECAST_NOT_AVAILABLE_YET")
    snapshot = ExecutableForecastSnapshot(
        snapshot_id=int(row["snapshot_id"]),
        city=str(row["city"]),
        target_local_date=_parse_date(row["target_date"]),
        temperature_metric=str(row["temperature_metric"]),
        data_version=str(row["dataset_id"]),
        members=_members(row["members_json"]),
        source_id=str(row["source_id"]),
        source_transport=str(row["source_transport"]),
        source_run_id=str(row["source_run_id"]),
        release_calendar_key=str(row["release_calendar_key"]),
        source_cycle_time=str(row["source_cycle_time"]),
        source_release_time=str(row["source_release_time"]),
        source_available_at=str(row["source_available_at"]),
        issue_time=str(row["issue_time"]),
        valid_time=str(row["valid_time"]),
        available_at=str(row["available_at"]),
        fetch_time=str(row["fetch_time"]),
        manifest_hash=row.get("manifest_hash"),
        members_unit=str(row["members_unit"]),
        local_day_start_utc=row.get("local_day_start_utc"),
        step_horizon_hours=float(row["step_horizon_hours"]) if row.get("step_horizon_hours") is not None else None,
        first_member_observed_time=str(row.get("first_member_observed_time") or ""),
        run_complete_time=str(row.get("run_complete_time") or ""),
        raw_orderbook_hash_transition_delta_ms=(
            int(row["raw_orderbook_hash_transition_delta_ms"])
            if row.get("raw_orderbook_hash_transition_delta_ms") is not None
            else None
        ),
    )
    return ExecutableForecastReadResult("LIVE_ELIGIBLE", "EXECUTABLE_FORECAST_READY", snapshot)


@dataclass(frozen=True)
class ExecutableForecastBundleCandidate:
    """One fully-resolved, gate-passing forecast bundle candidate.

    All four evidence layers (coverage, source_run, snapshot, derived
    producer-readiness id) reference the SAME source_run_id/coverage_id — the
    candidate is internally coherent before it enters ranking (P0 follow-up
    §1.3 evidence coherence).  ``eligibility`` is the extrema-authority class of
    the candidate's snapshot, used by ``_bundle_rank`` to prefer contributing
    cycles over later non-contributing ones.
    """

    snapshot: ExecutableForecastSnapshot
    evidence: ExecutableForecastEvidence
    eligibility: ForecastExtremaEligibility
    coverage: dict[str, Any]
    source_run: dict[str, Any]
    snapshot_row: dict[str, Any]


def _evaluate_candidate(
    conn: sqlite3.Connection,
    *,
    coverage: dict[str, Any],
    producer: dict[str, Any],
    entry: dict[str, Any] | None,
    city_id: str,
    city_name: str,
    city_timezone: str,
    target_local_date: date,
    temperature_metric: str,
    source_id: str,
    source_transport: str,
    data_version: str,
    condition_id: str,
    now: datetime,
    require_entry_readiness: bool,
) -> tuple[ExecutableForecastBundleCandidate | None, str | None]:
    """Run the per-bundle causality + completeness + member-floor + coverage-
    membership gates for a single ``coverage`` row.

    Returns ``(candidate, None)`` when every gate passes, else ``(None, reason)``
    where ``reason`` is the SAME single-path BLOCKED reason code the legacy
    reader produced (diagnosability is preserved).  A candidate that fails any
    gate is DROPPED from the candidate list rather than returned as a global
    block (P0 follow-up §1.1).

    Per-candidate producer-readiness handling (operator-surfaced, approach
    (iii)): only ONE readiness_state row survives in the DB (write_readiness_state
    UPSERTs on the scope tuple, which excludes source_run_id), so historical-cycle
    producer rows are not retrievable.  The producer-readiness LIVE_ELIGIBLE
    classification is, however, a pure function of the coverage row, and the
    writer derives ``readiness_id = f"producer_readiness:{coverage_id}"``.  We
    therefore (a) derive the producer_readiness_id from this candidate's
    coverage_id for evidence coherence, and (b) use the coverage's own
    ``computed_at`` as the per-candidate producer-readiness stamp in the
    causal-order check.  The scope-level readiness liveness gate (status +
    expires_at + entry-readiness presence) is applied once by the caller.
    """
    # Member floor BEFORE completeness/readiness gates (see read_executable_forecast
    # docstring): PARTIAL coverage whose only shortfall is member count is judged
    # against the statistical floor, not hard-blocked as SOURCE_RUN_PARTIAL.
    expected_members = int(coverage.get("expected_members") or 0)
    observed_members = int(coverage.get("observed_members") or 0)
    min_floor = settings["ensemble"].get("min_members_floor", expected_members)
    if expected_members <= 0 or observed_members < min_floor:
        return None, "MISSING_EXPECTED_MEMBERS"
    completeness_status = str(coverage.get("completeness_status") or "")
    if completeness_status != "COMPLETE":
        if completeness_status != "PARTIAL":
            mapping = {
                "MISSING": "FUTURE_TARGET_DATE_NOT_COVERED",
                "NOT_RELEASED": "SOURCE_RUN_NOT_RELEASED",
                "HORIZON_OUT_OF_RANGE": "SOURCE_RUN_HORIZON_OUT_OF_RANGE",
            }
            return None, mapping.get(completeness_status, "SOURCE_RUN_FAILED")
    if completeness_status == "COMPLETE" and coverage.get("readiness_status") != "LIVE_ELIGIBLE":
        return None, str(coverage.get("reason_code") or "SOURCE_RUN_COVERAGE_NOT_LIVE_ELIGIBLE")
    # Per-candidate readiness liveness: an older (e.g. 00Z) coverage whose
    # expires_at is already in the past relative to `now` must drop with
    # READINESS_EXPIRED (mirrors _is_live_readiness on the scope-level row).
    coverage_expires_at = _parse_utc(coverage.get("expires_at"))
    if completeness_status == "COMPLETE":
        if coverage_expires_at is None:
            return None, "READINESS_EXPIRY_MISSING"
        if coverage_expires_at <= now:
            return None, "READINESS_EXPIRED"
    expected_steps = _int_tuple(coverage.get("expected_steps_json"))
    observed_steps = _int_tuple(coverage.get("observed_steps_json"))
    if not set(expected_steps).issubset(set(observed_steps)):
        return None, "MISSING_REQUIRED_STEPS"

    source_run = _source_run_by_id(conn, str(coverage["source_run_id"]))
    if source_run is None:
        return None, "SOURCE_RUN_MISSING"
    if source_run.get("status") not in {"SUCCESS", "PARTIAL"}:
        return None, "SOURCE_RUN_FAILED"
    if source_run.get("completeness_status") not in {"COMPLETE", "PARTIAL"}:
        return None, "SOURCE_RUN_PARTIAL"

    # F1 antibody: unparseable coverage window is an error, not a fallback.
    # Do NOT use `or now` — that mask was the exact bug the F1 antibody was built to catch.
    coverage_window_start_utc = _parse_utc(coverage.get("target_window_start_utc"))
    coverage_window_end_utc = _parse_utc(coverage.get("target_window_end_utc"))
    if coverage_window_start_utc is None or coverage_window_end_utc is None:
        return None, "COVERAGE_WINDOW_UNPARSEABLE"
    source_cycle_time = _parse_utc(source_run.get("source_cycle_time"))
    if source_cycle_time is None:
        return None, "SOURCE_CYCLE_TIME_UNPARSEABLE"

    scope = ForecastTargetScope(
        city_id=city_id,
        city_name=city_name,
        city_timezone=city_timezone,
        target_local_date=target_local_date,
        temperature_metric=temperature_metric,
        data_version=data_version,
        target_window_start_utc=coverage_window_start_utc,
        target_window_end_utc=coverage_window_end_utc,
        source_cycle_time=source_cycle_time,
        required_step_hours=expected_steps,
        market_refs=(condition_id,),
    )
    snapshot_result = read_executable_forecast_snapshot(
        conn,
        scope=scope,
        source_id=source_id,
        source_transport=source_transport,
        source_run_id=str(coverage["source_run_id"]),
        now_utc=now,
    )
    if not snapshot_result.ok or snapshot_result.snapshot is None:
        return None, snapshot_result.reason_code
    snapshot = snapshot_result.snapshot
    if snapshot.target_local_date != target_local_date:
        return None, "SNAPSHOT_TARGET_DATE_MISMATCH"
    if snapshot.temperature_metric != temperature_metric:
        return None, "SNAPSHOT_METRIC_MISMATCH"
    coverage_window_start = _parse_utc(coverage.get("target_window_start_utc"))
    snapshot_window_start = _parse_utc(snapshot.local_day_start_utc)
    if coverage_window_start is None or snapshot_window_start != coverage_window_start:
        return None, "SNAPSHOT_LOCAL_DAY_WINDOW_MISMATCH"
    if len(snapshot.members) < min_floor:
        return None, "MISSING_EXPECTED_MEMBERS"
    coverage_snapshot_ids = tuple(
        int(item) for item in _json_list(coverage.get("snapshot_ids_json")) if str(item).isdigit()
    )
    if coverage_snapshot_ids and snapshot.snapshot_id not in coverage_snapshot_ids:
        return None, "SNAPSHOT_NOT_IN_COVERAGE"

    source_available_at = _parse_utc(source_run.get("source_available_at"))
    captured_at = _parse_utc(source_run.get("captured_at"))
    # Per-candidate producer-readiness stamp: the coverage's own computed_at is
    # the wall-clock at which THIS coverage was classified ready (the value the
    # writer would have stamped on its producer_readiness row).  Using it keeps
    # the causal-order check coherent for non-latest cycles whose readiness_state
    # row no longer exists in the DB.
    producer_computed_at = _parse_utc(coverage.get("computed_at"))
    if source_available_at is None:
        return None, "SOURCE_AVAILABLE_AT_MISSING"
    if captured_at is None:
        return None, "SOURCE_RUN_CAPTURED_AT_MISSING"
    if producer_computed_at is None:
        return None, "READINESS_COMPUTED_AT_INVALID"
    if require_entry_readiness:
        entry_computed_at = _parse_utc(entry.get("computed_at")) if entry is not None else None
        if entry_computed_at is None:
            return None, "READINESS_COMPUTED_AT_INVALID"
    else:
        entry_computed_at = producer_computed_at
    if source_available_at > captured_at:
        return None, "SOURCE_AVAILABLE_AFTER_CAPTURE"
    if source_available_at > now:
        return None, "SOURCE_AVAILABLE_AFTER_DECISION_TIME"
    if captured_at > now:
        return None, "SOURCE_CAPTURED_AFTER_DECISION_TIME"
    # Causal-order: capture <= producer-readiness <= entry-readiness.  The
    # entry stamp is the live (scope-level) entry row; the producer stamp is
    # this candidate's coverage computed_at.  When require_entry_readiness is
    # False, entry_computed_at == producer_computed_at so the second clause is
    # a no-op.  Note: when an older 00Z coverage is selected against a later
    # entry-readiness row, producer_computed_at (00Z) <= entry_computed_at is
    # the expected order, so the check holds.
    if captured_at > producer_computed_at or producer_computed_at > entry_computed_at:
        return None, "READINESS_TIMING_ORDER_INVALID"

    # Classify the candidate's extrema authority against the raw DB row (which
    # carries the contributes/attribution columns the snapshot dataclass drops).
    snapshot_row = _snapshot_row_for_classification(conn, snapshot, source_id, source_transport)
    eligibility = classify_forecast_extrema_authority(snapshot_row).eligibility
    candidate_validations: tuple[str, ...] = (
        "source_run_completeness_status",
        "coverage_completeness_status",
        "coverage_readiness_status",
        "required_steps_observed",
        "expected_members_observed",
        "causality_status_ok",
        "authority_verified",
        "available_at_not_future",
    )
    if eligibility == ForecastExtremaEligibility.LEGACY_NULL_PASSTHROUGH:
        candidate_validations = (*candidate_validations, LEGACY_NULL_PASSTHROUGH_VALIDATION)

    producer_readiness_id = f"producer_readiness:{coverage['coverage_id']}"
    evidence = ExecutableForecastEvidence(
        forecast_source_id=source_id,
        forecast_data_version=data_version,
        source_transport=source_transport,
        source_run_id=str(coverage["source_run_id"]),
        release_calendar_key=str(coverage["release_calendar_key"]),
        coverage_id=str(coverage["coverage_id"]),
        producer_readiness_id=producer_readiness_id,
        entry_readiness_id=str(entry["readiness_id"]) if entry is not None else None,
        source_cycle_time=str(source_run["source_cycle_time"]),
        source_issue_time=source_run.get("source_issue_time"),
        source_release_time=str(source_run["source_release_time"]),
        source_available_at=str(source_run["source_available_at"]),
        fetch_started_at=source_run.get("fetch_started_at"),
        fetch_finished_at=source_run.get("fetch_finished_at"),
        captured_at=str(source_run["captured_at"]),
        input_snapshot_ids=(snapshot.snapshot_id,),
        raw_payload_hash=source_run.get("raw_payload_hash"),
        manifest_hash=snapshot.manifest_hash or source_run.get("manifest_hash"),
        target_local_date=target_local_date.isoformat(),
        target_window_start_utc=str(coverage["target_window_start_utc"]),
        target_window_end_utc=str(coverage["target_window_end_utc"]),
        city_timezone=city_timezone,
        required_steps=expected_steps,
        observed_steps=observed_steps,
        expected_members=expected_members,
        observed_members=observed_members,
        source_run_status=str(source_run.get("status") or ""),
        source_run_completeness_status=str(source_run.get("completeness_status") or ""),
        coverage_completeness_status=str(coverage.get("completeness_status") or ""),
        coverage_readiness_status=(
            None if coverage.get("readiness_status") is None else str(coverage.get("readiness_status"))
        ),
        applied_validations=candidate_validations,
    )
    candidate = ExecutableForecastBundleCandidate(
        snapshot=snapshot,
        evidence=evidence,
        eligibility=eligibility,
        coverage=coverage,
        source_run=source_run,
        snapshot_row=snapshot_row,
    )
    return candidate, None


def _snapshot_row_for_classification(
    conn: sqlite3.Connection,
    snapshot: ExecutableForecastSnapshot,
    source_id: str,
    source_transport: str,
) -> dict[str, Any]:
    """Re-read the snapshot's extrema-authority columns for classification.

    ``ExecutableForecastSnapshot`` does not carry the contributes/attribution
    columns, so classification needs the raw DB row.  Looked up by snapshot_id
    on the authoritative table.

    p0-2-hardening: when the row is not found (table missing or snapshot_id
    unknown), inject ``data_version`` from the snapshot object so the classifier
    can apply the correct tri-state gate.  Without this, an empty dict yields
    data_version=None, which previously fell through to LEGACY_NULL_PASSTHROUGH
    instead of UNKNOWN (fail-closed).
    """
    table = _authority_table(conn, "ensemble_snapshots")
    if table is None:
        # Table not found — return sentinel with known data_version so the
        # classifier fails closed (UNKNOWN) rather than passing through as legacy.
        return {"data_version": snapshot.data_version}
    # SELECT * so the classifier sees every contributes/attribution column
    # without coupling this query to the exact schema column set (the
    # classifier already tolerates a missing short-alias attribution_status).
    row = conn.execute(
        f"SELECT * FROM {table} WHERE snapshot_id = ?",
        (snapshot.snapshot_id,),
    ).fetchone()
    if row:
        return dict(row)
    # Row not found — sentinel with known data_version (same fail-closed rationale).
    return {"data_version": snapshot.data_version}


def _bundle_rank(candidate: ExecutableForecastBundleCandidate) -> tuple[int, float, float, int]:
    """Sort key for candidate bundles — lower sorts first (min() wins).

    Contributor class dominates: a FULL_CONTRIBUTOR (e.g. an earlier 00Z run)
    outranks any non-full-contributor (e.g. a later 12Z post-peak run) REGARDLESS
    of recency.  Recency (source_cycle_time, then available_at, then snapshot_id)
    breaks ties only WITHIN the same contributor class (P0 follow-up §1.2).
    """
    contributor_rank = 0 if candidate.eligibility == ForecastExtremaEligibility.FULL_CONTRIBUTOR else 1
    cycle_epoch = _epoch_or_zero(candidate.source_run.get("source_cycle_time"))
    available_epoch = _epoch_or_zero(candidate.snapshot.available_at)
    return (contributor_rank, -cycle_epoch, -available_epoch, -candidate.snapshot.snapshot_id)


def _epoch_or_zero(value: object) -> float:
    parsed = _parse_utc(value if isinstance(value, str) else None)
    return parsed.timestamp() if parsed is not None else 0.0


def _candidate_forecast_bundles(
    conn: sqlite3.Connection,
    *,
    producer: dict[str, Any],
    entry: dict[str, Any] | None,
    city_id: str,
    city_name: str,
    city_timezone: str,
    target_local_date: date,
    temperature_metric: str,
    source_id: str,
    source_transport: str,
    data_version: str,
    condition_id: str,
    now: datetime,
    require_entry_readiness: bool,
) -> list[ExecutableForecastBundleCandidate]:
    """Enumerate every gate-passing forecast bundle for the scope.

    One candidate per eligible source_run_coverage row (00Z, 12Z, …).  Each is
    independently gated by ``_evaluate_candidate``; rows failing any gate are
    dropped.  The returned list is unranked — the caller applies ``_bundle_rank``.
    """
    coverages = _source_run_coverages_for_scope(
        conn,
        city_id=city_id,
        city_timezone=city_timezone,
        target_local_date=target_local_date,
        temperature_metric=temperature_metric,
        source_id=source_id,
        source_transport=source_transport,
        data_version=data_version,
    )
    candidates: list[ExecutableForecastBundleCandidate] = []
    for coverage in coverages:
        candidate, _drop_reason = _evaluate_candidate(
            conn,
            coverage=coverage,
            producer=producer,
            entry=entry,
            city_id=city_id,
            city_name=city_name,
            city_timezone=city_timezone,
            target_local_date=target_local_date,
            temperature_metric=temperature_metric,
            source_id=source_id,
            source_transport=source_transport,
            data_version=data_version,
            condition_id=condition_id,
            now=now,
            require_entry_readiness=require_entry_readiness,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def read_executable_forecast(
    conn: sqlite3.Connection,
    *,
    city_id: str,
    city_name: str,
    city_timezone: str,
    target_local_date: date,
    temperature_metric: str,
    source_id: str,
    source_transport: str,
    data_version: str,
    track: str,
    strategy_key: str,
    market_family: str,
    condition_id: str,
    decision_time: datetime,
    require_entry_readiness: bool = True,
) -> ExecutableForecastBundleResult:
    """Read and validate a live-eligible executable forecast bundle.

    Member floor (``ensemble.min_members_floor`` in settings.json, default 40):
    ECMWF Open Data routinely delivers partial ensembles of 48-50 members rather
    than the full 51.  A floor of 40 is statistically sufficient for variance
    estimation under our Monte Carlo strategies; the strict 51-only gate caused
    ~40-45% systematic discard.  The floor is applied BEFORE the coverage
    completeness gate so that PARTIAL coverage rows whose only shortfall is member
    count are evaluated against the floor rather than hard-blocked as
    SOURCE_RUN_PARTIAL.  Values < 40 remain fail-closed (MISSING_EXPECTED_MEMBERS).
    See ``.omc/plans/2026-05-19-ensemble-member-floor.md`` for full justification.
    """
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        return ExecutableForecastBundleResult("UNKNOWN_BLOCKED", "READINESS_NOW_INVALID")
    now = decision_time.astimezone(UTC)
    producer = _latest_producer_readiness(
        conn,
        city_id=city_id,
        city_timezone=city_timezone,
        target_local_date=target_local_date,
        temperature_metric=temperature_metric,
        data_version=data_version,
        source_id=source_id,
        track=track,
    )
    if producer is None:
        return ExecutableForecastBundleResult("BLOCKED", "PRODUCER_READINESS_MISSING")
    # review5.23 P1-1: do NOT hard-gate on scope-level producer readiness before
    # enumeration.  readiness_state uses ON CONFLICT(scope_key) DO UPDATE so newer
    # cycles overwrite older ones; a blocked 12Z row must not prevent a valid 00Z
    # coverage from being returned.  producer_reason is used as a diagnostic
    # fallback ONLY when enumeration yields no passing candidates.
    producer_reason = _is_live_readiness(producer, now_utc=now)

    entry = None
    if require_entry_readiness:
        entry = get_entry_readiness(
            conn,
            city_id=city_id,
            city_timezone=city_timezone,
            target_local_date=target_local_date,
            temperature_metric=temperature_metric,
            physical_quantity=str(producer.get("physical_quantity")),
            observation_field=str(producer.get("observation_field")),
            data_version=data_version,
            source_id=source_id,
            track=track,
            strategy_key=strategy_key,
            market_family=market_family,
            condition_id=condition_id,
            now_utc=now,
        )
        entry_reason = _is_live_readiness(entry, now_utc=now)
        if entry_reason is not None:
            return ExecutableForecastBundleResult("BLOCKED", entry_reason)
        if not entry.get("readiness_id"):
            return ExecutableForecastBundleResult("BLOCKED", "ENTRY_READINESS_MISSING")

    # P0 follow-up §1: lift selection to the full forecast-bundle layer.
    # Enumerate ALL eligible bundles (one per source_run_coverage cycle), gate
    # each independently, then rank by extrema authority so an earlier 00Z
    # FULL_CONTRIBUTOR outranks a later 12Z NON_CONTRIBUTOR.  The single-path
    # reader (latest producer -> its coverage -> its snapshot) locked the bundle
    # to whichever cycle was computed last; the contributor-first ORDER BY only
    # reshuffled snapshots WITHIN that one run.  This enumeration is the fix.
    candidates = _candidate_forecast_bundles(
        conn,
        producer=producer,
        entry=entry,
        city_id=city_id,
        city_name=city_name,
        city_timezone=city_timezone,
        target_local_date=target_local_date,
        temperature_metric=temperature_metric,
        source_id=source_id,
        source_transport=source_transport,
        data_version=data_version,
        condition_id=condition_id,
        now=now,
        require_entry_readiness=require_entry_readiness,
    )
    if not candidates:
        # No candidate passed every gate.
        # If the latest scope-level producer readiness was already blocked, surface
        # that reason directly — it is the authoritative explanation for why no
        # bundle could be elected (review5.23 P1-1 diagnostic fallback).
        if producer_reason is not None:
            return ExecutableForecastBundleResult("BLOCKED", producer_reason)
        # Otherwise fall back to the producer's own coverage ONCE to surface its
        # specific per-coverage BLOCKED reason code (diagnosability — §1.3).
        coverage = _coverage_for_producer(conn, producer=producer)
        if coverage is None:
            return ExecutableForecastBundleResult("BLOCKED", "SOURCE_RUN_COVERAGE_MISSING")
        _candidate, drop_reason = _evaluate_candidate(
            conn,
            coverage=coverage,
            producer=producer,
            entry=entry,
            city_id=city_id,
            city_name=city_name,
            city_timezone=city_timezone,
            target_local_date=target_local_date,
            temperature_metric=temperature_metric,
            source_id=source_id,
            source_transport=source_transport,
            data_version=data_version,
            condition_id=condition_id,
            now=now,
            require_entry_readiness=require_entry_readiness,
        )
        return ExecutableForecastBundleResult(
            "BLOCKED", drop_reason or "SOURCE_RUN_COVERAGE_MISSING"
        )

    best = min(candidates, key=_bundle_rank)
    # Block semantics on the SELECTED (highest-ranked) candidate — §1.3.
    # NON_CONTRIBUTOR / UNKNOWN are typed fail-closed.
    # PARTIAL_CONTRIBUTOR is retired from the live path (review5.23 P1-4):
    # boundary_ambiguous=1 rows are now classified as NON_CONTRIBUTOR by
    # classify_forecast_extrema_authority AND blocked as EXECUTABLE_FORECAST_CAUSALITY_NOT_OK
    # by read_executable_forecast_snapshot, so they are dropped by _evaluate_candidate
    # and never reach this point as a selected candidate.
    #
    # DEFENSIVE / defense-in-depth: in the normal flow these two branches are
    # unreachable, because read_executable_forecast_snapshot already BLOCKS
    # NON_CONTRIBUTOR and UNKNOWN snapshots (so _evaluate_candidate drops them
    # and they never enter `candidates`).  An only-non-contributor scope thus
    # surfaces its reason via the empty-candidate fallback above, not here.
    # These checks remain as a fail-closed guard that stays correct if the
    # snapshot reader's extrema policy ever diverges from the bundle ranker.
    if best.eligibility == ForecastExtremaEligibility.NON_CONTRIBUTOR:
        return ExecutableForecastBundleResult(
            "BLOCKED", "EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA"
        )
    if best.eligibility == ForecastExtremaEligibility.UNKNOWN:
        return ExecutableForecastBundleResult(
            "BLOCKED", "EXECUTABLE_FORECAST_EXTREMA_AUTHORITY_UNKNOWN"
        )
    return ExecutableForecastBundleResult(
        "LIVE_ELIGIBLE",
        "EXECUTABLE_FORECAST_READY",
        ExecutableForecastBundle(snapshot=best.snapshot, evidence=best.evidence),
    )
