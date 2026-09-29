# Created: 2026-09-29
# Last reused/audited: 2026-09-29
"""Optional station-bound physical channels; never settlement authority.

Adapters own fixed endpoints. Configuration cannot inject URLs, SQL, or code.
A shared request budget scales with station count, not with city-name branches.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import logging
import math
from pathlib import Path
import re
from typing import Any

from src.data.fmi_airport_temperature import FmiStation, SOURCE_CHANNEL

logger = logging.getLogger(__name__)
REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "physical_current_sources.json"


@dataclass(frozen=True)
class PhysicalCurrentSource:
    provider: str
    source_channel: str
    station_id: str
    settlement_source_types: tuple[str, ...]
    unit: str
    minimum_poll_seconds: float
    station: FmiStation


@lru_cache(maxsize=4)
def _load(path: str, mtime_ns: int, size: int) -> tuple[tuple[PhysicalCurrentSource, ...], float]:
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != 1 or data.get("role") != "physical_current_only":
        raise ValueError("PHYSICAL_CURRENT_REGISTRY_ROLE")
    sources = []
    seen = set()
    for row in data["sources"]:
        if row["provider"] != "fmi_wfs" or row["source_channel"] != SOURCE_CHANNEL or row["unit"] != "C":
            raise ValueError("PHYSICAL_CURRENT_ADAPTER_UNKNOWN")
        key = (row["station_id"], row["source_channel"])
        identity = row["identity"]
        latitude, longitude = float(identity["latitude"]), float(identity["longitude"])
        seconds = float(row["minimum_poll_seconds"])
        types = tuple(row["settlement_source_types"])
        if (key in seen or not re.fullmatch(r"[A-Z]{4}", row["station_id"])
            or not str(identity["fmisid"]).isdigit() or not str(identity["wmo"]).isdigit()
            or not identity["name"] or not -90 <= latitude <= 90 or not -180 <= longitude <= 180
            or not math.isfinite(seconds) or seconds < 1 or not types
            or any(t not in {"noaa", "wu_icao"} for t in types)):
            raise ValueError("PHYSICAL_CURRENT_STATION_INVALID")
        seen.add(key)
        station = FmiStation(row["station_id"], str(identity["fmisid"]), str(identity["wmo"]),
                             identity["name"], latitude, longitude)
        sources.append(PhysicalCurrentSource(row["provider"], row["source_channel"], row["station_id"],
                                              types, row["unit"], seconds, station))
    budget = data["providers"]["fmi_wfs"]
    fraction = float(budget["budget_fraction"])
    per_day, per_window = int(budget["requests_per_day"]), int(budget["requests_per_five_minutes"])
    requests = int(budget["requests_per_poll"])
    if not 0 < fraction <= 0.8 or not 0 < per_day <= 20000 or not 0 < per_window <= 600 or requests < 2:
        raise ValueError("PHYSICAL_CURRENT_PROVIDER_BUDGET_INVALID")
    period = max([1.0] + [s.minimum_poll_seconds for s in sources] + [
        math.ceil(requests * len(sources) * 86400 / (per_day * fraction)),
        math.ceil(requests * len(sources) * 300 / (per_window * fraction))])
    return tuple(sources), period


@lru_cache(maxsize=4)
def load_physical_current_sources(path: Path | None = None) -> tuple[tuple[PhysicalCurrentSource, ...], float]:
    # Registry and scheduler cadence are one process-lifetime snapshot. Apply
    # source-count/budget changes together on restart, never hot-load new work
    # under the old faster scheduler interval.
    path = path or REGISTRY_PATH
    stat = path.stat()
    return _load(str(path), stat.st_mtime_ns, stat.st_size)


def physical_current_sources_for_city(city: Any) -> tuple[PhysicalCurrentSource, ...]:
    try:
        sources, _ = load_physical_current_sources()
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        logger.error("PHYSICAL_CURRENT_CONFIG_UNAVAILABLE error=%s", type(exc).__name__)
        return ()  # Optional evidence missing; the base belief still serves.
    return tuple(s for s in sources
                 if s.station_id == str(getattr(city, "wu_station", "") or "").upper()
                 and str(getattr(city, "settlement_source_type", "") or "").lower() in s.settlement_source_types
                 and s.unit == str(getattr(city, "settlement_unit", "") or "").upper())


def physical_current_poll_seconds() -> float:
    try:
        return load_physical_current_sources()[1]
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return 300.0  # Bad optional config cannot spin or stop the rest of ingest.
