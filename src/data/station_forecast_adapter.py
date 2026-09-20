# Created: 2026-06-28
# Last reused/audited: 2026-06-28
# Authority basis: docs/evidence/hko_station_forecast/2026-06-28_hko_hk_integration.md (HKO 9-day
#   official forecast → HK served center, settlement-graded walk-forward). DATA-PRECISION external
#   station-forecast ingest (an INDEPENDENT published forecast), NOT a de-bias/fitted offset.
#   Enters raw_model_forecasts as a single_runs candidate exactly like a gridded model
#   (src/data/bayes_precision_fusion_download._RMF_INSERT_COLUMNS contract) and is weighted into
#   the city's served center by the existing source-clock fixed-weight scheme — no bolt-on path.
"""Ingest official forecasts with explicit product, point, metric and clock identity.

CWA township forecasts describe a representative point distinct from RCSS.
Hourly-sampled calendar extrema and native interval extrema are separate products;
none of these forecast rows establishes a settlement or observed temperature fact.
Registry-authorized rows enter the existing current-value/raw-precision fusion path.

``fetch_*`` performs HTTPS reads; parsers and persistence never fetch data.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

# Station rows use the same persisted-row writer as Open-Meteo single_runs, so
# their schema and logical-key conflict contract stay shared.
from src.data.bayes_precision_fusion_download import _persist_rows

UTC = timezone.utc

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


@dataclass(frozen=True)
class CwaHourlyProduct:
    """Authenticated CWA F-D0047-061 raw XML and its publisher clocks.

    ``update_time`` is CWA's timezone-aware DatasetInfo revision timestamp.
    ``captured_at`` remains Zeus's distinct proof-of-possession time; it is not
    a CWA issue/update time.
    """

    raw_xml: bytes
    issue_time: str
    update_time: str
    sent_time: str | None
    raw_sha256: str
    captured_at: str


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
    request_params: Mapping[str, object] | None = None,
    cell_selection: str = "station_official_forecast",
    elevation_param: str = "station",
    downscaling_policy: str = "agency_mos",
) -> dict[str, object]:
    """Build a raw_model_forecasts insert dict keyed by _RMF_INSERT_COLUMNS for one station row.

    Mirrors the Open-Meteo single_runs provenance shape: source_family identifies the lane,
    request_url_hash binds the logical key to a physical request identity (the B4 contamination
    guard relies on it), product_id = '<model>::single_runs'.
    """
    params = request_params or {
        "dataType": "fnd",
        "lang": "en",
        "metric": row.metric,
        "city": row.city,
        "timezone": city_timezone,
    }
    request_params_json = json.dumps(
        params, sort_keys=True, separators=(",", ":")
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
                "cell_selection": cell_selection,
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
        "cell_selection": cell_selection,
        "elevation_param": elevation_param,
        "downscaling_policy": downscaling_policy,
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
    request_params: Mapping[str, object] | None = None,
    cell_selection: str = "station_official_forecast",
    elevation_param: str = "station",
    downscaling_policy: str = "agency_mos",
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
            request_params=request_params,
            cell_selection=cell_selection,
            elevation_param=elevation_param,
            downscaling_policy=downscaling_policy,
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
    request_params: Mapping[str, object] | None = None,
    cell_selection: str = "station_official_forecast",
    elevation_param: str = "station",
    downscaling_policy: str = "agency_mos",
    raw_sha256: str | None = None,
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
        request_params=request_params,
        cell_selection=cell_selection,
        elevation_param=elevation_param,
        downscaling_policy=downscaling_policy,
    )
    if not rmf_rows:
        return 0
    written = _persist_rows(conn, rmf_rows)
    if raw_sha256 is not None:
        for row in rmf_rows:
            conn.execute(
                """
                UPDATE raw_model_forecasts
                   SET raw_sha256 = ?
                 WHERE model = ? AND city = ? AND target_date = ? AND metric = ?
                   AND source_cycle_time = ? AND endpoint = ?
                   AND request_url_hash = ?
                """,
                (
                    raw_sha256,
                    row["model"],
                    row["city"],
                    row["target_date"],
                    row["metric"],
                    row["source_cycle_time"],
                    row["endpoint"],
                    row["request_url_hash"],
                ),
            )
    return written


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
# Legacy F-D0047-063 12-hour daytime maximum adapter.  Its township representative point is not
# the RCSS settlement sensor and its 06:00→18:00 maximum is not a local-calendar daily maximum.
# It remains readable only for historical evidence; live entry uses F-D0047-061's complete D+1
# hourly Temperature grid below.
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
# CWA township hourly temperatures (F-D0047-061) — D+1 calendar-day LOW
# ---------------------------------------------------------------------------
_CWA_HOURLY_LOW_ENDPOINT = (
    "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/F-D0047-061"
)


def _xml_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _xml_child_text(element: ET.Element, name: str) -> str | None:
    child = next((item for item in element if _xml_name(item) == name), None)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _cwa_aware_time(value: str | None, *, field: str) -> datetime:
    if not value:
        raise ValueError(f"CWA F-D0047-061 missing {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"CWA F-D0047-061 invalid {field}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"CWA F-D0047-061 {field} must be timezone-aware")
    return parsed


def parse_cwa_township_hourly_product(raw_xml: bytes, *, captured_at: str) -> CwaHourlyProduct:
    """Validate the complete CWA fileapi product and extract publisher clocks.

    The REST datastore projection omits DatasetInfo IssueTime/Update.  This parser
    intentionally accepts only the official fileapi raw XML, where `Update` is
    the publisher's revision timestamp.  `captured_at` is validated separately
    and remains the local availability upper bound.
    """
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        raise ValueError("CWA F-D0047-061 invalid XML") from exc
    if _xml_name(root) != "cwaopendata":
        raise ValueError("CWA F-D0047-061 unexpected root")
    if _xml_child_text(root, "Dataid") != "D0047-061":
        raise ValueError("CWA fileapi response is not F-D0047-061")
    dataset = next((item for item in root if _xml_name(item) == "Dataset"), None)
    info = (
        next((item for item in dataset if _xml_name(item) == "DatasetInfo"), None)
        if dataset is not None
        else None
    )
    if info is None:
        raise ValueError("CWA F-D0047-061 missing DatasetInfo")
    issue = _cwa_aware_time(_xml_child_text(info, "IssueTime"), field="IssueTime")
    update = _cwa_aware_time(_xml_child_text(info, "Update"), field="Update")
    captured = _cwa_aware_time(captured_at, field="captured_at")
    if update > captured:
        raise ValueError("CWA F-D0047-061 requires Update <= captured_at")
    sent_text = _xml_child_text(root, "Sent")
    sent = None if sent_text is None else _cwa_aware_time(sent_text, field="Sent")
    return CwaHourlyProduct(
        raw_xml=raw_xml,
        issue_time=issue.astimezone(UTC).isoformat(),
        update_time=update.astimezone(UTC).isoformat(),
        sent_time=(None if sent is None else sent.astimezone(UTC).isoformat()),
        raw_sha256=hashlib.sha256(raw_xml).hexdigest(),
        captured_at=captured.astimezone(UTC).isoformat(),
    )


def fetch_cwa_township_hourly_product(
    *,
    api_key: str,
    endpoint: str = _CWA_HOURLY_LOW_ENDPOINT,
    timeout_s: float = 30.0,
) -> CwaHourlyProduct:
    """Fetch the CWA full XML product whose DatasetInfo carries IssueTime/Update."""
    import ssl  # noqa: PLC0415
    import urllib.parse  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    qs = urllib.parse.urlencode(
        {"Authorization": api_key, "downloadType": "WEB", "format": "XML"}
    )
    req = urllib.request.Request(
        f"{endpoint}?{qs}", headers={"User-Agent": "zeus-station-forecast/1.0"}
    )
    ssl_ctx = ssl.create_default_context()
    try:
        ssl_ctx.verify_flags &= ~ssl.VerifyFlags.VERIFY_X509_STRICT
    except AttributeError:
        pass
    with urllib.request.urlopen(req, timeout=timeout_s, context=ssl_ctx) as response:  # noqa: S310
        raw_xml = response.read()
    return parse_cwa_township_hourly_product(
        raw_xml, captured_at=datetime.now(tz=UTC).isoformat()
    )


def parse_cwa_township_hourly_extreme_product(
    product: CwaHourlyProduct,
    *,
    city: str = "Taipei",
    city_timezone: str = "Asia/Taipei",
    location_name: str = "松山區",
    location_geocode: str = "63000010",
    location_latitude: float = 25.051608,
    location_longitude: float = 121.568983,
    metric: str,
    model: str,
) -> tuple[StationForecastRow, ...]:
    """Return one complete D+1 calendar extreme from CWA hourly Temperature.

    F-D0047-061's `Temperature` samples are an hourly sampled product, not
    F-D0047-063's 12-hour `MaxT`/`MinT`.  We accept exactly one D+1 whose
    Asia/Taipei local clock has each unique whole hour 00..23 with a finite
    value, then take its max or min. Partial/current days, 3-hour later ranges,
    sub-hour samples, duplicates, and 12-hour extrema cannot form this source.
    """
    if metric not in {"high", "low"}:
        raise ValueError("CWA hourly extrema metric must be 'high' or 'low'")
    issue = _cwa_aware_time(product.issue_time, field="IssueTime")
    zone = ZoneInfo(city_timezone)
    issue_date = issue.astimezone(zone).date()
    try:
        root = ET.fromstring(product.raw_xml)
    except ET.ParseError as exc:  # product may have been built by a test fixture
        raise ValueError("CWA F-D0047-061 invalid XML") from exc

    candidates = [
        location for location in root.iter()
        if _xml_name(location) == "Location"
        and _xml_child_text(location, "LocationName") == location_name
        and _xml_child_text(location, "Geocode") == location_geocode
    ]
    if len(candidates) != 1:
        raise ValueError("CWA F-D0047-061 expected exactly one configured township location")
    location = candidates[0]
    try:
        latitude = float(_xml_child_text(location, "Latitude"))
        longitude = float(_xml_child_text(location, "Longitude"))
    except (TypeError, ValueError) as exc:
        raise ValueError("CWA F-D0047-061 township coordinates missing or invalid") from exc
    if not (
        math.isclose(latitude, location_latitude, abs_tol=1e-6)
        and math.isclose(longitude, location_longitude, abs_tol=1e-6)
    ):
        raise ValueError("CWA F-D0047-061 township coordinates do not match configured product")
    by_date: dict[date, dict[int, float]] = {}
    for weather_element in location:
        if (
            _xml_name(weather_element) != "WeatherElement"
            or _xml_child_text(weather_element, "ElementName") != "溫度"
        ):
            continue
        for point in weather_element:
            if _xml_name(point) != "Time":
                continue
            data_time = _xml_child_text(point, "DataTime")
            temperature = next(
                (
                    _xml_child_text(value, "Temperature")
                    for value in point
                    if _xml_name(value) == "ElementValue"
                ),
                None,
            )
            try:
                instant = _cwa_aware_time(data_time, field="DataTime")
                local = instant.astimezone(zone)
                value_c = float(temperature)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if local.minute or local.second or local.microsecond:
                continue
            values = by_date.setdefault(local.date(), {})
            if local.hour in values:
                values[local.hour] = float("nan")
            else:
                values[local.hour] = value_c

    target = issue_date.fromordinal(issue_date.toordinal() + 1)
    values = by_date.get(target, {})
    if set(values) != set(range(24)) or not all(math.isfinite(value) for value in values.values()):
        return ()
    return (
        StationForecastRow(
            model=model,
            city=city,
            metric=metric,
            target_date=target.isoformat(),
            lead_days=1,
            forecast_value_c=(max(values.values()) if metric == "high" else min(values.values())),
            source_cycle_time=product.update_time,
            # Update is a provider revision clock; possession is proven only by
            # the later local capture instant, never inferred from HTTP delivery.
            source_available_at=product.captured_at,
        ),
    )


def parse_cwa_township_hourly_low_product(
    product: CwaHourlyProduct,
    **kwargs: object,
) -> tuple[StationForecastRow, ...]:
    """Compatibility wrapper for the typed F-D0047-061 LOW product."""
    return parse_cwa_township_hourly_extreme_product(
        product,
        metric="low",
        model="cwa_township_hourly_low",
        **kwargs,
    )


def _ingest_cwa_township_hourly_extrema_by_metric(
    conn: sqlite3.Connection,
    *,
    city: str = "Taipei",
    metrics: Sequence[str] = ("high", "low"),
    city_timezone: str = "Asia/Taipei",
    location_name: str = "松山區",
    location_geocode: str = "63000010",
    location_latitude: float = 25.051608,
    location_longitude: float = 121.568983,
    endpoint: str = _CWA_HOURLY_LOW_ENDPOINT,
    api_key: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, int]:
    """Fetch F-D0047-061 once and return persisted-row counts by typed extreme."""
    selected = tuple(str(metric).lower() for metric in metrics)
    if not selected or len(set(selected)) != len(selected) or set(selected) - {"high", "low"}:
        raise ValueError("CWA hourly extrema metrics must be unique 'high'/'low' values")
    key = api_key or resolve_cwa_api_key(environ=environ)
    if not key:
        return {metric: 0 for metric in selected}
    product = fetch_cwa_township_hourly_product(api_key=key, endpoint=endpoint)
    written: dict[str, int] = {}
    for metric in selected:
        model = f"cwa_township_hourly_{metric}"
        rows = parse_cwa_township_hourly_extreme_product(
            product, city=city, city_timezone=city_timezone,
            location_name=location_name, location_geocode=location_geocode,
            location_latitude=location_latitude, location_longitude=location_longitude,
            metric=metric, model=model,
        )
        request_params = {
            "dataset": "F-D0047-061", "format": "XML", "downloadType": "WEB",
            "LocationName": location_name, "Geocode": location_geocode,
            "Latitude": location_latitude, "Longitude": location_longitude,
            "ElementName": "溫度", "metric": metric,
            "target_window": "[D00:00,D+1T00:00)_Asia/Taipei",
            "aggregation": f"{'max' if metric == 'high' else 'min'}_complete_24_unique_hourly_temperature_samples",
            "timestamp_basis": "DatasetInfo.Update_provider_revision",
            "issue_time": product.issue_time, "sent_time": product.sent_time,
            "response_sha256": product.raw_sha256,
        }
        written[metric] = persist_station_forecast_rows(
            conn, rows, provider="cwa_taiwan", endpoint=endpoint,
            city_timezone=city_timezone, latitude=location_latitude,
            longitude=location_longitude, captured_at=product.captured_at,
            request_params=request_params,
            cell_selection="cwa_township_district_forecast",
            elevation_param="township_area",
            downscaling_policy="cwa_operational_township_forecast",
            raw_sha256=product.raw_sha256,
        )
    return written


def ingest_cwa_township_hourly_extrema_live(
    conn: sqlite3.Connection,
    *,
    city: str = "Taipei",
    metrics: Sequence[str] = ("high", "low"),
    city_timezone: str = "Asia/Taipei",
    location_name: str = "松山區",
    location_geocode: str = "63000010",
    location_latitude: float = 25.051608,
    location_longitude: float = 121.568983,
    endpoint: str = _CWA_HOURLY_LOW_ENDPOINT,
    api_key: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Fetch F-D0047-061 once and persist each complete hourly-sampled calendar extreme."""
    return sum(
        _ingest_cwa_township_hourly_extrema_by_metric(
            conn,
            city=city,
            metrics=metrics,
            city_timezone=city_timezone,
            location_name=location_name,
            location_geocode=location_geocode,
            location_latitude=location_latitude,
            location_longitude=location_longitude,
            endpoint=endpoint,
            api_key=api_key,
            environ=environ,
        ).values()
    )


def ingest_cwa_township_hourly_low_live(
    conn: sqlite3.Connection,
    **kwargs: object,
) -> int:
    """Compatibility wrapper for callers explicitly requesting only LOW."""
    return ingest_cwa_township_hourly_extrema_live(conn, metrics=("low",), **kwargs)


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
    "cwa_township_hourly_xml": "ingest_cwa_township_hourly_extrema_live",
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
    if spec.get("metric") and adapter_kind != "cwa_township_hourly_xml":
        kw["metric"] = str(spec["metric"])
    if adapter_kind == "hko_fnd_json" and spec.get("metrics") is not None:
        raw_metrics = spec["metrics"]
        if not isinstance(raw_metrics, Sequence) or isinstance(raw_metrics, (str, bytes)):
            raise ValueError("hko_fnd metrics must be a sequence")
        kw["metrics"] = tuple(str(value) for value in raw_metrics)
    if adapter_kind == "cwa_township_hourly_xml" and spec.get("metrics") is not None:
        raw_metrics = spec["metrics"]
        if not isinstance(raw_metrics, Sequence) or isinstance(raw_metrics, (str, bytes)):
            raise ValueError("cwa hourly metrics must be a sequence")
        kw["metrics"] = tuple(str(value) for value in raw_metrics)
    elif adapter_kind == "cwa_township_hourly_xml" and spec.get("metric"):
        kw["metrics"] = (str(spec["metric"]),)
    if spec.get("endpoint"):
        kw["endpoint"] = str(spec["endpoint"])
    if adapter_kind in {"cwa_township_json", "cwa_township_hourly_xml"}:
        if spec.get("location_name"):
            kw["location_name"] = str(spec["location_name"])
        if adapter_kind == "cwa_township_hourly_xml" and spec.get("location_geocode"):
            kw["location_geocode"] = str(spec["location_geocode"])
        if adapter_kind == "cwa_township_hourly_xml" and spec.get("location_latitude") is not None:
            kw["location_latitude"] = float(spec["location_latitude"])
        if adapter_kind == "cwa_township_hourly_xml" and spec.get("location_longitude") is not None:
            kw["location_longitude"] = float(spec["location_longitude"])
        if adapter_kind == "cwa_township_json" and spec.get("element_name"):
            kw["element_name"] = str(spec["element_name"])
        if environ is not None:
            kw["environ"] = environ
    return kw


def ingest_enabled_station_sources_live(
    conn: sqlite3.Connection,
    *,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    source_ids: Sequence[str] | None = None,
) -> dict[str, int]:
    """Ingest selected ENABLED station forecasts, routed by ``adapter_kind``.

    Returns ``{source_id: rows_written}`` for each source that dispatched without error. Per-source
    fail-soft: a source whose ingest raises (or whose ``adapter_kind`` is unknown) is logged and
    omitted, so one provider outage never starves the cycle. NETWORK happens inside the dispatched
    ingest functions only. ``source_ids=None`` preserves the all-enabled bootstrap behavior; an
    explicit sequence lets the scheduler honor each provider's own source clock.
    """
    import logging  # noqa: PLC0415 - keep module import surface lean; called ~2x/cycle

    log = logging.getLogger(__name__)
    out: dict[str, int] = {}
    sources = load_station_forecast_config(root=root)
    selected = None if source_ids is None else {
        str(source_id).strip() for source_id in source_ids if str(source_id).strip()
    }
    handled: set[str] = set()
    for source_id, spec in sources.items():
        source_id = str(source_id)
        if source_id in handled:
            continue
        if selected is not None and str(source_id) not in selected:
            continue
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
            fetch_group = str(spec.get("shared_fetch_group") or "").strip()
            if adapter_kind == "cwa_township_hourly_xml" and fetch_group:
                siblings = [
                    (str(sibling_id), sibling_spec)
                    for sibling_id, sibling_spec in sources.items()
                    if isinstance(sibling_spec, Mapping)
                    and sibling_spec.get("enabled")
                    and str(sibling_spec.get("adapter_kind") or "") == adapter_kind
                    and str(sibling_spec.get("shared_fetch_group") or "").strip() == fetch_group
                    and (selected is None or str(sibling_id) in selected)
                ]
                metrics = tuple(
                    str(sibling_spec.get("metric") or "").lower()
                    for _, sibling_spec in siblings
                )
                by_metric = _ingest_cwa_township_hourly_extrema_by_metric(
                    conn, **{**kwargs, "metrics": metrics}
                )
                for sibling_id, sibling_spec in siblings:
                    metric = str(sibling_spec.get("metric") or "").lower()
                    out[sibling_id] = int(by_metric[metric])
                    handled.add(sibling_id)
                continue
            out[source_id] = int(fn(conn, **kwargs))
        except Exception as exc:  # noqa: BLE001 - one source must never abort the cycle
            log.warning(
                "station ingest: source %s (%s) failed fail-soft: %s", source_id, fn_name, exc
            )
            continue
    return out
