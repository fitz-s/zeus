#!/usr/bin/env python3
"""Download ECMWF ENS control + perturbed TIGGE fields for a fixed city/date/step."""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ecmwfapi import ECMWFDataServer

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "tigge_city_coordinate_manifest_20260330.json"
RAW_ROOT = ROOT / "raw"


def _daterange(start: date, end: date) -> list[date]:
    current = start
    out = []
    while current <= end:
        out.append(current)
        current += timedelta(days=1)
    return out


def _load_city(manifest_path: Path, city_name: str) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest["cities"]:
        if row["city"].lower() == city_name.lower():
            return row
    raise KeyError(f"City {city_name!r} not found in {manifest_path}")


def _safe_slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def _request(city_row: dict, date_str: str, step: int, param: str, target: Path, forecast_type: str) -> dict:
    tigge_request = city_row["tigge_request"]
    request = {
        "dataset": "tigge",
        "class": "ti",
        "origin": "ecmf",
        "expver": "prod",
        "stream": "enfo",
        "levtype": "sfc",
        "param": param,
        "date": date_str,
        "time": "00:00:00",
        "step": str(step),
        "type": forecast_type,
        "area": tigge_request["area"],
        "grid": tigge_request["grid"],
        "target": str(target),
    }
    if forecast_type == "pf":
        request["number"] = "1/to/50"
    return request


def download_city(
    city: str,
    *,
    date_from: date,
    date_to: date,
    step: int,
    param: str,
    manifest_path: Path,
    raw_root: Path,
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict:
    city_row = _load_city(manifest_path, city)
    city_slug = _safe_slug(city_row["city"])
    server = ECMWFDataServer()
    results = []

    for current_date in _daterange(date_from, date_to):
        date_str = current_date.isoformat()
        date_compact = current_date.strftime("%Y%m%d")
        target_dir = raw_root / "tigge_ecmwf_ens" / city_slug / date_compact
        target_dir.mkdir(parents=True, exist_ok=True)
        for forecast_type in ("cf", "pf"):
            suffix = "control" if forecast_type == "cf" else "perturbed"
            target = target_dir / f"tigge_ecmwf_{suffix}_param_{param.replace('.', '_')}_step_{step:03d}.grib"
            request = _request(city_row, date_str, step, param, target, forecast_type)
            if target.exists() and not overwrite:
                results.append(
                    {
                        "city": city_row["city"],
                        "date": date_str,
                        "step": step,
                        "type": forecast_type,
                        "target": str(target),
                        "status": "skipped_exists",
                    }
                )
                continue
            if dry_run:
                results.append(
                    {
                        "city": city_row["city"],
                        "date": date_str,
                        "step": step,
                        "type": forecast_type,
                        "target": str(target),
                        "status": "dry_run",
                        "request": request,
                    }
                )
                continue
            server.retrieve(request)
            results.append(
                {
                    "city": city_row["city"],
                    "date": date_str,
                    "step": step,
                    "type": forecast_type,
                    "target": str(target),
                    "bytes": target.stat().st_size if target.exists() else 0,
                    "status": "downloaded",
                }
            )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "city": city_row["city"],
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "step": step,
        "param": param,
        "raw_root": str(raw_root),
        "manifest_path": str(manifest_path),
        "dry_run": dry_run,
        "results": results,
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("city")
    parser.add_argument("--date", help="Single forecast basis date (YYYY-MM-DD)")
    parser.add_argument("--date-from", help="Inclusive start date (YYYY-MM-DD)")
    parser.add_argument("--date-to", help="Inclusive end date (YYYY-MM-DD)")
    parser.add_argument("--step", type=int, default=24)
    parser.add_argument("--param", default="167.128", help="GRIB parameter, defaults to 2m temperature")
    parser.add_argument("--manifest-path", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.date:
        date_from = date_to = date.fromisoformat(args.date)
    else:
        if not args.date_from or not args.date_to:
            raise SystemExit("Provide either --date or both --date-from and --date-to")
        date_from = date.fromisoformat(args.date_from)
        date_to = date.fromisoformat(args.date_to)

    summary = download_city(
        args.city,
        date_from=date_from,
        date_to=date_to,
        step=args.step,
        param=args.param,
        manifest_path=args.manifest_path,
        raw_root=args.raw_root,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
