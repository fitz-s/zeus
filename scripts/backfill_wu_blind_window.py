#!/usr/bin/env python3
# Lifecycle: created=2026-07-16; last_reviewed=2026-07-16; last_reused=2026-07-16
# Purpose: Re-fetch WU observations whose widening audit trail could not persist during the fixed blind window.
# Reuse: Run dry first; inspect the manifest, exact date/city scope, and rollup before any explicit --apply.
# Authority basis: 5997ee49d (observation_revisions CHECK rebuild) — recovery
#                  half of the blind-window repair; quantify_observation_
#                  revisions_blind_window_exposure.py is the sibling read-only
#                  sizing pass.
"""One-shot WU re-fetch recovery for the 2026-05-28..2026-07-16 blind window.

Between the 2026-05-29 observation_instants v1/v2 consolidation and
f1d135901 (2026-07-16), a WU backfill widening a bucket's true running_max/
running_min was quarantined into an observation_revisions INSERT that the
(pre-consolidation) CHECK constraint silently dropped via INSERT OR IGNORE —
no audit row exists to replay. The data source is still upstream: WU's
historical.json endpoint. Re-fetching with insert_rows' widening branch
(f1d135901, live) and the now-fixed CHECK (5997ee49d) means a re-fetch will
correctly fold in any wider bucket extremum discovered since the blind
window's first-seen capture, with a full audit trail this time.

Built from the SAME pieces scripts.obs_live_tick._tick_wu_city calls
internally (fetch_wu_hourly -> _hourly_obs_to_v2_row -> _write_rows,
including the observation_prints ledger double-write) rather than calling
that wrapper directly — a wrapper call would fetch a second time to also
preview the widening impact, which is the one thing a fixed historical range
adds that the rolling live-tick window doesn't need. Everything else is the
identical live pipeline; the only new code here is the date chunking and the
before/after comparison.

Chunked at <=30 days per WU request per scripts/obs_live_tick.py's documented
"single request per city covers up to 30 days comfortably" bound. 0.5s
courtesy sleep between requests, matching obs_live_tick.py /
backfill_wu_daily_all.py convention.

--dry-run (default): fetch + validate + compare against the current main row,
no writes — the report shows exactly what --apply would change. --apply:
writes through the SAME insert_rows path (BULK writer lock, per-chunk
short-lived connection, exactly as the live cron does).
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import cities_by_name
from src.data.observation_instants_writer import InvalidObsV2RowError
from src.data.tier_resolver import Tier, expected_source_for_city, tier_for_city
from src.data.wu_hourly_client import fetch_wu_hourly

from scripts.obs_live_tick import DEFAULT_DB_PATH, _hourly_obs_to_v2_row, _write_rows

logger = logging.getLogger(__name__)

BLIND_WINDOW_START = date(2026, 5, 28)
BLIND_WINDOW_END = date(2026, 7, 16)
WU_CHUNK_DAYS = 30
DEFAULT_SLEEP_SECONDS = 0.5


def _chunk_date_range(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return chunks


def _fetch_chunk(city_name: str, *, start: date, end: date, conn_ro: sqlite3.Connection | None) -> dict:
    """Fetch one city/chunk once; build rows + compare against the current row.

    Returns a report dict plus the built ``ObsV2Row`` list (under ``_rows``
    and matching ``_prints``, both private — stripped before JSON printing)
    for the caller to write when --apply is set, so this is the ONLY fetch
    per chunk regardless of dry-run vs apply.
    """
    city = cities_by_name[city_name]
    imported_at = datetime.now(timezone.utc).isoformat()
    fetch = fetch_wu_hourly(
        icao=city.wu_station, cc=city.country_code, start_date=start, end_date=end,
        unit=city.settlement_unit, timezone_name=city.timezone, city_name=city_name,
    )
    if fetch.failed:
        return {
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "failure_reason": fetch.failure_reason, "rows_ready": 0, "row_build_errors": 0,
            "would_widen": 0, "missing_locally": 0, "_rows": [], "_prints": [],
        }

    source = expected_source_for_city(city_name)
    rows = []
    prints = []
    row_build_errors = 0
    would_widen = 0
    missing_locally = 0
    for obs in fetch.observations:
        try:
            rows.append(_hourly_obs_to_v2_row(obs, imported_at=imported_at, tier_name="WU_ICAO"))
        except (InvalidObsV2RowError, ValueError) as exc:
            logger.warning("Row build error %s %s: %s", city_name, obs.utc_timestamp, exc)
            row_build_errors += 1
            continue
        prints.append(dict(
            city=city_name, station_id=obs.station_id, source_channel="wu_icao_history",
            publish_ts_utc=obs.hour_max_raw_ts, value_native=obs.hour_max_temp,
            unit=obs.temp_unit, fetched_at_utc=imported_at, raw_report=None,
        ))
        prints.append(dict(
            city=city_name, station_id=obs.station_id, source_channel="wu_icao_history",
            publish_ts_utc=obs.hour_min_raw_ts, value_native=obs.hour_min_temp,
            unit=obs.temp_unit, fetched_at_utc=imported_at, raw_report=None,
        ))
        if conn_ro is not None:
            row = conn_ro.execute(
                "SELECT running_max, running_min FROM observation_instants WHERE city=? AND source=? AND utc_timestamp=?",
                (city_name, source, obs.utc_timestamp),
            ).fetchone()
            if row is None or row[0] is None or row[1] is None:
                missing_locally += 1
            elif obs.hour_max_temp > row[0] or obs.hour_min_temp < row[1]:
                would_widen += 1

    return {
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "failure_reason": None, "rows_ready": len(rows), "row_build_errors": row_build_errors,
        "would_widen": would_widen, "missing_locally": missing_locally,
        "_rows": rows, "_prints": prints,
    }


def run_wu_blind_window_backfill(
    *,
    start: date = BLIND_WINDOW_START,
    end: date = BLIND_WINDOW_END,
    city_filter: list[str] | None = None,
    apply: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
) -> list[dict]:
    all_names = city_filter if city_filter else list(cities_by_name.keys())
    wu_names = [n for n in all_names if tier_for_city(n) == Tier.WU_ICAO]
    chunks = _chunk_date_range(start, end, WU_CHUNK_DAYS)

    conn_ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)
    results: list[dict] = []
    try:
        for city_name in wu_names:
            city_summary: dict = {"city": city_name, "chunks": []}
            for chunk_start, chunk_end in chunks:
                chunk = _fetch_chunk(city_name, start=chunk_start, end=chunk_end, conn_ro=conn_ro)
                rows = chunk.pop("_rows")
                prints = chunk.pop("_prints")
                if apply and rows:
                    chunk["rows_written"] = _write_rows(db_path, rows, prints)
                else:
                    chunk["rows_written"] = 0
                city_summary["chunks"].append(chunk)
                time.sleep(sleep_seconds)
            results.append(city_summary)
    finally:
        conn_ro.close()
    return results


def _rollup(results: list[dict]) -> dict:
    total_rows_ready = total_rows_written = total_would_widen = total_missing_locally = 0
    failed_chunks = []
    for city_summary in results:
        for chunk in city_summary["chunks"]:
            total_rows_ready += chunk["rows_ready"]
            total_rows_written += chunk["rows_written"]
            total_would_widen += chunk["would_widen"]
            total_missing_locally += chunk["missing_locally"]
            if chunk["failure_reason"]:
                failed_chunks.append(
                    {"city": city_summary["city"], "start_date": chunk["start_date"], "reason": chunk["failure_reason"]}
                )
    return {
        "cities": len(results),
        "rows_ready": total_rows_ready,
        "rows_written": total_rows_written,
        "would_widen": total_would_widen,
        "missing_locally": total_missing_locally,
        "failed_chunks": failed_chunks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--start", type=date.fromisoformat, default=BLIND_WINDOW_START)
    parser.add_argument("--end", type=date.fromisoformat, default=BLIND_WINDOW_END)
    parser.add_argument("--cities", nargs="+", metavar="CITY")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SECONDS)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    results = run_wu_blind_window_backfill(
        start=args.start, end=args.end, city_filter=args.cities,
        apply=args.apply, db_path=args.db, sleep_seconds=args.sleep,
    )
    print(json.dumps({"dry_run": not args.apply, "cities": results, "rollup": _rollup(results)}, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
