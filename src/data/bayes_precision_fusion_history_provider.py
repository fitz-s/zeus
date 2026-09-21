"""Current-resolver, possession-time-safe history for Bayes precision fusion."""
from __future__ import annotations

import json
import hashlib
import logging
import math
import sqlite3
from datetime import UTC, date, datetime, time
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.data.bayes_precision_fusion_capture import (
    OPENMETEO_MODEL_IDS,
    ModelHistory,
)
from src.data.bayes_precision_fusion_download import (
    BAYES_PRECISION_FUSION_CELL_SELECTION,
    OPENMETEO_PREVIOUS_RUNS_SOURCE_ID,
    OPENMETEO_PROVIDER,
    PREVIOUS_RUNS_SOURCE_FAMILY,
    SINGLE_RUNS_SOURCE_FAMILY,
    STANDARD_META_STAMPED_SOURCE_FAMILY,
)
from src.data.openmeteo_client import PREVIOUS_RUNS_URL
from src.data.openmeteo_ecmwf_ifs9_anchor import (
    SINGLE_RUNS_FORECAST_URL,
    STANDARD_FORECAST_URL,
)
from src.data.current_settlement_history import read_current_settlement_history

_LOG = logging.getLogger("zeus.bayes_precision_fusion_history_provider")

_STATION_SINGLE_RUNS_HISTORY_TIMEZONES = {
    "cwa_township": {"Taipei": "Asia/Taipei"},
    "cwa_township_hourly_high": {"Taipei": "Asia/Taipei"},
    "cwa_township_hourly_low": {"Taipei": "Asia/Taipei"},
    "hko_fnd": {"Hong Kong": "Asia/Hong_Kong"},
}


def _target_start_utc(city: Any, target_date: str) -> datetime | None:
    try:
        local_start = datetime.combine(
            date.fromisoformat(target_date), time.min, tzinfo=ZoneInfo(str(city.timezone)),
        )
    except (AttributeError, TypeError, ValueError, ZoneInfoNotFoundError):
        return None
    return local_start.astimezone(UTC)


def _parse_utc(value: object, *, sqlite_utc_when_naive: bool = False) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC) if sqlite_utc_when_naive else None
    return parsed.astimezone(UTC)


def _settlement_to_celsius(value: float, unit: str) -> float:
    return (float(value) - 32.0) * 5.0 / 9.0 if unit == "F" else float(value)


def _request_params_match_current_live_product(
    row: sqlite3.Row,
    city: Any,
    *,
    expected_model_name: str,
    lead_days: int,
    endpoint_mode: str,
) -> bool:
    try:
        params = json.loads(str(row["request_params_json"] or ""))
        if not isinstance(params, dict):
            return False
        lead_days = max(0, int(lead_days))
        expected_params: dict[str, object] = {
            "latitude": float(city.lat),
            "longitude": float(city.lon),
            "hourly": "temperature_2m",
            "models": expected_model_name,
            "temperature_unit": "celsius",
            "timezone": str(city.timezone),
            "cell_selection": BAYES_PRECISION_FUSION_CELL_SELECTION,
        }
        if str(row["endpoint"] or "").strip() == "previous_runs":
            expected_params["start_date"] = str(row["target_date"])
            expected_params["end_date"] = str(row["target_date"])
            if lead_days:
                expected_params["hourly"] = f"temperature_2m_previous_day{lead_days}"
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    endpoint = str(row["endpoint"] or "").strip()
    if endpoint_mode == "standard_api_meta_stamped":
        forecast_hours = params.get("forecast_hours")
        if (
            isinstance(forecast_hours, bool)
            or not isinstance(forecast_hours, int)
            or not 1 <= forecast_hours <= 240
        ):
            return False
        expected_params["forecast_hours"] = forecast_hours
        base_url = STANDARD_FORECAST_URL
    elif endpoint == "previous_runs":
        base_url = PREVIOUS_RUNS_URL
    else:
        base_url = SINGLE_RUNS_FORECAST_URL
    if params != expected_params:
        return False
    canonical_params = json.dumps(params, sort_keys=True, separators=(",", ":"))
    expected_hash = hashlib.sha256(
        f"{base_url}?{canonical_params}".encode("utf-8")
    ).hexdigest()
    return str(row["request_url_hash"] or "").strip() == expected_hash


def raw_product_matches_live_source(
    row: sqlite3.Row, city: Any, *, lead_days: int,
) -> bool:
    """Whether a gridded raw row is the live-equivalent physical product.

    ``ecmwf_ifs`` deliberately rejects its historical IFS025 previous-runs
    product.  Station products have their own typed roles and must not call
    this Open-Meteo predicate.
    """
    model = str(row["model"] or "").strip()
    endpoint = str(row["endpoint"] or "").strip()
    endpoint_mode = str(row["endpoint_mode"] or "").strip()
    expected_model = str(OPENMETEO_MODEL_IDS.get(model, model))
    product_id = str(row["product_id"] or "").strip()
    if endpoint == "single_runs" and endpoint_mode == "single_runs":
        source_id, source_family = f"{model}_single_runs", SINGLE_RUNS_SOURCE_FAMILY
        product_matches = product_id == f"{expected_model}::single_runs"
    elif endpoint == "single_runs" and endpoint_mode == "standard_api_meta_stamped":
        source_id, source_family = f"{model}_standard_meta_stamped", STANDARD_META_STAMPED_SOURCE_FAMILY
        cycle = _parse_utc(row["source_cycle_time"])
        prefix = (
            f"{expected_model}::standard_api_meta_stamped"
            f"::run={cycle.isoformat()}::modified="
        ) if cycle is not None else ""
        modification = _parse_utc(product_id[len(prefix):]) if prefix and product_id.startswith(prefix) else None
        product_matches = (
            modification is not None
            and product_id == f"{prefix}{modification.isoformat()}"
        )
    elif endpoint == "previous_runs" and model != "ecmwf_ifs" and endpoint_mode == "previous_runs":
        source_id = OPENMETEO_PREVIOUS_RUNS_SOURCE_ID.get(model, f"{model}_previous_runs")
        source_family = PREVIOUS_RUNS_SOURCE_FAMILY
        product_matches = product_id == f"{expected_model}::previous_runs"
    else:
        return False
    try:
        coordinates_match = (
            math.isclose(float(row["latitude_requested"]), float(city.lat), abs_tol=1e-6)
            and math.isclose(float(row["longitude_requested"]), float(city.lon), abs_tol=1e-6)
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return bool(
        coordinates_match
        and str(row["timezone_requested"] or "").strip() == str(city.timezone)
        and str(row["provider"] or "").strip() == OPENMETEO_PROVIDER
        and str(row["source_id"] or "").strip() == source_id
        and str(row["source_family"] or "").strip() == source_family
        and str(row["model_name"] or "").strip() == expected_model
        and product_matches
        and _request_params_match_current_live_product(
            row, city, expected_model_name=expected_model, lead_days=lead_days,
            endpoint_mode=endpoint_mode,
        )
    )


def raw_second_moment_by_model(
    conn: sqlite3.Connection,
    *,
    city: str,
    metric: str,
    lead_days: int,
    target_date: date | str,
    models: Sequence[str],
    as_of: datetime,
    cities_by_name: Mapping[str, Any] | None = None,
) -> dict[str, tuple[float, int]]:
    """Return raw residual second moments from the same as-of-safe history reader.

    ``as_of`` is required: a target-date midnight is not a possession timestamp.
    Missing/invalid history preserves the caller's existing equal-weight fallback.
    """
    try:
        histories = BayesPrecisionFusionHistoryProvider(
            conn, as_of=as_of, cities_by_name=cities_by_name,
        )(
            city=city, metric=metric, lead_days=int(lead_days),
            target_date=target_date, models=list(models),
        )
    except Exception as exc:  # fail-soft precision only
        _LOG.warning("BAYES_PRECISION_FUSION raw-m2 history failed: %s", exc)
        return {}
    out: dict[str, tuple[float, int]] = {}
    for model, history in histories.items():
        residuals = tuple(history.residual_by_target_date.values())
        if residuals:
            out[str(model)] = (
                float(sum(value * value for value in residuals) / len(residuals)),
                len(residuals),
            )
    return out


class BayesPrecisionFusionHistoryProvider:
    """Read fixed-lead residuals whose forecast and label were possessed by ``as_of``.

    Current settlement eligibility is delegated exclusively to
    ``read_current_settlement_history``.  Raw rows are then restricted to the
    same city/metric/date and require available, captured, and recorded clocks
    before the supplied decision instant.  Per-model/date deduplication prevents
    revisions from inflating a residual sample.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        as_of: datetime,
        cities_by_name: Mapping[str, Any] | None = None,
    ) -> None:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        self._conn = conn
        self._as_of = as_of.astimezone(UTC)
        self._cities_by_name = cities_by_name
        # This provider instance is scoped to one decision instant.  Memoizing
        # labels here avoids re-reading a city for multiple model batches while
        # never crossing an as-of boundary or surviving the process.
        self._eligible_labels: dict[tuple[str, str], dict[str, object]] = {}

    def _cities(self) -> Mapping[str, Any]:
        if self._cities_by_name is not None:
            return self._cities_by_name
        from src.config import runtime_cities_by_name

        return runtime_cities_by_name()

    def __call__(
        self,
        *,
        city: str,
        metric: str,
        lead_days: int,
        target_date: date | str,
        models: Sequence[str],
    ) -> Mapping[str, ModelHistory]:
        models = [str(model) for model in models]
        if not models:
            return {}
        decision_date = target_date.isoformat() if isinstance(target_date, date) else str(target_date)[:10]
        try:
            cache_key = (city, metric)
            settlement_by_date = self._eligible_labels.get(cache_key)
            if settlement_by_date is None:
                city_config = self._cities().get(city)
                if city_config is None:
                    return {}
                labels = read_current_settlement_history(
                    self._conn, cities_by_name={city: city_config}, as_of=self._as_of,
                )
                settlement_by_date = {
                    row.target_date: row for row in labels.rows if row.metric == metric
                }
                self._eligible_labels[cache_key] = settlement_by_date
            settlement_by_date = {
                target: label for target, label in settlement_by_date.items() if target < decision_date
            }
            if not settlement_by_date:
                return {}
            first_eligible_target = min(settlement_by_date)
            placeholders = ",".join("?" for _ in models)
            cursor = self._conn.cursor()
            cursor.row_factory = sqlite3.Row
            rows = cursor.execute(
                f"""
                SELECT raw_model_forecast_id, model, target_date, endpoint,
                       source_cycle_time, source_available_at, captured_at, recorded_at,
                       forecast_value_c, coverage_status, training_allowed, source_id, source_family,
                       product_id, model_name, provider, endpoint_mode,
                       request_params_json, request_url_hash,
                       latitude_requested, longitude_requested,
                       timezone_requested
                  FROM raw_model_forecasts INDEXED BY idx_raw_model_forecasts_history_join
                 WHERE city = ? AND metric = ? AND lead_days = ?
                   AND endpoint IN ('previous_runs', 'single_runs')
                   AND model IN ({placeholders})
                   AND target_date >= ? AND target_date < ?
                 ORDER BY model, target_date, raw_model_forecast_id
                """,
                (city, metric, int(lead_days), *models, first_eligible_target, decision_date),
            ).fetchall()
        except Exception as exc:
            _LOG.warning("BAYES_PRECISION_FUSION history read failed: %s", exc)
            return {}

        city_config = self._cities().get(city)
        if city_config is None:
            return {}
        valid: list[tuple[sqlite3.Row, object]] = []
        for row in rows:
            label = settlement_by_date.get(str(row["target_date"]))
            source_cycle = _parse_utc(row["source_cycle_time"])
            available = _parse_utc(row["source_available_at"])
            captured = _parse_utc(row["captured_at"])
            recorded = _parse_utc(row["recorded_at"], sqlite_utc_when_naive=True)
            try:
                forecast = float(row["forecast_value_c"])
            except (TypeError, ValueError):
                continue
            if (
                label is None or source_cycle is None or available is None or captured is None or recorded is None
                or source_cycle > self._as_of or available > self._as_of
                or captured > self._as_of or recorded > self._as_of
                or not math.isfinite(forecast)
                or str(row["coverage_status"] or "").strip() != "COVERED"
                or int(row["training_allowed"] or 0) != 0
            ):
                continue
            model = str(row["model"])
            is_station = model in _STATION_SINGLE_RUNS_HISTORY_TIMEZONES
            if is_station and int(lead_days) <= 0:
                continue
            if not is_station and not raw_product_matches_live_source(
                row, city_config, lead_days=int(lead_days),
            ):
                continue
            valid.append((row, label))

        station_models = {
            model for model in models
            if int(lead_days) > 0 and city in _STATION_SINGLE_RUNS_HISTORY_TIMEZONES.get(model, {})
        }
        chosen: dict[str, dict[str, tuple[tuple[datetime, datetime, int], float, float]]] = {}
        for row, label in valid:
            model = str(row["model"])
            endpoint = str(row["endpoint"])
            if model in station_models:
                if endpoint != "single_runs":
                    continue
            elif model not in models:
                continue
            available = _parse_utc(row["source_available_at"])
            captured = _parse_utc(row["captured_at"])
            source_cycle = _parse_utc(row["source_cycle_time"])
            assert available is not None and captured is not None and source_cycle is not None
            date_text = str(row["target_date"])
            if endpoint == "single_runs":
                target_start = _target_start_utc(city_config, date_text)
                if target_start is None or available >= target_start or captured >= target_start:
                    continue
            try:
                settlement_c = _settlement_to_celsius(label.settlement_value, label.settlement_unit)
                forecast = float(row["forecast_value_c"])
            except (AttributeError, TypeError, ValueError):
                continue
            rank = (source_cycle, captured, int(row["raw_model_forecast_id"]))
            previous = chosen.setdefault(model, {}).get(date_text)
            if previous is None or rank > previous[0]:
                chosen[model][date_text] = (rank, forecast, settlement_c)

        result: dict[str, ModelHistory] = {}
        for model, by_date in chosen.items():
            dates = tuple(sorted(by_date))
            forecasts = tuple(by_date[day][1] for day in dates)
            settlements = tuple(by_date[day][2] for day in dates)
            if forecasts and len(forecasts) == len(settlements):
                result[model] = ModelHistory(
                    model=model, forecast_values=forecasts,
                    settlement_values=settlements, target_dates=dates,
                )
        return result
