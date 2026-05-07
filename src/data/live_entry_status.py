"""Live-entry forecast status and blocker summary."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.config import EntryForecastConfig, EntryForecastRolloutMode
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


def count_executable_opendata_rows(conn: sqlite3.Connection, *, config: EntryForecastConfig) -> int:
    if not _table_exists(conn, "ensemble_snapshots_v2"):
        return 0
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM ensemble_snapshots_v2
        WHERE source_id = ?
          AND source_transport = ?
          AND source_run_id IS NOT NULL
          AND release_calendar_key IS NOT NULL
          AND source_cycle_time IS NOT NULL
          AND source_release_time IS NOT NULL
          AND data_version IN (?, ?, ?, ?)
        """,
        (
            config.source_id,
            config.source_transport.value,
            # 2026-05-07: mx2t3/mn2t3 active versions
            "ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
            "ecmwf_opendata_mn2t3_local_calendar_day_min_v1",
            # Legacy mx2t6/mn2t6 — historical rows written before 2026-05-07
            "ecmwf_opendata_mx2t6_local_calendar_day_max_v1",
            "ecmwf_opendata_mn2t6_local_calendar_day_min_v1",
        ),
    ).fetchone()
    return int(row["count"] if hasattr(row, "keys") else row[0])


def build_live_entry_forecast_status(
    conn: sqlite3.Connection,
    *,
    config: EntryForecastConfig,
    now_utc: datetime | None = None,
) -> LiveEntryForecastStatus:
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    blockers: list[str] = []
    executable_row_count = count_executable_opendata_rows(conn, config=config)
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
              AND track IN (?, ?)
            """,
            (
                PRODUCER_READINESS_STRATEGY_KEY,
                config.source_id,
                config.high_track,
                config.low_track,
            ),
        ).fetchall()
        producer_readiness_count = len(rows)
        producer_live_eligible_count = sum(
            1 for row in rows if _is_live_readiness_current(row, now_utc=now_utc)
        )
        if not rows:
            blockers.append("NO_FUTURE_TARGET_DATE_COVERAGE")
        for row in rows:
            if row["status"] != "LIVE_ELIGIBLE":
                blockers.extend(_parse_reasons(row["reason_codes_json"]))
            elif not _is_live_readiness_current(row, now_utc=now_utc):
                blockers.append("PRODUCER_READINESS_EXPIRED")

    if config.rollout_mode is EntryForecastRolloutMode.BLOCKED:
        blockers.append("ENTRY_FORECAST_ROLLOUT_BLOCKED")
    blockers = sorted(set(blockers))
    status = "LIVE_ELIGIBLE" if not blockers else "BLOCKED"
    return LiveEntryForecastStatus(
        status=status,
        blockers=tuple(blockers),
        executable_row_count=executable_row_count,
        producer_readiness_count=producer_readiness_count,
        producer_live_eligible_count=producer_live_eligible_count,
    )
