#!/usr/bin/env python3
"""Generate fixed TIGGE extraction coordinates from Rainstorm city metadata."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/leofitz/.openclaw/workspace-venus")
RAINSTORM_CITIES = ROOT / "rainstorm" / "config" / "cities.json"
STAGING_CITIES = ROOT / "rainstorm" / "config" / "city_expansion_staging.json"
DOCS_DIR = ROOT / "51 source data" / "docs"
JSON_OUT = DOCS_DIR / "tigge_city_coordinate_manifest_20260330.json"
MD_OUT = DOCS_DIR / "TIGGE_CITY_COORDINATE_MANIFEST_20260330.md"


def _request_area(lat: float, lon: float) -> dict[str, float | str]:
    nearest_lat = round(lat * 2) / 2.0
    nearest_lon = round(lon * 2) / 2.0
    north = nearest_lat + 0.5
    south = nearest_lat - 0.5
    west = nearest_lon - 0.5
    east = nearest_lon + 0.5
    return {
        "nearest_grid_lat": nearest_lat,
        "nearest_grid_lon": nearest_lon,
        "north": north,
        "west": west,
        "south": south,
        "east": east,
        "area": f"{north:.1f}/{west:.1f}/{south:.1f}/{east:.1f}",
        "grid": "0.5/0.5",
        "padding_note": "3x3 grid centered on the nearest 0.5 degree TIGGE node",
    }


def _city_lat_lon(city: dict) -> tuple[float, float]:
    noaa = city.get("noaa") or {}
    lat = city.get("lat")
    lon = city.get("lon")
    if lat is None:
        lat = noaa.get("lat")
    if lon is None:
        lon = noaa.get("lon")
    if lat is None or lon is None:
        raise ValueError(f"Missing lat/lon for city {city.get('name')}")
    return float(lat), float(lon)


def _iter_candidate_cities(*, include_staging: bool) -> list[dict]:
    configured = json.loads(RAINSTORM_CITIES.read_text(encoding="utf-8")).get("cities") or []
    seen: dict[str, dict] = {row["name"]: row for row in configured}

    if include_staging and STAGING_CITIES.exists():
        staging = json.loads(STAGING_CITIES.read_text(encoding="utf-8"))
        for key, rows in staging.items():
            if key.startswith("_") or key == "generated_from" or not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = row.get("name")
                if not name or name in seen:
                    continue
                lat = row.get("lat") if row.get("lat") is not None else (row.get("noaa") or {}).get("lat")
                lon = row.get("lon") if row.get("lon") is not None else (row.get("noaa") or {}).get("lon")
                if lat is None or lon is None:
                    continue
                if not row.get("unit") or not row.get("timezone"):
                    continue
                seen[name] = row
    return sorted(seen.values(), key=lambda row: row["name"])


def build_manifest(*, include_staging: bool) -> dict:
    generated_at = datetime.now(timezone.utc).isoformat()
    records = []
    for city in _iter_candidate_cities(include_staging=include_staging):
        lat, lon = _city_lat_lon(city)
        area = _request_area(lat, lon)
        records.append(
            {
                "city": city["name"],
                "unit": city.get("unit"),
                "timezone": city.get("timezone"),
                "airport_name": city.get("airport_name"),
                "settlement_source": city.get("settlement_source"),
                "wu_station": city.get("wu_station"),
                "lat": lat,
                "lon": lon,
                "tigge_request": area,
            }
        )
    return {
        "generated_at": generated_at,
        "source_file": (
            f"{RAINSTORM_CITIES} + staging_complete_entries"
            if include_staging
            else str(RAINSTORM_CITIES)
        ),
        "grid_resolution_deg": 0.5,
        "city_count": len(records),
        "cities": records,
    }


def write_outputs(manifest: dict, *, json_out: Path, md_out: Path) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# TIGGE City Coordinate Manifest",
        f"> Generated: {manifest['generated_at']}",
        "",
        "These coordinates are fixed extraction points derived from Rainstorm settlement metadata.",
        "The `area` field is the 3x3 request box around the nearest 0.5 degree TIGGE grid node.",
        "",
        "| City | Lat | Lon | Unit | Area (N/W/S/E) | Grid | Settlement Source |",
        "| --- | ---: | ---: | --- | --- | --- | --- |",
    ]
    for row in manifest["cities"]:
        req = row["tigge_request"]
        lines.append(
            "| {city} | {lat:.4f} | {lon:.4f} | {unit} | `{area}` | `{grid}` | {source} |".format(
                city=row["city"],
                lat=row["lat"],
                lon=row["lon"],
                unit=row["unit"],
                area=req["area"],
                grid=req["grid"],
                source=row["settlement_source"],
            )
        )
    md_out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-staging", action="store_true")
    parser.add_argument("--json-out", type=Path, default=JSON_OUT)
    parser.add_argument("--md-out", type=Path, default=MD_OUT)
    args = parser.parse_args()

    manifest = build_manifest(include_staging=args.include_staging)
    write_outputs(manifest, json_out=args.json_out, md_out=args.md_out)
    print(json.dumps({"json_out": str(args.json_out), "md_out": str(args.md_out), "city_count": manifest["city_count"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
