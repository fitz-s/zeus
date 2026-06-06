#!/usr/bin/env python3
"""Scan TIGGE historical member coverage gaps for selected dates/steps/cities."""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/Users/leofitz/.openclaw/workspace-venus/51 source data")
RAW_ROOT = ROOT / "raw" / "tigge_ecmwf_ens"
DEFAULT_MANIFEST = ROOT / "docs" / "tigge_city_coordinate_manifest_full_20260330.json"
DEFAULT_OUTPUT = ROOT / "tmp" / "tigge_coverage_gaps.json"


def _daily_dates(start: date, end: date) -> list[str]:
    out = []
    current = start
    while current <= end:
        out.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return out


def _slug(city: str) -> str:
    return city.lower().replace(" ", "-")


def _load_previous(previous_path: Path | None) -> dict | None:
    if previous_path is None or not previous_path.exists():
        return None
    return json.loads(previous_path.read_text(encoding="utf-8"))


def scan_gaps(
    *,
    manifest_path: Path,
    dates: list[str],
    steps: list[int],
    previous_path: Path | None = None,
    recent_rescan_days: int = 31,
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cities = [row["city"] for row in manifest["cities"]]
    city_count = len(cities)
    gap_rows = []
    coverage = []
    previous = _load_previous(previous_path)
    previous_cov = {}
    previous_gap = {}
    if previous:
        previous_cov = {
            (row["date"], int(row["step"])): row
            for row in previous.get("coverage", [])
        }
        previous_gap = {
            (row["date"], int(row["step"])): row
            for row in previous.get("gaps", [])
        }
    latest_date = max(dates)
    latest_dt = datetime.strptime(latest_date, "%Y%m%d").date()

    for date_value in dates:
        for step in steps:
            key = (date_value, int(step))
            prev_cov = previous_cov.get(key)
            prev_gap_row = previous_gap.get(key)
            current_dt = datetime.strptime(date_value, "%Y%m%d").date()
            is_recent = (latest_dt - current_dt).days <= recent_rescan_days
            if prev_cov and prev_cov.get("missing_count") == 0 and not is_recent and prev_cov.get("present_count") == city_count:
                coverage.append(
                    {
                        "date": date_value,
                        "step": step,
                        "present_count": city_count,
                        "missing_count": 0,
                        "scan_mode": "cached_complete",
                    }
                )
                continue

            present = []
            missing = []
            filename = f"tigge_ecmwf_members_param_167_128_step_{step:03d}.json"
            cities_to_check = cities
            if prev_cov and not is_recent and prev_gap_row:
                prev_missing = list(prev_gap_row.get("missing_cities") or [])
                prev_present_count = int(prev_cov.get("present_count") or 0)
                if prev_present_count > 0 and prev_missing:
                    present = [f"__cached__{idx}" for idx in range(prev_present_count)]
                    cities_to_check = prev_missing
            for city in cities_to_check:
                if (RAW_ROOT / _slug(city) / date_value / filename).exists():
                    present.append(city)
                else:
                    missing.append(city)
            coverage.append(
                {
                    "date": date_value,
                    "step": step,
                    "present_count": len(present),
                    "missing_count": len(missing),
                    "scan_mode": "incremental" if prev_cov else "full",
                }
            )
            if missing:
                gap_rows.append(
                    {
                        "date": date_value,
                        "step": step,
                        "missing_cities": missing,
                    }
                )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(manifest_path),
        "city_count": len(cities),
        "dates": dates,
        "steps": steps,
        "coverage": coverage,
        "gaps": gap_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dates", nargs="+", help="Compact dates YYYYMMDD")
    parser.add_argument("--date-from", help="Inclusive start date YYYY-MM-DD for dense daily scan")
    parser.add_argument("--date-to", help="Inclusive end date YYYY-MM-DD for dense daily scan")
    parser.add_argument("--steps", nargs="+", type=int, required=True)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--previous-path", type=Path)
    parser.add_argument("--recent-rescan-days", type=int, default=31)
    args = parser.parse_args()

    if args.dates:
        dates = args.dates
    else:
        if not args.date_from or not args.date_to:
            raise SystemExit("Provide either --dates or both --date-from and --date-to")
        dates = _daily_dates(date.fromisoformat(args.date_from), date.fromisoformat(args.date_to))

    payload = scan_gaps(
        manifest_path=args.manifest_path,
        dates=dates,
        steps=args.steps,
        previous_path=args.previous_path,
        recent_rescan_days=args.recent_rescan_days,
    )
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_path": str(args.output_path), "gap_count": len(payload["gaps"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
