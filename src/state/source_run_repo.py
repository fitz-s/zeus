# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45b source-run provenance contract.
"""Repository helpers for source_run provenance rows."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import date, datetime
from typing import Any, Iterator

SOURCE_RUN_STATUSES = frozenset({"RUNNING", "SUCCESS", "FAILED", "PARTIAL", "SKIPPED_NOT_RELEASED"})
COMPLETENESS_STATUSES = frozenset({"COMPLETE", "PARTIAL", "MISSING", "NOT_RELEASED"})
INGEST_MODES = frozenset({"SCHEDULED_LIVE", "BOOT_CATCHUP", "HOLE_BACKFILL", "ARCHIVE_BACKFILL"})


def _to_iso(value: datetime | date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _json_text(value: Any, *, default: object) -> str:
    if value is None:
        value = default
    if isinstance(value, str):
        json.loads(value)
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@contextlib.contextmanager
def _savepoint(conn: sqlite3.Connection, name: str) -> Iterator[None]:
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield
        conn.execute(f"RELEASE SAVEPOINT {name}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
        conn.execute(f"RELEASE SAVEPOINT {name}")
        raise


def write_source_run(
    conn: sqlite3.Connection,
    *,
    source_run_id: str,
    source_id: str,
    track: str,
    release_calendar_key: str,
    source_cycle_time: datetime | str,
    status: str,
    completeness_status: str,
    ingest_mode: str = "SCHEDULED_LIVE",
    origin_mode: str = "SCHEDULED_LIVE",
    source_issue_time: datetime | str | None = None,
    source_release_time: datetime | str | None = None,
    source_available_at: datetime | str | None = None,
    fetch_started_at: datetime | str | None = None,
    fetch_finished_at: datetime | str | None = None,
    captured_at: datetime | str | None = None,
    imported_at: datetime | str | None = None,
    valid_time_start: datetime | str | None = None,
    valid_time_end: datetime | str | None = None,
    target_local_date: date | str | None = None,
    city_id: str | None = None,
    city_timezone: str | None = None,
    temperature_metric: str | None = None,
    physical_quantity: str | None = None,
    observation_field: str | None = None,
    data_version: str | None = None,
    expected_members: int | None = None,
    observed_members: int | None = None,
    expected_steps_json: Any = None,
    observed_steps_json: Any = None,
    expected_count: int | None = None,
    observed_count: int | None = None,
    partial_run: bool = False,
    raw_payload_hash: str | None = None,
    manifest_hash: str | None = None,
    reason_code: str | None = None,
) -> None:
    if status not in SOURCE_RUN_STATUSES:
        raise ValueError(f"invalid source_run status: {status}")
    if completeness_status not in COMPLETENESS_STATUSES:
        raise ValueError(f"invalid source_run completeness_status: {completeness_status}")
    if ingest_mode not in INGEST_MODES or origin_mode not in INGEST_MODES:
        raise ValueError("invalid source_run ingest/origin mode")
    if partial_run and completeness_status != "PARTIAL":
        raise ValueError("partial_run requires completeness_status=PARTIAL")
    with _savepoint(conn, "source_run_write"):
        conn.execute(
            """
            INSERT OR REPLACE INTO source_run (
                source_run_id, source_id, track, release_calendar_key,
                ingest_mode, origin_mode, source_cycle_time, source_issue_time,
                source_release_time, source_available_at, fetch_started_at,
                fetch_finished_at, captured_at, imported_at, valid_time_start,
                valid_time_end, target_local_date, city_id, city_timezone,
                temperature_metric, physical_quantity, observation_field,
                data_version, expected_members, observed_members,
                expected_steps_json, observed_steps_json, expected_count,
                observed_count, completeness_status, partial_run,
                raw_payload_hash, manifest_hash, status, reason_code
            ) VALUES (
                :source_run_id, :source_id, :track, :release_calendar_key,
                :ingest_mode, :origin_mode, :source_cycle_time, :source_issue_time,
                :source_release_time, :source_available_at, :fetch_started_at,
                :fetch_finished_at, :captured_at, :imported_at, :valid_time_start,
                :valid_time_end, :target_local_date, :city_id, :city_timezone,
                :temperature_metric, :physical_quantity, :observation_field,
                :data_version, :expected_members, :observed_members,
                :expected_steps_json, :observed_steps_json, :expected_count,
                :observed_count, :completeness_status, :partial_run,
                :raw_payload_hash, :manifest_hash, :status, :reason_code
            )
            """,
            {
                "source_run_id": source_run_id,
                "source_id": source_id,
                "track": track,
                "release_calendar_key": release_calendar_key,
                "ingest_mode": ingest_mode,
                "origin_mode": origin_mode,
                "source_cycle_time": _to_iso(source_cycle_time),
                "source_issue_time": _to_iso(source_issue_time),
                "source_release_time": _to_iso(source_release_time),
                "source_available_at": _to_iso(source_available_at),
                "fetch_started_at": _to_iso(fetch_started_at),
                "fetch_finished_at": _to_iso(fetch_finished_at),
                "captured_at": _to_iso(captured_at),
                "imported_at": _to_iso(imported_at),
                "valid_time_start": _to_iso(valid_time_start),
                "valid_time_end": _to_iso(valid_time_end),
                "target_local_date": _to_iso(target_local_date),
                "city_id": city_id,
                "city_timezone": city_timezone,
                "temperature_metric": temperature_metric,
                "physical_quantity": physical_quantity,
                "observation_field": observation_field,
                "data_version": data_version,
                "expected_members": expected_members,
                "observed_members": observed_members,
                "expected_steps_json": _json_text(expected_steps_json, default=[]),
                "observed_steps_json": _json_text(observed_steps_json, default=[]),
                "expected_count": expected_count,
                "observed_count": observed_count,
                "completeness_status": completeness_status,
                "partial_run": 1 if partial_run else 0,
                "raw_payload_hash": raw_payload_hash,
                "manifest_hash": manifest_hash,
                "status": status,
                "reason_code": reason_code,
            },
        )


def get_source_run(conn: sqlite3.Connection, source_run_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM source_run WHERE source_run_id = ?", (source_run_id,)).fetchone()
    return dict(row) if row else None


def get_latest_source_run(conn: sqlite3.Connection, source_id: str, track: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM source_run WHERE source_id = ? AND track = ? ORDER BY source_cycle_time DESC, recorded_at DESC LIMIT 1",
        (source_id, track),
    ).fetchone()
    return dict(row) if row else None
