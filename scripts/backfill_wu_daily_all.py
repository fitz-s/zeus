#!/usr/bin/env python3
"""Backfill WU daily high temperatures for all configured cities.

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
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests

from src.state.db import get_world_connection, init_schema
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


def _fetch_wu_icao_daily_highs(
    icao: str,
    cc: str,
    start_date: date,
    end_date: date,
    unit: str,
    timezone_name: str,
) -> dict[str, float]:
    """Fetch local-date daily highs from WU ICAO station history."""
    url = WU_ICAO_HISTORY_URL.format(icao=icao, cc=cc)
    unit_code = "m" if unit == "C" else "e"

    try:
        resp = requests.get(
            url,
            params={"apiKey": WU_API_KEY, "units": unit_code,
                    "startDate": start_date.strftime("%Y%m%d"),
                    "endDate": end_date.strftime("%Y%m%d")},
            timeout=30, headers=HEADERS,
        )
        if resp.status_code != 200:
            return {}

        observations = resp.json().get("observations", [])
        if not observations:
            return {}

        tz = ZoneInfo(timezone_name)
        highs: dict[str, float] = {}
        for obs in observations:
            temp = obs.get("temp")
            epoch = obs.get("valid_time_gmt")
            if temp is None or epoch is None:
                continue
            local_date = datetime.fromtimestamp(int(epoch), timezone.utc).astimezone(tz).date()
            if local_date < start_date or local_date > end_date:
                continue
            key = local_date.isoformat()
            highs[key] = max(highs.get(key, float("-inf")), float(temp))
        return {key: value for key, value in highs.items() if value != float("-inf")}

    except Exception as e:
        logger.debug("WU ICAO fetch failed %s:%s %s..%s: %s", icao, cc, start_date, end_date, e)
        return {}


def _date_chunks(start_date: date, end_date: date, chunk_days: int) -> list[tuple[date, date]]:
    chunks = []
    current = start_date
    while current <= end_date:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end_date)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


def _dates_in_range(start_date: date, end_date: date) -> list[date]:
    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)
    return dates


def _contiguous_date_chunks(dates: list[date], chunk_days: int) -> list[tuple[date, date]]:
    if not dates:
        return []
    chunks = []
    chunk_start = dates[0]
    previous = dates[0]
    chunk_len = 1
    for current in dates[1:]:
        if current == previous + timedelta(days=1) and chunk_len < chunk_days:
            previous = current
            chunk_len += 1
            continue
        chunks.append((chunk_start, previous))
        chunk_start = current
        previous = current
        chunk_len = 1
    chunks.append((chunk_start, previous))
    return chunks


def _dates_needing_fetch(conn, city_name: str, start_date: date, end_date: date) -> list[date]:
    existing_wu = {
        row[0]
        for row in conn.execute(
            """
            SELECT target_date
            FROM observations
            WHERE city = ?
              AND source = 'wu_icao_history'
              AND target_date BETWEEN ? AND ?
            """,
            (city_name, start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
    }
    valued_settlements = {
        row[0]
        for row in conn.execute(
            """
            SELECT target_date
            FROM settlements
            WHERE city = ?
              AND settlement_value IS NOT NULL
              AND settlement_value != ''
              AND target_date BETWEEN ? AND ?
            """,
            (city_name, start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
    }
    return [
        target
        for target in _dates_in_range(start_date, end_date)
        if target.isoformat() not in existing_wu
        or target.isoformat() not in valued_settlements
    ]


def backfill_city(
    city_name: str,
    days_back: int,
    conn,
    *,
    chunk_days: int = 31,
    sleep_seconds: float = 0.5,
    missing_only: bool = False,
) -> dict:
    """Backfill WU daily observations for one city using ICAO station data."""
    info = CITY_STATIONS.get(city_name)
    if not info:
        print(f"  SKIP: {city_name} not in CITY_STATIONS")
        return {"city": city_name, "collected": 0, "skip": 0, "err": 0}

    icao, cc, unit = info
    city_cfg = cities_by_name.get(city_name)
    timezone_name = city_cfg.timezone if city_cfg is not None else "UTC"
    if city_cfg is not None:
        sem = SettlementSemantics.for_city(city_cfg)
    else:
        sem = SettlementSemantics(
            resolution_source=f"WU_{icao}",
            measurement_unit=unit,
            precision=1.0,
            rounding_rule="round_half_to_even",
            finalization_time="12:00:00Z",
        )
    collected = 0
    skip_count = 0
    err_count = 0
    request_count = 0

    end_date = date.today() - timedelta(days=2)
    start_date = end_date - timedelta(days=days_back - 1)
    target_dates = (
        _dates_needing_fetch(conn, city_name, start_date, end_date)
        if missing_only
        else _dates_in_range(start_date, end_date)
    )
    target_date_keys = {target.isoformat() for target in target_dates}
    for chunk_start, chunk_end in _contiguous_date_chunks(target_dates, chunk_days):
        request_count += 1
        highs = _fetch_wu_icao_daily_highs(
            icao,
            cc,
            chunk_start,
            chunk_end,
            unit,
            timezone_name,
        )
        if not highs:
            err_count += sum(
                1
                for target in target_dates
                if chunk_start <= target <= chunk_end
            )
            time.sleep(sleep_seconds)
            continue

        current = chunk_start
        while current <= chunk_end:
            target_str = current.isoformat()
            if target_str not in target_date_keys:
                current += timedelta(days=1)
                continue
            high = highs.get(target_str)
            if high is None:
                err_count += 1
                current += timedelta(days=1)
                continue

            existing = conn.execute(
                "SELECT high_temp FROM observations WHERE city = ? AND target_date = ? AND source = 'wu_icao_history'",
                (city_name, target_str),
            ).fetchone()

            settlement_val = sem.assert_settlement_value(
                high, context=f"backfill_wu_daily_all:{city_name}"
            )

            conn.execute("""
                INSERT OR REPLACE INTO observations
                (city, target_date, source, high_temp, unit, fetched_at)
                VALUES (?, ?, 'wu_icao_history', ?, ?, ?)
            """, (city_name, target_str, high, unit,
                  datetime.now(timezone.utc).isoformat()))

            conn.execute("""
                INSERT INTO settlements
                (city, target_date, settlement_value, settlement_source, settled_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(city, target_date) DO UPDATE SET
                    settlement_value = excluded.settlement_value,
                    settlement_source = excluded.settlement_source,
                    settled_at = COALESCE(settlements.settled_at, excluded.settled_at)
                WHERE settlements.settlement_value IS NULL
                   OR settlements.settlement_value = ''
                   OR settlements.settlement_source IS NULL
                   OR settlements.settlement_source = ''
                   OR settlements.settlement_source = 'openmeteo_archive_daily_max'
            """, (
                city_name,
                target_str,
                settlement_val,
                f"wu_icao_{icao}",
                datetime.combine(current, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
            ))

            if existing is None:
                collected += 1
            else:
                skip_count += 1
            current += timedelta(days=1)

        conn.commit()
        print(
            f"    {chunk_start} -> {chunk_end}: "
            f"collected={collected} skip={skip_count} err={err_count}"
        )
        time.sleep(sleep_seconds)


    conn.commit()
    return {"city": city_name, "collected": collected, "skip": skip_count, "err": err_count, "requests": request_count}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cities", nargs="+", default=None)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--chunk-days", type=int, default=31)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--missing-only", action="store_true", help="Fetch only dates missing WU observations or valued settlements")
    parser.add_argument("--all", action="store_true", help="All configured cities")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    conn = get_world_connection()
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
        r = backfill_city(
            city_name,
            args.days,
            conn,
            chunk_days=args.chunk_days,
            sleep_seconds=args.sleep,
            missing_only=args.missing_only,
        )
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
