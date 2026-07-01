#!/usr/bin/env python3
# Lifecycle: created=2026-04-02; last_reviewed=2026-04-24; last_reused=2026-05-25
# Purpose: Operator city-onboarding workflow that scaffolds config, data, and
# market/settlement rows.
# Reuse: Inspect architecture/script_manifest.yaml plus
# docs/operations/current_data_state.md before running against live DB.
"""One-click city onboarding pipeline for Zeus.

Adds new cities to config and runs all backfill ETLs in dependency order,
bringing them to the same archive window as the configured city universe.

Usage:
    cd zeus

    # Auto-discover a new city (looks up ICAO, coords, timezone from name)
    python scripts/onboard_cities.py --discover "Auckland"

    # Dry run — show what would happen
    python scripts/onboard_cities.py --dry-run

    # Onboard specific cities already in NEW_CITIES
    python scripts/onboard_cities.py --cities Auckland "Kuala Lumpur"

    # Onboard all pending new cities defined in NEW_CITIES below
    python scripts/onboard_cities.py --all

    # Skip WU daily (if API rate-limited) and just do OpenMeteo + aggregations
    python scripts/onboard_cities.py --all --skip-wu-daily

    # Resume from a specific step
    python scripts/onboard_cities.py --all --start-from hourly_openmeteo
"""
from __future__ import annotations

import json
import logging
import math
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db_writer_lock import WriteClass, subprocess_run_with_write_class  # noqa: E402

# Lazy-import at module level (available for tests + precondition check):
try:
    from src.data.tier_resolver import TIER_SCHEDULE as _TIER_SCHEDULE_IMPORT  # noqa: E402
    TIER_SCHEDULE: dict = _TIER_SCHEDULE_IMPORT
except ImportError:
    TIER_SCHEDULE = {}

# ─────────────────────────────────────────────────────────────
# Deferred artifacts: require explicit post-onboarding evidence or >=110
# settled calibration_pairs dates — cannot be derived from
# public archives during initial onboarding.  The pipeline
# records them as PENDING and keeps the city at oracle
# MISSING-status (mult=0.5) until an explicit authority rebuild lands.
# ─────────────────────────────────────────────────────────────
DEFERRED_ARTIFACTS: dict[str, str] = {
    "oracle_error_rates": (
        "requires explicit oracle authority evidence; not auto-derived during onboarding"
    ),
    "v2_nstar": (
        "requires ≥110 settled calibration_pairs_v2 target_dates "
        "for ECE analysis"
    ),
    "settlement_outcomes": (
        "market-facing settlements; populated as Polymarket markets "
        "open and settle over time"
    ),
}


def _check_city_registered(city_name: str) -> None:
    """Raise ValueError if city_name is not in TIER_SCHEDULE.

    This is a fail-closed precondition check at pipeline entry.  A city
    absent from TIER_SCHEDULE will crash ``tier_for_city`` (raising
    UnsupportedTierError) during every live ETL step, so we surface it
    early with a clear message.
    """
    if city_name not in TIER_SCHEDULE:
        registered = sorted(TIER_SCHEDULE.keys())
        raise ValueError(
            f"City {city_name!r} is not in TIER_SCHEDULE "
            f"(src/data/tier_resolver.py).  Add it there before "
            f"onboarding.  Registered cities: {registered}"
        )


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# New cities to onboard. Edit this section to add more.
# ─────────────────────────────────────────────────────────────

@dataclass
class NewCity:
    name: str
    lat: float
    lon: float
    timezone: str
    unit: str       # "F" or "C"
    cluster: str
    wu_station: str  # ICAO code
    airport_name: str = ""
    aliases: list[str] | None = None
    slug_names: list[str] | None = None
    settlement_source: str = ""
    historical_peak_hour: float = 14.5
    diurnal_amplitude: float = 8.0

NEW_CITIES = [
    NewCity(
        name="Auckland",
        lat=-36.8509,
        lon=174.7645,
        timezone="Pacific/Auckland",
        unit="C",
        cluster="Oceania-Maritime",
        wu_station="NZAA",
        airport_name="Auckland Airport",
        aliases=["Auckland", "auckland"],
        slug_names=["auckland"],
        settlement_source="https://www.wunderground.com/history/daily/nz/auckland/NZAA",
        historical_peak_hour=14.0,
        diurnal_amplitude=6.5,
    ),
    NewCity(
        name="Kuala Lumpur",
        lat=2.7456,
        lon=101.7072,
        timezone="Asia/Kuala_Lumpur",
        unit="C",
        cluster="Southeast-Asia-Equatorial",
        wu_station="WMKK",
        airport_name="Kuala Lumpur Intl Airport",
        aliases=["Kuala Lumpur", "kuala lumpur", "KL"],
        slug_names=["kuala-lumpur"],
        settlement_source="https://www.wunderground.com/history/daily/my/sepang/WMKK",
        historical_peak_hour=14.5,
        diurnal_amplitude=5.0,
    ),
    NewCity(
        name="Lagos",
        lat=6.5774,
        lon=3.3213,
        timezone="Africa/Lagos",
        unit="C",
        cluster="Africa-West-Tropical",
        wu_station="DNMM",
        airport_name="Murtala Muhammed Intl Airport",
        aliases=["Lagos", "lagos"],
        slug_names=["lagos"],
        settlement_source="https://www.wunderground.com/history/daily/ng/lagos/DNMM",
        historical_peak_hour=14.0,
        diurnal_amplitude=5.5,
    ),
    NewCity(
        name="Jeddah",
        lat=21.6796,
        lon=39.1565,
        timezone="Asia/Riyadh",
        unit="C",
        cluster="Middle-East-Arabian",
        wu_station="OEJN",
        airport_name="King Abdulaziz Intl Airport",
        aliases=["Jeddah", "jeddah", "Jiddah"],
        slug_names=["jeddah"],
        settlement_source="https://www.wunderground.com/history/daily/sa/jeddah/OEJN",
        historical_peak_hour=14.5,
        diurnal_amplitude=7.0,
    ),
    NewCity(
        name="Cape Town",
        lat=-33.9649,
        lon=18.6017,
        timezone="Africa/Johannesburg",
        unit="C",
        cluster="Africa-South-Maritime",
        wu_station="FACT",
        airport_name="Cape Town Intl Airport",
        aliases=["Cape Town", "cape town"],
        slug_names=["cape-town"],
        settlement_source="https://www.wunderground.com/history/daily/za/cape-town/FACT",
        historical_peak_hour=14.5,
        diurnal_amplitude=8.0,
    ),
    NewCity(
        name="Busan",
        lat=35.1796,
        lon=128.9382,
        timezone="Asia/Seoul",
        unit="C",
        cluster="Asia-Northeast",
        wu_station="RKPK",
        airport_name="Gimhae Intl Airport",
        aliases=["Busan", "busan", "Pusan"],
        slug_names=["busan"],
        settlement_source="https://www.wunderground.com/history/daily/kr/busan/RKPK",
        historical_peak_hour=14.5,
        diurnal_amplitude=7.5,
    ),
    NewCity(
        name="Jakarta",
        lat=-6.1256,
        lon=106.6558,
        timezone="Asia/Jakarta",
        unit="C",
        cluster="Southeast-Asia-Equatorial",
        wu_station="WIII",
        airport_name="Soekarno-Hatta Intl Airport",
        aliases=["Jakarta", "jakarta"],
        slug_names=["jakarta"],
        settlement_source="https://www.wunderground.com/history/daily/id/tangerang/WIII",
        historical_peak_hour=13.5,
        diurnal_amplitude=5.0,
    ),
    NewCity(
        name="Panama City",
        lat=9.0714,
        lon=-79.3835,
        timezone="America/Panama",
        unit="C",
        cluster="Latin-America-Tropical",
        wu_station="MPTO",
        airport_name="Tocumen Intl Airport",
        aliases=["Panama City", "panama city", "Panama"],
        slug_names=["panama-city"],
        settlement_source="https://www.wunderground.com/history/daily/pa/panama-city/MPTO",
        historical_peak_hour=14.0,
        diurnal_amplitude=4.5,
    ),
    NewCity(
        name="Jinan",
        lat=36.8572,
        lon=117.0560,
        timezone="Asia/Shanghai",
        unit="C",
        cluster="Jinan",
        wu_station="ZSJN",
        airport_name="Jinan Yaoqiang International Airport",
        aliases=["Jinan"],
        slug_names=["jinan"],
        settlement_source="https://www.wunderground.com/history/daily/cn/jinan/ZSJN",
        historical_peak_hour=15.0,
        diurnal_amplitude=10.0,
    ),
    NewCity(
        name="Zhengzhou",
        lat=34.5197,
        lon=113.8408,
        timezone="Asia/Shanghai",
        unit="C",
        cluster="Zhengzhou",
        wu_station="ZHCC",
        airport_name="Zhengzhou Xinzheng International Airport",
        aliases=["Zhengzhou"],
        slug_names=["zhengzhou"],
        settlement_source="https://www.wunderground.com/history/daily/cn/zhengzhou/ZHCC",
        historical_peak_hour=15.0,
        diurnal_amplitude=10.0,
    ),
]

# ─────────────────────────────────────────────────────────────
# Pipeline steps (in dependency order)
# ─────────────────────────────────────────────────────────────

PIPELINE_STEPS = [
    {
        "id": "config",
        "name": "Add cities to config/cities.json",
        "type": "python",
    },
    {
        "id": "settlements_scaffold",
        "name": "Create settlement scaffolds (90 days of target dates)",
        "type": "python",
    },
    {
        "id": "market_events",
        "name": "Discover markets from Polymarket Gamma API",
        "type": "python",
    },
    {
        "id": "wu_daily",
        "name": "Backfill WU daily observations + settlements",
        "script": "backfill_wu_daily_all.py",
        "city_flag": "--cities",
        "extra_args": ["--days", "900", "--chunk-days", "31", "--sleep", "0.2"],
        "rate_limited": True,
    },
    {
        "id": "hourly_openmeteo",
        "name": "Backfill hourly observations (OpenMeteo)",
        "script": "backfill_hourly_openmeteo.py",
        "city_flag": "--cities",
        "extra_args": ["--days", "900", "--chunk-days", "90", "--sleep", "0.2"],
    },
    {
        "id": "solar_daily",
        "name": "Compute sunrise/sunset times (astral)",
        "type": "python",
    },
    {
        "id": "temp_persistence",
        "name": "Compute temperature persistence statistics",
        "script": "etl_temp_persistence.py",
    },
    {
        "id": "diurnal_curves",
        "name": "Compute diurnal temperature curves",
        "script": "etl_diurnal_curves.py",
    },
    {
        "id": "openmeteo_previous_runs",
        "name": "Backfill historical forecast source rows (Open-Meteo Previous Runs)",
        "script": "backfill_openmeteo_previous_runs.py",
        "city_flag": "--cities",
        "extra_args": [
            "--days",
            "900",
            "--leads",
            "1,2,3,4,5,6,7",
            "--models",
            "best_match,gfs_global,ecmwf_ifs025,icon_global,ukmo_global_deterministic_10km",
            "--chunk-days",
            "90",
            "--sleep",
            "0.2",
        ],
    },
    {
        "id": "forecast_skill",
        "name": "Materialize forecast skill and model bias",
        "script": "etl_forecast_skill_from_forecasts.py",
    },
    {
        "id": "historical_forecasts",
        "name": "Materialize historical forecast model skill",
        "script": "etl_historical_forecasts.py",
        # VESTIGIAL: etl_historical_forecasts.py writes to historical_forecasts (0 rows)
        # and model_skill (table does not exist post-K1-split). Successor is
        # etl_forecast_skill_from_forecasts.py (step "forecast_skill", CURRENT).
        # Marked optional so pipeline failure here is logged but non-fatal.
        "optional": True,
    },
    {
        "id": "asos_wu_offsets",
        "name": "Compute ASOS-WU station offsets",
        "script": "etl_asos_wu_offset.py",
        "optional": True,
    },
    {
        "id": "obs_instants_v2",
        "name": "Backfill observation_instants (≥365d, data_version=v1.wu-native)",
        "script": "backfill_obs.py",
        "city_flag": "--cities",
        # --start / --end / --data-version injected dynamically via extra_args_factory
        "extra_args_factory": "_obs_instants_v2_extra_args",
    },
    {
        "id": "ens_backfill",
        "name": "Backfill ENS snapshots v1 from OpenMeteo (legacy p_raw lane)",
        "script": "backfill_ens.py",
        # VESTIGIAL/BLOCKED: backfill_ens.py writes to ensemble_snapshots (unsuffixed,
        # does not exist post-K1-split). Canonical is ensemble_snapshots in
        # zeus-forecasts.db with ~40 columns; pre-K1 INSERT shape is incompatible.
        # The live daemon is the canonical writer to ensemble_snapshots and will
        # populate new cities on next operator-initiated daemon restart.
        # Marked optional so pipeline failure here is logged but non-fatal.
        "optional": True,
    },
    {
        "id": "ens_backfill_v2",
        "name": "Backfill ensemble_snapshots from GRIB/TIGGE archive",
        "script": "ingest_grib_to_snapshots.py",
        "city_flag": "--cities",
        # --date-from injected via extra_args_factory
        "extra_args_factory": "_ens_backfill_v2_extra_args",
    },
    {
        "id": "calibration_pairs",
        "name": "Canonical calibration-pair rebuild from verified ENS + observations",
        "script": "rebuild_calibration_pairs_canonical.py",
        # SEV-1 fix: removed --dry-run and optional=True so the rebuild is
        # mandatory and actually writes calibration_pairs_v2 rows.
    },
    {
        "id": "platt_training",
        "name": "Refit Platt v2 models (refit_platt → promote to world.db)",
        "type": "python",
        # Inline Python step: calls refit_platt + promote_platt.
        # Uses zeus-forecasts.db (refuses zeus-world.db per refit_platt safety gate).
        "uses_forecasts_db": True,
        "db_source": "zeus-forecasts.db",
    },
    {
        "id": "fit_ens_bias_v2",
        "name": "Fit model_bias_ens per city (inline ens_bias_repo)",
        "type": "python",
    },
    {
        "id": "monthly_bounds",
        "name": "Regenerate city_monthly_bounds.json (global recompute)",
        "script": "generate_monthly_bounds.py",
        # No city flag — global recompute; new city rows appear automatically
        # once ensemble_snapshots have been backfilled.
    },
    {
        "id": "compute_ddd_floor",
        "name": "Compute v2_city_floors.json entry from observation_instants p05",
        "type": "python",
    },
]


def _city_to_config_dict(c: NewCity) -> dict:
    """Convert a NewCity to the cities.json format."""
    entry = {
        "name": c.name,
        "aliases": c.aliases or [c.name, c.name.lower()],
        "slug_names": c.slug_names or [c.name.lower().replace(" ", "-")],
        "noaa": None,
        "lat": c.lat,
        "lon": c.lon,
        "wu_station": c.wu_station,
        "wu_pws": None,
        "meteostat_station": None,
        "airport_name": c.airport_name,
        "settlement_source": c.settlement_source,
        "timezone": c.timezone,
        "cluster": c.cluster,
        "unit": c.unit,
        "historical_peak_hour": c.historical_peak_hour,
    }
    if c.unit == "C":
        entry["diurnal_amplitude_c"] = c.diurnal_amplitude
    else:
        entry["diurnal_amplitude_f"] = c.diurnal_amplitude
    return entry


def add_cities_to_config(cities: list[NewCity], dry_run: bool = False) -> list[str]:
    """Add new cities to config/cities.json. Returns list of actually added city names."""
    config_path = PROJECT_ROOT / "config" / "cities.json"
    config = json.loads(config_path.read_text())

    existing_names = {c["name"] for c in config["cities"]}
    added = []

    for city in cities:
        if city.name in existing_names:
            logger.info("  SKIP %s — already in config", city.name)
            continue
        entry = _city_to_config_dict(city)
        if dry_run:
            logger.info("  [DRY RUN] Would add %s (%s, %s)", city.name, city.cluster, city.unit)
        else:
            config["cities"].append(entry)
            logger.info("  ADDED %s (%s, %s, ICAO=%s)", city.name, city.cluster, city.unit, city.wu_station)
        added.append(city.name)

    if not dry_run and added:
        # Atomic write
        tmp = config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(config_path)
        logger.info("  Config updated: %d → %d cities", len(existing_names), len(config["cities"]))

    return added


def scaffold_settlements(city_names: list[str], days: int = 90, dry_run: bool = False):
    """Deprecated no-op: settlements require full market/source provenance.

    Empty city/date scaffolds were useful before INV-14 and REOPEN-2, but
    they now create semantically incomplete rows. Settlement truth must be
    written by the harvester or a packet-approved reconstruction path that can
    populate metric identity, market identity, source, rounding, and
    provenance together.
    """
    verb = "[DRY RUN] Would skip" if dry_run else "SKIP"
    logger.info(
        "  %s settlement scaffolding for %d days × %d cities; "
        "settlements require harvester/reconstruction provenance",
        verb,
        days,
        len(city_names),
    )


def discover_market_events(city_names: list[str], dry_run: bool = False):
    """Discover active Polymarket weather markets for cities and populate market_events.

    Uses the Gamma API to find temperature markets, then inserts bin structures
    into the market_events table so ENS backfill and calibration can proceed.
    """
    if dry_run:
        logger.info("  [DRY RUN] Would scan Polymarket Gamma for %d cities", len(city_names))
        return

    from src.data.market_scanner import find_weather_markets
    from src.state.db import get_forecasts_connection

    conn = get_forecasts_connection(write_class="bulk")
    city_set = set(city_names)

    try:
        # Fetch all weather markets with low min_hours to catch recent ones
        events = find_weather_markets(min_hours_to_resolution=0.0)
        logger.info("  Gamma API returned %d total weather markets", len(events))
    except Exception as e:
        logger.warning("  Gamma API call failed: %s — market_events will be empty", e)
        conn.close()
        return

    inserted = 0
    matched_cities = set()
    for event in events:
        city = event.get("city")
        if city is None or city.name not in city_set:
            continue
        matched_cities.add(city.name)

        target_date = event.get("target_date")
        market_slug = event.get("slug", "")
        temperature_metric = event.get("temperature_metric") or "high"
        for outcome in event.get("outcomes", []):
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO market_events_v2
                    (market_slug, city, target_date, temperature_metric,
                     condition_id, token_id,
                     range_label, range_low, range_high, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """, (
                    market_slug,
                    city.name,
                    target_date,
                    temperature_metric,
                    outcome.get("condition_id", ""),
                    outcome.get("token_id", ""),
                    outcome.get("title", ""),
                    outcome.get("range_low"),
                    outcome.get("range_high"),
                ))
                inserted += 1
            except Exception as e:
                logger.debug("  Insert failed: %s", e)

    conn.commit()
    conn.close()

    no_markets = city_set - matched_cities
    logger.info("  Inserted %d market_events for %d cities", inserted, len(matched_cities))
    if no_markets:
        logger.info("  No Polymarket markets found for: %s", ", ".join(sorted(no_markets)))
        logger.info("  (These cities will skip calibration until markets are created)")


def _noaa_sunrise_sunset_utc(target: date, lat: float, lon: float) -> tuple[datetime, datetime]:
    """Approximate sunrise/sunset UTC using the NOAA solar equations."""
    day_of_year = target.timetuple().tm_yday
    gamma = 2.0 * math.pi / 365.0 * (day_of_year - 1)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    lat_rad = math.radians(lat)
    zenith = math.radians(90.833)
    cos_hour_angle = (
        math.cos(zenith) / (math.cos(lat_rad) * math.cos(decl))
        - math.tan(lat_rad) * math.tan(decl)
    )
    cos_hour_angle = max(-1.0, min(1.0, cos_hour_angle))
    hour_angle = math.degrees(math.acos(cos_hour_angle))
    solar_noon_utc_minutes = 720.0 - 4.0 * lon - eqtime
    sunrise_minutes = solar_noon_utc_minutes - 4.0 * hour_angle
    sunset_minutes = solar_noon_utc_minutes + 4.0 * hour_angle
    midnight = datetime(target.year, target.month, target.day, tzinfo=timezone.utc)
    return (
        midnight + timedelta(minutes=sunrise_minutes),
        midnight + timedelta(minutes=sunset_minutes),
    )


def compute_solar_daily(cities: list[NewCity], days: int = 440, dry_run: bool = False):
    """Compute sunrise/sunset times using astral or a built-in NOAA fallback.

    Generates solar_daily entries for each city × date from coordinates alone.
    No external JSONL file needed.
    """
    if dry_run:
        logger.info("  [DRY RUN] Would compute solar times for %d cities × %d days", len(cities), days)
        return

    use_astral = True
    try:
        from astral import Observer
        from astral.sun import sun
    except ImportError:
        use_astral = False
        logger.info("  astral not installed — using NOAA solar fallback")

    from src.state.db import get_world_connection

    conn = get_world_connection(write_class="bulk")
    today = date.today()
    inserted = 0

    for city in cities:
        observer = Observer(latitude=city.lat, longitude=city.lon, elevation=0) if use_astral else None
        local_tz = ZoneInfo(city.timezone)

        for d in range(days):
            target = today - timedelta(days=d)
            try:
                if use_astral:
                    s = sun(observer, date=target, tzinfo=local_tz)
                    sunrise_dt = s["sunrise"]
                    sunset_dt = s["sunset"]
                else:
                    sunrise_dt, sunset_dt = _noaa_sunrise_sunset_utc(
                        target,
                        city.lat,
                        city.lon,
                    )
                    sunrise_dt = sunrise_dt.astimezone(local_tz)
                    sunset_dt = sunset_dt.astimezone(local_tz)
                sunrise_local = sunrise_dt.strftime("%Y-%m-%dT%H:%M:%S%z")
                sunset_local = sunset_dt.strftime("%Y-%m-%dT%H:%M:%S%z")
                sunrise_utc = sunrise_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                sunset_utc = sunset_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                # DST detection
                target_dt = datetime(target.year, target.month, target.day, 12, tzinfo=local_tz)
                jan1 = datetime(target.year, 1, 1, 12, tzinfo=local_tz)
                offset_now = target_dt.utcoffset().total_seconds() / 60
                offset_std = jan1.utcoffset().total_seconds() / 60
                dst_active = 1 if abs(offset_now - offset_std) > 0 else 0

                conn.execute("""
                    INSERT OR REPLACE INTO solar_daily
                    (city, target_date, timezone, lat, lon,
                     sunrise_local, sunset_local, sunrise_utc, sunset_utc,
                     utc_offset_minutes, dst_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    city.name, target.isoformat(), city.timezone, city.lat, city.lon,
                    sunrise_local, sunset_local, sunrise_utc, sunset_utc,
                    int(offset_now), dst_active,
                ))
                inserted += 1
            except Exception as e:
                logger.debug("Solar calc failed %s %s: %s", city.name, target, e)

    conn.commit()
    conn.close()
    logger.info("  Computed %d solar_daily entries for %d cities", inserted, len(cities))


# ─────────────────────────────────────────────────────────────
# Extra-args factories for steps that need dynamic date/path args
# ─────────────────────────────────────────────────────────────

# Number of lookback days for observation_instants backfill.
_OBS_V2_BACKFILL_DAYS = 400


def _obs_instants_v2_extra_args() -> list[str]:
    """Dynamic args for backfill_obs.py: --start, --end, --data-version."""
    end_date = date.today()
    start_date = end_date - timedelta(days=_OBS_V2_BACKFILL_DAYS)
    return [
        "--start", start_date.isoformat(),
        "--end", end_date.isoformat(),
        "--data-version", "v1.wu-native",
    ]


def _ens_backfill_v2_extra_args() -> list[str]:
    """Dynamic args for ingest_grib_to_snapshots.py: --date-from."""
    start_date = date.today() - timedelta(days=_OBS_V2_BACKFILL_DAYS)
    return ["--date-from", start_date.isoformat()]


# Registry so run_script can resolve factory names to callables.
_EXTRA_ARGS_FACTORIES: dict[str, object] = {
    "_obs_instants_v2_extra_args": _obs_instants_v2_extra_args,
    "_ens_backfill_v2_extra_args": _ens_backfill_v2_extra_args,
}


def run_script(step: dict, city_names: list[str], dry_run: bool = False) -> bool:
    """Run a single pipeline script. Returns True on success."""
    script = step.get("script")
    if not script:
        return True

    script_path = PROJECT_ROOT / "scripts" / script
    if not script_path.exists():
        logger.error("  Script not found: %s", script_path)
        return False

    cmd = [sys.executable, str(script_path)]

    # Add city flag if supported
    if "city_flag" in step and city_names:
        cmd.append(step["city_flag"])
        cmd.extend(city_names)

    # Add extra args (static list)
    cmd.extend(step.get("extra_args", []))

    # Add dynamic extra args from factory (for steps needing runtime-computed dates)
    factory_name = step.get("extra_args_factory")
    if factory_name:
        factory = _EXTRA_ARGS_FACTORIES.get(factory_name)
        if factory is None:
            logger.error("  Unknown extra_args_factory: %s", factory_name)
            return False
        cmd.extend(factory())

    if dry_run:
        logger.info("  [DRY RUN] Would run: %s", " ".join(cmd))
        return True

    logger.info("  Running: %s", " ".join(cmd[-4:]))
    start = time.time()
    try:
        result = subprocess_run_with_write_class(
            cmd,
            WriteClass.BULK,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour max per step
        )
        elapsed = time.time() - start

        if result.returncode == 0:
            # Show last few lines of output
            lines = result.stdout.strip().split("\n")
            for line in lines[-3:]:
                logger.info("    %s", line)
            logger.info("  ✅ %s completed in %.1fs", script, elapsed)
            return True
        else:
            logger.error("  ❌ %s failed (exit %d) in %.1fs", script, result.returncode, elapsed)
            for line in (result.stderr or result.stdout).strip().split("\n")[-5:]:
                logger.error("    %s", line)
            return False
    except subprocess.TimeoutExpired:
        logger.error("  ❌ %s timed out after 1 hour", script)
        return False


def run_pipeline(
    cities: list[NewCity],
    dry_run: bool = False,
    skip_wu_daily: bool = False,
    start_from: str | None = None,
):
    """Run the full onboarding pipeline for a batch of new cities."""
    # Fail-closed precondition: every city must be registered in TIER_SCHEDULE
    # before ETL steps can execute.  Runs for BOTH dry-run and live so dry-run
    # serves as a proper pre-flight that surfaces missing registrations early.
    for c in cities:
        _check_city_registered(c.name)

    total_steps = len(PIPELINE_STEPS)
    logger.info("=" * 70)
    logger.info("ZEUS CITY ONBOARDING PIPELINE")
    logger.info("Cities: %s", ", ".join(c.name for c in cities))
    logger.info("Mode: %s", "DRY RUN" if dry_run else "LIVE")
    logger.info("Steps: %d", total_steps)
    logger.info("=" * 70)

    city_names = [c.name for c in cities]
    started = start_from is None
    _ens_bias_zero_cities: set[str] = set()  # cities where fit_ens_bias_v2 produced 0 rows

    for step_num, step in enumerate(PIPELINE_STEPS, 1):
        step_id = step["id"]

        if not started:
            if start_from == step_id:
                started = True
            else:
                continue

        if skip_wu_daily and step_id == "wu_daily":
            logger.info("\n[%d/%d] SKIPPED %s (--skip-wu-daily)", step_num, total_steps, step["name"])
            continue

        logger.info("\n[%d/%d] %s...", step_num, total_steps, step["name"])

        # Custom Python steps — dispatched by step_id
        if step_id == "config":
            added = add_cities_to_config(cities, dry_run=dry_run)
            if not added and not dry_run:
                logger.info("  No new cities to add — all already in config")
            continue

        if step_id == "settlements_scaffold":
            scaffold_settlements(city_names, days=900, dry_run=dry_run)
            continue

        if step_id == "market_events":
            discover_market_events(city_names, dry_run=dry_run)
            continue

        if step_id == "solar_daily":
            compute_solar_daily(cities, days=900, dry_run=dry_run)
            continue

        if step_id == "platt_training":
            _run_platt_training(city_names, dry_run=dry_run)
            continue

        if step_id == "fit_ens_bias_v2":
            _ens_bias_zero_cities = _run_fit_ens_bias_v2(city_names, dry_run=dry_run)
            continue

        if step_id == "compute_ddd_floor":
            _write_nstar_stubs(city_names, dry_run=dry_run)
            _run_compute_ddd_floor(city_names, dry_run=dry_run)
            continue

        # Script-based steps
        if step.get("optional"):
            success = run_script(step, city_names, dry_run=dry_run)
            if not success and not dry_run:
                logger.warning("  Optional step %s failed — continuing", step["name"])
            continue

        success = run_script(step, city_names, dry_run=dry_run)
        if not success and not dry_run:
            logger.error("Pipeline failed at step: %s", step["name"])
            logger.error("Fix the issue and resume with: --start-from %s", step_id)
            return False

    logger.info("\n" + "=" * 70)
    logger.info("PIPELINE COMPLETE")
    logger.info("=" * 70)

    # Verification summary
    if not dry_run:
        _print_verification(city_names)
        _record_deferred_artifacts(city_names, ens_bias_no_coverage=_ens_bias_zero_cities)

    return True


def _print_verification(city_names: list[str]):
    """Print data coverage summary for newly onboarded cities.

    Post-K1-split: world-class tables are queried via get_world_connection();
    forecast-class tables are queried via get_forecasts_connection().
    """
    from src.state.db import get_world_connection, get_forecasts_connection

    world_tables, forecast_tables = _verification_tables()
    placeholders = ",".join("?" * len(city_names))

    logger.info("\nDATA COVERAGE VERIFICATION:")
    logger.info("-" * 60)

    try:
        conn = get_world_connection(write_class=None)
        for table in world_tables:
            try:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE city IN ({placeholders})",
                    city_names,
                ).fetchone()
                count = row[0] if row else 0
                status = "OK" if count > 0 else "EMPTY"
                logger.info("  %5s %-30s %d rows  [world.db]", status, table, count)
            except Exception:
                logger.info("  %5s %-30s (table missing)  [world.db]", "SKIP", table)
        conn.close()
    except Exception as e:
        logger.warning("World-DB verification skipped: %s", e)

    try:
        conn = get_forecasts_connection(write_class=None)
        for table in forecast_tables:
            try:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE city IN ({placeholders})",
                    city_names,
                ).fetchone()
                count = row[0] if row else 0
                status = "OK" if count > 0 else "EMPTY"
                logger.info("  %5s %-30s %d rows  [forecasts.db]", status, table, count)
            except Exception:
                logger.info("  %5s %-30s (table missing)  [forecasts.db]", "SKIP", table)
        conn.close()
    except Exception as e:
        logger.warning("Forecasts-DB verification skipped: %s", e)


def _verification_tables() -> tuple[list[str], list[str]]:
    """Return (world_tables, forecast_tables) after K1 split.

    world-class: observations, observation_instants, solar_daily,
                 temp_persistence, diurnal_curves, forecasts,
                 forecast_skill, model_bias, asos_wu_offsets
    forecast-class: settlement_outcomes, market_events, ensemble_snapshots,
                    calibration_pairs

    Removed vestigial: historical_forecasts (0 rows), model_skill (table gone).

    Canonical names (version-drop 2026-06-10): the forecast-class tables were
    renamed by the B3/B3cont collapse — settlements_v2 -> settlement_outcomes,
    market_events_v2 -> market_events, calibration_pairs_v2 -> calibration_pairs
    (verified live on zeus-forecasts.db). The dropped _v2 names made this
    read-only COUNT verification silently report "(table missing)" for 3 of 4
    forecast tables; the bare/renamed names below hit the live tables.
    """
    world_tables = [
        "observations",
        "observation_instants",
        "solar_daily",
        "temp_persistence",
        "diurnal_curves",
        "forecasts",
        "forecast_skill",
        "model_bias",
        "asos_wu_offsets",
    ]
    forecast_tables = [
        "settlement_outcomes",
        "market_events",
        "ensemble_snapshots",
        "calibration_pairs",
    ]
    return world_tables, forecast_tables


# ─────────────────────────────────────────────────────────────
# Inline Python pipeline step implementations
# ─────────────────────────────────────────────────────────────

def _run_platt_training(city_names: list[str], dry_run: bool = False) -> None:
    """Run refit_platt.py (write to forecasts.db) then promote to world.db.

    Two-stage:
      1. refit_platt.py --db <forecasts.db> --no-dry-run --force
         Reads calibration_pairs_v2 from zeus-forecasts.db.
         Refuses zeus-world.db (safety gate in the script).
      2. promote_platt.py promote
           --stage-db <forecasts.db> --prod-db <world.db> --commit
    """
    from src.state.db import ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH

    refit_script = PROJECT_ROOT / "scripts" / "refit_platt.py"
    promote_script = PROJECT_ROOT / "scripts" / "promote_platt.py"

    for script in (refit_script, promote_script):
        if not script.exists():
            logger.error("  Script not found: %s", script)
            return

    refit_cmd = [
        sys.executable, str(refit_script),
        "--db", str(ZEUS_FORECASTS_DB_PATH),
        "--no-dry-run",
        "--force",
    ]
    promote_cmd = [
        sys.executable, str(promote_script),
        "promote",
        "--stage-db", str(ZEUS_FORECASTS_DB_PATH),
        "--prod-db", str(ZEUS_WORLD_DB_PATH),
        "--commit",
    ]

    if dry_run:
        logger.info("  [DRY RUN] Would run: %s", " ".join(refit_cmd[-4:]))
        logger.info("  [DRY RUN] Would run: %s", " ".join(promote_cmd[-4:]))
        return

    for cmd, label in [(refit_cmd, "refit_platt"), (promote_cmd, "promote_platt")]:
        logger.info("  Running: %s", label)
        try:
            result = subprocess_run_with_write_class(
                cmd,
                WriteClass.BULK,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=3600,
            )
            if result.returncode != 0:
                logger.error("  %s failed (exit %d)", label, result.returncode)
                for line in (result.stderr or result.stdout).strip().split("\n")[-5:]:
                    logger.error("    %s", line)
                raise RuntimeError(f"Platt training step failed at {label}")
            lines = result.stdout.strip().split("\n")
            for line in lines[-3:]:
                logger.info("    %s", line)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Platt training step timed out at {label}")


# Canonical data versions for ENS bias fitting (see test_ens_bias_repo.py)
_ENS_LIVE_DATA_VERSION = "ecmwf_opendata_mx2t3_local_calendar_day_max"
_ENS_PRIOR_DATA_VERSION = "tigge_mx2t6_local_calendar_day_max"

# Seasons used for per-season bias estimation
_ENS_SEASONS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("DJF", (12, 1, 2)),
    ("MAM", (3, 4, 5)),
    ("JJA", (6, 7, 8)),
    ("SON", (9, 10, 11)),
)


def _run_fit_ens_bias_v2(city_names: list[str], dry_run: bool = False) -> set[str]:
    """Fit model_bias_ens for each city/season/metric using ens_bias_repo.

    Calls fit_city_predictive_error() (from ens_error_model) per (city, season,
    metric) bucket, then writes the posterior to model_bias_ens via
    write_bias_model().  init_ens_bias_schema() is called defensively so the
    table is created even if it did not exist yet in zeus-forecasts.db.
    """
    if dry_run:
        logger.info("  [DRY RUN] Would fit model_bias_ens for: %s", city_names)
        return set()

    try:
        from src.calibration.ens_bias_repo import (
            init_ens_bias_schema,
            write_bias_model,
        )
        from src.calibration.ens_error_model import fit_city_predictive_error
        from src.state.db import get_forecasts_connection
    except ImportError as exc:
        logger.error("  Cannot import ens_bias modules: %s", exc)
        return set()

    today_str = date.today().isoformat()

    conn = get_forecasts_connection(write_class="bulk")
    try:
        init_ens_bias_schema(conn)
        conn.commit()

        # Track fitted bucket count per city to detect zero-coverage cities individually.
        # A city with no TIGGE/GRIB data will have fitted_per_city[city]==0 across all
        # season/metric buckets; those cities are returned as deferred-sidecar candidates
        # even if other cities in the batch did fit successfully.
        fitted_per_city: dict[str, int] = {city: 0 for city in city_names}
        skipped = 0
        for city in city_names:
            for season, months in _ENS_SEASONS:
                for metric in ("high", "low"):
                    try:
                        model = fit_city_predictive_error(
                            conn,
                            city=city,
                            live_data_version=_ENS_LIVE_DATA_VERSION,
                            prior_data_version=_ENS_PRIOR_DATA_VERSION,
                            season_months=tuple(months),
                            metric=metric,
                        )
                        write_bias_model(
                            conn,
                            city=city,
                            season=season,
                            metric=metric,
                            live_data_version=_ENS_LIVE_DATA_VERSION,
                            prior_data_version=_ENS_PRIOR_DATA_VERSION,
                            posterior_bias_c=model.bias_c,
                            posterior_sd_c=model.bias_sd_c,
                            n_live=0,   # PredictiveErrorModel does not expose n_live; use producer for full lineage
                            n_prior=0,  # PredictiveErrorModel does not expose n_prior; use producer for full lineage
                            weight_live=0.0,
                            estimator="ens_error_model.fit_city_predictive_error",
                            training_cutoff=today_str,
                            recorded_at=today_str,
                            # canonical extension fields (requires migration on target DB)
                            error_model_family="none",
                            bias_c=model.bias_c,
                            bias_sd_c=model.bias_sd_c,
                            residual_sd_c=model.residual_sd_c,
                            heterogeneity_var_c2=model.heterogeneity_var_c2,
                            correction_strength=model.correction_strength,
                            effective_bias_c=model.effective_bias_c,
                            total_residual_sd_c=model.total_residual_sd_c,
                            authority="STAGING",
                        )
                        fitted_per_city[city] += 1
                    except (ValueError, RuntimeError) as exc:
                        logger.debug("  ENS bias skipped %s/%s/%s: %s", city, season, metric, exc)
                        skipped += 1

        fitted = sum(fitted_per_city.values())
        conn.commit()
        logger.info("  ENS bias v2: fitted=%d skipped=%d", fitted, skipped)
        zero_coverage_cities = {city for city, count in fitted_per_city.items() if count == 0}
        if zero_coverage_cities:
            logger.warning(
                "  fit_ens_bias_v2: ZERO buckets fitted for %s — likely no "
                "TIGGE/GRIB coverage yet.  model_bias_ens is empty for "
                "these cities; recording as PENDING in deferred sidecar.",
                sorted(zero_coverage_cities),
            )
            return zero_coverage_cities  # caller adds to deferred sidecar
    except Exception as exc:
        logger.error("  fit_ens_bias_v2 failed: %s", exc)
        conn.rollback()
        raise
    finally:
        conn.close()
    return set()


def _write_nstar_stubs(city_names: list[str], dry_run: bool = False) -> None:
    """Write N_STAR_NOT_FOUND stubs for new cities into v2_nstar.json.

    Writes ``{city}_high`` and ``{city}_low`` stub entries ONLY if absent
    (never clobbers calibrated entries). Atomic write — same pattern as
    _run_compute_ddd_floor.

    Without these stubs get_n_star() raises DDDFailClosed(DDD_NSTAR_UNCONFIGURED)
    for any trade decision on the new city.
    """
    nstar_path = PROJECT_ROOT / "src" / "oracle" / "ddd_artifacts" / "v2_nstar.json"

    if dry_run:
        logger.info("  [DRY RUN] Would write N_star stubs for: %s", city_names)
        return

    if not nstar_path.exists():
        logger.error("  v2_nstar.json not found at %s — cannot write stubs", nstar_path)
        return

    with nstar_path.open() as f:
        nstar_data = json.load(f)

    per_city_metric: dict = nstar_data.setdefault("per_city_metric", {})
    added: list[str] = []
    for city_name in city_names:
        for track in ("high", "low"):
            key = f"{city_name}_{track}"
            if key not in per_city_metric:
                per_city_metric[key] = {"status": "N_STAR_NOT_FOUND", "N_star": None}
                added.append(key)
                logger.info("  N_star stub written: %s", key)
            else:
                logger.debug("  N_star key already exists (no-op): %s", key)

    if not added:
        logger.info("  N_star stubs: all keys already present, no writes needed")
        return

    tmp_path = nstar_path.with_suffix(".json.tmp")
    with tmp_path.open("w") as f:
        json.dump(nstar_data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp_path.replace(nstar_path)
    logger.info("  v2_nstar.json updated — added stubs: %s", added)


def _run_compute_ddd_floor(city_names: list[str], dry_run: bool = False) -> None:
    """Compute and write v2_city_floors.json entries for each new city.

    Algorithm (matches existing floor_method in _metadata):
      final_floor = max(p05_directional_coverage, SAFETY_MINIMUM_FLOOR)

    Coverage is sampled from observation_instants over ≥365 recent dates
    per city, using the canonical settlement source and data_version.
    """
    floors_path = PROJECT_ROOT / "src" / "oracle" / "ddd_artifacts" / "v2_city_floors.json"

    if dry_run:
        logger.info("  [DRY RUN] Would compute DDD floors for: %s", city_names)
        return

    try:
        from src.oracle.data_density_discount import SAFETY_MINIMUM_FLOOR
        from src.state.db import get_world_connection
        from src.config import cities_by_name
    except ImportError as exc:
        logger.error("  Cannot import DDD modules: %s", exc)
        return

    # Load current floors config
    with floors_path.open() as f:
        floors_data: dict = json.load(f)
    per_city: dict = floors_data.setdefault("per_city", {})

    conn = get_world_connection(write_class=None)
    try:
        for city_name in city_names:
            city_cfg = cities_by_name.get(city_name)
            if city_cfg is None:
                logger.warning("  DDD floor: city %r not in cities.json — skipping", city_name)
                continue

            # Determine canonical peak-hour window using ddd_wiring's directional_window
            # (WINDOW_RADIUS=3, matches runtime DDD coverage quantile semantics)
            from src.engine.ddd_wiring import directional_window
            peak_hour = getattr(city_cfg, "historical_peak_hour", 14.5)
            target_hours = directional_window(peak_hour)
            if not target_hours:
                target_hours = list(range(8, 21))  # fallback: 8am–8pm

            # Collect per-date directional coverage over the lookback window
            lookback_days = 400
            coverages: list[float] = []
            for delta in range(lookback_days):
                target_date = (date.today() - timedelta(days=delta)).isoformat()
                in_clause = ",".join(str(h) for h in target_hours)
                row = conn.execute(
                    f"""
                    SELECT COUNT(DISTINCT CAST(local_hour AS INTEGER)) AS hrs
                    FROM observation_instants
                    WHERE city = ?
                      AND source = 'wu_icao_history'
                      AND data_version = 'v1.wu-native'
                      AND target_date = ?
                      AND CAST(local_hour AS INTEGER) IN ({in_clause})
                    """,
                    (city_name, target_date),
                ).fetchone()
                hrs = (row[0] or 0) if row else 0
                coverages.append(hrs / len(target_hours) if target_hours else 0.0)

            if not coverages:
                logger.warning("  DDD floor: no coverage data for %r — using SAFETY_MINIMUM_FLOOR", city_name)
                p05 = 0.0
            else:
                sorted_covs = sorted(coverages)
                p05_idx = max(0, int(len(sorted_covs) * 0.05) - 1)
                p05 = sorted_covs[p05_idx]

            final_floor = max(p05, SAFETY_MINIMUM_FLOOR)

            sorted_covs_local = sorted(coverages)
            entry = {
                "p05": round(p05, 4),
                "p10": round(sorted_covs_local[max(0, int(len(sorted_covs_local) * 0.10) - 1)], 4),
                "p25": round(sorted_covs_local[max(0, int(len(sorted_covs_local) * 0.25) - 1)], 4),
                "recommended_floor_empirical": round(final_floor, 4),
                "policy_override": None,
                "final_floor": round(final_floor, 4),
                "train_FP_rate": 0.0,
                "n_zero_train": sum(1 for c in coverages if c == 0.0),
                "sigma_diagnostic": 0.0,
                "floor_source": "empirical_p05",
                "computed_date": date.today().isoformat(),
            }
            per_city[city_name] = entry
            logger.info("  DDD floor %s: p05=%.3f final_floor=%.3f", city_name, p05, final_floor)
    finally:
        conn.close()

    # Atomic write back to floors file
    tmp_path = floors_path.with_suffix(".json.tmp")
    with tmp_path.open("w") as f:
        json.dump(floors_data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp_path.replace(floors_path)
    logger.info("  v2_city_floors.json updated for: %s", city_names)


def _record_deferred_artifacts(
    city_names: list[str],
    *,
    ens_bias_no_coverage: set[str] | None = None,
) -> None:
    """Write per-city PENDING sidecar files for deferred artifacts.

    These files signal to operators (and bridge scripts) that the city
    is onboarded but certain calibration artifacts require accumulated
    live history before they become available.

    ``ens_bias_no_coverage``: set of city names where fit_ens_bias_v2
    produced zero fitted buckets (no TIGGE/GRIB coverage).  These cities
    get an additional model_bias_ens=PENDING entry in their sidecar.

    Written to: data/onboarding_pending/<city_slug>.json
    """
    pending_dir = PROJECT_ROOT / "data" / "onboarding_pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    ens_bias_no_coverage = ens_bias_no_coverage or set()

    for city_name in city_names:
        slug = city_name.lower().replace(" ", "_")
        deferred: dict = {
            artifact: {"status": "PENDING", "reason": reason}
            for artifact, reason in DEFERRED_ARTIFACTS.items()
        }
        if city_name in ens_bias_no_coverage:
            deferred["model_bias_ens"] = {
                "status": "PENDING",
                "reason": (
                    "fit_ens_bias_v2 produced 0 fitted buckets — "
                    "city has no TIGGE/GRIB ensemble_snapshots coverage yet; "
                    "re-run after ingest_grib_to_snapshots backfill completes."
                ),
            }
        pending: dict = {
            "city": city_name,
            "onboarded_at": date.today().isoformat(),
            "deferred_artifacts": deferred,
            "oracle_status": "MISSING",
            "oracle_effective_multiplier": 0.5,
            "note": (
                "City is onboarded and will receive ARCHIVE-derivable data. "
                "Deferred artifacts require explicit authority evidence. "
                "oracle_error_rates is MISSING (mult=0.5) until a reviewed "
                "oracle authority rebuild lands."
            ),
        }
        sidecar = pending_dir / f"{slug}.json"
        with sidecar.open("w") as f:
            json.dump(pending, f, indent=2, ensure_ascii=False)
            f.write("\n")
        logger.info("  Deferred-artifacts sidecar: %s", sidecar.relative_to(PROJECT_ROOT))


CLUSTER_RULES = [
    # (lat_min, lat_max, lon_min, lon_max, cluster_name)
    (-90, -15, -180, 180, "Southern-Hemisphere-Tropical"),
    (-15, 15, 90, 180, "Southeast-Asia-Equatorial"),
    (-15, 15, -90, 90, "Tropical"),
    (15, 35, 90, 150, "Asia-Subtropical"),
    (35, 55, 120, 150, "Asia-Northeast"),
    (35, 55, 90, 120, "Asia-East-China"),
    (15, 35, 40, 90, "Middle-East-Arabian"),
    (35, 55, -15, 40, "Europe-Continental"),
    (45, 70, -15, 40, "Europe-Eastern"),
    (35, 55, -15, 10, "Europe-Mediterranean"),
    (50, 70, -15, 10, "Europe-Maritime"),
    (25, 50, -130, -60, "US-generic"),
    (-60, 15, -130, -30, "Latin-America-Tropical"),
    (-15, 15, -30, 60, "Africa-West-Tropical"),
    (-40, -15, 10, 60, "Africa-South-Maritime"),
    (-55, 0, 140, 180, "Oceania-Maritime"),
    (15, 35, 60, 90, "India-North"),
]


def _guess_cluster(lat: float, lon: float) -> str:
    """Best-effort cluster assignment from coordinates."""
    for lat_min, lat_max, lon_min, lon_max, cluster in CLUSTER_RULES:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return cluster
    return "Unclassified"


def _guess_unit(lat: float, lon: float) -> str:
    """F for US cities, C for everything else."""
    if 25 <= lat <= 50 and -130 <= lon <= -60:
        return "F"
    return "C"


def auto_discover_city(city_name: str) -> NewCity | None:
    """Auto-discover city metadata from just a name.

    Uses:
    - OpenMeteo Geocoding API → lat, lon, country
    - timezonefinder → timezone
    - Geographic heuristics → cluster, unit
    - WU station search → ICAO code

    Returns a NewCity with all fields populated, or None on failure.
    """
    import requests

    # 1. Geocoding: get coordinates
    logger.info("  Geocoding '%s'...", city_name)
    try:
        resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city_name, "count": 5, "language": "en"},
            timeout=10,
        )
        results = resp.json().get("results", [])
        if not results:
            logger.error("  Geocoding failed: no results for '%s'", city_name)
            return None
    except Exception as e:
        logger.error("  Geocoding API error: %s", e)
        return None

    # Pick the top result (usually the major city)
    geo = results[0]
    lat = geo["latitude"]
    lon = geo["longitude"]
    country_code = geo.get("country_code", "").upper()
    admin = geo.get("admin1", "")
    logger.info("  Found: %s, %s (%s) → %.4f, %.4f", city_name, admin, country_code, lat, lon)

    # 2. Timezone
    try:
        from timezonefinder import TimezoneFinder
        tf = TimezoneFinder()
        tz_name = tf.timezone_at(lat=lat, lng=lon)
        if not tz_name:
            tz_name = geo.get("timezone", "UTC")
    except ImportError:
        tz_name = geo.get("timezone", "UTC")
    logger.info("  Timezone: %s", tz_name)

    # 3. Unit and cluster
    unit = _guess_unit(lat, lon)
    cluster = _guess_cluster(lat, lon)
    logger.info("  Unit: %s, Cluster: %s", unit, cluster)

    # 4. ICAO station lookup via WU geocoding
    icao = _find_nearest_icao(lat, lon, country_code)
    if not icao:
        logger.warning("  Could not auto-detect ICAO station — you'll need to set it manually")
        icao = "XXXX"
    else:
        logger.info("  ICAO station: %s", icao)

    slug = city_name.lower().replace(" ", "-")
    return NewCity(
        name=city_name,
        lat=lat,
        lon=lon,
        timezone=tz_name,
        unit=unit,
        cluster=cluster,
        wu_station=icao,
        airport_name=f"{city_name} Airport",
        aliases=[city_name, city_name.lower()],
        slug_names=[slug],
        settlement_source=f"https://www.wunderground.com/history/daily/{country_code.lower()}/{slug}/{icao}",
        historical_peak_hour=14.5,
        diurnal_amplitude=8.0 if abs(lat) > 30 else 5.0,
    )


def _find_nearest_icao(lat: float, lon: float, country_code: str) -> str | None:
    """Find the nearest major airport ICAO code using WU autocomplete API."""
    import requests

    # Use OpenMeteo's built-in weather station search (free, no key)
    try:
        resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": f"airport {country_code}", "count": 1},
            timeout=5,
        )
    except Exception:
        pass

    # Fallback: use a curated mapping of country → major airport ICAO prefixes
    COUNTRY_ICAO_PREFIX = {
        "US": "K", "CA": "C", "MX": "MM", "BR": "SB", "AR": "SA",
        "GB": "EG", "FR": "LF", "DE": "ED", "ES": "LE", "IT": "LI",
        "NL": "EH", "PL": "EP", "RU": "UU", "TR": "LT", "IL": "LL",
        "CN": "Z", "JP": "RJ", "KR": "RK", "TW": "RC", "SG": "WS",
        "HK": "VH", "IN": "VI", "MY": "WM", "ID": "WI", "TH": "VT",
        "NZ": "NZ", "AU": "Y", "ZA": "FA", "NG": "DN", "EG": "HE",
        "SA": "OE", "AE": "OM", "QA": "OT", "PA": "MP", "CO": "SK",
        "CL": "SC", "PE": "SP", "PH": "RP", "VN": "VV",
    }

    # Try WU station search
    try:
        resp = requests.get(
            "https://api.weather.com/v3/location/search",
            params={
                "query": f"{lat},{lon}",
                "language": "en-US",
                "format": "json",
                "apiKey": "e1f10a1e78da46f5b10a1e78da96f525",  # Public WU web key
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            icao_list = data.get("location", {}).get("iataCode", [])
            if icao_list:
                return icao_list[0]
    except Exception:
        pass

    # Fallback: construct from country prefix + generic
    prefix = COUNTRY_ICAO_PREFIX.get(country_code, "")
    if prefix:
        logger.info("  ICAO prefix for %s: %s (manual verification needed)", country_code, prefix)
    return None


def interactive_discover(city_names: list[str]):
    """Interactively discover and confirm city metadata, then run pipeline."""
    discovered = []

    for name in city_names:
        logger.info("\n" + "=" * 60)
        logger.info("DISCOVERING: %s", name)
        logger.info("=" * 60)

        city = auto_discover_city(name)
        if city is None:
            logger.error("Failed to discover %s — skipping", name)
            continue

        # Display for confirmation
        print(f"\n{'─' * 50}")
        print(f"  Name:       {city.name}")
        print(f"  Lat/Lon:    {city.lat:.4f}, {city.lon:.4f}")
        print(f"  Timezone:   {city.timezone}")
        print(f"  Unit:       {city.unit}")
        print(f"  Cluster:    {city.cluster}")
        print(f"  ICAO:       {city.wu_station}")
        print(f"  Settlement: {city.settlement_source}")
        print(f"{'─' * 50}")

        confirm = input(f"  Accept {city.name}? [Y/n/edit] ").strip().lower()
        if confirm == "n":
            logger.info("  Skipped %s", name)
            continue
        elif confirm == "edit":
            # Allow editing individual fields
            new_icao = input(f"  ICAO [{city.wu_station}]: ").strip()
            if new_icao:
                city = NewCity(**{**city.__dict__, "wu_station": new_icao})
            new_cluster = input(f"  Cluster [{city.cluster}]: ").strip()
            if new_cluster:
                city = NewCity(**{**city.__dict__, "cluster": new_cluster})
            new_tz = input(f"  Timezone [{city.timezone}]: ").strip()
            if new_tz:
                city = NewCity(**{**city.__dict__, "timezone": new_tz})

        discovered.append(city)
        logger.info("  ✅ Confirmed %s", city.name)

    if not discovered:
        logger.info("No cities confirmed — exiting")
        return

    # Run pipeline
    confirm_run = input(f"\nRun pipeline for {len(discovered)} cities? [Y/n] ").strip().lower()
    if confirm_run == "n":
        # Just print the NEW_CITIES code for manual addition
        print("\n# Add to NEW_CITIES in onboard_cities.py:")
        for c in discovered:
            print(f"""    NewCity(
        name="{c.name}",
        lat={c.lat},
        lon={c.lon},
        timezone="{c.timezone}",
        unit="{c.unit}",
        cluster="{c.cluster}",
        wu_station="{c.wu_station}",
        airport_name="{c.airport_name}",
        aliases={c.aliases},
        slug_names={c.slug_names},
        settlement_source="{c.settlement_source}",
    ),""")
        return

    success = run_pipeline(discovered, dry_run=False)
    sys.exit(0 if success else 1)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="One-click city onboarding pipeline for Zeus",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--discover", nargs="+",
                        help="Auto-discover city metadata from names (interactive)")
    parser.add_argument("--cities", nargs="+", help="City names to onboard (must be in NEW_CITIES)")
    parser.add_argument("--all", action="store_true", help="Onboard all cities in NEW_CITIES")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without doing it")
    parser.add_argument("--skip-wu-daily", action="store_true", help="Skip WU daily backfill (rate limited)")
    parser.add_argument("--start-from", choices=[s["id"] for s in PIPELINE_STEPS],
                        help="Resume from a specific step")
    parser.add_argument("--list", action="store_true", help="List available new cities")

    args = parser.parse_args()

    if args.list:
        print("\nAvailable new cities:")
        for c in NEW_CITIES:
            print(f"  {c.name:20s} {c.cluster:30s} {c.unit} ICAO={c.wu_station}")
        return

    if args.discover:
        interactive_discover(args.discover)
        return

    if not args.cities and not args.all:
        parser.print_help()
        print("\nError: specify --discover, --cities, or --all")
        sys.exit(1)

    # Resolve city list
    city_map = {c.name: c for c in NEW_CITIES}
    if args.all:
        cities = NEW_CITIES
    else:
        cities = []
        for name in args.cities:
            if name in city_map:
                cities.append(city_map[name])
            else:
                logger.error("Unknown city: %s (available: %s)",
                             name, ", ".join(city_map.keys()))
                sys.exit(1)

    success = run_pipeline(
        cities,
        dry_run=args.dry_run,
        skip_wu_daily=args.skip_wu_daily,
        start_from=args.start_from,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
