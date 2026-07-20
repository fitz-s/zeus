#!/usr/bin/env python3
# Lifecycle: created=2026-04-23; last_reviewed=2026-07-20; last_reused=2026-07-20
# Purpose: Ingest HKO accumulator readings and project them into observation_instants.
# Reuse: Keep HKO source identity separate from WU/VHHH and preserve writer provenance identity.
# Created: 2026-04-23
# Last reused/audited: 2026-07-20
# Authority basis: .omc/plans/observation-instants-migration-iter3.md Phase 1
#                  L95 ("HK: no backfill; write accumulator-forward-only
#                  starting now with data_version='v1.wu-native' + authority=
#                  'ICAO_STATION_NATIVE'"); operator directive 2026-04-23
#                  ("daemon-live和polymarket数据/天气数据采集本不应该混为一谈")
#                  separating data-collection from trading daemon.
"""HKO current-temperature tick + official running-extrema projection.

This closes two gaps:

1. **Coupling gap**: ``_accumulate_hko_reading`` currently only runs from
   ``src/main.py`` (the trading daemon). When trading is stopped, HKO
   accumulation stops. Data-collection should not depend on trading.
   This script runs one accumulator tick *without* importing or
   triggering any trading path.
2. **Semantic gap**: ``rhrread`` is a rounded current temperature, not a
   running daily maximum/minimum.  The executable observation row combines
   that diagnostic current temperature with HKO's official since-midnight
   extrema dataset; legacy rows that equated all three are retired.

Usage
-----
::

    # Default: fetch current HKO reading AND project accumulator→v2
    python scripts/hko_ingest_tick.py

    # Tick only (no v2 projection)
    python scripts/hko_ingest_tick.py --tick-only

    # Project the current official extrema without refreshing rhrread
    python scripts/hko_ingest_tick.py --project-only

Designed for hourly cron invocation. Idempotent: accumulator uses
``ON CONFLICT … DO UPDATE`` and the typed writer replaces the same provider
timestamp without inventing historical extrema.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Intentionally NOT importing from src.main, src.engine, src.execution —
# this script must not pull in the trading daemon's import graph.
from src.data.daily_obs_append import _accumulate_hko_reading  # noqa: E402
from src.data.observation_instants_writer import (  # noqa: E402
    InvalidObsV2RowError,
    ObsV2Row,
    insert_rows,
)
from src.config import STATE_DIR  # noqa: E402
from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = STATE_DIR / "zeus-world.db"
DEFAULT_LOG_PATH = STATE_DIR / "hko_ingest_log.jsonl"
HKO_EXTREMA_URL = (
    "https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/"
    "latest_since_midnight_maxmin.csv"
)
HKO_EXTREMA_BASIS = "hko_since_midnight_extrema_1min_mean"
HKO_EXTREMA_PARSER = "hko_since_midnight_extrema"

HK_CITY_NAME = "Hong Kong"
HK_TIMEZONE = "Asia/Hong_Kong"
HK_UTC_OFFSET_MINUTES = 480  # UTC+8, no DST


@dataclass(frozen=True)
class HkoExtremaSnapshot:
    target_date: str
    observed_at_utc: str
    high_c: float
    low_c: float
    fetched_at_utc: str


def _append_log(log_path: Path, entry: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


def _append_committed_log(log_path: Path, entry: dict) -> None:
    """Report a committed outcome without changing its canonical result."""
    try:
        _append_log(log_path, entry)
    except OSError as exc:
        logger.error(
            "HKO committed outcome log failed path=%s exc=%s: %s",
            log_path,
            type(exc).__name__,
            exc,
        )


def _parse_hko_extrema_csv(
    payload: str,
    *,
    fetched_at_utc: str,
) -> HkoExtremaSnapshot:
    """Parse the official HKO since-midnight extrema for Observatory HQ."""

    reader = csv.DictReader(io.StringIO(payload.lstrip("\ufeff")))
    for row in reader:
        station = str(row.get("Automatic Weather Station") or "").strip()
        if station != "HK Observatory":
            continue
        raw_time = str(row.get("Date time") or "").strip()
        high_raw = row.get(
            "Maximum Air Temperature Since Midnight(degree Celsius)"
        )
        low_raw = row.get(
            "Minimum Air Temperature Since Midnight(degree Celsius)"
        )
        local = datetime.strptime(raw_time, "%Y%m%d%H%M").replace(
            tzinfo=ZoneInfo(HK_TIMEZONE)
        )
        high_c = float(high_raw)
        low_c = float(low_raw)
        if high_c < low_c:
            raise ValueError("HKO since-midnight maximum is below minimum")
        return HkoExtremaSnapshot(
            target_date=local.date().isoformat(),
            observed_at_utc=local.astimezone(timezone.utc).isoformat(),
            high_c=high_c,
            low_c=low_c,
            fetched_at_utc=fetched_at_utc,
        )
    raise ValueError("HKO extrema CSV missing HK Observatory row")


def _fetch_hko_extrema() -> HkoExtremaSnapshot:
    fetched_at = datetime.now(timezone.utc).isoformat()
    response = httpx.get(HKO_EXTREMA_URL, timeout=30.0)
    response.raise_for_status()
    return _parse_hko_extrema_csv(response.text, fetched_at_utc=fetched_at)


def _latest_accumulator_temperature(
    conn: sqlite3.Connection,
    *,
    target_date: str,
) -> tuple[float, str] | None:
    row = conn.execute(
        """
        SELECT temperature, fetched_at
          FROM hko_hourly_accumulator
         WHERE target_date = ?
         ORDER BY datetime(REPLACE(hour_utc, 'Z', '+00:00')) DESC
         LIMIT 1
        """,
        (target_date,),
    ).fetchone()
    if row is None:
        return None
    return float(row[0]), str(row[1])


def _same_extrema_already_materialized(
    conn: sqlite3.Connection,
    snapshot: HkoExtremaSnapshot,
) -> bool:
    row = conn.execute(
        """
        SELECT running_max, running_min
          FROM observation_instants
         WHERE city = ?
           AND source = 'hko_hourly_accumulator'
           AND utc_timestamp = ?
           AND COALESCE(causality_status, 'OK') = 'OK'
           AND CASE
                WHEN NOT json_valid(COALESCE(provenance_json, '')) THEN 0
                WHEN json_extract(
                     provenance_json, '$.observation_basis'
                ) <> ? THEN 0
                WHEN COALESCE(json_type(
                     provenance_json, '$.official_running_high_c'
                ), '') NOT IN ('integer', 'real') THEN 0
                WHEN COALESCE(json_type(
                     provenance_json, '$.official_running_low_c'
                ), '') NOT IN ('integer', 'real') THEN 0
                ELSE 1
           END = 1
         ORDER BY id DESC
         LIMIT 1
        """,
        (HK_CITY_NAME, snapshot.observed_at_utc, HKO_EXTREMA_BASIS),
    ).fetchone()
    if row is None:
        return False
    return (
        abs(float(row[0]) - snapshot.high_c) <= 1e-9
        and abs(float(row[1]) - snapshot.low_c) <= 1e-9
    )


def _build_hko_extrema_row(
    snapshot: HkoExtremaSnapshot,
    *,
    temperature_c: float,
    accumulator_fetched_at: str,
    data_version: str,
    imported_at: str,
) -> ObsV2Row:
    """Build one HKO row with source-typed official and diagnostic fields.

    Schema semantics:
    - source='hko_hourly_accumulator' (A6 pinned for HK)
    - authority='ICAO_STATION_NATIVE' per plan v3 L95
    - data_version='v1.wu-native' to match the corpus family
    - temp_current comes from rhrread — the diagnostic current reading
    - running_max/running_min are HKO's official since-midnight 1-minute-mean
      extrema only
    - observation_count=1, station_id='HKO' (Observatory HQ)
    - provenance_json makes the two source roles explicit.

    ``temp_current`` is a different observation statistic from HKO's official
    since-midnight 1-minute-mean extrema. It remains diagnostic and must never
    create an absorbing settlement fact. Combining the two fabricated a 30.0C
    running maximum on 2026-07-20 while the official HKO maximum remained
    29.7C, which incorrectly collapsed the 29C NO belief to q=1.
    """
    utc_dt = datetime.fromisoformat(snapshot.observed_at_utc.replace("Z", "+00:00"))
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    hk_dt = utc_dt.astimezone(ZoneInfo(HK_TIMEZONE))
    local_timestamp = hk_dt.isoformat()
    local_hour = float(hk_dt.hour) + float(hk_dt.minute) / 60.0

    provenance = json.dumps(
        {
            "tier": "HKO_NATIVE",
            "station_id": "HKO",
            "source_table": "hko_hourly_accumulator",
            "source_url": HKO_EXTREMA_URL,
            "observation_basis": HKO_EXTREMA_BASIS,
            "official_running_high_c": snapshot.high_c,
            "official_running_low_c": snapshot.low_c,
            "diagnostic_current_temperature_c": temperature_c,
            "accumulator_fetched_at": accumulator_fetched_at,
            "extrema_fetched_at": snapshot.fetched_at_utc,
            "payload_hash": _sha256_json(
                {
                    "target_date": snapshot.target_date,
                    "observed_at_utc": snapshot.observed_at_utc,
                    "temperature_c": temperature_c,
                    "running_max_c": snapshot.high_c,
                    "running_min_c": snapshot.low_c,
                    "observation_basis": HKO_EXTREMA_BASIS,
                }
            ),
            "payload_scope": "hko_current_and_since_midnight_extrema",
            "source_file": HKO_EXTREMA_URL,
            "parser_version": HKO_EXTREMA_PARSER,
        },
        separators=(",", ":"),
    )
    return ObsV2Row(
        city=HK_CITY_NAME,
        target_date=snapshot.target_date,
        source="hko_hourly_accumulator",
        timezone_name=HK_TIMEZONE,
        local_hour=local_hour,
        local_timestamp=local_timestamp,
        utc_timestamp=snapshot.observed_at_utc,
        utc_offset_minutes=HK_UTC_OFFSET_MINUTES,
        dst_active=0,  # HK does not observe DST
        is_ambiguous_local_hour=0,
        is_missing_local_hour=0,
        time_basis="station_local",
        temp_current=temperature_c,
        running_max=snapshot.high_c,
        running_min=snapshot.low_c,
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
    """Write one current HKO extrema fact and retire legacy pseudo-extrema."""
    ts_now = datetime.now(timezone.utc).isoformat()
    try:
        snapshot = _fetch_hko_extrema()
        current = _latest_accumulator_temperature(
            conn,
            target_date=snapshot.target_date,
        )
        if current is None:
            raise ValueError("HKO current-temperature accumulator row missing")
        temp_c, accumulator_fetched_at = current
        row = _build_hko_extrema_row(
            snapshot,
            temperature_c=temp_c,
            accumulator_fetched_at=accumulator_fetched_at,
            data_version=data_version,
            imported_at=ts_now,
        )
    except (httpx.HTTPError, InvalidObsV2RowError, ValueError) as exc:
        logger.warning("HKO extrema build failed: %s", exc)
        _append_log(log_path, {
            "ts": ts_now, "phase": "project", "candidates": 0,
            "written": 0, "build_errors": 1, "dry_run": dry_run,
            "error": f"{type(exc).__name__}:{exc}",
        })
        return {"candidates": 0, "written": 0, "build_errors": 1, "retired": 0}

    log_entry = {
        "ts": ts_now,
        "phase": "project",
        "candidates": 1,
        "build_errors": 0,
        "dry_run": dry_run,
    }
    if dry_run:
        log_entry["written"] = 0
        log_entry["retired"] = 0
        _append_log(log_path, log_entry)
        return {"candidates": 1, "written": 0, "build_errors": 0, "retired": 0}

    # ``project_accumulator_to_v2`` is also called by ingest_main on the
    # connection used for the HKO tick.  That caller can already own a
    # transaction (for example after writing the observation-print ledger), so
    # an unconditional BEGIN raises "cannot start a transaction within a
    # transaction".  Use a savepoint in that case: it keeps this projection
    # atomic without committing or rolling back the caller's work.
    caller_owns_transaction = conn.in_transaction
    savepoint = f"sp_hko_project_{id(conn)}"
    if caller_owns_transaction:
        conn.execute(f"SAVEPOINT {savepoint}")
    else:
        conn.execute("BEGIN")
    try:
        retired = conn.execute(
            """
            UPDATE observation_instants
               SET causality_status = 'REQUIRES_SOURCE_REAUDIT'
             WHERE city = ?
               AND target_date = ?
               AND source = 'hko_hourly_accumulator'
               AND COALESCE(causality_status, 'OK') = 'OK'
               AND CASE
                    WHEN NOT json_valid(COALESCE(provenance_json, '')) THEN 1
                    WHEN COALESCE(
                         json_extract(provenance_json, '$.observation_basis'), ''
                    ) <> ? THEN 1
                    WHEN COALESCE(json_type(
                         provenance_json, '$.official_running_high_c'
                    ), '') NOT IN ('integer', 'real') THEN 1
                    WHEN COALESCE(json_type(
                         provenance_json, '$.official_running_low_c'
                    ), '') NOT IN ('integer', 'real') THEN 1
                    ELSE 0
               END = 1
            """,
            (HK_CITY_NAME, snapshot.target_date, HKO_EXTREMA_BASIS),
        ).rowcount
        written = (
            0
            if _same_extrema_already_materialized(conn, snapshot)
            else insert_rows(conn, [row])
        )
        if caller_owns_transaction:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        else:
            conn.execute("COMMIT")
    except Exception as body_exc:
        if caller_owns_transaction:
            try:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            except sqlite3.Error as cleanup_exc:
                raise RuntimeError(
                    "HKO projection failed and savepoint rollback failed; "
                    "caller must roll back the outer transaction "
                    f"(body={type(body_exc).__name__}: {body_exc})"
                ) from cleanup_exc
            try:
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            except sqlite3.Error as cleanup_exc:
                raise RuntimeError(
                    "HKO projection failed and savepoint release failed; "
                    "caller must roll back the outer transaction "
                    f"(body={type(body_exc).__name__}: {body_exc})"
                ) from cleanup_exc
        else:
            if conn.in_transaction:
                conn.rollback()
        raise
    log_entry["written"] = written
    log_entry["retired"] = int(retired)
    # A savepoint release is not a durable commit.  A caller-owned transaction
    # must report success only after its owner commits.
    if not caller_owns_transaction:
        _append_committed_log(log_path, log_entry)
    return {
        "candidates": 1,
        "written": written,
        "build_errors": 0,
        "retired": int(retired),
    }


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
    if conn.in_transaction:
        raise RuntimeError("HKO tick requires a transaction-free connection")
    ok = _accumulate_hko_reading(conn)
    # The accumulator commits its primary reading before appending the optional
    # observation-print ledger.  Finish that residual transaction here so both
    # runtime callers receive a durable tick and a clean transaction boundary.
    try:
        if conn.in_transaction:
            conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    _append_committed_log(log_path, {
        "ts": ts_now, "phase": "tick", "tick_ok": bool(ok), "dry_run": False,
    })
    return {"tick_ok": bool(ok)}


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Standalone HKO accumulator tick + observation_instants projection",
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
                      f"retired={project_result.get('retired', 0)} "
                      f"dry_run={args.dry_run}")
            if tick_result is not None and not tick_result.get("tick_ok"):
                return 1
            return 0
        finally:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
