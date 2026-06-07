#!/usr/bin/env python3
"""Run TIGGE download + member extraction for multiple cities and dates."""
from __future__ import annotations

import argparse
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).resolve().parent
DOWNLOAD_SCRIPT = SCRIPT_DIR / "tigge_download_ecmwf_ens.py"
EXTRACT_SCRIPT = SCRIPT_DIR / "extract_tigge_city_member_vectors.py"
REGION_DOWNLOAD_SCRIPT = SCRIPT_DIR / "tigge_download_ecmwf_ens_region.py"
REGION_EXTRACT_SCRIPT = SCRIPT_DIR / "extract_tigge_region_member_vectors.py"
REGION_MULTI_DOWNLOAD_SCRIPT = SCRIPT_DIR / "tigge_download_ecmwf_ens_region_multistep.py"
REGION_MULTI_EXTRACT_SCRIPT = SCRIPT_DIR / "extract_tigge_region_member_vectors_multistep.py"
ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "raw"
MANIFEST_PATH = ROOT / "docs" / "tigge_city_coordinate_manifest_20260330.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


download_mod = _load_module("tigge_download_ecmwf_ens", DOWNLOAD_SCRIPT)
extract_mod = _load_module("extract_tigge_city_member_vectors", EXTRACT_SCRIPT)
region_download_mod = _load_module("tigge_download_ecmwf_ens_region", REGION_DOWNLOAD_SCRIPT)
region_extract_mod = _load_module("extract_tigge_region_member_vectors", REGION_EXTRACT_SCRIPT)
region_multi_download_mod = _load_module("tigge_download_ecmwf_ens_region_multistep", REGION_MULTI_DOWNLOAD_SCRIPT)
region_multi_extract_mod = _load_module("extract_tigge_region_member_vectors_multistep", REGION_MULTI_EXTRACT_SCRIPT)
regions_mod = _load_module("tigge_regions", SCRIPT_DIR / "tigge_regions.py")


def _daterange(start: date, end: date) -> list[date]:
    current = start
    out = []
    while current <= end:
        out.append(current)
        current += timedelta(days=1)
    return out


def _safe_slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def _load_manifest_rows(manifest_path: Path) -> dict[str, dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {row["city"]: row for row in manifest["cities"]}


def backfill_batch(
    cities: list[str],
    *,
    date_from: date,
    date_to: date,
    step: int,
    param: str,
    manifest_path: Path = MANIFEST_PATH,
    dry_run: bool = False,
    overwrite: bool = False,
    max_workers: int = 4,
) -> dict:
    results = []
    manifest_rows = _load_manifest_rows(manifest_path)
    region_groups: dict[str, list[str]] = defaultdict(list)
    for city in cities:
        row = manifest_rows[city]
        region = regions_mod.region_for_city(lat=float(row["lat"]), lon=float(row["lon"]))
        region_groups[region.name].append(city)

    city_results: dict[str, dict] = {city: {"city": city, "download_summary": None, "extraction_results": []} for city in cities}

    def _run_region(region_name: str, region_cities: list[str]) -> tuple[str, list[str], dict, list[tuple[str, dict]]]:
        download_summary = region_download_mod.download_region(
            region_name,
            date_from=date_from,
            date_to=date_to,
            step=step,
            param=param,
            raw_root=RAW_ROOT,
            overwrite=overwrite,
            dry_run=dry_run,
        )
        if dry_run:
            return region_name, region_cities, download_summary, []
        extracts: list[tuple[str, dict]] = []
        for current_date in _daterange(date_from, date_to):
            date_compact = current_date.strftime("%Y%m%d")
            region_dir = RAW_ROOT / "tigge_ecmwf_ens_regions" / region_name / date_compact
            cf_path = region_dir / f"tigge_ecmwf_control_param_{param.replace('.', '_')}_step_{step:03d}.grib"
            pf_path = region_dir / f"tigge_ecmwf_perturbed_param_{param.replace('.', '_')}_step_{step:03d}.grib"
            if not cf_path.exists() or not pf_path.exists():
                for city in region_cities:
                    extracts.append((city, {"city": city, "date": current_date.isoformat(), "status": "skipped_missing_raw"}))
                continue
            summary = region_extract_mod.extract_region_members(
                cf_path=cf_path,
                pf_path=pf_path,
                cities=region_cities,
                manifest_path=manifest_path,
                output_root=RAW_ROOT,
                param=param,
                step=step,
                overwrite=overwrite,
            )
            by_city = {row["city"]: row for row in summary["results"]}
            for city in region_cities:
                row = by_city.get(city, {"city": city, "status": "missing_extract_result"})
                row["date"] = current_date.isoformat()
                extracts.append((city, row))
        return region_name, region_cities, download_summary, extracts

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(region_groups) or 1))) as executor:
        futures = [executor.submit(_run_region, region_name, region_cities) for region_name, region_cities in region_groups.items()]
        for future in as_completed(futures):
            region_name, region_cities, download_summary, extracts = future.result()
            for city in region_cities:
                city_results[city]["download_summary"] = {
                    "mode": "region_batch",
                    "region": region_name,
                    "results": download_summary["results"],
                }
            for city, row in extracts:
                city_results[city]["extraction_results"].append(row)

    for city in cities:
        results.append(city_results[city])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cities": cities,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "step": step,
        "param": param,
        "dry_run": dry_run,
        "overwrite": overwrite,
        "results": results,
    }


def backfill_batch_multistep(
    cities: list[str],
    *,
    date_from: date,
    date_to: date,
    steps: list[int],
    param: str,
    manifest_path: Path = MANIFEST_PATH,
    dry_run: bool = False,
    overwrite: bool = False,
    max_workers: int = 4,
) -> dict:
    manifest_rows = _load_manifest_rows(manifest_path)
    region_groups: dict[str, list[str]] = defaultdict(list)
    for city in cities:
        row = manifest_rows[city]
        region = regions_mod.region_for_city(lat=float(row["lat"]), lon=float(row["lon"]))
        region_groups[region.name].append(city)

    date_values = _daterange(date_from, date_to)
    date_summaries = []
    def _run_region_multistep(current_date: date, region_name: str, region_cities: list[str]) -> dict:
            download_summary = region_multi_download_mod.download_region_multistep(
                region_name,
                date_value=current_date,
                steps=steps,
                param=param,
                raw_root=RAW_ROOT,
                overwrite=overwrite,
                dry_run=dry_run,
            )
            extract_summary = None
            if not dry_run:
                date_compact = current_date.strftime("%Y%m%d")
                step_slug = "-".join(f"{step:03d}" for step in steps)
                region_dir = RAW_ROOT / "tigge_ecmwf_ens_regions" / region_name / date_compact
                cf_path = region_dir / f"tigge_ecmwf_control_param_{param.replace('.', '_')}_steps_{step_slug}.grib"
                pf_path = region_dir / f"tigge_ecmwf_perturbed_param_{param.replace('.', '_')}_steps_{step_slug}.grib"
                if cf_path.exists() and pf_path.exists():
                    extract_summary = region_multi_extract_mod.extract_region_members_multistep(
                        cf_path=cf_path,
                        pf_path=pf_path,
                        cities=region_cities,
                        manifest_path=manifest_path,
                        output_root=RAW_ROOT,
                        param=param,
                        steps=steps,
                        overwrite=overwrite,
                    )
            return {
                "region": region_name,
                "cities": region_cities,
                "download_summary": download_summary,
                "extract_summary": extract_summary,
            }

    date_ranges = [(current_date, current_date) for current_date in date_values]
    if len(date_values) > 1:
        date_ranges = [(date_values[0], date_values[-1])]

    def _run_region_multistep(date_start: date, date_end: date, region_name: str, region_cities: list[str]) -> dict:
        dates = _daterange(date_start, date_end)
        download_summary = region_multi_download_mod.download_region_multistep(
            region_name,
            dates=dates,
            steps=steps,
            param=param,
            raw_root=RAW_ROOT,
            overwrite=overwrite,
            dry_run=dry_run,
        )
        extract_summary = None
        if not dry_run:
            start_compact = dates[0].strftime("%Y%m%d")
            end_compact = dates[-1].strftime("%Y%m%d")
            date_compact = start_compact if start_compact == end_compact else f"{start_compact}_{end_compact}"
            step_slug = "-".join(f"{step:03d}" for step in steps)
            region_dir = RAW_ROOT / "tigge_ecmwf_ens_regions" / region_name / date_compact
            cf_path = region_dir / f"tigge_ecmwf_control_param_{param.replace('.', '_')}_steps_{step_slug}.grib"
            pf_path = region_dir / f"tigge_ecmwf_perturbed_param_{param.replace('.', '_')}_steps_{step_slug}.grib"
            if cf_path.exists() and pf_path.exists():
                extract_summary = region_multi_extract_mod.extract_region_members_multistep(
                    cf_path=cf_path,
                    pf_path=pf_path,
                    cities=region_cities,
                    manifest_path=manifest_path,
                    output_root=RAW_ROOT,
                    param=param,
                    steps=steps,
                    dates=[day.strftime("%Y%m%d") for day in dates],
                    overwrite=overwrite,
                )
        return {
            "date_from": date_start.isoformat(),
            "date_to": date_end.isoformat(),
            "region": region_name,
            "cities": region_cities,
            "download_summary": download_summary,
            "extract_summary": extract_summary,
        }

    for date_start, date_end in date_ranges:
        region_summaries = []
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(region_groups) or 1))) as executor:
            futures = [
                executor.submit(_run_region_multistep, date_start, date_end, region_name, region_cities)
                for region_name, region_cities in region_groups.items()
            ]
            for future in as_completed(futures):
                region_summaries.append(future.result())
        date_summaries.append(
            {
                "date_from": date_start.isoformat(),
                "date_to": date_end.isoformat(),
                "steps": steps,
                "regions": region_summaries,
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cities": cities,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "steps": steps,
        "param": param,
        "dry_run": dry_run,
        "overwrite": overwrite,
        "date_summaries": date_summaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cities", nargs="+", help="City names from the TIGGE manifest")
    parser.add_argument("--date", help="Single date (YYYY-MM-DD)")
    parser.add_argument("--date-from", help="Inclusive start date (YYYY-MM-DD)")
    parser.add_argument("--date-to", help="Inclusive end date (YYYY-MM-DD)")
    parser.add_argument("--step", type=int, default=24)
    parser.add_argument("--steps", nargs="+", type=int)
    parser.add_argument("--param", default="167.128")
    parser.add_argument("--manifest-path", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()

    if args.date:
        date_from = date_to = date.fromisoformat(args.date)
    else:
        if not args.date_from or not args.date_to:
            raise SystemExit("Provide either --date or both --date-from and --date-to")
        date_from = date.fromisoformat(args.date_from)
        date_to = date.fromisoformat(args.date_to)

    if args.steps:
        summary = backfill_batch_multistep(
            args.cities,
            date_from=date_from,
            date_to=date_to,
            steps=args.steps,
            param=args.param,
            manifest_path=args.manifest_path,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
            max_workers=args.max_workers,
        )
    else:
        summary = backfill_batch(
            args.cities,
            date_from=date_from,
            date_to=date_to,
            step=args.step,
            param=args.param,
            manifest_path=args.manifest_path,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
            max_workers=args.max_workers,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
