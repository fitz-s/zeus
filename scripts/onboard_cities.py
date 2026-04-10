#!/usr/bin/env python3
"""One-click city onboarding pipeline for Zeus.

Adds new cities to config and runs all backfill ETLs in dependency order,
achieving data parity with the original 8 cities.

Usage:
    cd zeus
    source ../rainstorm/.venv/bin/activate

    # Dry run — show what would happen
    python scripts/onboard_cities.py --dry-run

    # Onboard specific cities
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
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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
        "extra_args": ["--days", "90"],
        "rate_limited": True,
    },
    {
        "id": "hourly_openmeteo",
        "name": "Backfill hourly observations (OpenMeteo)",
        "script": "backfill_hourly_openmeteo.py",
        "city_flag": "--cities",
        "extra_args": ["--days", "440"],
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
        "id": "historical_forecasts",
        "name": "Backfill historical forecast skill",
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
        "name": "Generate calibration pairs from ENS + settlements",
        "script": "generate_calibration_pairs.py",
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

    # Also update rainstorm config if it exists
    rs_config_path = PROJECT_ROOT.parent / "rainstorm" / "config" / "cities.json"
    if rs_config_path.exists() and not dry_run and added:
        rs_config = json.loads(rs_config_path.read_text())
        rs_existing = {c["name"] for c in rs_config.get("cities", [])}
        for city in cities:
            if city.name not in rs_existing and city.name in added:
                rs_config.setdefault("cities", []).append(_city_to_config_dict(city))
        tmp = rs_config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rs_config, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(rs_config_path)
        logger.info("  Rainstorm config also updated")

    return added


def scaffold_settlements(city_names: list[str], days: int = 90, dry_run: bool = False):
    """Create empty settlement rows for new cities (target_date scaffolds)."""
    if dry_run:
        logger.info("  [DRY RUN] Would scaffold %d days × %d cities", days, len(city_names))
        return

    from src.state.db import get_shared_connection, init_schema
    from datetime import date, timedelta

    conn = get_shared_connection()
    init_schema(conn)

    today = date.today()
    count = 0
    for city_name in city_names:
        for d in range(days):
            target = today - timedelta(days=d)
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO settlements (city, target_date)
                    VALUES (?, ?)
                """, (city_name, target.isoformat()))
                count += 1
            except Exception:
                pass
    conn.commit()
    conn.close()
    logger.info("  Scaffolded %d settlement rows for %d cities", count, len(city_names))


def discover_market_events(city_names: list[str], dry_run: bool = False):
    """Discover active Polymarket weather markets for cities and populate market_events.

    Uses the Gamma API to find temperature markets, then inserts bin structures
    into the market_events table so ENS backfill and calibration can proceed.
    """
    if dry_run:
        logger.info("  [DRY RUN] Would scan Polymarket Gamma for %d cities", len(city_names))
        return

    from src.data.market_scanner import find_weather_markets
    from src.state.db import get_shared_connection

    conn = get_shared_connection()
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


def compute_solar_daily(cities: list[NewCity], days: int = 440, dry_run: bool = False):
    """Compute sunrise/sunset times using the astral library.

    Generates solar_daily entries for each city × date from coordinates alone.
    No external JSONL file needed.
    """
    if dry_run:
        logger.info("  [DRY RUN] Would compute solar times for %d cities × %d days", len(cities), days)
        return

    try:
        from astral import Observer
        from astral.sun import sun
    except ImportError:
        logger.warning("  astral not installed — skipping solar_daily")
        logger.warning("  Install with: pip install astral")
        return

    from datetime import date, timedelta, timezone as tz
    from zoneinfo import ZoneInfo
    from src.state.db import get_shared_connection

    conn = get_shared_connection()
    today = date.today()
    inserted = 0

    for city in cities:
        observer = Observer(latitude=city.lat, longitude=city.lon, elevation=0)
        local_tz = ZoneInfo(city.timezone)

        for d in range(days):
            target = today - timedelta(days=d)
            try:
                s = sun(observer, date=target, tzinfo=local_tz)
                sunrise_local = s["sunrise"].strftime("%Y-%m-%dT%H:%M:%S%z")
                sunset_local = s["sunset"].strftime("%Y-%m-%dT%H:%M:%S%z")
                sunrise_utc = s["sunrise"].astimezone(tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                sunset_utc = s["sunset"].astimezone(tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

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
            scaffold_settlements(city_names, days=90, dry_run=dry_run)
            continue

        if step_id == "market_events":
            discover_market_events(city_names, dry_run=dry_run)
            continue

        if step_id == "solar_daily":
            compute_solar_daily(cities, days=440, dry_run=dry_run)
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
        from src.state.db import get_shared_connection
        conn = get_shared_connection()

        tables = [
            "settlements", "observations", "observation_instants",
            "market_events", "solar_daily", "temp_persistence",
            "diurnal_curves", "ensemble_snapshots",
            "calibration_pairs", "historical_forecasts",
            "asos_wu_offsets",
        ]

        logger.info("\nDATA COVERAGE VERIFICATION:")
        logger.info("-" * 60)
        for table in tables:
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


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="One-click city onboarding pipeline for Zeus",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
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

    if not args.cities and not args.all:
        parser.print_help()
        print("\nError: specify --cities or --all")
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
