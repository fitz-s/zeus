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
        "id": "wu_daily",
        "name": "Backfill WU daily observations",
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
        "id": "ens_backfill",
        "name": "Backfill ENS snapshots from OpenMeteo",
        "script": "backfill_ens.py",
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
    logger.info("=" * 70)
    logger.info("ZEUS CITY ONBOARDING PIPELINE")
    logger.info("Cities: %s", ", ".join(c.name for c in cities))
    logger.info("Mode: %s", "DRY RUN" if dry_run else "LIVE")
    logger.info("=" * 70)

    # Step 1: Config
    started = start_from is None
    if not started and start_from == "config":
        started = True

    if started:
        logger.info("\n[1/8] Adding cities to config...")
        added = add_cities_to_config(cities, dry_run=dry_run)
        if not added and not dry_run:
            logger.info("  No new cities to add — all already in config")
    city_names = [c.name for c in cities]

    # Step 2: Settlements scaffold
    if not started and start_from == "settlements_scaffold":
        started = True
    if started:
        logger.info("\n[2/8] Creating settlement scaffolds...")
        scaffold_settlements(city_names, days=90, dry_run=dry_run)

    # Steps 3-8: ETL scripts
    step_num = 3
    for step in PIPELINE_STEPS[2:]:  # Skip config and scaffold
        step_id = step["id"]
        if not started:
            if start_from == step_id:
                started = True
            else:
                step_num += 1
                continue

        if skip_wu_daily and step_id == "wu_daily":
            logger.info("\n[%d/8] SKIPPED %s (--skip-wu-daily)", step_num, step["name"])
            step_num += 1
            continue

        logger.info("\n[%d/8] %s...", step_num, step["name"])
        success = run_script(step, city_names, dry_run=dry_run)
        if not success and not dry_run:
            logger.error("Pipeline failed at step: %s", step["name"])
            logger.error("Fix the issue and resume with: --start-from %s", step_id)
            return False
        step_num += 1

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
            "temp_persistence", "diurnal_curves", "ensemble_snapshots",
            "calibration_pairs", "historical_forecasts",
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
                status = "✅" if count > 0 else "⚠️"
                logger.info("  %s %-25s %d rows", status, table, count)
            except Exception:
                logger.info("  ❓ %-25s (table may not exist)", table)

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
