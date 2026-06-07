#!/usr/bin/env python3
"""Scan ECMWF Open Data ENS city-vector coverage for a single run date."""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "raw" / "ecmwf_open_ens"
DEFAULT_MANIFEST = ROOT / "docs" / "tigge_city_coordinate_manifest_full_20260330.json"
DEFAULT_OUTPUT = ROOT / "tmp" / "open_ens_coverage_gaps_latest.json"


def _slug(city: str) -> str:
    return city.lower().replace(" ", "-")


def scan_gaps(
    *,
    manifest_path: Path,
    date_value: date,
    run_hour: int,
    steps: list[int],
    source: str,
    output_subdir: str,
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cities = [row["city"] for row in manifest["cities"]]
    date_compact = date_value.strftime("%Y%m%d")
    base_dir = RAW_ROOT / source / date_compact / output_subdir

    gap_rows = []
    coverage = []
    for step in steps:
        present = []
        missing = []
        filename_template = f"open_ens_{{slug}}_members_{date_compact}_{run_hour:02d}z_step_{step:03d}_2t.json"
        for city in cities:
            path = base_dir / filename_template.format(slug=_slug(city))
            if path.exists():
                present.append(city)
            else:
                missing.append(city)
        coverage.append(
            {
                "date": date_compact,
                "run_hour": run_hour,
                "step": step,
                "present_count": len(present),
                "missing_count": len(missing),
            }
        )
        if missing:
            gap_rows.append({"date": date_compact, "run_hour": run_hour, "step": step, "missing_cities": missing})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(manifest_path),
        "city_count": len(cities),
        "date": date_value.isoformat(),
        "run_hour": run_hour,
        "steps": steps,
        "source": source,
        "output_subdir": output_subdir,
        "coverage": coverage,
        "gaps": gap_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--run-hour", type=int, default=0)
    parser.add_argument("--steps", nargs="+", type=int, default=[24, 48, 72, 96, 120, 144, 168])
    parser.add_argument("--source", default="ecmwf")
    parser.add_argument("--output-subdir", default="all_cities")
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    payload = scan_gaps(
        manifest_path=args.manifest_path,
        date_value=date.fromisoformat(args.date),
        run_hour=args.run_hour,
        steps=args.steps,
        source=args.source,
        output_subdir=args.output_subdir,
    )
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_path": str(args.output_path), "gap_count": len(payload["gaps"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
