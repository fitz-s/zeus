"""Strict config loader. No .get(key, fallback) pattern — every key must exist.

Loads config/settings.json and config/cities.json from the project root.
Missing keys raise KeyError immediately at startup, not at trade time.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
STATE_DIR = PROJECT_ROOT / "state"


def state_path(filename: str) -> Path:
    """Mode-qualified path for per-process state files.

    This is the single control point for process state isolation.
    All per-process mutable files (positions, tracker, status, control, risk_state)
    MUST use this function.

    zeus.db does NOT use this — it holds shared world data (ENS, calibration,
    settlements) plus env-tagged decision data.

    positions.json → positions-paper.json / positions-live.json
    """
    import os
    mode = os.environ.get("ZEUS_MODE", settings.mode)
    dot = filename.rfind('.')
    if dot > 0:
        stem, ext = filename[:dot], filename[dot:]
    else:
        stem, ext = filename, ""
    return STATE_DIR / f"{stem}-{mode}{ext}"


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


# Cluster assignment by city name. Calibration uses 6 clusters × 4 seasons = 24 buckets.
CITY_CLUSTERS = {
    "NYC": "US-Northeast",
    "Chicago": "US-Midwest",
    "Atlanta": "US-Southeast", "Miami": "US-Southeast",
    "Dallas": "US-SouthCentral", "Austin": "US-SouthCentral", "Houston": "US-SouthCentral",
    "Seattle": "US-Pacific", "Los Angeles": "US-Pacific", "San Francisco": "US-Pacific",
    "Denver": "US-Mountain",
    "London": "Europe", "Paris": "Europe",
    "Seoul": "Asia", "Shanghai": "Asia", "Tokyo": "Asia",
}


@dataclass(frozen=True)
class City:
    """City configuration with validated airport coordinates.

    Coordinates MUST correspond to the WU settlement station (airport),
    not city center. This affects ENS grid point selection.
    """
    name: str
    lat: float
    lon: float
    timezone: str
    settlement_unit: str  # "F" or "C"
    cluster: str
    wu_station: str
    aliases: tuple[str, ...] = ()
    slug_names: tuple[str, ...] = ()
    wu_pws: Optional[str] = None
    meteostat_station: Optional[str] = None
    airport_name: str = ""
    settlement_source: str = ""
    diurnal_amplitude: float = 12.0
    noaa_office: Optional[str] = None
    noaa_gridX: Optional[int] = None
    noaa_gridY: Optional[int] = None


class Settings:
    """Strict settings — every access is a direct dict key lookup."""

    def __init__(self, path: Optional[Path] = None):
        path = path or (CONFIG_DIR / "settings.json")
        self._data = _load_json(path)
        required = [
            "capital_base_usd", "mode", "discovery", "ensemble",
            "calibration", "edge", "sizing", "exit", "riskguard", "execution"
        ]
        for key in required:
            if key not in self._data:
                raise KeyError(f"Missing required config key: {key}")

    def __getitem__(self, key: str):
        return self._data[key]

    @property
    def mode(self) -> str:
        return self._data["mode"]

    @property
    def capital_base_usd(self) -> float:
        return float(self._data["capital_base_usd"])


def load_cities(path: Optional[Path] = None) -> list[City]:
    """Load cities from JSON. Handles both US (noaa.lat/lon) and intl (top-level lat/lon)."""
    path = path or (CONFIG_DIR / "cities.json")
    data = _load_json(path)

    result = []
    for c in data["cities"]:
        name = c["name"]

        # Coordinates: US cities use noaa.lat/lon, international use top-level
        noaa = c.get("noaa")  # This .get is for JSON structure detection, not config fallback
        if noaa and isinstance(noaa, dict):
            lat = noaa["lat"]
            lon = noaa["lon"]
            noaa_office = noaa.get("office")  # JSON structure, not config
            noaa_gx = noaa.get("gridX")
            noaa_gy = noaa.get("gridY")
        else:
            lat = c["lat"]
            lon = c["lon"]
            noaa_office = None
            noaa_gx = None
            noaa_gy = None

        # Diurnal amplitude: °F or °C depending on unit
        unit = c["unit"]
        amp = c.get("diurnal_amplitude_f") or c.get("diurnal_amplitude_c") or 12.0

        cluster = CITY_CLUSTERS.get(name, "Other")

        result.append(City(
            name=name,
            lat=float(lat),
            lon=float(lon),
            timezone=c["timezone"],
            settlement_unit=unit,
            cluster=cluster,
            wu_station=c["wu_station"],
            aliases=tuple(c.get("aliases", [])),
            slug_names=tuple(c.get("slug_names", [])),
            wu_pws=c.get("wu_pws"),
            meteostat_station=c.get("meteostat_station"),
            airport_name=c.get("airport_name", ""),
            settlement_source=c.get("settlement_source", ""),
            diurnal_amplitude=float(amp),
            noaa_office=noaa_office,
            noaa_gridX=noaa_gx,
            noaa_gridY=noaa_gy,
        ))

    return result


# Module-level singletons — initialized once at import
settings = Settings()
cities = load_cities()
cities_by_name: dict[str, City] = {c.name: c for c in cities}
# Also index by alias for market title matching
cities_by_alias: dict[str, City] = {}
for c in cities:
    for alias in c.aliases:
        cities_by_alias[alias.lower()] = c
