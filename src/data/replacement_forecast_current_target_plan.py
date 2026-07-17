"""Current-market coverage plan for replacement forecast materialization."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.data.replacement_forecast_cycle_policy import tradeable_grade_coverage_sql
from src.data.replacement_input_hwm import (
    prime_frozen_replacement_artifact_hwm,
)
from src.data.replacement_forecast_source_run_identity import expected_replacement_dependency_identity_by_role
from src.engine.time_context import has_city_local_day_started
from src.state.db import _connect


SOURCE_ID = "openmeteo_ecmwf_ifs9_bayes_fusion"


@dataclass(frozen=True)
class ReplacementForecastCurrentTargetPlanRow:
    city: str
    target_date: str
    temperature_metric: str
    market_bin_count: int
    posterior_count: int
    readiness_count: int
    openmeteo_manifest_count: int
    fusion_current_value_count: int = 0
    baseline_source_run_id: str | None = None
    baseline_source_cycle_time: str | None = None
    openmeteo_source_run_id: str | None = None
    day0_observed_extreme_required: bool = False
    input_lag_reason: str | None = None

    @property
    def covered(self) -> bool:
        return (
            self.posterior_count > 0
            and self.readiness_count > 0
            and self.input_lag_reason is None
        )

    @property
    def can_seed(self) -> bool:
        # Live seeding needs the OM9 anchor plus already-captured fusion rows.
        # Removed model families are not completeness requirements here.
        return (
            not self.covered
            and not self.day0_observed_extreme_required
            and self.openmeteo_manifest_count > 0
            and self.fusion_current_value_count > 0
        )

    @property
    def missing_openmeteo_manifest(self) -> bool:
        return not self.covered and self.openmeteo_manifest_count <= 0

    @property
    def missing_fusion_current_values(self) -> bool:
        return (
            not self.covered
            and not self.day0_observed_extreme_required
            and self.openmeteo_manifest_count > 0
            and self.fusion_current_value_count <= 0
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "city": self.city,
            "target_date": self.target_date,
            "temperature_metric": self.temperature_metric,
            "market_bin_count": self.market_bin_count,
            "posterior_count": self.posterior_count,
            "readiness_count": self.readiness_count,
            "openmeteo_manifest_count": self.openmeteo_manifest_count,
            "fusion_current_value_count": self.fusion_current_value_count,
            "baseline_source_run_id": self.baseline_source_run_id,
            "baseline_source_cycle_time": self.baseline_source_cycle_time,
            "openmeteo_source_run_id": self.openmeteo_source_run_id,
            "day0_observed_extreme_required": self.day0_observed_extreme_required,
            "input_lag_reason": self.input_lag_reason,
            "covered": self.covered,
            "can_seed": self.can_seed,
            "missing_openmeteo_manifest": self.missing_openmeteo_manifest,
            "missing_fusion_current_values": self.missing_fusion_current_values,
        }


@dataclass(frozen=True)
class ReplacementForecastCurrentTargetPlan:
    status: str
    reason_codes: tuple[str, ...]
    target_count: int
    covered_count: int
    missing_coverage_count: int
    can_seed_count: int
    missing_openmeteo_manifest_count: int
    missing_fusion_current_values_count: int
    day0_observed_extreme_required_count: int
    rows: tuple[ReplacementForecastCurrentTargetPlanRow, ...]

    @property
    def ready(self) -> bool:
        return self.status == "CURRENT_TARGETS_COVERED"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "target_count": self.target_count,
            "covered_count": self.covered_count,
            "missing_coverage_count": self.missing_coverage_count,
            "can_seed_count": self.can_seed_count,
            "missing_openmeteo_manifest_count": self.missing_openmeteo_manifest_count,
            "missing_fusion_current_values_count": self.missing_fusion_current_values_count,
            "day0_observed_extreme_required_count": self.day0_observed_extreme_required_count,
            "rows": [row.as_dict() for row in self.rows],
            "ready": self.ready,
        }


@dataclass(frozen=True)
class ReplacementForecastTargetKey:
    city: str
    target_date: str
    temperature_metric: str


@dataclass(frozen=True)
class _OpenMeteoManifest:
    artifact_path: str
    metadata: Mapping[str, object]
    column_source_cycle_time: str
    source_cycle_time: str
    source_available_at: str
    captured_at: str


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    }


def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _raw_artifact_metadata_column(columns: set[str]) -> str | None:
    if "product_metadata_json" in columns:
        return "product_metadata_json"
    if "artifact_metadata_json" in columns:
        return "artifact_metadata_json"
    return None


def _supports_source_run_targets(conn: sqlite3.Connection) -> bool:
    tables = _table_names(conn)
    if "source_run_coverage" not in tables or "source_run" not in tables:
        return False
    required = {
        "source_run_id",
        "source_id",
        "city",
        "target_local_date",
        "temperature_metric",
        "data_version",
        "computed_at",
    }
    source_run_required = {"source_run_id", "source_cycle_time"}
    return required.issubset(_columns(conn, "source_run_coverage")) and source_run_required.issubset(
        _columns(conn, "source_run")
    )


def _json_object(text: object) -> dict[str, object]:
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _row_value(row: sqlite3.Row, key: str) -> object | None:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _openmeteo_source_run_id(metadata: Mapping[str, object]) -> str | None:
    value = metadata.get("source_run_id")
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def _cycle_at_or_after(candidate: str, floor: str | None) -> bool:
    if floor is None or not str(floor).strip():
        return True
    if not str(candidate or "").strip():
        return False
    try:
        candidate_dt = datetime.fromisoformat(str(candidate).replace("Z", "+00:00")).astimezone(timezone.utc)
        floor_dt = datetime.fromisoformat(str(floor).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return str(candidate) >= str(floor)
    return candidate_dt >= floor_dt


def _path_from_metadata_path(
    path_text: object,
    *,
    base_dir: Path,
) -> Path | None:
    if path_text is None or not str(path_text).strip():
        return None
    path = Path(str(path_text))
    if not path.is_absolute():
        path = base_dir / path
    return path


def _openmeteo_payload_covers_target_local_day(
    metadata: Mapping[str, object],
    *,
    artifact_path: str,
    city_timezone: str | None,
    target_date: str,
    cache: dict[tuple[str, str, str], bool] | None = None,
) -> bool:
    """Return whether an explicit Open-Meteo payload has target-local-day samples.

    ``raw_forecast_artifacts`` rows can point at a manifest whose metadata says a
    target date is in horizon while the on-disk payload is a clipped partial
    response. That false positive makes the downloader skip the fresh cycle and
    lets the materializer fail later with "insufficient Open-Meteo hourly
    samples inside target local day". Only explicit ``openmeteo_payload_json``
    payloads are checked here so old fixture/dummy artifacts keep their legacy
    existence-only semantics.
    """

    if not city_timezone:
        return True
    payload_path = _path_from_metadata_path(
        metadata.get("openmeteo_payload_json"),
        base_dir=Path(artifact_path).parent,
    )
    if payload_path is None:
        return True
    cache_key = (str(payload_path), str(city_timezone), str(target_date))
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    if not payload_path.exists():
        if cache is not None:
            cache[cache_key] = False
        return False
    try:
        from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: PLC0415
            extract_openmeteo_ecmwf_ifs9_localday_anchor,
        )

        wanted = date.fromisoformat(str(target_date).strip())
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except Exception:
        if cache is not None:
            cache[cache_key] = False
        return False
    try:
        extract_openmeteo_ecmwf_ifs9_localday_anchor(
            payload,
            city_timezone=city_timezone,
            target_local_date=wanted,
            min_hourly_samples=1,
            require_full_localday=False,
        )
    except Exception:
        if cache is not None:
            cache[cache_key] = False
        return False
    if cache is not None:
        cache[cache_key] = True
    return True


def _openmeteo_manifest_metadata_allows_target_date(
    metadata: Mapping[str, object],
    *,
    target_date: str,
) -> bool:
    dates = metadata.get("target_dates")
    if isinstance(dates, list) and dates:
        if target_date in {str(item).strip() for item in dates}:
            return True
        return _openmeteo_manifest_horizon_allows_target_date(
            metadata, target_date=target_date
        )
    explicit = metadata.get("target_date")
    if explicit is not None and str(explicit).strip() == target_date:
        return True
    return _openmeteo_manifest_horizon_allows_target_date(
        metadata, target_date=target_date
    )


def _openmeteo_manifest_horizon_allows_target_date(
    metadata: Mapping[str, object],
    *,
    target_date: str,
) -> bool:
    if str(metadata.get("artifact_class") or "") != "openmeteo_ecmwf_ifs9_anchor_current_targets":
        return False
    endpoint = str(metadata.get("openmeteo_endpoint") or "")
    if endpoint and endpoint not in {"single_runs_api", "standard_api_meta_stamped"}:
        return False
    start_raw = metadata.get("target_date")
    if start_raw is None or not str(start_raw).strip():
        return False
    try:
        start = date.fromisoformat(str(start_raw).strip())
        wanted = date.fromisoformat(str(target_date).strip())
        hours = int(float(metadata.get("forecast_hours") or 0))
    except Exception:
        return False
    if hours <= 0:
        return False
    max_extra_days = max(0, (hours + 23) // 24)
    return start <= wanted <= start + timedelta(days=max_extra_days)


def _load_openmeteo_manifest_index(
    conn: sqlite3.Connection,
    *,
    raw_artifact_columns: set[str],
    metadata_column: str | None,
    identities: set[tuple[str, str, str]],
    cities: set[str],
    minimum_source_cycle_time: str | None = None,
) -> dict[tuple[str, str, str], tuple[_OpenMeteoManifest, ...]]:
    if metadata_column is None or not identities or not cities:
        return {}
    optional_columns = [
        col
        for col in ("source_cycle_time", "source_available_at", "captured_at", "recorded_at")
        if col in raw_artifact_columns
    ]
    select_optional = "".join(f", {col}" for col in optional_columns)
    has_product_id = "product_id" in raw_artifact_columns
    if has_product_id:
        identity_clause = " OR ".join(
            "(source_id = ? AND product_id = ? AND data_version = ?)"
            for _ in identities
        )
        identity_params = tuple(
            value for identity in sorted(identities) for value in identity
        )
    else:
        identity_clause = " OR ".join(
            "(source_id = ? AND data_version = ?)" for _ in identities
        )
        identity_params = tuple(
            value
            for source_id, _, data_version in sorted(identities)
            for value in (source_id, data_version)
        )
    city_placeholders = ",".join("?" for _ in cities)
    city_params = tuple(sorted(cities))
    cycle_clause = (
        "AND source_cycle_time >= ?"
        if minimum_source_cycle_time and "source_cycle_time" in raw_artifact_columns
        else ""
    )
    cycle_params = (
        (str(minimum_source_cycle_time),)
        if cycle_clause
        else ()
    )
    rows = conn.execute(
        f"""
        SELECT source_id, data_version, artifact_path,
               {metadata_column} AS metadata_json{select_optional}
        FROM raw_forecast_artifacts
        WHERE ({identity_clause})
          {cycle_clause}
          AND artifact_path IS NOT NULL
          AND artifact_path != ''
          AND (
            json_extract({metadata_column}, '$.city') IN ({city_placeholders})
            OR EXISTS (
                SELECT 1
                FROM json_each({metadata_column}, '$.cities')
                WHERE value IN ({city_placeholders})
            )
          )
        """,
        (*identity_params, *cycle_params, *city_params, *city_params),
    ).fetchall()
    index: dict[tuple[str, str, str], list[_OpenMeteoManifest]] = {}
    for row in rows:
        artifact_path = str(row["artifact_path"] or "")
        if not artifact_path or not os.path.exists(artifact_path):
            continue
        metadata = _json_object(row["metadata_json"])
        manifest_cities = {str(metadata.get("city") or "").strip()}
        raw_cities = metadata.get("cities")
        if isinstance(raw_cities, list):
            manifest_cities.update(
                str(value).strip()
                for value in raw_cities
                if str(value).strip()
            )
        manifest_cities.discard("")
        column_source_cycle_time = str(
            _row_value(row, "source_cycle_time") or ""
        )
        source_cycle_time = str(
            column_source_cycle_time
            or metadata.get("source_cycle_time")
            or ""
        )
        source_available_at = str(
            _row_value(row, "source_available_at")
            or metadata.get("source_available_at")
            or metadata.get("requested_source_available_at")
            or ""
        )
        captured_at = str(
            _row_value(row, "captured_at")
            or metadata.get("captured_at")
            or _row_value(row, "recorded_at")
            or ""
        )
        manifest = _OpenMeteoManifest(
            artifact_path=artifact_path,
            metadata=metadata,
            column_source_cycle_time=column_source_cycle_time,
            source_cycle_time=source_cycle_time,
            source_available_at=source_available_at,
            captured_at=captured_at,
        )
        source_id = str(row["source_id"])
        data_version = str(row["data_version"])
        for city in manifest_cities & cities:
            index.setdefault((source_id, data_version, city), []).append(manifest)
    return {key: tuple(value) for key, value in index.items()}


def _openmeteo_manifest_coverage(
    manifests: tuple[_OpenMeteoManifest, ...],
    *,
    target_date: str,
    city_timezone: str | None = None,
    required_source_cycle_time: str | None = None,
    minimum_source_cycle_time: str | None = None,
    payload_coverage_cache: dict[tuple[str, str, str], bool] | None = None,
) -> tuple[int, str | None, str | None]:
    candidates: list[tuple[tuple[str, str, str, str], str | None]] = []
    for manifest in manifests:
        if required_source_cycle_time and required_source_cycle_time not in {
            manifest.column_source_cycle_time,
            str(manifest.metadata.get("source_cycle_time") or ""),
        }:
            continue
        if not _cycle_at_or_after(
            manifest.source_cycle_time, minimum_source_cycle_time
        ):
            continue
        if not _openmeteo_manifest_metadata_allows_target_date(
            manifest.metadata, target_date=target_date
        ):
            continue
        if not _openmeteo_payload_covers_target_local_day(
            manifest.metadata,
            artifact_path=manifest.artifact_path,
            city_timezone=city_timezone,
            target_date=target_date,
            cache=payload_coverage_cache,
        ):
            continue
        source_run_id = _openmeteo_source_run_id(manifest.metadata)
        candidates.append(
            (
                (
                    manifest.source_cycle_time,
                    manifest.source_available_at,
                    manifest.captured_at,
                    manifest.artifact_path,
                ),
                source_run_id,
            )
        )
    if not candidates:
        return 0, None, None
    latest = max(candidates, key=lambda item: item[0])
    return len(candidates), latest[1], latest[0][0]


def _replacement_coverage_counts_for_dependencies(
    conn: sqlite3.Connection,
    *,
    requests: set[tuple[str, str, str, str, str]],
    posterior_tradeable_grade_clause: str,
    readiness_status_clause: str,
) -> dict[tuple[str, str, str, str, str], tuple[int, int]]:
    counts = {request: [0, 0] for request in requests}
    ordered = sorted(requests)
    for offset in range(0, len(ordered), 100):
        chunk = ordered[offset : offset + 100]
        values = ",".join("(?, ?, ?, ?, ?)" for _ in chunk)
        params = tuple(value for request in chunk for value in request)
        posterior_rows = conn.execute(
            f"""
            WITH requested(
                city,
                target_date,
                temperature_metric,
                baseline_source_run_id,
                openmeteo_source_run_id
            ) AS (VALUES {values})
            SELECT requested.city,
                   requested.target_date,
                   requested.temperature_metric,
                   requested.baseline_source_run_id,
                   requested.openmeteo_source_run_id,
                   COUNT(p.source_id) AS posterior_count
              FROM requested
              LEFT JOIN forecast_posteriors p
                ON p.source_id = ?
               AND p.training_allowed = 0
               AND p.runtime_layer = 'live'
               AND p.city = requested.city
               AND p.target_date = requested.target_date
               AND p.temperature_metric = requested.temperature_metric
               {posterior_tradeable_grade_clause}
               AND json_extract(
                       p.dependency_source_run_ids_json,
                       '$.baseline_b0'
                   ) = requested.baseline_source_run_id
               AND json_extract(
                       p.dependency_source_run_ids_json,
                       '$.openmeteo_ifs9_anchor'
                   ) = requested.openmeteo_source_run_id
             GROUP BY 1, 2, 3, 4, 5
            """,
            (*params, SOURCE_ID),
        ).fetchall()
        for row in posterior_rows:
            key = tuple(str(row[index]) for index in range(5))
            counts[key][0] = int(row["posterior_count"])
        readiness_rows = conn.execute(
            f"""
            WITH requested(
                city,
                target_date,
                temperature_metric,
                baseline_source_run_id,
                openmeteo_source_run_id
            ) AS (VALUES {values})
            SELECT requested.city,
                   requested.target_date,
                   requested.temperature_metric,
                   requested.baseline_source_run_id,
                   requested.openmeteo_source_run_id,
                   COUNT(r.strategy_key) AS readiness_count
              FROM requested
              LEFT JOIN readiness_state r
                ON r.strategy_key = ?
               AND json_extract(r.provenance_json, '$.city') = requested.city
               AND json_extract(r.provenance_json, '$.target_date') = requested.target_date
               AND json_extract(
                       r.provenance_json,
                       '$.temperature_metric'
                   ) = requested.temperature_metric
               {readiness_status_clause}
               AND EXISTS (
                   SELECT 1
                     FROM json_each(r.dependency_json, '$.dependencies')
                    WHERE json_extract(value, '$.role') = 'baseline_b0'
                      AND json_extract(
                              value,
                              '$.source_run_id'
                          ) = requested.baseline_source_run_id
               )
               AND EXISTS (
                   SELECT 1
                     FROM json_each(r.dependency_json, '$.dependencies')
                    WHERE json_extract(value, '$.role') = 'openmeteo_ifs9_anchor'
                      AND json_extract(
                              value,
                              '$.source_run_id'
                          ) = requested.openmeteo_source_run_id
               )
             GROUP BY 1, 2, 3, 4, 5
            """,
            (*params, SOURCE_ID),
        ).fetchall()
        for row in readiness_rows:
            key = tuple(str(row[index]) for index in range(5))
            counts[key][1] = int(row["readiness_count"])
    return {key: (value[0], value[1]) for key, value in counts.items()}


def _fusion_current_value_count(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    source_cycle_time: str | None,
    raw_model_forecasts_available: bool | None = None,
) -> int:
    """Count current values the materializer q path can actually serve for a scope."""

    if not source_cycle_time or not str(source_cycle_time).strip():
        return 0
    if raw_model_forecasts_available is None:
        raw_model_forecasts_available = "raw_model_forecasts" in _table_names(conn)
    if not raw_model_forecasts_available:
        # Legacy/fixture DBs without fusion capture storage cannot prove absence here.
        return 1
    try:
        from src.data.replacement_current_value_serving import (  # noqa: PLC0415
            read_current_instrument_values,
        )

        return len(
            read_current_instrument_values(
                conn,
                city=city,
                metric=temperature_metric,
                target_date=target_date,
                source_cycle_time_iso=str(source_cycle_time),
                include_station_sources=True,
            )
        )
    except Exception:
        return 0


def _latest_authorized_day0_fact(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    decision_time: datetime,
    require_settlement_channel: bool = False,
    ) -> dict[str, object] | None:
    """Latest Day0 fact, optionally restricted to the settlement channel.

    Same-station fast observations are current physical evidence, but the
    prediction-market payoff is defined by the declared settlement channel.
    They may advance refresh/redecision; they cannot alone create exact
    absorbing certainty when ``require_settlement_channel`` is true.
    """

    metric = str(temperature_metric or "").strip().lower()
    if metric not in {"high", "low"}:
        return None
    from src.config import runtime_cities_by_name
    from src.events.triggers.day0_extreme_updated import (
        _expected_station_for_city,
        _station_matches,
    )

    city_obj = runtime_cities_by_name().get(city)
    expected_station = _expected_station_for_city(city_obj)
    source_type = str(
        getattr(city_obj, "settlement_source_type", "") or ""
    ).strip().lower()
    expected_unit = str(
        getattr(city_obj, "settlement_unit", "") or ""
    ).strip().upper()
    decision_utc = decision_time.astimezone(timezone.utc)
    facts: list[dict[str, object]] = []
    if "observation_instants" in _table_names(conn):
        extreme_col = "running_min" if metric == "low" else "running_max"
        extreme_order = "ASC" if metric == "low" else "DESC"
        instant_columns = {
            str(column[1])
            for column in conn.execute("PRAGMA table_info(observation_instants)").fetchall()
        }

        def optional_column(name: str) -> str:
            return name if name in instant_columns else f"NULL AS {name}"

        availability_clause = (
            "AND imported_at <= ?"
            if "imported_at" in instant_columns
            else "AND 0 = 1"
        )
        time_geometry_clause = " ".join(
            clause
            for column, clause in (
                (
                    "is_ambiguous_local_hour",
                    "AND COALESCE(is_ambiguous_local_hour, 0) = 0",
                ),
                (
                    "is_missing_local_hour",
                    "AND COALESCE(is_missing_local_hour, 0) = 0",
                ),
            )
            if column in instant_columns
        )
        query_params: tuple[object, ...] = (
            city,
            target_date,
            decision_utc.isoformat(),
        )
        if "imported_at" in instant_columns:
            query_params += (decision_utc.isoformat(),)
        station_identity_clause = ""
        if expected_station and "station_id" in instant_columns:
            station_identity_clause = (
                "AND (UPPER(station_id) = ? OR UPPER(station_id) LIKE ?)"
            )
            query_params += (expected_station, f"{expected_station}:%")
        unit_identity_clause = ""
        if expected_unit:
            if "temp_unit" not in instant_columns:
                unit_identity_clause = "AND 0 = 1"
            else:
                unit_identity_clause = "AND UPPER(temp_unit) = ?"
                query_params += (expected_unit,)
        source_identity_clause = {
            "wu_icao": "LOWER(COALESCE(source, '')) = 'wu_icao_history'",
            "hko": "LOWER(COALESCE(source, '')) = 'hko_hourly_accumulator'",
        }.get(source_type, "0 = 1")
        if source_type == "noaa":
            if not expected_station:
                source_identity_clause = "0 = 1"
            else:
                source_identity_clause = "LOWER(COALESCE(source, '')) = ?"
                query_params += (f"ogimet_metar_{expected_station.lower()}",)
        row = conn.execute(
            f"""
            WITH authorized AS (
                SELECT CAST({extreme_col} AS REAL) AS observed_extreme_native,
                       utc_timestamp,
                       source,
                       {optional_column('station_id')},
                       {optional_column('temp_unit')},
                       {optional_column('imported_at')}
                  FROM observation_instants
                 WHERE city = ?
                   AND target_date = ?
                   AND substr(local_timestamp, 1, 10) = target_date
                   AND utc_timestamp <= ?
                   {availability_clause}
                   {time_geometry_clause}
                   {station_identity_clause}
                   {unit_identity_clause}
                   AND {source_identity_clause}
                   AND COALESCE(causality_status, 'OK') = 'OK'
                   AND (
                        (
                            UPPER(COALESCE(authority, '')) = 'VERIFIED'
                            AND COALESCE(source_role, '') = 'historical_hourly'
                            AND COALESCE(training_allowed, 0) = 1
                            AND (
                                LOWER(COALESCE(source, '')) LIKE 'wu%'
                                OR LOWER(COALESCE(source, '')) LIKE 'ogimet_metar_%'
                            )
                        )
                        OR (
                            city = 'Hong Kong'
                            AND LOWER(COALESCE(source, '')) = 'hko_hourly_accumulator'
                            AND UPPER(COALESCE(authority, '')) = 'ICAO_STATION_NATIVE'
                            AND COALESCE(source_role, '') = 'runtime_monitoring'
                            AND COALESCE(training_allowed, 0) = 0
                        )
                   )
                   AND {extreme_col} IS NOT NULL
            )
            SELECT observed_extreme_native,
                   (SELECT MAX(utc_timestamp) FROM authorized) AS observation_time,
                   (SELECT COUNT(*) FROM authorized) AS sample_count,
                   source AS observation_source,
                   station_id,
                   temp_unit,
                   (
                       SELECT MAX(COALESCE(imported_at, utc_timestamp))
                         FROM authorized
                   ) AS observation_available_at
              FROM authorized
             ORDER BY observed_extreme_native {extreme_order},
                      utc_timestamp DESC,
                      source DESC
             LIMIT 1
            """,
            query_params,
        ).fetchone()
        if row is not None and row["observation_time"] and row["observed_extreme_native"] is not None:
            facts.append(
                {
                    "observed_extreme_native": float(row["observed_extreme_native"]),
                    "observation_time": str(row["observation_time"]),
                    "sample_count": int(row["sample_count"] or 0),
                    "source": "durable_observation_instants",
                    "observation_source": str(row["observation_source"] or ""),
                    "station_id": str(row["station_id"] or ""),
                    "unit": str(row["temp_unit"] or "").strip().upper(),
                    "observation_available_at": str(
                        row["observation_available_at"] or row["observation_time"]
                    ),
                }
            )

    if "opportunity_events" in _table_names(conn):
        event_rows = conn.execute(
            """
            SELECT payload_json, available_at, received_at
              FROM opportunity_events
             WHERE event_type = 'DAY0_EXTREME_UPDATED'
               AND available_at <= ?
               AND received_at <= ?
               AND json_extract(payload_json, '$.city') = ?
               AND json_extract(payload_json, '$.target_date') = ?
               AND json_extract(payload_json, '$.metric') = ?
             ORDER BY datetime(json_extract(payload_json, '$.observation_time')) DESC,
                      available_at DESC,
                      created_at DESC,
                      event_id DESC
            """,
            (
                decision_utc.isoformat(),
                decision_utc.isoformat(),
                city,
                target_date,
                metric,
            ),
        )
        from src.contracts.settlement_semantics import SettlementSemantics
        from src.events.day0_authority import assert_live_day0_payload_authority

        for event_row in event_rows:
            try:
                if source_type not in {"wu_icao", "noaa", "hko"}:
                    continue
                payload = json.loads(str(event_row["payload_json"] or "{}"))
                if not isinstance(payload, Mapping):
                    continue
                assert_live_day0_payload_authority(payload)
                if expected_station and not _station_matches(
                    str(payload.get("station_id") or "").strip().upper(),
                    expected_station,
                ):
                    continue
                event_source = str(
                    payload.get("settlement_source") or ""
                ).strip().lower()
                settlement_channel_source = (
                    (
                        source_type == "wu_icao"
                        and event_source in {"wu_icao_history", "wu_api"}
                    )
                    or (
                        source_type == "noaa"
                        and event_source
                        == f"ogimet_metar_{expected_station.lower()}"
                    )
                    or (
                        source_type == "hko"
                        and event_source == "hko_hourly_accumulator"
                    )
                )
                if require_settlement_channel and not settlement_channel_source:
                    continue
                event_source_allowed = (
                    (
                        source_type in {"wu_icao", "noaa"}
                        and event_source == "aviationweather_metar"
                    )
                    or (
                        source_type == "wu_icao"
                        and event_source
                        in {
                            "wu_icao_history",
                            "wu_api",
                            "same_station_fast_tail",
                            "wu_api+same_station_fast_tail",
                        }
                    )
                    or (
                        source_type == "noaa"
                        and event_source
                        == f"ogimet_metar_{expected_station.lower()}"
                    )
                    or (
                        source_type == "hko"
                        and event_source == "hko_hourly_accumulator"
                    )
                )
                if not event_source_allowed:
                    continue
                observation_time = datetime.fromisoformat(
                    str(payload.get("observation_time") or "").replace("Z", "+00:00")
                )
                if observation_time.tzinfo is None:
                    continue
                observation_time = observation_time.astimezone(timezone.utc)
                if observation_time > decision_utc or city_obj is None:
                    continue
                observation_available_at = datetime.fromisoformat(
                    str(
                        payload.get("observation_available_at")
                        or event_row["available_at"]
                        or ""
                    ).replace("Z", "+00:00")
                )
                agent_received_at = datetime.fromisoformat(
                    str(event_row["received_at"] or "").replace("Z", "+00:00")
                )
                if (
                    observation_available_at.tzinfo is None
                    or agent_received_at.tzinfo is None
                ):
                    continue
                observation_available_at = observation_available_at.astimezone(
                    timezone.utc
                )
                agent_received_at = agent_received_at.astimezone(timezone.utc)
                if not (
                    observation_time
                    <= observation_available_at
                    <= agent_received_at
                    <= decision_utc
                ):
                    continue
                raw_value = float(payload.get("raw_value"))
                rounded_value = int(payload.get("rounded_value"))
                semantics = SettlementSemantics.for_city(city_obj)
                event_unit = str(
                    payload.get("settlement_unit")
                    or getattr(city_obj, "settlement_unit", "")
                    or ""
                ).strip().upper()
                if event_unit != expected_unit:
                    continue
                if int(semantics.round_single(raw_value)) != rounded_value:
                    continue
                extreme_raw = payload.get("low_so_far" if metric == "low" else "high_so_far")
                observed_extreme = float(raw_value if extreme_raw is None else extreme_raw)
            except (TypeError, ValueError):
                continue
            facts.append(
                {
                    "observed_extreme_native": observed_extreme,
                    "observation_time": observation_time.isoformat(),
                    "sample_count": 1,
                    "source": (
                        "durable_day0_event:"
                        f"{str(payload.get('settlement_source') or 'unknown')}"
                    ),
                    "observation_source": str(
                        payload.get("settlement_source") or ""
                    ),
                    "station_id": str(payload.get("station_id") or ""),
                    "unit": str(
                        event_unit
                    ),
                    "observation_available_at": str(
                        observation_available_at.isoformat()
                    ),
                }
            )
            break

    # LEDGER FACT (day0 defect-ledger, 2026-07-16): a third candidate, over
    # the append-only observation_prints publication stream — see
    # src/state/schema/observation_prints_schema.py. This is the zero-risk
    # migration path: it joins the SAME facts list as the two branches above
    # and goes through the SAME absorbing-direction reduction below — no new
    # reduction logic. observation_instants + the monotone-widening branch
    # (defect-2, commit f1d135901) are now LEGACY COMPENSATION for
    # derived-state-as-truth; once the ledger has full channel coverage and a
    # settled period of shadow-parity against the other two facts, the day0
    # belief can read the ledger alone and the widening branch retires — not
    # yet, this only adds a third fact.
    if "observation_prints" in _table_names(conn) and city_obj is not None:
        try:
            tz = ZoneInfo(str(getattr(city_obj, "timezone", "") or "UTC"))
            target_day = date.fromisoformat(str(target_date)[:10])
            local_day_start_utc = datetime.combine(
                target_day, datetime.min.time(), tzinfo=tz
            ).astimezone(timezone.utc)
            local_day_end_utc = local_day_start_utc + timedelta(days=1)
        except (ValueError, ZoneInfoNotFoundError):
            local_day_start_utc = None
            local_day_end_utc = None
        if local_day_start_utc is not None:
            # Channel authorization mirrors the opportunity_events branch
            # above: settlement-family channels only when
            # require_settlement_channel; physical (settlement + same-station
            # fast) channels otherwise. wu_api and ogimet_metar_* channels are
            # not yet written to the ledger (no writer for them) but are
            # listed here so a future writer needs no reader change.
            settlement_channels: set[str]
            physical_channels: set[str]
            if source_type == "wu_icao":
                settlement_channels = {"wu_icao_history"}
                physical_channels = {"wu_icao_history", "aviationweather_metar", "wu_api"}
            elif source_type == "hko":
                settlement_channels = set()
                physical_channels = {"hko_rhrread_spot"}
            elif source_type == "noaa" and expected_station:
                ogimet_channel = f"ogimet_metar_{expected_station.lower()}"
                settlement_channels = {ogimet_channel}
                physical_channels = {ogimet_channel}
            else:
                settlement_channels = set()
                physical_channels = set()
            allowed_channels = (
                settlement_channels if require_settlement_channel else physical_channels
            )
            if allowed_channels:
                placeholders = ",".join("?" for _ in allowed_channels)
                print_rows = conn.execute(
                    f"""
                    SELECT source_channel, publish_ts_utc, value_native, unit, station_id, raw_report
                      FROM observation_prints
                     WHERE city = ?
                       AND source_channel IN ({placeholders})
                       AND publish_ts_utc >= ?
                       AND publish_ts_utc < ?
                       AND publish_ts_utc <= ?
                    """,
                    (
                        city,
                        *sorted(allowed_channels),
                        local_day_start_utc.isoformat(),
                        local_day_end_utc.isoformat(),
                        decision_utc.isoformat(),
                    ),
                ).fetchall()
                margin_by_channel: dict[str, float | None] = {}
                best_value: float | None = None
                best_channel = ""
                best_publish_ts = ""
                for print_row in print_rows:
                    channel = str(print_row["source_channel"])
                    print_unit = str(print_row["unit"] or "").strip().upper()
                    if expected_station and not _station_matches(
                        str(print_row["station_id"] or "").strip().upper(),
                        expected_station,
                    ):
                        continue
                    value = float(print_row["value_native"])
                    if channel == "aviationweather_metar":
                        # Always stored raw Celsius on the wire (day0_fast_obs
                        # writer) — apply the SAME unit law
                        # settlement_temp_for_report does (F-settled cities
                        # only trust a report carrying a T-group; a whole-C
                        # ->F conversion is imprecise enough to falsely cross
                        # a bin edge) instead of the generic unit-match check.
                        from src.data.day0_fast_obs import _T_GROUP_RE

                        if expected_unit == "F":
                            if not _T_GROUP_RE.search(str(print_row["raw_report"] or "")):
                                continue
                            value = value * 9.0 / 5.0 + 32.0
                        elif expected_unit != "C":
                            continue
                        # Same-station fast channel (not the settlement
                        # channel itself) enters margin-adjusted, toward the
                        # absorbing direction — reuses the SAME lookup as the
                        # emission layer (day0_fast_obs) and the exit lane
                        # (day0_hard_fact_exit) — no second margin mechanism.
                        if channel not in margin_by_channel:
                            from src.data.day0_oracle_anomaly import (
                                metar_margin_units_for_city,
                            )

                            margin_by_channel[channel] = metar_margin_units_for_city(
                                city, expected_unit
                            )
                        margin = margin_by_channel[channel]
                        if margin is None:
                            continue  # not enough evidence to trust even margin-adjusted
                        value = value - margin if metric == "high" else value + margin
                    elif print_unit != expected_unit:
                        continue
                    publish_ts = str(print_row["publish_ts_utc"])
                    if best_value is None or (
                        (metric == "high" and value > best_value)
                        or (metric == "low" and value < best_value)
                    ):
                        best_value = value
                        best_channel = channel
                        best_publish_ts = publish_ts
                if best_value is not None:
                    facts.append(
                        {
                            "observed_extreme_native": best_value,
                            "observation_time": best_publish_ts,
                            "sample_count": len(print_rows),
                            "source": f"observation_prints:{best_channel}",
                            "observation_source": best_channel,
                            "station_id": expected_station or "",
                            "unit": expected_unit,
                            "observation_available_at": best_publish_ts,
                        }
                    )

    def fact_time(fact: Mapping[str, object]) -> datetime:
        parsed = datetime.fromisoformat(
            str(fact.get("observation_time") or "").replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    if not facts:
        return None
    # ABSORBING-DIRECTION REDUCTION, not "most recent wins" (2026-07-14 Paris
    # regression): the day-so-far extreme is the max (high) / min (low) across
    # every authorized source seen so far. Picking the temporally freshest
    # candidate let a newly-eligible source whose OWN history never covered
    # the earlier peak (e.g. a fast lane's first observation after
    # wu_icao_history had already recorded 34.0) report a lower value as if it
    # were current — a running max cannot decrease. Time only breaks ties
    # between facts that agree on the extreme value.
    def fact_extreme(fact: Mapping[str, object]) -> float:
        return float(fact["observed_extreme_native"])

    best_extreme = (min if metric == "low" else max)(fact_extreme(fact) for fact in facts)
    candidates = [fact for fact in facts if fact_extreme(fact) == best_extreme]
    return max(candidates, key=fact_time)


def _day0_observation_lag_reason(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    decision_time: datetime,
    posterior_provenance_json: object,
) -> str | None:
    try:
        provenance = json.loads(str(posterior_provenance_json or "{}"))
    except (TypeError, ValueError):
        provenance = {}
    conditioning = (
        provenance.get("day0_conditioning")
        if isinstance(provenance, dict)
        else None
    )
    served_raw = (
        conditioning.get("observation_time")
        if isinstance(conditioning, Mapping)
        else None
    )
    try:
        served_at = datetime.fromisoformat(str(served_raw or "").replace("Z", "+00:00"))
    except ValueError:
        served_at = None
    if served_at is not None:
        if served_at.tzinfo is None:
            served_at = served_at.replace(tzinfo=timezone.utc)
        served_at = served_at.astimezone(timezone.utc)
    fact = _latest_authorized_day0_fact(
        conn,
        city=city,
        target_date=target_date,
        temperature_metric=temperature_metric,
        decision_time=decision_time,
        require_settlement_channel=True,
    )
    if fact is None:
        return None
    try:
        latest_at = datetime.fromisoformat(
            str(fact["observation_time"]).replace("Z", "+00:00")
        )
    except (KeyError, ValueError):
        return None
    if latest_at.tzinfo is None:
        latest_at = latest_at.replace(tzinfo=timezone.utc)
    latest_at = latest_at.astimezone(timezone.utc)
    if served_at is not None and latest_at <= served_at:
        return None
    return (
        "basis=day0_observation_hwm_lag:"
        f"latest_observation_time={latest_at.isoformat()}:"
        f"posterior_observation_time={served_at.isoformat() if served_at else 'missing'}"
    )


def _latest_readiness_bound_posterior_id(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    columns: set[str] | None = None,
    binding_supported: bool | None = None,
) -> int | None:
    """Return the posterior bound by the exact readiness the live reader serves.

    ``None`` means the DB predates soft-anchor posterior binding and retains the
    legacy fixture contract. ``-1`` means the binding contract exists but the
    current scope cannot prove one, so coverage must fail closed.
    """

    columns = columns if columns is not None else _columns(conn, "readiness_state")
    if "dependency_json" not in columns:
        return None
    if binding_supported is None:
        binding_supported = conn.execute(
            """
            SELECT 1
              FROM readiness_state r,
                   json_each(r.dependency_json, '$.dependencies')
             WHERE json_extract(value, '$.role') = 'soft_anchor_posterior'
             LIMIT 1
            """
        ).fetchone() is not None
    if not binding_supported:
        return None
    predicates = [
        "strategy_key = ?",
        "json_extract(provenance_json, '$.city') = ?",
        "json_extract(provenance_json, '$.target_date') = ?",
        "json_extract(provenance_json, '$.temperature_metric') = ?",
    ]
    params: list[object] = [SOURCE_ID, city, target_date, temperature_metric]
    order = "datetime(computed_at) DESC, readiness_id DESC" if "computed_at" in columns else "rowid DESC"
    selected = "dependency_json" + (", status" if "status" in columns else "")
    row = conn.execute(
        f"""
        SELECT {selected}
          FROM readiness_state
         WHERE {' AND '.join(predicates)}
         ORDER BY {order}
         LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    if row is None or ("status" in columns and str(row["status"] or "") != "READY"):
        return -1
    try:
        payload = json.loads(str(row["dependency_json"] or "{}"))
    except (TypeError, ValueError):
        return -1
    dependencies = payload.get("dependencies") if isinstance(payload, Mapping) else None
    if not isinstance(dependencies, list):
        return -1
    matches = [
        item
        for item in dependencies
        if isinstance(item, Mapping)
        and item.get("role") == "soft_anchor_posterior"
    ]
    if len(matches) != 1:
        return -1
    try:
        posterior_id = int(matches[0].get("posterior_id"))
    except (TypeError, ValueError):
        return -1
    return posterior_id if posterior_id > 0 else -1


def _covering_posterior_input_lag_reason(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    decision_time: datetime,
    baseline_source_run_id: str | None,
    openmeteo_source_run_id: str | None,
    posterior_tradeable_grade_clause: str,
    check_day0_observation: bool = False,
    observation_conn: sqlite3.Connection | None = None,
    posterior_columns: set[str] | None = None,
    readiness_columns: set[str] | None = None,
    readiness_binding_supported: bool | None = None,
) -> str | None:
    """Use the live read gate's HWM rule to invalidate stale plan coverage."""

    columns = (
        posterior_columns
        if posterior_columns is not None
        else _columns(conn, "forecast_posteriors")
    )
    required = {
        "city",
        "target_date",
        "temperature_metric",
        "source_id",
        "source_cycle_time",
        "computed_at",
        "provenance_json",
    }
    if not required.issubset(columns):
        return None
    predicates = [
        "p.source_id = ?",
        "p.city = ?",
        "p.target_date = ?",
        "p.temperature_metric = ?",
        "p.training_allowed = 0",
        "p.runtime_layer = 'live'",
    ]
    params: list[object] = [SOURCE_ID, city, target_date, temperature_metric]
    readiness_posterior_id = _latest_readiness_bound_posterior_id(
        conn,
        city=city,
        target_date=target_date,
        temperature_metric=temperature_metric,
        columns=readiness_columns,
        binding_supported=readiness_binding_supported,
    )
    if readiness_posterior_id == -1:
        return "basis=readiness_posterior_identity_missing"
    if readiness_posterior_id is not None:
        predicates.append("p.posterior_id = ?")
        params.append(readiness_posterior_id)
    if "dependency_source_run_ids_json" in columns:
        if baseline_source_run_id:
            predicates.append(
                "json_extract(p.dependency_source_run_ids_json, '$.baseline_b0') = ?"
            )
            params.append(baseline_source_run_id)
        if openmeteo_source_run_id:
            predicates.append(
                "json_extract(p.dependency_source_run_ids_json, '$.openmeteo_ifs9_anchor') = ?"
            )
            params.append(openmeteo_source_run_id)
    row = conn.execute(
        f"""
        SELECT p.source_cycle_time, p.computed_at, p.provenance_json
          FROM forecast_posteriors p
         WHERE {' AND '.join(predicates)}
           {posterior_tradeable_grade_clause}
         ORDER BY datetime(p.computed_at) DESC, p.posterior_id DESC
         LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    if row is None:
        return (
            "basis=readiness_bound_posterior_unavailable"
            if readiness_posterior_id is not None
            else None
        )
    from src.data.replacement_input_hwm import replacement_live_input_lag_reason

    raw_lag = replacement_live_input_lag_reason(
        conn,
        city=city,
        target_date=target_date,
        metric=temperature_metric,
        decision_time=decision_time,
        posterior_source_cycle_time=row["source_cycle_time"],
        posterior_computed_at=row["computed_at"],
        posterior_provenance=_json_object(row["provenance_json"]),
    )
    if raw_lag is not None or not check_day0_observation:
        return raw_lag
    return _day0_observation_lag_reason(
        observation_conn or conn,
        city=city,
        target_date=target_date,
        temperature_metric=temperature_metric,
        decision_time=decision_time,
        posterior_provenance_json=row["provenance_json"],
    )


def _blocked_plan(reason_code: str) -> ReplacementForecastCurrentTargetPlan:
    return ReplacementForecastCurrentTargetPlan(
        status="BLOCKED",
        reason_codes=(reason_code,),
        target_count=0,
        covered_count=0,
        missing_coverage_count=0,
        can_seed_count=0,
        missing_openmeteo_manifest_count=0,
        missing_fusion_current_values_count=0,
        day0_observed_extreme_required_count=0,
        rows=(),
    )


def _status_from_counts(
    *,
    target_count: int,
    missing_coverage_count: int,
    can_seed_count: int,
    missing_openmeteo_manifest_count: int,
    missing_fusion_current_values_count: int,
    day0_observed_extreme_required_count: int,
) -> tuple[str, tuple[str, ...]]:
    if target_count <= 0:
        return "NO_CURRENT_TARGETS", ("REPLACEMENT_CURRENT_TARGET_PLAN_NO_CURRENT_TARGETS",)
    if missing_coverage_count <= 0:
        return "CURRENT_TARGETS_COVERED", ("REPLACEMENT_CURRENT_TARGET_PLAN_COVERED",)
    reasons: list[str] = ["REPLACEMENT_CURRENT_TARGET_PLAN_MISSING_REPLACEMENT_COVERAGE"]
    if can_seed_count:
        reasons.append("REPLACEMENT_CURRENT_TARGET_PLAN_HAS_SEEDABLE_TARGETS")
    if missing_openmeteo_manifest_count:
        reasons.append("REPLACEMENT_CURRENT_TARGET_PLAN_MISSING_OPENMETEO_MANIFESTS")
    if missing_fusion_current_values_count:
        reasons.append("REPLACEMENT_CURRENT_TARGET_PLAN_MISSING_FUSION_CURRENT_VALUES")
    if day0_observed_extreme_required_count:
        reasons.append("REPLACEMENT_CURRENT_TARGET_PLAN_DAY0_OBSERVED_EXTREME_REQUIRED")
    if (
        can_seed_count <= 0
        and missing_openmeteo_manifest_count <= 0
        and missing_fusion_current_values_count <= 0
        and day0_observed_extreme_required_count >= missing_coverage_count
    ):
        return (
            "CURRENT_TARGETS_REQUIRE_DAY0_OBSERVED_EXTREME",
            ("REPLACEMENT_CURRENT_TARGET_PLAN_DAY0_OBSERVED_EXTREME_REQUIRED",),
        )
    return "CURRENT_TARGETS_MISSING_REPLACEMENT_COVERAGE", tuple(reasons)


def _city_timezone_by_name() -> dict[str, str]:
    path = Path(__file__).resolve().parents[2] / "config" / "cities.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    cities = payload.get("cities") if isinstance(payload, Mapping) else None
    if not isinstance(cities, list):
        return {}
    out: dict[str, str] = {}
    for row in cities:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("name") or "").strip()
        timezone_name = str(row.get("timezone") or "").strip()
        if name and timezone_name:
            out[name] = timezone_name
    return out


def _city_timezone_by_name_from_source_run_coverage(conn: sqlite3.Connection) -> dict[str, str]:
    if "source_run_coverage" not in _table_names(conn):
        return {}
    if not {"city", "city_timezone"}.issubset(_columns(conn, "source_run_coverage")):
        return {}
    out: dict[str, str] = {}
    for row in conn.execute(
        """
        SELECT city, city_timezone, max(recorded_at) AS recorded_at
        FROM source_run_coverage
        WHERE city IS NOT NULL
          AND city != ''
          AND city_timezone IS NOT NULL
          AND city_timezone != ''
        GROUP BY city, city_timezone
        ORDER BY recorded_at DESC
        """
    ).fetchall():
        city = str(row["city"]).strip()
        timezone_name = str(row["city_timezone"]).strip()
        if city and timezone_name and city not in out:
            out[city] = timezone_name
    return out


def _day0_observed_extreme_required(
    *,
    city: str,
    target_date: str,
    timezone_by_city: Mapping[str, str],
    now_utc: datetime,
) -> bool:
    timezone_name = timezone_by_city.get(city)
    if not timezone_name:
        return False
    try:
        return has_city_local_day_started(target_date, timezone_name, now_utc)
    except (ValueError, ZoneInfoNotFoundError):
        return False


def replacement_forecast_current_target_keys(
    forecast_db: Path | str,
    *,
    min_target_date: date | str | None = None,
) -> tuple[ReplacementForecastTargetKey, ...]:
    """Return only current market scope identities needed by raw capture.

    Source-clock capture must not pay for manifest, posterior, readiness, and
    input-HWM validation before it can start fetching a newly published run.
    This keeps the target universe identical to the full plan while leaving
    all coverage proof with ``build_replacement_forecast_current_target_plan``.
    """

    db_path = Path(forecast_db)
    if not db_path.exists():
        return ()
    minimum_target_date = (
        min_target_date.isoformat()
        if isinstance(min_target_date, date)
        else str(min_target_date or datetime.now(tz=timezone.utc).date().isoformat())
    )
    conn = _connect(db_path, write_class="live")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        tables = _table_names(conn)
        if "market_events" not in tables:
            return ()
        required_market_columns = {
            "city",
            "target_date",
            "temperature_metric",
            "token_id",
            "range_label",
        }
        if not required_market_columns.issubset(_columns(conn, "market_events")):
            return ()
        source_run_targets = _supports_source_run_targets(conn)
        if "source_run_coverage" in tables and not source_run_targets:
            return ()
        if source_run_targets:
            expected_high = expected_replacement_dependency_identity_by_role("high")[
                "baseline_b0"
            ]
            expected_low = expected_replacement_dependency_identity_by_role("low")[
                "baseline_b0"
            ]
            rows = conn.execute(
                """
                SELECT DISTINCT
                    c.city,
                    c.target_local_date AS target_date,
                    c.temperature_metric
                FROM source_run_coverage c
                WHERE c.source_id = ?
                  AND c.target_local_date >= ?
                  AND (
                      (c.temperature_metric = 'high' AND c.data_version = ?)
                      OR (c.temperature_metric = 'low' AND c.data_version = ?)
                  )
                  AND EXISTS (
                      SELECT 1
                      FROM market_events m
                      WHERE m.city = c.city
                        AND m.target_date = c.target_local_date
                        AND m.temperature_metric = c.temperature_metric
                        AND m.token_id IS NOT NULL
                        AND m.token_id != ''
                        AND m.range_label IS NOT NULL
                        AND m.range_label != ''
                  )
                ORDER BY target_date, c.city, c.temperature_metric
                """,
                (
                    expected_high.source_id,
                    minimum_target_date,
                    expected_high.data_version,
                    expected_low.data_version,
                ),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT DISTINCT city, target_date, temperature_metric
                FROM market_events
                WHERE token_id IS NOT NULL
                  AND token_id != ''
                  AND range_label IS NOT NULL
                  AND range_label != ''
                  AND target_date >= ?
                ORDER BY target_date, city, temperature_metric
                """,
                (minimum_target_date,),
            ).fetchall()
        return tuple(
            ReplacementForecastTargetKey(
                city=str(row["city"]),
                target_date=str(row["target_date"]),
                temperature_metric=str(row["temperature_metric"]),
            )
            for row in rows
        )
    finally:
        conn.close()


def build_replacement_forecast_current_target_plan(
    forecast_db: Path | str,
    *,
    limit: int | None = None,
    min_target_date: date | str | None = None,
    require_raw_artifacts: bool = True,
    now_utc: datetime | None = None,
    required_openmeteo_source_cycle_time: datetime | str | None = None,
    observation_conn: sqlite3.Connection | None = None,
) -> ReplacementForecastCurrentTargetPlan:
    """Return current market targets and the replacement artifacts needed for them."""

    db_path = Path(forecast_db)
    # Use now_utc as the reference clock when min_target_date is not explicit — avoids
    # wall-clock drift against fixtures or callers that pass a fixed now_utc.
    _ref_clock = (now_utc or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    minimum_target_date = (
        min_target_date.isoformat()
        if isinstance(min_target_date, date)
        else str(min_target_date or _ref_clock.date().isoformat())
    )
    required_openmeteo_cycle_iso: str | None = None
    if isinstance(required_openmeteo_source_cycle_time, datetime):
        required_openmeteo_cycle_iso = (
            required_openmeteo_source_cycle_time.astimezone(timezone.utc).isoformat()
        )
    elif required_openmeteo_source_cycle_time is not None:
        required_openmeteo_cycle_iso = str(required_openmeteo_source_cycle_time)
    if not db_path.exists():
        return ReplacementForecastCurrentTargetPlan(
            status="BLOCKED",
            reason_codes=("REPLACEMENT_CURRENT_TARGET_PLAN_FORECAST_DB_MISSING",),
            target_count=0,
            covered_count=0,
            missing_coverage_count=0,
            can_seed_count=0,
            missing_openmeteo_manifest_count=0,
            missing_fusion_current_values_count=0,
            day0_observed_extreme_required_count=0,
            rows=(),
        )
    conn = _connect(db_path, write_class="live")
    conn.row_factory = sqlite3.Row
    release_input_hwm = None
    owned_observation_conn: sqlite3.Connection | None = None
    if observation_conn is None:
        try:
            from src.state.db import (
                ZEUS_FORECASTS_DB_PATH,
                get_world_connection_read_only,
            )

            if db_path.resolve() == Path(ZEUS_FORECASTS_DB_PATH).resolve():
                owned_observation_conn = get_world_connection_read_only()
                owned_observation_conn.row_factory = sqlite3.Row
                observation_conn = owned_observation_conn
        except Exception:
            observation_conn = None
    try:
        conn.execute("PRAGMA query_only=ON")
        tables = _table_names(conn)
        required = {"market_events", "forecast_posteriors", "readiness_state"}
        if require_raw_artifacts:
            required.add("raw_forecast_artifacts")
        if not required.issubset(tables):
            return _blocked_plan("REPLACEMENT_CURRENT_TARGET_PLAN_REQUIRED_TABLE_MISSING")
        try:
            market_event_count = int(conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0])
        except sqlite3.Error:
            market_event_count = -1
        if market_event_count == 0:
            return ReplacementForecastCurrentTargetPlan(
                status="NO_CURRENT_TARGETS",
                reason_codes=("REPLACEMENT_CURRENT_TARGET_PLAN_NO_CURRENT_TARGETS",),
                target_count=0,
                covered_count=0,
                missing_coverage_count=0,
                can_seed_count=0,
                missing_openmeteo_manifest_count=0,
                missing_fusion_current_values_count=0,
                day0_observed_extreme_required_count=0,
                rows=(),
            )
        if not {"city", "target_date", "temperature_metric", "token_id", "range_label"}.issubset(_columns(conn, "market_events")):
            return _blocked_plan("REPLACEMENT_CURRENT_TARGET_PLAN_MARKET_EVENTS_SCHEMA_MISSING")
        posterior_columns = _columns(conn, "forecast_posteriors")
        if not {"city", "target_date", "temperature_metric", "source_id", "data_version"}.issubset(posterior_columns):
            return _blocked_plan("REPLACEMENT_CURRENT_TARGET_PLAN_POSTERIOR_SCHEMA_MISSING")
        readiness_columns = _columns(conn, "readiness_state")
        readiness_binding_supported = (
            conn.execute(
                """
                SELECT 1
                  FROM readiness_state r,
                       json_each(r.dependency_json, '$.dependencies')
                 WHERE json_extract(value, '$.role') = 'soft_anchor_posterior'
                 LIMIT 1
                """
            ).fetchone()
            is not None
            if "dependency_json" in readiness_columns
            else False
        )
        raw_artifact_columns: set[str] = set()
        metadata_column = None
        if "raw_forecast_artifacts" in tables:
            raw_artifact_columns = _columns(conn, "raw_forecast_artifacts")
            metadata_column = _raw_artifact_metadata_column(raw_artifact_columns)
            if require_raw_artifacts and (
                metadata_column is None
                or not {"source_id", "data_version", "artifact_path"}.issubset(raw_artifact_columns)
            ):
                raise ValueError("raw_forecast_artifacts schema lacks manifest metadata columns")
        source_run_targets = _supports_source_run_targets(conn)
        if "source_run_coverage" in tables and not source_run_targets:
            return _blocked_plan("REPLACEMENT_CURRENT_TARGET_PLAN_SOURCE_RUN_DEPENDENCY_SCHEMA_MISSING")
        posterior_source_run_clause = ""
        readiness_source_run_clause = ""
        readiness_status_clause = ""
        # TRADEABLE-GRADE COVERAGE (2026-06-11, second site of the 2026-06-10 K-decision;
        # basis-predicate fix 2026-06-12): a covering posterior must be CERTIFIED-bootstrap
        # tradeable-grade. The mask-and-starve antibody guards against a capture-missing
        # materialization marking its scope covered at PLAN level and blocking its own fusion repair
        # (observed 2026-06-11: Atlanta/Austin/Beijing 00Z rows self-masked one tick after
        # materializing). The original proxy `p.q_lcb_json IS NOT NULL` broke once the soft-anchor
        # older non-certified paths began carrying q_lcb instead of NULL, so the
        # predicate now keys on the certified bootstrap basis (single authority:
        # cycle_policy). Schema-conditional like the queue clause.
        posterior_tradeable_grade_clause = tradeable_grade_coverage_sql(
            posterior_columns=posterior_columns, alias="p."
        )
        if source_run_targets and "dependency_source_run_ids_json" not in posterior_columns:
            return _blocked_plan("REPLACEMENT_CURRENT_TARGET_PLAN_SOURCE_RUN_DEPENDENCY_SCHEMA_MISSING")
        if source_run_targets and "dependency_json" not in readiness_columns:
            return _blocked_plan("REPLACEMENT_CURRENT_TARGET_PLAN_SOURCE_RUN_DEPENDENCY_SCHEMA_MISSING")
        if source_run_targets:
            posterior_source_run_clause = """
                  AND json_extract(p.dependency_source_run_ids_json, '$.baseline_b0') = targets.baseline_source_run_id
            """
            readiness_source_run_clause = """
                  AND EXISTS (
                      SELECT 1
                      FROM json_each(r.dependency_json, '$.dependencies')
                      WHERE json_extract(value, '$.role') = 'baseline_b0'
                        AND json_extract(value, '$.source_run_id') = targets.baseline_source_run_id
                  )
            """
        if "status" in readiness_columns:
            # Expired readiness must NOT count as coverage (else a city stays "covered"
            # forever after its first posterior and the downloader never re-fetches its
            # raw inputs once the 3h TTL lapses — the stale-after-first-cycle bug). Only
            # a row whose expires_at is still in the future counts as live coverage.
            readiness_status_clause = """
                          AND r.status = 'READY'
                          AND (r.expires_at IS NULL OR r.expires_at > strftime('%Y-%m-%dT%H:%M:%S', 'now'))
            """
        sql_limit = "" if limit is None else f" LIMIT {int(limit)}"
        if source_run_targets:
            expected_high = expected_replacement_dependency_identity_by_role("high")["baseline_b0"]
            expected_low = expected_replacement_dependency_identity_by_role("low")["baseline_b0"]
            if metadata_column is not None:
                coverage_select = """
                    0 AS posterior_count,
                    0 AS readiness_count
                """
                coverage_params: tuple[object, ...] = ()
            else:
                coverage_select = f"""
                    (
                        SELECT COUNT(*)
                        FROM forecast_posteriors p
                        WHERE p.source_id = ?
                          AND p.training_allowed = 0
                          AND p.runtime_layer = 'live'
                          AND p.city = targets.city
                          AND p.target_date = targets.target_date
                          AND p.temperature_metric = targets.temperature_metric
                          {posterior_tradeable_grade_clause}
                          {posterior_source_run_clause}
                    ) AS posterior_count,
                    (
                        SELECT COUNT(*)
                        FROM readiness_state r
                        WHERE r.strategy_key = ?
                          AND json_extract(r.provenance_json, '$.city') = targets.city
                          AND json_extract(r.provenance_json, '$.target_date') = targets.target_date
                          AND json_extract(r.provenance_json, '$.temperature_metric') = targets.temperature_metric
                          {readiness_status_clause}
                          {readiness_source_run_clause}
                    ) AS readiness_count
                """
                coverage_params = (SOURCE_ID, SOURCE_ID)
            rows = conn.execute(
                f"""
                WITH ranked_coverage AS (
                    SELECT
                        c.city,
                        c.target_local_date AS target_date,
                        c.temperature_metric,
                        c.source_run_id AS baseline_source_run_id,
                        sr.source_cycle_time AS baseline_source_cycle_time,
                        c.computed_at,
                        c.recorded_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY c.city, c.target_local_date, c.temperature_metric
                            ORDER BY
                                CASE WHEN c.completeness_status = 'COMPLETE' THEN 0 ELSE 1 END,
                                CASE WHEN c.readiness_status = 'LIVE_ELIGIBLE' THEN 0 ELSE 1 END,
                                c.computed_at DESC,
                                c.recorded_at DESC
                        ) AS rn
                    FROM source_run_coverage c
                    LEFT JOIN source_run sr ON sr.source_run_id = c.source_run_id
                    WHERE c.source_id = ?
                      AND c.target_local_date >= ?
                      AND (
                          (c.temperature_metric = 'high' AND c.data_version = ?)
                          OR (c.temperature_metric = 'low' AND c.data_version = ?)
                      )
                      AND EXISTS (
                          SELECT 1
                          FROM market_events m
                          WHERE m.city = c.city
                            AND m.target_date = c.target_local_date
                            AND m.temperature_metric = c.temperature_metric
                            AND m.token_id IS NOT NULL
                            AND m.token_id != ''
                            AND m.range_label IS NOT NULL
                            AND m.range_label != ''
                      )
                ),
                targets AS (
                    SELECT
                        rc.city,
                        rc.target_date,
                        rc.temperature_metric,
                        rc.baseline_source_run_id,
                        rc.baseline_source_cycle_time,
                        (
                            SELECT COUNT(*)
                            FROM market_events m
                            WHERE m.city = rc.city
                              AND m.target_date = rc.target_date
                              AND m.temperature_metric = rc.temperature_metric
                              AND m.token_id IS NOT NULL
                              AND m.token_id != ''
                              AND m.range_label IS NOT NULL
                              AND m.range_label != ''
                        ) AS market_bin_count
                    FROM ranked_coverage rc
                    WHERE rc.rn = 1
                )
                SELECT
                    targets.city,
                    targets.target_date,
                    targets.temperature_metric,
                    targets.baseline_source_run_id,
                    targets.baseline_source_cycle_time,
                    targets.market_bin_count,
                    {coverage_select}
                FROM targets
                ORDER BY targets.target_date, targets.city, targets.temperature_metric
                {sql_limit}
                """,
                (
                    expected_high.source_id,
                    minimum_target_date,
                    expected_high.data_version,
                    expected_low.data_version,
                    *coverage_params,
                ),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                WITH targets AS (
                    SELECT city, target_date, temperature_metric, COUNT(*) AS market_bin_count
                    FROM market_events
                    WHERE token_id IS NOT NULL
                      AND token_id != ''
                      AND range_label IS NOT NULL
                      AND range_label != ''
                      AND target_date >= ?
                    GROUP BY city, target_date, temperature_metric
                ),
                posteriors AS (
                    SELECT city, target_date, temperature_metric, COUNT(*) AS posterior_count
                    FROM forecast_posteriors
                    WHERE source_id = ?
                      AND training_allowed = 0
                      AND runtime_layer = 'live'
                      {posterior_tradeable_grade_clause.replace("p.q_lcb_json", "q_lcb_json")}
                    GROUP BY city, target_date, temperature_metric
                ),
                readiness AS (
                    SELECT
                        json_extract(provenance_json, '$.city') AS city,
                        json_extract(provenance_json, '$.target_date') AS target_date,
                        json_extract(provenance_json, '$.temperature_metric') AS temperature_metric,
                        COUNT(*) AS readiness_count
                    FROM readiness_state
                    WHERE strategy_key = ?
                      {readiness_status_clause.replace("r.", "")}
                    GROUP BY 1, 2, 3
                )
                SELECT
                    targets.city,
                    targets.target_date,
                    targets.temperature_metric,
                    NULL AS baseline_source_run_id,
                    NULL AS baseline_source_cycle_time,
                    targets.market_bin_count,
                    COALESCE(posteriors.posterior_count, 0) AS posterior_count,
                    COALESCE(readiness.readiness_count, 0) AS readiness_count
                FROM targets
                LEFT JOIN posteriors USING (city, target_date, temperature_metric)
                LEFT JOIN readiness USING (city, target_date, temperature_metric)
                ORDER BY targets.target_date, targets.city, targets.temperature_metric
                {sql_limit}
                """,
                (minimum_target_date, SOURCE_ID, SOURCE_ID),
            ).fetchall()
        out: list[ReplacementForecastCurrentTargetPlanRow] = []
        timezone_by_city = {
            **_city_timezone_by_name(),
            **_city_timezone_by_name_from_source_run_coverage(conn),
        }
        expected_by_metric = {
            metric: expected_replacement_dependency_identity_by_role(metric)
            for metric in {str(row["temperature_metric"]) for row in rows}
        }
        required_manifest_cycle = (
            str(required_openmeteo_cycle_iso or "").strip() or None
        )
        baseline_cycles = [
            str(row["baseline_source_cycle_time"])
            for row in rows
            if row["baseline_source_cycle_time"]
        ]
        manifest_cycle_floor = required_manifest_cycle
        if (
            manifest_cycle_floor is None
            and baseline_cycles
            and len(baseline_cycles) == len(rows)
        ):
            manifest_cycle_floor = min(baseline_cycles)
        openmeteo_manifest_index = _load_openmeteo_manifest_index(
            conn,
            raw_artifact_columns=raw_artifact_columns,
            metadata_column=metadata_column,
            identities={
                (
                    expected["openmeteo_ifs9_anchor"].source_id,
                    expected["openmeteo_ifs9_anchor"].product_id,
                    expected["openmeteo_ifs9_anchor"].data_version,
                )
                for expected in expected_by_metric.values()
            },
            cities={str(row["city"]) for row in rows},
            minimum_source_cycle_time=manifest_cycle_floor,
        )
        payload_coverage_cache: dict[tuple[str, str, str], bool] = {}
        evaluation_now_utc = (now_utc or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
        if not conn.in_transaction:
            conn.execute("BEGIN")
        release_input_hwm = prime_frozen_replacement_artifact_hwm(
            conn,
            requests={
                (
                    str(row["city"]),
                    str(row["target_date"]),
                    str(row["temperature_metric"]),
                )
                for row in rows
            },
            decision_time=evaluation_now_utc,
        )
        manifest_coverage_by_scope: dict[
            tuple[str, str, str],
            tuple[int, str | None, str | None],
        ] = {}
        for row in rows:
            city = str(row["city"])
            target_date = str(row["target_date"])
            metric = str(row["temperature_metric"])
            expected = expected_by_metric[metric]["openmeteo_ifs9_anchor"]
            if metadata_column is not None:
                coverage = _openmeteo_manifest_coverage(
                    openmeteo_manifest_index.get(
                        (expected.source_id, expected.data_version, city),
                        (),
                    ),
                    target_date=target_date,
                    city_timezone=timezone_by_city.get(city),
                    required_source_cycle_time=required_manifest_cycle,
                    minimum_source_cycle_time=(
                        None
                        if required_manifest_cycle
                        else row["baseline_source_cycle_time"]
                    ),
                    payload_coverage_cache=payload_coverage_cache,
                )
            else:
                coverage = (1, None, None) if not require_raw_artifacts else (0, None, None)
            manifest_coverage_by_scope[(city, target_date, metric)] = coverage
        dependency_requests = {
            (
                str(row["city"]),
                str(row["target_date"]),
                str(row["temperature_metric"]),
                str(row["baseline_source_run_id"]),
                str(
                    manifest_coverage_by_scope[
                        (
                            str(row["city"]),
                            str(row["target_date"]),
                            str(row["temperature_metric"]),
                        )
                    ][1]
                ),
            )
            for row in rows
            if source_run_targets
            and row["baseline_source_run_id"]
            and manifest_coverage_by_scope[
                (
                    str(row["city"]),
                    str(row["target_date"]),
                    str(row["temperature_metric"]),
                )
            ][1]
        }
        dependency_coverage = _replacement_coverage_counts_for_dependencies(
            conn,
            requests=dependency_requests,
            posterior_tradeable_grade_clause=posterior_tradeable_grade_clause,
            readiness_status_clause=readiness_status_clause,
        )
        for row in rows:
            metric = str(row["temperature_metric"])
            city = str(row["city"])
            target_date = str(row["target_date"])
            baseline_source_run_id = row["baseline_source_run_id"]
            baseline_source_cycle_time = row["baseline_source_cycle_time"]
            day0_observed_extreme_required = _day0_observed_extreme_required(
                city=city,
                target_date=target_date,
                timezone_by_city=timezone_by_city,
                now_utc=evaluation_now_utc,
            )
            openmeteo_count = 0
            openmeteo_source_run_id = None
            openmeteo_resolved_cycle: str | None = None
            fusion_current_count = 0
            (
                openmeteo_count,
                openmeteo_source_run_id,
                openmeteo_resolved_cycle,
            ) = manifest_coverage_by_scope[(city, target_date, metric)]
            posterior_count = int(row["posterior_count"])
            readiness_count = int(row["readiness_count"])
            if source_run_targets and openmeteo_source_run_id:
                dependency_key = (
                    city,
                    target_date,
                    metric,
                    str(baseline_source_run_id or ""),
                    openmeteo_source_run_id,
                )
                posterior_count, readiness_count = dependency_coverage.get(
                    dependency_key,
                    (0, 0),
                )
            elif source_run_targets and metadata_column is not None:
                posterior_count = 0
                readiness_count = 0
            elif required_manifest_cycle and metadata_column is not None and openmeteo_count <= 0:
                posterior_count = 0
                readiness_count = 0
            if openmeteo_count > 0:
                fusion_current_count = _fusion_current_value_count(
                    conn,
                    city=city,
                    target_date=target_date,
                    temperature_metric=metric,
                    source_cycle_time=required_manifest_cycle
                    or openmeteo_resolved_cycle
                    or baseline_source_cycle_time,
                    raw_model_forecasts_available="raw_model_forecasts" in tables,
                )
            input_lag_reason = None
            if posterior_count > 0 and readiness_count > 0:
                input_lag_reason = _covering_posterior_input_lag_reason(
                    conn,
                    city=city,
                    target_date=target_date,
                    temperature_metric=metric,
                    decision_time=evaluation_now_utc,
                    baseline_source_run_id=(
                        str(baseline_source_run_id)
                        if baseline_source_run_id
                        else None
                    ),
                    openmeteo_source_run_id=openmeteo_source_run_id,
                    posterior_tradeable_grade_clause=posterior_tradeable_grade_clause,
                    check_day0_observation=day0_observed_extreme_required,
                    observation_conn=observation_conn,
                    posterior_columns=posterior_columns,
                    readiness_columns=readiness_columns,
                    readiness_binding_supported=readiness_binding_supported,
                )
            out.append(
                ReplacementForecastCurrentTargetPlanRow(
                    city=city,
                    target_date=target_date,
                    temperature_metric=metric,
                    market_bin_count=int(row["market_bin_count"]),
                    posterior_count=posterior_count,
                    readiness_count=readiness_count,
                    openmeteo_manifest_count=openmeteo_count,
                    fusion_current_value_count=fusion_current_count,
                    baseline_source_run_id=baseline_source_run_id,
                    baseline_source_cycle_time=baseline_source_cycle_time,
                    openmeteo_source_run_id=openmeteo_source_run_id,
                    day0_observed_extreme_required=day0_observed_extreme_required,
                    input_lag_reason=input_lag_reason,
                )
            )
    finally:
        if release_input_hwm is not None:
            release_input_hwm()
        if owned_observation_conn is not None:
            owned_observation_conn.close()
        conn.close()
    target_count = len(out)
    covered_count = sum(1 for row in out if row.covered)
    missing_coverage_count = target_count - covered_count
    can_seed_count = sum(1 for row in out if row.can_seed)
    missing_openmeteo_manifest_count = sum(1 for row in out if row.missing_openmeteo_manifest)
    missing_fusion_current_values_count = sum(1 for row in out if row.missing_fusion_current_values)
    day0_observed_extreme_required_count = sum(1 for row in out if row.day0_observed_extreme_required and not row.covered)
    status, reasons = _status_from_counts(
        target_count=target_count,
        missing_coverage_count=missing_coverage_count,
        can_seed_count=can_seed_count,
        missing_openmeteo_manifest_count=missing_openmeteo_manifest_count,
        missing_fusion_current_values_count=missing_fusion_current_values_count,
        day0_observed_extreme_required_count=day0_observed_extreme_required_count,
    )
    return ReplacementForecastCurrentTargetPlan(
        status=status,
        reason_codes=reasons,
        target_count=target_count,
        covered_count=covered_count,
        missing_coverage_count=missing_coverage_count,
        can_seed_count=can_seed_count,
        missing_openmeteo_manifest_count=missing_openmeteo_manifest_count,
        missing_fusion_current_values_count=missing_fusion_current_values_count,
        day0_observed_extreme_required_count=day0_observed_extreme_required_count,
        rows=tuple(out),
    )


def replacement_forecast_download_plan_from_current_targets(
    plan: ReplacementForecastCurrentTargetPlan,
) -> dict[str, object]:
    """Return a compact actionable download/materialization plan from coverage rows."""

    missing = [row for row in plan.rows if not row.covered]
    return {
        "status": plan.status,
        "reason_codes": list(plan.reason_codes),
        "openmeteo_download_targets": [
            row.as_dict() for row in missing if row.missing_openmeteo_manifest
        ],
        "fusion_current_value_missing_targets": [
            row.as_dict() for row in missing if row.missing_fusion_current_values
        ],
        "seedable_targets": [
            row.as_dict() for row in missing if row.can_seed
        ],
    }
