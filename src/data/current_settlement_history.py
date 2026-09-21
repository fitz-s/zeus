"""Read only settlement labels proven compatible with the current resolver contract.

This reader is deliberately stricter than a city/date/metric join.  A forecast
skill fit may only use a label when the resolved market, settlement source
regime, exact observation, and availability times all describe the same current
settlement contract.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Mapping

from src.config import settlement_source_type_for_city
from src.contracts.residual_key import SettlementIncompleteError, _station_from_settlement_source
from src.contracts.settlement_semantics import SettlementSemantics

_CURRENT_RESOLVER_ERA = "internal_resolver_post_2026_02_21"
_CURRENT_RESOLVER_START = date(2026, 2, 21)


@dataclass(frozen=True)
class EligibleSettlementHistoryRow:
    city: str
    target_date: str
    metric: str
    settlement_value: float
    settlement_unit: str
    label_known_at: datetime
    settled_at: datetime
    recorded_at: datetime
    observation_fetched_at: datetime
    source_type: str
    station_id: str | None
    rounding_rule: str
    page_view: str | None
    observation_id: int


@dataclass(frozen=True)
class SettlementHistoryRead:
    rows: tuple[EligibleSettlementHistoryRow, ...]
    excluded_reason_counts: Mapping[str, int]


def _parse_instant(value: object, *, allow_sqlite_utc: bool = False) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC) if allow_sqlite_utc else None
    return parsed.astimezone(UTC)


def _parse_json(value: object) -> dict[str, Any] | None:
    try:
        parsed = json.loads(str(value)) if isinstance(value, str) else dict(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalized_source_type(value: object) -> str | None:
    raw = str(value or "").strip().lower()
    aliases = {"wu": "wu_icao", "wu_icao": "wu_icao", "noaa": "noaa", "hko": "hko"}
    return aliases.get(raw)


def _metric_provenance(row: sqlite3.Row, metric: str) -> dict[str, Any] | None:
    field = "high_provenance_metadata" if metric == "high" else "low_provenance_metadata"
    return _parse_json(row[field])


def _metric_fetch_time(row: sqlite3.Row, metric: str) -> datetime | None:
    field = "high_fetch_utc" if metric == "high" else "low_fetch_utc"
    return _parse_instant(row[field])


def _current_source_epoch_start(city: Any) -> date | None:
    effective = getattr(city, "settlement_source_type_effective_date", None)
    if effective in (None, ""):
        return _CURRENT_RESOLVER_START
    try:
        return max(_CURRENT_RESOLVER_START, date.fromisoformat(str(effective)[:10]))
    except (TypeError, ValueError):
        return None


def _expected_observation_source(source_type: str, station: str) -> str | None:
    if source_type == "wu_icao":
        return "wu_icao_history"
    if source_type == "noaa":
        return f"noaa_wrh_{station.lower()}" if station else None
    if source_type == "hko":
        return "hko_daily_api"
    return None


def _outcome_rows_for_city(conn: sqlite3.Connection, city: str, start: date) -> list[sqlite3.Row]:
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    return cursor.execute(
        """
        SELECT city, target_date, temperature_metric, winning_bin, settlement_value,
               settlement_source, settled_at, authority, provenance_json, recorded_at,
               settlement_unit
          FROM settlement_outcomes
         WHERE city = ?
           AND authority = 'VERIFIED'
           AND target_date >= ?
         ORDER BY target_date, temperature_metric, settlement_id
        """,
        (city, start.isoformat()),
    ).fetchall()


def _observation_by_id(conn: sqlite3.Connection, observation_id: int) -> sqlite3.Row | None:
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    return cursor.execute(
        """
        SELECT id, city, target_date, source, station_id, unit, data_source_version,
               high_temp, low_temp, high_fetch_utc, low_fetch_utc,
               high_provenance_metadata, low_provenance_metadata
          FROM observations
         WHERE id = ?
        """,
        (observation_id,),
    ).fetchone()


def read_current_settlement_history(
    conn: sqlite3.Connection,
    *,
    cities_by_name: Mapping[str, Any],
    as_of: datetime,
) -> SettlementHistoryRead:
    """Return only current-resolver settlement labels known before ``as_of``.

    The caller supplies the canonical forecast-store connection and the runtime
    city map.  The reader never writes.  An outcome must point to its exact
    observation through ``provenance_json.obs_id``; it never searches for a
    replacement observation by city/date.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    cutoff = as_of.astimezone(UTC)
    eligible: list[EligibleSettlementHistoryRow] = []
    excluded: Counter[str] = Counter()

    for city_name, city in sorted(cities_by_name.items()):
        epoch_start = _current_source_epoch_start(city)
        if epoch_start is None:
            excluded["CONFIG_EFFECTIVE_DATE_INVALID"] += 1
            continue
        for outcome in _outcome_rows_for_city(conn, str(city_name), date.min):
            target_raw = str(outcome["target_date"] or "")
            try:
                target = date.fromisoformat(target_raw[:10])
            except ValueError:
                excluded["TARGET_DATE_INVALID"] += 1
                continue
            source_type = settlement_source_type_for_city(city, target)
            if target < epoch_start or source_type != str(getattr(city, "settlement_source_type", "")):
                excluded["PRE_CURRENT_SOURCE_EPOCH"] += 1
                continue
            metric = str(outcome["temperature_metric"] or "").lower()
            if metric not in {"high", "low"} or not str(outcome["winning_bin"] or "").strip():
                excluded["OUTCOME_NOT_FINAL"] += 1
                continue
            try:
                value = float(outcome["settlement_value"])
            except (TypeError, ValueError):
                excluded["OUTCOME_VALUE_INVALID"] += 1
                continue
            if not math.isfinite(value) or not value.is_integer():
                excluded["OUTCOME_VALUE_NOT_INTEGER"] += 1
                continue
            unit = str(outcome["settlement_unit"] or "").upper()
            if unit not in {"C", "F"} or unit != str(getattr(city, "settlement_unit", "")).upper():
                excluded["OUTCOME_UNIT_MISMATCH"] += 1
                continue
            settled_at = _parse_instant(outcome["settled_at"])
            recorded_at = _parse_instant(outcome["recorded_at"], allow_sqlite_utc=True)
            if settled_at is None or recorded_at is None:
                excluded["OUTCOME_TIME_MISSING_OR_INVALID"] += 1
                continue
            if settled_at >= cutoff or recorded_at >= cutoff:
                excluded["OUTCOME_NOT_KNOWN_AS_OF"] += 1
                continue
            provenance = _parse_json(outcome["provenance_json"])
            if provenance is None:
                excluded["OUTCOME_PROVENANCE_INVALID"] += 1
                continue
            if (
                provenance.get("era") != _CURRENT_RESOLVER_ERA
                or str(provenance.get("era_start_date_utc") or "") != _CURRENT_RESOLVER_START.isoformat()
            ):
                excluded["NOT_CURRENT_RESOLVER_ERA"] += 1
                continue
            if (
                _normalized_source_type(provenance.get("source_family")) != source_type
                or _normalized_source_type(provenance.get("settlement_source_type")) != source_type
            ):
                excluded["OUTCOME_SOURCE_FAMILY_MISMATCH"] += 1
                continue
            try:
                observation_id = int(provenance["obs_id"])
            except (KeyError, TypeError, ValueError):
                excluded["OUTCOME_OBSERVATION_ID_MISSING"] += 1
                continue
            observation = _observation_by_id(conn, observation_id)
            if observation is None:
                excluded["EXACT_OBSERVATION_MISSING"] += 1
                continue
            station = str(getattr(city, "wu_station", "") or "").strip().upper()
            expected_source = _expected_observation_source(source_type, station)
            if expected_source is None:
                excluded["SOURCE_TYPE_UNSUPPORTED"] += 1
                continue
            if (
                str(observation["city"] or "") != str(city_name)
                or str(observation["target_date"] or "") != target.isoformat()
                or str(observation["source"] or "").lower() != expected_source
                or str(observation["unit"] or "").upper() != unit
            ):
                excluded["EXACT_OBSERVATION_IDENTITY_MISMATCH"] += 1
                continue
            if source_type != "hko" and str(observation["station_id"] or "").strip().upper() != station:
                excluded["OBSERVATION_STATION_MISMATCH"] += 1
                continue
            try:
                parsed_station = _station_from_settlement_source(str(outcome["settlement_source"] or "")).upper()
            except SettlementIncompleteError:
                parsed_station = None
            if source_type != "hko" and parsed_station != station:
                excluded["OUTCOME_STATION_MISMATCH"] += 1
                continue
            observation_fetch = _metric_fetch_time(observation, metric)
            if observation_fetch is None:
                excluded["OBSERVATION_FETCH_TIME_MISSING_OR_INVALID"] += 1
                continue
            if observation_fetch >= cutoff:
                excluded["OBSERVATION_NOT_KNOWN_AS_OF"] += 1
                continue
            metric_provenance = _metric_provenance(observation, metric)
            if metric_provenance is None:
                excluded["OBSERVATION_PROVENANCE_INVALID"] += 1
                continue
            if source_type == "noaa":
                expected_view = str(getattr(city, "settlement_page_view", "all") or "all")
                if metric_provenance.get("settlement_page_view") != expected_view:
                    excluded["SETTLEMENT_PAGE_VIEW_MISMATCH"] += 1
                    continue
                if str(metric_provenance.get("station") or "").strip().upper() != station:
                    excluded["OBSERVATION_PROVENANCE_STATION_MISMATCH"] += 1
                    continue
                if str(observation["data_source_version"] or "") != "noaa_wrh_timeseries_v1":
                    excluded["OBSERVATION_PRODUCT_MISMATCH"] += 1
                    continue
                page_view: str | None = expected_view
            else:
                page_view = None
            try:
                semantics = SettlementSemantics.for_city(city)
                rounded_observation = semantics.assert_settlement_value(
                    float(observation["high_temp"] if metric == "high" else observation["low_temp"]),
                    context=f"current_settlement_history/{city_name}/{target}/{metric}",
                )
            except (TypeError, ValueError):
                excluded["OBSERVATION_VALUE_INVALID"] += 1
                continue
            if semantics.rounding_rule != provenance.get("rounding_rule"):
                excluded["ROUNDING_RULE_MISMATCH"] += 1
                continue
            if float(rounded_observation) != value:
                excluded["OBSERVATION_VALUE_MISMATCH"] += 1
                continue
            eligible.append(
                EligibleSettlementHistoryRow(
                    city=str(city_name), target_date=target.isoformat(), metric=metric,
                    settlement_value=value, settlement_unit=unit,
                    label_known_at=max(settled_at, recorded_at, observation_fetch),
                    settled_at=settled_at, recorded_at=recorded_at,
                    observation_fetched_at=observation_fetch,
                    source_type=source_type, station_id=(None if source_type == "hko" else station),
                    rounding_rule=semantics.rounding_rule, page_view=page_view,
                    observation_id=observation_id,
                )
            )
    eligible.sort(key=lambda row: (row.label_known_at, row.city, row.target_date, row.metric))
    return SettlementHistoryRead(rows=tuple(eligible), excluded_reason_counts=dict(sorted(excluded.items())))
