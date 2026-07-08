# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator v4 problematic-20 best-sources audit
#   (zeus_problematic20_best_sources_v4.csv; reconciliation
#   docs/evidence/source_truth/problematic20_v4_reconciliation.md). Singapore
#   (settlement station WSSS Changi) gains NEA data.gov.sg real-time air
#   temperature as high-frequency day0 observation context, never a settlement
#   replacement. API shape verified live
#   2026-06-17 against api-open.data.gov.sg/v2/real-time/api/air-temperature
#   (16 stations; readingUnit "deg C"; readings[0].data[] of {stationId, value};
#   nearest station to WSSS = S24 "Upper Changi Road North" at 0.040 km).
"""Singapore NEA real-time air-temperature observation parser.

First principles
----------------
NEA (data.gov.sg) publishes a network of air-temperature sensors across
Singapore at minute/5-min cadence, free, no key, commercial-reuse permitted.
The sensor nearest the WSSS Changi settlement station (S24 "Upper Changi Road
North", ~0.04 km from the configured WSSS coords) is a VERY high-frequency
day0 covariate.

Critical semantic
-----------------
  NEA is a Changi-AREA station network, NOT the WSSS settlement station. Even
  the nearest sensor is a DIFFERENT physical instrument from WU/METAR's WSSS
  ASOS. NEA therefore never acts as a settlement truth source:
    - is_settlement_faithful is hard-coded False on every NEA record;
    - settlement for Singapore stays WU/METAR exact-WSSS (the existing
      wu_icao_history + ogimet METAR path is untouched by this module).

Provenance law (source + station + distance on EVERY datum; the v4/Zeus rule):
  Every NeaObsReading carries source_id ("nea_sg_air_temperature"), the chosen
  NEA station_id, the station name, and distance_km from the WSSS settlement
  coords. The nearest station is resolved by haversine against the city's
  configured WSSS lat/lon (config/cities.json Singapore), so the provenance is
  reproducible and the area-vs-settlement gap (distance_km) is explicit on the
  reading rather than hidden.

Integration point (NEVER wired; R1-b 2026-07-08 deleted src/forecast/observation_precision_fusion.py
  — zero live callers, module never reached the seam it was written for. This adapter is now a
  dangling shape-conversion helper kept for its ObsSourceReading-compatible kwargs shape; a future
  day0 multi-source observation fusion module would be the real target, not the deleted one):
  nea_obs_to_fusion_reading() below adapts a
  NeaObsReading into that shape with is_settlement_faithful=False, so when the
  multi-source day0 observation fusion is wired for Singapore, NEA enters as a
  correlated-but-distinct source (its station_id differs from WSSS, so it is
  treated as an independent station that down-weights via station-mismatch sigma).
  The immediate deliverable here is NEA being FETCHABLE + RESOLVABLE with correct
  provenance — full fusion wiring is a later step.

Fail-soft law: any network / parse error yields the source being ABSENT
(empty list / None), never a crash — mirrors day0_fast_obs.fetch_metar_reports.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

UTC = timezone.utc

#: NEA real-time air temperature, v2 endpoint (verified live 2026-06-17, no key).
NEA_ENDPOINT = "https://api-open.data.gov.sg/v2/real-time/api/air-temperature"

#: Legacy v1 endpoint — used as a fail-over only if the v2 path is unavailable.
#: Different JSON shape (metadata.stations / items[].readings[]); parsed by the
#: same tolerant parser which accepts both shapes.
NEA_ENDPOINT_LEGACY = "https://api.data.gov.sg/v1/environment/air-temperature"

#: Canonical source id carried in provenance on every NEA datum.
NEA_SOURCE_ID = "nea_sg_air_temperature"

#: NEA reports air temperature in degrees Celsius (readingUnit "deg C").
NEA_NATIVE_UNIT = "C"

#: City whose WSSS settlement station NEA can contextualize. Singleton today (operator v4
#: lists Singapore as the only net-new free public source from the audit).
NEA_CITY_NAME = "Singapore"

#: Throttle: NEA refreshes ~1/min; do not hammer a free government API.
DEFAULT_MIN_FETCH_INTERVAL_S = 60.0


@dataclass(frozen=True)
class NeaStation:
    """One NEA air-temperature station and its distance from the settlement coord."""

    station_id: str
    name: str
    lat: float
    lon: float
    distance_km: float  # haversine from the city's WSSS settlement coords


@dataclass(frozen=True)
class NeaObsReading:
    """A single NEA air-temperature reading with full station-identity provenance.

    Settlement invariant: ``is_settlement_faithful`` is ALWAYS False — NEA is a
    Changi-AREA sensor, never the WSSS settlement instrument. ``source_id``,
    ``station_id``, ``station_name`` and ``distance_km`` are carried on every
    datum (the v4/Zeus provenance law).
    """

    source_id: str
    station_id: str
    station_name: str
    distance_km: float
    value_c: float
    timestamp: datetime  # the reading's publication time (UTC)
    is_settlement_faithful: bool = False  # never settlement truth

    def __post_init__(self) -> None:
        # Hard guard: a NEA reading must NEVER be constructed as settlement-faithful.
        if self.is_settlement_faithful:
            raise ValueError(
                "NeaObsReading.is_settlement_faithful must be False — NEA is a "
                "Changi-area source, never the WSSS settlement station."
            )
        if not math.isfinite(float(self.value_c)):
            raise ValueError("NeaObsReading.value_c must be finite")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2.0) ** 2
    return 2.0 * radius * math.asin(math.sqrt(a))


def _iter_station_records(payload: object) -> list[dict[str, Any]]:
    """Yield {id, name, lat, lon} dicts from either the v2 or legacy payload.

    v2:     data.stations[] {id, name, location.{latitude,longitude}}
    legacy: metadata.stations[] {id, name, location.{latitude,longitude}}
    Tolerant: a malformed station row is skipped (debug-logged).
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    stations = None
    if isinstance(data, dict) and isinstance(data.get("stations"), list):
        stations = data["stations"]  # v2
    elif isinstance(payload.get("metadata"), dict) and isinstance(
        payload["metadata"].get("stations"), list
    ):
        stations = payload["metadata"]["stations"]  # legacy
    if not isinstance(stations, list):
        return []
    out: list[dict[str, Any]] = []
    for row in stations:
        if not isinstance(row, dict):
            continue
        try:
            sid = str(row.get("id") or row.get("device_id") or row.get("deviceId") or "").strip()
            loc = row.get("location") or {}
            lat = float(loc["latitude"])
            lon = float(loc["longitude"])
            if not sid:
                continue
            out.append({"id": sid, "name": str(row.get("name") or sid), "lat": lat, "lon": lon})
        except (TypeError, ValueError, KeyError) as exc:
            logger.debug("NEA station row skipped: %s", exc)
    return out


def _iter_value_records(payload: object) -> tuple[dict[str, float], Optional[datetime]]:
    """Return ({station_id: value_c}, latest_timestamp_utc) from v2 or legacy.

    v2:     data.readings[] (one element) {timestamp, data[] of {stationId, value}}
    legacy: items[] (one element) {timestamp, readings[] of {station_id, value}}
    Tolerant: malformed value rows are skipped.
    """
    if not isinstance(payload, dict):
        return {}, None
    data = payload.get("data")
    reading_block = None
    value_key = None  # the per-datum station-id key differs between shapes
    inner_key = None
    if isinstance(data, dict) and isinstance(data.get("readings"), list) and data["readings"]:
        reading_block = data["readings"][0]  # v2
        inner_key, value_key = "data", "stationId"
    elif isinstance(payload.get("items"), list) and payload["items"]:
        reading_block = payload["items"][0]  # legacy
        inner_key, value_key = "readings", "station_id"
    if not isinstance(reading_block, dict):
        return {}, None
    ts = _parse_ts(reading_block.get("timestamp"))
    values: dict[str, float] = {}
    inner = reading_block.get(inner_key)
    if isinstance(inner, list):
        for datum in inner:
            if not isinstance(datum, dict):
                continue
            try:
                sid = str(datum.get(value_key) or "").strip()
                val = float(datum["value"])
                if sid:
                    values[sid] = val
            except (TypeError, ValueError, KeyError) as exc:
                logger.debug("NEA value row skipped: %s", exc)
    return values, ts


def _parse_ts(raw: object) -> Optional[datetime]:
    """Parse an ISO8601 timestamp (e.g. '2026-06-17T22:54:00+08:00') to UTC."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def nearest_station(
    payload: object, *, settlement_lat: float, settlement_lon: float
) -> Optional[NeaStation]:
    """Resolve the NEA station nearest the settlement coords (haversine).

    Returns None when the payload carries no usable stations. Distance is the
    AREA-vs-settlement gap recorded as provenance on every downstream reading.
    """
    records = _iter_station_records(payload)
    if not records:
        return None
    best: Optional[NeaStation] = None
    for rec in records:
        dist = haversine_km(settlement_lat, settlement_lon, rec["lat"], rec["lon"])
        if best is None or dist < best.distance_km:
            best = NeaStation(
                station_id=rec["id"], name=rec["name"], lat=rec["lat"], lon=rec["lon"],
                distance_km=dist,
            )
    return best


def parse_nea_payload(
    payload: object, *, settlement_lat: float, settlement_lon: float
) -> Optional[NeaObsReading]:
    """Parse a NEA payload into the nearest-station reading, or None.

    Selects the station nearest the WSSS settlement coords, reads its current
    value, and stamps source_id + station identity + distance_km provenance.
    None when no station / no value for the nearest station is present.
    """
    station = nearest_station(
        payload, settlement_lat=settlement_lat, settlement_lon=settlement_lon
    )
    if station is None:
        return None
    values, ts = _iter_value_records(payload)
    value = values.get(station.station_id)
    if value is None:
        logger.debug("NEA nearest station %s has no current value", station.station_id)
        return None
    return NeaObsReading(
        source_id=NEA_SOURCE_ID,
        station_id=station.station_id,
        station_name=station.name,
        distance_km=station.distance_km,
        value_c=float(value),
        timestamp=ts or datetime.now(UTC),
        is_settlement_faithful=False,
    )


def fetch_nea_reading(
    *,
    settlement_lat: float,
    settlement_lon: float,
    timeout: float = 15.0,
    endpoint: str = NEA_ENDPOINT,
    fallback_endpoint: str = NEA_ENDPOINT_LEGACY,
) -> Optional[NeaObsReading]:
    """Fetch + parse the nearest-Changi NEA reading. Fail-soft -> None.

    Tries the v2 endpoint first; on any HTTP/parse failure (incl. 404) falls
    over to the legacy v1 endpoint. Any error on BOTH returns None (the source
    is simply absent this cycle — never raises).
    """
    for url in (endpoint, fallback_endpoint):
        try:
            resp = httpx.get(
                url,
                timeout=timeout,
                headers={"User-Agent": "zeus-nea-sg-obs/1.0"},
            )
            if resp.status_code != 200:
                logger.warning("NEA_HTTP_%s url=%s", resp.status_code, url)
                continue
            reading = parse_nea_payload(
                resp.json(), settlement_lat=settlement_lat, settlement_lon=settlement_lon
            )
            if reading is not None:
                return reading
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "NEA_FETCH_FAILED url=%s exc=%s: %s", url, type(exc).__name__, exc
            )
    return None


def nea_obs_to_fusion_reading(reading: NeaObsReading) -> dict[str, Any]:
    """Adapt a NeaObsReading into an ObsSourceReading-shaped kwargs dict.

    The fusion module this was written for (src/forecast/observation_precision_fusion.py)
    was deleted R1-b (2026-07-08, zero live callers). This adapter is retained as a
    dangling shape-conversion helper for a future day0 multi-source fusion module.

    Returns a kwargs dict (not a dataclass — kept import-free here) with
    is_settlement_faithful=False so the fusion treats NEA as distinct from the
    settlement instrument. ``value`` is the NEA Celsius reading; callers
    that fuse against an F-settled city must convert first (Singapore settles in
    C, so for Singapore no conversion is needed).
    """
    return {
        "value": float(reading.value_c),
        "source_family": reading.source_id,
        "station_id": reading.station_id,
        "observation_available_at": reading.timestamp.astimezone(UTC).isoformat(),
        "sample_count": 1,
        "is_settlement_faithful": False,
    }
