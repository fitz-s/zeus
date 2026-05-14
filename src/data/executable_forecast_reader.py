"""Executable forecast reader for V4 source-linked ensemble snapshots."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY
from src.data.forecast_target_contract import ForecastTargetScope
from src.state.readiness_repo import get_entry_readiness

UTC = timezone.utc
SOURCE_TRANSPORT = "ensemble_snapshots_v2_db_reader"
WORLD_SCHEMA = "world"
# K1 split 2026-05-11 moved forecast-class rows out of zeus-world.db. The
# live data-daemon authority split extends that ownership to source coverage
# and producer readiness. When both schemas are ATTACHed, forecasts is the
# authoritative source for the forecast execution chain; world is legacy
# fallback only for pre-split connections that have no forecasts attach.
FORECASTS_SCHEMA = "forecasts"
WORLD_OWNED_TABLES = frozenset({
    "ensemble_snapshots_v2",
    "readiness_state",
    "source_run",
    "source_run_coverage",
})
# Tables that migrated to zeus-forecasts.db in K1 but may still appear in
# world (pre-migration connections) or main (no ATTACH at all).
FORECASTS_OWNED_TABLES = frozenset({
    "ensemble_snapshots_v2",
    "source_run",
    "source_run_coverage",
})
PRODUCER_READINESS_TABLE = "readiness_state"


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


@dataclass(frozen=True)
class ExecutableForecastEvidence:
    forecast_source_id: str
    forecast_data_version: str
    source_transport: str
    source_run_id: str
    release_calendar_key: str
    coverage_id: str
    producer_readiness_id: str
    entry_readiness_id: str
    source_cycle_time: str
    source_issue_time: str | None
    source_release_time: str
    source_available_at: str
    captured_at: str
    input_snapshot_ids: tuple[int, ...]
    raw_payload_hash: str | None
    manifest_hash: str | None
    target_local_date: str
    city_timezone: str
    required_steps: tuple[int, ...]
    observed_steps: tuple[int, ...]
    expected_members: int
    observed_members: int


@dataclass(frozen=True)
class ExecutableForecastBundle:
    snapshot: ExecutableForecastSnapshot
    evidence: ExecutableForecastEvidence

    def to_ens_result(self) -> dict[str, Any]:
        evidence_hash = self.evidence.raw_payload_hash or self.evidence.manifest_hash
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
            "fetch_time": self.evidence.captured_at,
            "available_at": self.evidence.source_available_at,
            "executable_forecast_evidence": self.evidence,
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


def _table_exists(conn: sqlite3.Connection, *, schema: str, table: str) -> bool:
    if schema not in {"main", WORLD_SCHEMA, FORECASTS_SCHEMA} or table not in WORLD_OWNED_TABLES:
        raise ValueError("unsupported executable forecast authority table")
    if not _schema_attached(conn, schema):
        return False
    row = conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _schema_attached(conn: sqlite3.Connection, schema: str) -> bool:
    return schema == "main" or any(row[1] == schema for row in conn.execute("PRAGMA database_list"))


def _resolve_owned_table(conn: sqlite3.Connection, table: str) -> str | None:
    """Return the qualified ``schema.table`` reference for *table*, or ``None``.

    Resolution order (K1 split 2026-05-11):
    1. ``forecasts`` schema attached and table is forecast-owned → check there.
       Do not fall back to world when forecasts is attached but missing the
       table; that would hide a broken forecast authority chain behind stale
       shadow rows.
    2. ``world`` schema attached → legacy fallback for pre-K1 connections or
       world-class tables.
    3. Neither schema attached → fall back to unqualified ``table`` (``main``).
    Returns ``None`` only when a schema is attached but the table is absent
    from every attached schema — indicates the row does not exist yet.
    """
    if table not in WORLD_OWNED_TABLES:
        raise ValueError("unsupported executable forecast authority table")
    world_attached = _schema_attached(conn, WORLD_SCHEMA)
    forecasts_attached = _schema_attached(conn, FORECASTS_SCHEMA)
    if not world_attached and not forecasts_attached:
        # No ATTACHes (e.g. bare main-only connection in tests).
        return table
    if forecasts_attached and table in FORECASTS_OWNED_TABLES:
        if not _table_exists(conn, schema=FORECASTS_SCHEMA, table=table):
            return None
        return f"{FORECASTS_SCHEMA}.{table}"
    if world_attached and _table_exists(conn, schema=WORLD_SCHEMA, table=table):
        return f"{WORLD_SCHEMA}.{table}"
    # Schema(s) attached but table found in neither — caller should treat as missing.
    return None


def _producer_readiness_table(conn: sqlite3.Connection) -> str | None:
    """Resolve producer readiness authority without falling back past forecasts."""
    world_attached = _schema_attached(conn, WORLD_SCHEMA)
    forecasts_attached = _schema_attached(conn, FORECASTS_SCHEMA)
    if not world_attached and not forecasts_attached:
        return PRODUCER_READINESS_TABLE
    if forecasts_attached:
        if not _table_exists(conn, schema=FORECASTS_SCHEMA, table=PRODUCER_READINESS_TABLE):
            return None
        return f"{FORECASTS_SCHEMA}.{PRODUCER_READINESS_TABLE}"
    if world_attached and _table_exists(conn, schema=WORLD_SCHEMA, table=PRODUCER_READINESS_TABLE):
        return f"{WORLD_SCHEMA}.{PRODUCER_READINESS_TABLE}"
    return None


# Backward-compat alias so any module that imported _world_owned_table directly
# still works without changes.
_world_owned_table = _resolve_owned_table


def _source_run_coverage_by_id(conn: sqlite3.Connection, coverage_id: str) -> dict[str, Any] | None:
    table = _world_owned_table(conn, "source_run_coverage")
    if table is None:
        return None
    row = conn.execute(
        f"SELECT * FROM {table} WHERE coverage_id = ?",
        (coverage_id,),
    ).fetchone()
    return dict(row) if row else None


def _source_run_by_id(conn: sqlite3.Connection, source_run_id: str) -> dict[str, Any] | None:
    table = _world_owned_table(conn, "source_run")
    if table is None:
        return None
    row = conn.execute(
        f"SELECT * FROM {table} WHERE source_run_id = ?",
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
    table = _producer_readiness_table(conn)
    if table is None:
        return None
    row = conn.execute(
        f"""
        SELECT * FROM {table}
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
        """,
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
    source_run_filter = "AND source_run_id = ?" if source_run_id is not None else ""
    table = _world_owned_table(conn, "ensemble_snapshots_v2")
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
            f"""
            -- Phase B7 (REMEDIATION_PLAN_2026-05-03.md): IS NOT NULL filters
            -- on source_run_id/release_calendar_key/source_cycle_time/
            -- source_release_time/source_available_at removed so legacy rows
            -- with missing linkage land here and are rejected by the post-check
            -- with FORECAST_SOURCE_LINKAGE_MISSING (reachable reason code)
            -- instead of being silently filtered as NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET.
            SELECT * FROM {table}
            WHERE city = ?
              AND target_date = ?
              AND temperature_metric = ?
              AND data_version = ?
              AND source_id = ?
              AND source_transport = ?
              {source_run_filter}
            ORDER BY source_cycle_time DESC, available_at DESC, snapshot_id DESC
            LIMIT 1
            """,
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
        data_version=str(row["data_version"]),
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
    )
    return ExecutableForecastReadResult("LIVE_ELIGIBLE", "EXECUTABLE_FORECAST_READY", snapshot)


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
) -> ExecutableForecastBundleResult:
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
    producer_reason = _is_live_readiness(producer, now_utc=now)
    if producer_reason is not None:
        return ExecutableForecastBundleResult("BLOCKED", producer_reason)

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

    coverage = _coverage_for_producer(conn, producer=producer)
    if coverage is None:
        return ExecutableForecastBundleResult("BLOCKED", "SOURCE_RUN_COVERAGE_MISSING")
    if coverage.get("completeness_status") != "COMPLETE":
        mapping = {
            "PARTIAL": "SOURCE_RUN_PARTIAL",
            "MISSING": "FUTURE_TARGET_DATE_NOT_COVERED",
            "NOT_RELEASED": "SOURCE_RUN_NOT_RELEASED",
            "HORIZON_OUT_OF_RANGE": "SOURCE_RUN_HORIZON_OUT_OF_RANGE",
        }
        return ExecutableForecastBundleResult(
            "BLOCKED",
            mapping.get(str(coverage.get("completeness_status")), "SOURCE_RUN_FAILED"),
        )
    if coverage.get("readiness_status") != "LIVE_ELIGIBLE":
        return ExecutableForecastBundleResult(
            "BLOCKED",
            str(coverage.get("reason_code") or "SOURCE_RUN_COVERAGE_NOT_LIVE_ELIGIBLE"),
        )
    expected_steps = _int_tuple(coverage.get("expected_steps_json"))
    observed_steps = _int_tuple(coverage.get("observed_steps_json"))
    if not set(expected_steps).issubset(set(observed_steps)):
        return ExecutableForecastBundleResult("BLOCKED", "MISSING_REQUIRED_STEPS")
    expected_members = int(coverage.get("expected_members") or 0)
    observed_members = int(coverage.get("observed_members") or 0)
    if expected_members <= 0 or observed_members < expected_members:
        return ExecutableForecastBundleResult("BLOCKED", "MISSING_EXPECTED_MEMBERS")

    source_run = _source_run_by_id(conn, str(coverage["source_run_id"]))
    if source_run is None:
        return ExecutableForecastBundleResult("BLOCKED", "SOURCE_RUN_MISSING")
    if source_run.get("status") != "SUCCESS":
        return ExecutableForecastBundleResult("BLOCKED", "SOURCE_RUN_FAILED")
    if source_run.get("completeness_status") != "COMPLETE":
        return ExecutableForecastBundleResult("BLOCKED", "SOURCE_RUN_PARTIAL")

    scope = ForecastTargetScope(
        city_id=city_id,
        city_name=city_name,
        city_timezone=city_timezone,
        target_local_date=target_local_date,
        temperature_metric=temperature_metric,
        data_version=data_version,
        target_window_start_utc=_parse_utc(coverage.get("target_window_start_utc")) or now,
        target_window_end_utc=_parse_utc(coverage.get("target_window_end_utc")) or now,
        source_cycle_time=_parse_utc(source_run.get("source_cycle_time")) or now,
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
        return ExecutableForecastBundleResult("BLOCKED", snapshot_result.reason_code)
    snapshot = snapshot_result.snapshot
    if snapshot.target_local_date != target_local_date:
        return ExecutableForecastBundleResult("BLOCKED", "SNAPSHOT_TARGET_DATE_MISMATCH")
    if snapshot.temperature_metric != temperature_metric:
        return ExecutableForecastBundleResult("BLOCKED", "SNAPSHOT_METRIC_MISMATCH")
    coverage_window_start = _parse_utc(coverage.get("target_window_start_utc"))
    snapshot_window_start = _parse_utc(snapshot.local_day_start_utc)
    if coverage_window_start is None or snapshot_window_start != coverage_window_start:
        return ExecutableForecastBundleResult("BLOCKED", "SNAPSHOT_LOCAL_DAY_WINDOW_MISMATCH")
    if len(snapshot.members) < expected_members:
        return ExecutableForecastBundleResult("BLOCKED", "MISSING_EXPECTED_MEMBERS")
    coverage_snapshot_ids = tuple(int(item) for item in _json_list(coverage.get("snapshot_ids_json")) if str(item).isdigit())
    if coverage_snapshot_ids and snapshot.snapshot_id not in coverage_snapshot_ids:
        return ExecutableForecastBundleResult("BLOCKED", "SNAPSHOT_NOT_IN_COVERAGE")

    source_available_at = _parse_utc(source_run.get("source_available_at"))
    captured_at = _parse_utc(source_run.get("captured_at"))
    producer_computed_at = _parse_utc(producer.get("computed_at"))
    entry_computed_at = _parse_utc(entry.get("computed_at"))
    if source_available_at is None:
        return ExecutableForecastBundleResult("BLOCKED", "SOURCE_AVAILABLE_AT_MISSING")
    if captured_at is None:
        return ExecutableForecastBundleResult("BLOCKED", "SOURCE_RUN_CAPTURED_AT_MISSING")
    if producer_computed_at is None or entry_computed_at is None:
        return ExecutableForecastBundleResult("BLOCKED", "READINESS_COMPUTED_AT_INVALID")
    if source_available_at > captured_at:
        return ExecutableForecastBundleResult("BLOCKED", "SOURCE_AVAILABLE_AFTER_CAPTURE")
    if captured_at > producer_computed_at or producer_computed_at > entry_computed_at or entry_computed_at > now:
        return ExecutableForecastBundleResult("BLOCKED", "READINESS_TIMING_ORDER_INVALID")

    evidence = ExecutableForecastEvidence(
        forecast_source_id=source_id,
        forecast_data_version=data_version,
        source_transport=source_transport,
        source_run_id=str(coverage["source_run_id"]),
        release_calendar_key=str(coverage["release_calendar_key"]),
        coverage_id=str(coverage["coverage_id"]),
        producer_readiness_id=str(producer["readiness_id"]),
        entry_readiness_id=str(entry["readiness_id"]),
        source_cycle_time=str(source_run["source_cycle_time"]),
        source_issue_time=source_run.get("source_issue_time"),
        source_release_time=str(source_run["source_release_time"]),
        source_available_at=str(source_run["source_available_at"]),
        captured_at=str(source_run["captured_at"]),
        input_snapshot_ids=(snapshot.snapshot_id,),
        raw_payload_hash=source_run.get("raw_payload_hash"),
        manifest_hash=snapshot.manifest_hash or source_run.get("manifest_hash"),
        target_local_date=target_local_date.isoformat(),
        city_timezone=city_timezone,
        required_steps=expected_steps,
        observed_steps=observed_steps,
        expected_members=expected_members,
        observed_members=observed_members,
    )
    return ExecutableForecastBundleResult(
        "LIVE_ELIGIBLE",
        "EXECUTABLE_FORECAST_READY",
        ExecutableForecastBundle(snapshot=snapshot, evidence=evidence),
    )
