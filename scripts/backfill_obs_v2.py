#!/usr/bin/env python3
# Lifecycle: created=2026-04-21; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Build and write observation_instants_v2 hourly backfill rows through the typed writer.
# Reuse: Re-run topology and current source/data state before changing source or provenance semantics.
# Created: 2026-04-21
# Last reused/audited: 2026-04-25
# Authority basis: plan v3 Phase 0 file #6 (.omc/plans/observation-instants-
#                  migration-iter3.md L86-93); step2_phase0_pilot_plan.md.
"""Multi-tier backfill driver for observation_instants_v2.

Per-city tier resolution dispatches to the matching hourly client
(``wu_hourly_client`` or ``ogimet_hourly_client``), attaches plan-v3
provenance (``authority`` + ``data_version`` + ``provenance_json``),
and writes through ``observation_instants_v2_writer.insert_rows`` which
enforces A1/A2/A6 row-by-row.

Default usage (Phase 0 pilot)
-----------------------------
::

    python scripts/backfill_obs_v2.py \\
        --cities Chicago London Tokyo "Sao Paulo" Moscow \\
        --start 2024-01-01 --end 2026-04-21 \\
        --data-version v1.wu-native.pilot

Idempotency
-----------
``INSERT OR REPLACE`` on ``UNIQUE(city, source, utc_timestamp)`` — running
the same command twice is a no-op on every row that already matched.
Re-running with a different ``--data-version`` writes a second parallel
corpus (Phase 0 pilot and Phase 1 fleet coexist by data_version).

Observability
-------------
Per-window HTTP status is appended to ``state/obs_v2_backfill_log.jsonl``.
Each line is a JSON object with city, station, local date range, HTTP
outcome, raw+snapped row counts, and retry count. Tail the file to watch
progress::

    tail -f state/obs_v2_backfill_log.jsonl | jq -c '.'

Rate-limit handling
-------------------
On HTTP 429/403/5xx, the driver retries with exponential backoff
(2s → 4s → 8s, capped at 3 attempts per window). After the cap is
reached the window is logged as failed but the driver continues with
the next city — it does not abort the whole batch. A separate
resume command (``--resume``) can be added later; for now, the
``audit_observation_instants_v2.py`` script identifies gaps via
row-count comparison.

Safety
------
- Respects ``--dry-run`` (no writes, just fetch + snap + log).
- Refuses to run if ``--data-version`` doesn't match ``^v1\\.``
  (the writer enforces this too; the pre-check catches typos at
  the shell level before any HTTP request fires).
- Never writes ``data_version='v0'`` — that sentinel belongs to
  ``zeus_meta`` only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

# Add repo root to sys.path so `python scripts/backfill_obs_v2.py` works
# without an editable install.
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
from src.data.wu_hourly_client import (  # noqa: E402
    HourlyObservation,
    fetch_wu_hourly,
)
from scripts.backfill_completeness import (  # noqa: E402
    add_completeness_args,
    emit_manifest_footer,
    evaluate_completeness,
    resolve_manifest_path,
    write_manifest,
)

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = _REPO_ROOT / "state" / "zeus-world.db"
DEFAULT_LOG_PATH = _REPO_ROOT / "state" / "obs_v2_backfill_log.jsonl"
COMPLETENESS_MANIFEST_PREFIX = "backfill_manifest_obs_v2"

# Exponential-backoff schedule per window on transient failures.
_RETRY_DELAYS_SEC = [2.0, 4.0, 8.0]

# WU prefers ~30-day windows to keep payloads manageable; larger windows
# are often fine but hit transient timeouts more frequently.
WU_WINDOW_DAYS = 30

# Ogimet also chunks at 30 days internally (OGIMET_CHUNK_DAYS) so the
# outer driver uses 60-day windows to minimize request count.
OGIMET_WINDOW_DAYS = 60

_DATA_VERSION_SAFE_RE = re.compile(r"^v1\.[a-z0-9\-\._]+$")
OBS_V2_BACKFILL_PARSER_VERSION = "obs_v2_backfill_hourly_extremum_v2"


@dataclass(frozen=True)
class BackfillStats:
    city: str
    tier: str
    station: str
    rows_written: int
    rows_ready: int
    rows_raw: int
    row_build_errors: int
    windows_attempted: int
    windows_failed: int
    empty_windows: int


# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------


def _write_log(path: Path, entry: dict) -> None:
    """Append one JSON line to the backfill log. Atomic at line grain."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


# ----------------------------------------------------------------------
# Row construction — HourlyObservation → ObsV2Row
# ----------------------------------------------------------------------


def _hourly_obs_to_v2_row(
    obs: HourlyObservation,
    *,
    data_version: str,
    imported_at: str,
    tier_name: str,
) -> ObsV2Row:
    """Build an ObsV2Row from a client's HourlyObservation.

    Extremum-preserving mapping (plan v3 + 2026-04-22 critic correction):
    - ``running_max`` <- ``hour_max_temp``  (THE settlement-correct field)
    - ``running_min`` <- ``hour_min_temp``
    - ``temp_current`` <- ``None``  (see M1 below)
    - ``observation_count`` = raw obs in the bucket
    - ``provenance_json`` carries both raw timestamps + count so audits
      can verify the extremum is a legitimate SPECI.

    M1 footgun fix: temp_current is set to NULL, NOT hour_max_temp. A
    prior revision stamped temp_current = hour_max_temp so legacy
    HIGH-track consumers reading ``SELECT temp_current`` still got a
    settlement-correct value. But LOW-track consumers reading the same
    column would silently receive the per-hour MAXIMUM — a systematic
    high bias that poisons any LOW-track query. NULL forces consumers
    to make track awareness explicit via running_max / running_min, or
    crash loudly at query time.

    Authority is always 'VERIFIED' for Phase 0 pilot (no HK in pilot).
    Phase 1 HK rows will use 'ICAO_STATION_NATIVE' via a separate code
    path (accumulator, not this driver).
    """
    source_tag = expected_source_for_city(obs.city)
    provenance_source = _source_locator_for_hourly_obs(
        obs,
        source_tag=source_tag,
    )
    provenance = {
        "tier": tier_name,
        "station_id": obs.station_id,
        "hour_max_raw_ts": obs.hour_max_raw_ts,
        "hour_min_raw_ts": obs.hour_min_raw_ts,
        "raw_obs_count": obs.observation_count,
        "aggregation": "utc_hour_bucket_extremum",
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
            }
        ),
        "payload_scope": "obs_v2_hour_bucket_source_identity",
        "source_url": provenance_source,
        "parser_version": OBS_V2_BACKFILL_PARSER_VERSION,
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
        temp_current=None,  # M1: force track-awareness (no HIGH-biased default)
        running_max=obs.hour_max_temp,  # explicit per-hour maximum
        running_min=obs.hour_min_temp,  # explicit per-hour minimum
        temp_unit=obs.temp_unit,
        station_id=obs.station_id,
        observation_count=obs.observation_count,
        imported_at=imported_at,
        authority="VERIFIED",
        data_version=data_version,
        provenance_json=json.dumps(provenance, separators=(",", ":")),
    )


def _sha256_json(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _source_locator_for_hourly_obs(obs: HourlyObservation, *, source_tag: str) -> str:
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


# ----------------------------------------------------------------------
# Per-city drivers
# ----------------------------------------------------------------------


def _retry_schedule() -> list[float]:
    """Return the retry delay list. Kept as a function for test override."""
    return list(_RETRY_DELAYS_SEC)


def _backfill_wu_city(
    conn: sqlite3.Connection,
    city_name: str,
    start_date: date,
    end_date: date,
    data_version: str,
    log_path: Path,
    dry_run: bool,
) -> BackfillStats:
    """WU backfill for one city.

    Windows OVERLAP by 1 day (C2 fix): ``cursor_date = window_end`` for the
    next iteration, not ``window_end + 1``. Without overlap the aggregator's
    local-date filter drops UTC hours whose local date belongs to the
    boundary local-date (e.g. UTC 2024-02-29 21:00 in Moscow tz = local
    2024-03-01 00:00 — the filter rejects it under window [2024-01-01,
    2024-02-29] and the NEXT window starting UTC 2024-03-01 00:00 = local
    03:00 doesn't retrieve the missing 00-02 local hours). Overlap + INSERT
    OR REPLACE re-fetches the boundary UTC hours under the next window's
    filter, so every local hour of every local date is captured.
    """
    city = cities_by_name[city_name]
    icao = city.wu_station
    cc = city.country_code
    unit = city.settlement_unit
    tz_name = city.timezone

    cursor_date = start_date
    total_written = 0
    total_ready = 0
    total_raw = 0
    total_build_errors = 0
    windows_attempted = 0
    windows_failed = 0
    empty_windows = 0

    from datetime import timedelta

    while cursor_date <= end_date:
        window_end = min(cursor_date + timedelta(days=WU_WINDOW_DAYS - 1), end_date)
        windows_attempted += 1

        for attempt, delay in enumerate([0.0] + _retry_schedule()):
            if delay > 0:
                time.sleep(delay)
            result = fetch_wu_hourly(
                icao=icao,
                cc=cc,
                start_date=cursor_date,
                end_date=window_end,
                unit=unit,
                timezone_name=tz_name,
                city_name=city_name,
            )
            _write_log(
                log_path,
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "city": city_name,
                    "tier": "WU_ICAO",
                    "station": icao,
                    "window_start": cursor_date.isoformat(),
                    "window_end": window_end.isoformat(),
                    "attempt": attempt,
                    "failed": result.failed,
                    "failure_reason": result.failure_reason,
                    "raw_count": result.raw_observation_count,
                    "snap_count": len(result.observations),
                    "dry_run": dry_run,
                },
            )
            if not result.failed:
                break
            if not result.retryable:
                break
        if result.failed:
            windows_failed += 1
            if window_end >= end_date:
                break
            cursor_date = window_end  # C2 fix: overlap by 1 day (INSERT OR REPLACE dedupes)
            continue

        total_raw += result.raw_observation_count
        imported_at = datetime.now(timezone.utc).isoformat()
        rows = []
        build_errors = 0
        for obs in result.observations:
            try:
                rows.append(
                    _hourly_obs_to_v2_row(
                        obs,
                        data_version=data_version,
                        imported_at=imported_at,
                        tier_name="WU_ICAO",
                    )
                )
            except (InvalidObsV2RowError, ValueError) as exc:
                build_errors += 1
                logger.warning(
                    "row build failed %s %s: %s", city_name, obs.utc_timestamp, exc
                )
        if build_errors:
            _write_log(
                log_path,
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "city": city_name,
                    "event": "row_build_failures",
                    "window_start": cursor_date.isoformat(),
                    "window_end": window_end.isoformat(),
                    "build_errors": build_errors,
                    "rows_salvaged": len(rows),
                    "dry_run": dry_run,
                },
            )
        if not rows and build_errors == 0:
            empty_windows += 1
        total_ready += len(rows)
        total_build_errors += build_errors
        if not dry_run and rows:
            conn.execute("BEGIN")
            try:
                written = insert_rows(conn, rows)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            total_written += written
        if window_end >= end_date:
            break
        cursor_date = window_end  # C2 fix: overlap by 1 day

    return BackfillStats(
        city=city_name,
        tier="WU_ICAO",
        station=icao,
        rows_written=total_written,
        rows_ready=total_ready,
        rows_raw=total_raw,
        row_build_errors=total_build_errors,
        windows_attempted=windows_attempted,
        windows_failed=windows_failed,
        empty_windows=empty_windows,
    )


def _backfill_ogimet_city(
    conn: sqlite3.Connection,
    city_name: str,
    start_date: date,
    end_date: date,
    data_version: str,
    log_path: Path,
    dry_run: bool,
) -> BackfillStats:
    city = cities_by_name[city_name]
    station_map = {
        "Istanbul": "LTFM",
        "Moscow": "UUWW",
        "Tel Aviv": "LLBG",
    }
    station = station_map[city_name]
    unit = city.settlement_unit  # 'C' for all three
    tz_name = city.timezone
    source_tag = expected_source_for_city(city_name)

    from datetime import timedelta

    cursor_date = start_date
    total_written = 0
    total_ready = 0
    total_raw = 0
    total_build_errors = 0
    windows_attempted = 0
    windows_failed = 0
    empty_windows = 0

    while cursor_date <= end_date:
        window_end = min(
            cursor_date + timedelta(days=OGIMET_WINDOW_DAYS - 1), end_date
        )
        windows_attempted += 1

        for attempt, delay in enumerate([0.0] + _retry_schedule()):
            if delay > 0:
                time.sleep(delay)
            result = fetch_ogimet_hourly(
                station=station,
                start_date=cursor_date,
                end_date=window_end,
                city_name=city_name,
                timezone_name=tz_name,
                source_tag=source_tag,
                unit=unit,
            )
            _write_log(
                log_path,
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "city": city_name,
                    "tier": "OGIMET_METAR",
                    "station": station,
                    "window_start": cursor_date.isoformat(),
                    "window_end": window_end.isoformat(),
                    "attempt": attempt,
                    "failed": result.failed,
                    "failure_reason": result.failure_reason,
                    "raw_count": result.raw_metar_count,
                    "snap_count": len(result.observations),
                    "dry_run": dry_run,
                },
            )
            if not result.failed:
                break
            if not result.retryable:
                break
        if result.failed:
            windows_failed += 1
            if window_end >= end_date:
                break
            cursor_date = window_end  # C2 fix: overlap by 1 day
            continue

        total_raw += result.raw_metar_count
        imported_at = datetime.now(timezone.utc).isoformat()
        rows = []
        build_errors = 0
        for obs in result.observations:
            try:
                rows.append(
                    _hourly_obs_to_v2_row(
                        obs,
                        data_version=data_version,
                        imported_at=imported_at,
                        tier_name="OGIMET_METAR",
                    )
                )
            except (InvalidObsV2RowError, ValueError) as exc:
                build_errors += 1
                logger.warning(
                    "row build failed %s %s: %s", city_name, obs.utc_timestamp, exc
                )
        if build_errors:
            _write_log(
                log_path,
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "city": city_name,
                    "event": "row_build_failures",
                    "build_errors": build_errors,
                    "rows_salvaged": len(rows),
                    "dry_run": dry_run,
                },
            )
        if not rows and build_errors == 0:
            empty_windows += 1
        total_ready += len(rows)
        total_build_errors += build_errors
        if not dry_run and rows:
            conn.execute("BEGIN")
            try:
                written = insert_rows(conn, rows)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            total_written += written
        if window_end >= end_date:
            break
        cursor_date = window_end  # C2 fix: overlap by 1 day

    return BackfillStats(
        city=city_name,
        tier="OGIMET_METAR",
        station=station,
        rows_written=total_written,
        rows_ready=total_ready,
        rows_raw=total_raw,
        row_build_errors=total_build_errors,
        windows_attempted=windows_attempted,
        windows_failed=windows_failed,
        empty_windows=empty_windows,
    )


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="observation_instants_v2 multi-tier backfill driver",
    )
    p.add_argument(
        "--cities",
        nargs="+",
        required=True,
        help="City names from config/cities.json (e.g. Chicago London 'Sao Paulo').",
    )
    p.add_argument(
        "--start",
        type=date.fromisoformat,
        required=True,
        help="Inclusive start local date (YYYY-MM-DD).",
    )
    p.add_argument(
        "--end",
        type=date.fromisoformat,
        required=True,
        help="Inclusive end local date (YYYY-MM-DD).",
    )
    p.add_argument(
        "--data-version",
        required=True,
        help="data_version tag for every written row (e.g. 'v1.wu-native.pilot').",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite DB path (default: {DEFAULT_DB_PATH}).",
    )
    p.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help=f"JSONL log path (default: {DEFAULT_LOG_PATH}).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + snap + log, but do not write to the DB.",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Emit INFO-level progress to stderr.",
    )
    add_completeness_args(
        p,
        manifest_prefix=COMPLETENESS_MANIFEST_PREFIX,
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not _DATA_VERSION_SAFE_RE.match(args.data_version):
        print(
            f"FATAL: --data-version={args.data_version!r} must match "
            f"{_DATA_VERSION_SAFE_RE.pattern!r}. Example: 'v1.wu-native.pilot'.",
            file=sys.stderr,
        )
        return 2
    if args.start > args.end:
        print(
            f"FATAL: --start {args.start} is after --end {args.end}.",
            file=sys.stderr,
        )
        return 2
    for name in args.cities:
        if name not in cities_by_name:
            print(
                f"FATAL: city {name!r} not in config/cities.json.",
                file=sys.stderr,
            )
            return 2

    conn = sqlite3.connect(str(args.db))
    try:
        # Ensure zeus_meta + observation_instants_v2 schema is current.
        # Cheap; idempotent; protects against running on a DB that was
        # never migrated.
        from src.state.schema.v2_schema import apply_v2_schema
        apply_v2_schema(conn)

        all_stats: list[BackfillStats] = []
        unsupported_cities: list[str] = []
        for name in args.cities:
            tier = tier_for_city(name)
            if tier is Tier.WU_ICAO:
                stats = _backfill_wu_city(
                    conn, name, args.start, args.end, args.data_version,
                    args.log, args.dry_run,
                )
            elif tier is Tier.OGIMET_METAR:
                stats = _backfill_ogimet_city(
                    conn, name, args.start, args.end, args.data_version,
                    args.log, args.dry_run,
                )
            elif tier is Tier.HKO_NATIVE:
                unsupported_cities.append(name)
                print(
                    f"SKIP: {name} is Tier HKO_NATIVE; backfill not supported "
                    "(plan v3 Phase 0 deliberately excludes HK — accumulator-"
                    "only from deploy forward). Use the hko accumulator path.",
                    file=sys.stderr,
                )
                continue
            else:  # pragma: no cover
                raise RuntimeError(f"Unknown tier {tier!r} for {name!r}")
            all_stats.append(stats)
            logger.info(
                "city=%s tier=%s station=%s rows_written=%d rows_raw=%d "
                "rows_ready=%d windows=%d/%d_failed",
                stats.city, stats.tier, stats.station, stats.rows_written,
                stats.rows_raw, stats.rows_ready, stats.windows_attempted,
                stats.windows_failed,
            )
            print(
                f"{stats.city:16s} tier={stats.tier:13s} station={stats.station:6s} "
                f"rows={stats.rows_written:7d}  ready={stats.rows_ready:7d}  "
                f"raw={stats.rows_raw:7d}  "
                f"windows={stats.windows_attempted}/{stats.windows_failed}_failed"
            )

        # Summary footer
        total_written = sum(s.rows_written for s in all_stats)
        total_ready = sum(s.rows_ready for s in all_stats)
        total_build_errors = sum(s.row_build_errors for s in all_stats)
        total_empty_windows = sum(s.empty_windows for s in all_stats)
        total_failed_windows = sum(s.windows_failed for s in all_stats)
        unsupported_count = len(unsupported_cities)
        hard_blockers = {
            "failed_windows": total_failed_windows,
            "empty_windows": total_empty_windows,
            "unsupported_cities": unsupported_count,
        }
        hard_blocker_count = sum(hard_blockers.values())
        total_attempted = sum(s.windows_attempted for s in all_stats)
        completeness = evaluate_completeness(
            actual_count=total_ready,
            failed_count=total_build_errors,
            attempted_count=total_ready + total_build_errors,
            expected_count=args.expected_count,
            fail_threshold_percent=args.fail_threshold_percent,
        )
        if hard_blocker_count:
            completeness = dict(completeness)
            hard_reasons = [
                name
                for name, count in hard_blockers.items()
                if count > 0
            ]
            completeness["passed"] = False
            completeness["exit_code"] = 1
            completeness["hard_blocker_count"] = hard_blocker_count
            completeness["hard_blocker_reasons"] = hard_reasons
            completeness["reasons"] = sorted(
                set(completeness["reasons"] + ["hard_blocker_present"])
            )
        else:
            completeness["hard_blocker_count"] = 0
            completeness["hard_blocker_reasons"] = []
        run_id = f"obs_v2_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        manifest_path = resolve_manifest_path(
            args.completeness_manifest,
            manifest_prefix=COMPLETENESS_MANIFEST_PREFIX,
            run_id=run_id,
        )
        write_manifest(
            manifest_path,
            script_name="backfill_obs_v2.py",
            run_id=run_id,
            dry_run=args.dry_run,
            inputs={
                "cities": args.cities,
                "start": args.start,
                "end": args.end,
                "data_version": args.data_version,
                "db": args.db,
                "log": args.log,
                "expected_count": args.expected_count,
                "fail_threshold_percent": args.fail_threshold_percent,
            },
            counters={
                "unit_kind": "obs_v2_row",
                "rows_written": total_written,
                "rows_ready": total_ready,
                "rows_raw": sum(s.rows_raw for s in all_stats),
                "windows_attempted": total_attempted,
                "windows_failed": total_failed_windows,
                "row_build_errors": total_build_errors,
                "empty_windows": total_empty_windows,
                "hard_blockers": hard_blockers,
                "hard_blocker_count": hard_blocker_count,
                "unsupported_city_count": unsupported_count,
                "unsupported_cities": unsupported_cities,
                "city_count": len(all_stats),
                "per_city": [s.__dict__ for s in all_stats],
            },
            completeness=completeness,
        )
        print(f"\nTotal rows written: {total_written}")
        print(f"Total rows ready: {total_ready}")
        print(f"Total failed windows: {total_failed_windows}")
        print(f"Total row build errors: {total_build_errors}")
        print(f"Total empty windows: {total_empty_windows}")
        print(f"Unsupported cities: {unsupported_count}")
        emit_manifest_footer(manifest_path, completeness)
        return int(completeness["exit_code"])
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
