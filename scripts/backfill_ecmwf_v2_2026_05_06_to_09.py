#!/usr/bin/env python3
# Created: 2026-05-09
# Last reused/audited: 2026-05-09
# Authority basis: Operator directive 2026-05-09, TaskCreate #272.
# v2 of backfill — targets May 6-9 only (May 4-5 confirmed expired 404).
# Uses extract_timeout_seconds=7200 (1.59GB GRIB needs >900s).
# Uses skip_download=True where GRIB already on disk.
# DO NOT modify production code. Run once, then discard.
"""Backfill ECMWF Open Data cycles 2026-05-06T00 through 2026-05-09T00.

May 4T12–May 5T12 confirmed expired (404) — skipped entirely.
Serially respects single-writer doctrine via db_writer_lock inside collect_open_ens_cycle.
"""
from __future__ import annotations

import os
import sys
import logging
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.ecmwf_open_data import (
    collect_open_ens_cycle,
    SOURCE_ID,
    TRACKS,
    _download_output_path,
    _step_hours_signature,
)
from src.state.db import get_world_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("backfill_ecmwf_v2")

# Cycles to attempt — May 4T12 through May 5T12 are 404-expired, skip them.
# May 9T12 not yet released (release = cycle+7h40m).
CYCLES: list[tuple[date, int]] = [
    (date(2026, 5, 6), 0),
    (date(2026, 5, 6), 12),
    (date(2026, 5, 7), 0),
    (date(2026, 5, 7), 12),
    (date(2026, 5, 8), 0),
    (date(2026, 5, 8), 12),
    (date(2026, 5, 9), 0),
]
TRACK_NAMES = ["mx2t6_high", "mn2t6_low"]


def already_succeeded(run_date: date, run_hour: int, track: str) -> bool:
    source_run_id = f"{SOURCE_ID}:{track}:{run_date.isoformat()}T{run_hour:02d}Z"
    conn = get_world_connection()
    try:
        row = conn.execute(
            "SELECT status FROM source_run WHERE source_run_id = ?",
            (source_run_id,),
        ).fetchone()
        return row is not None and row[0] == "SUCCESS"
    finally:
        conn.close()


def grib_already_downloaded(run_date: date, run_hour: int, track: str) -> bool:
    """True if the GRIB file exists and is >1MB (i.e., not a stub/empty file)."""
    cfg = TRACKS[track]
    grib_path = _download_output_path(
        run_date=run_date, run_hour=run_hour, param=cfg["open_data_param"]
    )
    if not grib_path.exists():
        return False
    size = grib_path.stat().st_size
    logger.info("GRIB check: %s — size=%d", grib_path.name, size)
    return size > 1_000_000  # >1MB = real file


def main() -> None:
    total = len(CYCLES) * len(TRACK_NAMES)
    landed = 0
    skipped_existing = 0
    failed: list[str] = []

    logger.info("Backfill v2 starting: %d cycles × %d tracks = %d tasks", len(CYCLES), len(TRACK_NAMES), total)

    for run_date, run_hour in CYCLES:
        for track in TRACK_NAMES:
            label = f"{run_date.isoformat()}T{run_hour:02d}Z:{track}"

            if already_succeeded(run_date, run_hour, track):
                logger.info("SKIP (already SUCCESS): %s", label)
                skipped_existing += 1
                continue

            on_disk = grib_already_downloaded(run_date, run_hour, track)
            if on_disk:
                logger.info("GRIB on disk — using skip_download=True for: %s", label)

            logger.info("Fetching: %s (skip_download=%s)", label, on_disk)
            try:
                result = collect_open_ens_cycle(
                    track=track,
                    run_date=run_date,
                    run_hour=run_hour,
                    skip_download=on_disk,
                    extract_timeout_seconds=7200,   # 2h — 1.59GB GRIB needs >900s
                    download_timeout_seconds=1800,  # 30min for large GRIBs
                )
            except Exception as exc:
                logger.error("EXCEPTION on %s: %r", label, exc)
                failed.append(f"{label}:EXCEPTION:{exc}")
                continue

            status = result.get("status", "unknown")
            inserted = result.get("snapshots_inserted", 0)
            src_run_status = result.get("source_run_status", "?")

            if status == "ok" and src_run_status == "SUCCESS":
                logger.info("OK: %s — inserted=%d", label, inserted)
                landed += 1
            elif status in ("skipped_not_released", "skipped"):
                logger.warning("SKIPPED_NOT_RELEASED: %s", label)
                failed.append(f"{label}:SKIPPED_NOT_RELEASED")
            elif status == "download_failed":
                stages = result.get("stages", [])
                stderr_tail = next(
                    (s.get("stderr_tail", "") for s in stages if "download" in s.get("label", "")),
                    "",
                )
                logger.error("DOWNLOAD_FAILED: %s\nstderr: %s", label, stderr_tail[-600:])
                failed.append(f"{label}:DOWNLOAD_FAILED")
            elif status == "extract_failed":
                logger.error("EXTRACT_FAILED: %s — stages=%s", label, result.get("stages", []))
                failed.append(f"{label}:EXTRACT_FAILED")
            else:
                logger.error("FAILED: %s — status=%s source_run_status=%s inserted=%d",
                             label, status, src_run_status, inserted)
                failed.append(f"{label}:{status.upper()}")

            # Spot-check DB row
            conn = get_world_connection()
            try:
                rows = conn.execute(
                    "SELECT source_cycle_time, status FROM source_run "
                    "WHERE source_id=? AND source_cycle_time >= '2026-05-06' "
                    "ORDER BY source_cycle_time DESC LIMIT 5",
                    (SOURCE_ID,),
                ).fetchall()
                for r in rows:
                    logger.info("  DB: %s | %s", r[0], r[1])
            finally:
                conn.close()

    logger.info(
        "Backfill v2 complete: landed=%d/%d skipped_existing=%d failed=%d",
        landed, total - skipped_existing, skipped_existing, len(failed),
    )
    if failed:
        logger.warning("Failed: %s", failed)

    print(f"\nSUMMARY: landed={landed}/{total - skipped_existing} skipped_existing={skipped_existing} failed={len(failed)}")
    if failed:
        print("Failed:", failed)


if __name__ == "__main__":
    main()
