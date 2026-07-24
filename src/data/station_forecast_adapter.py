# Created: 2026-06-28
# Last reused/audited: 2026-06-28
# Authority basis: docs/evidence/hko_station_forecast/2026-06-28_hko_hk_integration.md (HKO 9-day
#   official forecast → HK served center, settlement-graded walk-forward). DATA-PRECISION external
#   station-forecast ingest (an INDEPENDENT published forecast), NOT a de-bias/fitted offset.
#   Enters raw_model_forecasts as a single_runs candidate exactly like a gridded model
#   (src/data/bayes_precision_fusion_download._RMF_INSERT_COLUMNS contract) and is weighted into
#   the city's served center by the existing source-clock fixed-weight scheme — no bolt-on path.
"""Reusable adapter for station-calibrated official forecasts (HKO, KMA, CWA, MetMalaysia, TWC...).

A *station forecast* is a national met agency's OWN published daily-extreme forecast for the SAME
station the corresponding market settles on. Unlike a gridded model interpolated to a point, the
agency's forecast bakes in the station microclimate through its operational MOS, so it is a
genuinely station-calibrated, decorrelated information source — DATA PRECISION, never a de-bias.

This module ingests such a forecast into ``raw_model_forecasts`` under a dedicated ``model`` id
(e.g. ``hko_fnd``) with ``endpoint='single_runs'`` and full request provenance, mirroring the
Open-Meteo single_runs capture contract. Once a row exists, the model is admitted into the city's
served center solely by the per-city source-clock scheme weights
(``src/strategy/live_inference/source_clock_city_weights.py``): there is NO new fusion code path
and NO hard-coded weight in code — a station source contributes iff (a) its row is persisted AND
(b) the city's scheme row lists it. Out-of-domain cities (every city whose scheme omits the source)
are byte-identical to before.

Adding a sibling source is a config addition in ``config/station_forecast_sources.json`` plus a
per-city scheme-weight entry; only HKO/Hong Kong is wired + walk-forward validated today.

NETWORK: ``fetch_*`` makes a live HTTPS GET. The pure parser/persist functions never touch the
network — tests pin behaviour with a recorded fixture (tests/data/hko_fnd_sample.json).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

UTC = timezone.utc

# The persisted raw_model_forecasts column order — IDENTICAL to
# src.data.bayes_precision_fusion_download._RMF_INSERT_COLUMNS so a station-forecast row is
# indistinguishable in shape from an Open-Meteo single_runs row (single-builder for the schema
# contract; if that tuple changes, this import will surface the drift at call time).
from src.data.bayes_precision_fusion_download import (
    _RMF_INSERT_COLUMNS,
    _persist_rows,
)

# HKO publishes degC integers; forecast_value_c is ALWAYS degC (SPEC §7 C/F unit-mix antibody).
_HKO_ENDPOINT = (
    "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=fnd&lang=en"
)
_STATION_FORECAST_CONFIG = "config/station_forecast_sources.json"


@dataclass(frozen=True)
class StationForecastRow:
    """One (target_date, lead) station-forecast value parsed from a provider payload."""

    model: str
    city: str
    metric: str
    target_date: str          # YYYY-MM-DD (Zeus target local date)
    lead_days: int            # target_date − issue_local_date (city-local calendar)
    forecast_value_c: float   # degC
    source_cycle_time: str    # provider issue/update instant, ISO-8601 (the cycle clock)
    source_available_at: str  # proof-of-possession instant, ISO-8601


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_station_forecast_config(
    *, root: Path | None = None
) -> Mapping[str, Mapping[str, object]]:
    """Return the ``sources`` mapping from config/station_forecast_sources.json (empty on absence)."""
    base = root or _project_root()
    path = base / _STATION_FORECAST_CONFIG
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    sources = data.get("sources")
    return sources if isinstance(sources, dict) else {}


def _forecast_date_to_iso(forecast_date: str) -> str:
    """HKO forecastDate is 'YYYYMMDD'; return ISO 'YYYY-MM-DD'."""
    s = str(forecast_date).strip()
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"unexpected forecastDate {forecast_date!r} (want YYYYMMDD)")
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def parse_hko_fnd_payload(
    payload: Mapping[str, object],
    *,
    city: str = "Hong Kong",
    metric: str = "high",
    city_timezone: str = "Asia/Hong_Kong",
    model: str = "hko_fnd",
) -> tuple[StationForecastRow, ...]:
    """Pure parser for the HKO Nine-Day Forecast (``dataType=fnd``) JSON.

    Maps each ``weatherForecast[].forecastDate`` (YYYYMMDD) to a Zeus target_date and a lead
    (``target_date − issue_local_date`` in the city-local calendar — the SAME lead convention as
    ``_bayes_precision_fusion_city_local_lead_days``: the first day, forecastDate == issue date, is
    lead 0). Reads ``forecastMaxtemp.value`` (degC) for ``metric='high'`` (or ``forecastMintemp``
    for 'low'). ``updateTime`` is the provider issue instant = the cycle clock AND the
    proof-of-possession ``source_available_at`` (the forecast exists once HKO published it).

    NETWORK-FREE. Raises ValueError on a structurally invalid payload (missing updateTime /
    weatherForecast). Individual malformed day entries are skipped (fail-soft per row) so a single
    bad day never voids the whole capture.
    """
    if metric not in {"high", "low"}:
        raise ValueError("metric must be 'high' or 'low'")
    temp_key = "forecastMaxtemp" if metric == "high" else "forecastMintemp"

    update_time = payload.get("updateTime")
    if not isinstance(update_time, str) or not update_time.strip():
        raise ValueError("HKO fnd payload missing 'updateTime'")
    cycle_dt = datetime.fromisoformat(update_time.replace("Z", "+00:00"))
    if cycle_dt.tzinfo is None:
        raise ValueError("HKO updateTime must be timezone-aware")
    source_cycle_time = cycle_dt.astimezone(UTC).isoformat()
    # Proof of possession: the forecast is possessed the instant HKO published it (updateTime).
    source_available_at = source_cycle_time
    issue_local_date = cycle_dt.astimezone(ZoneInfo(city_timezone)).date()

    days = payload.get("weatherForecast")
    if not isinstance(days, Sequence) or not days:
        raise ValueError("HKO fnd payload missing 'weatherForecast'")

    rows: list[StationForecastRow] = []
    for entry in days:
        if not isinstance(entry, Mapping):
            continue
        try:
            target_iso = _forecast_date_to_iso(str(entry.get("forecastDate")))
            temp_obj = entry.get(temp_key)
            if not isinstance(temp_obj, Mapping):
                continue
            unit = str(temp_obj.get("unit", "C")).strip().upper()
            if unit not in {"C", "CELSIUS", "°C"}:
                # forecast_value_c MUST be degC; refuse to silently store a non-C value.
                continue
            value_c = float(temp_obj.get("value"))
        except (TypeError, ValueError):
            continue
        target_date = date.fromisoformat(target_iso)
        lead_days = (target_date - issue_local_date).days
        if lead_days < 0:
            # A forecastDate before the issue date is not a forward forecast — skip.
            continue
        rows.append(
            StationForecastRow(
                model=model,
                city=city,
                metric=metric,
                target_date=target_iso,
                lead_days=int(lead_days),
                forecast_value_c=value_c,
                source_cycle_time=source_cycle_time,
                source_available_at=source_available_at,
            )
        )
    return tuple(rows)


def fetch_hko_fnd_payload(
    *, endpoint: str = _HKO_ENDPOINT, timeout_s: float = 20.0
) -> Mapping[str, object]:
    """Live HTTPS GET of the HKO Nine-Day Forecast JSON. NETWORK — never called from tests."""
    import urllib.request  # noqa: PLC0415

    req = urllib.request.Request(endpoint, headers={"User-Agent": "zeus-station-forecast/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 (https only)
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _row_to_rmf_dict(
    row: StationForecastRow,
    *,
    provider: str,
    endpoint: str,
    city_timezone: str,
    latitude: float | None,
    longitude: float | None,
    captured_at: str,
) -> dict[str, object]:
    """Build a raw_model_forecasts insert dict keyed by _RMF_INSERT_COLUMNS for one station row.

    Mirrors the Open-Meteo single_runs provenance shape: source_family identifies the lane,
    request_url_hash binds the logical key to a physical request identity (the B4 contamination
    guard relies on it), product_id = '<model>::single_runs'.
    """
    request_params = {
        "dataType": "fnd",
        "lang": "en",
        "metric": row.metric,
        "city": row.city,
        "timezone": city_timezone,
    }
    request_params_json = json.dumps(
        request_params, sort_keys=True, separators=(",", ":")
    )
    request_url_hash = hashlib.sha256(
        f"{endpoint}?{request_params_json}".encode("utf-8")
    ).hexdigest()
    model_name = row.model
    product_id = f"{model_name}::single_runs"
    model_domain_hash = hashlib.sha256(
        json.dumps(
            {
                "provider": provider,
                "model_name": model_name,
                "city": row.city,
                "endpoint_mode": "single_runs",
                "cell_selection": "station_official_forecast",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "model": row.model,
        "city": row.city,
        "target_date": row.target_date,
        "metric": row.metric,
        "source_cycle_time": row.source_cycle_time,
        "source_available_at": row.source_available_at,
        "captured_at": captured_at,
        "lead_days": int(row.lead_days),
        "forecast_value_c": float(row.forecast_value_c),
        "endpoint": "single_runs",
        "source_id": f"{model_name}_single_runs",
        "source_family": "station_official_forecast",
        "product_id": product_id,
        "provider": provider,
        "model_name": model_name,
        "request_params_json": request_params_json,
        "request_url_hash": request_url_hash,
        "latitude_requested": (None if latitude is None else float(latitude)),
        "longitude_requested": (None if longitude is None else float(longitude)),
        "timezone_requested": city_timezone,
        # Not a gridded cell — the station IS the settlement point. Distinct sentinel so this row
        # is never mistaken for a grid-interpolated value.
        "cell_selection": "station_official_forecast",
        "elevation_param": "station",
        "downscaling_policy": "agency_mos",
        "endpoint_mode": "single_runs",
        "model_domain_hash": model_domain_hash,
        "coverage_status": "COVERED",
    }


def station_rows_to_rmf_dicts(
    rows: Sequence[StationForecastRow],
    *,
    provider: str,
    endpoint: str,
    city_timezone: str,
    latitude: float | None = None,
    longitude: float | None = None,
    captured_at: str | None = None,
) -> list[dict[str, object]]:
    """Pure transform: StationForecastRow[] → raw_model_forecasts insert dicts. NETWORK-FREE."""
    cap = captured_at or datetime.now(tz=UTC).isoformat()
    return [
        _row_to_rmf_dict(
            r,
            provider=provider,
            endpoint=endpoint,
            city_timezone=city_timezone,
            latitude=latitude,
            longitude=longitude,
            captured_at=cap,
        )
        for r in rows
    ]


def persist_station_forecast_rows(
    conn: sqlite3.Connection,
    rows: Sequence[StationForecastRow],
    *,
    provider: str,
    endpoint: str,
    city_timezone: str,
    latitude: float | None = None,
    longitude: float | None = None,
    captured_at: str | None = None,
) -> int:
    """Persist station rows into raw_model_forecasts via the SAME idempotent writer the Open-Meteo
    capture uses (_persist_rows: B4 logical-key conflict guard + INSERT OR IGNORE). Returns rows
    written. NETWORK-FREE — the caller fetches+parses, this only writes.
    """
    rmf_rows = station_rows_to_rmf_dicts(
        rows,
        provider=provider,
        endpoint=endpoint,
        city_timezone=city_timezone,
        latitude=latitude,
        longitude=longitude,
        captured_at=captured_at,
    )
    if not rmf_rows:
        return 0
    return _persist_rows(conn, rmf_rows)


def ingest_hko_fnd_live(
    conn: sqlite3.Connection,
    *,
    city: str = "Hong Kong",
    metric: str = "high",
    metrics: Sequence[str] | None = None,
    city_timezone: str = "Asia/Hong_Kong",
    latitude: float | None = None,
    longitude: float | None = None,
    endpoint: str = _HKO_ENDPOINT,
) -> int:
    """LIVE end-to-end HKO ingest: fetch → parse → persist into raw_model_forecasts.

    This is the ONLY function here that touches the network. One HKO issue contains both maximum
    and minimum forecasts, so a configured multi-metric ingest fetches once and persists both typed
    rows. Siblings are added by config + a parser dispatch, not a rewrite. Returns the number of
    raw_model_forecasts rows written.
    """
    if isinstance(metrics, (str, bytes)):
        raise ValueError("metrics must be a sequence of 'high'/'low' values")
    selected = (metric,) if metrics is None else tuple(str(value).lower() for value in metrics)
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("metrics must be non-empty and unique")
    if any(value not in {"high", "low"} for value in selected):
        raise ValueError("metrics must contain only 'high' or 'low'")

    payload = fetch_hko_fnd_payload(endpoint=endpoint)
    rows = tuple(
        row
        for value in selected
        for row in parse_hko_fnd_payload(
            payload,
            city=city,
            metric=value,
            city_timezone=city_timezone,
        )
    )
    return persist_station_forecast_rows(
        conn,
        rows,
        provider="hong_kong_observatory",
        endpoint=endpoint,
        city_timezone=city_timezone,
        latitude=latitude,
        longitude=longitude,
    )


# ---------------------------------------------------------------------------
# CWA Township (Taiwan) — Central Weather Administration 鄉鎮天氣預報 (F-D0047-063)
# ---------------------------------------------------------------------------
# A *sibling* station-forecast source, wired exactly like HKO: a national met agency's OWN
# published daily-max forecast for the district (松山區/Songshan) that contains the SAME station
# (RCSS/Taipei-Songshan) the Taipei market settles on. The CWA township MOS bakes in the local
# microclimate, so it is a station-calibrated, decorrelated information source — DATA PRECISION,
# never a de-bias. It enters raw_model_forecasts under model id ``cwa_township`` via the identical
# single_runs persist contract; it contributes to Taipei's served center ONLY via the per-city
# source-clock scheme weight (no new fusion path, no hard-coded weight in code).
#
# KEY HANDLING: the CWA Open Data API requires an Authorization token. Following the WU_API_KEY
# pattern (src/data/observation_client.py), the key is read from the ``CWA_API_KEY`` environment
# variable injected by the forecast-live launch plist, with a gitignored ``config/cwa_secret.json``
# file fallback. The key is NEVER committed to source. Absent a key, the live ingest is a fail-soft
# no-op (no row written) — Taipei serves the gridded basket unchanged.
_CWA_ENDPOINT = (
    "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-063"
)
_CWA_SECRET_CONFIG = "config/cwa_secret.json"
_CWA_API_KEY_ENV = "CWA_API_KEY"


def resolve_cwa_api_key(
    *, environ: Mapping[str, str] | None = None, root: Path | None = None
) -> str | None:
    """Resolve the CWA Open Data Authorization token.

    Order: ``CWA_API_KEY`` env var (forecast-live plist) → gitignored ``config/cwa_secret.json``
    (``{"cwa_api_key": "..."}``). Returns None when neither is present (caller fail-softs). The key
    is NEVER logged or returned in any provenance field.
    """
    import os  # noqa: PLC0415

    env = environ if environ is not None else os.environ
    key = str(env.get(_CWA_API_KEY_ENV, "") or "").strip()
    if key:
        return key
    base = root or _project_root()
    path = base / _CWA_SECRET_CONFIG
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    # Accept either casing in the file: the documented contract is lowercase ``cwa_api_key``,
    # but the env-var name ``CWA_API_KEY`` is an easy-to-mistype alternative that once caused a
    # silent 0-row no-op. Tolerate both so a mis-cased key never silently disables CWA again.
    blob = data or {}
    secret = str(blob.get("cwa_api_key") or blob.get(_CWA_API_KEY_ENV) or "").strip()
    return secret or None


def parse_cwa_township_payload(
    payload: Mapping[str, object],
    *,
    city: str = "Taipei",
    metric: str = "high",
    city_timezone: str = "Asia/Taipei",
    model: str = "cwa_township",
    captured_at: str | None = None,
) -> tuple[StationForecastRow, ...]:
    """Pure parser for the CWA township forecast (``F-D0047-063``, ElementName=最高溫度) JSON.

    The dataset gives 12-hour blocks per district. The Zeus daily-MAX target for a local date D is
    the **day** block ``StartTime D 06:00 → EndTime D 18:00`` (Asia/Taipei); the overnight
    18:00→06:00 block is a different aggregation and is skipped. ``ElementValue[0].MaxTemperature``
    is the degC max for that day. Lead = ``target_date − issue_local_date``; CWA publishes NO issue
    timestamp in this dataset, so the proof-of-possession instant (``captured_at`` / now) IS the
    cycle clock and the issue date (we possess the forecast the instant we fetch it — honest, no
    look-ahead beyond the wall clock).

    NETWORK-FREE. Raises ValueError on a structurally invalid payload; individual malformed day
    blocks are skipped (fail-soft per row). Only ``metric='high'`` is supported (the settlement
    metric for Taipei); ``'low'`` raises (CWA's 最高溫度 element is max-only).
    """
    if metric != "high":
        # This dataset's requested element (最高溫度) is the daily MAX only.
        raise ValueError("cwa_township adapter supports metric='high' only")

    cap = captured_at or datetime.now(tz=UTC).isoformat()
    cap_dt = datetime.fromisoformat(cap)
    if cap_dt.tzinfo is None:
        cap_dt = cap_dt.replace(tzinfo=UTC)
    source_cycle_time = cap_dt.astimezone(UTC).isoformat()
    source_available_at = source_cycle_time
    issue_local_date = cap_dt.astimezone(ZoneInfo(city_timezone)).date()

    records = payload.get("records")
    if not isinstance(records, Mapping):
        raise ValueError("CWA payload missing 'records'")
    locations_outer = records.get("Locations") or records.get("locations")
    if not isinstance(locations_outer, Sequence) or not locations_outer:
        raise ValueError("CWA payload missing 'records.Locations'")
    first_outer = locations_outer[0]
    if not isinstance(first_outer, Mapping):
        raise ValueError("CWA payload Locations[0] is not an object")
    inner = first_outer.get("Location") or first_outer.get("location")
    if not isinstance(inner, Sequence) or not inner:
        raise ValueError("CWA payload missing 'Location[]'")
    loc = inner[0]
    if not isinstance(loc, Mapping):
        raise ValueError("CWA payload Location[0] is not an object")

    elements = loc.get("WeatherElement") or loc.get("weatherElement")
    if not isinstance(elements, Sequence) or not elements:
        raise ValueError("CWA payload missing 'WeatherElement[]'")

    rows: list[StationForecastRow] = []
    for element in elements:
        if not isinstance(element, Mapping):
            continue
        times = element.get("Time") or element.get("time")
        if not isinstance(times, Sequence):
            continue
        for block in times:
            if not isinstance(block, Mapping):
                continue
            try:
                start_raw = str(block.get("StartTime") or block.get("startTime"))
                end_raw = str(block.get("EndTime") or block.get("endTime"))
                start_dt = datetime.fromisoformat(start_raw)
                end_dt = datetime.fromisoformat(end_raw)
                if start_dt.tzinfo is None or end_dt.tzinfo is None:
                    continue
                start_local = start_dt.astimezone(ZoneInfo(city_timezone))
                end_local = end_dt.astimezone(ZoneInfo(city_timezone))
                # The daily-MAX block is the daytime 06:00 → 18:00 window on the SAME local date.
                if not (
                    start_local.hour == 6
                    and end_local.hour == 18
                    and start_local.date() == end_local.date()
                ):
                    continue
                values = block.get("ElementValue") or block.get("elementValue")
                if not isinstance(values, Sequence) or not values:
                    continue
                first_value = values[0]
                if not isinstance(first_value, Mapping):
                    continue
                raw_max = first_value.get("MaxTemperature")
                if raw_max is None or str(raw_max).strip() == "":
                    continue
                value_c = float(raw_max)
            except (TypeError, ValueError):
                continue
            target_date = start_local.date()
            lead_days = (target_date - issue_local_date).days
            if lead_days < 0:
                continue
            rows.append(
                StationForecastRow(
                    model=model,
                    city=city,
                    metric=metric,
                    target_date=target_date.isoformat(),
                    lead_days=int(lead_days),
                    forecast_value_c=value_c,
                    source_cycle_time=source_cycle_time,
                    source_available_at=source_available_at,
                )
            )
    return tuple(rows)


def fetch_cwa_township_payload(
    *,
    api_key: str,
    location_name: str = "松山區",
    element_name: str = "最高溫度",
    endpoint: str = _CWA_ENDPOINT,
    timeout_s: float = 25.0,
) -> Mapping[str, object]:
    """Live HTTPS GET of the CWA township forecast JSON. NETWORK — never called from tests.

    ``api_key`` is the CWA Authorization token; it is sent as a query parameter (CWA's required
    transport) but is NEVER logged. The request is scoped to a single district + the max-temp
    element to keep the payload small.
    """
    import urllib.parse  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    qs = urllib.parse.urlencode(
        {
            "Authorization": api_key,
            "format": "JSON",
            "LocationName": location_name,
            "ElementName": element_name,
        }
    )
    url = f"{endpoint}?{qs}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "zeus-station-forecast/1.0"}
    )
    # CWA's government TLS cert omits the Subject Key Identifier extension, which
    # OpenSSL 3.x strict X.509 verification rejects ("Missing Subject Key Identifier")
    # even though the chain is valid (curl accepts it). Relax ONLY that formatting
    # strictness — certificate-chain and hostname verification remain enforced.
    import ssl  # noqa: PLC0415

    ssl_ctx = ssl.create_default_context()
    try:
        ssl_ctx.verify_flags &= ~ssl.VerifyFlags.VERIFY_X509_STRICT
    except AttributeError:  # older Python without the strict flag — already lenient
        pass
    with urllib.request.urlopen(  # noqa: S310 (https only)
        req, timeout=timeout_s, context=ssl_ctx
    ) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def ingest_cwa_township_live(
    conn: sqlite3.Connection,
    *,
    city: str = "Taipei",
    metric: str = "high",
    city_timezone: str = "Asia/Taipei",
    latitude: float | None = None,
    longitude: float | None = None,
    location_name: str = "松山區",
    element_name: str = "最高溫度",
    endpoint: str = _CWA_ENDPOINT,
    api_key: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """LIVE end-to-end CWA township ingest: resolve key → fetch → parse → persist.

    Reads the Authorization token via :func:`resolve_cwa_api_key` (env → gitignored secret file)
    unless an explicit ``api_key`` is passed. Returns 0 (fail-soft no-op) when no key is available
    so a missing key never breaks the capture cycle and Taipei serves the gridded basket unchanged.
    NETWORK happens here only. The endpoint is endpoint-scoped to the 松山區 (Songshan) district,
    which contains the RCSS settlement station. Provider = ``cwa_taiwan``.
    """
    key = api_key or resolve_cwa_api_key(environ=environ)
    if not key:
        return 0
    payload = fetch_cwa_township_payload(
        api_key=key,
        location_name=location_name,
        element_name=element_name,
        endpoint=endpoint,
    )
    rows = parse_cwa_township_payload(
        payload, city=city, metric=metric, city_timezone=city_timezone
    )
    return persist_station_forecast_rows(
        conn,
        rows,
        provider="cwa_taiwan",
        endpoint=endpoint,
        city_timezone=city_timezone,
        latitude=latitude,
        longitude=longitude,
    )


# ---------------------------------------------------------------------------
# Config-driven live ingest dispatcher — the seam the forecast-download lane calls
# ---------------------------------------------------------------------------
# Turns the static config/station_forecast_sources.json into live raw_model_forecasts rows:
# for each ENABLED source it routes by ``adapter_kind`` to that provider's live ingest function
# (fetch → parse → persist, single_runs contract). Per-source FAIL-SOFT: one provider's network
# or parse error is logged and skipped, never aborting the others or the parent download cycle.
# This is the ONLY wiring that adds station data live — no hard-coded per-source call list in the
# daemon, no new fusion path, no hand-set weight. A source contributes to its city's served center
# solely through the per-city source-clock scheme weight downstream.
_STATION_ADAPTER_DISPATCH: dict[str, str] = {
    "cwa_township_json": "ingest_cwa_township_live",
    "hko_fnd_json": "ingest_hko_fnd_live",
}


def _station_ingest_kwargs(
    adapter_kind: str,
    spec: Mapping[str, object],
    *,
    environ: Mapping[str, str] | None,
) -> dict[str, object]:
    """Build only the kwargs the target ingest function accepts, from the config spec."""
    kw: dict[str, object] = {}
    if spec.get("city"):
        kw["city"] = str(spec["city"])
    if spec.get("metric"):
        kw["metric"] = str(spec["metric"])
    if adapter_kind == "hko_fnd_json" and spec.get("metrics") is not None:
        raw_metrics = spec["metrics"]
        if not isinstance(raw_metrics, Sequence) or isinstance(raw_metrics, (str, bytes)):
            raise ValueError("hko_fnd metrics must be a sequence")
        kw["metrics"] = tuple(str(value) for value in raw_metrics)
    if spec.get("endpoint"):
        kw["endpoint"] = str(spec["endpoint"])
    if adapter_kind == "cwa_township_json":
        if spec.get("location_name"):
            kw["location_name"] = str(spec["location_name"])
        if spec.get("element_name"):
            kw["element_name"] = str(spec["element_name"])
        if environ is not None:
            kw["environ"] = environ
    return kw


def ingest_enabled_station_sources_live(
    conn: sqlite3.Connection,
    *,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, int]:
    """Ingest every ENABLED station-forecast source from config, routed by ``adapter_kind``.

    Returns ``{source_id: rows_written}`` for each source that dispatched without error. Per-source
    fail-soft: a source whose ingest raises (or whose ``adapter_kind`` is unknown) is logged and
    omitted, so one provider outage never starves the cycle. NETWORK happens inside the dispatched
    ingest functions only.
    """
    import logging  # noqa: PLC0415 - keep module import surface lean; called ~2x/cycle

    log = logging.getLogger(__name__)
    out: dict[str, int] = {}
    sources = load_station_forecast_config(root=root)
    for source_id, spec in sources.items():
        if not isinstance(spec, Mapping) or not spec.get("enabled"):
            continue
        adapter_kind = str(spec.get("adapter_kind") or "")
        fn_name = _STATION_ADAPTER_DISPATCH.get(adapter_kind)
        if fn_name is None:
            log.warning(
                "station ingest: source %s has unknown adapter_kind %r — skipped",
                source_id,
                spec.get("adapter_kind"),
            )
            continue
        fn = globals().get(fn_name)
        if not callable(fn):
            continue
        try:
            kwargs = _station_ingest_kwargs(adapter_kind, spec, environ=environ)
            out[str(source_id)] = int(fn(conn, **kwargs))
        except Exception as exc:  # noqa: BLE001 - one source must never abort the cycle
            log.warning(
                "station ingest: source %s (%s) failed fail-soft: %s", source_id, fn_name, exc
            )
            continue
    return out
