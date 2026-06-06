#!/usr/bin/env python3
"""Re-extract archived TIGGE member JSON files from existing raw GRIB files.

This is for coordinate repairs: member JSON files are archived first, then
rebuilt from preserved regional/per-city raw GRIB using the active manifest.
It does not call ECMWF and does not download data.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/Users/leofitz/.openclaw/workspace-venus/51 source data")
RAW_ROOT = ROOT / "raw"
REGION_ROOT = RAW_ROOT / "tigge_ecmwf_ens_regions"
CITY_ROOT = RAW_ROOT / "tigge_ecmwf_ens"
MANIFEST_PATH = ROOT / "docs" / "tigge_city_coordinate_manifest_full_20260330.json"
SCRIPT_DIR = ROOT / "scripts"
DEFAULT_STATUS = ROOT / "tmp" / "tigge_reextract_member_json_status.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


regions_mod = _load_module("tigge_regions", SCRIPT_DIR / "tigge_regions.py")
region_extract_mod = _load_module(
    "extract_tigge_region_member_vectors_multistep",
    SCRIPT_DIR / "extract_tigge_region_member_vectors_multistep.py",
)
city_extract_mod = _load_module(
    "extract_tigge_city_member_vectors",
    SCRIPT_DIR / "extract_tigge_city_member_vectors.py",
)


def _latest_archive() -> Path:
    archives = sorted(
        Path("/Users/leofitz/.openclaw/trash").glob("tigge_wrong_coordinate_member_json_*"),
        reverse=True,
    )
    if not archives:
        raise FileNotFoundError("No tigge_wrong_coordinate_member_json_* archive found")
    return archives[0]


def _parse_steps(filename: str) -> list[int] | None:
    match = re.search(r"_(?:step|steps)_([0-9-]+)\.grib$", filename)
    if not match:
        return None
    return [int(part) for part in match.group(1).split("-")]


def _parse_dates(dirname: str) -> list[str]:
    if "_" not in dirname:
        return [dirname]
    start_s, end_s = dirname.split("_", 1)
    start = datetime.strptime(start_s, "%Y%m%d").date()
    end = datetime.strptime(end_s, "%Y%m%d").date()
    out = []
    current = start
    while current <= end:
        out.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return out


def _candidate_score(path: Path, date_count: int, step_count: int) -> tuple[int, int, int]:
    # Prefer narrow date/step files, then shorter paths for deterministic ties.
    return (date_count, step_count, len(str(path)))


def _index_region_raw() -> dict[tuple[str, str, int, str], Path]:
    best: dict[tuple[str, str, int, str], tuple[tuple[int, int, int], Path]] = {}
    for path in REGION_ROOT.rglob("*.grib"):
        type_match = re.search(r"tigge_ecmwf_(control|perturbed)_param_167_128_", path.name)
        if not type_match:
            continue
        forecast_type = "cf" if type_match.group(1) == "control" else "pf"
        steps = _parse_steps(path.name)
        if not steps:
            continue
        region = path.parent.parent.name
        dates = _parse_dates(path.parent.name)
        score = _candidate_score(path, len(dates), len(steps))
        for date_value in dates:
            for step in steps:
                key = (region, date_value, step, forecast_type)
                current = best.get(key)
                if current is None or score < current[0]:
                    best[key] = (score, path)
    return {key: value[1] for key, value in best.items()}


def _archive_items(archive: Path) -> list[dict]:
    manifest_path = archive / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return list(payload.get("files") or [])


def _parse_archived_output(path_text: str) -> tuple[str, str, int, Path] | None:
    path = Path(path_text)
    parts = path.parts
    try:
        idx = parts.index("tigge_ecmwf_ens")
    except ValueError:
        try:
            idx = parts.index("raw") + 1
        except ValueError:
            return None
    if len(parts) <= idx + 3:
        return None
    slug = parts[idx + 1]
    date_value = parts[idx + 2]
    step_match = re.search(r"step_(\d+)\.json$", parts[-1])
    if not step_match:
        return None
    return slug, date_value, int(step_match.group(1)), CITY_ROOT / slug / date_value / parts[-1]


def _write_status(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def plan_reextract(*, archive: Path, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_slug = {row["city"].lower().replace(" ", "-"): row for row in manifest["cities"]}
    region_index = _index_region_raw()

    region_groups: dict[tuple[str, str], dict] = {}
    city_groups: dict[tuple[str, str, str], dict] = {}
    missing = []
    planned_files = 0

    for item in _archive_items(archive):
        parsed = _parse_archived_output(item["from"])
        if parsed is None:
            missing.append({"item": item, "reason": "unparseable_archive_path"})
            continue
        slug, date_value, step, output_path = parsed
        city = by_slug.get(slug)
        if city is None:
            missing.append({"output": str(output_path), "reason": "city_not_in_manifest"})
            continue
        region = regions_mod.region_for_city(lat=float(city["lat"]), lon=float(city["lon"])).name
        cf = region_index.get((region, date_value, step, "cf"))
        pf = region_index.get((region, date_value, step, "pf"))
        if cf and pf:
            key = (str(cf), str(pf))
            group = region_groups.setdefault(
                key,
                {"cf_path": cf, "pf_path": pf, "cities": set(), "dates": set(), "steps": set(), "outputs": set()},
            )
            group["cities"].add(city["city"])
            group["dates"].add(date_value)
            group["steps"].add(step)
            group["outputs"].add(str(output_path))
            planned_files += 1
            continue

        city_dir = CITY_ROOT / slug / date_value
        cf_city = city_dir / f"tigge_ecmwf_control_param_167_128_step_{step:03d}.grib"
        pf_city = city_dir / f"tigge_ecmwf_perturbed_param_167_128_step_{step:03d}.grib"
        if cf_city.exists() and pf_city.exists():
            key = (city["city"], str(cf_city), str(pf_city))
            group = city_groups.setdefault(
                key,
                {"city": city["city"], "cf_path": cf_city, "pf_path": pf_city, "outputs": set()},
            )
            group["outputs"].add(str(output_path))
            planned_files += 1
            continue

        missing.append(
            {
                "output": str(output_path),
                "city": city["city"],
                "date": date_value,
                "step": step,
                "region": region,
                "reason": "raw_pair_missing",
            }
        )

    return {
        "archive": str(archive),
        "planned_files": planned_files,
        "region_groups": list(region_groups.values()),
        "city_groups": list(city_groups.values()),
        "missing": missing,
    }


def run_reextract(plan: dict, *, manifest_path: Path, workers: int, status_path: Path) -> dict:
    totals = {
        "region_groups_total": len(plan["region_groups"]),
        "city_groups_total": len(plan["city_groups"]),
        "missing_raw_files": len(plan["missing"]),
        "planned_files": plan["planned_files"],
        "region_groups_done": 0,
        "city_groups_done": 0,
        "extracted_or_existing_outputs": 0,
        "errors": [],
    }

    def run_region(group: dict) -> dict:
        summary = region_extract_mod.extract_region_members_multistep(
            cf_path=Path(group["cf_path"]),
            pf_path=Path(group["pf_path"]),
            cities=sorted(group["cities"]),
            manifest_path=manifest_path,
            output_root=RAW_ROOT,
            param="167.128",
            steps=sorted(group["steps"]),
            dates=sorted(group["dates"]),
            overwrite=True,
        )
        outputs = [Path(path) for path in group["outputs"]]
        return {"kind": "region", "outputs_ok": sum(1 for output in outputs if output.exists()), "summary_count": len(summary["results"])}

    def run_city(group: dict) -> dict:
        outputs_ok = 0
        for output in sorted(group["outputs"]):
            city_extract_mod.extract_city_members(
                group["city"],
                Path(group["cf_path"]),
                Path(group["pf_path"]),
                manifest_path=manifest_path,
                output_path=Path(output),
            )
            if Path(output).exists():
                outputs_ok += 1
        return {"kind": "city", "outputs_ok": outputs_ok, "summary_count": outputs_ok}

    jobs = [("region", group) for group in plan["region_groups"]] + [("city", group) for group in plan["city_groups"]]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [
            executor.submit(run_region if kind == "region" else run_city, group)
            for kind, group in jobs
        ]
        for future in as_completed(futures):
            try:
                result = future.result()
                if result["kind"] == "region":
                    totals["region_groups_done"] += 1
                else:
                    totals["city_groups_done"] += 1
                totals["extracted_or_existing_outputs"] += result["outputs_ok"]
            except Exception as exc:  # noqa: BLE001
                totals["errors"].append(repr(exc))
            totals["updated_at"] = datetime.now(timezone.utc).isoformat()
            _write_status(status_path, totals)
    totals["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write_status(status_path, totals)
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=None)
    parser.add_argument("--manifest-path", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--status-path", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    archive = args.archive or _latest_archive()
    plan = plan_reextract(archive=archive, manifest_path=args.manifest_path)
    printable = {
        "archive": plan["archive"],
        "planned_files": plan["planned_files"],
        "region_groups": len(plan["region_groups"]),
        "city_groups": len(plan["city_groups"]),
        "missing_raw_files": len(plan["missing"]),
    }
    if args.dry_run:
        print(json.dumps(printable, ensure_ascii=False, indent=2))
        return 0

    status = run_reextract(plan, manifest_path=args.manifest_path, workers=args.workers, status_path=args.status_path)
    print(json.dumps({**printable, **status}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
