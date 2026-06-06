#!/usr/bin/env python3
"""Resilient TIGGE backfill over a date range for multiple forecast lead steps."""
from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CITY_BATCH_SCRIPT = SCRIPT_DIR / "backfill_tigge_city_batch.py"
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


city_batch_mod = _load_module("backfill_tigge_city_batch", CITY_BATCH_SCRIPT)


def _daterange(start: date, end: date, interval_days: int) -> list[date]:
    from datetime import timedelta
    current = start
    out = []
    while current <= end:
        out.append(current)
        current += timedelta(days=interval_days)
    return out


def run_multistep_date_range(
    *,
    date_from: date,
    date_to: date,
    interval_days: int,
    cities: list[str],
    steps: list[int],
    param: str,
    manifest_path: Path,
    overwrite: bool = False,
    summary_path: Path | None = None,
    max_workers: int = 4,
) -> dict:
    written_path = summary_path or TMP_DIR / "tigge_multistep_date_range_summary.json"
    written_path.parent.mkdir(parents=True, exist_ok=True)
    initial_checkpoint = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "interval_days": interval_days,
        "cities": cities,
        "steps": steps,
        "param": param,
        "manifest_path": str(manifest_path),
        "completed_steps": 0,
        "total_steps": len(steps),
        "step_summaries": [],
    }
    written_path.write_text(json.dumps(initial_checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    step_summaries = []
    dates = _daterange(date_from, date_to, interval_days)
    for index, current_date in enumerate(dates, start=1):
        date_summary_path = written_path.with_name(f"{written_path.stem}_{current_date.strftime('%Y%m%d')}{written_path.suffix}")
        summary = city_batch_mod.backfill_batch_multistep(
            cities,
            date_from=current_date,
            date_to=current_date,
            steps=steps,
            param=param,
            manifest_path=manifest_path,
            overwrite=overwrite,
            dry_run=False,
            max_workers=max_workers,
        )
        date_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        step_summaries.append(
            {
                "date": current_date.isoformat(),
                "summary_path": str(date_summary_path),
                "steps": steps,
                "city_count": len(cities),
            }
        )
        checkpoint = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "interval_days": interval_days,
            "cities": cities,
            "steps": steps,
            "param": param,
            "manifest_path": str(manifest_path),
            "completed_steps": index,
            "total_steps": len(dates),
            "step_summaries": step_summaries,
        }
        written_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return checkpoint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True)
    parser.add_argument("--interval-days", type=int, default=1)
    parser.add_argument("--cities", nargs="+", required=True)
    parser.add_argument("--steps", nargs="+", required=True, type=int)
    parser.add_argument("--param", default="167.128")
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--summary-path", type=Path)
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()

    summary = run_multistep_date_range(
        date_from=date.fromisoformat(args.date_from),
        date_to=date.fromisoformat(args.date_to),
        interval_days=args.interval_days,
        cities=args.cities,
        steps=args.steps,
        param=args.param,
        manifest_path=args.manifest_path,
        overwrite=args.overwrite,
        summary_path=args.summary_path,
        max_workers=args.max_workers,
    )
    print(
        json.dumps(
            {
                "summary_path": str(args.summary_path or TMP_DIR / "tigge_multistep_date_range_summary.json"),
                "completed_steps": summary["completed_steps"],
                "total_steps": summary["total_steps"],
                "step_summaries": summary["step_summaries"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
