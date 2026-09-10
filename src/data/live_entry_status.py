"""Live-entry forecast status and blocker summary."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.config import EntryForecastConfig, runtime_coordinate_manifest_json
from src.data.forecast_fetch_plan import data_version_for_track
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY


@dataclass(frozen=True)
class LiveEntryForecastStatus:
    status: str
    blockers: tuple[str, ...]
    executable_row_count: int
    producer_readiness_count: int
    producer_live_eligible_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "blockers": list(self.blockers),
            "executable_row_count": self.executable_row_count,
            "producer_readiness_count": self.producer_readiness_count,
            "producer_live_eligible_count": self.producer_live_eligible_count,
        }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _parse_reasons(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value:
        return ()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return ("READINESS_REASON_CODES_MALFORMED",)
    if not isinstance(parsed, list):
        return ("READINESS_REASON_CODES_MALFORMED",)
    return tuple(str(item) for item in parsed if str(item))


def _is_live_readiness_current(row: sqlite3.Row, *, now_utc: datetime) -> bool:
    if row["status"] != "LIVE_ELIGIBLE":
        return False
    value = row["expires_at"]
    if not isinstance(value, str) or not value:
        return False
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return False
    return parsed.astimezone(timezone.utc) > now_utc.astimezone(timezone.utc)


def _active_opendata_dataset_ids(config: EntryForecastConfig) -> tuple[str, str]:
    manifest_json = runtime_coordinate_manifest_json()
    if not isinstance(manifest_json, str) or not manifest_json:
        raise ValueError("runtime coordinate manifest must be a non-empty string")
    return (
        data_version_for_track(config.high_track, manifest_json),
        data_version_for_track(config.low_track, manifest_json),
    )


def _count_executable_opendata_rows(
    conn: sqlite3.Connection,
    *,
    config: EntryForecastConfig,
    dataset_ids: tuple[str, str],
) -> int:
    if not _table_exists(conn, "ensemble_snapshots"):
        return 0
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM ensemble_snapshots
        WHERE source_id = ?
          AND source_transport = ?
          AND source_run_id IS NOT NULL
          AND release_calendar_key IS NOT NULL
          AND source_cycle_time IS NOT NULL
          AND source_release_time IS NOT NULL
          AND dataset_id IN (?, ?)
        """,
        (
            config.source_id,
            config.source_transport.value,
            *dataset_ids,
        ),
    ).fetchone()
    return int(row["count"] if hasattr(row, "keys") else row[0])


def count_executable_opendata_rows(conn: sqlite3.Connection, *, config: EntryForecastConfig) -> int:
    return _count_executable_opendata_rows(
        conn,
        config=config,
        dataset_ids=_active_opendata_dataset_ids(config),
    )


def build_live_entry_forecast_status(
    conn: sqlite3.Connection,
    *,
    config: EntryForecastConfig,
    now_utc: datetime | None = None,
) -> LiveEntryForecastStatus:
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    blockers: list[str] = []
    dataset_ids = _active_opendata_dataset_ids(config)
    executable_row_count = _count_executable_opendata_rows(
        conn,
        config=config,
        dataset_ids=dataset_ids,
    )
    if executable_row_count == 0:
        blockers.append("ZERO_EXECUTABLE_OPENDATA_ROWS")

    producer_readiness_count = 0
    producer_live_eligible_count = 0
    if not _table_exists(conn, "readiness_state"):
        blockers.append("READINESS_STATE_TABLE_MISSING")
    else:
        rows = conn.execute(
            """
            SELECT status, reason_codes_json, expires_at
            FROM readiness_state
            WHERE strategy_key = ?
              AND source_id = ?
              AND (
                    (track = ? AND data_version = ?)
                 OR (track = ? AND data_version = ?)
              )
            """,
            (
                PRODUCER_READINESS_STRATEGY_KEY,
                config.source_id,
                config.high_track,
                dataset_ids[0],
                config.low_track,
                dataset_ids[1],
            ),
        ).fetchall()
        producer_readiness_count = len(rows)
        producer_live_eligible_count = sum(
            1 for row in rows if _is_live_readiness_current(row, now_utc=now_utc)
        )
        if not rows:
            blockers.append("NO_FUTURE_TARGET_DATE_COVERAGE")
        row_blockers: list[str] = []
        for row in rows:
            if row["status"] != "LIVE_ELIGIBLE":
                row_blockers.extend(_parse_reasons(row["reason_codes_json"]))
            elif not _is_live_readiness_current(row, now_utc=now_utc):
                row_blockers.append("PRODUCER_READINESS_EXPIRED")
        if rows and producer_live_eligible_count == 0:
            blockers.extend(row_blockers or ("NO_CURRENT_PRODUCER_LIVE_ELIGIBLE",))

    blockers = sorted(set(blockers))
    status = "LIVE_ELIGIBLE" if not blockers else "BLOCKED"
    return LiveEntryForecastStatus(
        status=status,
        blockers=tuple(blockers),
        executable_row_count=executable_row_count,
        producer_readiness_count=producer_readiness_count,
        producer_live_eligible_count=producer_live_eligible_count,
    )
