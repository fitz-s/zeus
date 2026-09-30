# Created: 2026-09-29
# Last reused/audited: 2026-09-29
"""Fixed-endpoint station observations with independent receipt and valid clocks.

Provider names select parsers, never arbitrary URLs or executable config. Native
values survive storage; settlement-grade routing is a separately tested registry
claim. A decimal physical observation is not implicitly a daily extreme.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
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
}


def _utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("STATION_TIME_NAIVE")
    return result.astimezone(UTC)


def _sample(route, observed: datetime, value, receipt: datetime, digest: str,
            published: datetime | None = None) -> FmiTemperaturePrint | None:
    value = float(value)
    if not math.isfinite(value) or value == -999 or observed > receipt:
        return None
    payload = {
        "station_id": route.station_id, "source_channel": route.source_channel,
        "provider_station": route.identity["provider_station"], "unit": "C",
        "value_native": value, "observed_at": observed.isoformat(),
        "provider_observed_at_ms": int(observed.timestamp() * 1000),
        "provider_published_at_ms": None if published is None else int(published.timestamp() * 1000),
        "received_at_ms": int(receipt.timestamp() * 1000), "payload_sha256": digest,
    }
    return FmiTemperaturePrint(observed, receipt, value,
                               json.dumps(payload, sort_keys=True, allow_nan=False))


def parse_station_payload(route, body: bytes, *, received_at: datetime) -> tuple[FmiTemperaturePrint, ...]:
    if received_at.tzinfo is None:
        raise ValueError("STATION_RECEIPT_NAIVE")
    provider = route.provider
    expected = str(route.identity["provider_station"])
    digest = hashlib.sha256(body).hexdigest()
    values = []
    if provider == "jma_amedas":
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
                and data["unit"] == "C" and float(data["value_native"]) == value
                and _utc(data["observed_at"]) == observed_at.astimezone(UTC))
    except (ValueError, TypeError, KeyError):
        return False


def fetch_station_temperature(route, *, start: datetime, end: datetime, client=httpx):
    if route.provider == "fmi_wfs":
        from src.data.fmi_airport_temperature import fetch_temperature
        return fetch_temperature(start=start, end=end, station=route.station, client=client)
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
