#!/usr/bin/env python3
"""Resumable TIGGE mn2t6 regional downloader with checkpoint/status output."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from tigge_mx2t6_download_resumable import run


ROOT = Path("/Users/leofitz/.openclaw/workspace-venus/51 source data")
DEFAULT_MANIFEST = ROOT / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
DEFAULT_STATUS = ROOT / "tmp" / "tigge_mn2t6_download_status.json"
DEFAULT_RAW_ROOT = ROOT / "raw"
DEFAULT_REGION_SUBDIR = "tigge_ecmwf_ens_regions_mn2t6"
DEFAULT_MAX_TARGET_LEAD_DAY = 7


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--status-path", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--date-from", default="2024-01-01")
    parser.add_argument("--date-to", default=(date.today() - timedelta(days=2)).isoformat())
    parser.add_argument("--steps", nargs="+", type=int, default=None)
    parser.add_argument("--param", default="122.128")
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--region-subdir", default=DEFAULT_REGION_SUBDIR)
    parser.add_argument("--max-batch-days", type=int, default=3)
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--max-passes", type=int, default=2000)
    parser.add_argument("--retry-stall-limit", type=int, default=60)
    parser.add_argument("--sleep-seconds", type=int, default=180)
    parser.add_argument("--max-target-lead-day", type=int, default=DEFAULT_MAX_TARGET_LEAD_DAY)
    parser.add_argument("--cities", nargs="+")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cycle", choices=["00", "12"], default="00",
                        help="TIGGE model cycle: '00' (00Z, default) or '12' (12Z)")
    parser.add_argument("--grid", default="0.5/0.5",
                        help="MARS output grid, e.g. 0.5/0.5 legacy or 0.25/0.25 to match OpenData.")
    args = parser.parse_args()
    return run(
        manifest_path=args.manifest_path,
        status_path=args.status_path,
        date_from=date.fromisoformat(args.date_from),
        date_to=date.fromisoformat(args.date_to),
        steps=list(args.steps) if args.steps is not None else None,
        param=args.param,
        raw_root=args.raw_root,
        region_subdir=args.region_subdir,
        max_batch_days=args.max_batch_days,
        max_workers=args.max_workers,
        max_passes=args.max_passes,
        retry_stall_limit=args.retry_stall_limit,
        sleep_seconds=args.sleep_seconds,
        max_target_lead_day=args.max_target_lead_day,
        cities=args.cities,
        dry_run=args.dry_run,
        cycle=args.cycle,
        grid=args.grid,
    )


if __name__ == "__main__":
    raise SystemExit(main())
