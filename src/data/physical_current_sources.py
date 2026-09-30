# Created: 2026-09-29
# Last reused/audited: 2026-09-29
"""Station-bound current observations and measured settlement-value equality.

Adapters own fixed endpoints. Configuration cannot inject URLs, SQL, or code.
A shared request budget scales with station count, not with city-name branches.
Settlement-grade samples remain distinct from final daily resolver publication.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    station: FmiStation | None
    identity: dict[str, Any] = field(default_factory=dict)
    settlement_grade: bool = False


def _settlement_grade(row: dict[str, Any]) -> bool:
    """One value-identity admission rule for every provider, including FMI."""
    grade = row.get("settlement_grade", False)
    if not isinstance(grade, bool):
        raise ValueError("STATION_VALUE_IDENTITY_GRADE_INVALID")
    if not grade:
        return False
    proof = row.get("value_identity_proof", {})
    pairs, exact = proof.get("n_pairs"), proof.get("n_exact")
    if not (type(pairs) is int and type(exact) is int and pairs > 0
            and exact == pairs and proof.get("mismatches") == []
            and not proof.get("version_conflicts", [])):
        raise ValueError("STATION_VALUE_IDENTITY_NOT_PROVEN")
    return True


@lru_cache(maxsize=4)
def _load(path: str, mtime_ns: int, size: int) -> tuple[tuple[PhysicalCurrentSource, ...], float]:
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != 1 or data.get("role") != "station_temperature_observations":
        raise ValueError("PHYSICAL_CURRENT_REGISTRY_ROLE")
    sources = []
    seen = set()
    for row in data["sources"]:
        grade = _settlement_grade(row)
        if row["provider"] != "fmi_wfs":
            from src.data.station_temperature_adapters import CHANNELS
            identity = row["identity"]
            native_id = str(identity["provider_station"])
            key = (row["station_id"], row["source_channel"])
            seconds = float(row["minimum_poll_seconds"])
            kinds = tuple(row["settlement_source_types"])
            expected_channel = (f"noaa_wrh_{row['station_id'].lower()}" if row["provider"] == "noaa_wrh"
                                else CHANNELS.get(row["provider"]))
            unit = row["unit"]
            if (row["source_channel"] != expected_channel
                or key in seen or not re.fullmatch(r"[A-Z]{4}", row["station_id"])
                or not re.fullmatch(r"[A-Za-z0-9:]+", native_id)
                or not math.isfinite(seconds) or seconds < 60
                or unit not in ({"C", "F"} if row["provider"] == "noaa_wrh" else {"C"})
                or (row["provider"] == "noaa_wrh" and
                    (native_id != row["station_id"] or identity.get("resolver_view") not in {"hourly", "all"}))
                or (row["provider"] in {"mgm_metar", "metaviatelecom_metar"} and native_id != row["station_id"])
                or (row["provider"] == "metaviatelecom_metar" and
                    not re.fullmatch(r"[0-9]{1,8}", str(identity.get("display_id", ""))))
                or not kinds or any(t not in {"noaa", "wu_icao"} for t in kinds)):
                raise ValueError("PHYSICAL_CURRENT_ADAPTER_INVALID")
            sources.append(PhysicalCurrentSource(row["provider"], row["source_channel"], row["station_id"],
                                                  kinds, unit, seconds, None, dict(identity), grade))
            seen.add(key)
            continue
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
                                              types, row["unit"], seconds, station, dict(identity), grade))
    budget = data["providers"]["fmi_wfs"]
    fraction = float(budget["budget_fraction"])
    per_day, per_window = int(budget["requests_per_day"]), int(budget["requests_per_five_minutes"])
    requests = int(budget["requests_per_poll"])
    if not 0 < fraction <= 0.8 or not 0 < per_day <= 20000 or not 0 < per_window <= 600 or requests < 2:
        raise ValueError("PHYSICAL_CURRENT_PROVIDER_BUDGET_INVALID")
    fmi_sources = [s for s in sources if s.provider == "fmi_wfs"]
    period = max([1.0] + [s.minimum_poll_seconds for s in fmi_sources] + [
        math.ceil(requests * len(fmi_sources) * 86400 / (per_day * fraction)),
        math.ceil(requests * len(fmi_sources) * 300 / (per_window * fraction))])
    return tuple(sources), min([period] + [s.minimum_poll_seconds for s in sources if s.provider != "fmi_wfs"]) if fmi_sources else min([s.minimum_poll_seconds for s in sources] or [300.0])


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
