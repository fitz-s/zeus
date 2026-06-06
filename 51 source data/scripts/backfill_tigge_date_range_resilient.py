#!/usr/bin/env python3
"""Resilient TIGGE backfill over a contiguous date range."""
from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ANCHOR_SCRIPT = SCRIPT_DIR / "backfill_tigge_anchor_dates_resilient.py"
ROOT = Path("/Users/leofitz/.openclaw/workspace-venus/51 source data")
DEFAULT_MANIFEST = ROOT / "docs" / "tigge_city_coordinate_manifest_20260330.json"
TMP_DIR = ROOT / "tmp"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


anchor_mod = _load_module("backfill_tigge_anchor_dates_resilient", ANCHOR_SCRIPT)


def _daterange(start: date, end: date, interval_days: int) -> list[str]:
    current = start
    out = []
    while current <= end:
        out.append(current.isoformat())
        current += timedelta(days=interval_days)
    return out


def run_date_range(
    *,
    date_from: date,
    date_to: date,
    interval_days: int,
    cities: list[str],
    step: int,
    param: str,
    manifest_path: Path,
    overwrite: bool = False,
    summary_path: Path | None = None,
) -> dict:
    dates = _daterange(date_from, date_to, interval_days)
    results = []
    ok_count = 0
    error_count = 0
    partial_count = 0
    written_path = summary_path or TMP_DIR / "tigge_date_range_resilient_summary.json"
    written_path.parent.mkdir(parents=True, exist_ok=True)
    initial_checkpoint = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "interval_days": interval_days,
        "processed_dates": 0,
        "total_dates": len(dates),
        "cities": cities,
        "step": step,
        "param": param,
        "manifest_path": str(manifest_path),
        "ok_count": 0,
        "partial_count": 0,
        "error_count": 0,
        "results": [],
    }
    written_path.write_text(json.dumps(initial_checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for index, date_value in enumerate(dates, start=1):
        batch_summary = anchor_mod.run_anchor_dates(
            dates=[date_value],
            cities=cities,
            step=step,
            param=param,
            manifest_path=manifest_path,
            overwrite=overwrite,
            summary_path=written_path.with_name(f"{written_path.stem}_{date_value.replace('-', '')}{written_path.suffix}"),
        )
        batch_results = batch_summary["results"]
        results.extend(batch_results)
        for row in batch_results:
            status = row["status"]
            if status == "ok":
                ok_count += 1
            elif status == "partial":
                partial_count += 1
            elif status == "error":
                error_count += 1
        checkpoint = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "interval_days": interval_days,
            "processed_dates": index,
            "total_dates": len(dates),
            "cities": cities,
            "step": step,
            "param": param,
            "manifest_path": str(manifest_path),
            "ok_count": ok_count,
            "partial_count": partial_count,
            "error_count": error_count,
            "results": results,
        }
        written_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return checkpoint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True)
    parser.add_argument("--interval-days", type=int, default=1)
    parser.add_argument("--cities", nargs="+", required=True)
    parser.add_argument("--step", type=int, default=24)
    parser.add_argument("--param", default="167.128")
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--summary-path", type=Path)
    args = parser.parse_args()

    summary = run_date_range(
        date_from=date.fromisoformat(args.date_from),
        date_to=date.fromisoformat(args.date_to),
        interval_days=args.interval_days,
        cities=args.cities,
        step=args.step,
        param=args.param,
        manifest_path=args.manifest_path,
        overwrite=args.overwrite,
        summary_path=args.summary_path,
    )
    print(
        json.dumps(
            {
                "summary_path": str(args.summary_path or TMP_DIR / "tigge_date_range_resilient_summary.json"),
                "processed_dates": summary["processed_dates"],
                "total_dates": summary["total_dates"],
                "ok_count": summary["ok_count"],
                "partial_count": summary["partial_count"],
                "error_count": summary["error_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
