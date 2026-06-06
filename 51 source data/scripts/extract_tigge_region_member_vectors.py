#!/usr/bin/env python3
"""Extract multiple city member vectors from one regional TIGGE GRIB pair."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from eccodes import codes_get, codes_grib_find_nearest, codes_grib_new_from_file, codes_release

ROOT = Path("/Users/leofitz/.openclaw/workspace-venus/51 source data")
MANIFEST_PATH = ROOT / "docs" / "tigge_city_coordinate_manifest_full_20260330.json"


def _load_manifest(manifest_path: Path) -> dict:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _kelvin_to_native(value_k: float, unit: str) -> float:
    value_c = value_k - 273.15
    if unit.upper() == "F":
        return value_c * 9.0 / 5.0 + 32.0
    return value_c


def _extract_for_file(path: Path, cities: list[dict], forecast_type: str, city_members: dict[str, list[dict]]) -> None:
    with path.open("rb") as fh:
        while True:
            gid = codes_grib_new_from_file(fh)
            if gid is None:
                break
            member = 0 if forecast_type == "cf" else int(codes_get(gid, "number"))
            short_name = codes_get(gid, "shortName")
            data_date = int(codes_get(gid, "dataDate"))
            data_time = int(codes_get(gid, "dataTime"))
            step_range = str(codes_get(gid, "stepRange"))
            for city in cities:
                nearest = codes_grib_find_nearest(gid, float(city["lat"]), float(city["lon"]))[0]
                value_k = float(nearest["value"])
                city_members[city["city"]].append(
                    {
                        "member": member,
                        "forecast_type": forecast_type,
                        "short_name": short_name,
                        "data_date": data_date,
                        "data_time": data_time,
                        "step_range": step_range,
                        "nearest_grid_lat": float(nearest["lat"]),
                        "nearest_grid_lon": float(nearest["lon"]),
                        "distance_km": float(nearest["distance"]),
                        "value_kelvin": value_k,
                        "value_native_unit": _kelvin_to_native(value_k, str(city["unit"])),
                        "native_unit": str(city["unit"]),
                    }
                )
            codes_release(gid)


def extract_region_members(
    *,
    cf_path: Path,
    pf_path: Path,
    cities: list[str],
    manifest_path: Path,
    output_root: Path,
    param: str,
    step: int,
    overwrite: bool = False,
) -> dict:
    manifest = _load_manifest(manifest_path)
    selected = [row for row in manifest["cities"] if row["city"] in cities]
    city_members: dict[str, list[dict]] = defaultdict(list)
    _extract_for_file(cf_path, selected, "cf", city_members)
    _extract_for_file(pf_path, selected, "pf", city_members)

    summaries = []
    for city in selected:
        city_slug = city["city"].lower().replace(" ", "-")
        date_compact = cf_path.parent.name
        output_dir = output_root / "tigge_ecmwf_ens" / city_slug / date_compact
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"tigge_ecmwf_members_param_{param.replace('.', '_')}_step_{step:03d}.json"
        members = sorted(city_members[city["city"]], key=lambda item: item["member"])
        if output_path.exists() and not overwrite:
            summaries.append({"city": city["city"], "output_path": str(output_path), "status": "skipped_exists"})
            continue
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "city": city["city"],
            "lat": city["lat"],
            "lon": city["lon"],
            "unit": city["unit"],
            "cf_path": str(cf_path),
            "pf_path": str(pf_path),
            "member_count": len(members),
            "member_min": min((member["member"] for member in members), default=None),
            "member_max": max((member["member"] for member in members), default=None),
            "mean_native_unit": round(sum(m["value_native_unit"] for m in members) / len(members), 4) if members else None,
            "members": members,
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summaries.append(
            {
                "city": city["city"],
                "output_path": str(output_path),
                "member_count": len(members),
                "mean_native_unit": payload["mean_native_unit"],
                "status": "extracted",
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cf_path": str(cf_path),
        "pf_path": str(pf_path),
        "city_count": len(selected),
        "results": summaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cf-path", type=Path, required=True)
    parser.add_argument("--pf-path", type=Path, required=True)
    parser.add_argument("--cities", nargs="+", required=True)
    parser.add_argument("--manifest-path", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--output-root", type=Path, default=ROOT / "raw")
    parser.add_argument("--param", default="167.128")
    parser.add_argument("--step", type=int, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    summary = extract_region_members(
        cf_path=args.cf_path,
        pf_path=args.pf_path,
        cities=args.cities,
        manifest_path=args.manifest_path,
        output_root=args.output_root,
        param=args.param,
        step=args.step,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
