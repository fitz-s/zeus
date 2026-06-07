#!/usr/bin/env python3
"""Resilient TIGGE backfill runner for a list of anchor dates across many cities."""
from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKFILL_SCRIPT = SCRIPT_DIR / "backfill_tigge_city_batch.py"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "tigge_city_coordinate_manifest_20260330.json"
TMP_DIR = ROOT / "tmp"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backfill_mod = _load_module("backfill_tigge_city_batch", BACKFILL_SCRIPT)


def run_anchor_dates(
    *,
    dates: list[str],
    cities: list[str],
    step: int,
    param: str,
    manifest_path: Path,
    overwrite: bool = False,
    summary_path: Path | None = None,
) -> dict:
    written_path = summary_path or TMP_DIR / "tigge_anchor_dates_resilient_summary.json"
    written_path.parent.mkdir(parents=True, exist_ok=True)
    results = []
    ok_count = 0
    partial_count = 0
    error_count = 0
    for date_value in dates:
        for city in cities:
            try:
                summary = backfill_mod.backfill_batch(
                    [city],
                    date_from=backfill_mod.date.fromisoformat(date_value),
                    date_to=backfill_mod.date.fromisoformat(date_value),
                    step=step,
                    param=param,
                    manifest_path=manifest_path,
                    dry_run=False,
                    overwrite=overwrite,
                )
                city_result = summary["results"][0]
                extraction_results = city_result.get("extraction_results") or []
                ok_statuses = {"extracted", "skipped_exists"}
                ok = any(row.get("status") in ok_statuses for row in extraction_results)
                results.append(
                    {
                        "date": date_value,
                        "city": city,
                        "status": "ok" if ok else "partial",
                        "summary": city_result,
                    }
                )
                if ok:
                    ok_count += 1
                else:
                    partial_count += 1
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "date": date_value,
                        "city": city,
                        "status": "error",
                        "error": repr(exc),
                    }
                )
                error_count += 1
            checkpoint = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "dates": dates,
                "cities": cities,
                "step": step,
                "param": param,
                "manifest_path": str(manifest_path),
                "ok_count": ok_count,
                "partial_count": partial_count,
                "error_count": error_count,
                "processed_results": len(results),
                "total_results": len(dates) * len(cities),
                "results": results,
            }
            written_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return checkpoint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dates", nargs="+", help="Anchor dates YYYY-MM-DD")
    parser.add_argument("--cities", nargs="+", required=True, help="City names")
    parser.add_argument("--step", type=int, default=24)
    parser.add_argument("--param", default="167.128")
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--summary-path", type=Path)
    args = parser.parse_args()

    summary = run_anchor_dates(
        dates=args.dates,
        cities=args.cities,
        step=args.step,
        param=args.param,
        manifest_path=args.manifest_path,
        overwrite=args.overwrite,
        summary_path=args.summary_path,
    )
    summary_path = args.summary_path or TMP_DIR / "tigge_anchor_dates_resilient_summary.json"
    print(json.dumps({"summary_path": str(summary_path), "result_count": len(summary["results"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
