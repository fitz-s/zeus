"""Day0ExtremeUpdatedTrigger for EDLI v1."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

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


def _dict_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    names = [description[0] for description in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]
