# Created: 2026-09-29
# Last reused/audited: 2026-09-29
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


_WRH_BATCH_LOCK = threading.Lock()
_WRH_BATCH_CACHE: dict[tuple, tuple[float, dict, datetime, str | None]] = {}


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
    key = (route.unit, ids, id(client))
    with _WRH_BATCH_LOCK:
        now = time.monotonic()
        cached = _WRH_BATCH_CACHE.get(key)
        if cached is not None and now < cached[0]:
            if cached[3]:
                raise ValueError("WRH_CURRENT_TRANSPORT_DEFERRED:" + cached[3])
            return cached[1], cached[2]
        try:
            response = client.get(wrh.WRH_TIMESERIES_URL,
                params=wrh._query_params(",".join(ids),unit=route.unit,start_utc=None,end_utc=None,
                                         recent_minutes=180,token=wrh.fetch_wrh_token()),
                headers=wrh._page_headers(ids[0]),timeout=6)
            response.raise_for_status()
            receipt = datetime.now(UTC)
            payload = response.json()
            if len(response.content) > 10_000_000:
                raise ValueError("WRH_CURRENT_RESPONSE_TOO_LARGE")
            _WRH_BATCH_CACHE[key] = (now + 60.0, payload, receipt, None)
            return payload, receipt
        except Exception as exc:
            delay = 60.0
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                try: delay = max(delay, float(exc.response.headers.get("Retry-After", "300")))
                except ValueError: delay = 300.0
            _WRH_BATCH_CACHE[key] = (now + delay, {}, datetime.now(UTC), type(exc).__name__)
            raise ValueError("WRH_CURRENT_TRANSPORT_DEFERRED:" + type(exc).__name__) from None


class _PublicMetarPage(HTMLParser):
    """Extract data, never execute the national service's JavaScript/HTML."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.next_data: list[str] = []
        self.plain_text: list[str] = []
        self._script = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and attributes.get("id") == "__NEXT_DATA__":
            self._script = True

    def handle_endtag(self, tag):
        if tag == "script": self._script = False

    def handle_data(self, data):
        self.plain_text.append(data)
        if self._script: self.next_data.append(data)


def _public_metar_value(raw: str, station: str, receipt: datetime):
    from src.data.metar_temperature import metar_temperature_c
    from src.data.day0_fast_obs import _kma_observation_time
    # Station and source-issued UTC clock are both mandatory. Do not match a
    # foreign report merely because the page title names the requested airport.
    match = re.match(r"^(?:(?:METAR|SPECI)\s+)?(?:COR\s+)?([A-Z]{4})\s+(\d{6}Z)\b", raw.strip())
    if match is None or match[1] != station:
        raise ValueError("PUBLIC_METAR_STATION_CLOCK_MISMATCH")
    observed = _kma_observation_time(match[2], as_of=receipt)
    value = metar_temperature_c(raw)
    if observed is None or value is None: return None
    return observed, value


def _public_metar_values(route, body: bytes, receipt: datetime):
    if len(body) > 10_000_000: raise ValueError("STATION_RESPONSE_TOO_LARGE")
    if route.unit != "C" or route.identity["provider_station"] != route.station_id:
        raise ValueError("STATION_ID_OR_UNIT_MISMATCH")
    page = _PublicMetarPage(); page.feed(body.decode("utf-8"))
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
        text = " ".join(page.plain_text)
        for raw in re.finditer(r"(?:(?:METAR|SPECI)\s+)?(?:COR\s+)?" + re.escape(route.station_id) + r"\s+\d{6}Z[^=]+=", text):
            sample = _public_metar_value(raw[0], route.station_id, receipt)
            if sample is None: continue
            observed, value = sample
            if observed in values and values[observed] != value:
                raise ValueError("PUBLIC_METAR_VERSION_CONFLICT")
            values[observed] = value
    return [(stamp, value, None) for stamp, value in sorted(values.items())]


_PUBLIC_METAR_LOCK = threading.Lock()
_PUBLIC_METAR_CACHE: dict[tuple, tuple[float, bytes, datetime, str | None]] = {}


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
    else:
        raise ValueError("PUBLIC_METAR_PROVIDER_UNKNOWN")
    with _PUBLIC_METAR_LOCK:
        now = time.monotonic(); old = _PUBLIC_METAR_CACHE.get(key)
        if old and now < old[0]:
            if old[3]: raise ValueError("PUBLIC_METAR_TRANSPORT_DEFERRED:" + old[3])
            return old[1], old[2]
        try:
            headers = {"User-Agent": "zeus-free-public-obs/4"}
            r = (client.post(url, data=post_data, headers=headers, timeout=6, follow_redirects=False)
                 if post_data is not None else
                 client.get(url, params=params, headers=headers, timeout=6, follow_redirects=False))
            r.raise_for_status()
            if len(r.content) > 10_000_000: raise ValueError("STATION_RESPONSE_TOO_LARGE")
            received = datetime.now(UTC)
            _PUBLIC_METAR_CACHE[key] = (time.monotonic() + 60, r.content, received, None)
            return r.content, received
        except Exception as exc:
            delay = 60.0
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                try: delay = max(300.0, float(exc.response.headers.get("Retry-After", "300")))
                except ValueError: delay = 300.0
            _PUBLIC_METAR_CACHE[key] = (time.monotonic() + delay, b"", datetime.now(UTC), type(exc).__name__)
            raise ValueError("PUBLIC_METAR_TRANSPORT_DEFERRED:" + type(exc).__name__) from None


def _utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("STATION_TIME_NAIVE")
    return result.astimezone(UTC)


def _sample(route, observed: datetime, value, receipt: datetime, digest: str,
            published: datetime | None = None) -> StationTemperaturePrint | None:
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
    return StationTemperaturePrint(observed, receipt, value, route.unit,
                                   json.dumps(payload, sort_keys=True, allow_nan=False))


def parse_station_payload(route, body: bytes, *, received_at: datetime) -> tuple[StationTemperaturePrint, ...]:
    if received_at.tzinfo is None:
        raise ValueError("STATION_RECEIPT_NAIVE")
    provider = route.provider
    expected = str(route.identity["provider_station"])
    digest = hashlib.sha256(body).hexdigest()
    values = []
    if provider in {"mgm_metar", "imd_olbs_metar"}:
        values = _public_metar_values(route, body, received_at)
    elif provider == "noaa_wrh":
        from src.data.noaa_wrh_timeseries import rows_from_payload
        payload = json.loads(body)
        if payload.get("UNITS", {}).get("air_temp") != {"C":"Celsius", "F":"Fahrenheit"}[route.unit]:
            raise ValueError("STATION_UNIT_OR_QC_INVALID")
        view = route.identity["resolver_view"]
        for row in rows_from_payload(payload, route.station_id):
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
    samples = [_sample(route, stamp, value, received_at, digest, publication)
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
    if route.provider in {"mgm_metar", "imd_olbs_metar"}:
        body, received = _fetch_public_metar(route, client)
        return tuple(s for s in parse_station_payload(route, body, received_at=received)
                     if start <= s.observed_at <= min(end, received))
    if route.provider == "noaa_wrh":
        payload, received = _fetch_wrh_batch(route, client)
        stations = [s for s in payload.get("STATION", []) if s.get("STID") == route.station_id]
        station_payload = {"UNITS": payload.get("UNITS", {}), "STATION": stations}
        return tuple(s for s in parse_station_payload(route,json.dumps(station_payload).encode(),received_at=received)
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
        key = os.environ.get("KNMI_API_KEY")
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
    response = client.get(url, params=params, headers=headers, timeout=6)
    response.raise_for_status()
    received = datetime.now(UTC)
    if len(response.content) > 10_000_000:
        raise ValueError("STATION_RESPONSE_TOO_LARGE")
    return tuple(s for s in parse_station_payload(route, response.content, received_at=received)
                 if start <= s.observed_at <= end)
