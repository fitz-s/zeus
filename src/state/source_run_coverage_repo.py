"""Repository helpers for source_run_coverage rows."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Any

COMPLETENESS_STATUSES = frozenset({"COMPLETE", "PARTIAL", "MISSING", "HORIZON_OUT_OF_RANGE", "NOT_RELEASED"})
READINESS_STATUSES = frozenset({"LIVE_ELIGIBLE", "SHADOW_ONLY", "BLOCKED", "UNKNOWN_BLOCKED"})


def _to_iso(value: datetime | date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _timestamp_iso(value: datetime | str | None, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field} must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat()


def _json_text(value: Any, *, default: object) -> str:
    if value is None:
        value = default
    if isinstance(value, str):
        json.loads(value)
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def write_source_run_coverage(
    conn: sqlite3.Connection,
    *,
    coverage_id: str,
    source_run_id: str,
    source_id: str,
    source_transport: str,
    release_calendar_key: str,
    track: str,
    city_id: str,
    city: str,
    city_timezone: str,
    target_local_date: date | str,
    temperature_metric: str,
    physical_quantity: str,
    observation_field: str,
    data_version: str,
    expected_members: int,
    observed_members: int,
    expected_steps_json: Any,
    observed_steps_json: Any,
    snapshot_ids_json: Any = None,
    target_window_start_utc: datetime | str | None = None,
    target_window_end_utc: datetime | str | None = None,
    completeness_status: str,
    readiness_status: str,
    reason_code: str | None = None,
    computed_at: datetime | str,
    expires_at: datetime | str | None = None,
) -> None:
    if completeness_status not in COMPLETENESS_STATUSES:
        raise ValueError(f"invalid source_run_coverage completeness_status: {completeness_status}")
    if readiness_status not in READINESS_STATUSES:
        raise ValueError(f"invalid source_run_coverage readiness_status: {readiness_status}")
    if temperature_metric not in {"high", "low"}:
        raise ValueError("temperature_metric must be high or low")
    if readiness_status == "LIVE_ELIGIBLE" and expires_at is None:
        raise ValueError("LIVE_ELIGIBLE source_run_coverage requires expires_at")
    target_window_start_utc_iso = _timestamp_iso(
        target_window_start_utc,
        "target_window_start_utc",
    )
    target_window_end_utc_iso = _timestamp_iso(
        target_window_end_utc,
        "target_window_end_utc",
    )
    if target_window_start_utc_iso is None:
        raise ValueError("target_window_start_utc is required")
    if target_window_end_utc_iso is None:
        raise ValueError("target_window_end_utc is required")

    conn.execute("SAVEPOINT source_run_coverage_write")
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO source_run_coverage (
                coverage_id, source_run_id, source_id, source_transport,
                release_calendar_key, track, city_id, city, city_timezone,
                target_local_date, temperature_metric, physical_quantity,
                observation_field, data_version, expected_members,
                observed_members, expected_steps_json, observed_steps_json,
                snapshot_ids_json, target_window_start_utc, target_window_end_utc,
                completeness_status, readiness_status, reason_code,
                computed_at, expires_at
            ) VALUES (
                :coverage_id, :source_run_id, :source_id, :source_transport,
                :release_calendar_key, :track, :city_id, :city, :city_timezone,
                :target_local_date, :temperature_metric, :physical_quantity,
                :observation_field, :data_version, :expected_members,
                :observed_members, :expected_steps_json, :observed_steps_json,
                :snapshot_ids_json, :target_window_start_utc, :target_window_end_utc,
                :completeness_status, :readiness_status, :reason_code,
                :computed_at, :expires_at
            )
            """,
            {
                "coverage_id": coverage_id,
                "source_run_id": source_run_id,
                "source_id": source_id,
                "source_transport": source_transport,
                "release_calendar_key": release_calendar_key,
                "track": track,
                "city_id": city_id,
                "city": city,
                "city_timezone": city_timezone,
                "target_local_date": _to_iso(target_local_date),
                "temperature_metric": temperature_metric,
                "physical_quantity": physical_quantity,
                "observation_field": observation_field,
                "data_version": data_version,
                "expected_members": expected_members,
                "observed_members": observed_members,
                "expected_steps_json": _json_text(expected_steps_json, default=[]),
                "observed_steps_json": _json_text(observed_steps_json, default=[]),
                "snapshot_ids_json": _json_text(snapshot_ids_json, default=[]),
                "target_window_start_utc": target_window_start_utc_iso,
                "target_window_end_utc": target_window_end_utc_iso,
                "completeness_status": completeness_status,
                "readiness_status": readiness_status,
                "reason_code": reason_code,
                "computed_at": _timestamp_iso(computed_at, "computed_at"),
                "expires_at": _timestamp_iso(expires_at, "expires_at"),
            },
        )
        conn.execute("RELEASE SAVEPOINT source_run_coverage_write")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT source_run_coverage_write")
        conn.execute("RELEASE SAVEPOINT source_run_coverage_write")
        raise


def get_source_run_coverage(conn: sqlite3.Connection, coverage_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM source_run_coverage WHERE coverage_id = ?",
        (coverage_id,),
    ).fetchone()
    return dict(row) if row else None


def get_latest_source_run_coverage(
    conn: sqlite3.Connection,
    *,
    city_id: str,
    city_timezone: str,
    target_local_date: date | str,
    temperature_metric: str,
    source_id: str,
    source_transport: str,
    data_version: str,
    track: str | None = None,
    release_calendar_key: str | None = None,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM source_run_coverage
        WHERE city_id = ?
          AND city_timezone = ?
          AND target_local_date = ?
          AND temperature_metric = ?
          AND source_id = ?
          AND source_transport = ?
          AND data_version = ?
                    AND (? IS NULL OR track = ?)
                    AND (? IS NULL OR release_calendar_key = ?)
        ORDER BY computed_at DESC, recorded_at DESC
        LIMIT 1
        """,
        (
            city_id,
            city_timezone,
            _to_iso(target_local_date),
            temperature_metric,
            source_id,
            source_transport,
            data_version,
            track,
            track,
            release_calendar_key,
            release_calendar_key,
        ),
    ).fetchone()
    return dict(row) if row else None
