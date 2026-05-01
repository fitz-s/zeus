"""Open-Meteo Ensemble API client.

Fetches ECMWF IFS 51-member and GFS 31-member ensemble forecasts.
Returns raw hourly temperature arrays for signal generation.

API: https://ensemble-api.open-meteo.com/v1/ensemble
Free tier: 10,000 calls/day, no API key required.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Optional

import httpx
import numpy as np

from src.config import City, ensemble_member_count
from src.data.forecast_source_registry import (
    ForecastSourceRole,
    gate_source,
    gate_source_role,
    source_id_for_ensemble_model,
    stable_payload_hash,
)
from src.data.openmeteo_quota import quota_tracker


API_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

# Retry config per CLAUDE.md: retry 3× with 10s backoff
MAX_RETRIES = 3
RETRY_BACKOFF_S = 10.0
CACHE_TTL_SECONDS = 15 * 60
_ENSEMBLE_CACHE: dict[tuple[str, float, float, str, str, int, ForecastSourceRole], dict] = {}


def _cache_key(
    city: City,
    model: str,
    past_days: int = 0,
    role: ForecastSourceRole = "entry_primary",
) -> tuple[str, float, float, str, str, int, ForecastSourceRole]:
    return (
        city.name,
        float(city.lat),
        float(city.lon),
        city.settlement_unit,
        model,
        past_days,
        role,
    )


def _clone_result(result: dict) -> dict:
    cloned = dict(result)
    if "members_hourly" in cloned:
        cloned["members_hourly"] = np.array(cloned["members_hourly"], copy=True)
    if "times" in cloned:
        cloned["times"] = list(cloned["times"])
    return cloned


def _parse_timestamp_as_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fetch_ensemble(
    city: City,
    forecast_days: int = 8,
    model: str = "ecmwf_ifs025",
    past_days: int = 0,
    role: ForecastSourceRole = "entry_primary",
) -> Optional[dict]:  # Spec §2.1
    """Fetch ensemble forecast from Open-Meteo.

    Returns dict with:
        members_hourly: np.ndarray shape (n_members, hours) in city's settlement unit
        issue_time: datetime | None (UTC, if upstream exposes true cycle issue time)
        first_valid_time: datetime (UTC forecast-window start from payload)
        fetch_time: datetime (UTC)
        model: str
        n_members: int

    Returns None if all retries fail.
    """
    source_id = source_id_for_ensemble_model(model)
    source_spec = gate_source(source_id)
    gate_source_role(source_spec, role)
    if source_spec.ingest_class is not None:
        return _fetch_registered_ingest_ensemble(
            city,
            forecast_days=forecast_days,
            model=model,
            past_days=past_days,
            role=role,
            ingest_class=source_spec.ingest_class,
        )
    temp_unit = "fahrenheit" if city.settlement_unit == "F" else "celsius"

    params = {
        "latitude": city.lat,
        "longitude": city.lon,
        "hourly": "temperature_2m",
        "models": model,
        "forecast_days": forecast_days,
        "temperature_unit": temp_unit,
    }
    if past_days > 0:
        params["past_days"] = past_days

    fetch_time = datetime.now(timezone.utc)
    last_error = None
    cache_key = _cache_key(city, model, past_days, role)
    cached = _ENSEMBLE_CACHE.get(cache_key)
    if cached is not None:
        age_seconds = (fetch_time - cached["fetch_time"]).total_seconds()
        cached_days = int(cached.get("forecast_days", 0))
        if age_seconds <= CACHE_TTL_SECONDS and cached_days >= int(forecast_days):
            return _clone_result(cached)

    for attempt in range(MAX_RETRIES):
        try:
            if not quota_tracker.can_call():
                print("  WARN Open-Meteo quota blocked ensemble request")
                return None
            resp = httpx.get(API_URL, params=params, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
            quota_tracker.record_call("ensemble")
            parsed = _parse_response(
                data,
                model,
                fetch_time,
                source_id=source_spec.source_id,
                authority_tier=source_spec.authority_tier,
                degradation_level=source_spec.degradation_level,
                forecast_source_role=role,
            )
            parsed["forecast_days"] = int(forecast_days)
            _ENSEMBLE_CACHE[cache_key] = parsed
            return _clone_result(parsed)
        except (httpx.HTTPError, KeyError, ValueError) as e:
            last_error = e
            if isinstance(e, httpx.HTTPStatusError) and e.response is not None and e.response.status_code == 429:
                retry_after = e.response.headers.get("Retry-After")
                try:
                    retry_after_seconds = int(retry_after) if retry_after else None
                except ValueError:
                    retry_after_seconds = None
                quota_tracker.note_rate_limited(retry_after_seconds)
            if attempt < MAX_RETRIES - 1:
                import time
                time.sleep(RETRY_BACKOFF_S)

    # All retries exhausted — return None (caller decides: skip market this cycle)
    print(f"  WARN ensemble fetch failed after {MAX_RETRIES} retries: {last_error}")
    return None


def _fetch_registered_ingest_ensemble(
    city: City,
    *,
    forecast_days: int,
    model: str,
    past_days: int,
    role: ForecastSourceRole,
    ingest_class,
) -> Optional[dict]:
    """Fetch an operator-gated registered ingest source without Open-Meteo.

    This keeps TIGGE switch-only wiring dormant behind the forecast-source
    registry. Gate-closed TIGGE fails before this function is reached; gate-open
    TIGGE reads only the operator-approved local payload configured on the
    ingest adapter.
    """

    fetch_time = datetime.now(timezone.utc)
    cache_key = _cache_key(city, model, past_days, role)
    cached = _ENSEMBLE_CACHE.get(cache_key)
    if cached is not None:
        age_seconds = (fetch_time - cached["fetch_time"]).total_seconds()
        cached_days = int(cached.get("forecast_days", 0))
        if age_seconds <= CACHE_TTL_SECONDS and cached_days >= int(forecast_days):
            return _clone_result(cached)

    lead_hours = tuple(range(0, max(1, int(forecast_days)) * 24))
    try:
        ingest = ingest_class(city=city)
    except TypeError:
        ingest = ingest_class()
    bundle = ingest.fetch(fetch_time, lead_hours)
    parsed = _parse_ingest_bundle(bundle, model=model, fetch_time=fetch_time, role=role)
    parsed["forecast_days"] = int(forecast_days)
    _ENSEMBLE_CACHE[cache_key] = parsed
    return _clone_result(parsed)


def _parse_ingest_bundle(
    bundle,
    *,
    model: str,
    fetch_time: datetime,
    role: ForecastSourceRole,
) -> dict:
    raw = bundle.raw_payload
    if isinstance(raw, Mapping) and "hourly" in raw:
        parsed = _parse_response(
            dict(raw),
            model,
            fetch_time,
            source_id=bundle.source_id,
            authority_tier=bundle.authority_tier,
            degradation_level="OK",
            forecast_source_role=role,
        )
        parsed["issue_time"] = bundle.run_init_utc
        parsed["raw_payload_hash"] = bundle.raw_payload_hash
        parsed["captured_at"] = bundle.captured_at.isoformat()
        return parsed

    times = _extract_times(raw)
    members = _extract_members(raw, bundle.ensemble_members)
    if not times:
        raise ValueError("registered ingest bundle must include `times` or hourly.time")
    if not members:
        raise ValueError("registered ingest bundle must include ensemble member vectors")
    members_hourly = np.array(members, dtype=np.float64)
    if members_hourly.ndim != 2:
        raise ValueError("registered ingest member vectors must form a 2D array")
    if members_hourly.shape[1] != len(times):
        raise ValueError(
            "registered ingest member vectors must align with the provided times"
        )
    return {
        "members_hourly": members_hourly,
        "times": list(times),
        "issue_time": bundle.run_init_utc,
        "first_valid_time": _parse_timestamp_as_utc(str(times[0])),
        "fetch_time": fetch_time,
        "captured_at": bundle.captured_at.isoformat(),
        "model": model,
        "source_id": bundle.source_id,
        "raw_payload_hash": bundle.raw_payload_hash,
        "authority_tier": bundle.authority_tier,
        "degradation_level": "OK",
        "forecast_source_role": role,
        "n_members": int(members_hourly.shape[0]),
    }


def _extract_times(raw: object) -> Sequence[object]:
    if isinstance(raw, Mapping):
        if isinstance(raw.get("times"), Sequence) and not isinstance(raw.get("times"), (str, bytes)):
            return raw["times"]  # type: ignore[index]
        hourly = raw.get("hourly")
        if (
            isinstance(hourly, Mapping)
            and isinstance(hourly.get("time"), Sequence)
            and not isinstance(hourly.get("time"), (str, bytes))
        ):
            return hourly["time"]  # type: ignore[index]
    return ()


def _extract_members(raw: object, bundle_members: Sequence[object]) -> list[Sequence[object]]:
    candidates: object = bundle_members
    if isinstance(raw, Mapping):
        if "members_hourly" in raw:
            candidates = raw["members_hourly"]
        elif "ensemble_members" in raw:
            candidates = raw["ensemble_members"]
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        return []
    rows: list[Sequence[object]] = []
    for member in candidates:
        values = member.get("values") if isinstance(member, Mapping) else member
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            rows.append(values)
    return rows


def _parse_response(
    data: dict,
    model: str,
    fetch_time: datetime,
    *,
    source_id: str | None = None,
    authority_tier: str = "FORECAST",
    degradation_level: str = "OK",
    forecast_source_role: ForecastSourceRole = "entry_primary",
) -> dict:
    """Parse Open-Meteo ensemble response into structured dict.

    Open-Meteo returns ensemble members as separate keys:
    temperature_2m_member0, temperature_2m_member1, ..., temperature_2m_member50
    """
    hourly = data["hourly"]
    times = hourly["time"]

    # Collect all member arrays.
    # Open-Meteo format: temperature_2m (control run), temperature_2m_member01, ..., member50
    # The control run (temperature_2m without suffix) is member 0.
    members = []

    # Member 0 = control run (key: temperature_2m, no suffix)
    if "temperature_2m" in hourly:
        members.append(hourly["temperature_2m"])

    # Members 01-50 (zero-padded two digits)
    for i in range(1, 100):
        key = f"temperature_2m_member{i:02d}"
        if key not in hourly:
            break
        members.append(hourly[key])

    if not members:
        raise ValueError(f"No ensemble members found in response for model {model}")

    members_hourly = np.array(members, dtype=np.float64)  # (n_members, hours)
    n_members = members_hourly.shape[0]

    first_valid_time = _parse_timestamp_as_utc(times[0])

    return {
        "members_hourly": members_hourly,
        "times": times,
        "issue_time": None,
        "first_valid_time": first_valid_time,
        "fetch_time": fetch_time,
        "captured_at": fetch_time.isoformat(),
        "model": model,
        "source_id": source_id or source_id_for_ensemble_model(model),
        "raw_payload_hash": stable_payload_hash(data),
        "authority_tier": authority_tier,
        "degradation_level": degradation_level,
        "forecast_source_role": forecast_source_role,
        "n_members": n_members,
    }


def _coerce_members_hourly(result: dict) -> np.ndarray | None:
    members = result.get("members_hourly")
    if members is None:
        return None
    try:
        arr = np.asarray(members, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if arr.ndim != 2:
        return None
    return arr


def _effective_finite_member_count(
    members: np.ndarray,
    required_hour_indices: Sequence[int] | np.ndarray | None,
) -> int:
    if required_hour_indices is None:
        return int(members.shape[0])
    idxs = [int(idx) for idx in required_hour_indices]
    if not idxs:
        return 0
    if min(idxs) < 0 or max(idxs) >= members.shape[1]:
        return 0
    required = members[:, idxs]
    finite_member_mask = np.isfinite(required).all(axis=1)
    return int(np.count_nonzero(finite_member_mask))


def _required_hours_all_finite(
    members: np.ndarray,
    required_hour_indices: Sequence[int] | np.ndarray,
) -> bool:
    idxs = [int(idx) for idx in required_hour_indices]
    if not idxs:
        return False
    if min(idxs) < 0 or max(idxs) >= members.shape[1]:
        return False
    return bool(np.isfinite(members[:, idxs]).all())


def validate_ensemble(
    result: dict,
    expected_members: int | None = None,
    required_hour_indices: Sequence[int] | np.ndarray | None = None,
) -> bool:
    """Validate ensemble response. Per CLAUDE.md: reject if < expected members."""
    if expected_members is None:
        expected_members = ensemble_member_count()
    if result is None:
        return False
    n = result["n_members"]
    if n < expected_members:
        print(f"  WARN ensemble has {n} members, expected {expected_members}. REJECTED.")
        return False
    members_present = result.get("members_hourly") is not None
    members = _coerce_members_hourly(result)
    if members is None:
        if members_present or required_hour_indices is not None:
            print("  WARN ensemble members_hourly is missing or malformed. REJECTED.")
            return False
    else:
        if members.shape[0] != n:
            print(
                "  WARN ensemble n_members metadata does not match members_hourly rows. "
                "REJECTED."
            )
            return False
        if required_hour_indices is not None:
            effective_n = _effective_finite_member_count(members, required_hour_indices)
            if effective_n < expected_members:
                print(
                    f"  WARN ensemble has {effective_n} finite members for required hours, "
                    f"expected {expected_members}. REJECTED."
                )
                return False
            if not _required_hours_all_finite(members, required_hour_indices):
                print("  WARN ensemble has non-finite supplied members for required hours. REJECTED.")
                return False
            return True
        nonfinite_frac = (~np.isfinite(members)).mean()
        if nonfinite_frac > 0.5:
            print(f"  WARN ensemble has {nonfinite_frac:.0%} non-finite values. REJECTED.")
            return False
    return True


def _clear_cache() -> None:
    _ENSEMBLE_CACHE.clear()
