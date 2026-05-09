"""Derived forecast-vs-calibration serving visibility for operators."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from src.config import cities_by_name
from src.contracts.season import season_from_date
from src.data.forecast_source_registry import calibration_source_id_for_lookup
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY

_SCHEMA_VERSION = 1


def _attached_schema_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute("PRAGMA database_list").fetchall()
        if len(row) > 1 and row[1]
    }


def _table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type IN ('table', 'view') AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _table_ref(conn: sqlite3.Connection, table: str, *, prefer_world: bool = True) -> str | None:
    schemas = _attached_schema_names(conn)
    candidates = ["world", "main"] if prefer_world else ["main", "world"]
    for schema in candidates:
        if schema in schemas and _table_exists(conn, schema, table):
            return table if schema == "main" else f"{schema}.{table}"
    return None


def _parse_json_list(value: object) -> list[str]:
    if not isinstance(value, str) or not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return ["READINESS_REASON_CODES_MALFORMED"]
    if not isinstance(parsed, list):
        return ["READINESS_REASON_CODES_MALFORMED"]
    return [str(item) for item in parsed if str(item)]


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _cycle_from_source_cycle_time(value: object) -> str | None:
    source_cycle_time = _parse_utc(value)
    if source_cycle_time is None:
        return None
    return f"{source_cycle_time.hour:02d}"


def _horizon_profile_from_track_cycle(track: str, cycle: str | None) -> str | None:
    normalized_track = track.lower()
    if "full" in normalized_track:
        return "full"
    if "short" in normalized_track:
        return "short"
    if cycle in {"00", "12"}:
        return "full"
    if cycle in {"06", "18"}:
        return "short"
    return None


def _producer_current(row: dict[str, Any], *, now_utc: datetime) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    if row.get("status") != "LIVE_ELIGIBLE":
        blockers.append("FORECAST_NOT_LIVE_ELIGIBLE")
        blockers.extend(_parse_json_list(row.get("reason_codes_json")))
        return False, sorted(set(blockers))
    expires_at = _parse_utc(row.get("expires_at"))
    if expires_at is None:
        blockers.append("PRODUCER_READINESS_EXPIRY_MISSING")
        return False, blockers
    if expires_at <= now_utc.astimezone(timezone.utc):
        blockers.append("PRODUCER_READINESS_EXPIRED")
        return False, blockers
    return True, []


def _bucket_from_producer_row(row: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    city_name = str(row.get("city") or row.get("city_id") or "")
    target_date = str(row.get("target_local_date") or "")
    metric = str(row.get("temperature_metric") or "unknown")
    data_version = str(row.get("data_version") or "unknown")
    forecast_source_id = str(row.get("source_id") or "unknown")
    source_id = calibration_source_id_for_lookup(forecast_source_id)
    track = str(row.get("track") or "unknown")
    cycle = _cycle_from_source_cycle_time(row.get("source_cycle_time"))
    horizon_profile = _horizon_profile_from_track_cycle(track, cycle)
    blockers: list[str] = []
    if source_id is None:
        source_id = forecast_source_id
        blockers.append("CALIBRATION_SOURCE_UNMAPPED")
    if cycle is None:
        cycle = "unknown"
        blockers.append("CALIBRATION_CYCLE_UNRESOLVED")
    if horizon_profile is None:
        horizon_profile = "unknown"
        blockers.append("CALIBRATION_HORIZON_PROFILE_UNRESOLVED")
    city = cities_by_name.get(city_name)
    if city is None:
        cluster = city_name or "unknown"
        season = "unknown"
        blockers.append("CALIBRATION_BUCKET_UNRESOLVED")
    else:
        cluster = str(city.cluster)
        season = season_from_date(target_date, lat=city.lat) if target_date else "unknown"
        if season == "unknown":
            blockers.append("CALIBRATION_BUCKET_UNRESOLVED")
    return {
        "temperature_metric": metric,
        "cluster": cluster,
        "season": season,
        "data_version": data_version,
        "cycle": cycle,
        "source_id": source_id,
        "forecast_source_id": forecast_source_id,
        "horizon_profile": horizon_profile,
        "track": track,
    }, blockers


def _bucket_key(bucket: dict[str, str]) -> str:
    return ":".join(
        str(bucket.get(key) or "unknown")
        for key in ("temperature_metric", "cluster", "season", "data_version", "cycle", "source_id", "horizon_profile")
    )


def _blank_bucket(bucket: dict[str, str]) -> dict[str, Any]:
    return {
        "bucket_key": _bucket_key(bucket),
        "serving_bucket": dict(bucket),
        "authority": "derived_operator_visibility",
        "forecast_ready": False,
        "calibration_ready": False,
        "trade_ready": False,
        "forecast_blockers": [],
        "calibration_blockers": [],
        "producer": {
            "readiness_count": 0,
            "live_eligible_count": 0,
            "tracks": [],
        },
        "calibration": {
            "verified_pair_count": 0,
            "active_verified_model_count": 0,
            "active_model_count": 0,
        },
    }


def _read_producer_buckets(
    conn: sqlite3.Connection,
    *,
    now_utc: datetime,
    source_errors: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    table = _table_ref(conn, "readiness_state", prefer_world=False)
    if table is None:
        source_errors.append({"source": "readiness_state", "error": "table_missing"})
        return {}
    source_run_table = _table_ref(conn, "source_run", prefer_world=False)
    source_cycle_select = "sr.source_cycle_time" if source_run_table else "NULL"
    source_run_join = f"LEFT JOIN {source_run_table} AS sr ON sr.source_run_id = rs.source_run_id" if source_run_table else ""
    rows = conn.execute(
        f"""
         SELECT rs.readiness_id, rs.city_id, rs.city, rs.target_local_date, rs.temperature_metric,
             rs.data_version, rs.source_id, rs.track, rs.source_run_id, rs.status,
             rs.reason_codes_json, rs.computed_at, rs.expires_at,
               {source_cycle_select} AS source_cycle_time
        FROM {table} AS rs
        {source_run_join}
        WHERE rs.strategy_key = ?
        ORDER BY rs.computed_at DESC
        """,
        (PRODUCER_READINESS_STRATEGY_KEY,),
    ).fetchall()
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_dict = dict(row)
        bucket, bucket_blockers = _bucket_from_producer_row(row_dict)
        key = _bucket_key(bucket)
        item = buckets.setdefault(key, _blank_bucket(bucket))
        producer = item["producer"]
        producer["readiness_count"] += 1
        track = str(row_dict.get("track") or "unknown")
        if track not in producer["tracks"]:
            producer["tracks"].append(track)
        forecast_ready, blockers = _producer_current(row_dict, now_utc=now_utc)
        if forecast_ready:
            producer["live_eligible_count"] += 1
            item["forecast_ready"] = True
        else:
            item["forecast_blockers"] = sorted(set(item["forecast_blockers"] + blockers))
        if bucket_blockers:
            item["calibration_blockers"] = sorted(set(item["calibration_blockers"] + bucket_blockers))
        if "latest_readiness_id" not in producer:
            producer["latest_readiness_id"] = row_dict.get("readiness_id")
            producer["latest_status"] = row_dict.get("status")
            producer["latest_expires_at"] = row_dict.get("expires_at")
    return buckets


def _merge_calibration_counts(
    conn: sqlite3.Connection,
    buckets: dict[str, dict[str, Any]],
    *,
    source_errors: list[dict[str, str]],
) -> None:
    pairs_table = _table_ref(conn, "calibration_pairs_v2")
    if pairs_table is None:
        source_errors.append({"source": "calibration_pairs_v2", "error": "table_missing"})
    else:
        rows = conn.execute(
            f"""
                 SELECT temperature_metric, cluster, season, data_version, cycle, source_id, horizon_profile,
                   SUM(CASE WHEN authority = 'VERIFIED' AND training_allowed = 1 THEN 1 ELSE 0 END) AS verified_pair_count
            FROM {pairs_table}
                 GROUP BY temperature_metric, cluster, season, data_version, cycle, source_id, horizon_profile
            """
        ).fetchall()
        for row in rows:
            bucket = {
                "temperature_metric": str(row["temperature_metric"] or "unknown"),
                "cluster": str(row["cluster"] or "unknown"),
                "season": str(row["season"] or "unknown"),
                "data_version": str(row["data_version"] or "unknown"),
                "cycle": str(row["cycle"] or "unknown"),
                "source_id": str(row["source_id"] or "unknown"),
                "forecast_source_id": str(row["source_id"] or "unknown"),
                "horizon_profile": str(row["horizon_profile"] or "unknown"),
                "track": "unknown",
            }
            item = buckets.setdefault(_bucket_key(bucket), _blank_bucket(bucket))
            item["calibration"]["verified_pair_count"] = int(row["verified_pair_count"] or 0)

    models_table = _table_ref(conn, "platt_models_v2")
    if models_table is None:
        source_errors.append({"source": "platt_models_v2", "error": "table_missing"})
    else:
        rows = conn.execute(
            f"""
                 SELECT temperature_metric, cluster, season, data_version, cycle, source_id, horizon_profile,
                   COUNT(*) AS active_model_count,
                   SUM(CASE WHEN authority = 'VERIFIED' THEN 1 ELSE 0 END) AS active_verified_model_count,
                   MAX(fitted_at) AS latest_fitted_at
            FROM {models_table}
            WHERE is_active = 1
                 GROUP BY temperature_metric, cluster, season, data_version, cycle, source_id, horizon_profile
            """
        ).fetchall()
        for row in rows:
            bucket = {
                "temperature_metric": str(row["temperature_metric"] or "unknown"),
                "cluster": str(row["cluster"] or "unknown"),
                "season": str(row["season"] or "unknown"),
                "data_version": str(row["data_version"] or "unknown"),
                "cycle": str(row["cycle"] or "unknown"),
                "source_id": str(row["source_id"] or "unknown"),
                "forecast_source_id": str(row["source_id"] or "unknown"),
                "horizon_profile": str(row["horizon_profile"] or "unknown"),
                "track": "unknown",
            }
            item = buckets.setdefault(_bucket_key(bucket), _blank_bucket(bucket))
            item["calibration"]["active_model_count"] = int(row["active_model_count"] or 0)
            item["calibration"]["active_verified_model_count"] = int(row["active_verified_model_count"] or 0)
            item["calibration"]["latest_fitted_at"] = row["latest_fitted_at"]


def _finalize_bucket(item: dict[str, Any]) -> dict[str, Any]:
    calibration = item["calibration"]
    blockers = list(item.get("calibration_blockers", []))
    if int(calibration.get("verified_pair_count") or 0) <= 0:
        blockers.append("CALIBRATION_PAIRS_ABSENT")
    if int(calibration.get("active_verified_model_count") or 0) <= 0:
        blockers.append("PLATT_MODEL_ABSENT")
    item["calibration_blockers"] = sorted(set(blockers))
    item["calibration_ready"] = not item["calibration_blockers"]
    if not item["forecast_ready"] and not item["forecast_blockers"]:
        item["forecast_blockers"] = ["FORECAST_READINESS_ABSENT"]
    item["trade_ready"] = bool(item["forecast_ready"] and item["calibration_ready"])
    item["producer"]["tracks"] = sorted(item["producer"].get("tracks", []))
    return item


def build_calibration_serving_status(
    conn: sqlite3.Connection,
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Report forecast readiness and calibration readiness as separate derived dimensions."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    source_errors: list[dict[str, str]] = []
    try:
        buckets = _read_producer_buckets(conn, now_utc=now_utc, source_errors=source_errors)
        _merge_calibration_counts(conn, buckets, source_errors=source_errors)
    except Exception as exc:
        return {
            "schema_version": _SCHEMA_VERSION,
            "status": "query_error",
            "authority": "derived_operator_visibility",
            "buckets": [],
            "source_errors": [{"source": "calibration_serving_status", "error_type": type(exc).__name__, "error": str(exc)}],
        }

    bucket_rows = [_finalize_bucket(item) for item in buckets.values()]
    bucket_rows.sort(key=lambda item: item["bucket_key"])
    observed_total = len(bucket_rows)
    if source_errors:
        status = "partial" if observed_total else "query_error"
    else:
        status = "observed" if observed_total else "certified_empty"
    return {
        "schema_version": _SCHEMA_VERSION,
        "status": status,
        "authority": "derived_operator_visibility",
        "bucket_count": observed_total,
        "forecast_ready_count": sum(1 for item in bucket_rows if item["forecast_ready"]),
        "calibration_ready_count": sum(1 for item in bucket_rows if item["calibration_ready"]),
        "trade_ready_count": sum(1 for item in bucket_rows if item["trade_ready"]),
        "buckets": bucket_rows,
        "source_errors": source_errors,
    }
