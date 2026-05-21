#!/usr/bin/env python3
# Created: 2026-05-17
# Lifecycle: created=2026-05-17; last_reviewed=2026-05-20; last_reused=2026-05-20
# Last reused or audited: 2026-05-20
# Purpose: Live rolling-window writer for observation_instants_v2 WU/OGIMET hourly rows.
# Reuse: Run when ingest_main obs_v2 live-tick, hourly payload identity, or obs_v2 writer relationships change.
# Authority basis: docs/operations/task_2026-05-17_post_karachi_remediation/F44_INVESTIGATION.md
#   Root cause H2: observation_instants_v2 had no live-tick writer. This script provides the
#   rolling-window live ingest for WU_ICAO and OGIMET_METAR cities. HKO_NATIVE (Hong Kong)
#   is handled by the existing scripts/hko_ingest_tick.py --project-only path.
#   2026-05-20 live stability: payload_hash includes hourly extrema material values.
"""Live rolling-window ingest for observation_instants_v2.

Fetches the last N days of hourly observations for all non-HKO cities
and writes through the typed v2 writer (A1/A2/A6 enforcement).

Designed for hourly cron or ingest_main.py scheduler invocation.
Idempotent: the writer preserves the first current row for a natural key and
records later different payload hashes in revision history.

Usage
-----
::

    # Default: last 7 days, all cities (WU_ICAO + OGIMET_METAR)
    python scripts/obs_v2_live_tick.py

    # Specify look-back window (useful for catch-up after outage)
    python scripts/obs_v2_live_tick.py --days-back 14

    # Dry run (fetch + validate, no writes)
    python scripts/obs_v2_live_tick.py --dry-run

    # Restrict to specific cities for debugging
    python scripts/obs_v2_live_tick.py --cities Karachi London

Source-tier routing
-------------------
- WU_ICAO cities  → wu_hourly_client.fetch_wu_hourly  (source='wu_icao_history')
- OGIMET_METAR cities → ogimet_hourly_client.fetch_ogimet_hourly (source=city-specific)
- HKO_NATIVE cities → SKIPPED (handled by hko_ingest_tick.py --project-only)

Data version
------------
All rows are stamped with ``data_version='v1.wu-native'`` (same as the
historical backfill, per obs-migration-iter3.md Phase 0 provenance contract).
``authority='VERIFIED'`` for WU and OGIMET (non-HK only).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from dataclasses import dataclass, field
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
    expected_source_for_city,
    tier_for_city,
)
from src.data.wu_hourly_client import HourlyObservation, fetch_wu_hourly  # noqa: E402
from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = _REPO_ROOT / "state" / "zeus-world.db"
DEFAULT_LOG_PATH = _REPO_ROOT / "state" / "obs_v2_live_tick_log.jsonl"
DEFAULT_DAYS_BACK = 7
DATA_VERSION = "v1.wu-native"
LIVE_TICK_PARSER_VERSION = "obs_v2_live_tick_v1"

# WU: single request per city covers up to 30 days comfortably.
WU_WINDOW_DAYS = DEFAULT_DAYS_BACK
# Ogimet: 21s inter-request rate limit; we keep a single window per city.
OGIMET_WINDOW_DAYS = DEFAULT_DAYS_BACK


@dataclass
class TickResult:
    """Summary of one live-tick run."""
    city: str
    tier: str
    rows_written: int = 0
    rows_ready: int = 0
    row_build_errors: int = 0
    skipped_hko: bool = False
    failure_reason: Optional[str] = None

    def __str__(self) -> str:
        if self.skipped_hko:
            return f"{self.city}({self.tier}): skipped (HKO_NATIVE — use hko_ingest_tick)"
        if self.failure_reason:
            return f"{self.city}({self.tier}): FAILED {self.failure_reason}"
        return (
            f"{self.city}({self.tier}): wrote={self.rows_written} "
            f"ready={self.rows_ready} build_errors={self.row_build_errors}"
        )


# ---------------------------------------------------------------------------
# Row construction helpers (mirrors backfill_obs_v2._hourly_obs_to_v2_row)
# ---------------------------------------------------------------------------

def _sha256_json(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _source_url_for_obs(obs: HourlyObservation, *, source_tag: str) -> str:
    """Build a source_url string for A1 provenance (mirrors backfill_obs_v2)."""
    city = cities_by_name[obs.city]
    if source_tag == "wu_icao_history":
        unit_code = "m" if city.settlement_unit == "C" else "e"
        return (
            "https://api.weather.com/v1/location/"
            f"{obs.station_id}:9:{city.country_code}/observations/historical.json"
            f"?units={unit_code}&targetDate={obs.target_date}&apiKey=REDACTED"
        )
    if source_tag.startswith("ogimet_metar_"):
        return (
            "https://www.ogimet.com/cgi-bin/getmetar"
            f"?icao={obs.station_id}&targetDate={obs.target_date}"
        )
    return f"source:{source_tag}:{obs.station_id}:{obs.target_date}"


def _hourly_obs_to_v2_row(
    obs: HourlyObservation,
    *,
    imported_at: str,
    tier_name: str,
) -> ObsV2Row:
    """Build ObsV2Row from HourlyObservation. Mirrors backfill_obs_v2 semantics.

    M1: temp_current=None — forces consumers to use running_max/running_min
    for track-aware queries. Do NOT set to hour_max_temp.
    """
    source_tag = expected_source_for_city(obs.city)
    provenance = {
        "tier": tier_name,
        "station_id": obs.station_id,
        "hour_max_raw_ts": obs.hour_max_raw_ts,
        "hour_min_raw_ts": obs.hour_min_raw_ts,
        "raw_obs_count": obs.observation_count,
        "aggregation": "utc_hour_bucket_extremum",
        "source_url": _source_url_for_obs(obs, source_tag=source_tag),
        "payload_hash": _sha256_json({
            "city": obs.city,
            "target_date": obs.target_date,
            "source": source_tag,
            "station_id": obs.station_id,
            "utc_timestamp": obs.utc_timestamp,
            "hour_max_raw_ts": obs.hour_max_raw_ts,
            "hour_min_raw_ts": obs.hour_min_raw_ts,
            "hour_max_temp": obs.hour_max_temp,
            "hour_min_temp": obs.hour_min_temp,
            "temp_unit": obs.temp_unit,
            "observation_count": obs.observation_count,
        }),
        "payload_scope": "obs_v2_hour_bucket_source_identity",
        "parser_version": LIVE_TICK_PARSER_VERSION,
    }
    return ObsV2Row(
        city=obs.city,
        target_date=obs.target_date,
        source=source_tag,
        timezone_name=cities_by_name[obs.city].timezone,
        local_hour=obs.local_hour,
        local_timestamp=obs.local_timestamp,
        utc_timestamp=obs.utc_timestamp,
        utc_offset_minutes=obs.utc_offset_minutes,
        dst_active=obs.dst_active,
        is_ambiguous_local_hour=obs.is_ambiguous_local_hour,
        is_missing_local_hour=obs.is_missing_local_hour,
        time_basis=obs.time_basis,
        temp_current=None,  # M1: no HIGH-biased default
        running_max=obs.hour_max_temp,
        running_min=obs.hour_min_temp,
        temp_unit=obs.temp_unit,
        station_id=obs.station_id,
        observation_count=obs.observation_count,
        imported_at=imported_at,
        authority="VERIFIED",
        data_version=DATA_VERSION,
        provenance_json=json.dumps(provenance, separators=(",", ":")),
    )


# ---------------------------------------------------------------------------
# Per-city fetch + write
# ---------------------------------------------------------------------------

def _tick_wu_city(
    city_name: str,
    conn,
    *,
    start_date: date,
    end_date: date,
    dry_run: bool,
) -> TickResult:
    city = cities_by_name[city_name]
    result = TickResult(city=city_name, tier="WU_ICAO")
    imported_at = datetime.now(timezone.utc).isoformat()

    fetch = fetch_wu_hourly(
        icao=city.wu_station,
        cc=city.country_code,
        start_date=start_date,
        end_date=end_date,
        unit=city.settlement_unit,
        timezone_name=city.timezone,
        city_name=city_name,
    )
    if fetch.failed:
        result.failure_reason = fetch.failure_reason
        return result

    rows: list[ObsV2Row] = []
    for obs in fetch.observations:
        try:
            rows.append(_hourly_obs_to_v2_row(obs, imported_at=imported_at, tier_name="WU_ICAO"))
        except (InvalidObsV2RowError, ValueError) as exc:
            logger.warning("Row build error %s %s: %s", city_name, obs.utc_timestamp, exc)
            result.row_build_errors += 1

    result.rows_ready = len(rows)
    if not dry_run and rows:
        result.rows_written = insert_rows(conn, rows)
    return result


def _tick_ogimet_city(
    city_name: str,
    conn,
    *,
    start_date: date,
    end_date: date,
    dry_run: bool,
) -> TickResult:
    city = cities_by_name[city_name]
    result = TickResult(city=city_name, tier="OGIMET_METAR")
    imported_at = datetime.now(timezone.utc).isoformat()
    source_tag = expected_source_for_city(city_name)

    # Ogimet needs the ICAO station ID; use the city's wu_station field
    # which stores the ICAO for all tier types (consistent with tier_resolver).
    station = city.wu_station
    fetch = fetch_ogimet_hourly(
        station=station,
        start_date=start_date,
        end_date=end_date,
        city_name=city_name,
        timezone_name=city.timezone,
        source_tag=source_tag,
        unit=city.settlement_unit,
    )
    if fetch.failed:
        result.failure_reason = fetch.failure_reason
        return result

    rows: list[ObsV2Row] = []
    for obs in fetch.observations:
        try:
            rows.append(_hourly_obs_to_v2_row(obs, imported_at=imported_at, tier_name="OGIMET_METAR"))
        except (InvalidObsV2RowError, ValueError) as exc:
            logger.warning("Row build error %s %s: %s", city_name, obs.utc_timestamp, exc)
            result.row_build_errors += 1

    result.rows_ready = len(rows)
    if not dry_run and rows:
        result.rows_written = insert_rows(conn, rows)
    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_live_tick(
    *,
    days_back: int = DEFAULT_DAYS_BACK,
    city_filter: list[str] | None = None,
    dry_run: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
    log_path: Path = DEFAULT_LOG_PATH,
) -> list[TickResult]:
    """Run one live-tick pass over all non-HKO cities.

    Returns one TickResult per city. Caller is responsible for the DB
    connection lifecycle when called from ingest_main.py scheduler.
    """
    import sqlite3

    now_utc = datetime.now(timezone.utc)
    end_date = now_utc.date()
    start_date = end_date - timedelta(days=days_back)

    # Collect cities by tier
    all_names = city_filter if city_filter else list(cities_by_name.keys())
    wu_names = [n for n in all_names if tier_for_city(n) == Tier.WU_ICAO]
    ogimet_names = [n for n in all_names if tier_for_city(n) == Tier.OGIMET_METAR]
    hko_names = [n for n in all_names if tier_for_city(n) == Tier.HKO_NATIVE]

    logger.info(
        "obs_v2_live_tick: start=%s end=%s wu=%d ogimet=%d hko_skipped=%d dry_run=%s",
        start_date, end_date, len(wu_names), len(ogimet_names), len(hko_names), dry_run,
    )

    results: list[TickResult] = []

    # HKO: always skip — handled by hko_ingest_tick.py
    for name in hko_names:
        r = TickResult(city=name, tier="HKO_NATIVE", skipped_hko=True)
        results.append(r)
        logger.debug("skip HKO_NATIVE city %s", name)

    with db_writer_lock(db_path, WriteClass.BULK):
        conn = sqlite3.connect(str(db_path)) if not dry_run else None
        try:
            # WU_ICAO cities
            for city_name in wu_names:
                try:
                    r = _tick_wu_city(city_name, conn, start_date=start_date, end_date=end_date, dry_run=dry_run)
                except Exception as exc:
                    r = TickResult(city=city_name, tier="WU_ICAO", failure_reason=f"unexpected: {exc}")
                    logger.exception("Unexpected error for WU city %s", city_name)
                results.append(r)
                logger.info("%s", r)
                _append_log(log_path, r, start_date=start_date, end_date=end_date)
                time.sleep(0.5)  # modest rate-limit courtesy

            # OGIMET_METAR cities (21s inter-request limit enforced by client module)
            for city_name in ogimet_names:
                try:
                    r = _tick_ogimet_city(city_name, conn, start_date=start_date, end_date=end_date, dry_run=dry_run)
                except Exception as exc:
                    r = TickResult(city=city_name, tier="OGIMET_METAR", failure_reason=f"unexpected: {exc}")
                    logger.exception("Unexpected error for Ogimet city %s", city_name)
                results.append(r)
                logger.info("%s", r)
                _append_log(log_path, r, start_date=start_date, end_date=end_date)
        finally:
            if conn is not None:
                conn.commit()
                conn.close()

    return results


def _append_log(log_path: Path, result: TickResult, *, start_date: date, end_date: date) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "city": result.city,
            "tier": result.tier,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "rows_written": result.rows_written,
            "rows_ready": result.rows_ready,
            "row_build_errors": result.row_build_errors,
            "skipped_hko": result.skipped_hko,
            "failure_reason": result.failure_reason,
        }
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except Exception as exc:
        logger.warning("Failed to write tick log: %s", exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Live rolling-window ingest for observation_instants_v2")
    parser.add_argument("--days-back", type=int, default=DEFAULT_DAYS_BACK,
                        help="Look-back window in days (default: %(default)s)")
    parser.add_argument("--cities", nargs="+", metavar="CITY",
                        help="Restrict to specific city names (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and validate but do not write to DB")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    results = run_live_tick(
        days_back=args.days_back,
        city_filter=args.cities,
        dry_run=args.dry_run,
        db_path=args.db_path,
        log_path=args.log_path,
    )

    failed = [r for r in results if r.failure_reason]
    written = sum(r.rows_written for r in results)
    logger.info(
        "obs_v2_live_tick complete: cities=%d written=%d failed=%d",
        len(results), written, len(failed),
    )
    if failed:
        logger.warning("Failed cities: %s", [r.city for r in failed])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
