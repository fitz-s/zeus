"""Strict config loader. No .get(key, fallback) pattern — every key must exist.

Loads config/settings.json and config/cities.json from the project root.
Missing keys raise KeyError immediately at startup, not at trade time.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
STATE_DIR = PROJECT_ROOT / "state"


def legacy_state_path(filename: str) -> Path:
    return STATE_DIR / filename


def mode_state_path(filename: str, mode: Optional[str] = None) -> Path:
    """Mode-qualified path for per-process state files.

    This is the single control point for process state isolation.
    All per-process mutable files (positions, tracker, status, control, risk_state)
    MUST use this function.

    zeus.db does NOT use this — it holds shared world data (ENS, calibration,
    settlements) plus env-tagged decision data.

    positions.json → positions-live.json (live-only, Phase 1)
    """
    import os

    mode = mode or get_mode()
    dot = filename.rfind(".")
    if dot > 0:
        stem, ext = filename[:dot], filename[dot:]
    else:
        stem, ext = filename, ""
    return STATE_DIR / f"{stem}-{mode}{ext}"


def get_mode() -> str:
    """Mode accessor. Zeus is live-only — always returns 'live'."""
    return "live"


def state_path(filename: str) -> Path:
    return mode_state_path(filename)


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


_DEFAULT_CITY_DATA = _load_json(CONFIG_DIR / "cities.json")["cities"]
ALL_CLUSTERS = tuple(dict.fromkeys(city["cluster"] for city in _DEFAULT_CITY_DATA))
CALIBRATION_SEASONS = ("DJF", "MAM", "JJA", "SON")


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
            "capital_base_usd",
            "mode",
            "discovery",
            "ensemble",
            "calibration",
            "day0",
            "edge",
            "sizing",
            "correlation",
            "exit",
            "riskguard",
            "execution",
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


def _unit_diurnal_amplitude(city_row: dict, unit: str) -> float:
    """Select the unit-matching diurnal amplitude without truthiness bugs."""
    preferred_key = "diurnal_amplitude_c" if unit == "C" else "diurnal_amplitude_f"
    fallback_key = "diurnal_amplitude_f" if preferred_key == "diurnal_amplitude_c" else "diurnal_amplitude_c"

    if preferred_key in city_row and city_row[preferred_key] is not None:
        return float(city_row[preferred_key])
    if fallback_key in city_row and city_row[fallback_key] is not None:
        return float(city_row[fallback_key])
    return 12.0


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

        unit = c["unit"]
        amp = _unit_diurnal_amplitude(c, unit)

        if "cluster" not in c:
            raise KeyError(
                f"City {name!r} missing from city metadata cluster field. "
                "Cluster taxonomy must be explicit and single-sourced."
            )
        cluster = c["cluster"]

        result.append(
            City(
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
                diurnal_amplitude=amp,
                noaa_office=noaa_office,
                noaa_gridX=noaa_gx,
                noaa_gridY=noaa_gy,
            )
        )

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


def calibration_clusters() -> tuple[str, ...]:
    return ALL_CLUSTERS


def calibration_seasons() -> tuple[str, ...]:
    return CALIBRATION_SEASONS


def calibration_maturity_thresholds() -> tuple[int, int, int]:
    maturity = settings["calibration"]["maturity"]
    return int(maturity["level1"]), int(maturity["level2"]), int(maturity["level3"])


def calibration_n_bootstrap() -> int:
    return int(settings["calibration"]["n_bootstrap"])


def edge_n_bootstrap() -> int:
    return int(settings["edge"]["n_bootstrap"])


def ensemble_member_count() -> int:
    return int(settings["ensemble"]["primary_members"])


def ensemble_crosscheck_member_count() -> int:
    return int(settings["ensemble"]["crosscheck_members"])


def ensemble_n_mc() -> int:
    return int(settings["ensemble"]["n_mc"])


def day0_n_mc() -> int:
    return int(settings["day0"]["n_mc"])


def day0_obs_dominates_threshold() -> float:
    return float(settings["day0"]["obs_dominates_threshold"])


def ensemble_instrument_noise(unit: str) -> float:
    if unit == "C":
        return float(settings["ensemble"]["instrument_noise_c"])
    return float(settings["ensemble"]["instrument_noise_f"])


def ensemble_bimodal_kde_order() -> int:
    return int(settings["ensemble"]["bimodal_kde_order"])


def ensemble_bimodal_gap_ratio() -> float:
    return float(settings["ensemble"]["bimodal_gap_ratio"])


def ensemble_boundary_window() -> float:
    return float(settings["ensemble"]["boundary_window"])


def ensemble_unimodal_range_epsilon() -> float:
    return float(settings["ensemble"]["unimodal_range_epsilon"])


def sizing_defaults() -> dict[str, float]:
    sizing = settings["sizing"]
    return {
        "max_single_position_pct": float(sizing["max_single_position_pct"]),
        "max_portfolio_heat_pct": float(sizing["max_portfolio_heat_pct"]),
        "max_correlated_pct": float(sizing["max_correlated_pct"]),
        "max_city_pct": float(sizing["max_city_pct"]),
        "max_region_pct": float(sizing["max_region_pct"]),
        "min_order_usd": float(sizing["min_order_usd"]),
    }


def correlation_default_cross_cluster() -> float:
    return float(settings["correlation"]["default_cross_cluster"])


def correlation_matrix() -> dict[str, dict[str, float]]:
    matrix = {
        cluster: {other: float(value) for other, value in mapping.items()}
        for cluster, mapping in settings["correlation"]["matrix"].items()
    }
    missing = set(ALL_CLUSTERS) - set(matrix)
    unknown = set(matrix) - set(ALL_CLUSTERS)
    if missing or unknown:
        raise KeyError(
            "correlation.matrix must match canonical cluster taxonomy. "
            f"missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    for cluster, mapping in matrix.items():
        bad_targets = set(mapping) - set(ALL_CLUSTERS)
        if bad_targets:
            raise KeyError(
                f"correlation.matrix[{cluster!r}] has unknown cluster targets: {sorted(bad_targets)}"
            )
    return matrix
