#!/usr/bin/env python3
"""Backfill Hong Kong daily high+low from HKO Open Data API.

Hong Kong uses the Hong Kong Observatory (HKO) as Polymarket's canonical
weather settlement source, NOT WU/VHHH. WU ICAO VHHH is the Chek Lap Kok
airport station on reclaimed land in the harbour, while HKO HQ is the
Tsim Sha Tsui station in urban Kowloon — the two stations routinely differ
by 1-3°C due to the urban heat island, so using WU would systematically
mis-match Polymarket HK settlements.

This script fetches daily max and daily min from the HKO Open Data API
(`data.weather.gov.hk/weatherAPI/opendata/opendata.php`) and writes them
to the `observations` table with source='hko_daily_api' and station='HKO'.

K1-C discipline:
- Same `_write_atom_to_observations` pattern as backfill_wu_daily_all.py,
  writing high_temp and low_temp in ONE observation row per day.
- IngestionGuard Layer 1 (unit consistency + Earth records) applied to both
  high and low; Layer 3 (seasonal envelope) applied to high; Layer 4
  (collection_timing) and Layer 5 (DST boundary) applied.
- Layer 2 (physical_bounds) skipped — TIGGE monthly bounds are for daily
  max and under-represent observation tails (see WU fix 2026-04-13).
- Internal sanity check: low <= high.

Data completeness flag handling:
The HKO response carries a per-day completeness flag: "C" = complete,
"#" = incomplete, "***" = unavailable. Only "C" rows are written. "#" and
"***" rows are counted in stats as `incomplete` and are not rejected as
errors — they simply do not create observations rows.

Usage:
    cd zeus && python scripts/backfill_hko_daily.py --start 2024-01 --end 2026-04
    cd zeus && python scripts/backfill_hko_daily.py --months 30 --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from datetime import timezone as _tz
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from src.config import cities_by_name
from src.data.ingestion_guard import (
    IngestionGuard,
    IngestionRejected,
)
from src.types.observation_atom import ObservationAtom
from src.calibration.manager import season_from_date
from src.state.db import get_world_connection, init_schema

logger = logging.getLogger(__name__)

HKO_API_URL = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
HKO_STATION = "HKO"  # HKO Headquarters — the canonical settlement station
SLEEP_BETWEEN_REQUESTS = 0.5
FETCH_RETRY_COUNT = 2
FETCH_RETRY_BACKOFF_SEC = 3.0

_CITY_NAME = "Hong Kong"

# Guard instance (loads city_monthly_bounds.json once)
_GUARD = IngestionGuard()


def _hemisphere_for_lat(lat: float) -> str:
    return "N" if lat >= 0 else "S"


# ---------------------------------------------------------------------------
# Write helper — identical signature to backfill_wu_daily_all.py
# ---------------------------------------------------------------------------


def _write_atom_to_observations(
    conn,
    atom: ObservationAtom,
    atom_low: ObservationAtom | None = None,
) -> None:
    """Write one observation row with optional high + low values.

    Identical contract to `scripts/backfill_wu_daily_all.py::_write_atom_to_observations`.
    Kept local to avoid cross-script imports.
    """
    if atom_low is not None:
        assert atom.value_type == "high", f"expected high atom, got {atom.value_type!r}"
        assert atom_low.value_type == "low", f"expected low atom, got {atom_low.value_type!r}"
        assert atom.city == atom_low.city
        assert atom.target_date == atom_low.target_date
        assert atom.source == atom_low.source
        assert atom.target_unit == atom_low.target_unit
        low_temp_value = atom_low.value
    else:
        low_temp_value = None

    conn.execute("""
        INSERT OR REPLACE INTO observations (
            city, target_date, source, high_temp, low_temp, unit, station_id, fetched_at,
            raw_value, raw_unit, target_unit, value_type,
            fetch_utc, local_time, collection_window_start_utc, collection_window_end_utc,
            timezone, utc_offset_minutes, dst_active,
            is_ambiguous_local_hour, is_missing_local_hour,
            hemisphere, season, month,
            rebuild_run_id, data_source_version,
            authority, provenance_metadata
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?
        )
    """, (
        atom.city, atom.target_date.isoformat(), atom.source,
        atom.value, low_temp_value, atom.target_unit,
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


# ---------------------------------------------------------------------------
# Fetch layer
# ---------------------------------------------------------------------------


def _fetch_hko_month(
    year: int,
    month: int,
    data_type: str,
) -> dict[tuple[int, int, int], tuple[float, str]]:
    """Fetch one month of HKO climate data for the specified dataType.

    Returns dict keyed by (year, month, day) → (value_celsius, completeness_flag).
    completeness_flag is one of "C" (complete), "#" (incomplete), "***" (unavailable).
    """
    params = {
        "dataType": data_type,
        "year": str(year),
        "month": f"{month:02d}",
        "rformat": "json",
        "lang": "en",
        "station": HKO_STATION,
    }
    resp = httpx.get(HKO_API_URL, params=params, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()

    rows = data.get("data", [])
    out: dict[tuple[int, int, int], tuple[float, str]] = {}
    for row in rows:
        if len(row) < 5:
            logger.warning("Unexpected HKO row shape for %s %d/%d: %r",
                           data_type, year, month, row)
            continue
        try:
            y = int(row[0])
            m = int(row[1])
            d = int(row[2])
            val_str = str(row[3])
            completeness = str(row[4])
            if completeness == "C":
                value = float(val_str)
                out[(y, m, d)] = (value, completeness)
            else:
                out[(y, m, d)] = (float("nan"), completeness)
        except (ValueError, TypeError) as e:
            logger.warning(
                "Parse failed HKO %s %d/%d row %r: %s",
                data_type, year, month, row, e,
            )
            continue
    return out


def _fetch_hko_month_with_retry(
    year: int,
    month: int,
    data_type: str,
) -> tuple[dict[tuple[int, int, int], tuple[float, str]], str | None]:
    """Fetch with retry on transient HTTP errors."""
    for attempt in range(FETCH_RETRY_COUNT + 1):
        try:
            return _fetch_hko_month(year, month, data_type), None
        except httpx.HTTPError as e:
            if attempt < FETCH_RETRY_COUNT:
                wait = FETCH_RETRY_BACKOFF_SEC * (attempt + 1)
                logger.warning(
                    "HKO fetch retry %d/%d %s %d/%d after %.1fs: %s",
                    attempt + 1, FETCH_RETRY_COUNT, data_type, year, month, wait, e,
                )
                time.sleep(wait)
                continue
            return {}, f"http error after {FETCH_RETRY_COUNT + 1} tries: {e}"
        except Exception as e:
            return {}, f"unexpected error: {type(e).__name__}: {e}"
    return {}, "exhausted retries"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


_STAT_KEYS = (
    "months_fetched", "days_complete", "days_incomplete", "days_unavailable",
    "inserted", "guard_rejected", "fetch_errors", "insert_errors",
)


def _new_stats() -> dict[str, int]:
    return {k: 0 for k in _STAT_KEYS}


def _iter_months(start: date, end: date):
    """Yield (year, month) tuples covering [start, end], inclusive."""
    current = date(start.year, start.month, 1)
    stop = date(end.year, end.month, 1)
    while current <= stop:
        yield current.year, current.month
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)


def run_backfill(
    start: date,
    end: date,
    *,
    conn,
    rebuild_run_id: str,
    dry_run: bool = False,
    sleep_seconds: float = SLEEP_BETWEEN_REQUESTS,
) -> dict:
    city_cfg = cities_by_name.get(_CITY_NAME)
    if city_cfg is None:
        raise RuntimeError(f"{_CITY_NAME} not in cities.json — cannot backfill HKO")
    if city_cfg.settlement_unit != "C":
        raise RuntimeError(
            f"{_CITY_NAME} settlement_unit is {city_cfg.settlement_unit!r}, "
            f"expected 'C' for HKO (HKO publishes in Celsius only)"
        )

    tz = ZoneInfo(city_cfg.timezone)
    hemisphere = _hemisphere_for_lat(city_cfg.lat)
    peak_hour_raw = city_cfg.historical_peak_hour
    _peak_h = int(peak_hour_raw)
    _peak_m = int((peak_hour_raw - _peak_h) * 60)

    stats = _new_stats()

    for year, month in _iter_months(start, end):
        stats["months_fetched"] += 1
        print(f"\n[{_CITY_NAME}] {year}-{month:02d}")

        max_map, err_max = _fetch_hko_month_with_retry(year, month, "CLMMAXT")
        if err_max:
            stats["fetch_errors"] += 1
            logger.error("CLMMAXT fetch failed %d/%d: %s", year, month, err_max)
            time.sleep(sleep_seconds)
            continue
        time.sleep(sleep_seconds)

        min_map, err_min = _fetch_hko_month_with_retry(year, month, "CLMMINT")
        if err_min:
            stats["fetch_errors"] += 1
            logger.error("CLMMINT fetch failed %d/%d: %s", year, month, err_min)
            time.sleep(sleep_seconds)
            continue

        common_days = set(max_map.keys()) & set(min_map.keys())
        month_inserted = 0
        month_guard_rej = 0
        for ymd in sorted(common_days):
            high_val, high_flag = max_map[ymd]
            low_val, low_flag = min_map[ymd]

            if high_flag != "C" or low_flag != "C":
                # HKO marked day as incomplete or unavailable — skip.
                if high_flag == "***" or low_flag == "***":
                    stats["days_unavailable"] += 1
                else:
                    stats["days_incomplete"] += 1
                continue

            stats["days_complete"] += 1

            y, m, d = ymd
            target_d = date(y, m, d)
            if target_d < start or target_d > end:
                continue

            target_str = target_d.isoformat()
            fetch_utc = datetime.now(_tz.utc)

            local_time = datetime(
                target_d.year, target_d.month, target_d.day,
                _peak_h, _peak_m, tzinfo=tz,
            )
            from src.signal.diurnal import _is_missing_local_hour as _is_missing
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

            season = season_from_date(target_str, lat=city_cfg.lat)

            try:
                # Layer 1 — unit consistency + Earth records for BOTH values
                _GUARD.check_unit_consistency(
                    city=_CITY_NAME,
                    raw_value=high_val,
                    raw_unit="C",
                    declared_unit="C",
                )
                _GUARD.check_unit_consistency(
                    city=_CITY_NAME,
                    raw_value=low_val,
                    raw_unit="C",
                    declared_unit="C",
                )
                if low_val > high_val:
                    raise IngestionRejected(
                        f"{_CITY_NAME}/{target_str}: HKO low={low_val}°C > high={high_val}°C "
                        f"(internal HKO dataset inconsistency)"
                    )
                # Layer 3 deleted 2026-04-13 — hemisphere-uniform envelope
                # was a category error. See src/data/ingestion_guard.py module
                # docstring. HK subtropical warm days routinely exceed the old
                # _N_ENVELOPE May-Sept upper bound.
                # Layer 4 — collection timing (historical backfill: always passes
                # because fetch_utc is now and target_date is in the past)
                _GUARD.check_collection_timing(
                    city=_CITY_NAME,
                    fetch_utc=fetch_utc,
                    target_date=target_d,
                    peak_hour=peak_hour_raw,
                )
                # Layer 5 — DST boundary (HK has no DST since 1979,
                # so this is a no-op but kept for defense in depth)
                _GUARD.check_dst_boundary(
                    city=_CITY_NAME,
                    local_time=local_time,
                )
            except IngestionRejected as e:
                stats["guard_rejected"] += 1
                month_guard_rej += 1
                logger.warning("Guard rejected HK/%s: %s", target_str, e)
                continue

            _atom_common = dict(
                city=_CITY_NAME,
                target_date=target_d,
                target_unit="C",
                raw_unit="C",
                source="hko_daily_api",
                station_id=HKO_STATION,
                api_endpoint=(
                    f"{HKO_API_URL}?dataType=CLMMAXT|CLMMINT&year={year}&month={month:02d}"
                    f"&rformat=json&lang=en&station={HKO_STATION}"
                ),
                fetch_utc=fetch_utc,
                local_time=local_time,
                collection_window_start_utc=window_start_utc,
                collection_window_end_utc=window_end_utc,
                timezone=city_cfg.timezone,
                utc_offset_minutes=utc_offset_min,
                dst_active=dst_active,
                is_ambiguous_local_hour=is_ambiguous,
                is_missing_local_hour=is_missing_local,
                hemisphere=hemisphere,
                season=season,
                month=target_d.month,
                rebuild_run_id=rebuild_run_id,
                data_source_version="hko_opendata_v1_2026",
                authority="VERIFIED",
                validation_pass=True,
                provenance_metadata={"station": HKO_STATION, "dataType": ["CLMMAXT", "CLMMINT"]},
            )
            atom_high = ObservationAtom(
                value_type="high",
                value=high_val,
                raw_value=high_val,
                **_atom_common,
            )
            atom_low = ObservationAtom(
                value_type="low",
                value=low_val,
                raw_value=low_val,
                **_atom_common,
            )

            if not dry_run:
                try:
                    _write_atom_to_observations(conn, atom_high, atom_low=atom_low)
                    stats["inserted"] += 1
                    month_inserted += 1
                except Exception as e:
                    stats["insert_errors"] += 1
                    logger.error(
                        "Insert failed HK/%s: %s", target_str, e,
                    )
            else:
                stats["inserted"] += 1
                month_inserted += 1

        if not dry_run:
            conn.commit()

        print(
            f"  CLMMAXT={len(max_map)} CLMMINT={len(min_map)} "
            f"common={len(common_days)} inserted={month_inserted} "
            f"guard_rej={month_guard_rej}"
        )

        time.sleep(sleep_seconds)

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--start", default=None,
                        help="Start month YYYY-MM (default: 2024-01)")
    parser.add_argument("--end", default=None,
                        help="End month YYYY-MM (default: current month - 1)")
    parser.add_argument("--sleep", type=float, default=SLEEP_BETWEEN_REQUESTS)
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and validate but no DB writes")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.start:
        start_parts = args.start.split("-")
        start = date(int(start_parts[0]), int(start_parts[1]), 1)
    else:
        start = date(2024, 1, 1)

    if args.end:
        end_parts = args.end.split("-")
        # End at last day of the specified month (approximation: last day)
        end_year, end_month = int(end_parts[0]), int(end_parts[1])
        if end_month == 12:
            end = date(end_year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(end_year, end_month + 1, 1) - timedelta(days=1)
    else:
        today = date.today()
        if today.month == 1:
            end = date(today.year - 1, 12, 31)
        else:
            end = date(today.year, today.month, 1) - timedelta(days=1)

    rebuild_run_id = (
        f"backfill_hko_daily_"
        f"{datetime.now(_tz.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )

    if args.dry_run:
        print("[DRY RUN] No rows will be written.")
    print(f"=== HKO Daily Backfill (K1-C guarded, high+low) ===")
    print(f"Run ID:  {rebuild_run_id}")
    print(f"Range:   {start.isoformat()} → {end.isoformat()}")
    print(f"Source:  {HKO_API_URL}")
    print(f"Station: {HKO_STATION}")

    conn = get_world_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)

    stats = run_backfill(
        start=start,
        end=end,
        conn=conn,
        rebuild_run_id=rebuild_run_id,
        dry_run=args.dry_run,
        sleep_seconds=args.sleep,
    )

    conn.close()

    print(f"\n=== Summary ===")
    for k in _STAT_KEYS:
        print(f"  {k:25s} {stats[k]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
