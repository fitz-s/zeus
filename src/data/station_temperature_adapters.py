# Created: 2026-09-29
# Last reused/audited: 2026-10-07 (KNMI key resolver env->config/knmi_secret.json; fast-obs G3a)
"""Fixed-endpoint station observations with independent receipt and valid clocks.

Provider names select parsers, never arbitrary URLs or executable config. Native
values survive storage; settlement-grade routing is a separately tested registry
claim. A decimal physical observation is not implicitly a daily extreme.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import io
from html.parser import HTMLParser
import re
import json
import math
import os
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import zipfile

import httpx
from src.data.fmi_airport_temperature import FmiTemperaturePrint

UTC = timezone.utc
CHANNELS = {
    "jma_amedas": "jma_amedas_temperature",
    "eccc_swob": "eccc_swob_temperature",
    "imgw_synop": "imgw_synop_temperature",
    "dwd_cdc": "dwd_cdc_temperature",
    "knmi_observations": "knmi_station_temperature",
    "wu_station_current": "wu_station_current_temperature",
    "wu_station_history": "wu_station_history_temperature",
    "noaa_wrh": "noaa_wrh_temperature",
    "mgm_metar": "mgm_metar_temperature",
    "imd_olbs_metar": "imd_olbs_metar_temperature",
    "metaviatelecom_metar": "metaviatelecom_metar_temperature",
}


@dataclass(frozen=True)
class StationTemperaturePrint:
    observed_at: datetime
    fetched_at: datetime
    value_native: float
    unit: str
    raw_report: str

    @property
    def temperature_c(self) -> float:
        return self.value_native if self.unit == "C" else (self.value_native - 32.0) / 1.8


def native_sample_value(sample, unit: str) -> float:
    """Preserve resolver-native Fahrenheit; only legacy FMI prints need conversion."""
    if hasattr(sample, "value_native"):
        if sample.unit != unit:
            raise ValueError("STATION_NATIVE_UNIT_MISMATCH")
        return float(sample.value_native)
    return float(sample.temperature_c) if unit == "C" else float(sample.temperature_c) * 1.8 + 32.0


_KNMI_API_KEY_ENV = "KNMI_API_KEY"
_KNMI_SECRET_CONFIG = "config/knmi_secret.json"


def resolve_knmi_api_key(*, environ=None, root=None) -> str | None:
    """KNMI Open Data key: env ``KNMI_API_KEY``, then gitignored
    ``config/knmi_secret.json`` (``{"knmi_api_key": "..."}``), else None.

    The key is never logged and never enters a print or its provenance.
    """
    env = os.environ if environ is None else environ
    key = str(env.get(_KNMI_API_KEY_ENV, "") or "").strip()
    if key:
        return key
    from pathlib import Path
    path = (root or Path(__file__).resolve().parents[2]) / _KNMI_SECRET_CONFIG
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return str(data.get("knmi_api_key") or "").strip() or None


_RESPONSE_BYTE_LIMIT = 10_000_000
_RESPONSE_DEADLINE_SECONDS = 15.0
_RETRY_AFTER_DEFAULT_SECONDS = 300.0
_RETRY_AFTER_CAP_SECONDS = 3600.0


def _bounded_body(client, method: str, url: str, **kwargs) -> bytes:
    """Read one response body under a byte limit and a total deadline.

    The limit is enforced while streaming, before the body is buffered; a
    declared Content-Length over the limit is refused without reading.
    """
    started = time.monotonic()
    with client.stream(method, url, **kwargs) as response:
        response.raise_for_status()
        declared = response.headers.get("Content-Length")
        if declared is not None and declared.isdigit() and int(declared) > _RESPONSE_BYTE_LIMIT:
            raise ValueError("STATION_RESPONSE_TOO_LARGE")
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > _RESPONSE_BYTE_LIMIT:
                raise ValueError("STATION_RESPONSE_TOO_LARGE")
            if time.monotonic() - started > _RESPONSE_DEADLINE_SECONDS:
                raise ValueError("STATION_RESPONSE_DEADLINE")
            chunks.append(chunk)
    return b"".join(chunks)


def _retry_after_seconds(value: str | None, *, floor: float) -> float:
    """Finite deferral from a Retry-After header: delta-seconds or HTTP-date."""
    seconds = None
    if value:
        try:
            seconds = float(value)
        except ValueError:
            try:
                when = parsedate_to_datetime(value)
            except (TypeError, ValueError, IndexError):
                when = None
            if when is not None and when.tzinfo is not None:
                seconds = (when - datetime.now(UTC)).total_seconds()
    if seconds is None or not math.isfinite(seconds):
        seconds = _RETRY_AFTER_DEFAULT_SECONDS
    return min(_RETRY_AFTER_CAP_SECONDS, max(floor, seconds))


_FETCH_CACHE_LOCK = threading.Lock()
_FETCH_KEY_LOCKS: dict[tuple, threading.Lock] = {}


def _cached_fetch(cache: dict, key: tuple, fetch, *, prefix: str, retry_floor: float):
    """Single-flight per key; the shared lock never spans network I/O.

    Errors are cached as errors, never as source-empty evidence.
    """
    with _FETCH_CACHE_LOCK:
        key_lock = _FETCH_KEY_LOCKS.setdefault(key, threading.Lock())
    with key_lock:
        with _FETCH_CACHE_LOCK:
            cached = cache.get(key)
        if cached is not None and time.monotonic() < cached[0]:
            if cached[3]:
                raise ValueError(prefix + cached[3])
            return cached[1], cached[2]
        try:
            value = fetch()
            received = datetime.now(UTC)
        except Exception as exc:
            delay = 60.0
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                delay = _retry_after_seconds(
                    exc.response.headers.get("Retry-After"), floor=retry_floor
                )
            with _FETCH_CACHE_LOCK:
                cache[key] = (time.monotonic() + delay, None, datetime.now(UTC), type(exc).__name__)
            raise ValueError(prefix + type(exc).__name__) from None
        with _FETCH_CACHE_LOCK:
            cache[key] = (time.monotonic() + 60.0, value, received, None)
        return value, received


_WRH_BATCH_CACHE: dict[tuple, tuple[float, dict | None, datetime, str | None]] = {}


class _WrhBatchPayload(dict):
    """Native JSON with its HTTP-body digest carried outside provider fields."""

    def __init__(self, payload: dict, response_sha256: str):
        super().__init__(payload)
        self.response_sha256 = response_sha256


def _fetch_wrh_batch(route, client):
    """One bounded request per unit/registered station-set per minute, not per city.

    This is the existing resolver product, not a promotion of a slower substitute
    for AWC. The physical METAR lane continues independently. Errors are cached
    as errors, never as source-empty evidence, and credentials never enter prints.
    """
    from src.data import noaa_wrh_timeseries as wrh
    from src.data.physical_current_sources import load_physical_current_sources
    ids = tuple(sorted({r.station_id for r in load_physical_current_sources()[0]
                        if r.provider == "noaa_wrh" and r.unit == route.unit} | {route.station_id}))

    def fetch():
        body = _bounded_body(
            client, "GET", wrh.WRH_TIMESERIES_URL,
            params=wrh._query_params(",".join(ids), unit=route.unit, start_utc=None, end_utc=None,
                                     recent_minutes=180, token=wrh.fetch_wrh_token()),
            headers=wrh._page_headers(ids[0]), timeout=6)
        return _WrhBatchPayload(json.loads(body), hashlib.sha256(body).hexdigest())

    return _cached_fetch(_WRH_BATCH_CACHE, (route.unit, ids, id(client)), fetch,
                         prefix="WRH_CURRENT_TRANSPORT_DEFERRED:", retry_floor=60.0)


class _PublicMetarPage(HTMLParser):
    """Extract data, never execute the national service's JavaScript/HTML.

    ``plain_text`` holds one entry per visible text node; script/style content
    is never weather data. ``next_data`` is MGM's JSON island only.
    """
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.next_data: list[str] = []
        self.plain_text: list[str] = []
        self._next_data = False
        self._hidden = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._hidden = tag
            self._next_data = tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__"

    def handle_endtag(self, tag):
        if tag == self._hidden:
            self._hidden = None
            self._next_data = False

    def handle_data(self, data):
        if self._next_data:
            self.next_data.append(data)
        elif self._hidden is None:
            self.plain_text.append(data)


_REPORT_HEADER_RE = re.compile(r"^(?:(?:METAR|SPECI)\s+)?(?:COR\s+)?([A-Z]{4})\s+(\d{6}Z)\b")
_EMBEDDED_HEADER_RE = re.compile(r"\b(?:METAR|SPECI)\b|\b[A-Z]{4}\s+\d{6}Z\b")
_REPORT_START_RE = re.compile(r"\b(?:METAR|SPECI)\b")


def _public_metar_value(raw: str, station: str, receipt: datetime):
    """Temperature of exactly one bounded report, or None for NIL/no value.

    Station and source-issued UTC clock are validated inside this report. A
    second report header inside it is a boundary failure, never a value source.
    """
    from src.data.metar_temperature import metar_temperature_c
    from src.data.day0_fast_obs import _kma_observation_time
    report = raw.strip()
    if report.endswith("="):
        report = report[:-1].rstrip()
    if "=" in report:
        raise ValueError("PUBLIC_METAR_REPORT_BOUNDARY")
    match = _REPORT_HEADER_RE.match(report)
    if match is None or match[1] != station:
        raise ValueError("PUBLIC_METAR_STATION_CLOCK_MISMATCH")
    body = report[match.end():]
    if _EMBEDDED_HEADER_RE.search(body):
        raise ValueError("PUBLIC_METAR_EMBEDDED_REPORT")
    if re.search(r"\bNIL\b", body):
        return None
    observed = _kma_observation_time(match[2], as_of=receipt)
    value = metar_temperature_c(report)
    if observed is None or value is None: return None
    return observed, value


def _page_reports(text_nodes: list[str], station: str) -> list[str]:
    """Terminated reports for ``station`` from visible text, one per report.

    A report runs from a METAR/SPECI header to its own '='; it never crosses a
    text-node boundary or the next report header. An unterminated report
    (truncated, or NIL without '=') carries no value.
    """
    reports = []
    for node in text_nodes:
        starts = [m.start() for m in _REPORT_START_RE.finditer(node)]
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(node)
            segment = node[start:end]
            header = _REPORT_HEADER_RE.match(segment)
            if header is None or header[1] != station:
                continue
            terminator = segment.find("=")
            if terminator < 0:
                continue
            reports.append(" ".join(segment[:terminator + 1].split()))
    return reports


def _public_metar_values(route, body: bytes, receipt: datetime):
    if len(body) > _RESPONSE_BYTE_LIMIT: raise ValueError("STATION_RESPONSE_TOO_LARGE")
    if route.unit != "C" or route.identity["provider_station"] != route.station_id:
        raise ValueError("STATION_ID_OR_UNIT_MISMATCH")
    page = _PublicMetarPage(); page.feed(body.decode("utf-8")); page.close()
    values: dict[datetime, float] = {}
    if route.provider == "mgm_metar":
        payload = json.loads("".join(page.next_data))
        groups = payload["props"]["pageProps"]["response"]
        if not isinstance(groups, list): raise ValueError("MGM_RESPONSE_SHAPE")
        rows = []
        for group in groups:
            if group.get("istInfo", {}).get("icao") == route.station_id:
                rows.extend(group.get("data", []))
        if not rows: return []
        for row in rows:
            if row.get("stationIcaoCode") != route.station_id:
                raise ValueError("STATION_ID_MISMATCH")
            clock = _utc(row["observationTimeNormal"]).replace(second=0, microsecond=0)
            if clock > receipt: continue
            raw = str(row["observationText"]).strip()
            sample = _public_metar_value(raw, route.station_id, clock + timedelta(seconds=1))
            if sample is None: continue
            observed, value = sample
            if observed != clock: raise ValueError("MGM_RAW_METADATA_CLOCK_MISMATCH")
            if observed in values and values[observed] != value:
                # Conflicting same-clock versions require new unambiguous source
                # evidence. No row-order or highest-temperature authority guess.
                raise ValueError("PUBLIC_METAR_VERSION_CONFLICT")
            values[observed] = value
    else:
        for raw in _page_reports(page.plain_text, route.station_id):
            sample = _public_metar_value(raw, route.station_id, receipt)
            if sample is None: continue
            observed, value = sample
            if observed in values and values[observed] != value:
                raise ValueError("PUBLIC_METAR_VERSION_CONFLICT")
            values[observed] = value
    return [(stamp, value, None) for stamp, value in sorted(values.items())]


_PUBLIC_METAR_CACHE: dict[tuple, tuple[float, bytes | None, datetime, str | None]] = {}


def _fetch_public_metar(route, client):
    from src.data.physical_current_sources import load_physical_current_sources
    post_data = None
    if route.provider == "mgm_metar":
        ids = sorted({r.station_id for r in load_physical_current_sources()[0]
                      if r.provider == route.provider} | {route.station_id})
        # The server actually caps responses at ten stations. Batch only the
        # requested station's deterministic partition, shared by all its cities.
        offset = ids.index(route.station_id) // 10 * 10
        group = tuple(ids[offset:offset + 10])
        url = "https://rasat.mgm.gov.tr/result"
        params = [("hours", "24"), ("obsType", "1")] + [("stations", sid) for sid in group]
        key = (route.provider, group, id(client))
    elif route.provider == "imd_olbs_metar":
        url = "https://olbs.amsschennai.gov.in/nsweb/FlightBriefing/showopmetquery.php"
        params = None
        post_data = {"icaos": route.station_id, "type": "metar"}
        key = (route.provider, route.station_id, id(client))
    elif route.provider == "metaviatelecom_metar":
        display_id = str(route.identity.get("display_id", ""))
        if not re.fullmatch(r"[0-9]{1,8}", display_id):
            raise ValueError("PUBLIC_METAR_DISPLAY_ID_INVALID")
        # The origin serves only plaintext HTTP (its HTTPS certificate is
        # self-signed); the operator accepted that risk on 2026-10-06. No TLS
        # is involved, so there is no verification to disable.
        url = "http://display.meteocenter.ru/" + display_id
        params = None
        key = (route.provider, display_id, id(client))
    else:
        raise ValueError("PUBLIC_METAR_PROVIDER_UNKNOWN")
    headers = {"User-Agent": "zeus-free-public-obs/4"}

    def fetch():
        if post_data is not None:
            return _bounded_body(client, "POST", url, data=post_data, headers=headers,
                                 timeout=6, follow_redirects=False)
        return _bounded_body(client, "GET", url, params=params, headers=headers,
                             timeout=6, follow_redirects=False)

    return _cached_fetch(_PUBLIC_METAR_CACHE, key, fetch,
                         prefix="PUBLIC_METAR_TRANSPORT_DEFERRED:", retry_floor=300.0)


def _utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("STATION_TIME_NAIVE")
    return result.astimezone(UTC)


def _sample(route, observed: datetime, value, receipt: datetime, digest: str,
            published: datetime | None = None, station_reference=None) -> StationTemperaturePrint | None:
    if isinstance(value, bool):
        return None
    value = float(value)
    if not math.isfinite(value) or value == -999 or observed > receipt:
        return None
    payload = {
        "station_id": route.station_id, "source_channel": route.source_channel,
        "provider_station": route.identity["provider_station"], "unit": route.unit,
        "value_native": value, "observed_at": observed.isoformat(),
        "provider_observed_at_ms": int(observed.timestamp() * 1000),
        "provider_published_at_ms": None if published is None else int(published.timestamp() * 1000),
        "received_at_ms": int(receipt.timestamp() * 1000), "payload_sha256": digest,
    }
    if station_reference is not None:
        reference = station_reference.to_provenance()
        if reference["fetched_at_utc"] is not None:
            receipt_ms = payload["received_at_ms"]
            reference["fetched_at_utc"] = (
                datetime.fromtimestamp(receipt_ms // 1000, UTC)
                + timedelta(milliseconds=receipt_ms % 1000)
            ).isoformat(timespec="milliseconds")
            reference["fetched_at_precision"] = "millisecond"
        payload["station_reference"] = reference
    return StationTemperaturePrint(observed, receipt, value, route.unit,
                                   json.dumps(payload, sort_keys=True, allow_nan=False))


def parse_station_payload(route, body: bytes, *, received_at: datetime,
                          source_response_sha256: str | None = None) -> tuple[StationTemperaturePrint, ...]:
    if received_at.tzinfo is None:
        raise ValueError("STATION_RECEIPT_NAIVE")
    provider = route.provider
    expected = str(route.identity["provider_station"])
    digest = hashlib.sha256(body).hexdigest()
    values = []
    station_reference = None
    if provider in {"mgm_metar", "imd_olbs_metar", "metaviatelecom_metar"}:
        values = _public_metar_values(route, body, received_at)
    elif provider == "noaa_wrh":
        from src.data.noaa_wrh_timeseries import rows_from_payload, station_reference_from_payload
        payload = json.loads(body)
        if payload.get("UNITS", {}).get("air_temp") != {"C":"Celsius", "F":"Fahrenheit"}[route.unit]:
            raise ValueError("STATION_UNIT_OR_QC_INVALID")
        view = route.identity["resolver_view"]
        rows = rows_from_payload(payload, route.station_id)
        station_reference = station_reference_from_payload(
            payload, response_sha256=digest, fetched_at=received_at,
            source_response_sha256=source_response_sha256,
        )
        for row in rows:
            if view == "all" or row.is_official_report:
                # air_temp_set_1 is the page's numeric value. Raw METAR body vs
                # T-group is not an interchangeable reconstruction of that field.
                values.append((row.utc, row.air_temp, None))
    elif provider == "jma_amedas":
        # The station is bound by the fixed station-specific resource path.
        zone = timezone(timedelta(hours=9))
        for stamp, row in json.loads(body).items():
            temp = row.get("temp")
            if isinstance(temp, list) and len(temp) == 2 and temp[1] == 0:
                observed = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=zone).astimezone(UTC)
                values.append((observed, temp[0], None))
    elif provider == "eccc_swob":
        root = ET.fromstring(body)
        elements = {e.get("name"): e for e in root.iter() if e.tag.endswith("}element")}
        if elements["icao_stn_id"].get("value") != expected:
            raise ValueError("STATION_ID_MISMATCH")
        temp = elements["air_temp"]
        quality = [e.get("value") for e in temp if e.get("name") == "qa_summary"]
        if temp.get("uom") != "°C" or quality != ["100"]:
            raise ValueError("STATION_UNIT_OR_QC_INVALID")
        published = root.find(".//{http://www.opengis.net/om/1.0}resultTime//{http://www.opengis.net/gml}timePosition")
        values.append((_utc(elements["date_tm"].get("value")), temp.get("value"),
                       _utc(published.text) if published is not None else None))
    elif provider == "imgw_synop":
        row = json.loads(body)
        if str(row["id_stacji"]) != expected:
            raise ValueError("STATION_ID_MISMATCH")
        values.append((_utc(row["data_pomiaru"] + "T" + str(row["godzina_pomiaru"]).zfill(2) + ":00:00+00:00"), row["temperatura"], None))
    elif provider == "dwd_cdc":
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            names = [n for n in archive.namelist() if n.startswith("produkt_") and n.endswith(".txt")]
            if len(names) != 1 or archive.getinfo(names[0]).file_size > 2_000_000:
                raise ValueError("STATION_ARCHIVE_SHAPE")
            text = archive.read(names[0]).decode("utf-8-sig")
        for row in csv.DictReader(io.StringIO(text), delimiter=";"):
            row = {k.strip(): v.strip() for k, v in row.items()}
            if int(row["STATIONS_ID"]) != int(expected):
                raise ValueError("STATION_ID_MISMATCH")
            values.append((datetime.strptime(row["MESS_DATUM"], "%Y%m%d%H%M").replace(tzinfo=UTC), row["TT_10"], None))
    elif provider == "knmi_observations":
        from netCDF4 import Dataset, num2date
        with Dataset("station_observations", memory=body) as dataset:
            ids = [str(s) for s in dataset.variables["station"][:].tolist()]
            if ids.count(expected) != 1 or dataset.variables["ta"].units != "°C":
                raise ValueError("STATION_ID_OR_UNIT_MISMATCH")
            i = ids.index(expected)
            times = dataset.variables["time"]
            for j, value in enumerate(dataset.variables["ta"][i, :]):
                if getattr(value, "mask", False):
                    continue
                stamp = num2date(times[j], times.units, only_use_cftime_datetimes=False).replace(tzinfo=UTC)
                values.append((stamp, value, None))
    elif provider in {"wu_station_current", "wu_station_history"}:
        data = json.loads(body)
        meta = data["metadata"]
        if meta.get("location_id") != expected or meta.get("units") != "m":
            raise ValueError("STATION_ID_OR_UNIT_MISMATCH")
        if provider == "wu_station_current":
            row = data["observation"]
            values.append((datetime.fromtimestamp(float(row["obs_time"]), UTC), row["metric"]["temp"], None))
        else:
            for row in data["observations"]:
                if row.get("obs_id") != route.station_id:
                    raise ValueError("STATION_ID_MISMATCH")
                if row.get("temp") is not None:
                    values.append((datetime.fromtimestamp(float(row["valid_time_gmt"]), UTC), row["temp"], None))
    else:
        raise ValueError("STATION_ADAPTER_UNKNOWN")
    samples = [_sample(route, stamp, value, received_at, digest, publication, station_reference)
               for stamp, value, publication in values if value is not None and value != "MSNG"]
    return tuple(sorted((s for s in samples if s is not None), key=lambda s: s.observed_at))


def valid_station_print(route, raw: str, *, observed_at: datetime, value: float) -> bool:
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        return False
    if route.provider == "fmi_wfs":
        from src.data.fmi_airport_temperature import valid_ledger_print
        return valid_ledger_print(raw, observed_at=observed_at, value=value, station=route.station)
    try:
        data = json.loads(raw)
        return (data["station_id"] == route.station_id
                and data["source_channel"] == route.source_channel
                and data["provider_station"] == route.identity["provider_station"]
                and data["unit"] == route.unit and float(data["value_native"]) == value
                and _utc(data["observed_at"]) == observed_at.astimezone(UTC))
    except (ValueError, TypeError, KeyError):
        return False


def fetch_station_temperature(route, *, start: datetime, end: datetime, client=httpx):
    if route.provider == "fmi_wfs":
        from src.data.fmi_airport_temperature import fetch_temperature
        return fetch_temperature(start=start, end=end, station=route.station, client=client)
    if route.provider in {"mgm_metar", "imd_olbs_metar", "metaviatelecom_metar"}:
        body, received = _fetch_public_metar(route, client)
        return tuple(s for s in parse_station_payload(route, body, received_at=received)
                     if start <= s.observed_at <= min(end, received))
    if route.provider == "noaa_wrh":
        payload, received = _fetch_wrh_batch(route, client)
        stations = [s for s in payload.get("STATION", []) if s.get("STID") == route.station_id]
        station_payload = {"UNITS": payload.get("UNITS", {}), "STATION": stations}
        return tuple(s for s in parse_station_payload(
            route, json.dumps(station_payload).encode(), received_at=received,
            source_response_sha256=getattr(payload, "response_sha256", None),
        )
                     if start <= s.observed_at <= min(end,received))
    station = route.identity["provider_station"]
    params, headers = {}, {"User-Agent": "zeus-station-observation/2"}
    if route.provider == "jma_amedas":
        local = end.astimezone(timezone(timedelta(hours=9)))
        hour = local.hour // 3 * 3
        url = f"https://www.jma.go.jp/bosai/amedas/data/point/{station}/{local:%Y%m%d}_{hour:02d}.json"
    elif route.provider == "eccc_swob":
        url = f"https://dd.weather.gc.ca/today/observations/swob-ml/latest/{station}-MAN-swob.xml"
    elif route.provider == "imgw_synop":
        url = f"https://danepubliczne.imgw.pl/api/data/synop/id/{station}"
    elif route.provider == "dwd_cdc":
        url = f"https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature/now/10minutenwerte_TU_{int(station):05d}_now.zip"
    elif route.provider in {"wu_station_current", "wu_station_history"}:
        from src.data.daily_obs_append import WU_API_KEY, WU_HEADERS
        kind = "current" if route.provider == "wu_station_current" else "historical"
        url = f"https://api.weather.com/v1/location/{station}/observations/{kind}.json"
        params = {"apiKey": WU_API_KEY, "units": "m"}
        if kind == "historical":
            params.update(startDate=start.astimezone(UTC).strftime("%Y%m%d"), endDate=end.astimezone(UTC).strftime("%Y%m%d"))
        headers.update(WU_HEADERS)
    elif route.provider == "knmi_observations":
        key = resolve_knmi_api_key()
        if not key:
            raise ValueError("KNMI_API_KEY_UNAVAILABLE")
        base = "https://api.dataplatform.knmi.nl/open-data/v1/datasets/10-minute-in-situ-meteorological-observations/versions/1.0/files"
        response = client.get(base, params={"maxKeys": 1, "sorting": "desc", "orderBy": "filename"}, headers={**headers, "Authorization": key}, timeout=6)
        response.raise_for_status()
        name = response.json()["files"][0]["filename"]
        if not name.startswith("KMDS__OPER_P___10M_OBS_L2_") or "/" in name or ".." in name:
            raise ValueError("KNMI_FILENAME_INVALID")
        response = client.get(base + "/" + name + "/url", headers={**headers, "Authorization": key}, timeout=6)
        response.raise_for_status()
        url = response.json()["temporaryDownloadUrl"]
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.username or not (parsed.hostname or "").endswith((".knmi.nl", ".amazonaws.com")):
            raise ValueError("KNMI_DOWNLOAD_HOST_INVALID")
    else:
        raise ValueError("STATION_ADAPTER_UNKNOWN")
    body = _bounded_body(client, "GET", url, params=params, headers=headers, timeout=6)
    received = datetime.now(UTC)
    return tuple(s for s in parse_station_payload(route, body, received_at=received)
                 if start <= s.observed_at <= end)


_WRH_CURRENT_PRODUCT_CACHE: dict = {}
_WRH_CURRENT_PRODUCT_LOCK = threading.Lock()


def _current_wrh_cached_fetch(key, fetch, *, prefix):
    """Bound dynamic native-body custody to 4 entries / 20 MB in memory.

    The dedicated serial lane already owns WRH acquisition. This separate lock
    makes cache eviction safe for direct callers without holding the generic
    provider cache lock over network I/O or accumulating dynamic key locks.
    """
    key = ("wrh_current_snapshot", key)
    with _WRH_CURRENT_PRODUCT_LOCK:
        try:
            return _cached_fetch(_WRH_CURRENT_PRODUCT_CACHE, key, fetch,
                                 prefix=prefix, retry_floor=60.0)
        finally:
            with _FETCH_CACHE_LOCK:
                now = time.monotonic()
                for old, entry in tuple(_WRH_CURRENT_PRODUCT_CACHE.items()):
                    if old != key and entry[0] <= now:
                        del _WRH_CURRENT_PRODUCT_CACHE[old]
                        _FETCH_KEY_LOCKS.pop(old, None)
                def body_bytes():
                    return sum(len(entry[1][0]) for entry in _WRH_CURRENT_PRODUCT_CACHE.values()
                               if entry[1] is not None and isinstance(entry[1][0], bytes))
                while len(_WRH_CURRENT_PRODUCT_CACHE) > 4 or body_bytes() > 20_000_000:
                    old = next(iter(_WRH_CURRENT_PRODUCT_CACHE))
                    del _WRH_CURRENT_PRODUCT_CACHE[old]
                    _FETCH_KEY_LOCKS.pop(old, None)
                # This wrapper serializes own callers; no waiter can retain an
                # evicted key lock. Other provider caches are untouched.
                for old in tuple(_FETCH_KEY_LOCKS):
                    if old and old[0] == "wrh_current_snapshot" and old not in _WRH_CURRENT_PRODUCT_CACHE:
                        _FETCH_KEY_LOCKS.pop(old, None)


def iter_current_noaa_wrh_products(scopes, *, client=httpx):
    """Bounded full-page requests for held/resting scopes only, grouped by unit.

    Existing fast rolling-tail acquisition is unchanged. There is at most one
    request per unit/station-set per minute, using the same body/deadline caps;
    a malformed/failed product never becomes an explicit empty publication.
    """
    from src.data import noaa_wrh_timeseries as wrh
    from zoneinfo import ZoneInfo
    from dataclasses import replace
    import math

    groups = {}
    now = datetime.now(UTC)
    for city, target in scopes:
        try:
            day = datetime.fromisoformat(target).date()
            local_start = datetime.combine(day, datetime.min.time(), ZoneInfo(city.timezone)).astimezone(UTC)
            if (str(city.settlement_source_type).lower() != "noaa" or local_start > now
                    or now - local_start > timedelta(days=wrh.MAX_REQUEST_WINDOW_DAYS, minutes=-180)):
                continue
            groups.setdefault(city.settlement_unit, []).append((city, target, local_start))
        except (ValueError, AttributeError):
            continue
    for unit, group in sorted(groups.items()):
        ids = tuple(sorted({city.wu_station.upper() for city, target, start in group}))
        key = (unit, ids, tuple(sorted({target for city, target, start in group})), id(client))
        earliest = min(start for city, target, start in group)
        def fetch():
            started = datetime.now(UTC)
            minutes = math.ceil((started - earliest).total_seconds() / 60) + 180
            if minutes > wrh.MAX_REQUEST_WINDOW_DAYS * 24 * 60:
                raise ValueError("WRH_CURRENT_PRODUCT_WINDOW_EXPIRED")
            body = _bounded_body(
                client, "GET", wrh.WRH_TIMESERIES_URL,
                params=wrh._query_params(",".join(ids), unit=unit, start_utc=None, end_utc=None,
                                         recent_minutes=minutes, token=wrh.fetch_wrh_token()),
                headers=wrh._page_headers(ids[0]), timeout=6)
            return body, started, started - timedelta(minutes=minutes)
        try:
            (body, started, coverage_start), received = _current_wrh_cached_fetch(
                key, fetch, prefix="WRH_CURRENT_PRODUCT_DEFERRED:",
            )
        except ValueError:
            continue
        for city, target, _start in group:
            try:
                product = replace(wrh.product_from_response(
                    body, city.wu_station.upper(), unit=unit, fetched_at=received,
                    source_response_sha256=hashlib.sha256(body).hexdigest(), request_station_ids=ids,
                ), request_started_at=started, coverage_start_utc=coverage_start, coverage_end_utc=started)
                wrh.current_snapshot_from_product(product, city=city, target_date=target, as_of=received)
            except (ValueError, wrh.WrhError):
                continue
            yield city, target, product



def fetch_current_noaa_wrh_products(scopes, *, client=httpx):
    """Compatibility collection; ingest streams each unit's qualified products."""
    return tuple(iter_current_noaa_wrh_products(scopes, client=client))


def iter_noaa_wrh_completed_owner_recovery(scope, *, client=httpx):
    """At most one bounded explicit-day request for an already-typed old owner.

    The caller selects it after current held work. Provider refusals or malformed
    bodies remain unavailable; this never changes generic backfill eligibility.
    """
    if scope is None:
        return
    from dataclasses import replace
    from zoneinfo import ZoneInfo
    from src.data import noaa_wrh_timeseries as wrh
    city, target = scope
    day = datetime.fromisoformat(target).date()
    zone = ZoneInfo(city.timezone)
    start = datetime.combine(day, datetime.min.time(), zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone).astimezone(UTC)
    if end > datetime.now(UTC) or end-start > timedelta(days=wrh.MAX_REQUEST_WINDOW_DAYS):
        return
    key = ("completed_owner", city.wu_station, city.settlement_unit, target, id(client))
    def fetch():
        started = datetime.now(UTC)
        body = _bounded_body(client, "GET", wrh.WRH_TIMESERIES_URL,
            params=wrh._query_params(city.wu_station, unit=city.settlement_unit,
                                     start_utc=start, end_utc=end, recent_minutes=None, token=wrh.fetch_wrh_token()),
            headers=wrh._page_headers(city.wu_station), timeout=6)
        return body, started
    try:
        (body, started), received = _current_wrh_cached_fetch(key, fetch,
            prefix="WRH_COMPLETED_OWNER_RECOVERY_DEFERRED:")
        product = replace(wrh.product_from_response(body, city.wu_station, unit=city.settlement_unit,
            fetched_at=received, source_response_sha256=hashlib.sha256(body).hexdigest()),
            request_started_at=started, coverage_start_utc=start, coverage_end_utc=end)
        wrh.current_snapshot_from_product(product, city=city, target_date=target, as_of=received)
    except (ValueError, wrh.WrhError):
        return
    yield city, target, product
