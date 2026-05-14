# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45b readiness-state contract.
"""Repository helpers for scoped data readiness verdicts."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Iterator

from src.state.connection_pair import WorldConnection

READINESS_STATUSES = frozenset({
    "LIVE_ELIGIBLE",
    "SHADOW_ONLY",
    "BLOCKED",
    "DEGRADED_LOG_ONLY",
    "UNKNOWN_BLOCKED",
})
SCOPE_TYPES = frozenset({"global", "source", "city_metric", "market", "strategy", "quote"})


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


def _scope_key(*parts: object) -> str:
    return "|".join("" if part is None else str(part) for part in parts)


@contextlib.contextmanager
def _savepoint(conn: WorldConnection, name: str) -> Iterator[None]:
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield
        conn.execute(f"RELEASE SAVEPOINT {name}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
        conn.execute(f"RELEASE SAVEPOINT {name}")
        raise


def _unknown_blocked(reason: str) -> dict[str, Any]:
    return {
        "readiness_id": None,
        "status": "UNKNOWN_BLOCKED",
        "reason_codes_json": json.dumps([reason]),
    }


def _parse_expiry(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def write_readiness_state(
    conn: WorldConnection,
    *,
    readiness_id: str,
    scope_type: str,
    status: str,
    computed_at: datetime | str,
    city_id: str | None = None,
    city: str | None = None,
    city_timezone: str | None = None,
    target_local_date: date | str | None = None,
    metric: str | None = None,
    temperature_metric: str | None = None,
    physical_quantity: str | None = None,
    observation_field: str | None = None,
    data_version: str | None = None,
    source_id: str | None = None,
    track: str | None = None,
    source_run_id: str | None = None,
    market_family: str | None = None,
    event_id: str | None = None,
    condition_id: str | None = None,
    token_ids_json: Any = None,
    strategy_key: str | None = None,
    reason_codes_json: Any = None,
    expires_at: datetime | str | None = None,
    dependency_json: Any = None,
    provenance_json: Any = None,
) -> None:
    if status not in READINESS_STATUSES:
        raise ValueError(f"invalid readiness status: {status}")
    if scope_type not in SCOPE_TYPES:
        raise ValueError(f"invalid readiness scope_type: {scope_type}")
    if status == "LIVE_ELIGIBLE" and expires_at is None:
        raise ValueError("LIVE_ELIGIBLE readiness requires expires_at")
    target_local_date_iso = _to_iso(target_local_date)
    scope_key = _scope_key(
        scope_type,
        city_id,
        city_timezone,
        target_local_date_iso,
        temperature_metric,
        physical_quantity,
        observation_field,
        data_version,
        strategy_key,
        market_family,
        source_id,
        track,
        condition_id,
    )
    with _savepoint(conn, "readiness_state_write"):
        conn.execute(
            """
            INSERT INTO readiness_state (
                readiness_id, scope_key, scope_type, city_id, city, city_timezone,
                target_local_date, metric, temperature_metric, physical_quantity,
                observation_field, data_version, source_id, track, source_run_id,
                market_family, event_id, condition_id, token_ids_json,
                strategy_key, status, reason_codes_json, computed_at, expires_at,
                dependency_json, provenance_json
            ) VALUES (
                :readiness_id, :scope_key, :scope_type, :city_id, :city, :city_timezone,
                :target_local_date, :metric, :temperature_metric, :physical_quantity,
                :observation_field, :data_version, :source_id, :track, :source_run_id,
                :market_family, :event_id, :condition_id, :token_ids_json,
                :strategy_key, :status, :reason_codes_json, :computed_at, :expires_at,
                :dependency_json, :provenance_json
            )
            ON CONFLICT(scope_key) DO UPDATE SET
                readiness_id = excluded.readiness_id,
                city = excluded.city,
                metric = excluded.metric,
                source_run_id = excluded.source_run_id,
                event_id = excluded.event_id,
                token_ids_json = excluded.token_ids_json,
                status = excluded.status,
                reason_codes_json = excluded.reason_codes_json,
                computed_at = excluded.computed_at,
                expires_at = excluded.expires_at,
                dependency_json = excluded.dependency_json,
                provenance_json = excluded.provenance_json
            """,
            {
                "readiness_id": readiness_id,
                "scope_key": scope_key,
                "scope_type": scope_type,
                "city_id": city_id,
                "city": city,
                "city_timezone": city_timezone,
                "target_local_date": target_local_date_iso,
                "metric": metric,
                "temperature_metric": temperature_metric,
                "physical_quantity": physical_quantity,
                "observation_field": observation_field,
                "data_version": data_version,
                "source_id": source_id,
                "track": track,
                "source_run_id": source_run_id,
                "market_family": market_family,
                "event_id": event_id,
                "condition_id": condition_id,
                "token_ids_json": _json_text(token_ids_json, default=[]),
                "strategy_key": strategy_key,
                "status": status,
                "reason_codes_json": _json_text(reason_codes_json, default=[]),
                "computed_at": _timestamp_iso(computed_at, "computed_at"),
                "expires_at": _timestamp_iso(expires_at, "expires_at"),
                "dependency_json": _json_text(dependency_json, default={}),
                "provenance_json": _json_text(provenance_json, default={}),
            },
        )


def get_readiness_state(conn: WorldConnection, readiness_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM readiness_state WHERE readiness_id = ?", (readiness_id,)).fetchone()
    return dict(row) if row else None


def get_entry_readiness(
    conn: WorldConnection,
    *,
    city_id: str,
    city_timezone: str,
    target_local_date: date | str,
    temperature_metric: str,
    physical_quantity: str,
    observation_field: str,
    data_version: str,
    source_id: str,
    track: str,
    strategy_key: str,
    market_family: str,
    condition_id: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT * FROM readiness_state
        WHERE scope_type = 'city_metric'
          AND city_id = ?
          AND city_timezone = ?
          AND target_local_date = ?
          AND temperature_metric = ?
          AND physical_quantity = ?
          AND observation_field = ?
          AND data_version = ?
          AND source_id = ?
          AND track = ?
          AND strategy_key = ?
          AND market_family = ?
          AND condition_id = ?
        ORDER BY computed_at DESC
        LIMIT 1
        """,
        (
            city_id,
            city_timezone,
            _to_iso(target_local_date),
            temperature_metric,
            physical_quantity,
            observation_field,
            data_version,
            source_id,
            track,
            strategy_key,
            market_family,
            condition_id,
        ),
    ).fetchone()
    if row is None:
        return _unknown_blocked("READINESS_MISSING")
    result = dict(row)
    expires_at = _parse_expiry(result.get("expires_at"))
    if result.get("status") == "LIVE_ELIGIBLE" and not result.get("expires_at"):
        return _unknown_blocked("READINESS_EXPIRY_MISSING")
    if result.get("expires_at") and expires_at is None:
        return _unknown_blocked("READINESS_EXPIRY_INVALID")
    if expires_at is not None:
        now = now_utc or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            return _unknown_blocked("READINESS_NOW_INVALID")
        if expires_at <= now.astimezone(timezone.utc):
            return _unknown_blocked("READINESS_EXPIRED")
    return result
