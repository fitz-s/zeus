#!/usr/bin/env python3
# Lifecycle: created=2026-04-23; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Ingest HKO accumulator readings and project them into observation_instants_v2.
# Reuse: Keep HKO source identity separate from WU/VHHH and preserve writer provenance identity.
# Created: 2026-04-23
# Last reused/audited: 2026-04-25
# Authority basis: .omc/plans/observation-instants-migration-iter3.md Phase 1
#                  L95 ("HK: no backfill; write accumulator-forward-only
#                  starting now with data_version='v1.wu-native' + authority=
#                  'ICAO_STATION_NATIVE'"); operator directive 2026-04-23
#                  ("daemon-live和polymarket数据/天气数据采集本不应该混为一谈")
#                  separating data-collection from trading daemon.
"""HKO hourly accumulator tick + v2 projection (standalone, cron-safe).

This closes two gaps:

1. **Coupling gap**: ``_accumulate_hko_reading`` currently only runs from
   ``src/main.py`` (the trading daemon). When trading is stopped, HKO
   accumulation stops. Data-collection should not depend on trading.
   This script runs one accumulator tick *without* importing or
   triggering any trading path.
2. **Projection gap**: accumulator rows live in ``hko_hourly_accumulator``
   but are never written to ``observation_instants_v2``. Plan v3 L95
   specified accumulator-forward-only for HK via ``source=
   'hko_hourly_accumulator'`` + ``authority='ICAO_STATION_NATIVE'`` +
   ``data_version='v1.wu-native'`` + provenance ``hourly_history_gap_
   pre_deploy``, but no script does the projection. This one does.

Usage
-----
::

    # Default: fetch current HKO reading AND project accumulator→v2
    python scripts/hko_ingest_tick.py

    # Tick only (no v2 projection)
    python scripts/hko_ingest_tick.py --tick-only

    # Project-only catch-up (no fetch) — for backfilling existing
    # accumulator rows into v2 without hitting HKO endpoint
    python scripts/hko_ingest_tick.py --project-only

Designed for hourly cron invocation. Idempotent: accumulator uses
``ON CONFLICT … DO UPDATE``, v2 projection uses ``UNIQUE(city, source,
utc_timestamp)`` via INSERT OR REPLACE in the writer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Intentionally NOT importing from src.main, src.engine, src.execution —
# this script must not pull in the trading daemon's import graph.
from src.data.daily_obs_append import _accumulate_hko_reading  # noqa: E402
from src.data.observation_instants_v2_writer import (  # noqa: E402
    InvalidObsV2RowError,
    ObsV2Row,
    insert_rows,
)
from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = _REPO_ROOT / "state" / "zeus-world.db"
DEFAULT_LOG_PATH = _REPO_ROOT / "state" / "hko_ingest_log.jsonl"
HKO_ACCUMULATOR_PARSER_VERSION = "hko_hourly_accumulator_projection_v2"

HK_CITY_NAME = "Hong Kong"
HK_TIMEZONE = "Asia/Hong_Kong"
HK_UTC_OFFSET_MINUTES = 480  # UTC+8, no DST


def _append_log(log_path: Path, entry: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


def _accumulator_rows_missing_from_v2(
    conn: sqlite3.Connection, data_version: str
) -> list[tuple[str, str, float, str]]:
    """Return accumulator rows not already projected into v2.

    Returns list of (target_date, hour_utc, temperature, fetched_at) for
    rows where no matching (city='Hong Kong', source='hko_hourly_
    accumulator', utc_timestamp=<hour_utc>) row exists in v2 for the
    given data_version.
    """
    rows = conn.execute(
        """
        SELECT a.target_date, a.hour_utc, a.temperature, a.fetched_at
        FROM hko_hourly_accumulator a
        WHERE NOT EXISTS (
            SELECT 1 FROM observation_instants_v2 v
            WHERE v.city = ?
              AND v.source = 'hko_hourly_accumulator'
              AND v.data_version = ?
              AND v.utc_timestamp = a.hour_utc
        )
        ORDER BY a.hour_utc
        """,
        (HK_CITY_NAME, data_version),
    ).fetchall()
    return [(str(r[0]), str(r[1]), float(r[2]), str(r[3])) for r in rows]


def _build_v2_row(
    target_date: str,
    hour_utc: str,
    temperature_c: float,
    fetched_at: str,
    data_version: str,
    imported_at: str,
) -> ObsV2Row:
    """Build an ObsV2Row for an HKO accumulator reading.

    Schema semantics:
    - source='hko_hourly_accumulator' (A6 pinned for HK)
    - authority='ICAO_STATION_NATIVE' per plan v3 L95
    - data_version='v1.wu-native' to match the corpus family
    - time_basis='hourly_accumulator' (already in _ALLOWED_TIME_BASIS)
    - running_max = running_min = temp_current = temperature (single
      hourly reading; no intra-hour extremum available from rhrread)
    - observation_count=1, station_id='HKO' (Observatory HQ)
    - provenance_json records the accumulator fetch time and the
      hourly_history_gap_pre_deploy note mandated by plan v3 L95.
    """
    # Parse the hour_utc stamp into a UTC datetime for local_timestamp math.
    # hour_utc is formatted as '%Y-%m-%dT%H:00Z'
    if hour_utc.endswith("Z"):
        utc_dt = datetime.fromisoformat(hour_utc.replace("Z", "+00:00"))
    else:
        utc_dt = datetime.fromisoformat(hour_utc)
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    hk_dt = utc_dt.astimezone(ZoneInfo(HK_TIMEZONE))
    local_timestamp = hk_dt.isoformat()
    local_hour = float(hk_dt.hour)

    provenance = json.dumps(
        {
            "tier": "HKO_NATIVE",
            "station_id": "HKO",
            "source_table": "hko_hourly_accumulator",
            "accumulator_fetched_at": fetched_at,
            "note": "hourly_history_gap_pre_deploy",
            "payload_hash": _sha256_json(
                {
                    "target_date": target_date,
                    "hour_utc": hour_utc,
                    "temperature_c": temperature_c,
                    "fetched_at": fetched_at,
                    "source_table": "hko_hourly_accumulator",
                }
            ),
            "payload_scope": "hko_accumulator_row_source_identity",
            "source_file": "hko_hourly_accumulator",
            "parser_version": HKO_ACCUMULATOR_PARSER_VERSION,
        },
        separators=(",", ":"),
    )
    return ObsV2Row(
        city=HK_CITY_NAME,
        target_date=target_date,
        source="hko_hourly_accumulator",
        timezone_name=HK_TIMEZONE,
        local_hour=local_hour,
        local_timestamp=local_timestamp,
        utc_timestamp=hour_utc,
        utc_offset_minutes=HK_UTC_OFFSET_MINUTES,
        dst_active=0,  # HK does not observe DST
        is_ambiguous_local_hour=0,
        is_missing_local_hour=0,
        time_basis="hourly_accumulator",
        temp_current=temperature_c,
        running_max=temperature_c,
        running_min=temperature_c,
        temp_unit="C",
        station_id="HKO",
        observation_count=1,
        imported_at=imported_at,
        authority="ICAO_STATION_NATIVE",
        data_version=data_version,
        provenance_json=provenance,
    )


def project_accumulator_to_v2(
    conn: sqlite3.Connection,
    data_version: str,
    log_path: Path,
    dry_run: bool = False,
) -> dict:
    """Project all accumulator rows missing from v2 into v2.

    Returns dict with counts: {candidates, written, build_errors}.
    Idempotent via UNIQUE(city, source, utc_timestamp) in v2.
    """
    ts_now = datetime.now(timezone.utc).isoformat()
    candidates = _accumulator_rows_missing_from_v2(conn, data_version)
    if not candidates:
        _append_log(log_path, {
            "ts": ts_now, "phase": "project", "candidates": 0,
            "written": 0, "dry_run": dry_run,
        })
        return {"candidates": 0, "written": 0, "build_errors": 0}

    rows: list[ObsV2Row] = []
    build_errors = 0
    for target_date, hour_utc, temp_c, fetched_at in candidates:
        try:
            rows.append(_build_v2_row(
                target_date=target_date,
                hour_utc=hour_utc,
                temperature_c=temp_c,
                fetched_at=fetched_at,
                data_version=data_version,
                imported_at=ts_now,
            ))
        except (InvalidObsV2RowError, ValueError) as exc:
            build_errors += 1
            logger.warning("HKO row build failed %s %s: %s", target_date, hour_utc, exc)

    log_entry = {
        "ts": ts_now,
        "phase": "project",
        "candidates": len(candidates),
        "build_errors": build_errors,
        "dry_run": dry_run,
    }
    if dry_run or not rows:
        log_entry["written"] = 0
        _append_log(log_path, log_entry)
        return {"candidates": len(candidates), "written": 0, "build_errors": build_errors}

    conn.execute("BEGIN")
    try:
        written = insert_rows(conn, rows)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    log_entry["written"] = written
    _append_log(log_path, log_entry)
    return {"candidates": len(candidates), "written": written, "build_errors": build_errors}


def _sha256_json(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def tick_accumulator(
    conn: sqlite3.Connection, log_path: Path, dry_run: bool = False
) -> dict:
    """Run one HKO accumulator fetch + store. Returns {tick_ok: bool}."""
    ts_now = datetime.now(timezone.utc).isoformat()
    if dry_run:
        _append_log(log_path, {"ts": ts_now, "phase": "tick", "dry_run": True})
        return {"tick_ok": True, "dry_run": True}
    ok = _accumulate_hko_reading(conn)
    _append_log(log_path, {
        "ts": ts_now, "phase": "tick", "tick_ok": bool(ok), "dry_run": False,
    })
    return {"tick_ok": bool(ok)}


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Standalone HKO accumulator tick + observation_instants_v2 projection",
    )
    p.add_argument(
        "--data-version", default="v1.wu-native",
        help="data_version tag for v2 rows (default: v1.wu-native)",
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    p.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    p.add_argument(
        "--tick-only", action="store_true",
        help="Only run accumulator fetch; do not project to v2.",
    )
    p.add_argument(
        "--project-only", action="store_true",
        help="Only project existing accumulator rows to v2; no HKO fetch.",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.tick_only and args.project_only:
        print("FATAL: --tick-only and --project-only are mutually exclusive",
              file=sys.stderr)
        return 2
    if not args.db.exists():
        print(f"FATAL: DB not found at {args.db}", file=sys.stderr)
        return 2

    with db_writer_lock(args.db, WriteClass.BULK):
        conn = sqlite3.connect(str(args.db))
        try:
            tick_result = None
            project_result = None
            if not args.project_only:
                tick_result = tick_accumulator(conn, args.log, dry_run=args.dry_run)
                print(f"tick: tick_ok={tick_result.get('tick_ok')} "
                      f"dry_run={args.dry_run}")
            if not args.tick_only:
                project_result = project_accumulator_to_v2(
                    conn, args.data_version, args.log, dry_run=args.dry_run,
                )
                print(f"project: candidates={project_result['candidates']} "
                      f"written={project_result['written']} "
                      f"build_errors={project_result['build_errors']} "
                      f"dry_run={args.dry_run}")
            if tick_result is not None and not tick_result.get("tick_ok"):
                return 1
            return 0
        finally:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
