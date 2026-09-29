"""FMI EFHK current-temperature prints; physical state, never settlement truth."""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx


SOURCE_CHANNEL = "fmi_airport_temperature"
STATION_ID = "EFHK"
FMISID = "100968"
WMO = "2974"
STATION_NAME = "Vantaa Helsinki-Vantaan lentoasema"
TEMPERATURE_PROPERTY = (
    "https://opendata.fmi.fi/meta?observableProperty=observation"
    "&param=temperature&language=eng"
)
ENDPOINT = "https://opendata.fmi.fi/wfs"
NS = {
    "wfs": "http://www.opengis.net/wfs/2.0",
    "om": "http://www.opengis.net/om/2.0",
    "gml": "http://www.opengis.net/gml/3.2",
    "gmlcov": "http://www.opengis.net/gmlcov/1.0",
    "swe": "http://www.opengis.net/swe/2.0",
    "target": "http://xml.fmi.fi/namespace/om/atmosphericfeatures/1.1",
    "xlink": "http://www.w3.org/1999/xlink",
}


@dataclass(frozen=True)
class FmiStation:
    station_id: str
    fmisid: str
    wmo: str
    name: str
    latitude: float
    longitude: float


DEFAULT_STATION = FmiStation(STATION_ID, FMISID, WMO, STATION_NAME, 60.32937, 24.97274)


@dataclass(frozen=True)
class FmiTemperaturePrint:
    observed_at: datetime
    fetched_at: datetime
    temperature_c: float
    raw_report: str


def parse_temperature_metadata(payload: str) -> None:
    """The WFS tuples carry no unit; require FMI's parameter metadata."""
    root = ET.fromstring(payload)
    if root.attrib.get(f"{{{NS['gml']}}}id") != "temperature":
        raise ValueError("FMI_PARAMETER_MISMATCH")
    units = root.findall(".//{*}uom")
    if len(units) != 1 or units[0].get("uom") != "degC":
        raise ValueError("FMI_UNIT_MISMATCH")


def parse_temperature_coverage(
    payload: str, *, fetched_at: datetime, station: FmiStation = DEFAULT_STATION,
) -> tuple[FmiTemperaturePrint, ...]:
    if fetched_at.tzinfo is None:
        raise ValueError("FMI_FETCH_TIME_NAIVE")
    root = ET.fromstring(payload)
    if root.tag != f"{{{NS['wfs']}}}FeatureCollection":
        raise ValueError("FMI_COVERAGE_MISMATCH")
    observations = root.findall(".//om:result/gmlcov:MultiPointCoverage", NS)
    locations = root.findall(".//target:Location", NS)
    if len(observations) != 1 or len(locations) != 1:
        raise ValueError("FMI_STATION_COUNT_MISMATCH")
    location = locations[0]
    identifiers = location.findall("gml:identifier", NS)
    names = location.findall("gml:name", NS)
    if (
        len(identifiers) != 1
        or identifiers[0].text != station.fmisid
        or identifiers[0].get("codeSpace", "").split("/")[-1] != "fmisid"
        or not any(n.text == station.name for n in names)
        or not any(n.text == station.wmo and n.get("codeSpace", "").endswith("/wmo") for n in names)
    ):
        raise ValueError("FMI_STATION_IDENTITY_MISMATCH")
    properties = root.findall(".//om:observedProperty", NS)
    fields = observations[0].findall(".//swe:field", NS)
    if (
        len(properties) != 1
        or properties[0].get(f"{{{NS['xlink']}}}href") != TEMPERATURE_PROPERTY
        or len(fields) != 1
        or fields[0].get("name") != "temperature"
        or fields[0].get(f"{{{NS['xlink']}}}href") != TEMPERATURE_PROPERTY
    ):
        raise ValueError("FMI_PARAMETER_MISMATCH")
    positions = observations[0].find(".//gmlcov:positions", NS)
    values = observations[0].find(".//gml:doubleOrNilReasonTupleList", NS)
    if positions is None or values is None:
        raise ValueError("FMI_COVERAGE_EMPTY")
    coordinates = (positions.text or "").split()
    readings = (values.text or "").split()
    if len(coordinates) % 3 or len(coordinates) // 3 != len(readings):
        raise ValueError("FMI_COVERAGE_SHAPE_MISMATCH")
    now = fetched_at.astimezone(timezone.utc)
    prints = []
    for i, raw_value in enumerate(readings):
        latitude, longitude, stamp = map(float, coordinates[3 * i:3 * i + 3])
        if not (abs(latitude - station.latitude) < 0.0001 and abs(longitude - station.longitude) < 0.0001):
            raise ValueError("FMI_STATION_COORDINATES_MISMATCH")
        if not math.isfinite(stamp):
            raise ValueError("FMI_OBSERVATION_TIME_INVALID")
        observed = datetime.fromtimestamp(stamp, timezone.utc)
        value = float(raw_value)
        if observed > now or not math.isfinite(value):
            continue
        prints.append(FmiTemperaturePrint(
            observed_at=observed,
            fetched_at=now,
            temperature_c=value,
            raw_report=json.dumps({
                "fmisid": station.fmisid, "wmo": station.wmo, "station": station.name,
                "property": TEMPERATURE_PROPERTY, "unit": "degC",
                "observed_at": observed.isoformat(), "value": raw_value,
                "availability": "local_fetch_only",
                "provider_observed_at_ms": int(observed.timestamp() * 1000),
                "received_at_ms": int(now.timestamp() * 1000),
                "provider_published_at_ms": None,  # Not exposed by WFS.
            }, sort_keys=True, separators=(",", ":")),
        ))
    return tuple(prints)


def valid_ledger_print(raw_report: str, *, observed_at: datetime, value: float,
                       station: FmiStation = DEFAULT_STATION) -> bool:
    """Reject a manually mistagged print on the physical current-state read."""
    try:
        record = json.loads(raw_report)
        return (
            record["fmisid"] == station.fmisid
            and record["wmo"] == station.wmo
            and record["station"] == station.name
            and record["property"] == TEMPERATURE_PROPERTY
            and record["unit"] == "degC"
            and record["availability"] == "local_fetch_only"
            and datetime.fromisoformat(record["observed_at"]).astimezone(timezone.utc)
            == observed_at.astimezone(timezone.utc)
            and float(record["value"]) == value
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def fetch_temperature(
    *, start: datetime, end: datetime, station: FmiStation = DEFAULT_STATION,
    client: Any = httpx,
) -> tuple[FmiTemperaturePrint, ...]:
    """One bounded ten-minute-grid request; receipt is captured after response."""
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("FMI_REQUEST_WINDOW_INVALID")
    response = client.get(TEMPERATURE_PROPERTY, timeout=4.0)
    response.raise_for_status()
    parse_temperature_metadata(response.text)
    response = client.get(ENDPOINT, params={
        "service": "WFS", "version": "2.0.0", "request": "getFeature",
        "storedquery_id": "fmi::observations::weather::multipointcoverage",
        "fmisid": station.fmisid,
        "starttime": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endtime": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parameters": "temperature", "timestep": "10",
    }, timeout=6.0)
    response.raise_for_status()
    fetched_at = datetime.now(timezone.utc)
    return tuple(p for p in parse_temperature_coverage(response.text, fetched_at=fetched_at, station=station)
                 if start <= p.observed_at <= end)


def fetch_efhk_temperature(*, start: datetime, end: datetime, client: Any = httpx) -> tuple[FmiTemperaturePrint, ...]:
    """Compatibility entry point; all stations share fetch_temperature."""
    return fetch_temperature(start=start, end=end, client=client)
