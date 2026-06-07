#!/usr/bin/env python3
"""Extract fixed-city ECMWF ENS member vectors from TIGGE GRIB files."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from eccodes import codes_get, codes_grib_find_nearest, codes_grib_new_from_file, codes_release

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "tigge_city_coordinate_manifest_20260330.json"


def _load_city(manifest_path: Path, city_name: str) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest["cities"]:
        if row["city"].lower() == city_name.lower():
            return row
    raise KeyError(f"City {city_name!r} not found in {manifest_path}")


def _kelvin_to_native(value_k: float, unit: str) -> float:
    value_c = value_k - 273.15
    if unit.upper() == "F":
        return value_c * 9.0 / 5.0 + 32.0
    return value_c


def _extract_members(path: Path, city_row: dict, forecast_type: str) -> list[dict]:
    lat = float(city_row["lat"])
    lon = float(city_row["lon"])
    unit = str(city_row["unit"])
    members = []
    with path.open("rb") as fh:
        while True:
            gid = codes_grib_new_from_file(fh)
            if gid is None:
                break
            nearest = codes_grib_find_nearest(gid, lat, lon)[0]
            member = 0 if forecast_type == "cf" else int(codes_get(gid, "number"))
            value_k = float(nearest["value"])
            members.append(
                {
                    "member": member,
                    "forecast_type": forecast_type,
                    "short_name": codes_get(gid, "shortName"),
                    "data_date": int(codes_get(gid, "dataDate")),
                    "data_time": int(codes_get(gid, "dataTime")),
                    "step_range": str(codes_get(gid, "stepRange")),
                    "nearest_grid_lat": float(nearest["lat"]),
                    "nearest_grid_lon": float(nearest["lon"]),
                    "distance_km": float(nearest["distance"]),
                    "value_kelvin": value_k,
                    "value_native_unit": _kelvin_to_native(value_k, unit),
                    "native_unit": unit,
                }
            )
            codes_release(gid)
    return members


def extract_city_members(city: str, cf_path: Path, pf_path: Path, *, manifest_path: Path, output_path: Path | None = None) -> dict:
    city_row = _load_city(manifest_path, city)
    members = _extract_members(cf_path, city_row, "cf") + _extract_members(pf_path, city_row, "pf")
    members = sorted(members, key=lambda item: item["member"])
    members = list({member["member"]: member for member in members}.values())
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "city": city_row["city"],
        "lat": city_row["lat"],
        "lon": city_row["lon"],
        "unit": city_row["unit"],
        "cf_path": str(cf_path),
        "pf_path": str(pf_path),
        "member_count": len(members),
        "member_min": min(member["member"] for member in members) if members else None,
        "member_max": max(member["member"] for member in members) if members else None,
        "mean_native_unit": round(sum(m["value_native_unit"] for m in members) / len(members), 4) if members else None,
        "members": members,
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("city")
    parser.add_argument("--cf-path", type=Path, required=True)
    parser.add_argument("--pf-path", type=Path, required=True)
    parser.add_argument("--manifest-path", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--output-path", type=Path)
    args = parser.parse_args()
    summary = extract_city_members(
        args.city,
        args.cf_path,
        args.pf_path,
        manifest_path=args.manifest_path,
        output_path=args.output_path,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
