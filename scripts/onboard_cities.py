#!/usr/bin/env python3
# Lifecycle: created=2026-04-02; last_reviewed=2026-04-24; last_reused=2026-04-24
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
    },
    {
        "id": "asos_wu_offsets",
        "name": "Compute ASOS-WU station offsets",
        "script": "etl_asos_wu_offset.py",
        "optional": True,
    },
    {
        "id": "ens_backfill",
        "name": "Backfill ENS snapshots from OpenMeteo",
        "script": "backfill_ens.py",
    },
    {
        "id": "calibration_pairs",
        "name": "Canonical calibration-pair rebuild from verified ENS + observations",
        "script": "rebuild_calibration_pairs_canonical.py",
        "extra_args": ["--dry-run"],
        "optional": True,
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
    from src.state.db import get_world_connection

    conn = get_world_connection()
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
        event_id = event.get("event_id", "")
        for outcome in event.get("outcomes", []):
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO market_events
                    (market_slug, city, target_date, condition_id, token_id,
                     range_label, range_low, range_high, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """, (
                    event_id,
                    city.name,
                    target_date,
                    outcome.get("market_id", ""),
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

    conn = get_world_connection()
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

    # Add extra args
    cmd.extend(step.get("extra_args", []))

    if dry_run:
        logger.info("  [DRY RUN] Would run: %s", " ".join(cmd))
        return True

    logger.info("  Running: %s", " ".join(cmd[-4:]))
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
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
    total_steps = len(PIPELINE_STEPS)
    logger.info("=" * 70)
    logger.info("ZEUS CITY ONBOARDING PIPELINE")
    logger.info("Cities: %s", ", ".join(c.name for c in cities))
    logger.info("Mode: %s", "DRY RUN" if dry_run else "LIVE")
    logger.info("Steps: %d", total_steps)
    logger.info("=" * 70)

    city_names = [c.name for c in cities]
    started = start_from is None

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

        # Custom Python steps
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

        # Script-based steps
        if step.get("optional"):
            success = run_script(step, city_names, dry_run=dry_run)
            if not success and not dry_run:
                logger.warning("  ⚠️  Optional step %s failed — continuing", step["name"])
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

    return True


def _print_verification(city_names: list[str]):
    """Print data coverage summary for newly onboarded cities."""
    try:
        from src.state.db import get_world_connection
        conn = get_world_connection()

        logger.info("\nDATA COVERAGE VERIFICATION:")
        logger.info("-" * 60)
        for table in _verification_tables():
            try:
                placeholders = ",".join("?" * len(city_names))
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE city IN ({placeholders})",
                    city_names,
                ).fetchone()
                count = row[0] if row else 0
                status = "OK" if count > 0 else "EMPTY"
                logger.info("  %5s %-25s %d rows", status, table, count)
            except Exception:
                logger.info("  %5s %-25s (table missing)", "SKIP", table)

        conn.close()
    except Exception as e:
        logger.warning("Verification skipped: %s", e)


def _verification_tables() -> list[str]:
    return [
        "settlements",
        "observations",
        "observation_instants",
        "market_events",
        "solar_daily",
        "temp_persistence",
        "diurnal_curves",
        "forecasts",
        "forecast_skill",
        "model_bias",
        "ensemble_snapshots",
        "calibration_pairs",
        "historical_forecasts",
        "model_skill",
        "asos_wu_offsets",
    ]


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
