"""Strict config loader. No .get(key, fallback) pattern — every key must exist.

Loads config/settings.json and config/cities.json from the project root.
Missing keys raise KeyError immediately at startup, not at trade time.
"""

# Created: pre-Phase-0 (K1 Phase 1 strict-contract commits 96b70a8 / f6f612e)
# Last reused/audited: 2026-04-30
# Authority basis: Phase 10 DT-close B001 — docs/operations/task_2026-04-16_dual_track_metric_spine/phase10_evidence/SCAFFOLD_B001_config_contract.md

import copy
import functools
import json
import hashlib
import logging
import math
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
RUNTIME_ROOT = Path(os.environ.get("ZEUS_PRIMARY_ROOT") or PROJECT_ROOT).expanduser().resolve()
TEST_STATE_ROOT_ENV = "ZEUS_TEST_STATE_ROOT"


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _test_state_forbidden_roots() -> tuple[Path, ...]:
    return (
        PROJECT_ROOT.resolve(strict=False),
        (PROJECT_ROOT / "state").resolve(strict=False),
    )


def validate_test_state_root(value: str | os.PathLike[str] | Path) -> Path:
    """Validate a pytest state root before any test state path is resolved.

    The marker is intentionally independent of ``ZEUS_PRIMARY_ROOT``. It is a
    test-only capability boundary, not a production runtime-root override.
    """

    raw = os.fspath(value) if value is not None else ""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("test state root must be non-empty")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError("test state root must be absolute")
    if candidate.is_symlink():
        raise ValueError("test state root must not be a symlink")

    resolved = candidate.resolve(strict=False)
    temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
    if resolved == temp_root or not _path_is_within(resolved, temp_root):
        raise ValueError("test state root must be a private temporary child")
    for forbidden in _test_state_forbidden_roots():
        if _path_is_within(resolved, forbidden):
            raise ValueError("test state root may not overlap repo/live state")
    return resolved


def validate_test_state_path(value: str | os.PathLike[str] | Path) -> Path:
    """Validate a test-only state target by resolved filesystem boundaries."""

    marker = os.environ.get(TEST_STATE_ROOT_ENV)
    if marker is None:
        raise RuntimeError("test state path validation requires the test marker")
    validate_test_state_root(marker)
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ValueError("test state path must be absolute")
    resolved = candidate.resolve(strict=False)
    temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
    if resolved == temp_root or not _path_is_within(resolved, temp_root):
        raise ValueError("test state path must stay under a temporary directory")
    for forbidden in _test_state_forbidden_roots():
        if _path_is_within(resolved, forbidden):
            raise ValueError("test state path may not overlap repo/live state")
    return candidate


_TEST_STATE_ROOT: Path | None = None
if TEST_STATE_ROOT_ENV in os.environ:
    # SCOPE: only the pytest marker's root; production has no marker and is untouched.
    # DRAIN: pytest owns this temporary namespace until session teardown.
    # RESET: removing the marker restores the existing ZEUS_PRIMARY_ROOT/state path.
    _TEST_STATE_ROOT = validate_test_state_root(os.environ[TEST_STATE_ROOT_ENV])

STATE_DIR = _TEST_STATE_ROOT or (RUNTIME_ROOT / "state")


def runtime_state_path(filename: str) -> Path:
    """State path for the live runtime.

    Backtest/replay lanes use their own DB paths and must not route through
    runtime state files.
    """
    target = STATE_DIR / filename
    if _TEST_STATE_ROOT is not None:
        validated = validate_test_state_path(target)
        if not _path_is_within(validated.resolve(strict=False), _TEST_STATE_ROOT):
            raise ValueError("default state path escaped the test state root")
    return target


ACTIVE_MODES = ("live",)


def get_mode() -> str:
    """Return the only supported runtime mode.

    Historical builds routed runtime behavior through ``ZEUS_MODE``. That made
    a single live daemon depend on an environment string while replay/backtest
    already had separate entry points and stores. The environment variable is
    no longer authority; callers that need replay/backtest behavior must use
    the replay/backtest APIs directly.
    """
    return "live"


def state_path(filename: str) -> Path:
    return runtime_state_path(filename)


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
    settlement_source_type: str  # "wu_icao" | "hko" | "noaa" | "cwa_station" — required, no default: see validate_cities_config
    aliases: tuple[str, ...] = ()
    slug_names: tuple[str, ...] = ()
    wu_pws: Optional[str] = None
    meteostat_station: Optional[str] = None
    airport_name: str = ""
    settlement_source: str = ""
    country_code: str = ""
    previous_settlement_source_type: Optional[str] = None
    settlement_source_type_effective_date: Optional[str] = None
    # Which view of weather.gov/wrh/timeseries the market's own description
    # names as its resolution surface: "hourly" for the 11 US cities whose
    # contract says 'This market will resolve off of the Hourly Data provided
    # using the "Show Hourly Data" button.', "all" for every other NOAA city.
    # The two views give different daily extrema (the hourly view shows only
    # routine METAR + station-prefixed SPECI rows), so this is a settlement
    # field, not a display preference. NOAA cities only.
    settlement_page_view: str = "all"  # "hourly" | "all"
    diurnal_amplitude: float = 12.0
    historical_peak_hour: float = 15.0
    # Optional per-city instrument noise override (in city.settlement_unit).
    # See src/signal/ensemble_signal.py::sigma_instrument_for_city for the
    # rationale. Default None means use the unit-keyed ASOS spec from
    # settings.json. Set tighter values for institutional stations like
    # HKO and Taiwan CWA where the underlying sensor is materially more
    # precise than airport AWOS.
    instrument_noise_override: Optional[float] = None
    weighted_low_calibration_eligible: bool = True
    noaa_office: Optional[str] = None
    noaa_gridX: Optional[int] = None
    noaa_gridY: Optional[int] = None


class Settings:
    """Strict settings — every access is a direct dict key lookup."""

    def __init__(self, path: Optional[Path] = None):
        path = path or (CONFIG_DIR / "settings.json")
        self._data = _load_json(path)
        required = [
            # Fixed config-bankroll authority was removed 2026-05-04; bankroll
            # truth flows from src.runtime.bankroll_provider.current().
            "discovery",
            "ensemble",
            "entry_forecast",
            "calibration",
            "day0",
            "edge",
            "sizing",
            "correlation",
            "exit",
            "riskguard",
            "execution",
            "feature_flags",
        ]
        for key in required:
            if key not in self._data:
                raise KeyError(f"Missing required config key: {key}")

    def __getitem__(self, key: str):
        return self._data[key]

    @property
    def mode(self) -> str:
        return get_mode()

    # Fixed config-bankroll property removed 2026-05-04 — see
    # _bankroll_doctrine_2026_05_04 in config/settings.json. Live bankroll:
    # src.runtime.bankroll_provider.current().

    @property
    def feature_flags(self) -> dict:
        """Feature flags dict. Strict — missing key = startup KeyError (B001)."""
        return dict(self._data["feature_flags"])


class EntryForecastSourceTransport(StrEnum):
    ENSEMBLE_SNAPSHOTS_V2_DB_READER = "ensemble_snapshots_db_reader"


class EntryForecastCalibrationPolicyId(StrEnum):
    ECMWF_OPEN_DATA_USES_TIGGE_LOCALDAY_CAL_V1 = "ecmwf_open_data_uses_tigge_localday_cal_v1"


@dataclass(frozen=True)
class EntryForecastConfig:
    source_id: str
    source_transport: EntryForecastSourceTransport
    authority_family: str
    high_track: str
    low_track: str
    target_horizon_days: int
    warm_horizon_days: int
    source_cycle_policy: str
    calibration_policy_id: EntryForecastCalibrationPolicyId

    def __post_init__(self) -> None:
        for field_name in (
            "source_id",
            "authority_family",
            "high_track",
            "low_track",
            "source_cycle_policy",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"entry_forecast.{field_name} must not be empty")
        if self.target_horizon_days < 1 or self.target_horizon_days > 10:
            raise ValueError("entry_forecast.target_horizon_days must be in [1, 10]")
        if self.warm_horizon_days < 1 or self.warm_horizon_days > 10:
            raise ValueError("entry_forecast.warm_horizon_days must be in [1, 10]")
        if self.warm_horizon_days < self.target_horizon_days:
            raise ValueError("entry_forecast.warm_horizon_days must cover target_horizon_days")


def entry_forecast_config(config: Settings | None = None) -> EntryForecastConfig:
    """Strict live-entry forecast source config.

    Missing or invalid config fails closed before forecast entry can size or
    submit.

    Phase C-5 removed two dead knobs: ``allow_short_horizon_06_18`` and
    ``require_active_market_future_coverage``. They were loaded into
    ``EntryForecastConfig`` but never read by production code, creating a
    false sense of operator control. The actual safety property they
    appeared to govern is enforced elsewhere:
    ``allow_short_horizon_06_18`` was redundant with
    ``config/source_release_calendar.yaml:live_authorization=false`` for
    06/18 cycle profiles; ``require_active_market_future_coverage``
    duplicated the producer-readiness gate. Removing the knobs eliminates
    the risk of an operator flipping a dead knob and assuming a safety
    behavior change occurred.
    """

    cfg = config or settings
    data = cfg["entry_forecast"]
    return EntryForecastConfig(
        source_id=str(data["source_id"]).strip(),
        source_transport=EntryForecastSourceTransport(data["source_transport"]),
        authority_family=str(data["authority_family"]).strip(),
        high_track=str(data["high_track"]).strip(),
        low_track=str(data["low_track"]).strip(),
        target_horizon_days=int(data["target_horizon_days"]),
        warm_horizon_days=int(data["warm_horizon_days"]),
        source_cycle_policy=str(data["source_cycle_policy"]).strip(),
        calibration_policy_id=EntryForecastCalibrationPolicyId(data["calibration_policy_id"]),
    )


def _unit_diurnal_amplitude(city_row: dict, unit: str) -> float:
    """Select the unit-matching diurnal amplitude without truthiness bugs."""
    preferred_key = "diurnal_amplitude_c" if unit == "C" else "diurnal_amplitude_f"
    fallback_key = "diurnal_amplitude_f" if preferred_key == "diurnal_amplitude_c" else "diurnal_amplitude_c"

    if preferred_key in city_row and city_row[preferred_key] is not None:
        return float(city_row[preferred_key])
    if fallback_key in city_row and city_row[fallback_key] is not None:
        return float(city_row[fallback_key])
    raise ValueError(
        f"No diurnal amplitude ('{preferred_key}' or '{fallback_key}') "
        f"in city config for {city_row.get('name', '?')}"
    )


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

        if "cluster" not in c:
            raise KeyError(
                f"City {name!r} missing from city metadata cluster field. "
                "Cluster taxonomy must be explicit and single-sourced."
            )
        for required_field in ("unit", "timezone", "wu_station", "country_code"):
            if required_field not in c:
                raise KeyError(
                    f"City {name!r} missing required field {required_field!r}"
                )
        if not c.get("settlement_source_type"):
            raise KeyError(
                f"City {name!r} missing required field 'settlement_source_type'. "
                "The resolver family (wu_icao/hko/noaa/cwa_station) must be "
                "explicit — the runtime default silently carries the wrong "
                "family the day a city is cut over to a new source."
            )
        if "weighted_low_calibration_eligible" not in c:
            raise KeyError(
                f"City {name!r} missing required field "
                "'weighted_low_calibration_eligible'"
            )
        weighted_low_calibration_eligible = c["weighted_low_calibration_eligible"]
        if type(weighted_low_calibration_eligible) is not bool:
            raise TypeError(
                f"City {name!r} field 'weighted_low_calibration_eligible' "
                "must be a JSON boolean"
            )
        if lat is None or lon is None:
            raise KeyError(
                f"City {name!r} missing lat/lon "
                "(expected top-level or under noaa.lat/noaa.lon)"
            )
        cluster = c["cluster"]
        unit = c["unit"]
        amp = _unit_diurnal_amplitude(c, unit)

        result.append(
            City(
                name=name,
                lat=float(lat),
                lon=float(lon),
                timezone=c["timezone"],
                settlement_unit=unit,
                cluster=cluster,
                wu_station=c["wu_station"],
                settlement_source_type=c["settlement_source_type"],
                aliases=tuple(c.get("aliases", [])),
                slug_names=tuple(c.get("slug_names", [])),
                wu_pws=c.get("wu_pws"),
                meteostat_station=c.get("meteostat_station"),
                airport_name=c.get("airport_name", ""),
                settlement_source=c.get("settlement_source", ""),
                country_code=c["country_code"],
                previous_settlement_source_type=c.get(
                    "previous_settlement_source_type"
                ),
                settlement_source_type_effective_date=c.get(
                    "settlement_source_type_effective_date"
                ),
                settlement_page_view=c.get("settlement_page_view") or "all",
                diurnal_amplitude=amp,
                historical_peak_hour=float(c.get("historical_peak_hour", 15.0)),
                instrument_noise_override=(
                    float(c["instrument_noise_override"])
                    if c.get("instrument_noise_override") is not None
                    else None
                ),
                weighted_low_calibration_eligible=weighted_low_calibration_eligible,
                noaa_office=noaa_office,
                noaa_gridX=noaa_gx,
                noaa_gridY=noaa_gy,
            )
        )

    return result


def _build_cities_by_alias(loaded_cities: list[City]) -> dict[str, City]:
    aliases: dict[str, City] = {}
    for c in loaded_cities:
        for alias in c.aliases:
            alias_lower = alias.lower()
            if alias_lower in aliases:
                raise ValueError(
                    f"Alias conflict: {alias!r} maps to both "
                    f"{aliases[alias_lower].name!r} and {c.name!r}"
                )
            aliases[alias_lower] = c
    return aliases


def _cities_config_mtime_ns(path: Path | None = None) -> int:
    city_path = path or (CONFIG_DIR / "cities.json")
    try:
        return city_path.stat().st_mtime_ns
    except FileNotFoundError:
        return -1


class _RuntimeCityMap(dict):
    """Dict that refreshes config/cities.json before read access.

    Many live modules import ``cities_by_name`` once at startup. Keeping this
    object identity stable, and mutating it in place on reload, lets those
    imports observe source-conversion config changes without a daemon restart.
    """

    def _refresh(self) -> None:
        reload_cities_if_changed()

    def __getitem__(self, key):
        self._refresh()
        return super().__getitem__(key)

    def get(self, key, default=None):
        self._refresh()
        return super().get(key, default)

    def __contains__(self, key):
        self._refresh()
        return super().__contains__(key)

    def values(self):
        self._refresh()
        return super().values()

    def items(self):
        self._refresh()
        return super().items()

    def keys(self):
        self._refresh()
        return super().keys()

    def copy(self):
        self._refresh()
        return dict(self)


# Module-level singletons. Source-conversion apply can update cities.json while
# the daemon process is alive, so scanner-facing callers use the refresh helpers
# below instead of assuming these bindings never change.
settings = Settings()
cities = load_cities()
cities_by_name: dict[str, City] = _RuntimeCityMap({c.name: c for c in cities})
cities_by_alias: dict[str, City] = _RuntimeCityMap(_build_cities_by_alias(cities))
_cities_loaded_mtime_ns = _cities_config_mtime_ns()


def reload_cities_if_changed(*, force: bool = False) -> bool:
    """Reload city config if config/cities.json changed on disk.

    Returns True when the module-level city indexes were refreshed. This keeps
    source-conversion runtime changes visible to market discovery without
    requiring a daemon restart for new-entry gating.
    """

    global _cities_loaded_mtime_ns
    current_mtime = _cities_config_mtime_ns()
    if not force and current_mtime == _cities_loaded_mtime_ns:
        return False
    loaded = load_cities()
    cities[:] = loaded
    dict.clear(cities_by_name)
    dict.update(cities_by_name, {c.name: c for c in loaded})
    dict.clear(cities_by_alias)
    dict.update(cities_by_alias, _build_cities_by_alias(loaded))
    _cities_loaded_mtime_ns = current_mtime
    return True


def runtime_cities() -> list[City]:
    reload_cities_if_changed()
    return list(cities)


def runtime_cities_by_name() -> dict[str, City]:
    reload_cities_if_changed()
    return dict(cities_by_name)


STATION_GROUND_PROOF_REVISION = "station_ground_roles_v1"
HKO_GROUND_SOURCE_URL = "https://www.hko.gov.hk/en/cis/stn.htm"
HKO_GROUND_QUANTITY = "Elevation of ground above mean sea-level (metres)"
HOMR_GROUND_SOURCE_URL = "https://www.ncei.noaa.gov/access/homr/services/station/search"
HOMR_INTERNATIONAL_GROUND_SOURCE_KIND = "noaa_homr_international_station_ground_snapshot_v1"
OSCAR_WMD_SOURCE_KIND = "wmo_wmd_awc_station_snapshot_v1"
OSCAR_WMD_SOURCE_URL = "https://oscar.wmo.int/surface/rest/api/wmd/download/"
AWC_STATION_IDENTITY_SOURCE_URL = "https://aviationweather.gov/api/data/stationinfo"
STATION_GROUND_SOURCE_ARTIFACTS = {
    "hko_station_table_v1": "config/hko_station_metadata.html",
    "noaa_homr_primary_dcp_snapshot_v1": "config/noaa_homr_kord_station.json",
}
_STATION_GROUND_SOURCE_KINDS = frozenset({*STATION_GROUND_SOURCE_ARTIFACTS, OSCAR_WMD_SOURCE_KIND, HOMR_INTERNATIONAL_GROUND_SOURCE_KIND})
_HOMR_INTERNATIONAL_GROUND_NCDC = {
    "ZSPD": "30137822", "EGLC": "30146303",
    "NZAA": "30151541", "RKPK": "20029737", "ZUUU": "30137836",
    "ZUCK": "30137833", "ZGGG": "30137796", "LTFM": "30146057",
    "WIHH": "30140750", "ZSJN": "30137818", "DNMM": "30152035",
    "MMMX": "30149753", "EDDM": "30103328", "SBGR": "30134434",
    "RKSI": "30150507", "ZGSZ": "30137800", "NZWN": "30083512",
    "ZHCC": "30137802",
}
_HOMR_INTERNATIONAL_GROUND_STATIONS = frozenset(_HOMR_INTERNATIONAL_GROUND_NCDC)
_HOMR_PRIMARY_DCP_STATIONS = frozenset({
    "KATL", "KAUS", "KORD", "KDAL", "KBKF", "KHOU", "KLAX", "KMIA", "KLGA", "KSFO", "KSEA",
})
_OSCAR_WMD_STATIONS = frozenset({
    "CYYZ", "EFHK", "EHAM", "EPWA", "FACT", "LEMD", "LFPB", "LIMC", "LTAC",
    "OEJN", "OPKC", "RJTT", "RPLL", "SAEZ", "VILK", "WMKK", "WSSS",
})


def station_ground_source_artifact_ref(*, source_kind: str, station_id: str) -> str | None:
    """Return only the approved per-station source entity, never a global fallback."""
    if source_kind == "hko_station_table_v1" and station_id == "HKO_HQ":
        return STATION_GROUND_SOURCE_ARTIFACTS[source_kind]
    if source_kind == "noaa_homr_primary_dcp_snapshot_v1" and station_id in _HOMR_PRIMARY_DCP_STATIONS:
        return f"config/noaa_homr_{station_id.lower()}_station.json"
    if source_kind == HOMR_INTERNATIONAL_GROUND_SOURCE_KIND and station_id in _HOMR_INTERNATIONAL_GROUND_STATIONS:
        return f"config/noaa_homr_{station_id.lower()}_station.json"
    if source_kind == OSCAR_WMD_SOURCE_KIND and station_id in _OSCAR_WMD_STATIONS:
        return f"config/wmo_wmd_{station_id.lower()}_station.xml"
    return None


def station_ground_identity_bridge_artifact_ref(*, source_kind: str, station_id: str) -> str | None:
    if source_kind == OSCAR_WMD_SOURCE_KIND and station_id in _OSCAR_WMD_STATIONS:
        return "config/awc_stationinfo_53_station.json"
    return None


class _HkoStationTable(HTMLParser):
    """Read the official manned-station table, not a descriptive page label."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.in_table = False
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table" and dict(attrs).get("id") == "manned":
            self.in_table = True
        if not self.in_table:
            return
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"}:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, data):
        if self.in_table and self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if not self.in_table:
            return
        if tag in {"td", "th"} and self.cell is not None:
            if self.row is not None:
                self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "table":
            self.in_table = False


def _hko_ground_facts(raw: bytes, station_id: str) -> dict[str, object]:
    if station_id != "HKO_HQ":
        raise ValueError("official HKO row is not this settlement station")
    table = _HkoStationTable()
    table.feed(raw.decode("utf-8"))
    rows = table.rows
    if len(rows) < 3 or rows[0][2] != HKO_GROUND_QUANTITY or rows[1][:4] != ["Latitude N", "Longitude E", "Wind", "Temp"]:
        raise ValueError("official ground quantity/temperature column unavailable")
    sites = [row for row in rows[2:] if row and row[0] == "Hong Kong Observatory (HKO) (01/01/1884)"]
    if len(sites) != 1 or len(sites[0]) != 15 or sites[0][5] != "✔":
        raise ValueError("official HKO temperature station row unavailable")
    site = sites[0]

    def coordinate(value: str, limit: int) -> float:
        match = re.fullmatch(r'(\d+)°(\d+)\'(\d+)"', value)
        if match is None:
            raise ValueError("official site coordinate invalid")
        degrees, minutes, seconds = map(int, match.groups())
        if not 0 <= degrees <= limit or not 0 <= minutes < 60 or not 0 <= seconds < 60:
            raise ValueError("official site coordinate invalid")
        return degrees + minutes / 60 + seconds / 3600

    elevation = float(site[3])
    if not math.isfinite(elevation):
        raise ValueError("official ground elevation invalid")
    return {
        "revision": STATION_GROUND_PROOF_REVISION, "source_kind": "hko_station_table_v1",
        "station_id": station_id,
        "source_station_id": "HKO", "height_role": "ground_msl",
        "quantity": HKO_GROUND_QUANTITY, "elevation_m": elevation,
        "site_lat": coordinate(site[1], 90), "site_lon": coordinate(site[2], 180),
        "temperature_station": True, "source_url": HKO_GROUND_SOURCE_URL,
    }


def _homr_ground_facts(raw: bytes, station_id: str) -> dict[str, object]:
    """Replay an approved current US primary-DCP snapshot, not airport MSL.

    HOMR Enhanced ELEV_GROUND denotes the official temperature site; DSI9767B
    section 2aa defines signed feet above sea level. POR is not a location's
    effective interval, and capture possession must not authorize earlier cuts.
    SCOPE: this exact station's OM9 precision only, not ENS/reference identity.
    DRAIN: approved current metadata capture plus normal config reload.
    RESET: valid role/bytes let normal producers recompute after possession.
    """
    if station_id not in _HOMR_PRIMARY_DCP_STATIONS:
        raise ValueError("unsupported primary-DCP settlement station")

    def number(value: object) -> float:
        if isinstance(value, bool):
            raise ValueError("boolean is not a physical measurement")
        converted = float(value)
        if not math.isfinite(converted):
            raise ValueError("non-finite physical measurement")
        return converted

    collection = json.loads(raw)["stationCollection"]
    definitions = [row for row in collection["definitions"]
                   if row.get("defType") == "elevations" and row.get("abbr") == "GROUND"]
    if len(definitions) != 1 or definitions[0].get("description") != "ELEVATION OF THE GROUND":
        raise ValueError("official ground quantity unavailable")
    stations = collection["stations"]
    if not isinstance(stations, list) or len(stations) != 1:
        raise ValueError("HOMR snapshot must contain one station")
    station = stations[0]
    identifiers = station["identifiers"]
    ids = {}
    for kind in ("ICAO", "WMO", "WBAN", "NCDCSTNID"):
        found = [row["id"] for row in identifiers if row.get("idType") == kind]
        if len(found) != 1 or not isinstance(found[0], str):
            raise ValueError("official station identifier ambiguous or foreign")
        ids[kind] = found[0]
    if ids["ICAO"] != station_id or any(re.fullmatch(r"\d+", ids[k]) is None for k in ("WMO", "WBAN", "NCDCSTNID")):
        raise ValueError("official station identifier ambiguous or foreign")
    location = station["location"]
    if station["ncdcStnId"] != ids["NCDCSTNID"] or location["ncdcstnId"] != ids["NCDCSTNID"]:
        raise ValueError("official location belongs to another station")
    if station["header"]["por"]["endDate"] != "Present":
        raise ValueError("not a current station snapshot")
    platforms = {row["platform"] for row in station["platforms"]}
    if "ASOS" not in platforms:
        raise ValueError("official temperature platform unavailable")
    remarks = [row["remark"] for row in station["remarks"] if row.get("type") == "GENERAL"]
    if not any(
        "PRIMARY STATION LATITUDE/LONGITUDE WAS UPDATED TO MATCH THE PRIMARY DCP (DATA COLLECTION PACKAGE) SENSOR EQUIPMENT REPORTING OBSERVATIONS TO NCEI." in remark
        and "GROUND ELEVATION WAS UPDATED BASED UPON PROVIDED DCP LOCATIONS USING GIS." in remark
        for remark in remarks
    ):
        raise ValueError("ground is not bound to primary observation DCP")
    coordinates = location["latLonPairs"]
    if len(coordinates) != 1 or coordinates[0]["source"] != "ASOS CM":
        raise ValueError("primary observation coordinate ambiguous")
    lat, lon = number(coordinates[0]["latitude_dec"]), number(coordinates[0]["longitude_dec"])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("primary observation coordinate invalid")
    if lat != number(station["header"]["latitude_dec"]) or lon != number(station["header"]["longitude_dec"]):
        raise ValueError("header and primary observation coordinate differ")
    elevations = [row for row in location["elevations"] if row.get("elevationType") == "GROUND"]
    if len(elevations) != 1:
        raise ValueError("primary observation ground ambiguous or missing")
    ground = elevations[0]
    elevation = number(ground["elevationMeters"])
    number(ground["elevationFeet"])
    # Both published quantities are rounded independently. Compare their
    # displayed-precision intervals, not a tolerance that treats integer feet
    # as exact and falsely rejects genuine one-decimal metre observations.
    def interval(value: object, scale: Decimal) -> tuple[Decimal, Decimal]:
        decimal = Decimal(str(value))
        half_unit = Decimal("0.5").scaleb(decimal.as_tuple().exponent)
        return (decimal - half_unit) * scale, (decimal + half_unit) * scale

    metres_interval = interval(ground["elevationMeters"], Decimal(1))
    feet_interval = interval(ground["elevationFeet"], Decimal("0.3048"))
    if max(metres_interval[0], feet_interval[0]) > min(metres_interval[1], feet_interval[1]):
        raise ValueError("official ground units inconsistent")
    return {
        "revision": STATION_GROUND_PROOF_REVISION,
        "source_kind": "noaa_homr_primary_dcp_snapshot_v1", "station_id": station_id,
        "source_station_id": ids["NCDCSTNID"], "wmo_station_id": ids["WMO"], "wban_station_id": ids["WBAN"],
        "height_role": "ground_msl", "quantity": "location.elevations.GROUND",
        "elevation_m": elevation, "site_lat": lat, "site_lon": lon, "temperature_station": True,
        "location_role": "primary_temperature_dcp", "coordinate_source": "ASOS CM",
        "source_url": HOMR_GROUND_SOURCE_URL,
    }


def _homr_international_ground_facts(raw: bytes, station_id: str) -> dict[str, object]:
    """Replay current station-reference ground, not the US primary sensor DCP.

    SCOPE: the explicit ICAO/NCDC entities. DRAIN: real metadata acquisition and
    normal canonical archive before seeds. RESET: a new causal certificate;
    POR is not a location interval and this parser grants no historic possession.
    """
    if station_id not in _HOMR_INTERNATIONAL_GROUND_STATIONS:
        raise ValueError("unsupported international ground station")

    def number(value: object) -> float:
        if isinstance(value, bool):
            raise ValueError("boolean is not a physical measurement")
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite physical measurement")
        return parsed

    def reject_periods(value: object) -> None:
        # Individual periods require a period-aware parser, not a silent
        # undated promotion. Station header POR is deliberately outside here.
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in {
                    "begindate", "enddate", "startdate", "effective date", "effectivedate",
                    "validfrom", "validto", "validperiod", "effectiveperiod", "validity",
                    "beginposition", "endposition", "datefrom", "dateto",
                } and child not in (None, ""):
                    raise ValueError("individual station period requires explicit validation")
                reject_periods(child)
        elif isinstance(value, list):
            for child in value:
                reject_periods(child)

    collection = json.loads(raw)["stationCollection"]
    definitions = [row for row in collection["definitions"]
                   if row.get("defType") == "elevations" and row.get("abbr") == "GROUND"]
    if len(definitions) != 1 or definitions[0].get("description") != "ELEVATION OF THE GROUND":
        raise ValueError("official ground quantity unavailable")
    stations = collection["stations"]
    if not isinstance(stations, list) or len(stations) != 1:
        raise ValueError("HOMR snapshot must contain one station")
    station = stations[0]
    identifiers, location = station["identifiers"], station["location"]
    if station.get("locations"):
        raise ValueError("multiple location representation requires explicit validation")
    reject_periods(identifiers)
    reject_periods(location)
    ids = {}
    for kind in ("ICAO", "NCDCSTNID"):
        found = [row["id"] for row in identifiers if row.get("idType") == kind]
        if len(found) != 1 or not isinstance(found[0], str):
            raise ValueError("official station identifier ambiguous")
        ids[kind] = found[0]
    if ids["ICAO"] != station_id or ids["NCDCSTNID"] != _HOMR_INTERNATIONAL_GROUND_NCDC[station_id]:
        raise ValueError("official station identifier foreign")
    if any(value != ids["NCDCSTNID"] for value in (
        station["ncdcStnId"], location["ncdcstnId"],
        location["geoInfo"]["ncdcstnId"], location["nwsInfo"]["ncdcstnId"],
    )):
        raise ValueError("official location namespace differs")
    if station["header"]["por"]["endDate"] != "Present":
        raise ValueError("not a current station snapshot")
    coordinates = location["latLonPairs"]
    if not isinstance(coordinates, list) or len(coordinates) != 1:
        raise ValueError("official station coordinate ambiguous")
    lat, lon = number(coordinates[0]["latitude_dec"]), number(coordinates[0]["longitude_dec"])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("official station coordinate invalid")
    if lat != number(station["header"]["latitude_dec"]) or lon != number(station["header"]["longitude_dec"]):
        raise ValueError("header and station coordinate differ")
    for field, coordinate, expected in (("latitudes", "latitude_dec", lat), ("longitudes", "longitude_dec", lon)):
        representations = location[field]
        if not isinstance(representations, list) or len(representations) != 1 or number(representations[0][coordinate]) != expected:
            raise ValueError("official location coordinate representations differ")
    elevations = [row for row in location["elevations"] if row.get("elevationType") == "GROUND"]
    if len(elevations) != 1:
        raise ValueError("official station ground missing or ambiguous")
    ground = elevations[0]
    # NCEI DSI9767B section2aa reserves -99999 feet for missing ground.
    # Its finite converted metres are not terrain; zero/negative terrain is legal.
    if number(ground["elevationFeet"]) == -99999:
        raise ValueError("official ground is documented missing")
    elevation = number(ground["elevationMeters"])

    def interval(value: object, scale: Decimal) -> tuple[Decimal, Decimal]:
        decimal = Decimal(str(value))
        half = Decimal("0.5").scaleb(decimal.as_tuple().exponent)
        return (decimal-half)*scale, (decimal+half)*scale

    metres, feet = interval(ground["elevationMeters"], Decimal(1)), interval(ground["elevationFeet"], Decimal("0.3048"))
    if max(metres[0], feet[0]) > min(metres[1], feet[1]):
        raise ValueError("official ground units inconsistent")
    return {
        "revision": STATION_GROUND_PROOF_REVISION,
        "source_kind": HOMR_INTERNATIONAL_GROUND_SOURCE_KIND, "station_id": station_id,
        "source_station_id": ids["NCDCSTNID"], "height_role": "ground_msl",
        "quantity": "location.elevations.GROUND", "elevation_m": elevation,
        "site_lat": lat, "site_lon": lon, "location_role": "station_ground_reference",
        "source_url": HOMR_GROUND_SOURCE_URL,
    }


def _oscar_wmd_ground_facts(
    raw: bytes, station_id: str, identity_bridge_bytes: bytes, effective_at: datetime,
) -> dict[str, object]:
    """Bind a fixed-land site's 3-07 ground to its current METAR station ID.

    Facility position is ground; nested equipment positions and AWC elevation
    are different quantities. Explicit periods constrain the captured current
    snapshot, without requiring an extra sensor installation history or AGL.
    """
    if effective_at.tzinfo is None or effective_at.utcoffset() is None:
        raise ValueError("station snapshot needs aware possession time")
    at = effective_at.astimezone(timezone.utc)
    bridge = json.loads(identity_bridge_bytes)
    if not isinstance(bridge, list):
        raise ValueError("AWC station identity entity is not a list")
    matches = [row for row in bridge if row.get("icaoId") == station_id]
    if len(matches) != 1:
        raise ValueError("AWC ICAO identity ambiguous or missing")
    identity = matches[0]
    wmo_id = identity.get("wmoId")
    if (identity.get("id") != station_id or not isinstance(wmo_id, str)
            or re.fullmatch(r"\d{5}", wmo_id) is None
            or not isinstance(identity.get("siteType"), list) or "METAR" not in identity["siteType"]):
        raise ValueError("current meteorological station identity unavailable")
    for key, limit in (("lat", 90), ("lon", 180)):
        value = identity[key]
        if isinstance(value, bool) or not math.isfinite(float(value)) or abs(float(value)) > limit:
            raise ValueError("AWC identity coordinate invalid")
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("external or expanded XML entities are unsupported")
    ns = {"w": "http://def.wmo.int/wmdr/2017", "g": "http://www.opengis.net/gml/3.2"}
    href = "{http://www.w3.org/1999/xlink}href"
    root = ET.fromstring(raw)
    if root.tag != "{http://def.wmo.int/wmdr/2017}WIGOSMetadataRecord":
        raise ValueError("official WMDR namespace unavailable")
    facilities = root.findall("w:facility/w:ObservingFacility", ns)
    if len(facilities) != 1:
        raise ValueError("WMDR station ambiguous")
    facility = facilities[0]
    identifiers = facility.findall("g:identifier", ns)
    if len(identifiers) != 1:
        raise ValueError("WMDR station identity ambiguous")
    ids = [value.strip() for value in (identifiers[0].text or "").split(",")]
    matched_id = f"0-20000-0-{wmo_id}"
    if len(set(ids)) != len(ids) or ids.count(matched_id) != 1:
        raise ValueError("WMDR station differs from current ICAO/WMO bridge")
    types = facility.findall("w:facilityType", ns)
    if len(types) != 1 or types[0].get(href) != "http://codes.wmo.int/wmdr/FacilityType/landFixed":
        raise ValueError("station elevation is not fixed-land ground")

    def period(node: ET.Element) -> tuple[datetime | None, datetime | None]:
        periods = node.findall("w:validPeriod/g:TimePeriod", ns)
        if len(periods) > 1:
            raise ValueError("station validity period ambiguous")
        if not periods:
            return None, None  # Current official snapshot, not an invented old interval.
        def boundary(name: str) -> datetime | None:
            elements = periods[0].findall("g:" + name, ns)
            if len(elements) > 1:
                raise ValueError("station validity boundary ambiguous")
            if elements and (elements[0].get("indeterminatePosition") or elements[0].get("nilReason")):
                raise ValueError("station validity boundary indeterminate")
            value = (elements[0].text or "").strip() if elements else ""
            if not value:
                return None
            stripped = value.removesuffix("Z")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
                return datetime.combine(date.fromisoformat(stripped), datetime.min.time(), timezone.utc)
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if result.tzinfo is None or result.utcoffset() is None:
                raise ValueError("station validity clock has no timezone")
            return result.astimezone(timezone.utc)
        start, end = boundary("beginPosition"), boundary("endPosition")
        if start is not None and end is not None and end <= start:
            raise ValueError("station validity interval invalid")
        return start, end

    def active(node: ET.Element) -> bool:
        start, end = period(node)
        return (start is None or start <= at) and (end is None or at < end)

    statuses = facility.findall("w:programAffiliation/w:ProgramAffiliation/w:reportingStatus/w:ReportingStatus", ns)
    if not any(active(status) and any(e.get(href) == "http://codes.wmo.int/wmdr/ReportingStatus/operational"
                                    for e in status.findall("w:reportingStatus", ns)) for status in statuses):
        raise ValueError("current station is not operational")
    location_history = [(node, *period(node)) for node in facility.findall("w:geospatialLocation/w:GeospatialLocation", ns)]
    if len(location_history) > 1:
        if any(start is None for _, start, _ in location_history):
            raise ValueError("station ground history has undated versions")
        location_history.sort(key=lambda item: item[1])
        starts = [start for _, start, _ in location_history]
        if len(set(starts)) != len(starts):
            raise ValueError("station ground history has tied versions")
    locations = []
    for index, (node, start, end) in enumerate(location_history):
        following = location_history[index + 1][1] if index + 1 < len(location_history) else None
        # WMDR GeospatialLocationType defines a from-date record's implicit
        # exclusive end as the next location's begin. A future version does not
        # invalidate the current interval. Explicit overlaps remain malformed.
        if end is not None and following is not None and end > following:
            raise ValueError("station ground history explicitly overlaps")
        interval_end = end if end is not None else following
        if (start is None or start <= at) and (interval_end is None or at < interval_end):
            locations.append(node)
    if len(locations) != 1:
        raise ValueError("current station ground position ambiguous or unavailable")
    points = locations[0].findall("w:geoLocation/g:Point", ns)
    if len(points) != 1:
        raise ValueError("station ground point ambiguous")
    positions = points[0].findall("g:pos", ns)
    if len(positions) != 1:
        raise ValueError("station ground coordinate unavailable")
    position = positions[0]
    for geometry in (points[0], position):
        # GML pos inherits containing Point attributes. An explicit alternate
        # CRS cannot be silently treated as WMDR lat/lon/MSL ground; this parser
        # performs no geocentric or ellipsoidal-height transformation.
        if geometry.get("srsDimension", "3") != "3":
            raise ValueError("station ground dimensionality differs")
        if geometry.get("srsName") is not None:
            raise ValueError("explicit station ground CRS is unsupported")
        axes = geometry.get("axisLabels")
        if axes is not None and axes.split() != ["lat", "lon", "elevation"]:
            raise ValueError("station ground axis order differs")
        units = geometry.get("uomLabels")
        if units is not None and units.split() != ["deg", "deg", "m"]:
            raise ValueError("station ground units differ from WMDR coordinate contract")
    values = (position.text or "").split()
    if len(values) != 3:
        raise ValueError("station ground coordinate must include elevation")
    lat, lon, height = map(float, values)
    if not all(math.isfinite(value) for value in (lat, lon, height)) or abs(lat) > 90 or abs(lon) > 180:
        raise ValueError("station ground coordinate invalid")
    a = math.sin(math.radians(lat - float(identity["lat"])) / 2) ** 2 + math.cos(math.radians(lat)) * math.cos(
        math.radians(float(identity["lat"]))
    ) * math.sin(math.radians(lon - float(identity["lon"])) / 2) ** 2
    # Existing 5 km identity screen is necessary, not proof by proximity:
    # the independent exact WMO/METAR/fixed-land/current-role bindings above
    # must all be present. Known other-site matches cannot borrow this ground.
    if 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a))) > 5:
        raise ValueError("ground facility differs from current source site")
    return {
        "revision": STATION_GROUND_PROOF_REVISION, "source_kind": OSCAR_WMD_SOURCE_KIND,
        "station_id": station_id, "source_station_id": matched_id, "wmo_station_id": wmo_id,
        "height_role": "ground_msl", "quantity": "WIGOS3-07.ObservingFacility.geospatialLocation.elevation",
        "elevation_m": height, "site_lat": lat, "site_lon": lon,
        "location_role": "fixed_land_station_ground_reference", "source_url": OSCAR_WMD_SOURCE_URL + matched_id,
        "identity_bridge_source_url": AWC_STATION_IDENTITY_SOURCE_URL,
    }


def station_ground_facts_from_bytes(
    *, source_kind: str, station_id: str, raw_body: bytes,
    identity_bridge_bytes: bytes | None = None, effective_at: datetime | None = None,
) -> dict[str, object] | None:
    """Replay supported official entities without I/O, clocks or self-claims.

    This establishes physical facts only. Approved artifact identity, capture
    possession and decision-time authority remain the caller's obligations.
    """
    if (not isinstance(source_kind, str) or not isinstance(station_id, str)
            or not isinstance(raw_body, bytes) or len(raw_body) > 256 * 1024):
        return None
    if station_ground_source_artifact_ref(source_kind=source_kind, station_id=station_id) is None:
        return None
    try:
        if source_kind == "hko_station_table_v1":
            return _hko_ground_facts(raw_body, station_id)
        if source_kind == HOMR_INTERNATIONAL_GROUND_SOURCE_KIND:
            return _homr_international_ground_facts(raw_body, station_id)
        if source_kind == OSCAR_WMD_SOURCE_KIND:
            if (not isinstance(identity_bridge_bytes, bytes) or len(identity_bridge_bytes) > 256 * 1024
                    or not isinstance(effective_at, datetime)):
                return None
            return _oscar_wmd_ground_facts(raw_body, station_id, identity_bridge_bytes, effective_at)
        return _homr_ground_facts(raw_body, station_id)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError, ArithmeticError, ET.ParseError):
        return None


@functools.cache
def _registry_claim_ground_facts(
    kind: str, station_id: str, raw: bytes, bridge_raw: bytes | None, checked: datetime,
) -> dict[str, object] | None:
    """Replay one registry claim's own bytes at its own possession clock.

    Pure in its key. Every coordinate-identity read replays all stations' pages,
    so an unmemoized replay (~60 ms per manifest) starved per-scope anchor reads
    past their acquisition deadline. Bounded by the distinct registry claims and
    config asset bodies a process observes; decision-cutoff replays stay uncached.
    """
    return station_ground_facts_from_bytes(
        source_kind=kind, station_id=station_id, raw_body=raw,
        identity_bridge_bytes=bridge_raw, effective_at=checked,
    )


def _station_ground_for_entry(
    entry: dict, station_id: str, *, effective_at: datetime | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "ground_status": "UNPROVEN", "ground_reason": "STATION_GROUND_PROOF_MISSING",
        "ground_elevation_m": None, "ground_facts": None, "ground_audit": None,
    }
    claim = entry.get("station_ground_proof")
    if not isinstance(claim, dict):
        return result
    try:
        kind = claim.get("source_kind")
        if kind not in _STATION_GROUND_SOURCE_KINDS or claim.get("revision") != STATION_GROUND_PROOF_REVISION:
            raise ValueError("unsupported ground source/revision")
        checked = datetime.fromisoformat(str(claim["checked_at"]).replace("Z", "+00:00"))
        if checked.tzinfo is None or checked.utcoffset() is None:
            raise ValueError("ground source audit time must be timezone-aware")
        artifact_ref = station_ground_source_artifact_ref(source_kind=kind, station_id=station_id)
        if artifact_ref is None:
            raise ValueError("ground source has no approved station entity")
        if claim.get("artifact_ref") != artifact_ref:
            raise ValueError("ground source artifact is not the approved config asset")
        artifact = CONFIG_DIR / Path(artifact_ref).name
        if artifact.is_symlink() or not artifact.is_file():
            raise ValueError("ground source artifact must be the regular config asset")
        if artifact.stat().st_size > 256 * 1024:
            raise ValueError("ground source body exceeds bounded catalog size")
        raw = artifact.read_bytes()
        if hashlib.sha256(raw).hexdigest() != claim["body_sha256"]:
            raise ValueError("ground source body identity mismatch")
        bridge_raw = None
        bridge_audit = None
        if kind == OSCAR_WMD_SOURCE_KIND:
            bridge = claim.get("bridge")
            bridge_ref = station_ground_identity_bridge_artifact_ref(source_kind=kind, station_id=station_id)
            if (not isinstance(bridge, dict) or bridge_ref is None
                    or bridge.get("source_kind") != "awc_stationinfo_v1"
                    or bridge.get("source_url") != AWC_STATION_IDENTITY_SOURCE_URL
                    or bridge.get("artifact_ref") != bridge_ref):
                raise ValueError("station ground lacks its approved identity bridge")
            bridge_asset = CONFIG_DIR / Path(bridge_ref).name
            if (bridge_asset.is_symlink() or not bridge_asset.is_file()
                    or bridge_asset.stat().st_size > 256 * 1024):
                raise ValueError("identity bridge must be the bounded regular config entity")
            bridge_raw = bridge_asset.read_bytes()
            if hashlib.sha256(bridge_raw).hexdigest() != bridge.get("body_sha256"):
                raise ValueError("identity bridge body identity differs")
            source_checked = datetime.fromisoformat(str(claim["source_checked_at"]).replace("Z", "+00:00"))
            bridge_checked = datetime.fromisoformat(str(bridge["checked_at"]).replace("Z", "+00:00"))
            if any(time.tzinfo is None or time.utcoffset() is None for time in (source_checked, bridge_checked)):
                raise ValueError("dual ground source possession must be timezone-aware")
            if checked != max(source_checked, bridge_checked):
                raise ValueError("ground possession must include both actual source entities")
            bridge_audit = {key: bridge[key] for key in (
                "source_kind", "artifact_ref", "body_sha256", "checked_at", "source_url",
            )}
        facts = copy.deepcopy(_registry_claim_ground_facts(kind, station_id, raw, bridge_raw, checked))
        if facts is None:
            raise ValueError("official ground source facts unavailable")
        audit_keys = ["artifact_ref", "body_sha256", "checked_at"]
        if kind == OSCAR_WMD_SOURCE_KIND:
            audit_keys.append("source_checked_at")
        if kind in {"noaa_homr_primary_dcp_snapshot_v1", HOMR_INTERNATIONAL_GROUND_SOURCE_KIND}:
            query_date = date.fromisoformat(claim["query_date"])
            queries = {
                f"{HOMR_GROUND_SOURCE_URL}?current=true&qid=ICAO%3A{station_id}&date={query_date.isoformat()}&phrData=false",
            }
            if station_id == "KORD":  # Original approved receipt predates current=true narrowing.
                queries.add(f"{HOMR_GROUND_SOURCE_URL}?qid=ICAO%3AKORD&date={query_date.isoformat()}&phrData=false")
            if kind == HOMR_INTERNATIONAL_GROUND_SOURCE_KIND:
                # Exact original acquisition forms only: order differs, not
                # host/path/key/value authority. Duplicate/extra keys never match.
                queries = {
                    f"{HOMR_GROUND_SOURCE_URL}?qid=ICAO%3A{station_id}&qidMod=is&current=true&date={query_date.isoformat()}&phrData=false",
                    f"{HOMR_GROUND_SOURCE_URL}?date={query_date.isoformat()}&current=true&qid=ICAO%3A{station_id}&qidMod=is&phrData=false",
                }
            if query_date != checked.astimezone(timezone.utc).date() or claim.get("query_url") not in queries:
                raise ValueError("HOMR snapshot query is not bound to current capture")
            audit_keys.extend(("query_date", "query_url"))
        if any(claim.get(key) != value for key, value in facts.items()):
            raise ValueError("ground claim differs from official site facts")
        if effective_at is not None:
            if not isinstance(effective_at, datetime) or effective_at.tzinfo is None or effective_at.utcoffset() is None:
                raise ValueError("ground analysis cutoff must be timezone-aware")
            if effective_at < checked:
                result["ground_reason"] = "STATION_GROUND_NOT_POSSESSED_AT_ANALYSIS_CUTOFF"
                return result
            # Verify the registry's source-capture facts first, then select the
            # independently supplied decision interval from the same raw bytes.
            # A future location becoming effective is not another HTTP capture.
            facts = station_ground_facts_from_bytes(
                source_kind=kind, station_id=station_id, raw_body=raw,
                identity_bridge_bytes=bridge_raw, effective_at=effective_at,
            )
            if facts is None:
                raise ValueError("official ground unavailable at analysis cutoff")
        # The recorded reference point need not equal the official DMS point.
        # Preserve both and apply the existing station identity tolerance only.
        lat, lon = float(entry["lat"]), float(entry["lon"])
        p1, p2 = math.radians(lat), math.radians(float(facts["site_lat"]))
        dl = math.radians(lon - float(facts["site_lon"]))
        a = math.sin((p1 - p2) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        if 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a))) > 5:
            raise ValueError("official site differs from station reference identity")
        result.update(
            ground_status="VERIFIED", ground_reason=None,
            ground_elevation_m=facts["elevation_m"], ground_facts=facts,
            ground_audit={key: claim.get(key) for key in audit_keys},
        )
        if bridge_audit is not None:
            result["ground_audit"]["bridge"] = bridge_audit
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError):
        result["ground_reason"] = "STATION_GROUND_PROOF_INVALID"
    return result


@functools.cache
def _station_registry(raw: bytes) -> tuple[object, str]:
    """Pure parse keyed by exact registry bytes; bounded by distinct registry versions."""
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def runtime_station_geometry_for_city(
    city: City, *, registry_path: Path | None = None, effective_at: datetime | None = None,
) -> dict[str, object]:
    """Bind station reference identity separately from proved measurement ground.

    Invalid registry rows degrade only this city. The existing 5 km station
    request-coordinate tolerance is an identity check, not a fitted forecast
    cutoff; it does not attest the model grid's land mask or elevation. Explicit
    effective_at selects decision-time facts after source possession; omission
    returns source-capture metadata, not published-posterior authority.
    """
    path = registry_path or CONFIG_DIR / "station_precise_coords.json"
    source_type = str(getattr(city, "settlement_source_type", "") or "").strip().lower()
    expected_id = (
        "HKO_HQ" if source_type == "hko"
        else str(getattr(city, "wu_station", "") or "").strip().upper()
        if source_type in {"wu_icao", "noaa"} else ""
    )
    proof: dict[str, object] = {
        "station_id": expected_id or None, "lat": None, "lon": None,
        "elevation_m": None, "station_surface": None,
        "reference_role": "station_reference" if source_type == "hko" else "airport_reference",
        "ground_status": "UNPROVEN", "ground_reason": "STATION_GROUND_PROOF_MISSING",
        "ground_elevation_m": None, "ground_facts": None, "ground_audit": None,
        "registry_sha256": None, "source": None,
        "validity_reason": "STATION_SOURCE_IDENTITY_UNAVAILABLE",
    }
    if not expected_id:
        return proof
    try:
        rows, registry_sha = _station_registry(path.read_bytes())
        entry = copy.deepcopy(rows[city.name])
        proof["registry_sha256"] = registry_sha
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        proof["validity_reason"] = "STATION_REGISTRY_ROW_UNAVAILABLE"
        return proof
    if not isinstance(entry, dict) or str(entry.get("station") or "").strip().upper() != expected_id:
        proof["validity_reason"] = "STATION_REGISTRY_ID_MISMATCH"
        return proof
    try:
        lat, lon = float(entry["lat"]), float(entry["lon"])
    except (KeyError, TypeError, ValueError):
        proof["validity_reason"] = "STATION_REGISTRY_GEOMETRY_INVALID"
        return proof
    if not (math.isfinite(lat) and math.isfinite(lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        proof["validity_reason"] = "STATION_REGISTRY_GEOMETRY_INVALID"
        return proof
    p1, p2 = math.radians(lat), math.radians(float(city.lat))
    d_lon = math.radians((lon - float(city.lon) + 180) % 360 - 180)
    a = math.sin((p1 - p2) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lon / 2) ** 2
    if 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a))) > 5.0:
        proof["validity_reason"] = "STATION_REGISTRY_REQUEST_COORDINATE_MISMATCH"
        return proof
    try:
        elevation = float(entry["elevation_m"])
        if not math.isfinite(elevation):
            elevation = None
    except (KeyError, TypeError, ValueError):
        elevation = None
    proof.update(
        # Legacy elevation is airport/site reference metadata, not ground
        # authority. Only the separate bytes-verified ground fields may certify
        # the real measurement site's ground above MSL.
        lat=lat, lon=lon, elevation_m=elevation,
        station_surface="UNKNOWN", source=str(entry.get("source") or ""),
        validity_reason=None,
    )
    proof.update(_station_ground_for_entry(entry, expected_id, effective_at=effective_at))
    return proof


def runtime_coordinate_manifest_json() -> str:
    """Freeze one station-coordinate, calendar and unit snapshot for source identity."""
    rows = []
    station_identity_keys = (
        "station_id", "lat", "lon", "validity_reason",
    )
    for name, city in sorted(runtime_cities_by_name().items()):
        lat, lon = float(city.lat), float(city.lon)
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise ValueError(f"invalid extraction coordinates: {name}")
        if city.settlement_unit not in {"C", "F"} or not city.timezone:
            raise ValueError(f"invalid extraction calendar/unit: {name}")
        station = runtime_station_geometry_for_city(city)
        rows.append({
            "city": name, "lat": lat, "lon": lon,
            "timezone": city.timezone, "unit": city.settlement_unit,
            # ENS extraction uses reference identity/coordinates and actual
            # model LSM, not station ground height or asserted station surface.
            # Ground facts belong to the independent OM9 precision proof.
            "station_geometry": {key: station[key] for key in station_identity_keys},
        })
    if not rows:
        raise ValueError("runtime extraction city universe is empty")
    return json.dumps(
        {"coordinate_basis": "runtime_settlement_station", "cities": rows},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    )


def settlement_source_type_for_city(
    city: City,
    target_date: date | str | None = None,
) -> str:
    """Return the resolver family effective for a market target date."""

    current_type = str(getattr(city, "settlement_source_type", "") or "wu_icao")
    effective_raw = getattr(city, "settlement_source_type_effective_date", None)
    previous_type = getattr(city, "previous_settlement_source_type", None)
    if target_date is None or not effective_raw:
        return current_type
    effective = date.fromisoformat(str(effective_raw))
    observed = (
        target_date
        if isinstance(target_date, date)
        else date.fromisoformat(str(target_date)[:10])
    )
    if observed < effective and previous_type:
        return str(previous_type)
    return current_type


def validate_cities_config(city_list: list[City] | None = None) -> list[str]:
    """Validate city configs — returns list of warning strings.

    Checks fields that should be populated for production but are allowed
    to be empty/default during development. Does not raise — caller decides
    whether warnings are fatal.
    """
    warnings = []
    for c in (city_list or cities):
        if not c.settlement_source:
            warnings.append(f"{c.name}: settlement_source is empty")
        # Both families name their settlement station in wu_station: the WU
        # history page and the weather.gov station page read the same ICAO id.
        # Enforcing it only for wu_icao let a noaa city be configured without
        # one, and a stationless noaa city falls through to defaults that name
        # OTHER cities' stations (day0_observation_reader._default_source_priority).
        if c.settlement_source_type in ("wu_icao", "noaa") and not c.wu_station:
            warnings.append(f"{c.name}: wu_station is empty")
        if not c.timezone:
            warnings.append(f"{c.name}: timezone is empty")
        if c.settlement_source_type not in ("wu_icao", "hko", "noaa", "cwa_station"):
            warnings.append(
                f"{c.name}: settlement_source_type={c.settlement_source_type!r} "
                "is not a known type"
            )
        transition_fields = (
            c.previous_settlement_source_type,
            c.settlement_source_type_effective_date,
        )
        if any(transition_fields) and not all(transition_fields):
            warnings.append(
                f"{c.name}: source transition requires previous type and effective date"
            )
        if c.previous_settlement_source_type and (
            c.previous_settlement_source_type
            not in ("wu_icao", "hko", "noaa", "cwa_station")
        ):
            warnings.append(
                f"{c.name}: invalid previous_settlement_source_type="
                f"{c.previous_settlement_source_type!r}"
            )
        if c.settlement_source_type_effective_date:
            try:
                date.fromisoformat(c.settlement_source_type_effective_date)
            except ValueError:
                warnings.append(
                    f"{c.name}: invalid settlement_source_type_effective_date="
                    f"{c.settlement_source_type_effective_date!r}"
                )
        if c.settlement_page_view not in ("hourly", "all"):
            warnings.append(
                f"{c.name}: settlement_page_view={c.settlement_page_view!r} "
                "is not a known view"
            )
        elif c.settlement_page_view == "hourly" and c.settlement_source_type != "noaa":
            warnings.append(
                f"{c.name}: settlement_page_view='hourly' requires "
                f"settlement_source_type='noaa', got "
                f"{c.settlement_source_type!r}"
            )
    if warnings:
        for w in warnings:
            logger.warning("City config validation: %s", w)
    return warnings


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


def _ensemble_model_setting(key: str) -> str:
    model = str(settings["ensemble"][key]).strip()
    if not model:
        raise ValueError(f"settings['ensemble']['{key}'] must not be empty")
    return model


def ensemble_primary_model() -> str:
    return _ensemble_model_setting("primary")


def ensemble_crosscheck_model() -> str:
    return _ensemble_model_setting("crosscheck")


def ensemble_n_mc() -> int:
    return int(settings["ensemble"]["n_mc"])


def calibration_batch_rebuild_n_mc() -> int:
    """Default MC count for offline calibration-pair rebuilds.

    LAW 4 separates aggregate training rebuild precision from live per-trade
    runtime precision. Batch rebuilds can rely on many-pair averaging; live
    runtime decisions must continue to use ``ensemble_n_mc()``.
    """

    return 1000


def day0_n_mc() -> int:
    return int(settings["day0"]["n_mc"])


def day0_current_state_innovation_e_fold_hours() -> float:
    value = float(settings["day0"]["current_state_innovation_e_fold_hours"])
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(
            "settings['day0']['current_state_innovation_e_fold_hours'] "
            "must be finite and positive"
        )
    return value


# Slice P4-1 (PR #19 phase 4 cleanup, 2026-04-26): day0_obs_dominates_
# threshold() removed — its only caller was Day0Signal.obs_dominates()
# legacy boolean interface, also removed in this slice. The continuous
# replacement (observation_weight() at day0_signal.py:215) does not use
# this threshold. settings.json's `day0.obs_dominates_threshold` key is
# now dead; operator may remove on next config refresh.


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
    result = {
        "max_single_position_pct": float(sizing["max_single_position_pct"]),
        "max_portfolio_heat_pct": float(sizing["max_portfolio_heat_pct"]),
        "max_correlated_pct": float(sizing["max_correlated_pct"]),
        "max_city_pct": float(sizing["max_city_pct"]),
        "min_order_usd": float(sizing["min_order_usd"]),
    }
    # K3 cluster collapse: max_region_pct removed from settings.json and
    # RiskLimits dataclass. Tolerate its absence for forward compatibility.
    if "max_region_pct" in sizing:
        result["max_region_pct"] = float(sizing["max_region_pct"])
    return result


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


def exit_fee_rate() -> float:
    """T6.4: fee_rate parameter for polymarket_fee() when computing
    HoldValue fee_cost in exit-decision path. See config/settings.json
    exit.fee_rate for authority + calibration notes.

    T6.4-hardening (surrogate MEDIUM finding): bounded [0, 0.1] to
    catch operator misconfiguration (e.g., typo 0.05 → 0.5 or 5.0)
    which would silently trigger mass exit pressure under flag ON.
    """
    rate = float(settings["exit"]["fee_rate"])
    if not (0.0 <= rate <= 0.1):
        raise ValueError(
            f"exit.fee_rate={rate} out of sane range [0.0, 0.1]. "
            f"Real Polymarket fee rates are typically 0.02-0.05; values "
            f"above 0.1 would make every trade unprofitable. Check "
            f"config/settings.json exit.fee_rate."
        )
    return rate


def exit_daily_hurdle_rate() -> float:
    """T6.4: daily opportunity-cost rate on locked capital for HoldValue
    time_cost in exit-decision path. See config/settings.json
    exit.daily_hurdle_rate for authority + calibration notes.

    T6.4-hardening (surrogate MEDIUM finding): bounded [0, 0.01] (1%/day
    is already an extreme hurdle ≈ 3650%/year annualized). Catches
    operator typo (0.0001 → 0.001 or 0.01) which would systematically
    flag most positions as below-hurdle and trigger mass exits.
    """
    rate = float(settings["exit"]["daily_hurdle_rate"])
    if not (0.0 <= rate <= 0.01):
        raise ValueError(
            f"exit.daily_hurdle_rate={rate} out of sane range [0.0, 0.01]. "
            f"Realistic capital-cost hurdles are ≈0.0001 (0.01%/day = "
            f"3.65%/year); values above 0.01 imply >36.5%/year hurdle "
            f"which would force near-immediate exits. Check "
            f"config/settings.json exit.daily_hurdle_rate."
        )
    return rate


def hold_value_exit_costs_enabled() -> bool:
    """T6.4 feature flag: when False (default), _buy_yes_exit /
    _buy_no_exit call HoldValue.compute with fee=0/time=0 (pre-T6.4
    behavior preserved until activation). When True, exit
    decisions include fee + time opportunity cost via
    HoldValue.compute_with_exit_costs. See config/settings.json
    feature_flags.HOLD_VALUE_EXIT_COSTS for flip protocol."""
    return bool(settings["feature_flags"].get("HOLD_VALUE_EXIT_COSTS", False))


def day0_resolver_terminal_residual_enabled() -> bool:
    """Default OFF. When True and a validated state/day0_resolver_terminal_residual.json
    is present, provisional Day0 carriers compose the resolver-graded terminal
    non-violation probability instead of the AWC->Ogimet report-survival mixture
    (src.calibration.day0_resolver_terminal_residual). Absent key = OFF."""
    return bool(
        settings["feature_flags"].get("DAY0_RESOLVER_TERMINAL_RESIDUAL", False)
    )


def tier0_research_mode_enabled() -> bool:
    """reversal_plan_tier0_2026-08-24 item 6: default OFF. When True, every
    new ENTRY admission must clear src.strategy.tier0_policy's cheap-only /
    taker-only / one-per-cluster / flat-stake gate. When False, behavior is
    unchanged from pre-Tier-0 (and is moot anyway while entries are globally
    paused). See config/settings.json feature_flags.TIER0_RESEARCH_MODE."""
    return bool(settings["feature_flags"].get("TIER0_RESEARCH_MODE", False))
