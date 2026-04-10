#!/usr/bin/env python3
"""Backfill WU daily high temperatures for all 38 TIGGE manifest cities.

Uses the WU v1/location/{ICAO}:9:{CC}/observations/historical.json endpoint
to fetch actual WU settlement-source daily highs from ICAO airport stations.

This is the REAL settlement data source — same as what WU page shows and
what Polymarket settles on.

Usage:
    cd zeus && .venv/bin/python scripts/backfill_wu_daily_all.py --all --days 90
    cd zeus && .venv/bin/python scripts/backfill_wu_daily_all.py --cities Beijing Toronto --days 30
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests

from src.state.db import get_shared_connection, init_schema
from src.contracts.settlement_semantics import SettlementSemantics
from src.config import cities_by_name

logger = logging.getLogger(__name__)

WU_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
WU_ICAO_HISTORY_URL = "https://api.weather.com/v1/location/{icao}:9:{cc}/observations/historical.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# Complete mapping: city → (ICAO, country_code, unit)
CITY_STATIONS = {
    # US cities
    "NYC":           ("KLGA", "US", "F"),
    "Chicago":       ("KORD", "US", "F"),
    "Atlanta":       ("KATL", "US", "F"),
    "Austin":        ("KAUS", "US", "F"),
    "Dallas":        ("KDAL", "US", "F"),
    "Denver":        ("KBKF", "US", "F"),
    "Houston":       ("KHOU", "US", "F"),
    "Los Angeles":   ("KLAX", "US", "F"),
    "Miami":         ("KMIA", "US", "F"),
    "San Francisco": ("KSFO", "US", "F"),
    "Seattle":       ("KSEA", "US", "F"),
    # Americas
    "Buenos Aires":  ("SAEZ", "AR", "C"),
    "Mexico City":   ("MMMX", "MX", "C"),
    "Sao Paulo":     ("SBGR", "BR", "C"),
    "Toronto":       ("CYYZ", "CA", "C"),
    # Europe
    "London":        ("EGLL", "GB", "C"),
    "Paris":         ("LFPG", "FR", "C"),
    "Munich":        ("EDDM", "DE", "C"),
    "Madrid":        ("LEMD", "ES", "C"),
    "Milan":         ("LIMC", "IT", "C"),
    "Warsaw":        ("EPWA", "PL", "C"),
    "Moscow":        ("UUEE", "RU", "C"),
    "Istanbul":      ("LTFM", "TR", "C"),
    "Ankara":        ("LTAC", "TR", "C"),
    "Tel Aviv":      ("LLBG", "IL", "C"),
    # Asia
    "Beijing":       ("ZBAA", "CN", "C"),
    "Shanghai":      ("ZSPD", "CN", "C"),
    "Shenzhen":      ("ZGSZ", "CN", "C"),
    "Chengdu":       ("ZUUU", "CN", "C"),
    "Chongqing":     ("ZUCK", "CN", "C"),
    "Wuhan":         ("ZHHH", "CN", "C"),
    "Hong Kong":     ("VHHH", "HK", "C"),
    "Tokyo":         ("RJTT", "JP", "C"),
    "Seoul":         ("RKSI", "KR", "C"),
    "Taipei":        ("RCTP", "TW", "C"),
    "Singapore":     ("WSSS", "SG", "C"),
    "Lucknow":       ("VILK", "IN", "C"),
    # Oceania
    "Wellington":    ("NZWN", "NZ", "C"),
    "Auckland":      ("NZAA", "NZ", "C"),
    # Africa
    "Lagos":         ("DNMM", "NG", "C"),
    "Cape Town":     ("FACT", "ZA", "C"),
    # Middle East
    "Jeddah":        ("OEJN", "SA", "C"),
    # Southeast Asia
    "Kuala Lumpur":  ("WMKK", "MY", "C"),
    "Jakarta":       ("WIII", "ID", "C"),
    # Asia-Northeast
    "Busan":         ("RKPK", "KR", "C"),
    # Latin America
    "Panama City":   ("MPTO", "PA", "C"),
}


def _fetch_wu_icao_daily_high(icao: str, cc: str, target_date: date, unit: str) -> float | None:
    """Fetch daily high from WU ICAO station history — the real settlement source."""
    url = WU_ICAO_HISTORY_URL.format(icao=icao, cc=cc)
    date_str = target_date.strftime("%Y%m%d")
    unit_code = "m" if unit == "C" else "e"

    try:
        resp = requests.get(
            url,
            params={"apiKey": WU_API_KEY, "units": unit_code,
                    "startDate": date_str, "endDate": date_str},
            timeout=15, headers=HEADERS,
        )
        if resp.status_code != 200:
            return None

        observations = resp.json().get("observations", [])
        if not observations:
            return None

        temps = [o["temp"] for o in observations if o.get("temp") is not None]
        return float(max(temps)) if temps else None

    except Exception as e:
        logger.debug("WU ICAO fetch failed %s:%s %s: %s", icao, cc, target_date, e)
        return None


def backfill_city(city_name: str, days_back: int, conn) -> dict:
    """Backfill WU daily observations for one city using ICAO station data."""
    info = CITY_STATIONS.get(city_name)
    if not info:
        print(f"  SKIP: {city_name} not in CITY_STATIONS")
        return {"city": city_name, "collected": 0, "skip": 0, "err": 0}

    icao, cc, unit = info
    collected = 0
    skip_count = 0
    err_count = 0

    for days_ago in range(2, days_back + 1):
        target = date.today() - timedelta(days=days_ago)

        # Check if already have WU data for this date
        existing = conn.execute(
            "SELECT high_temp FROM observations WHERE city = ? AND target_date = ? AND source = 'wu_icao_history'",
            (city_name, target.isoformat()),
        ).fetchone()
        if existing is not None:
            skip_count += 1
            continue

        high = _fetch_wu_icao_daily_high(icao, cc, target, unit)

        if high is not None:
            # Settlement precision gate: round to integer per contract
            city_cfg = cities_by_name.get(city_name)
            if city_cfg is not None:
                _sem = SettlementSemantics.for_city(city_cfg)
            else:
                # Fallback for cities not yet in config
                _sem = (SettlementSemantics.default_wu_celsius(icao) if unit == "C"
                        else SettlementSemantics.default_wu_fahrenheit(icao))
            settlement_val = _sem.assert_settlement_value(
                high, context=f"backfill_wu_daily_all:{city_name}"
            )

            # Insert/update observation (keep raw for obs, rounded for settlement)
            conn.execute("""
                INSERT OR REPLACE INTO observations
                (city, target_date, source, high_temp, unit, fetched_at)
                VALUES (?, ?, 'wu_icao_history', ?, ?, ?)
            """, (city_name, target.isoformat(), high, unit,
                  datetime.now(timezone.utc).isoformat()))

            # Also fill settlement_value if missing — ROUNDED
            conn.execute("""
                UPDATE settlements
                SET settlement_value = ?, settlement_source = ?
                WHERE city = ? AND target_date = ?
                AND (settlement_value IS NULL OR settlement_value = ''
                     OR settlement_source = 'openmeteo_archive_daily_max')
            """, (settlement_val, f"wu_icao_{icao}", city_name, target.isoformat()))

            collected += 1
        else:
            err_count += 1

        # Rate limit: ~30 req/min safe
        time.sleep(2.5)

        if (collected + skip_count + err_count) % 10 == 0 and collected + err_count > 0:
            conn.commit()
            print(f"    at {target}: collected={collected} skip={skip_count} err={err_count}")

    conn.commit()
    return {"city": city_name, "collected": collected, "skip": skip_count, "err": err_count}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cities", nargs="+", default=None)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--all", action="store_true", help="All 38 cities")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    conn = get_shared_connection()
    init_schema(conn)

    if args.cities:
        targets = args.cities
    elif args.all:
        targets = list(CITY_STATIONS.keys())
    else:
        # Default: cities without wu_icao_history observations
        covered = {r[0] for r in conn.execute(
            "SELECT DISTINCT city FROM observations WHERE source = 'wu_icao_history'"
        ).fetchall()}
        targets = [c for c in CITY_STATIONS if c not in covered]

    print(f"=== WU ICAO Station History Backfill ({len(targets)} cities, {args.days} days) ===")

    results = []
    for city_name in targets:
        icao, cc, unit = CITY_STATIONS[city_name]
        print(f"\n[{city_name}] {icao}:{cc} unit={unit}")
        r = backfill_city(city_name, args.days, conn)
        results.append(r)
        print(f"  → collected={r['collected']} skip={r['skip']} err={r['err']}")

    conn.close()

    print("\n=== Summary ===")
    total = sum(r["collected"] for r in results)
    print(f"Total collected: {total}")
    for r in results:
        if r["collected"] > 0:
            print(f"  {r['city']:20s} +{r['collected']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
