# Created: 2026-07-09
# Last reused or audited: 2026-07-09
# Authority basis: live Day0 monitoring audit gap 2026-07-09; day0_metric_fact canonical owner is world DB.
"""Durable Day0 observed-fact persistence for held-position monitoring.

This store writes the world-owned ``day0_metric_fact`` table. It is an audit
surface for Day0 monitor observations; it is not a second probability authority
and must not be used to bypass ``forecast_posteriors`` or hard-fact gates.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo


_FACT_ID_PREFIX = "d0mf_v1_"
_DIGEST_CHARS = 20


def _clean(value: object) -> str:
    return str(value or "").strip()


def _finite_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _parse_utc_timestamp(value: object) -> datetime:
    raw = _clean(value)
    if not raw:
        raise ValueError("utc_timestamp must be non-empty")
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"utc_timestamp must be ISO-8601: {raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _derive_local_timestamp(
    utc_dt: datetime,
    *,
    local_timezone: object,
    local_timestamp: object = None,
) -> tuple[str, float | None]:
    local_raw = _clean(local_timestamp)
    if local_raw:
        try:
            parsed = datetime.fromisoformat(local_raw[:-1] + "+00:00" if local_raw.endswith("Z") else local_raw)
            hour = (
                float(parsed.hour)
                + float(parsed.minute) / 60.0
                + float(parsed.second) / 3600.0
            )
            return local_raw, hour
        except ValueError:
            return local_raw, None
    tz_name = _clean(local_timezone)
    if not tz_name:
        raise ValueError("local_timezone is required when local_timestamp is absent")
    local_dt = utc_dt.astimezone(ZoneInfo(tz_name))
    hour = float(local_dt.hour) + float(local_dt.minute) / 60.0 + float(local_dt.second) / 3600.0
    return local_dt.isoformat(), hour


def day0_metric_fact_id_v1(
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    source: str,
    utc_timestamp: str,
) -> str:
    canonical = "|".join(
        [
            _clean(city),
            _clean(target_date),
            _clean(temperature_metric).lower(),
            _clean(source),
            _clean(utc_timestamp),
        ]
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]
    return f"{_FACT_ID_PREFIX}{digest}"


def _conn_db_path(conn: sqlite3.Connection) -> str:
    rows = conn.execute("PRAGMA database_list").fetchall()
    if not rows:
        return ""
    return str(rows[0][2] if not hasattr(rows[0], "keys") else rows[0]["file"])


def _assert_world_or_memory_conn(conn: sqlite3.Connection) -> None:
    path = _conn_db_path(conn)
    if path and not path.endswith("zeus-world.db"):
        raise AssertionError(
            "write_day0_metric_fact requires a world DB connection; "
            f"got {path!r}. Pass conn=None to use the canonical world DB."
        )


def write_day0_metric_fact(
    *,
    city: object,
    target_date: object,
    temperature_metric: object,
    source: object,
    utc_timestamp: object,
    local_timezone: object,
    local_timestamp: object = None,
    temp_current: object = None,
    running_extreme: object = None,
    fact_status: str = "complete",
    missing_reasons: Optional[list[object]] = None,
    recorded_at: object = None,
    conn: Optional[sqlite3.Connection] = None,
) -> str:
    """Write or update one Day0 metric fact row in the world DB.

    ``running_extreme`` and ``temp_current`` are in the city's native settlement
    unit as seen by the monitor observation context. The table has no unit
    column; consumers must use city/source contract identity before interpreting
    the numbers.
    """
    city_s = _clean(city)
    target_s = _clean(target_date)
    metric_s = _clean(temperature_metric).lower()
    source_s = _clean(source)
    if not city_s:
        raise ValueError("city must be non-empty")
    if not target_s:
        raise ValueError("target_date must be non-empty")
    if metric_s not in {"high", "low"}:
        raise ValueError("temperature_metric must be 'high' or 'low'")
    if not source_s:
        raise ValueError("source must be non-empty")
    if fact_status not in {"complete", "missing_inputs"}:
        raise ValueError("fact_status must be 'complete' or 'missing_inputs'")

    utc_dt = _parse_utc_timestamp(utc_timestamp)
    utc_iso = _iso_utc(utc_dt)
    local_iso, local_hour = _derive_local_timestamp(
        utc_dt,
        local_timezone=local_timezone,
        local_timestamp=local_timestamp,
    )
    recorded_dt = _parse_utc_timestamp(recorded_at) if _clean(recorded_at) else datetime.now(timezone.utc)
    recorded_iso = _iso_utc(recorded_dt)
    age_minutes = max(0.0, (recorded_dt - utc_dt).total_seconds() / 60.0)
    reasons_json = json.dumps(list(missing_reasons or []), sort_keys=True, separators=(",", ":"))
    fact_id = day0_metric_fact_id_v1(
        city=city_s,
        target_date=target_s,
        temperature_metric=metric_s,
        source=source_s,
        utc_timestamp=utc_iso,
    )

    own_conn = conn is None
    if own_conn:
        from src.state.db import ZEUS_WORLD_DB_PATH, get_world_connection
        from src.state.db_writer_lock import WriteClass, db_writer_lock

        conn = get_world_connection(write_class=WriteClass.LIVE)
        lock_ctx = db_writer_lock(ZEUS_WORLD_DB_PATH, WriteClass.LIVE)
    else:
        _assert_world_or_memory_conn(conn)
        lock_ctx = nullcontext()

    try:
        with lock_ctx:
            conn.execute(
                """
                INSERT INTO day0_metric_fact (
                    fact_id, city, target_date, temperature_metric, source,
                    local_timestamp, utc_timestamp, local_hour, temp_current,
                    running_extreme, obs_age_minutes, fact_status,
                    missing_reason_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(city, target_date, temperature_metric, utc_timestamp, source)
                DO UPDATE SET
                    local_timestamp = excluded.local_timestamp,
                    local_hour = excluded.local_hour,
                    temp_current = excluded.temp_current,
                    running_extreme = excluded.running_extreme,
                    obs_age_minutes = excluded.obs_age_minutes,
                    fact_status = excluded.fact_status,
                    missing_reason_json = excluded.missing_reason_json,
                    recorded_at = excluded.recorded_at
                """,
                (
                    fact_id,
                    city_s,
                    target_s,
                    metric_s,
                    source_s,
                    local_iso,
                    utc_iso,
                    local_hour,
                    _finite_or_none(temp_current),
                    _finite_or_none(running_extreme),
                    age_minutes,
                    fact_status,
                    reasons_json,
                    recorded_iso,
                ),
            )
            if own_conn:
                conn.commit()
        return fact_id
    finally:
        if own_conn and conn is not None:
            conn.close()
