"""Strict config loader. No .get(key, fallback) pattern — every key must exist.

Loads config/settings.json and config/cities.json from the project root.
Missing keys raise KeyError immediately at startup, not at trade time.
"""

# Created: pre-Phase-0 (K1 Phase 1 strict-contract commits 96b70a8 / f6f612e)
# Last reused/audited: 2026-04-30
# Authority basis: Phase 10 DT-close B001 — docs/operations/task_2026-04-16_dual_track_metric_spine/phase10_evidence/SCAFFOLD_B001_config_contract.md

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
STATE_DIR = PROJECT_ROOT / "state"


def legacy_state_path(filename: str) -> Path:
    return STATE_DIR / filename


def mode_state_path(filename: str, mode: Optional[str] = None) -> Path:
    """State path for the live runtime.

    ``mode`` remains an explicit compatibility parameter for truth-file callers
    that must prove they are reading live runtime state. Backtest/replay lanes
    use their own DB paths and must not route through runtime state files.
    """
    resolved = mode or get_mode()
    if resolved not in ACTIVE_MODES:
        raise ValueError(
            f"mode_state_path called with invalid mode={resolved!r}; "
            f"runtime state is live-only ({ACTIVE_MODES})."
        )
    return STATE_DIR / filename


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
    country_code: str = ""
    settlement_source_type: str = "wu_icao"  # "wu_icao" | "hko" | "noaa" | "cwa_station"
    diurnal_amplitude: float = 12.0
    historical_peak_hour: float = 15.0
    # Optional per-city instrument noise override (in city.settlement_unit).
    # See src/signal/ensemble_signal.py::sigma_instrument_for_city for the
    # rationale. Default None means use the unit-keyed ASOS spec from
    # settings.json. Set tighter values for institutional stations like
    # HKO and Taiwan CWA where the underlying sensor is materially more
    # precise than airport AWOS.
    instrument_noise_override: Optional[float] = None
    noaa_office: Optional[str] = None
    noaa_gridX: Optional[int] = None
    noaa_gridY: Optional[int] = None


class Settings:
    """Strict settings — every access is a direct dict key lookup."""

    def __init__(self, path: Optional[Path] = None):
        path = path or (CONFIG_DIR / "settings.json")
        self._data = _load_json(path)
        required = [
            # capital_base_usd removed 2026-05-04 — bankroll truth flows from
            # src.runtime.bankroll_provider.current(); no config-literal default.
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
            "bias_correction_enabled",
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

    # capital_base_usd property removed 2026-05-04 — see _bankroll_doctrine_2026_05_04
    # in config/settings.json. Live bankroll: src.runtime.bankroll_provider.current().

    @property
    def bias_correction_enabled(self) -> bool:
        """Whether bias correction is enabled. Strict — missing key = startup KeyError (B001)."""
        return bool(self._data["bias_correction_enabled"])

    @property
    def feature_flags(self) -> dict:
        """Feature flags dict. Strict — missing key = startup KeyError (B001)."""
        return dict(self._data["feature_flags"])


class EntryForecastRolloutMode(StrEnum):
    BLOCKED = "blocked"
    SHADOW = "shadow"
    CANARY = "canary"
    LIVE = "live"


class EntryForecastSourceTransport(StrEnum):
    ENSEMBLE_SNAPSHOTS_V2_DB_READER = "ensemble_snapshots_v2_db_reader"


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
    allow_short_horizon_06_18: bool
    rollout_mode: EntryForecastRolloutMode
    calibration_policy_id: EntryForecastCalibrationPolicyId
    require_active_market_future_coverage: bool

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


def _entry_forecast_bool(data: dict, key: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise TypeError(f"settings['entry_forecast']['{key}'] must be a bool")
    return value


def entry_forecast_config(config: Settings | None = None) -> EntryForecastConfig:
    """Strict live-entry forecast source config.

    Missing or invalid config fails closed before forecast entry can size or
    submit. ``rollout_mode='blocked'`` is the safe default in settings.json.
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
        allow_short_horizon_06_18=_entry_forecast_bool(data, "allow_short_horizon_06_18"),
        rollout_mode=EntryForecastRolloutMode(data["rollout_mode"]),
        calibration_policy_id=EntryForecastCalibrationPolicyId(data["calibration_policy_id"]),
        require_active_market_future_coverage=_entry_forecast_bool(
            data,
            "require_active_market_future_coverage",
        ),
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
                aliases=tuple(c.get("aliases", [])),
                slug_names=tuple(c.get("slug_names", [])),
                wu_pws=c.get("wu_pws"),
                meteostat_station=c.get("meteostat_station"),
                airport_name=c.get("airport_name", ""),
                settlement_source=c.get("settlement_source", ""),
                country_code=c["country_code"],
                settlement_source_type=c.get("settlement_source_type") or "wu_icao",
                diurnal_amplitude=amp,
                historical_peak_hour=float(c.get("historical_peak_hour", 15.0)),
                instrument_noise_override=(
                    float(c["instrument_noise_override"])
                    if c.get("instrument_noise_override") is not None
                    else None
                ),
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
        if c.settlement_source_type == "wu_icao" and not c.wu_station:
            warnings.append(f"{c.name}: wu_station is empty")
        if not c.timezone:
            warnings.append(f"{c.name}: timezone is empty")
        if c.settlement_source_type not in ("wu_icao", "hko", "noaa", "cwa_station"):
            warnings.append(
                f"{c.name}: settlement_source_type={c.settlement_source_type!r} "
                "is not a known type"
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


def day0_n_mc() -> int:
    return int(settings["day0"]["n_mc"])


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


def exit_correlation_crowding_rate() -> float:
    """T6.4-phase2 correlation-crowding cost rate.

    Cost model: crowding_cost_usd = rate × exposure_ratio × shares × best_bid
    where exposure_ratio is the output of
    src.strategy.correlation.correlated_exposure over OTHER held positions.
    rate=0.01 means 1% of exit notional per unit of correlated exposure.

    Default 0.0 = no-op (correlation crowding adds zero cost; feature is
    plumbed but disabled). Operator raises after measuring should_exit
    sensitivity via T6.3-followup-1 replay harness.

    Bounds [0.0, 0.1] — values above 0.1 imply >10% of exit notional per
    unit-exposure which would aggressively exit any correlated book.
    """
    rate = float(settings["exit"]["correlation_crowding_rate"])
    if not (0.0 <= rate <= 0.1):
        raise ValueError(
            f"exit.correlation_crowding_rate={rate} out of sane range "
            f"[0.0, 0.1]. Values above 0.1 would force near-immediate exit "
            f"of any correlated book. Check config/settings.json "
            f"exit.correlation_crowding_rate."
        )
    return rate


def hold_value_exit_costs_enabled() -> bool:
    """T6.4 feature flag: when False (default), _buy_yes_exit /
    _buy_no_exit call HoldValue.compute with fee=0/time=0 (pre-T6.4
    behavior preserved during shadow validation). When True, exit
    decisions include fee + time opportunity cost via
    HoldValue.compute_with_exit_costs. See config/settings.json
    feature_flags.HOLD_VALUE_EXIT_COSTS for flip protocol."""
    return bool(settings["feature_flags"].get("HOLD_VALUE_EXIT_COSTS", False))
