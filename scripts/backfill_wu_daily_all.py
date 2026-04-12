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
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone as _tz
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests

from src.state.db import get_world_connection, init_schema
from src.config import cities_by_name
from src.data.ingestion_guard import IngestionGuard
from src.types.observation_atom import ObservationAtom, IngestionRejected
from src.calibration.manager import hemisphere_for_lat, season_from_date


def _write_atom_to_observations(conn, atom: ObservationAtom) -> None:
    """Single authoritative write path for observations. Uses K1 atom schema."""
    conn.execute("""
        INSERT OR REPLACE INTO observations (
            city, target_date, source, high_temp, unit, station_id, fetched_at,
            raw_value, raw_unit, target_unit, value_type,
            fetch_utc, local_time, collection_window_start_utc, collection_window_end_utc,
            timezone, utc_offset_minutes, dst_active,
            is_ambiguous_local_hour, is_missing_local_hour,
            hemisphere, season, month,
            rebuild_run_id, data_source_version,
            authority, provenance_metadata
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?
        )
    """, (
        atom.city, atom.target_date.isoformat(), atom.source, atom.value, atom.target_unit,
        atom.station_id, atom.fetch_utc.isoformat(),
        atom.raw_value, atom.raw_unit, atom.target_unit, atom.value_type,
        atom.fetch_utc.isoformat(), atom.local_time.isoformat(),
        atom.collection_window_start_utc.isoformat(), atom.collection_window_end_utc.isoformat(),
        atom.timezone, atom.utc_offset_minutes, int(atom.dst_active),
        int(atom.is_ambiguous_local_hour), int(atom.is_missing_local_hour),
        atom.hemisphere, atom.season, atom.month,
        atom.rebuild_run_id, atom.data_source_version,
        atom.authority, json.dumps(atom.provenance_metadata),
    ))

logger = logging.getLogger(__name__)

WU_API_KEY = os.environ.get("WU_API_KEY", "e1f10a1e78da46f5b10a1e78da96f525")
# Default preserves existing behavior; set WU_API_KEY env var to override.
WU_ICAO_HISTORY_URL = "https://api.weather.com/v1/location/{icao}:9:{cc}/observations/historical.json"

# Module-level guard instance (loads config/city_monthly_bounds.json once)
_GUARD = IngestionGuard()
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
            local_date = datetime.fromtimestamp(int(epoch), _tz.utc).astimezone(tz).date()
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
    dry_run: bool = False,
) -> dict:
    """Backfill WU daily observations for one city using ICAO station data.

    When dry_run=True, all API fetches and guard validations run as normal but
    no rows are written to the DB. Returns fetched/passed/rejected counts.
    """
    info = CITY_STATIONS.get(city_name)
    if not info:
        print(f"  SKIP: {city_name} not in CITY_STATIONS")
        return {"city": city_name, "collected": 0, "skip": 0, "err": 0}

    icao, cc, unit = info
    city_cfg = cities_by_name.get(city_name)
    timezone_name = city_cfg.timezone if city_cfg is not None else "UTC"
    collected = 0
    skip_count = 0
    err_count = 0
    guard_rejected = 0
    request_count = 0
    rebuild_run_id = f"backfill_wu_daily_all_{datetime.now(_tz.utc).isoformat()}"

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

            # Settlement derivation moved to K4 rebuild_settlements.py (Revision 3 plan)

            existing = conn.execute(
                "SELECT high_temp FROM observations WHERE city = ? AND target_date = ? AND source = 'wu_icao_history'",
                (city_name, target_str),
            ).fetchone()

            target_d = date.fromisoformat(target_str)
            tz = ZoneInfo(timezone_name)
            fetch_utc = datetime.now(_tz.utc)

            # Fix 3: local_time is the expected peak time on the target date in local tz,
            # NOT the script runtime converted to local tz. This gives semantically correct
            # provenance for historical atoms.
            from src.signal.diurnal import _is_missing_local_hour as _is_missing
            peak_hour_raw = city_cfg.historical_peak_hour if city_cfg else 15.0
            _peak_h = int(peak_hour_raw)
            _peak_m = int((peak_hour_raw - _peak_h) * 60)
            local_time = datetime(
                target_d.year, target_d.month, target_d.day,
                _peak_h, _peak_m,
                tzinfo=tz,
            )

            # Fix 2: compute is_missing_local_hour from actual local_time rather than hardcoding False
            is_missing_local = _is_missing(local_time, tz)
            is_ambiguous = bool(getattr(local_time, "fold", 0))
            dst_offset = local_time.dst()
            dst_active = bool(dst_offset and dst_offset.total_seconds() > 0)
            utc_offset = local_time.utcoffset()
            utc_offset_min = int(utc_offset.total_seconds() // 60) if utc_offset is not None else 0

            window_start_local = datetime(target_d.year, target_d.month, target_d.day, 0, 0, tzinfo=tz)
            window_end_local = datetime(target_d.year, target_d.month, target_d.day, 23, 59, 59, tzinfo=tz)
            window_start_utc = window_start_local.astimezone(_tz.utc)
            window_end_utc = window_end_local.astimezone(_tz.utc)

            hemisphere = hemisphere_for_lat(city_cfg.lat) if city_cfg else "N"
            season = season_from_date(target_str, lat=city_cfg.lat if city_cfg else 90.0)

            try:
                _GUARD.validate(
                    city=city_name,
                    raw_value=high,
                    raw_unit=unit,
                    fetch_utc=fetch_utc,
                    target_date=target_d,
                    peak_hour=city_cfg.historical_peak_hour if city_cfg else 15.0,
                    local_time=local_time,
                    hemisphere=hemisphere,
                )
            except IngestionRejected as e:
                guard_rejected += 1
                logger.warning("Guard rejected %s/%s: %s", city_name, target_str, e)
                current += timedelta(days=1)
                continue

            atom = ObservationAtom(
                city=city_name,
                target_date=target_d,
                value_type="high",
                value=high,
                target_unit=unit,
                raw_value=high,
                raw_unit=unit,
                source="wu_icao_history",
                station_id=icao,
                api_endpoint=f"https://api.weather.com/v1/location/{icao}:9:{cc}/observations/historical.json",
                fetch_utc=fetch_utc,
                local_time=local_time,
                collection_window_start_utc=window_start_utc,
                collection_window_end_utc=window_end_utc,
                timezone=timezone_name,
                utc_offset_minutes=utc_offset_min,
                dst_active=dst_active,
                is_ambiguous_local_hour=is_ambiguous,
                is_missing_local_hour=is_missing_local,
                hemisphere=hemisphere,
                season=season,
                month=target_d.month,
                rebuild_run_id=rebuild_run_id,
                data_source_version="wu_icao_v1_2026",
                authority="VERIFIED",
                validation_pass=True,
                provenance_metadata={},
            )

            if not dry_run:
                _write_atom_to_observations(conn, atom)

            if existing is None:
                collected += 1
            else:
                skip_count += 1
            current += timedelta(days=1)

        if not dry_run:
            conn.commit()
        chunk_label = "[DRY RUN] " if dry_run else ""
        print(
            f"    {chunk_label}{chunk_start} -> {chunk_end}: "
            f"collected={collected} skip={skip_count} err={err_count} guard_rejected={guard_rejected}"
        )
        time.sleep(sleep_seconds)


    if not dry_run:
        conn.commit()
    return {"city": city_name, "collected": collected, "skip": skip_count, "err": err_count, "guard_rejected": guard_rejected, "requests": request_count}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cities", nargs="+", default=None)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--chunk-days", type=int, default=31)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--missing-only", action="store_true", help="Fetch only dates missing WU observations or valued settlements")
    parser.add_argument("--all", action="store_true", help="All configured cities")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate but do not write to DB; print per-city summary")
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

    dry_run = args.dry_run
    if dry_run:
        print("[DRY RUN] No rows will be written to the DB.")
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
            dry_run=dry_run,
        )
        results.append(r)
        print(f"  → collected={r['collected']} skip={r['skip']} err={r['err']} guard_rejected={r['guard_rejected']}")

    conn.close()

    if dry_run:
        print("\n=== Dry Run Summary (nothing written) ===")
        print(f"{'city':<22} {'fetched':>7} {'passed':>7} {'rejected':>9} {'would_write':>12}")
        print("-" * 62)
        for r in results:
            fetched = r["collected"] + r["skip"] + r["guard_rejected"]
            passed = r["collected"] + r["skip"]
            print(f"  {r['city']:<20} {fetched:>7} {passed:>7} {r['guard_rejected']:>9} {r['collected']:>12}")
    else:
        print("\n=== Summary ===")
        total = sum(r["collected"] for r in results)
        print(f"Total collected: {total}")
        for r in results:
            if r["collected"] > 0:
                print(f"  {r['city']:20s} +{r['collected']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
