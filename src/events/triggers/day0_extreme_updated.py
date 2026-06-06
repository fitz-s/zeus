"""Day0ExtremeUpdatedTrigger for EDLI v1."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.events.event_writer import EventWriter, EventWriteResult
from src.events.opportunity_event import Day0ExtremeUpdatedPayload, OpportunityEvent, make_day0_extreme_updated_event

UTC = timezone.utc


@dataclass(frozen=True)
class Day0HardFactGate:
    source_match_status: str
    local_date_status: str
    station_match_status: str
    dst_status: str
    metric_match_status: str
    rounding_status: str
    source_authorized_status: str
    live_authority_status: str

    def live_eligible(self) -> bool:
        return (
            self.source_match_status == "MATCH"
            and self.local_date_status == "MATCH"
            and self.station_match_status == "MATCH"
            and self.dst_status == "UNAMBIGUOUS"
            and self.metric_match_status == "MATCH"
            and self.rounding_status == "MATCH"
            and self.source_authorized_status == "AUTHORIZED"
            and self.live_authority_status == "LIVE_AUTHORITY"
        )


def build_day0_extreme_updated_event(
    *,
    observation: dict[str, Any],
    settlement_semantics: Any,
    decision_time: datetime,
    received_at: str,
) -> OpportunityEvent:
    available_at = _parse_utc(observation["observation_available_at"], "observation_available_at")
    if available_at > decision_time.astimezone(UTC):
        raise ValueError("observation_available_at is after decision_time")

    raw_value = float(observation["raw_value"])
    rounded_value = int(settlement_semantics.round_single(raw_value))
    payload = Day0ExtremeUpdatedPayload(
        city=str(observation["city"]),
        target_date=str(observation["target_date"]),
        metric=str(observation["metric"]),  # type: ignore[arg-type]
        settlement_source=str(observation["settlement_source"]),
        station_id=str(observation["station_id"]),
        observation_time=str(observation["observation_time"]),
        observation_available_at=str(observation["observation_available_at"]),
        raw_value=raw_value,
        rounded_value=rounded_value,
        high_so_far=observation.get("high_so_far"),
        low_so_far=observation.get("low_so_far"),
        source_match_status=str(observation.get("source_match_status", "UNKNOWN")),
        local_date_status=str(observation.get("local_date_status", "UNKNOWN")),
        station_match_status=str(observation.get("station_match_status", "UNKNOWN")),
        dst_status=str(observation.get("dst_status", "UNKNOWN")),
        metric_match_status=str(observation.get("metric_match_status", "UNKNOWN")),
        rounding_status=str(observation.get("rounding_status", "UNKNOWN")),
        source_authorized_status=str(observation.get("source_authorized_status", "UNKNOWN")),
        live_authority_status=str(observation.get("live_authority_status", "UNKNOWN")),
    )
    entity_key = "|".join((payload.city, payload.target_date, payload.metric, payload.station_id))
    return make_day0_extreme_updated_event(
        entity_key=entity_key,
        source="day0_extreme_updated_trigger",
        observed_at=payload.observation_time,
        received_at=received_at,
        payload=payload,
        causal_snapshot_id=str(observation.get("observation_context_id") or ""),
        priority=20,
    )


class Day0ExtremeUpdatedTrigger:
    def __init__(self, writer: EventWriter) -> None:
        self._writer = writer

    def emit_from_observation(
        self,
        *,
        observation: dict[str, Any],
        settlement_semantics: Any,
        decision_time: datetime,
        received_at: str,
    ) -> EventWriteResult:
        event = build_day0_extreme_updated_event(
            observation=observation,
            settlement_semantics=settlement_semantics,
            decision_time=decision_time,
            received_at=received_at,
        )
        return self._writer.write(event)

    def scan_authority_rows(
        self,
        *,
        observation_conn: sqlite3.Connection,
        settlement_semantics: Any,
        decision_time: datetime,
        received_at: str,
        limit: int = 100,
    ) -> list[EventWriteResult]:
        """Catch up from persisted Day0 observation authority rows.

        The exact online observation hook can call `emit_from_observation`
        directly. This scanner is the reboot/catch-up path over durable rows and
        emits only the first observation or monotonic high/low extreme changes.
        """

        if not _table_exists(observation_conn, "settlement_day_observation_authority"):
            return []
        rows = _dict_rows(
            observation_conn,
            """
            SELECT *
            FROM settlement_day_observation_authority
            WHERE recorded_at <= ?
            ORDER BY recorded_at DESC, authority_id DESC
            LIMIT ?
            """,
            (decision_time.astimezone(UTC).isoformat(), limit),
        )
        results: list[EventWriteResult] = []
        high_seen: dict[tuple[str, str, str], float] = {}
        low_seen: dict[tuple[str, str, str], float] = {}
        for row in reversed(rows):
            try:
                observation = authority_row_to_observation(row)
            except ValueError:
                continue
            key = (observation["city"], observation["target_date"], observation["metric"])
            high = observation.get("high_so_far")
            low = observation.get("low_so_far")
            should_emit = key not in high_seen and key not in low_seen
            if high is not None and (key not in high_seen or float(high) > high_seen[key]):
                high_seen[key] = float(high)
                should_emit = True
            if low is not None and (key not in low_seen or float(low) < low_seen[key]):
                low_seen[key] = float(low)
                should_emit = True
            if should_emit:
                semantics = settlement_semantics(observation) if callable(settlement_semantics) else settlement_semantics
                results.append(
                    self.emit_from_observation(
                        observation=observation,
                        settlement_semantics=semantics,
                        decision_time=decision_time,
                        received_at=received_at,
                    )
                )
        return results

    def scan_observation_instants_rows(
        self,
        *,
        observation_conn: sqlite3.Connection,
        settlement_semantics: Any,
        decision_time: datetime,
        received_at: str,
        limit: int = 100,
    ) -> list[EventWriteResult]:
        """Emit Day0 events from canonical live observation_instants rows.

        EDLI day0 shadow needs the current observation stream. The older
        settlement_day_observation_authority catch-up table is written only by
        the legacy cycle path and can be stale or empty while live observation
        ingestion is healthy. This scanner reads the canonical world
        observation_instants surface (or an attached ``world`` DB) and emits the
        latest high/low observations per city/date.
        """

        table = _qualified_observation_instants_table(observation_conn)
        if table is None:
            return []
        columns = _table_columns(observation_conn, table)
        required_columns = {
            "city",
            "target_date",
            "source",
            "timezone_name",
            "utc_timestamp",
            "imported_at",
            "running_max",
            "running_min",
            "temp_unit",
            "station_id",
            "authority",
            "training_allowed",
            "causality_status",
            "source_role",
            "provenance_json",
        }
        if not required_columns.issubset(columns):
            return []
        decision_iso = decision_time.astimezone(UTC).isoformat()
        rows = _dict_rows(
            observation_conn,
            f"""
            WITH eligible AS (
                SELECT
                    city,
                    target_date,
                    source,
                    timezone_name,
                    temp_unit,
                    station_id,
                    MAX(utc_timestamp) AS observation_time,
                    MAX(imported_at) AS observation_available_at,
                    MAX(running_max) AS high_so_far,
                    MIN(running_min) AS low_so_far,
                    COUNT(*) AS observation_count,
                    MIN(authority) AS authority,
                    MIN(training_allowed) AS training_allowed,
                    MIN(causality_status) AS causality_status,
                    MIN(source_role) AS source_role
                FROM {table}
                WHERE target_date IS NOT NULL
                  AND target_date >= date(?)
                  AND utc_timestamp <= ?
                  AND imported_at <= ?
                  AND (running_max IS NOT NULL OR running_min IS NOT NULL)
                  AND authority IN ('VERIFIED', 'ICAO_STATION_NATIVE')
                  AND COALESCE(training_allowed, 0) = 1
                  AND COALESCE(causality_status, '') = 'OK'
                  AND COALESCE(source_role, '') = 'historical_hourly'
                  AND COALESCE(provenance_json, '') NOT IN ('', '{{}}')
                  AND COALESCE(station_id, '') != ''
                  AND COALESCE(source, '') != ''
                GROUP BY city, target_date, source, timezone_name, temp_unit, station_id
            )
            SELECT *
            FROM eligible
            ORDER BY observation_available_at DESC, observation_time DESC
            LIMIT ?
            """,
            (decision_iso, decision_iso, decision_iso, max(1, int(limit))),
        )
        results: list[EventWriteResult] = []
        for row in reversed(rows):
            for metric in ("high", "low"):
                try:
                    observation = observation_instant_row_to_day0_observation(row, metric=metric)
                except ValueError:
                    continue
                semantics = settlement_semantics(observation) if callable(settlement_semantics) else settlement_semantics
                results.append(
                    self.emit_from_observation(
                        observation=observation,
                        settlement_semantics=semantics,
                        decision_time=decision_time,
                        received_at=received_at,
                    )
                )
        return results


def authority_row_to_observation(row: dict[str, Any]) -> dict[str, Any]:
    payload = _json_dict(row.get("payload_json"))
    metric = str(row.get("temperature_metric") or payload.get("metric") or "")
    raw_value = row.get("high_so_far") if metric == "high" else row.get("low_so_far")
    raw_value = row.get("current_temp") if raw_value is None else raw_value
    if raw_value is None:
        raise ValueError("Day0 authority row has no high/low/current_temp value")
    return {
        "city": str(row.get("city") or payload.get("city") or ""),
        "target_date": str(row.get("target_date") or payload.get("target_date") or ""),
        "metric": metric,
        "settlement_source": str(row.get("source") or payload.get("settlement_source") or ""),
        "station_id": str(row.get("station_id") or payload.get("station_id") or ""),
        "observation_time": str(row.get("observation_time_utc") or row.get("decision_time_utc") or ""),
        "observation_available_at": str(payload.get("observation_available_at") or row.get("recorded_at") or ""),
        "raw_value": float(raw_value),
        "high_so_far": row.get("high_so_far"),
        "low_so_far": row.get("low_so_far"),
        "source_match_status": payload.get("source_match_status", "UNKNOWN"),
        "local_date_status": "MATCH" if row.get("local_date_matches_target") == 1 else "UNKNOWN",
        "station_match_status": payload.get("station_match_status", "UNKNOWN"),
        "dst_status": payload.get("dst_status", "UNKNOWN"),
        "metric_match_status": payload.get("metric_match_status", "UNKNOWN"),
        "rounding_status": payload.get("rounding_status", "UNKNOWN"),
        "source_authorized_status": (
            "AUTHORIZED" if row.get("source_authorized_for_settlement") == 1 else "UNKNOWN"
        ),
        "live_authority_status": payload.get("live_authority_status", "OBSERVABILITY_ONLY"),
        "settlement_unit": payload.get("settlement_unit") or payload.get("measurement_unit"),
        "settlement_precision": payload.get("settlement_precision") or payload.get("precision"),
        "rounding_rule": payload.get("rounding_rule"),
        "observation_context_id": str(row.get("authority_id") or ""),
    }


def observation_instant_row_to_day0_observation(row: dict[str, Any], *, metric: str = "high") -> dict[str, Any]:
    """Convert a canonical observation_instants row into a live Day0 observation."""

    if metric not in {"high", "low"}:
        raise ValueError(f"unsupported Day0 observation metric: {metric}")
    city = str(row.get("city") or "")
    target_date = str(row.get("target_date") or "")
    station_id = str(row.get("station_id") or "").strip().upper()
    observation_time = str(row.get("observation_time") or row.get("utc_timestamp") or "")
    available_at = str(row.get("observation_available_at") or row.get("imported_at") or observation_time)
    high_so_far = row.get("high_so_far")
    high_so_far = row.get("running_max") if high_so_far is None else high_so_far
    low_so_far = row.get("low_so_far")
    low_so_far = row.get("running_min") if low_so_far is None else low_so_far
    raw_value = high_so_far if metric == "high" else low_so_far
    if not city or not target_date or not station_id or raw_value is None:
        raise ValueError("observation_instants row missing required Day0 fields")
    local_date_status, dst_status = _observation_local_date_status(
        observation_time=observation_time,
        city_timezone=str(row.get("timezone_name") or ""),
        target_date=target_date,
    )
    unit = str(row.get("temp_unit") or "").upper()
    verified = str(row.get("authority") or "").upper() == "VERIFIED"
    trusted_native = str(row.get("authority") or "").upper() == "ICAO_STATION_NATIVE"
    source = str(row.get("source") or "")
    source_match = "MATCH" if source else "MISMATCH"
    station_match = "MATCH" if station_id else "MISMATCH"
    rounding_status = "MATCH" if unit else "MISMATCH"
    source_role = str(row.get("source_role") or "")
    training_allowed = int(row.get("training_allowed") or 0) == 1
    causality_ok = str(row.get("causality_status") or "") == "OK"
    source_authorized = (
        "AUTHORIZED"
        if (
            (verified or trusted_native)
            and source_role == "historical_hourly"
            and training_allowed
            and causality_ok
            and source_match == "MATCH"
            and station_match == "MATCH"
            and rounding_status == "MATCH"
        )
        else "UNAUTHORIZED"
    )
    live_authority = (
        "LIVE_AUTHORITY"
        if (
            source_authorized == "AUTHORIZED"
            and local_date_status == "MATCH"
            and dst_status == "UNAMBIGUOUS"
        )
        else "NON_LIVE_AUTHORITY"
    )
    return {
        "city": city,
        "target_date": target_date,
        "metric": metric,
        "settlement_source": source,
        "station_id": station_id,
        "observation_time": observation_time,
        "observation_available_at": available_at,
        "raw_value": float(raw_value),
        "high_so_far": float(high_so_far) if high_so_far is not None else None,
        "low_so_far": float(low_so_far) if low_so_far is not None else None,
        "source_match_status": source_match,
        "local_date_status": local_date_status,
        "station_match_status": station_match,
        "dst_status": dst_status,
        "metric_match_status": "MATCH",
        "rounding_status": rounding_status,
        "source_authorized_status": source_authorized,
        "live_authority_status": live_authority,
        "settlement_unit": unit,
        "settlement_precision": 1.0,
        "rounding_rule": "wmo_half_up",
        "observation_context_id": str(
            row.get("observation_context_id")
            or f"observation_instants:{city}:{target_date}:{source}:{station_id}:{available_at}"
        ),
    }


def observation_context_to_live_observation(
    *,
    city: Any,
    target_date: str,
    metric: str,
    observation: Any,
    observation_context_id: str = "",
) -> dict[str, Any]:
    """Convert a Day0ObservationContext into a live-authority EDLI observation.

    This is the online source hook: it consumes the actual observation object
    returned by the settlement-bound Day0 provider path. The separate
    settlement_day_observation_authority scanner remains catch-up/evidence and
    defaults to OBSERVABILITY_ONLY.
    """

    observation_time = str(getattr(observation, "observation_time", "") or "")
    available_at = str(getattr(observation, "observation_available_at", "") or "")
    station_id = str(getattr(observation, "station_id", "") or "").strip().upper()
    expected_station = str(getattr(city, "wu_station", "") or "").strip().upper()
    source = str(getattr(observation, "source", "") or "")
    coverage_status = str(getattr(observation, "coverage_status", "") or "").upper()
    unit = str(getattr(observation, "unit", "") or "").upper()
    city_unit = str(getattr(city, "settlement_unit", "") or "").upper()
    city_source_type = str(getattr(city, "settlement_source_type", "") or "")

    local_date_status, dst_status = _observation_local_date_status(
        observation_time=observation_time,
        city_timezone=str(getattr(city, "timezone", "") or ""),
        target_date=str(target_date),
    )
    source_match_status = (
        "MATCH"
        if city_source_type == "wu_icao" and source == "wu_api" and coverage_status == "OK"
        else "MISMATCH"
    )
    station_match_status = (
        "MATCH"
        if expected_station and _station_matches(station_id, expected_station)
        else "MISMATCH"
    )
    metric_match_status = "MATCH" if str(metric) in {"high", "low"} else "MISMATCH"
    rounding_status = "MATCH" if unit and city_unit and unit == city_unit else "MISMATCH"
    source_authorized_status = (
        "AUTHORIZED"
        if source_match_status == "MATCH" and station_match_status == "MATCH" and rounding_status == "MATCH"
        else "UNAUTHORIZED"
    )
    live_authority_status = (
        "LIVE_AUTHORITY"
        if (
            available_at
            and source_match_status == "MATCH"
            and local_date_status == "MATCH"
            and station_match_status == "MATCH"
            and dst_status == "UNAMBIGUOUS"
            and metric_match_status == "MATCH"
            and rounding_status == "MATCH"
            and source_authorized_status == "AUTHORIZED"
        )
        else "NON_LIVE_AUTHORITY"
    )
    raw_value = getattr(observation, "high_so_far", None) if str(metric) == "high" else getattr(observation, "low_so_far", None)
    if raw_value is None:
        raw_value = getattr(observation, "current_temp", None)
    if raw_value is None:
        raise ValueError("Day0ObservationContext has no high/low/current_temp value")
    return {
        "city": str(getattr(city, "name", "") or ""),
        "target_date": str(target_date),
        "metric": str(metric),
        "settlement_source": source,
        "station_id": station_id,
        "observation_time": observation_time,
        "observation_available_at": available_at,
        "raw_value": float(raw_value),
        "high_so_far": getattr(observation, "high_so_far", None),
        "low_so_far": getattr(observation, "low_so_far", None),
        "source_match_status": source_match_status,
        "local_date_status": local_date_status,
        "station_match_status": station_match_status,
        "dst_status": dst_status,
        "metric_match_status": metric_match_status,
        "rounding_status": rounding_status,
        "source_authorized_status": source_authorized_status,
        "live_authority_status": live_authority_status,
        "settlement_unit": unit or city_unit,
        "settlement_precision": 1.0,
        "rounding_rule": "wmo_half_up",
        "observation_context_id": observation_context_id,
    }


def _station_matches(station_id: str, expected_station: str) -> bool:
    return station_id == expected_station or station_id.startswith(f"{expected_station}:")


def _observation_local_date_status(*, observation_time: str, city_timezone: str, target_date: str) -> tuple[str, str]:
    try:
        parsed = datetime.fromisoformat(str(observation_time).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return "UNKNOWN", "AMBIGUOUS"
        local = parsed.astimezone(ZoneInfo(city_timezone))
        expected = datetime.fromisoformat(str(target_date)[:10]).date()
        return ("MATCH" if local.date() == expected else "MISMATCH"), "UNAMBIGUOUS"
    except (ValueError, TypeError, OSError):
        return "UNKNOWN", "AMBIGUOUS"


def _parse_utc(value: str, field_name: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _json_dict(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def _qualified_observation_instants_table(conn: sqlite3.Connection) -> str | None:
    for schema, table in (("world", "world.observation_instants"), ("main", "observation_instants")):
        try:
            exists = conn.execute(
                f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name='observation_instants'"
            ).fetchone()
        except sqlite3.Error:
            exists = None
        if exists is not None:
            return table
    return None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    pragma_name = table_name
    if "." in table_name:
        schema, bare_table = table_name.split(".", 1)
        pragma_name = f"{schema}.table_info({bare_table})"
        rows = conn.execute(f"PRAGMA {pragma_name}").fetchall()
    else:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _dict_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    names = [description[0] for description in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]
