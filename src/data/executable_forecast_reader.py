"""Executable forecast reader for V4 source-linked ensemble snapshots."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from src.data.forecast_target_contract import ForecastTargetScope

UTC = timezone.utc
SOURCE_TRANSPORT = "ensemble_snapshots_v2_db_reader"


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
    available_at: str


@dataclass(frozen=True)
class ExecutableForecastReadResult:
    status: str
    reason_code: str
    snapshot: ExecutableForecastSnapshot | None = None

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
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT * FROM ensemble_snapshots_v2
            WHERE city = ?
              AND target_date = ?
              AND temperature_metric = ?
              AND data_version = ?
            ORDER BY source_cycle_time DESC, available_at DESC, snapshot_id DESC
            """,
            (
                scope.city_name,
                scope.target_local_date.isoformat(),
                scope.temperature_metric,
                scope.data_version,
            ),
        ).fetchall()
    ]
    if not rows:
        return ExecutableForecastReadResult("BLOCKED", "NO_FORECAST_ROWS_FOR_TARGET")
    linked_rows = [row for row in rows if _row_is_source_linked(row)]
    if not linked_rows:
        return ExecutableForecastReadResult("BLOCKED", "FORECAST_SOURCE_LINKAGE_MISSING")
    source_rows = [row for row in linked_rows if row.get("source_id") == source_id]
    if not source_rows:
        return ExecutableForecastReadResult("BLOCKED", "EXECUTABLE_FORECAST_SOURCE_ID_MISMATCH")
    transport_rows = [row for row in source_rows if row.get("source_transport") == source_transport]
    if not transport_rows:
        return ExecutableForecastReadResult("BLOCKED", "EXECUTABLE_FORECAST_SOURCE_TRANSPORT_MISMATCH")
    if source_run_id is not None:
        transport_rows = [row for row in transport_rows if row.get("source_run_id") == source_run_id]
        if not transport_rows:
            return ExecutableForecastReadResult("BLOCKED", "EXECUTABLE_FORECAST_SOURCE_RUN_MISMATCH")
    verified_rows = [row for row in transport_rows if row.get("authority") == "VERIFIED"]
    if not verified_rows:
        return ExecutableForecastReadResult("BLOCKED", "EXECUTABLE_FORECAST_AUTHORITY_NOT_VERIFIED")
    causal_rows = [
        row
        for row in verified_rows
        if row.get("causality_status") == "OK" and int(row.get("boundary_ambiguous") or 0) == 0
    ]
    if not causal_rows:
        return ExecutableForecastReadResult("BLOCKED", "EXECUTABLE_FORECAST_CAUSALITY_NOT_OK")
    if now_utc is not None:
        if now_utc.tzinfo is None or now_utc.utcoffset() is None:
            return ExecutableForecastReadResult("UNKNOWN_BLOCKED", "READ_NOW_INVALID")
        available_rows = [
            row
            for row in causal_rows
            if (available_at := _parse_utc(row.get("available_at"))) is not None
            and available_at <= now_utc.astimezone(UTC)
        ]
        if not available_rows:
            return ExecutableForecastReadResult("BLOCKED", "EXECUTABLE_FORECAST_NOT_AVAILABLE_YET")
        causal_rows = available_rows
    row = causal_rows[0]
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
        available_at=str(row["available_at"]),
    )
    return ExecutableForecastReadResult("LIVE_ELIGIBLE", "EXECUTABLE_FORECAST_READY", snapshot)
