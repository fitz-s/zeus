#!/usr/bin/env python3
# Lifecycle: created=2026-04-22; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Fill observation_instants_v2 WU DST/pilot-boundary gaps through Ogimet fallback rows.
# Reuse: Verify source-role fallback law and writer provenance identity before changing row construction.
# Created: 2026-04-22
# Last reused/audited: 2026-04-25
# Authority basis: critic 2026-04-22 C1 finding; plan v3 extremum-preservation
#                  contract.
"""Fill observation_instants_v2 gaps on WU DST-spring-forward days via Ogimet.

WU Underground's historical API has silent upstream gaps on DST-spring-
forward days: KORD 2024-03-10 returned 2 observations instead of 23,
EGLC 2024-03-31 returned 1 instead of 23. Ogimet mirrors raw NOAA METAR
bulletins and does NOT have these gaps.

This script identifies (city, target_date) pairs whose distinct-UTC-hour
count is below the audit threshold (22) and fills them by fetching
Ogimet METAR for the city's settlement ICAO, writing with
``source=ogimet_metar_<icao>``. The writer's A2 allows this fallback
source because ``tier_resolver.allowed_sources_for_city`` returns a
set including both ``wu_icao_history`` (primary) and the Ogimet mirror.

Usage
-----
::

    # Dry-run: show what would be filled
    python scripts/fill_obs_v2_dst_gaps.py --data-version v1.wu-native.pilot --dry-run

    # Actually fill
    python scripts/fill_obs_v2_dst_gaps.py --data-version v1.wu-native.pilot

Idempotent via INSERT OR REPLACE on UNIQUE(city, source, utc_timestamp).

Scope
-----
Only processes Tier 1 WU cities. Tier 2 Ogimet cities and Tier 3 HK
are intentionally skipped: Tier 2 is already Ogimet-primary (any gap
means Ogimet itself didn't have data — no fallback exists); Tier 3 HK
has a known permanent hourly-history gap per plan v3 L31.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import cities_by_name  # noqa: E402
from src.data.observation_instants_v2_writer import (  # noqa: E402
    InvalidObsV2RowError,
    ObsV2Row,
    insert_rows,
)
from src.data.ogimet_hourly_client import fetch_ogimet_hourly  # noqa: E402
from src.data.tier_resolver import (  # noqa: E402
    Tier,
    tier_for_city,
)
from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = _REPO_ROOT / "state" / "zeus-world.db"
DEFAULT_LOG_PATH = _REPO_ROOT / "state" / "obs_v2_dst_fill_log.jsonl"

# Per-day distinct-UTC-hour threshold: under this counts as a gap.
_MIN_HOURS_PER_DAY = 22
OBS_V2_DST_GAP_FILL_PARSER_VERSION = "obs_v2_dst_gap_fill_ogimet_v2"


def _find_gaps(
    conn: sqlite3.Connection, data_version: str,
    cities_filter: Optional[list[str]] = None,
) -> list[tuple[str, str, int]]:
    """Return [(city, target_date, hours_present)] for Tier 1 + Tier 2
    cities under threshold.

    Tier 1 (WU_ICAO): Ogimet fallback addresses WU upstream gaps.
    Tier 2 (OGIMET_METAR): widened-window re-fetch addresses pilot-boundary
        clips (first/last local-date hours need UTC from adjacent days that
        were outside the initial fetch window). Same Ogimet client, just
        wider date range.
    Tier 3 (HKO_NATIVE): skipped — HK is intentionally gap-accepting per
        plan v3 L31 (accumulator forward-only, no historical).
    """
    rows = conn.execute(
        """
        SELECT city, target_date, COUNT(DISTINCT utc_timestamp) AS h
        FROM observation_instants_v2
        WHERE data_version = ? AND city != 'Hong Kong'
        GROUP BY city, target_date
        HAVING h < ?
        ORDER BY city, target_date
        """,
        (data_version, _MIN_HOURS_PER_DAY),
    ).fetchall()
    out: list[tuple[str, str, int]] = []
    cities_set = set(cities_filter) if cities_filter else None
    for city, td, h in rows:
        if cities_set is not None and city not in cities_set:
            continue
        t = tier_for_city(city)
        if t in (Tier.WU_ICAO, Tier.OGIMET_METAR):
            out.append((city, td, int(h)))
    return out


def _fill_one_date(
    conn: sqlite3.Connection,
    city_name: str,
    target_date: date,
    data_version: str,
    log_path: Path,
    dry_run: bool,
) -> int:
    """Fetch Ogimet for target_date ±1 day and write rows for local date == target_date.

    - Tier 1 (WU_ICAO) cities: write with fallback source tag
      ``ogimet_metar_<icao>`` (fallback per plan v3 allowed-sources).
    - Tier 2 (OGIMET_METAR) cities: write with the SAME primary source tag
      used by the main driver (e.g. ``ogimet_metar_uuww`` for Moscow).
      The widened window closes pilot-boundary clips where the initial
      fetch missed UTC hours belonging to the first/last local date.
    """
    city = cities_by_name[city_name]
    tier = tier_for_city(city_name)
    if tier is Tier.WU_ICAO:
        icao = city.wu_station
        source_tag = f"ogimet_metar_{icao.lower()}"
        tier_label = "WU_ICAO_OGIMET_FALLBACK"
    elif tier is Tier.OGIMET_METAR:
        # Use the primary Ogimet source; station is the same one the
        # main driver already uses.
        _OGIMET_STATION_MAP = {
            "Istanbul": "LTFM",
            "Moscow": "UUWW",
            "Tel Aviv": "LLBG",
        }
        icao = _OGIMET_STATION_MAP[city_name]
        source_tag = f"ogimet_metar_{icao.lower()}"
        tier_label = "OGIMET_METAR_BOUNDARY_FILL"
    else:
        raise RuntimeError(f"Unsupported tier for gap fill: {tier}")

    # Widen window by ±1 day so bucket filter captures all UTC hours
    # belonging to the local target_date.
    window_start = target_date - timedelta(days=1)
    window_end = target_date + timedelta(days=1)

    result = fetch_ogimet_hourly(
        station=icao,
        start_date=window_start,
        end_date=window_end,
        city_name=city_name,
        timezone_name=city.timezone,
        source_tag=source_tag,
        unit=city.settlement_unit,
    )
    written = 0
    ts_now = datetime.now(timezone.utc).isoformat()
    log_entry: dict = {
        "ts": ts_now,
        "city": city_name,
        "target_date": target_date.isoformat(),
        "station": icao,
        "source_tag": source_tag,
        "failed": result.failed,
        "failure_reason": result.failure_reason,
        "raw_metars": result.raw_metar_count,
        "ogimet_buckets": len(result.observations),
        "dry_run": dry_run,
    }
    if result.failed:
        _append_log(log_path, log_entry)
        logger.warning(
            "Ogimet fetch failed for %s %s: %s", city_name, target_date, result.error
        )
        return 0

    # Filter to rows whose target_date matches the gap date (aggregator
    # emits rows for any local date in [window_start, window_end]; we
    # only want to fill the specific gap date).
    rows_for_gap = [
        obs for obs in result.observations if obs.target_date == target_date.isoformat()
    ]
    log_entry["rows_for_gap_date"] = len(rows_for_gap)

    if dry_run:
        _append_log(log_path, log_entry)
        return 0

    imported_at = ts_now
    v2_rows = []
    build_errors = 0
    for obs in rows_for_gap:
        try:
            v2_rows.append(
                ObsV2Row(
                    city=obs.city,
                    target_date=obs.target_date,
                    source=source_tag,  # Ogimet fallback source tag
                    timezone_name=city.timezone,
                    local_hour=obs.local_hour,
                    local_timestamp=obs.local_timestamp,
                    utc_timestamp=obs.utc_timestamp,
                    utc_offset_minutes=obs.utc_offset_minutes,
                    dst_active=obs.dst_active,
                    is_ambiguous_local_hour=obs.is_ambiguous_local_hour,
                    is_missing_local_hour=obs.is_missing_local_hour,
                    time_basis=obs.time_basis,
                    temp_current=None,  # M1: force track awareness
                    running_max=obs.hour_max_temp,
                    running_min=obs.hour_min_temp,
                    temp_unit=obs.temp_unit,
                    station_id=obs.station_id,
                    observation_count=obs.observation_count,
                    imported_at=imported_at,
                    authority="VERIFIED",
                    data_version=data_version,
                    provenance_json=json.dumps(
                        {
                            "tier": tier_label,
                            "station_id": obs.station_id,
                            "hour_max_raw_ts": obs.hour_max_raw_ts,
                            "hour_min_raw_ts": obs.hour_min_raw_ts,
                            "raw_obs_count": obs.observation_count,
                            "aggregation": obs.time_basis,
                            "fallback_reason": (
                                "wu_dst_hole"
                                if tier_label == "WU_ICAO_OGIMET_FALLBACK"
                                else "ogimet_pilot_boundary"
                            ),
                            "payload_hash": _sha256_json(
                                {
                                    "city": obs.city,
                                    "target_date": obs.target_date,
                                    "source": source_tag,
                                    "station_id": obs.station_id,
                                    "utc_timestamp": obs.utc_timestamp,
                                    "hour_max_raw_ts": obs.hour_max_raw_ts,
                                    "hour_min_raw_ts": obs.hour_min_raw_ts,
                                    "observation_count": obs.observation_count,
                                    "fill_target_date": target_date.isoformat(),
                                }
                            ),
                            "payload_scope": "obs_v2_dst_gap_hour_bucket_source_identity",
                            "source_url": (
                                "https://www.ogimet.com/cgi-bin/getmetar"
                                f"?icao={obs.station_id}&targetDate={target_date.isoformat()}"
                            ),
                            "parser_version": OBS_V2_DST_GAP_FILL_PARSER_VERSION,
                        },
                        separators=(",", ":"),
                    ),
                )
            )
        except (InvalidObsV2RowError, ValueError) as exc:
            build_errors += 1
            logger.warning("row build failed %s %s: %s", city_name, obs.utc_timestamp, exc)
    log_entry["build_errors"] = build_errors

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
    return written


def _append_log(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


def _sha256_json(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fill observation_instants_v2 DST-day gaps via Ogimet",
    )
    p.add_argument("--data-version", required=True)
    p.add_argument(
        "--cities", nargs="+", default=None,
        help="Optional city subset filter (default: all gap cities).",
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    p.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not args.db.exists():
        print(f"FATAL: DB not found at {args.db}", file=sys.stderr)
        return 2

    # Skip writer lock in dry-run: no DB writes occur, so holding an exclusive
    # flock would unnecessarily block other bulk writers (PR #86 Copilot fix).
    lock_ctx = db_writer_lock(args.db, WriteClass.BULK) if not args.dry_run else nullcontext()
    with lock_ctx:
        conn = sqlite3.connect(str(args.db))
        try:
            gaps = _find_gaps(conn, args.data_version, cities_filter=args.cities)
            if not gaps:
                print(f"No Tier 1 gaps < {_MIN_HOURS_PER_DAY} hours/day for "
                      f"data_version={args.data_version!r}. Nothing to fill.")
                return 0
            print(
                f"Found {len(gaps)} (city, date) gaps under {_MIN_HOURS_PER_DAY} "
                f"hours/day. Processing via Ogimet{' (DRY-RUN)' if args.dry_run else ''}:"
            )
            total_written = 0
            for city_name, td_str, hours in gaps:
                target_date = date.fromisoformat(td_str)
                written = _fill_one_date(
                    conn, city_name, target_date, args.data_version,
                    args.log, args.dry_run,
                )
                mark = "(dry-run)" if args.dry_run else f"wrote {written}"
                print(f"  {city_name:16s} {td_str} had {hours}h; {mark}")
                total_written += written
            print(f"\nTotal rows written: {total_written}")
            return 0
        finally:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
