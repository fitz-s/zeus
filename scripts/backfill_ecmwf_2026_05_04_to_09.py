#!/usr/bin/env python3
# Created: 2026-05-09
# Last reused/audited: 2026-05-09
# Authority basis: Operator directive 2026-05-09, TaskCreate #272.
# One-off backfill: ECMWF Open Data cycles 2026-05-04T12 through 2026-05-09T00
# that the daemon missed due to the NAME_MAX bug (fixed PR #105).
# DO NOT modify production code. Run once, then discard.
"""Backfill missing ECMWF Open Data cycles after NAME_MAX regression (PR #94).

Cycles to fill: 2026-05-04T12 through 2026-05-09T00 (both tracks).
Skips any cycle where source_run already has a SUCCESS row (idempotent).
Runs serially to respect the single-writer doctrine.
"""
from __future__ import annotations

import sys
import logging
from datetime import date, datetime, timezone

# Ensure project root on path
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.ecmwf_open_data import collect_open_ens_cycle, SOURCE_ID
from src.state.db import get_world_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("backfill_ecmwf")

# Cycles to backfill — 2026-05-09T12 not yet released (release is cycle+7h40m)
CYCLES: list[tuple[date, int]] = [
    (date(2026, 5, 4), 12),
    (date(2026, 5, 5), 0),
    (date(2026, 5, 5), 12),
    (date(2026, 5, 6), 0),
    (date(2026, 5, 6), 12),
    (date(2026, 5, 7), 0),
    (date(2026, 5, 7), 12),
    (date(2026, 5, 8), 0),
    (date(2026, 5, 8), 12),
    (date(2026, 5, 9), 0),
]
TRACKS = ["mx2t6_high", "mn2t6_low"]


def already_succeeded(run_date: date, run_hour: int, track: str) -> bool:
    """Return True if source_run has a SUCCESS row for this cycle+track."""
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


def main() -> None:
    total = len(CYCLES) * len(TRACKS)
    landed = 0
    skipped_existing = 0
    failed: list[str] = []

    logger.info("Backfill starting: %d cycles × %d tracks = %d tasks", len(CYCLES), len(TRACKS), total)

    for run_date, run_hour in CYCLES:
        for track in TRACKS:
            label = f"{run_date.isoformat()}T{run_hour:02d}Z:{track}"

            if already_succeeded(run_date, run_hour, track):
                logger.info("SKIP (already SUCCESS): %s", label)
                skipped_existing += 1
                continue

            logger.info("Fetching: %s", label)
            try:
                result = collect_open_ens_cycle(
                    track=track,
                    run_date=run_date,
                    run_hour=run_hour,
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
                    (s.get("stderr_tail", "") for s in stages if s.get("label", "").startswith("download")),
                    "",
                )
                # NAME_MAX bug should be fixed; any remaining failure is real
                logger.error("DOWNLOAD_FAILED: %s\nstderr_tail: %s", label, stderr_tail[-500:])
                failed.append(f"{label}:DOWNLOAD_FAILED")
            else:
                logger.error("FAILED: %s — status=%s source_run_status=%s inserted=%d",
                             label, status, src_run_status, inserted)
                failed.append(f"{label}:{status.upper()}")

            # Verify row landed
            if status == "ok":
                conn = get_world_connection()
                try:
                    row = conn.execute(
                        "SELECT source_cycle_time, status FROM source_run "
                        "WHERE source_id=? AND source_cycle_time >= '2026-05-04' "
                        "ORDER BY source_cycle_time DESC LIMIT 5",
                        (SOURCE_ID,),
                    ).fetchall()
                    for r in row:
                        logger.info("  DB row: %s | %s", r[0], r[1])
                finally:
                    conn.close()

    logger.info(
        "Backfill complete: landed=%d skipped_existing=%d failed=%d",
        landed, skipped_existing, len(failed),
    )
    if failed:
        logger.warning("Failed cycles: %s", failed)

    print(f"\nSUMMARY: landed={landed}/{total - skipped_existing} skipped_existing={skipped_existing} failed={len(failed)}")
    if failed:
        print("Failed:", failed)


if __name__ == "__main__":
    main()
