# Created: 2026-05-02
# Last reused/audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45b job-run provenance contract; docs/operations/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md Phase 6 release-key identity.
"""Repository helpers for the data-daemon job_run table."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import date, datetime
from typing import Any, Iterator

JOB_RUN_STATUSES = frozenset({
    "RUNNING",
    "SUCCESS",
    "FAILED",
    "PARTIAL",
    "SKIPPED_NOT_RELEASED",
    "SKIPPED_LOCK_HELD",
})
PLANES = frozenset({
    "forecast",
    "observation",
    "solar_aux",
    "market_topology",
    "quote",
    "settlement_truth",
    "source_health",
    "hole_backfill",
    "telemetry_control",
})


def _to_iso(value: datetime | date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _json_text(value: Any, field: str) -> str:
    if value is None:
        value = {} if field.endswith("scope_json") or field == "meta_json" else []
    if isinstance(value, str):
        json.loads(value)
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _scope_key(*parts: object) -> str:
    return "|".join("" if part is None else str(part) for part in parts)


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


def write_job_run(
    conn: sqlite3.Connection,
    *,
    job_run_id: str,
    job_name: str,
    plane: str,
    scheduled_for: datetime | str,
    status: str,
    missed_from: datetime | str | None = None,
    started_at: datetime | str | None = None,
    finished_at: datetime | str | None = None,
    lock_key: str | None = None,
    lock_acquired_at: datetime | str | None = None,
    reason_code: str | None = None,
    rows_written: int = 0,
    rows_failed: int = 0,
    source_run_id: str | None = None,
    source_id: str | None = None,
    track: str | None = None,
    release_calendar_key: str | None = None,
    safe_fetch_not_before: datetime | str | None = None,
    expected_scope_json: Any = None,
    affected_scope_json: Any = None,
    readiness_impacts_json: Any = None,
    readiness_recomputed_at: datetime | str | None = None,
    meta_json: Any = None,
) -> None:
    if status not in JOB_RUN_STATUSES:
        raise ValueError(f"invalid job_run status: {status}")
    if plane not in PLANES:
        raise ValueError(f"invalid job_run plane: {plane}")
    scheduled_for_iso = _to_iso(scheduled_for)
    job_run_key = _scope_key(job_name, scheduled_for_iso, source_id, track, release_calendar_key)
    with _savepoint(conn, "job_run_write"):
        conn.execute(
            """
            INSERT INTO job_run (
                job_run_id, job_run_key, job_name, plane, scheduled_for, missed_from,
                started_at, finished_at, lock_key, lock_acquired_at, status,
                reason_code, rows_written, rows_failed, source_run_id,
                source_id, track, release_calendar_key, safe_fetch_not_before,
                expected_scope_json, affected_scope_json, readiness_impacts_json,
                readiness_recomputed_at, meta_json
            ) VALUES (
                :job_run_id, :job_run_key, :job_name, :plane, :scheduled_for, :missed_from,
                :started_at, :finished_at, :lock_key, :lock_acquired_at, :status,
                :reason_code, :rows_written, :rows_failed, :source_run_id,
                :source_id, :track, :release_calendar_key, :safe_fetch_not_before,
                :expected_scope_json, :affected_scope_json, :readiness_impacts_json,
                :readiness_recomputed_at, :meta_json
            )
            ON CONFLICT(job_run_key) DO UPDATE SET
                job_run_id = excluded.job_run_id,
                plane = excluded.plane,
                missed_from = excluded.missed_from,
                started_at = excluded.started_at,
                finished_at = excluded.finished_at,
                lock_key = excluded.lock_key,
                lock_acquired_at = excluded.lock_acquired_at,
                status = excluded.status,
                reason_code = excluded.reason_code,
                rows_written = excluded.rows_written,
                rows_failed = excluded.rows_failed,
                source_run_id = excluded.source_run_id,
                release_calendar_key = excluded.release_calendar_key,
                safe_fetch_not_before = excluded.safe_fetch_not_before,
                expected_scope_json = excluded.expected_scope_json,
                affected_scope_json = excluded.affected_scope_json,
                readiness_impacts_json = excluded.readiness_impacts_json,
                readiness_recomputed_at = excluded.readiness_recomputed_at,
                meta_json = excluded.meta_json
            """,
            {
                "job_run_id": job_run_id,
                "job_run_key": job_run_key,
                "job_name": job_name,
                "plane": plane,
                "scheduled_for": scheduled_for_iso,
                "missed_from": _to_iso(missed_from),
                "started_at": _to_iso(started_at),
                "finished_at": _to_iso(finished_at),
                "lock_key": lock_key,
                "lock_acquired_at": _to_iso(lock_acquired_at),
                "status": status,
                "reason_code": reason_code,
                "rows_written": rows_written,
                "rows_failed": rows_failed,
                "source_run_id": source_run_id,
                "source_id": source_id,
                "track": track,
                "release_calendar_key": release_calendar_key,
                "safe_fetch_not_before": _to_iso(safe_fetch_not_before),
                "expected_scope_json": _json_text(expected_scope_json, "expected_scope_json"),
                "affected_scope_json": _json_text(affected_scope_json, "affected_scope_json"),
                "readiness_impacts_json": _json_text(readiness_impacts_json, "readiness_impacts_json"),
                "readiness_recomputed_at": _to_iso(readiness_recomputed_at),
                "meta_json": _json_text(meta_json, "meta_json"),
            },
        )


def get_job_run(conn: sqlite3.Connection, job_run_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM job_run WHERE job_run_id = ?", (job_run_id,)).fetchone()
    return dict(row) if row else None


def get_latest_job_run(conn: sqlite3.Connection, job_name: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM job_run WHERE job_name = ? ORDER BY scheduled_for DESC, recorded_at DESC LIMIT 1",
        (job_name,),
    ).fetchone()
    return dict(row) if row else None
