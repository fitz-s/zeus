# Created: 2026-06-10
# Last reused or audited: 2026-07-29
# Authority basis: operator green-light 2026-06-10 item B (remaining-day
#   pricing + persist-the-hourly-vector option from the day0 first-principles
#   review §6.1/§6.3). INV-37: all writes go to zeus-forecasts.db under
#   db_writer_lock(LIVE); reads are mode=ro.
"""Day0 high-res hourly forecast vectors: persist + remaining-day extremes.

Why
---
The day0 entry lane priced P(bin) from the FULL-DAY forecast distribution
masked by the running extreme — not P(remaining-day excursion | now). The
review (2026-06-10 §2.4) classified that DEVIATES: post-peak it overprices
bins above the running max. The data needed to fix it (hourly curves from the
high-res models icon_d2 / arome HD / UKMO UKV 2km / NCEP NBM) was being
FETCHED and then reduced to a single daily extremum (raw_model_forecasts).
This module persists the bounded hourly vector so the day0 q can condition on
hours AFTER now.

Bounded by design
-----------------
- Day0-relevant cities use in-domain regional hourly models when available
  (polygon gate reused from src/forecast/model_selection.regional_eligible,
  lead 0). Every city also uses the current global deterministic provider
  bundle so Day0 probability does not collapse to one model outside regional
  domains.
- Only ~2 forecast days of hours per row; retention prunes rows older than
  DAY0_VECTOR_RETENTION_DAYS (default 3) on every write pass.
- Refresh throttled to once per DEFAULT_REFRESH_INTERVAL_S per process.

Provenance: every row carries source identity (provider/model/endpoint/
request hash), a local capture clock, and fetch possession clocks.  The generic
Open-Meteo forecast response does not expose a per-model initialization/run
cycle, so this lane never fabricates one from local fetch time.
Temperatures are ALWAYS degC in storage (the C/F unit-mix antibody from the
bayes_precision_fusion lane: convert at the consumption seam, never store mixed units).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import threading
import time
from collections import Counter
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from functools import lru_cache
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np

from src.data.openmeteo_quota import quota_tracker

logger = logging.getLogger(__name__)

UTC = timezone.utc

OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPENMETEO_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

# The extrema product can be structurally non-identifiable when a 3-hour
# ECMWF bucket straddles a city's local midnight (UTC+8 LOW is the common
# case).  The conditional Day0 operator needs the unresolved-hour ENS shape,
# not a mislabeled full-day extrema row.  Persist the exact-run IFS025 member
# paths in the existing hourly-vector table so selection and submit can bind
# the same possession proof without adding a parallel truth store.
DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL = "ecmwf_ifs025"
DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_COUNT = 51
DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_PREFIX = (
    f"{DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL}_member"
)

#: High-res intraday models for the day0 remaining-day distribution
#: (operator charge #2: icon_d2 ~2km, arome HD, UKMO UKV 2km, NCEP NBM CONUS).
#: Each is domain-gated via config/model_domain_polygons.yaml.
DAY0_HOURLY_MODELS: tuple[str, ...] = (
    "icon_d2",
    "meteofrance_arome_france_hd",
    "ukmo_uk_deterministic_2km",
    "ncep_nbm_conus",
    "jma_msm",
)
GLOBAL_DAY0_HOURLY_MODELS: tuple[str, ...] = (
    "ecmwf_ifs",
    "icon_global",
    "ukmo_global_deterministic_10km",
)

DAY0_VECTOR_RETENTION_DAYS = 3.0
DEFAULT_REFRESH_INTERVAL_S = 1800.0  # 30 min — high-res runs update hourly-ish
DEFAULT_FETCH_TIMEOUT_S = 4.0
DEFAULT_REFRESH_BUDGET_S = 6.0
DEFAULT_REFRESH_MAX_CITIES = 3
DAY0_HOURLY_BUNDLE_MAX_AGE_HOURS = 3.0
DAY0_HOURLY_REFRESH_HEADROOM_HOURS = 1.0
DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES = 60.0
DAY0_HOURLY_FORECAST_HOURS = 72
INCOMPLETE_BUNDLE_RETRY_INTERVAL_S = 45.0
INCOMPLETE_BUNDLE_RETRY_MAX_INTERVAL_S = DEFAULT_REFRESH_INTERVAL_S
INCOMPLETE_BUNDLE_CRITICAL_RETRY_MAX_INTERVAL_S = 600.0

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS day0_hourly_vectors (
    vector_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    timezone_name TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'openmeteo',
    endpoint TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK (request_hash <> ''),
    times_json TEXT NOT NULL,
    temps_c_json TEXT NOT NULL,
    source_run_meta_json TEXT
)
"""
_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_day0_hourly_vectors_city_date "
    "ON day0_hourly_vectors(city, target_date, captured_at)"
)


def day0_hourly_target_dates_for_refresh(
    *, city: Any, decision_time: datetime
) -> tuple[str, ...]:
    """Target dates covered by a 2-day hourly fetch for the city's local clock.

    Open-Meteo requests in this module ask for ``forecast_days=2``. Persisting the
    response only under the city's current local date starves active next-day weather
    markets: the read path correctly requires exact ``(city, target_date)``, so a
    June 29 market cannot use a June 28-stamped vector even though the payload already
    contains June 29 hours. Persist both local today and local tomorrow under separate
    target_date identities.
    """

    tz = ZoneInfo(str(getattr(city, "timezone")))
    local_day = decision_time.astimezone(tz).date()
    return (
        local_day.isoformat(),
        (local_day + timedelta(days=1)).isoformat(),
    )


def day0_source_clock_ensemble_target_dates(
    *,
    city: Any,
    decision_time: datetime,
    conn: sqlite3.Connection | None = None,
) -> tuple[str, ...]:
    """Return current-day LOW scopes whose newest extrema ENS is ambiguous.

    This is a data-product routing decision, not a probability waiver.  Only a
    newest possessed canonical row with true boundary ambiguity can request the
    hourly ensemble carrier; a missing table/row simply leaves ENTRY blocked.
    """

    if decision_time.tzinfo is None:
        raise ValueError("decision_time must be timezone-aware")
    city_name = str(getattr(city, "name", "") or "").strip()
    timezone_name = str(getattr(city, "timezone", "") or "").strip()
    if not city_name or not timezone_name:
        return ()
    target_date = decision_time.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_forecasts_connection_read_only

        try:
            conn = get_forecasts_connection_read_only()
        except sqlite3.Error:
            return ()
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ensemble_snapshots'"
        ).fetchone()
        if table is None:
            return ()
        row = conn.execute(
            """
            SELECT boundary_ambiguous, causality_status,
                   contributes_to_target_extrema
              FROM ensemble_snapshots
             WHERE city = ? AND target_date = ?
               AND temperature_metric = 'low'
               AND available_at <= ?
             ORDER BY datetime(available_at) DESC, snapshot_id DESC
             LIMIT 1
            """,
            (city_name, target_date, decision_time.astimezone(UTC).isoformat()),
        ).fetchone()
        if row is None:
            return ()
        return (
            (target_date,)
            if int(row[0] or 0) == 1
            and str(row[1] or "").strip() == "REJECTED_BOUNDARY_AMBIGUOUS"
            and int(row[2] or 0) == 0
            else ()
        )
    except sqlite3.Error:
        return ()
    finally:
        if own_conn and conn is not None:
            conn.close()


@dataclass(frozen=True)
class Day0HourlyVector:
    model: str
    city: str
    target_date: str
    timezone_name: str
    # Local request/capture clock assigned by the fetcher; this is not a
    # provider-issued forecast/observation timestamp and is not possession.
    captured_at: str
    times: tuple[str, ...]       # ISO local timestamps as served (city timezone)
    temps_c: tuple[float, ...]   # ALWAYS degC
    # JSON provenance written only by the live fetch path.  It carries the
    # separate fetch-start/fetch-complete possession clocks and source-run
    # identity; rows without it cannot sponsor held probability authority.
    source_run_meta_json: str | None = None


@dataclass(frozen=True)
class Day0CausalBundleValidation:
    """Comparison result for one immutable Day0 vector/posterior bundle."""

    ok: bool
    reason: str | None
    expected_bundle_identity: str
    actual_bundle_identity: str
    expected_carrier_vector_identity: str
    actual_carrier_vector_identity: str
    expected_carrier_vector_hash: str
    actual_carrier_vector_hash: str

    def receipt(self) -> dict[str, object]:
        """Return the exact mismatch evidence suitable for a decision receipt."""

        return {
            "reason": self.reason,
            "expected_bundle_identity": self.expected_bundle_identity,
            "actual_bundle_identity": self.actual_bundle_identity,
            "expected_carrier_vector_identity": self.expected_carrier_vector_identity,
            "actual_carrier_vector_identity": self.actual_carrier_vector_identity,
            "expected_carrier_vector_hash": self.expected_carrier_vector_hash,
            "actual_carrier_vector_hash": self.actual_carrier_vector_hash,
        }


def _day0_canonical_json(value: object) -> object:
    """Normalize only deterministic JSON values used in causal identity keys."""

    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID")
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _day0_canonical_json(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_day0_canonical_json(item) for item in value]
    raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID")


def _day0_json_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            _day0_canonical_json(value), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def build_day0_causal_evidence_bundle(
    *,
    city: str,
    target_date: str,
    metric: str,
    observation_context: Mapping[str, object],
    cutoff_utc: str,
    vector_witness: Mapping[str, object],
) -> dict[str, object]:
    """Build one immutable Day0 causal bundle for a posterior and its vectors.

    The vector identity names the exact per-model persisted rows; the vector
    hash binds their complete provenance.  The bundle identity additionally
    commits to the Day0 observation context and causal cutoff.  Consumers must
    compare two bundles rather than rebind a posterior to a newer vector row.
    """

    normalized_city = str(city or "").strip()
    normalized_target_date = str(target_date or "").strip()
    normalized_metric = str(metric or "").strip().lower()
    normalized_cutoff = str(cutoff_utc or "").strip()
    if (
        not normalized_city
        or not normalized_target_date
        or normalized_metric not in {"high", "low"}
        or not normalized_cutoff
        or not isinstance(observation_context, Mapping)
        or not observation_context
        or not isinstance(vector_witness, Mapping)
    ):
        raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID")
    try:
        date.fromisoformat(normalized_target_date[:10])
        parsed_cutoff = datetime.fromisoformat(
            normalized_cutoff.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID") from exc
    if parsed_cutoff.tzinfo is None or parsed_cutoff.utcoffset() is None:
        raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID")
    vector_ids = vector_witness.get("vector_ids_by_model")
    if not isinstance(vector_ids, Mapping) or not vector_ids:
        raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID")
    normalized_vector_ids = {
        str(model).strip(): str(vector_id).strip()
        for model, vector_id in vector_ids.items()
    }
    if any(
        not model or not vector_id
        for model, vector_id in normalized_vector_ids.items()
    ):
        raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID")
    canonical_observation = _day0_canonical_json(observation_context)
    canonical_witness = _day0_canonical_json(vector_witness)
    vector_hash_fields = (
        "vector_ids_by_model",
        "capture_times_by_model_utc",
        "request_hash_by_model",
        "source_run_id_by_model",
        "provider_run_id_by_model",
        "provider_source_cycle_time_by_model_utc",
        "provider_source_available_at_by_model_utc",
        "provider_source_modified_at_by_model_utc",
    )
    canonical_vector_provenance = {
        field: canonical_witness[field]
        for field in vector_hash_fields
        if field in canonical_witness
    }
    carrier_vector_identity = _day0_json_hash(
        {"vector_ids_by_model": normalized_vector_ids}
    )
    carrier_vector_hash = _day0_json_hash(canonical_vector_provenance)
    core = {
        "schema": "day0_causal_evidence_bundle_v1",
        "city": normalized_city,
        "target_date": normalized_target_date,
        "metric": normalized_metric,
        "observation_context": canonical_observation,
        "cutoff_utc": parsed_cutoff.astimezone(UTC).isoformat(),
        "carrier_vector_identity": carrier_vector_identity,
        "carrier_vector_hash": carrier_vector_hash,
    }
    return {
        **core,
        "carrier_vector_ids_by_model": normalized_vector_ids,
        "carrier_vector_witness": canonical_witness,
        "bundle_identity": _day0_json_hash(core),
    }


def validate_day0_causal_evidence_bundle(
    *,
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> Day0CausalBundleValidation:
    """Compare immutable Day0 evidence bundles without authorizing a rebind."""

    try:
        fields = (
            "city",
            "target_date",
            "metric",
            "observation_context",
            "cutoff_utc",
        )
        expected_core = {key: expected[key] for key in fields}
        actual_core = {key: actual[key] for key in fields}
        expected_rebuilt = build_day0_causal_evidence_bundle(
            **expected_core,
            vector_witness=expected["carrier_vector_witness"],
        )
        actual_rebuilt = build_day0_causal_evidence_bundle(
            **actual_core,
            vector_witness=actual["carrier_vector_witness"],
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID") from None
    # A persisted bundle carries a full vector hash while a consumer's actual
    # bundle normally comes from the same full witness.  Require both supplied
    # values to agree with their own reconstructed identities before comparison.
    expected_identity = str(expected.get("bundle_identity") or "").strip()
    actual_identity = str(actual.get("bundle_identity") or "").strip()
    expected_vector_identity = str(expected.get("carrier_vector_identity") or "").strip()
    actual_vector_identity = str(actual.get("carrier_vector_identity") or "").strip()
    expected_vector_hash = str(expected.get("carrier_vector_hash") or "").strip()
    actual_vector_hash = str(actual.get("carrier_vector_hash") or "").strip()
    complete = all((
        expected_identity, actual_identity, expected_vector_identity,
        actual_vector_identity, expected_vector_hash, actual_vector_hash,
    ))
    self_consistent = (
        expected_identity == expected_rebuilt["bundle_identity"]
        and actual_identity == actual_rebuilt["bundle_identity"]
        and expected_vector_identity == expected_rebuilt["carrier_vector_identity"]
        and actual_vector_identity == actual_rebuilt["carrier_vector_identity"]
        and expected_vector_hash == expected_rebuilt["carrier_vector_hash"]
        and actual_vector_hash == actual_rebuilt["carrier_vector_hash"]
    )
    ok = bool(
        complete
        and self_consistent
        and expected_identity == actual_identity
        and expected_vector_identity == actual_vector_identity
        and expected_vector_hash == actual_vector_hash
    )
    return Day0CausalBundleValidation(
        ok=ok,
        reason=None if ok else "DAY0_CAUSAL_EVIDENCE_BUNDLE_MISMATCH",
        expected_bundle_identity=expected_identity,
        actual_bundle_identity=actual_identity,
        expected_carrier_vector_identity=expected_vector_identity,
        actual_carrier_vector_identity=actual_vector_identity,
        expected_carrier_vector_hash=expected_vector_hash,
        actual_carrier_vector_hash=actual_vector_hash,
    )


def day0_remaining_carrier_identity_inputs(
    *,
    city: str,
    unit: str,
    decision_time_utc: str,
    station_id: str,
    preliminary_survival_identity: str,
) -> dict[str, object]:
    """Build the one identity input shape shared by materialize and replay."""

    normalized_city = str(city or "").strip()
    normalized_unit = str(unit or "").strip().upper()
    normalized_decision = str(decision_time_utc or "").strip()
    normalized_station = str(station_id or "").strip().upper()
    normalized_likelihood = str(preliminary_survival_identity or "").strip().lower()
    if (
        not normalized_city
        or normalized_unit not in {"C", "F"}
        or not normalized_decision
        or not normalized_station
        or not normalized_likelihood
    ):
        raise ValueError("DAY0_REMAINING_CARRIER_IDENTITY_INPUT_INVALID")
    return {
        "city": normalized_city,
        "unit": normalized_unit,
        "probability_cutoff_utc": normalized_decision,
        "decision_time_utc": normalized_decision,
        "station_id": normalized_station,
        "awc_source_channel": "aviationweather_metar",
        "ogimet_source_channel": f"ogimet_metar_{normalized_station.lower()}",
        "preliminary_survival_identity": normalized_likelihood,
    }


def build_day0_remaining_probability_carrier(
    *, future_extremes_c: Iterable[float], boundary_scenarios: Iterable[tuple[float | None, float]],
    metric: str, path_error_sigma_c: float, instrument_sigma_c: float,
    bin_bounds_c: Iterable[tuple[float | None, float | None]], n_point: int,
    n_samples: int, identity_inputs: Mapping[str, object],
) -> dict[str, object]:
    """Pure ``extreme(boundary, noisy future)`` carrier for both Day0 readers.

    Boundary scenarios are a statistical report-survival likelihood, not final
    settlement authority.  Noise is always applied to the future path first.
    """
    values = np.sort(
        np.asarray(tuple(float(v) for v in future_extremes_c), dtype=float)
    )
    scenarios = tuple(
        (None if b is None else float(b), float(w))
        for b, w in boundary_scenarios
    )
    bounds = tuple(
        (
            None if low is None else float(low),
            None if high is None else float(high),
        )
        for low, high in bin_bounds_c
    )
    unit = str(identity_inputs.get("unit") or "").strip().upper()
    if unit not in {"C", "F"}:
        raise ValueError("DAY0_REMAINING_CARRIER_UNIT_INVALID")
    if any(
        (low is not None and not math.isclose(low, round(low), abs_tol=1e-9))
        or (high is not None and not math.isclose(high, round(high), abs_tol=1e-9))
        or (low is not None and high is not None and low > high)
        for low, high in bounds
    ):
        raise ValueError("DAY0_REMAINING_CARRIER_BIN_BOUNDS_INVALID")
    if any(low is None and high is None for low, high in bounds):
        raise ValueError("DAY0_REMAINING_CARRIER_OPEN_OPEN_BIN_INVALID")
    ordered = sorted(bounds, key=lambda item: float("-inf") if item[0] is None else item[0])
    if (ordered and ordered[0][0] is not None) or (
        ordered and ordered[-1][1] is not None
    ):
        raise ValueError("DAY0_REMAINING_CARRIER_SHOULDER_TOPOLOGY_INVALID")
    for previous, current in zip(ordered, ordered[1:]):
        if previous[1] is None or current[0] is None or current[0] != previous[1] + 1.0:
            raise ValueError("DAY0_REMAINING_CARRIER_BIN_GAP_OR_OVERLAP")
    if (metric not in {"high", "low"} or not values.size or not np.isfinite(values).all()
            or not scenarios or not bounds or n_point < 1 or n_samples < 1
            or path_error_sigma_c < 0 or instrument_sigma_c < 0
            or not math.isclose(sum(w for _, w in scenarios), 1.0, abs_tol=1e-9)
            or any(
                (b is not None and not math.isfinite(b)) or w < 0
                for b, w in scenarios
            )):
        raise ValueError("DAY0_REMAINING_CARRIER_INPUT_INVALID")
    content = {"v": 1, "metric": metric, "future": sorted(values.tolist()), "scenarios": scenarios,
               "path_sigma": path_error_sigma_c, "instrument_sigma": instrument_sigma_c,
               "bins": bounds, "n_point": n_point, "n_samples": n_samples, "inputs": dict(identity_inputs)}
    identity = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    sigma = math.hypot(path_error_sigma_c, instrument_sigma_c)
    def draw(rows: int, seed: int) -> np.ndarray:
        rng = np.random.default_rng(seed)
        future = values + rng.normal(0.0, sigma, (rows, values.size))
        scenario_i = rng.choice(len(scenarios), size=rows, p=[w for _, w in scenarios])
        boundary = np.asarray(
            [0.0 if scenarios[i][0] is None else scenarios[i][0] for i in scenario_i]
        )[:, None]
        has_boundary = np.asarray(
            [scenarios[i][0] is not None for i in scenario_i], dtype=bool
        )[:, None]
        bounded = (
            np.maximum(future, boundary)
            if metric == "high"
            else np.minimum(future, boundary)
        )
        final = np.where(has_boundary, bounded, future)
        settled = np.floor(final + 0.5)
        out = np.empty((rows, len(bounds)), dtype=float)
        for i, (low, high) in enumerate(bounds):
            mask = np.ones(settled.shape, dtype=bool)
            if low is not None:
                mask &= settled >= low
            if high is not None:
                mask &= settled <= high
            out[:, i] = np.mean(mask, axis=1)
        totals = out.sum(axis=1, keepdims=True)
        if np.any(totals <= 0.0) or not np.isfinite(totals).all():
            raise ValueError("DAY0_REMAINING_CARRIER_BIN_TOPOLOGY_INVALID")
        out /= totals
        return out
    seed = int(identity[:16], 16)
    point = draw(n_point, seed).mean(axis=0)
    samples = draw(n_samples, seed ^ 0x9E3779B97F4A7C15)
    return {"q": [float(x) for x in point], "samples": [[float(x) for x in row] for row in samples], "content_identity": identity,
            "operator": "extreme_observed_then_noisy_future_v1", "sample_count": n_samples}


@dataclass(frozen=True)
class Day0HourlyRefreshStats:
    vectors_written: int = 0
    cities_attempted: int = 0
    cities_skipped_throttle: int = 0
    cities_skipped_quota: int = 0
    incomplete_expected_bundles: int = 0
    unavailable_bundles: tuple["Day0HourlyBundleUnavailable", ...] = ()
    priority_reserve_exhausted: bool = False
    budget_exhausted: bool = False


@dataclass(frozen=True)
class Day0HourlyBundleUnavailable:
    """Typed fail-closed outcome for one attempted, incomplete live bundle."""

    city: str
    target_dates: tuple[str, ...]
    expected_models: tuple[str, ...]
    available_models: tuple[str, ...]
    missing_models: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Day0ProviderRunHwm:
    """Publicly usable provider-run scheduling witness.

    Metadata may wake an exact vector fetch, but it is never probability
    evidence. Persisted vector provenance must independently prove the same or
    a newer provider run before the bundle can be consumed.
    """

    model: str
    run_initialisation_time: datetime
    run_availability_time: datetime


def in_domain_models_for_city(city: Any, *, models: Iterable[str] = DAY0_HOURLY_MODELS) -> list[str]:
    """Polygon-gated model list for a city (lead 0). Fail-soft to [] on gate errors."""
    try:
        from src.forecast.model_selection import load_domain_polygons, regional_eligible

        polygons = load_domain_polygons()
        lat = float(getattr(city, "lat"))
        lon = float(getattr(city, "lon"))
        return [
            model
            for model in models
            if regional_eligible(model, lat=lat, lon=lon, lead_days=0, polygons=polygons)
        ]
    except Exception as exc:  # noqa: BLE001 — gating failure means no vectors, never a crash
        logger.warning(
            "DAY0_HOURLY_VECTORS_DOMAIN_GATE_FAILED city=%s exc=%s: %s",
            getattr(city, "name", "?"), type(exc).__name__, exc,
        )
        return []


def day0_hourly_models_for_city(city: Any) -> list[str]:
    """Live Day0 remaining-day hourly model set for a city.

    Regional high-resolution models are experts, not a replacement for the live
    probability chain's global evidence. Keep every available regional expert
    and the same three global deterministic models used by the current forecast
    provider set. A single global anchor is not a probability distribution: it
    erases current between-model disagreement and makes Day0 entry/exit bands
    depend on one provider path.
    """

    regional = in_domain_models_for_city(city)
    out: list[str] = []
    for model in (*regional, *GLOBAL_DAY0_HOURLY_MODELS):
        normalized = str(model or "").strip()
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def day0_source_clock_ensemble_member_models() -> tuple[str, ...]:
    """Canonical row identities for one 51-member IFS025 hourly capture."""

    return tuple(
        f"{DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_PREFIX}{index:02d}"
        for index in range(DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_COUNT)
    )


def probe_day0_provider_run_hwm(
    cities: Iterable[Any],
    *,
    decision_time: datetime,
    timeout_s: float,
) -> dict[str, Day0ProviderRunHwm]:
    """Read one coalesced provider-run HWM for the candidate city set."""

    if decision_time.tzinfo is None:
        raise ValueError("decision_time must be timezone-aware")
    models = tuple(
        sorted(
            {
                model
                for city in cities
                for model in day0_hourly_models_for_city(city)
                if str(model or "").strip()
            }
        )
    )
    if not models:
        return {}
    from src.data.openmeteo_model_updates import fetch_model_updates
    from src.strategy.live_inference.source_clock_vnext import source_publicly_usable_at

    updates = fetch_model_updates(
        models,
        timeout_seconds=max(0.25, float(timeout_s)),
        max_workers=max(1, min(len(models), 8)),
        priority=True,
    )
    now = decision_time.astimezone(UTC)
    out: dict[str, Day0ProviderRunHwm] = {}
    for update in updates:
        model = str(update.model or "").strip()
        if model not in models:
            continue
        if now < source_publicly_usable_at(update.to_source_run_clock()):
            continue
        out[model] = Day0ProviderRunHwm(
            model=model,
            run_initialisation_time=update.last_run_initialisation_time.astimezone(UTC),
            run_availability_time=update.last_run_availability_time.astimezone(UTC),
        )
    return out


def _provider_run_identity_from_meta(
    payload: object,
    *,
    expected_model: str,
) -> tuple[datetime, datetime] | None:
    """Parse exact Open-Meteo provenance without local-time coercion."""

    if not isinstance(payload, Mapping):
        return None
    if str(payload.get("model") or "").strip() != expected_model:
        return None
    if str(payload.get("provider") or "").strip() != "openmeteo":
        return None
    try:
        run = datetime.fromisoformat(
            str(payload["provider_source_cycle_time_utc"]).replace("Z", "+00:00")
        )
        available = datetime.fromisoformat(
            str(payload["provider_source_available_at_utc"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError):
        return None
    if (
        run.tzinfo is None
        or run.utcoffset() is None
        or available.tzinfo is None
        or available.utcoffset() is None
    ):
        return None
    return run.astimezone(UTC), available.astimezone(UTC)


def day0_hourly_release_due_city_dates(
    cities: Iterable[Any],
    *,
    decision_time: datetime,
    provider_run_hwm: Mapping[str, Day0ProviderRunHwm],
    conn: sqlite3.Connection | None = None,
) -> frozenset[tuple[str, str]]:
    """Return city/date scopes whose persisted vectors trail a public run HWM."""

    own_conn = conn is None
    if own_conn:
        from src.state.db import get_forecasts_connection_read_only

        conn = get_forecasts_connection_read_only()
    due: set[tuple[str, str]] = set()
    try:
        for city in cities:
            city_name = str(getattr(city, "name", "") or "").strip()
            if not city_name:
                continue
            target_date = day0_hourly_target_dates_for_refresh(
                city=city, decision_time=decision_time
            )[0]
            expected_models = day0_hourly_models_for_city(city)
            required = {
                model: provider_run_hwm[model]
                for model in expected_models
                if model in provider_run_hwm
            }
            if not required:
                continue
            rows = conn.execute(
                """
                SELECT model, source_run_meta_json
                FROM day0_hourly_vectors
                WHERE city = ? AND target_date = ?
                ORDER BY captured_at DESC
                """,
                (city_name, target_date),
            ).fetchall()
            latest: dict[str, Mapping[str, object]] = {}
            for row in rows:
                model = str(row[0] or "").strip()
                if model in latest or model not in required:
                    continue
                try:
                    payload = json.loads(str(row[1] or ""))
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = None
                latest[model] = payload if isinstance(payload, Mapping) else {}
            for model, hwm in required.items():
                payload = latest.get(model)
                actual = _provider_run_identity_from_meta(
                    payload,
                    expected_model=model,
                )
                if actual is None:
                    due.add((city_name, target_date))
                    break
                if actual < (
                    hwm.run_initialisation_time,
                    hwm.run_availability_time,
                ):
                    due.add((city_name, target_date))
                    break
    finally:
        if own_conn and conn is not None:
            conn.close()
    return frozenset(due)


def _vectors_trailing_provider_hwm(
    vectors: Iterable[Day0HourlyVector],
    *,
    required_hwm: Mapping[str, Day0ProviderRunHwm],
) -> tuple[str, ...]:
    """Identify exact payloads that do not prove their scheduling HWM."""

    by_model = {str(vector.model): vector for vector in vectors}
    trailing: list[str] = []
    for model, hwm in required_hwm.items():
        vector = by_model.get(model)
        try:
            payload = json.loads(str(vector.source_run_meta_json or ""))
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            trailing.append(model)
            continue
        actual = _provider_run_identity_from_meta(payload, expected_model=model)
        if actual is None:
            trailing.append(model)
            continue
        if actual < (hwm.run_initialisation_time, hwm.run_availability_time):
            trailing.append(model)
    return tuple(trailing)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_TABLE_DDL)
    conn.execute(_INDEX_DDL)


def _vector_id(model: str, city: str, target_date: str, captured_at: str) -> str:
    canonical = f"d0hv|{model}|{city}|{target_date}|{captured_at}"
    return "d0hv" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def build_request_hash(
    *,
    endpoint: str,
    params: dict,
    models: list[str],
    captured_at: str,
    payload: object,
) -> str:
    """Replayable provenance identity for one hourly-vector capture
    (PR#404 P1): canonicalized request params + endpoint + model list +
    captured_at bucket + response payload hash. A persisted vector row can
    always answer 'which exact request and response produced you'."""
    canonical_params = json.dumps(params, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    canonical = "|".join((
        "d0hv_req_v1", endpoint, canonical_params, ",".join(sorted(models)),
        str(captured_at)[:16],  # minute bucket: idempotent within a capture pass
        payload_hash,
    ))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _day0_provider_run_meta(
    *,
    model: str,
    model_api_id: str,
    run: datetime,
    available_at: datetime,
    modified_at: datetime | None,
    authority: str,
    endpoint_mode: str,
    request_params: Mapping[str, object],
    request_hash: str,
    fetch_started_at: datetime,
    fetch_finished_at: datetime,
) -> dict[str, object]:
    """Build explicit provider-run provenance for one hourly vector."""

    if modified_at is None:
        raise ValueError("provider model metadata modification time is required")
    return {
        "source_run_id": f"day0_hourly:{request_hash}",
        "provider_run_id": f"openmeteo:{model_api_id}:{run.isoformat()}",
        "provider_source_cycle_time_utc": run.isoformat(),
        "provider_source_available_at_utc": available_at.isoformat(),
        "provider_source_modified_at_utc": modified_at.isoformat(),
        "source_run_authority": authority,
        "endpoint_mode": endpoint_mode,
        "model": model,
        "model_api_id": model_api_id,
        "provider": "openmeteo",
        "endpoint": request_params.get("endpoint"),
        "request_params_json": json.dumps(
            {key: value for key, value in request_params.items() if key != "endpoint"},
            sort_keys=True,
            separators=(",", ":"),
        ),
        "request_hash": request_hash,
        "fetch_started_at": fetch_started_at.isoformat(),
        "fetch_finished_at": fetch_finished_at.isoformat(),
    }


def _day0_exact_run_payloads(
    *,
    city: Any,
    models: list[str],
    decision_time: datetime,
    timeout_s: float,
) -> tuple[list[tuple[str, Mapping[str, object], dict[str, object]]], dict[str, object]]:
    """Fetch one exact provider run per model, preserving the raw hourly payload.

    Model metadata is read directly, never from the stale source-clock JSONL cache. The
    raw Single Runs request is delegated to the existing BPF transport adapter. The
    standard endpoint is only accepted when its metadata bracket proves the same run.
    Metadata and the per-model exact requests share one bounded caller budget; a
    single-city refresh therefore fails closed rather than extending the cycle.
    """
    from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
    from src.data.bayes_precision_fusion_download import (
        _fetch_single_runs_hourly_payloads_batched,
        _fetch_standard_meta_stamped_payloads,
    )
    from src.data.openmeteo_ecmwf_ifs9_anchor import (
        SINGLE_RUNS_FORECAST_URL,
        STANDARD_FORECAST_URL,
    )
    from src.data.openmeteo_model_updates import fetch_model_updates
    from src.strategy.live_inference.source_clock_vnext import source_publicly_usable_at

    deadline_monotonic = time.monotonic() + max(1.0, float(timeout_s))

    def _remaining_budget_seconds() -> float:
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0.0:
            raise TimeoutError("DAY0_PROVIDER_RUN_BUDGET_EXHAUSTED")
        return remaining

    updates = fetch_model_updates(
        models,
        timeout_seconds=_remaining_budget_seconds(),
        max_workers=max(1, min(len(models), 8)),
    )
    by_model = {str(update.model).strip(): update for update in updates}
    if set(by_model) != {str(model).strip() for model in models}:
        raise ValueError("DAY0_PROVIDER_RUN_METADATA_INCOMPLETE")
    city_name = str(getattr(city, "name", "?") or "?")
    location = (
        float(getattr(city, "lat")),
        float(getattr(city, "lon")),
        str(getattr(city, "timezone")),
        day0_hourly_target_dates_for_refresh(city=city, decision_time=decision_time),
    )
    captured_at = decision_time.astimezone(UTC).isoformat()
    fetched: list[tuple[str, Mapping[str, object], dict[str, object]]] = []
    request_identity: dict[str, object] = {
        "endpoint": OPENMETEO_FORECAST_URL,
        "city": city_name,
        "latitude": location[0],
        "longitude": location[1],
        "timezone": location[2],
        "hourly": "temperature_2m",
        "forecast_hours": DAY0_HOURLY_FORECAST_HOURS,
        "temperature_unit": "celsius",
        "cell_selection": "land",
        "models": [],
        "runs": {},
        "endpoint_modes": {},
    }
    decision_utc = decision_time.astimezone(UTC)
    for model in models:
        _remaining_budget_seconds()
        model = str(model).strip()
        update = by_model[model]
        run = update.last_run_initialisation_time.astimezone(UTC)
        available_at = update.last_run_availability_time.astimezone(UTC)
        modified_at = (
            update.last_run_modification_time.astimezone(UTC)
            if update.last_run_modification_time is not None
            else None
        )
        if (
            run > decision_utc
            or available_at > decision_utc
            or decision_utc < source_publicly_usable_at(update.to_source_run_clock())
            or modified_at is None
        ):
            raise ValueError(f"DAY0_PROVIDER_RUN_NOT_PUBLICLY_USABLE:{model}")
        model_api_id = OPENMETEO_MODEL_IDS.get(model, model)
        request_identity["models"].append(model_api_id)
        request_identity["runs"][model] = run.isoformat()
        fetch_started = datetime.now(UTC)
        authority = "run_pinned_single_runs"
        endpoint_mode = "single_runs"
        try:
            payloads = _fetch_single_runs_hourly_payloads_batched(
                models=[model], locations=[location], run=run,
                forecast_hours=DAY0_HOURLY_FORECAST_HOURS,
                deadline_monotonic=deadline_monotonic,
            )
            payload = payloads[0]
        except Exception as single_exc:
            try:
                payloads, transport = _fetch_standard_meta_stamped_payloads(
                    model=model, locations=[location], run=run,
                    source_available_at=available_at,
                    forecast_hours=DAY0_HOURLY_FORECAST_HOURS,
                    deadline_monotonic=deadline_monotonic,
                )
                payload = payloads[0]
                run = transport.run.astimezone(UTC)
                available_at = transport.source_available_at.astimezone(UTC)
                modified_at = transport.modification_time.astimezone(UTC)
                authority = "provider_meta_declared"
                endpoint_mode = "standard_meta_stamped"
            except Exception as standard_exc:
                raise ValueError(
                    f"DAY0_PROVIDER_RUN_TRANSPORT_UNAVAILABLE:{model}:"
                    f"single={type(single_exc).__name__}:standard={type(standard_exc).__name__}"
                ) from standard_exc
        fetch_finished = datetime.now(UTC)
        request_identity["endpoint_modes"][model] = endpoint_mode
        fetched.append((model, payload, {
            "model_api_id": model_api_id,
            "run": run,
            "available_at": available_at,
            "modified_at": modified_at,
            "authority": authority,
            "endpoint_mode": endpoint_mode,
            "fetch_started": fetch_started,
            "fetch_finished": fetch_finished,
        }))
    request_identity_payload = {
        **request_identity,
        "runs": dict(sorted(request_identity["runs"].items())),
        "models": tuple(request_identity["models"]),
    }
    bundle_hash = build_request_hash(
        endpoint=OPENMETEO_FORECAST_URL, params=request_identity_payload,
        models=models, captured_at=captured_at,
        payload={model: payload for model, payload, _meta in fetched},
    )
    return ([(model, payload, _day0_provider_run_meta(
        model=model, model_api_id=str(meta["model_api_id"]), run=meta["run"],
        available_at=meta["available_at"], modified_at=meta["modified_at"],
        authority=str(meta["authority"]), endpoint_mode=str(meta["endpoint_mode"]),
        request_params={
            **request_identity_payload,
            "endpoint": (
                SINGLE_RUNS_FORECAST_URL
                if str(meta["endpoint_mode"]) == "single_runs"
                else STANDARD_FORECAST_URL
            ),
            "model": model,
                        "model_api_id": meta["model_api_id"],
                        "run": meta["run"].isoformat()},
        request_hash=bundle_hash, fetch_started_at=meta["fetch_started"],
        fetch_finished_at=meta["fetch_finished"],
    )) for model, payload, meta in fetched], request_identity_payload)


def fetch_day0_hourly_vectors(
    city: Any,
    *,
    models: Optional[list[str]] = None,
    now: Optional[datetime] = None,
    timeout_s: float = DEFAULT_FETCH_TIMEOUT_S,
) -> tuple[list[Day0HourlyVector], str]:
    """Fetch exact-run hourly temperature curves for in-domain models.

    Returns (vectors, request_hash) — the hash is the replayable
    provenance identity persisted with every row (PR#404 P1: empty provenance
    identity is not acceptable for q-construction inputs). Fail-soft:
    ([], "") on any transport/shape error.
    """
    chosen = models if models is not None else day0_hourly_models_for_city(city)
    if not chosen:
        return [], ""
    # This is the local request/capture clock used for vector row identity;
    # possession is the separate fetch_finished_at in source_run_meta_json.
    captured_at = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    try:
        fetched, _request_identity = _day0_exact_run_payloads(
            city=city,
            models=[str(model).strip() for model in chosen if str(model).strip()],
            decision_time=(now or datetime.now(UTC)).astimezone(UTC),
            timeout_s=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft lane
        logger.warning(
            "DAY0_HOURLY_VECTORS_FETCH_FAILED city=%s exc=%s: %s",
            getattr(city, "name", "?"), type(exc).__name__, exc,
        )
        return [], ""
    request_hash = str(fetched[0][2].get("request_hash") or "")
    if not request_hash:
        return [], ""
    vectors: list[Day0HourlyVector] = []
    for model, payload, source_meta in fetched:
        vectors.extend(parse_openmeteo_hourly_payload(
            payload, city=city, models=[model], captured_at=captured_at,
            source_run_meta_json=json.dumps(
                source_meta, sort_keys=True, separators=(",", ":")
            ),
        ))
    return (
        vectors,
        request_hash,
    )


def _same_model_update(left: Any, right: Any) -> bool:
    """Require the metadata bracket to name one immutable provider run."""

    return bool(
        left is not None
        and right is not None
        and left.last_run_initialisation_time == right.last_run_initialisation_time
        and left.last_run_availability_time == right.last_run_availability_time
        and left.last_run_modification_time == right.last_run_modification_time
    )


def parse_openmeteo_ensemble_hourly_payload(
    payload: object,
    *,
    city: Any,
    captured_at: str,
    source_meta_by_member: Mapping[str, Mapping[str, object]],
) -> list[Day0HourlyVector]:
    """Parse one complete IFS025 control+50 perturbed-member response.

    Open-Meteo names the control field ``temperature_2m`` and perturbed
    members ``temperature_2m_member01`` ... ``member50``.  A partial response
    is unusable: the current-evidence within-spread must retain all 51 members.
    """

    if not isinstance(payload, Mapping) or not isinstance(
        payload.get("hourly"), Mapping
    ):
        return []
    hourly = payload["hourly"]
    times = hourly.get("time")
    if not isinstance(times, (list, tuple)) or not times:
        return []
    expected = day0_source_clock_ensemble_member_models()
    if set(source_meta_by_member) != set(expected):
        return []
    vectors: list[Day0HourlyVector] = []
    for index, model in enumerate(expected):
        key = "temperature_2m" if index == 0 else f"temperature_2m_member{index:02d}"
        values = hourly.get(key)
        if not isinstance(values, (list, tuple)) or len(values) != len(times):
            return []
        pairs: list[tuple[str, float]] = []
        for timestamp, raw in zip(times, values, strict=True):
            if raw is None or isinstance(raw, bool):
                return []
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return []
            if not math.isfinite(value):
                return []
            pairs.append((str(timestamp), value))
        vectors.append(
            Day0HourlyVector(
                model=model,
                city=str(getattr(city, "name", "") or ""),
                target_date="",
                timezone_name=str(getattr(city, "timezone")),
                captured_at=captured_at,
                times=tuple(timestamp for timestamp, _value in pairs),
                temps_c=tuple(value for _timestamp, value in pairs),
                source_run_meta_json=json.dumps(
                    source_meta_by_member[model],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
    return vectors


def fetch_day0_source_clock_ensemble_vectors(
    city: Any,
    *,
    now: Optional[datetime] = None,
    timeout_s: float = DEFAULT_FETCH_TIMEOUT_S,
) -> tuple[list[Day0HourlyVector], str]:
    """Fetch one possession-bracketed 51-member hourly ENS carrier.

    The provider metadata is read before and after the response.  A run change
    inside that bracket discards the payload, so a local fetch clock can never
    masquerade as provider-cycle identity.  The standard Ensemble API response
    is accepted only with that exact metadata bracket and is persisted through
    the same replayable request hash as deterministic Day0 paths.
    """

    from src.data.openmeteo_client import fetch as fetch_openmeteo
    from src.data.openmeteo_model_updates import fetch_model_updates
    from src.strategy.live_inference.source_clock_vnext import (
        source_publicly_usable_at,
    )

    decision_time = (now or datetime.now(UTC)).astimezone(UTC)
    captured_at = decision_time.isoformat()
    try:
        before_rows = fetch_model_updates(
            [DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL],
            timeout_seconds=max(0.25, float(timeout_s)),
            max_workers=1,
            priority=True,
        )
        if len(before_rows) != 1:
            return [], ""
        before = before_rows[0]
        if (
            before.last_run_modification_time is None
            or before.last_run_initialisation_time > decision_time
            or before.last_run_availability_time > decision_time
            or decision_time < source_publicly_usable_at(before.to_source_run_clock())
        ):
            return [], ""
        params = {
            "latitude": float(getattr(city, "lat")),
            "longitude": float(getattr(city, "lon")),
            "hourly": "temperature_2m",
            "models": DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
            "timezone": str(getattr(city, "timezone")),
            "forecast_hours": DAY0_HOURLY_FORECAST_HOURS,
            "temperature_unit": "celsius",
            "cell_selection": "land",
        }
        fetch_started = datetime.now(UTC)
        payload = fetch_openmeteo(
            OPENMETEO_ENSEMBLE_URL,
            params,
            timeout=max(0.25, float(timeout_s)),
            max_retries=1,
            endpoint_label="day0_source_clock_ensemble",
        )
        fetch_finished = datetime.now(UTC)
        after_rows = fetch_model_updates(
            [DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL],
            timeout_seconds=max(0.25, float(timeout_s)),
            max_workers=1,
            priority=True,
        )
        after = after_rows[0] if len(after_rows) == 1 else None
        if not _same_model_update(before, after):
            return [], ""
        request_hash = build_request_hash(
            endpoint=OPENMETEO_ENSEMBLE_URL,
            params={
                **params,
                "provider_run": before.last_run_initialisation_time.isoformat(),
            },
            models=[DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL],
            captured_at=captured_at,
            payload=payload,
        )
        member_meta: dict[str, Mapping[str, object]] = {}
        for model in day0_source_clock_ensemble_member_models():
            member_meta[model] = _day0_provider_run_meta(
                model=model,
                model_api_id=DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
                run=before.last_run_initialisation_time.astimezone(UTC),
                available_at=before.last_run_availability_time.astimezone(UTC),
                modified_at=before.last_run_modification_time.astimezone(UTC),
                authority="provider_meta_declared",
                endpoint_mode="ensemble_meta_stamped",
                request_params={
                    **params,
                    "endpoint": OPENMETEO_ENSEMBLE_URL,
                    "run": before.last_run_initialisation_time.isoformat(),
                },
                request_hash=request_hash,
                fetch_started_at=fetch_started,
                fetch_finished_at=fetch_finished,
            )
        vectors = parse_openmeteo_ensemble_hourly_payload(
            payload,
            city=city,
            captured_at=captured_at,
            source_meta_by_member=member_meta,
        )
        if len(vectors) != DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_COUNT:
            return [], ""
        return vectors, request_hash
    except Exception as exc:  # noqa: BLE001 - missing carrier leaves ENTRY fail-closed.
        logger.warning(
            "DAY0_SOURCE_CLOCK_ENSEMBLE_FETCH_FAILED city=%s exc=%s: %s",
            getattr(city, "name", "?"),
            type(exc).__name__,
            exc,
        )
        return [], ""


def parse_openmeteo_hourly_payload(
    payload: object,
    *,
    city: Any,
    models: list[str],
    captured_at: str,
    source_run_meta_json: str | None = None,
) -> list[Day0HourlyVector]:
    """Parse a (possibly multi-model) open-meteo hourly payload.

    Multi-model requests return either a list of per-model dicts or a single
    dict with suffixed keys (temperature_2m_<model>). Both shapes handled;
    target_date is stamped per-vector at read time (the vector spans 2 days).
    """
    tz_name = str(getattr(city, "timezone"))
    city_name = str(getattr(city, "name", "") or "")

    def _vector_from(hourly: dict, model: str, temp_key: str) -> Optional[Day0HourlyVector]:
        times = hourly.get("time")
        temps = hourly.get(temp_key)
        if not isinstance(times, (list, tuple)) or not isinstance(temps, (list, tuple)):
            return None
        pairs = [
            (str(t), float(v))
            for t, v in zip(times, temps)
            if v is not None and isinstance(v, (int, float))
        ]
        if not pairs:
            return None
        return Day0HourlyVector(
            model=model,
            city=city_name,
            target_date="",  # stamped per consumption window
            timezone_name=tz_name,
            captured_at=captured_at,
            times=tuple(t for t, _ in pairs),
            temps_c=tuple(v for _, v in pairs),
            source_run_meta_json=source_run_meta_json,
        )

    out: list[Day0HourlyVector] = []
    if isinstance(payload, list):
        for model, entry in zip(models, payload):
            if isinstance(entry, dict) and isinstance(entry.get("hourly"), dict):
                vector = _vector_from(entry["hourly"], model, "temperature_2m")
                if vector is not None:
                    out.append(vector)
        return out
    if isinstance(payload, dict) and isinstance(payload.get("hourly"), dict):
        hourly = payload["hourly"]
        for model in models:
            vector = _vector_from(hourly, model, f"temperature_2m_{model}")
            if vector is None and len(models) == 1:
                # single-model responses may omit the model suffix
                vector = _vector_from(hourly, model, "temperature_2m")
            if vector is not None:
                out.append(vector)
    return out


def persist_day0_hourly_vectors(
    vectors: list[Day0HourlyVector],
    *,
    target_date: str,
    conn: Optional[sqlite3.Connection] = None,
    request_hash: str,
    endpoint: str = OPENMETEO_FORECAST_URL,
    retention_days: float = DAY0_VECTOR_RETENTION_DAYS,
    now: Optional[datetime] = None,
    lock_blocking: bool = True,
) -> int:
    """Persist vectors (idempotent on (model,city,date,captured_at)) + prune.

    conn=None -> zeus-forecasts.db under db_writer_lock(LIVE) per INV-37; the
    connection is OPENED INSIDE the flock (lock-order hygiene: connection-open
    contention stays under the same writer lock — PR review PR#404 finding).

    request_hash is REQUIRED non-empty (PR#404 P1: rows feeding the
    remaining-day q must carry a replayable provenance identity; the table
    CHECK enforces the same on fresh DBs).

    ``now`` pins the retention-prune reference clock (the cutoff is
    ``now - retention_days``). Defaults to live wall-clock ``datetime.now(UTC)``
    so production behaviour is unchanged; tests inject it so a fixture with
    fixed captured_at timestamps is not pruned non-deterministically as real
    time advances past the retention window.
    """
    if not vectors:
        return 0
    if not str(request_hash or "").strip():
        raise ValueError(
            "persist_day0_hourly_vectors requires a non-empty request_hash "
            "(replayable provenance identity; see build_request_hash)"
        )
    own_conn = conn is None
    if own_conn:
        from src.state.db import ZEUS_FORECASTS_DB_PATH, get_forecasts_connection
        from src.state.db_writer_lock import WriteClass, db_writer_lock

        lock_ctx = db_writer_lock(
            ZEUS_FORECASTS_DB_PATH,
            WriteClass.LIVE,
            blocking=lock_blocking,
        )
    else:
        from contextlib import nullcontext

        lock_ctx = nullcontext()
    written = 0
    try:
        with lock_ctx:
            if own_conn:
                conn = get_forecasts_connection(write_class=WriteClass.LIVE)
            _ensure_schema(conn)
            for vector in vectors:
                if not _vector_covers_target_from_capture(
                    vector, target_date=target_date
                ):
                    logger.warning(
                        "DAY0_HOURLY_VECTOR_TARGET_COVERAGE_REJECTED "
                        "city=%s model=%s target_date=%s captured_at=%s",
                        vector.city,
                        vector.model,
                        target_date,
                        vector.captured_at,
                    )
                    continue
                row_id = _vector_id(vector.model, vector.city, target_date, vector.captured_at)
                row_endpoint = endpoint
                try:
                    source_meta = json.loads(str(vector.source_run_meta_json or ""))
                    if isinstance(source_meta, Mapping) and str(
                        source_meta.get("endpoint") or ""
                    ).strip():
                        row_endpoint = str(source_meta["endpoint"]).strip()
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO day0_hourly_vectors (
                        vector_id, model, city, target_date, timezone_name,
                        captured_at, provider, endpoint, request_hash,
                        times_json, temps_c_json, source_run_meta_json
                    ) VALUES (?, ?, ?, ?, ?, ?, 'openmeteo', ?, ?, ?, ?, ?)
                    """,
                    (
                        row_id, vector.model, vector.city, target_date,
                        vector.timezone_name, vector.captured_at, row_endpoint,
                        request_hash, json.dumps(list(vector.times)),
                        json.dumps(list(vector.temps_c)),
                        vector.source_run_meta_json,
                    ),
                )
                written += int(cur.rowcount or 0)
            prune_reference = (now or datetime.now(UTC)).astimezone(UTC)
            cutoff = prune_reference.timestamp() - retention_days * 86400.0
            cutoff_iso = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()
            conn.execute(
                "DELETE FROM day0_hourly_vectors WHERE captured_at < ?",
                (cutoff_iso,),
            )
            conn.commit()
    finally:
        # conn can be None when the connection-open itself failed inside the
        # flock — guard so the original exception is never masked (PR review
        # PR#404 finding).
        if own_conn and conn is not None:
            conn.close()
    return written


def _vector_covers_target_from_capture(
    vector: Day0HourlyVector,
    *,
    target_date: str,
) -> bool:
    """Require exact target-day support still usable at capture time."""

    try:
        target = date.fromisoformat(str(target_date)[:10])
        captured = datetime.fromisoformat(
            str(vector.captured_at).replace("Z", "+00:00")
        )
        tz = ZoneInfo(vector.timezone_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return False
    if captured.tzinfo is None:
        return False
    captured = captured.astimezone(UTC)
    captured_day = captured.astimezone(tz).date()
    if captured_day > target:
        return False
    boundary = (
        captured
        if captured_day == target
        else datetime.combine(target, datetime_time.min, tzinfo=tz)
    )
    return day0_hourly_vectors_cover_remaining_window(
        [vector],
        target_date=target_date,
        window_start=boundary,
    )


def select_ready_day0_hourly_vectors(
    vectors: Iterable[Day0HourlyVector],
    *,
    target_date: str,
    max_age_hours: float = DAY0_HOURLY_BUNDLE_MAX_AGE_HOURS,
    now: Optional[datetime] = None,
    expected_models: Optional[Iterable[str]] = None,
    require_expected: bool = False,
    max_bundle_skew_minutes: Optional[float] = None,
    remaining_window_start: datetime | None = None,
    require_complete_remaining_window: bool = False,
) -> list[Day0HourlyVector]:
    """Pure strict-bundle predicate shared by producer and live readers.

    It is intentionally the one place that decides freshness, expected-model
    completeness, capture skew, and remaining-window coverage.  The producer
    probes persisted readiness through ``read_freshest_day0_hourly_vectors``;
    health and money-path readers do the same, so a city cannot be prioritized
    by a weaker interpretation than the authority consumer accepts.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    expected: list[str] = []
    for model in expected_models or ():
        normalized = str(model or "").strip()
        if normalized and normalized not in expected:
            expected.append(normalized)
    expected_set = set(expected)

    parsed: list[tuple[datetime, Day0HourlyVector]] = []
    for vector in vectors:
        model = str(vector.model or "").strip()
        if not model or (expected_set and model not in expected_set):
            continue
        try:
            captured = datetime.fromisoformat(
                str(vector.captured_at).replace("Z", "+00:00")
            )
            if captured.tzinfo is None:
                continue
            captured = captured.astimezone(UTC)
            age_hours = (moment - captured).total_seconds() / 3600.0
        except (TypeError, ValueError):
            continue
        if age_hours > float(max_age_hours) or age_hours < 0.0:
            continue
        if (
            require_complete_remaining_window
            and (
                remaining_window_start is None
                or not day0_hourly_vectors_cover_remaining_window(
                    [vector],
                    target_date=target_date,
                    window_start=remaining_window_start,
                )
            )
        ):
            continue
        parsed.append((captured, vector))

    freshest: dict[str, Day0HourlyVector] = {}
    for _captured, vector in sorted(parsed, key=lambda item: item[0], reverse=True):
        freshest.setdefault(str(vector.model), vector)
    if require_expected and expected and any(model not in freshest for model in expected):
        return []
    if (
        require_expected
        and expected
        and max_bundle_skew_minutes is not None
        and all(model in freshest for model in expected)
    ):
        captured_times: list[datetime] = []
        try:
            for model in expected:
                captured = datetime.fromisoformat(
                    str(freshest[model].captured_at).replace("Z", "+00:00")
                )
                if captured.tzinfo is None:
                    return []
                captured_times.append(captured.astimezone(UTC))
        except (TypeError, ValueError):
            return []
        if (
            max(captured_times) - min(captured_times)
        ).total_seconds() / 60.0 > float(max_bundle_skew_minutes):
            return []
    selected = (
        [freshest[model] for model in expected if model in freshest]
        if expected
        else list(freshest.values())
    )
    if require_complete_remaining_window and (
        remaining_window_start is None
        or not day0_hourly_vectors_cover_remaining_window(
            selected,
            target_date=target_date,
            window_start=remaining_window_start,
        )
    ):
        return []
    return selected


def read_freshest_day0_hourly_vectors(
    *,
    city: str,
    target_date: str,
    max_age_hours: float = DAY0_HOURLY_BUNDLE_MAX_AGE_HOURS,
    now: Optional[datetime] = None,
    conn: Optional[sqlite3.Connection] = None,
    expected_models: Optional[Iterable[str]] = None,
    require_expected: bool = False,
    max_bundle_skew_minutes: Optional[float] = None,
    remaining_window_start: datetime | None = None,
    require_complete_remaining_window: bool = False,
    raise_on_db_error: bool = False,
) -> list[Day0HourlyVector]:
    """Freshest persisted vector per model for (city, target_date).

    Vectors older than max_age_hours are EXCLUDED (a stale high-res run must
    not masquerade as the current remaining-day distribution — fail-closed to
    the legacy full-day path instead).

    ``expected_models`` lets live consumers define the complete bundle they are
    willing to treat as same-day authority. With ``require_expected=True``, any
    missing expected model returns [] so a partial single-model regional vector
    cannot sponsor a live decision. ``max_bundle_skew_minutes`` additionally
    prevents mixing a fresh model row with a materially older row from another
    model as one live authority bundle. Live probability consumers set
    ``require_complete_remaining_window`` and provide their causal boundary;
    every model must then contain each hourly grid point from that boundary to
    local-day end. A partial future path is not probability authority.
    Producer readiness probes set ``raise_on_db_error`` so an unreadable store
    cannot be misclassified as a proved, normally missing bundle.
    """
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_forecasts_connection_read_only

        conn = get_forecasts_connection_read_only()
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    oldest_capture = moment - timedelta(hours=float(max_age_hours))
    try:
        try:
            rows = conn.execute(
                """
                SELECT model, city, target_date, timezone_name, captured_at,
                       times_json, temps_c_json
                FROM day0_hourly_vectors
                WHERE city = ? AND target_date = ?
                  AND julianday(captured_at)
                      BETWEEN julianday(?) AND julianday(?)
                ORDER BY captured_at DESC
                """,
                (
                    str(city),
                    str(target_date),
                    oldest_capture.isoformat(),
                    moment.isoformat(),
                ),
            ).fetchall()
        except sqlite3.Error:
            if raise_on_db_error:
                raise
            return []
        candidates: list[Day0HourlyVector] = []
        for row in rows:
            model = str(row[0])
            try:
                times = tuple(str(t) for t in json.loads(row[5]))
                temps = tuple(float(v) for v in json.loads(row[6]))
                if not times or len(times) != len(temps):
                    continue
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            candidate = Day0HourlyVector(
                model=model, city=str(row[1]), target_date=str(row[2]),
                timezone_name=str(row[3]), captured_at=str(row[4]),
                times=times, temps_c=temps,
            )
            candidates.append(candidate)
        return select_ready_day0_hourly_vectors(
            candidates,
            target_date=target_date,
            max_age_hours=max_age_hours,
            now=moment,
            expected_models=expected_models,
            require_expected=require_expected,
            max_bundle_skew_minutes=max_bundle_skew_minutes,
            remaining_window_start=remaining_window_start,
            require_complete_remaining_window=require_complete_remaining_window,
        )
    finally:
        if own_conn:
            conn.close()


@lru_cache(maxsize=256)
def _target_day_hour_grid_utc(*, target: date, tz: ZoneInfo) -> tuple[datetime, ...]:
    """UTC instants for every local hourly grid point, including DST folds."""

    start = datetime.combine(target, datetime_time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(
        target + timedelta(days=1), datetime_time.min, tzinfo=tz
    ).astimezone(UTC)
    out: list[datetime] = []
    cursor = start
    while cursor < end:
        out.append(cursor)
        cursor += timedelta(hours=1)
    return tuple(out)


def day0_hourly_vector_target_values_utc(
    vector: Day0HourlyVector,
    *,
    target: date,
    tz: ZoneInfo,
) -> tuple[tuple[datetime, float], ...] | None:
    """Map one provider-local target-day vector to exact UTC instants."""

    grid = _target_day_hour_grid_utc(target=target, tz=tz)
    by_label: dict[str, list[datetime]] = {}
    for instant in grid:
        label = instant.astimezone(tz).strftime("%Y-%m-%dT%H:%M")
        by_label.setdefault(label, []).append(instant)
    label_uses: Counter[str] = Counter()
    seen_instants: set[datetime] = set()
    values: list[tuple[datetime, float]] = []
    for raw_time, temp in zip(vector.times, vector.temps_c):
        try:
            parsed = datetime.fromisoformat(str(raw_time))
            value = float(temp)
        except (TypeError, ValueError):
            return None
        local = (
            parsed.replace(tzinfo=tz)
            if parsed.tzinfo is None
            else parsed.astimezone(tz)
        )
        if local.date() != target:
            continue
        if (
            not math.isfinite(value)
            or local.minute != 0
            or local.second != 0
            or local.microsecond != 0
        ):
            return None
        if parsed.tzinfo is None:
            label = local.strftime("%Y-%m-%dT%H:%M")
            choices = by_label.get(label, [])
            use_index = label_uses[label]
            if use_index >= len(choices):
                return None
            instant = choices[use_index]
            label_uses[label] += 1
        else:
            instant = parsed.astimezone(UTC)
            if instant not in grid:
                return None
        if instant in seen_instants:
            return None
        seen_instants.add(instant)
        values.append((instant, value))
    return tuple(values)


def align_day0_hourly_vectors_on_common_causal_grid(
    vectors: Iterable[Day0HourlyVector],
    *,
    target_date: str,
    window_start: datetime,
) -> tuple[tuple[datetime, ...], tuple[tuple[float, ...], ...]] | None:
    """Align a complete provider bundle on one exact UTC causal grid.

    Provider runs can expose different *elapsed* prefixes (for example
    ``24/21/24`` target-day rows) while still sharing the complete stochastic
    suffix that begins at the current observation boundary.  The live
    consumers need a rectangular matrix, so this helper keeps only the exact
    UTC instants common to every provider: the latest hourly anchor at or
    before ``window_start`` and every target-day grid point after it.

    This is an alignment operation, not a resampler: no timestamp or value is
    fabricated.  The per-provider causal coverage gate runs first, then the
    common grid is checked again.  A missing causal hour, a missing <=1-hour
    anchor, timezone mismatch, duplicate/non-finite timestamp, or invalid DST
    shape returns ``None``.  In particular, a future target-day bundle whose
    provider omits midnight hours remains unavailable; a current-day prefix
    must never relax that contract.
    """
    bundle = tuple(vectors)
    if not bundle or window_start.tzinfo is None:
        return None
    try:
        target = date.fromisoformat(str(target_date)[:10])
    except (TypeError, ValueError):
        return None
    if not day0_hourly_vectors_cover_remaining_window(
        list(bundle), target_date=target_date, window_start=window_start
    ):
        return None

    timezone_name = str(bundle[0].timezone_name or "").strip()
    if not timezone_name:
        return None
    try:
        timezone_obj = ZoneInfo(timezone_name)
    except (TypeError, ZoneInfoNotFoundError):
        return None
    for vector in bundle[1:]:
        if str(vector.timezone_name or "").strip() != timezone_name:
            return None

    target_grid = _target_day_hour_grid_utc(target=target, tz=timezone_obj)
    if not target_grid:
        return None
    boundary_utc = window_start.astimezone(UTC)
    anchor_candidates = [instant for instant in target_grid if instant <= boundary_utc]
    if not anchor_candidates:
        return None
    causal_anchor = anchor_candidates[-1]
    if not timedelta(0) <= boundary_utc - causal_anchor <= timedelta(hours=1):
        return None
    causal_grid = tuple(instant for instant in target_grid if instant >= causal_anchor)
    if not causal_grid:
        return None

    aligned_rows: list[tuple[float, ...]] = []
    for vector in bundle:
        values = day0_hourly_vector_target_values_utc(
            vector, target=target, tz=timezone_obj
        )
        if values is None:
            return None
        by_instant: dict[datetime, float] = {}
        for instant, value in values:
            if instant in by_instant or not math.isfinite(float(value)):
                return None
            by_instant[instant] = float(value)
        if any(instant not in by_instant for instant in causal_grid):
            return None
        aligned_rows.append(tuple(by_instant[instant] for instant in causal_grid))
    if not aligned_rows or any(len(row) != len(causal_grid) for row in aligned_rows):
        return None
    return causal_grid, tuple(aligned_rows)


def day0_hourly_vectors_cover_remaining_window(
    vectors: list[Day0HourlyVector],
    *,
    target_date: str,
    window_start: datetime,
) -> bool:
    """Prove every model covers the causal boundary through local-day end.

    Open-Meteo serves a local hourly grid. Expected instants are generated in
    UTC and provider-local duplicate labels are assigned in chronological order,
    so 23/25-hour DST days remain exact even when timestamps omit offsets. When
    the causal boundary is inside the terminal sub-hour, the final elapsed grid
    point is required as the interval anchor instead of pretending that an empty
    future grid is complete.
    """

    if not vectors or window_start.tzinfo is None:
        return False
    try:
        target = date.fromisoformat(str(target_date)[:10])
    except ValueError:
        return False
    boundary_utc = window_start.astimezone(UTC)
    for vector in vectors:
        try:
            tz = ZoneInfo(vector.timezone_name)
        except Exception:
            return False
        boundary_local = boundary_utc.astimezone(tz)
        if boundary_local.date() != target:
            return False
        grid = _target_day_hour_grid_utc(target=target, tz=tz)
        values = day0_hourly_vector_target_values_utc(
            vector,
            target=target,
            tz=tz,
        )
        if not grid or values is None:
            return False
        counts = Counter(instant for instant, _value in values)
        required = tuple(instant for instant in grid if instant >= boundary_utc)
        if required:
            if any(counts[instant] != 1 for instant in required):
                return False
            continue
        final_grid = grid[-1]
        if (
            counts[final_grid] != 1
            or not timedelta(0) <= boundary_utc - final_grid <= timedelta(hours=1)
        ):
            return False
    return True


def remaining_day_extremes_c(
    vectors: list[Day0HourlyVector],
    *,
    target_date: str,
    now: datetime,
    metric: str,
    window_start: datetime | None = None,
) -> list[float]:
    """Per-model extreme over the target-day interval not yet observed.

    ``now`` is the decision/freshness cut. ``window_start`` is the latest causal
    observation time and defaults to ``now``. Grid points at/after that boundary
    are ordinary support. During the terminal sub-hour, the final elapsed hourly
    point remains the interval anchor for at most one hour. This preserves an
    unobserved target-day tail after local midnight without reopening hours that
    canonical observations already cover.
    """
    if metric not in {"high", "low"}:
        raise ValueError(f"unsupported metric: {metric}")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    start = window_start or now
    if start.tzinfo is None:
        raise ValueError("window_start must be timezone-aware")
    if start.astimezone(UTC) > now.astimezone(UTC):
        raise ValueError("window_start cannot be after now")
    target = date.fromisoformat(str(target_date)[:10])
    if not day0_hourly_vectors_cover_remaining_window(
        vectors,
        target_date=target_date,
        window_start=start,
    ):
        return []
    out: list[float] = []
    start_utc = start.astimezone(UTC)
    for vector in vectors:
        try:
            tz = ZoneInfo(vector.timezone_name)
        except Exception:
            continue
        start_local = start.astimezone(tz)
        if start_local.date() != target:
            continue
        target_values = day0_hourly_vector_target_values_utc(
            vector,
            target=target,
            tz=tz,
        )
        if target_values is None:
            return []
        values: list[float] = []
        elapsed_target_points: list[tuple[datetime, float]] = []
        for instant, temp in target_values:
            if instant < start_utc:
                elapsed_target_points.append((instant, float(temp)))
                continue
            values.append(float(temp))
        if (
            not values
            and elapsed_target_points
        ):
            local_day_end = datetime.combine(
                target + timedelta(days=1),
                datetime.min.time(),
                tzinfo=tz,
            )
            anchor_time, anchor_temp = max(
                elapsed_target_points,
                key=lambda item: item[0],
            )
            anchor_age = start_utc - anchor_time
            time_to_day_end = local_day_end.astimezone(UTC) - start_utc
            if (
                timedelta(0) < time_to_day_end <= timedelta(hours=1)
                and timedelta(0) <= anchor_age <= timedelta(hours=1)
            ):
                values.append(anchor_temp)
        if not values:
            continue
        out.append(max(values) if metric == "high" else min(values))
    return out


# ---------------------------------------------------------------------------
# Throttled refresh hook (wired from the day0 emit cycle; NO daemon restart
# needed for the schema — table is created on first write).
# ---------------------------------------------------------------------------

_REFRESH_LOCK = threading.Lock()
_LAST_REFRESH_MONOTONIC: dict[str, float] = {}
_INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC: dict[str, float] = {}
_INCOMPLETE_RETRY_STREAK: dict[str, int] = {}


def _refresh_throttled_locked(
    refresh_key: str,
    *,
    now_monotonic: float,
    interval_s: float,
    bypass_interval: bool = False,
) -> bool:
    """Return whether refresh is throttled while ``_REFRESH_LOCK`` is held."""

    retry_not_before = _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.get(refresh_key)
    if retry_not_before is not None:
        if now_monotonic < retry_not_before:
            return True
        _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.pop(refresh_key, None)
    if bypass_interval:
        return False
    last = _LAST_REFRESH_MONOTONIC.get(refresh_key)
    return last is not None and now_monotonic - last < float(interval_s)


def maybe_refresh_day0_hourly_vectors(
    cities: list[Any],
    *,
    decision_time: datetime,
    interval_s: float = DEFAULT_REFRESH_INTERVAL_S,
    budget_s: float = DEFAULT_REFRESH_BUDGET_S,
    max_cities: int = DEFAULT_REFRESH_MAX_CITIES,
    timeout_s: float = DEFAULT_FETCH_TIMEOUT_S,
    quota_critical_cities: int = 0,
    quota_priority_cities: int = 0,
    allow_priority_recovery: bool = False,
    remaining_window_starts: Mapping[tuple[str, str], datetime] | None = None,
    provider_run_hwm: Mapping[str, Day0ProviderRunHwm] | None = None,
    release_due_city_dates: Iterable[tuple[str, str]] = (),
    persist_lock_blocking: bool = True,
    return_stats: bool = False,
) -> int | Day0HourlyRefreshStats:
    """Throttled per-city fetch+persist of the freshest high-res hourly curves.

    Cities with an in-domain regional high-res model use that regional source;
    other cities use the ECMWF IFS global fallback from
    ``day0_hourly_models_for_city``. One open-meteo call per city per interval.
    Fail-soft per city. A maintenance fetch failure retains the normal refresh
    interval so a provider outage cannot turn the 45-second scheduler into a
    quota-consuming retry storm. Missing-authority priority and held-capital
    failures instead use the same bounded retry debt as incomplete bundles, so
    a recovered provider cannot leave current probability dark for the full
    normal interval. An incomplete fetch is never persisted as a partial live
    bundle. The ordered critical prefix may consume only the final held-position
    reserve; the following priority prefix may consume the source-clock reserve
    but never the critical reserve. All remaining cities stay in maintenance
    quota.  When explicitly authorized, a priority city may use the bounded
    recovery lane after ordinary priority quota is exhausted.  That lane is
    capped below the critical limits, preserving a hard held-capital floor.
    """
    if decision_time.tzinfo is None:
        raise ValueError("decision_time must be timezone-aware")
    from src.data.bayes_precision_fusion_download import (
        bayes_precision_fusion_held_quota_priority,
        bayes_precision_fusion_recovery_quota_priority,
        bayes_precision_fusion_source_clock_quota_priority,
    )

    def strict_window_start(city: Any, target_date: str) -> datetime | None:
        explicit = (remaining_window_starts or {}).get(
            (str(getattr(city, "name", "") or ""), target_date)
        )
        if explicit is not None:
            if explicit.tzinfo is None or explicit > decision_time:
                return None
            return explicit.astimezone(UTC)
        try:
            tz = ZoneInfo(str(getattr(city, "timezone")))
            target = date.fromisoformat(target_date)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            return None
        local_day = decision_time.astimezone(tz).date()
        if target < local_day:
            return None
        if target == local_day:
            return decision_time.astimezone(UTC)
        return datetime.combine(target, datetime_time.min, tzinfo=tz).astimezone(UTC)

    def mark_incomplete(
        *,
        refresh_key: str,
        quota_lane: str,
        name: str,
        target_dates: tuple[str, ...],
        expected_models: tuple[str, ...],
        available_models: tuple[str, ...],
        missing_models: tuple[str, ...],
        reason: str,
    ) -> None:
        nonlocal incomplete_expected_bundles
        incomplete_expected_bundles += 1
        unavailable_bundles.append(
            Day0HourlyBundleUnavailable(
                city=name,
                target_dates=target_dates,
                expected_models=expected_models,
                available_models=available_models,
                missing_models=missing_models,
                reason=reason,
            )
        )
        with _REFRESH_LOCK:
            _LAST_REFRESH_MONOTONIC.pop(refresh_key, None)
            streak = _INCOMPLETE_RETRY_STREAK.get(refresh_key, 0) + 1
            _INCOMPLETE_RETRY_STREAK[refresh_key] = streak
            retry_cap_s = (
                INCOMPLETE_BUNDLE_CRITICAL_RETRY_MAX_INTERVAL_S
                if quota_lane in {"critical", "priority"}
                else INCOMPLETE_BUNDLE_RETRY_MAX_INTERVAL_S
            )
            max_exponent = max(
                0,
                int(
                    math.ceil(
                        math.log2(
                            retry_cap_s / INCOMPLETE_BUNDLE_RETRY_INTERVAL_S
                        )
                    )
                ),
            )
            retry_delay_s = min(
                retry_cap_s,
                INCOMPLETE_BUNDLE_RETRY_INTERVAL_S
                * (2 ** min(streak - 1, max_exponent)),
            )
            _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC[refresh_key] = (
                time.monotonic() + retry_delay_s
            )

    written = 0
    skipped_throttle = 0
    skipped_quota = 0
    incomplete_expected_bundles = 0
    unavailable_bundles: list[Day0HourlyBundleUnavailable] = []
    priority_reserve_exhausted = False
    budget_exhausted = False
    now_monotonic = time.monotonic()
    started_monotonic = now_monotonic
    checked = 0
    release_due_scopes = frozenset(
        (str(city).strip(), str(target_date).strip())
        for city, target_date in release_due_city_dates
    )
    for city_index, city in enumerate(cities):
        if checked >= max(0, int(max_cities)):
            break
        if budget_s > 0.0 and checked > 0 and (time.monotonic() - started_monotonic) >= budget_s:
            budget_exhausted = True
            logger.warning(
                "DAY0_HOURLY_VECTORS_REFRESH_BUDGET_EXHAUSTED checked=%d budget_s=%.3f",
                checked,
                budget_s,
            )
            break
        name = str(getattr(city, "name", "") or "")
        if not name:
            continue
        try:
            target_dates = day0_hourly_target_dates_for_refresh(
                city=city, decision_time=decision_time
            )
            refresh_key = f"{name}|{target_dates[0]}"
            models = day0_hourly_models_for_city(city)
            if not models:
                continue
            required_hwm = {
                model: provider_run_hwm[model]
                for model in models
                if provider_run_hwm is not None and model in provider_run_hwm
            }
            release_due = (
                (name, target_dates[0]) in release_due_scopes and bool(required_hwm)
            )
            critical_city_count = max(0, int(quota_critical_cities))
            priority_city_count = max(0, int(quota_priority_cities))
            if city_index < critical_city_count:
                quota_lane = "critical"
                quota_context = quota_tracker.critical_lane()
                transport_quota_context = (
                    bayes_precision_fusion_held_quota_priority()
                )
            elif city_index < critical_city_count + priority_city_count:
                quota_lane = "priority"
                if allow_priority_recovery:
                    with quota_tracker.priority_lane():
                        priority_available = quota_tracker.can_call()
                    if not priority_available:
                        quota_lane = "recovery"
                quota_context = (
                    quota_tracker.recovery_lane()
                    if quota_lane == "recovery"
                    else quota_tracker.priority_lane()
                )
                transport_quota_context = (
                    bayes_precision_fusion_recovery_quota_priority()
                    if quota_lane == "recovery"
                    else bayes_precision_fusion_source_clock_quota_priority()
                )
            else:
                quota_lane = "maintenance"
                quota_context = nullcontext()
                transport_quota_context = nullcontext()
            ensemble_target_dates = (
                day0_source_clock_ensemble_target_dates(
                    city=city,
                    decision_time=decision_time,
                )
                if quota_lane in {"priority", "recovery"}
                else ()
            )
            ensemble_vectors: list[Day0HourlyVector] = []
            ensemble_request_hash = ""
            with _REFRESH_LOCK:
                retry_not_before = _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.get(
                    refresh_key
                )
                if quota_lane == "critical" and retry_not_before is not None:
                    _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC[refresh_key] = min(
                        retry_not_before,
                        now_monotonic
                        + INCOMPLETE_BUNDLE_CRITICAL_RETRY_MAX_INTERVAL_S,
                    )
                if _refresh_throttled_locked(
                    refresh_key,
                    now_monotonic=now_monotonic,
                    interval_s=interval_s,
                    bypass_interval=release_due,
                ):
                    skipped_throttle += 1
                    continue
            # The hourly builder delegates exact-run transport to the BPF
            # module, which owns a separate process-local tracker instance over
            # the same durable quota file.  Carry the selected economic lane to
            # both trackers; otherwise priority/recovery work is silently
            # reclassified as maintenance at the HTTP reservation boundary.
            with quota_context, transport_quota_context:
                if not quota_tracker.can_call():
                    skipped_quota += 1
                    if quota_lane in {"priority", "recovery"}:
                        priority_reserve_exhausted = True
                        logger.error(
                            "DAY0_HOURLY_PRIORITY_RECOVERY_EXHAUSTED "
                            "city=%s checked=%d lane=%s; held reserve preserved",
                            name,
                            checked,
                            quota_lane,
                        )
                    break
                with _REFRESH_LOCK:
                    if _refresh_throttled_locked(
                        refresh_key,
                        now_monotonic=now_monotonic,
                        interval_s=interval_s,
                        bypass_interval=release_due,
                    ):
                        skipped_throttle += 1
                        continue
                    _LAST_REFRESH_MONOTONIC[refresh_key] = now_monotonic
                checked += 1
                try:
                    vectors, request_hash = fetch_day0_hourly_vectors(
                        city, models=models, now=decision_time, timeout_s=timeout_s
                    )
                except TypeError as exc:
                    if "timeout_s" not in str(exc):
                        raise
                    vectors, request_hash = fetch_day0_hourly_vectors(
                        city, models=models, now=decision_time
                    )
                if ensemble_target_dates:
                    ensemble_vectors, ensemble_request_hash = (
                        fetch_day0_source_clock_ensemble_vectors(
                            city,
                            now=decision_time,
                            timeout_s=timeout_s,
                        )
                    )
            expected_models = tuple(dict.fromkeys(str(model) for model in models))
            vector_models = tuple(dict.fromkeys(str(vector.model) for vector in vectors))
            missing_models = tuple(
                model for model in expected_models if model not in vector_models
            )
            if not vectors or not request_hash:
                if quota_lane in {"critical", "priority"}:
                    mark_incomplete(
                        refresh_key=refresh_key,
                        quota_lane=quota_lane,
                        name=name,
                        target_dates=target_dates,
                        expected_models=expected_models,
                        available_models=vector_models,
                        missing_models=missing_models or expected_models,
                        reason="DAY0_HOURLY_BUNDLE_FETCH_UNAVAILABLE",
                    )
                else:
                    unavailable_bundles.append(
                        Day0HourlyBundleUnavailable(
                            city=name,
                            target_dates=target_dates,
                            expected_models=expected_models,
                            available_models=vector_models,
                            missing_models=missing_models or expected_models,
                            reason="DAY0_HOURLY_BUNDLE_FETCH_UNAVAILABLE",
                        )
                    )
                continue
            if missing_models:
                mark_incomplete(
                    refresh_key=refresh_key,
                    quota_lane=quota_lane,
                    name=name,
                    target_dates=target_dates,
                    expected_models=expected_models,
                    available_models=vector_models,
                    missing_models=missing_models,
                    reason="DAY0_HOURLY_BUNDLE_INCOMPLETE",
                )
                continue
            trailing_hwm_models = (
                _vectors_trailing_provider_hwm(vectors, required_hwm=required_hwm)
                if release_due
                else ()
            )
            if trailing_hwm_models:
                mark_incomplete(
                    refresh_key=refresh_key,
                    quota_lane=quota_lane,
                    name=name,
                    target_dates=target_dates,
                    expected_models=expected_models,
                    available_models=vector_models,
                    missing_models=trailing_hwm_models,
                    reason="DAY0_PROVIDER_RUN_HWM_NOT_CAPTURED",
                )
                continue

            strict_bundles: dict[str, tuple[datetime, list[Day0HourlyVector]]] = {}
            for target_date in target_dates:
                window_start = strict_window_start(city, target_date)
                selected = select_ready_day0_hourly_vectors(
                    vectors,
                    target_date=target_date,
                    now=decision_time,
                    expected_models=expected_models,
                    require_expected=True,
                    max_bundle_skew_minutes=DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES,
                    remaining_window_start=window_start,
                    require_complete_remaining_window=True,
                )
                if window_start is None or not selected:
                    mark_incomplete(
                        refresh_key=refresh_key,
                        quota_lane=quota_lane,
                        name=name,
                        target_dates=target_dates,
                        expected_models=expected_models,
                        available_models=vector_models,
                        missing_models=(),
                        reason="DAY0_HOURLY_BUNDLE_REMAINING_WINDOW_INCOMPLETE",
                    )
                    strict_bundles.clear()
                    break
                strict_bundles[target_date] = (window_start, selected)
            if not strict_bundles:
                continue

            persisted = 0
            for target_date in target_dates:
                persisted += persist_day0_hourly_vectors(
                    strict_bundles[target_date][1],
                    target_date=target_date,
                    request_hash=request_hash,
                    lock_blocking=persist_lock_blocking,
                )
            if ensemble_target_dates:
                ensemble_expected = day0_source_clock_ensemble_member_models()
                for target_date in ensemble_target_dates:
                    window_start = strict_window_start(city, target_date)
                    selected_ensemble = (
                        select_ready_day0_hourly_vectors(
                            ensemble_vectors,
                            target_date=target_date,
                            now=decision_time,
                            expected_models=ensemble_expected,
                            require_expected=True,
                            max_bundle_skew_minutes=(
                                DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES
                            ),
                            remaining_window_start=window_start,
                            require_complete_remaining_window=True,
                        )
                        if window_start is not None
                        and ensemble_request_hash
                        and len(ensemble_vectors)
                        == DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_COUNT
                        else []
                    )
                    if not selected_ensemble:
                        logger.warning(
                            "DAY0_SOURCE_CLOCK_ENSEMBLE_BUNDLE_UNAVAILABLE "
                            "city=%s target_date=%s available=%d expected=%d",
                            name,
                            target_date,
                            len(ensemble_vectors),
                            DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_COUNT,
                        )
                        continue
                    persisted += persist_day0_hourly_vectors(
                        selected_ensemble,
                        target_date=target_date,
                        request_hash=ensemble_request_hash,
                        endpoint=OPENMETEO_ENSEMBLE_URL,
                        lock_blocking=persist_lock_blocking,
                    )
            drained = all(
                read_freshest_day0_hourly_vectors(
                    city=name,
                    target_date=target_date,
                    now=decision_time,
                    expected_models=expected_models,
                    require_expected=True,
                    max_bundle_skew_minutes=DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES,
                    remaining_window_start=window_start,
                    require_complete_remaining_window=True,
                )
                for target_date, (window_start, _selected) in strict_bundles.items()
            )
            if not drained:
                mark_incomplete(
                    refresh_key=refresh_key,
                    quota_lane=quota_lane,
                    name=name,
                    target_dates=target_dates,
                    expected_models=expected_models,
                    available_models=vector_models,
                    missing_models=(),
                    reason="DAY0_HOURLY_BUNDLE_PERSIST_READBACK_INCOMPLETE",
                )
                continue
            written += persisted
            with _REFRESH_LOCK:
                _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.pop(refresh_key, None)
                _INCOMPLETE_RETRY_STREAK.pop(refresh_key, None)
        except Exception as exc:  # noqa: BLE001 — one city must not kill the pass
            if isinstance(exc, BlockingIOError):
                with _REFRESH_LOCK:
                    _LAST_REFRESH_MONOTONIC.pop(locals().get("refresh_key", name), None)
            logger.warning(
                "DAY0_HOURLY_VECTORS_REFRESH_FAILED city=%s exc=%s: %s",
                name, type(exc).__name__, exc,
            )
    stats = Day0HourlyRefreshStats(
        vectors_written=written,
        cities_attempted=checked,
        cities_skipped_throttle=skipped_throttle,
        cities_skipped_quota=skipped_quota,
        incomplete_expected_bundles=incomplete_expected_bundles,
        unavailable_bundles=tuple(unavailable_bundles),
        priority_reserve_exhausted=priority_reserve_exhausted,
        budget_exhausted=budget_exhausted,
    )
    return stats if return_stats else stats.vectors_written
