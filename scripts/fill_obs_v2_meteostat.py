#!/usr/bin/env python3
# Lifecycle: created=2026-04-22; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Fill sparse WU observation_instants_v2 gaps from Meteostat bulk hourly archives.
# Reuse: Verify fallback source allowlist and provenance identity before changing row construction.
# Created: 2026-04-22
# Last reused/audited: 2026-04-25
# Authority basis: subagent research 2026-04-22 → bulk CSV archive for
#                  sparse-WU ICAO stations. Closes the 12-hour Ogimet
#                  serialization gap for 5 sparse cities.
"""Bulk fill of observation_instants_v2 via Meteostat CDN archives.

For cities whose WU primary + Ogimet fallback are both slow/sparse
(empirically: Shenzhen ZGSZ, Lagos DNMM, Jakarta WIHH, Lucknow VILK,
Panama City MPMG), pull Meteostat's bulk CSV (one .csv.gz per station)
and write rows with source tag ``meteostat_bulk_<icao>``.

Usage
-----
::

    # Fill the 5 Meteostat-supported sparse cities in parallel:
    python scripts/fill_obs_v2_meteostat.py \\
        --data-version v1.wu-native \\
        --cities Shenzhen Lagos Jakarta Lucknow "Panama City"

    # With --dry-run for verification:
    python scripts/fill_obs_v2_meteostat.py \\
        --data-version v1.wu-native \\
        --cities Shenzhen --dry-run --verbose

Speed: 5 stations × ~15k rows each × single file download + parse =
~1 minute wall-clock total. Replaces ~12 hours of Ogimet serial fill.

Idempotent via ``INSERT OR REPLACE`` on
``UNIQUE(city, source, utc_timestamp)``. Re-running writes the same rows.

Residual gap after Meteostat
----------------------------
Meteostat bulk archives lag real-time by weeks to months. For the tail
window (roughly ``max(meteostat_last_date, Meteostat_station_cutoff)``
through ``end_date``), Ogimet remains the fill path — but the residual
volume is typically <10% of the original problem, so the existing
``fill_obs_v2_dst_gaps.py`` script completes in ~1 hour instead of 12.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from contextlib import nullcontext
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import cities_by_name  # noqa: E402
from src.data.meteostat_bulk_client import (  # noqa: E402
    ICAO_TO_WMO,
    METEOSTAT_BULK_URL,
    fetch_meteostat_bulk,
    meteostat_source_tag,
)
from src.data.observation_instants_v2_writer import (  # noqa: E402
    InvalidObsV2RowError,
    ObsV2Row,
    insert_rows,
)
from src.data.tier_resolver import Tier, tier_for_city  # noqa: E402
from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = _REPO_ROOT / "state" / "zeus-world.db"
DEFAULT_LOG_PATH = _REPO_ROOT / "state" / "obs_v2_meteostat_fill_log.jsonl"
OBS_V2_METEOSTAT_FILL_PARSER_VERSION = "obs_v2_meteostat_bulk_fill_v2"


def _append_log(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


def _fill_one_city(
    conn: sqlite3.Connection,
    city_name: str,
    start_date: date,
    end_date: date,
    data_version: str,
    log_path: Path,
    dry_run: bool,
) -> tuple[int, int, Optional[str]]:
    """Return (rows_written, raw_rows_fetched, failure_reason_or_None)."""
    city = cities_by_name[city_name]
    if tier_for_city(city_name) is not Tier.WU_ICAO:
        return 0, 0, "not_tier1_wu"
    icao = city.wu_station
    if icao not in ICAO_TO_WMO:
        return 0, 0, f"icao_{icao}_not_in_meteostat_map"

    ts_now = datetime.now(timezone.utc).isoformat()
    result = fetch_meteostat_bulk(
        icao=icao,
        start_date=start_date,
        end_date=end_date,
        city_name=city_name,
        timezone_name=city.timezone,
        unit=city.settlement_unit,
    )
    log_entry: dict = {
        "ts": ts_now,
        "city": city_name,
        "icao": icao,
        "wmo": ICAO_TO_WMO[icao],
        "failed": result.failed,
        "failure_reason": result.failure_reason,
        "raw_rows": result.raw_row_count,
        "in_range_rows": len(result.observations),
        "dry_run": dry_run,
    }
    if result.failed:
        _append_log(log_path, log_entry)
        return 0, result.raw_row_count, result.failure_reason

    if dry_run:
        _append_log(log_path, log_entry)
        return 0, result.raw_row_count, None

    source_tag = meteostat_source_tag(icao)
    v2_rows = []
    build_errors = 0
    for obs in result.observations:
        try:
            v2_rows.append(
                ObsV2Row(
                    city=obs.city,
                    target_date=obs.target_date,
                    source=source_tag,
                    timezone_name=city.timezone,
                    local_hour=obs.local_hour,
                    local_timestamp=obs.local_timestamp,
                    utc_timestamp=obs.utc_timestamp,
                    utc_offset_minutes=obs.utc_offset_minutes,
                    dst_active=obs.dst_active,
                    is_ambiguous_local_hour=obs.is_ambiguous_local_hour,
                    is_missing_local_hour=obs.is_missing_local_hour,
                    time_basis=obs.time_basis,
                    temp_current=None,  # M1: force track-aware reads
                    running_max=obs.hour_max_temp,
                    running_min=obs.hour_min_temp,
                    temp_unit=obs.temp_unit,
                    station_id=obs.station_id,
                    observation_count=obs.observation_count,
                    imported_at=ts_now,
                    authority="VERIFIED",
                    data_version=data_version,
                    provenance_json=json.dumps(
                        {
                            "tier": "METEOSTAT_BULK_FALLBACK",
                            "station_id": obs.station_id,
                            "wmo_id": ICAO_TO_WMO[icao],
                            "hour_max_raw_ts": obs.hour_max_raw_ts,
                            "hour_min_raw_ts": obs.hour_min_raw_ts,
                            "raw_obs_count": obs.observation_count,
                            "aggregation": "meteostat_hourly_single_value",
                            "fallback_reason": "sparse_wu_plus_slow_ogimet",
                            "payload_hash": _sha256_json(
                                {
                                    "city": obs.city,
                                    "target_date": obs.target_date,
                                    "source": source_tag,
                                    "station_id": obs.station_id,
                                    "wmo_id": ICAO_TO_WMO[icao],
                                    "utc_timestamp": obs.utc_timestamp,
                                    "hour_max_raw_ts": obs.hour_max_raw_ts,
                                    "hour_min_raw_ts": obs.hour_min_raw_ts,
                                    "observation_count": obs.observation_count,
                                }
                            ),
                            "payload_scope": "obs_v2_meteostat_hour_bucket_source_identity",
                            "source_url": METEOSTAT_BULK_URL.format(
                                wmo=ICAO_TO_WMO[icao]
                            ),
                            "parser_version": OBS_V2_METEOSTAT_FILL_PARSER_VERSION,
                        },
                        separators=(",", ":"),
                    ),
                )
            )
        except (InvalidObsV2RowError, ValueError) as exc:
            build_errors += 1
            logger.warning("row build failed %s %s: %s", city_name, obs.utc_timestamp, exc)
    log_entry["build_errors"] = build_errors

    written = 0
    if v2_rows:
        conn.execute("BEGIN")
        try:
            written = insert_rows(conn, v2_rows)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    log_entry["rows_written"] = written
    _append_log(log_path, log_entry)
    return written, result.raw_row_count, None


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Bulk fill observation_instants_v2 via Meteostat bulk CSVs",
    )
    p.add_argument("--data-version", required=True)
    p.add_argument(
        "--cities", nargs="+", required=True,
        help="City names (cities.json keys). Only Tier 1 WU cities with a WMO mapping are processed.",
    )
    p.add_argument(
        "--start", type=date.fromisoformat, default=date(2024, 1, 1),
        help="Inclusive local start date (default: 2024-01-01).",
    )
    p.add_argument(
        "--end", type=date.fromisoformat, default=date.today(),
        help="Inclusive local end date (default: today).",
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    p.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def _sha256_json(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not args.db.exists():
        print(f"FATAL: DB not found at {args.db}", file=sys.stderr)
        return 2

    for name in args.cities:
        if name not in cities_by_name:
            print(f"FATAL: city {name!r} not in config/cities.json", file=sys.stderr)
            return 2

    # Skip writer lock in dry-run: no DB writes occur, so holding an exclusive
    # flock would unnecessarily block other bulk writers (PR #86 Copilot fix).
    lock_ctx = db_writer_lock(args.db, WriteClass.BULK) if not args.dry_run else nullcontext()
    with lock_ctx:
        conn = sqlite3.connect(str(args.db))
        try:
            total_written = 0
            total_raw = 0
            failures: list[tuple[str, str]] = []
            for name in args.cities:
                written, raw, failure = _fill_one_city(
                    conn, name, args.start, args.end, args.data_version,
                    args.log, args.dry_run,
                )
                total_written += written
                total_raw += raw
                mark = (
                    f"(dry-run raw={raw})" if args.dry_run
                    else f"wrote {written} / raw {raw}"
                )
                if failure:
                    failures.append((name, failure))
                    print(f"  {name:20s} FAILED: {failure}")
                else:
                    print(f"  {name:20s} {mark}")
            print(f"\nTotal written: {total_written}")
            print(f"Total raw fetched: {total_raw}")
            if failures:
                print(f"Failures: {failures}")
                return 1
            return 0
        finally:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
