#!/usr/bin/env python3
"""Build the full TIGGE manifest from Zeus settlement-source city metadata."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

SOURCE_DATA_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SOURCE_DATA_ROOT.parent
ZEUS_CITIES = PROJECT_ROOT / "config" / "cities.json"
DOCS_DIR = SOURCE_DATA_ROOT / "docs"
JSON_OUT = DOCS_DIR / "tigge_city_coordinate_manifest_full_latest.json"
MD_OUT = DOCS_DIR / "TIGGE_CITY_COORDINATE_MANIFEST_FULL_LATEST.md"
LEGACY_JSON_OUT = DOCS_DIR / "tigge_city_coordinate_manifest_full_20260330.json"
LEGACY_MD_OUT = DOCS_DIR / "TIGGE_CITY_COORDINATE_MANIFEST_FULL_20260330.md"


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
    lat = city.get("lat") if city.get("lat") is not None else noaa.get("lat")
    lon = city.get("lon") if city.get("lon") is not None else noaa.get("lon")
    if lat is None or lon is None:
        raise ValueError(f"Missing lat/lon for {city.get('name')}")
    return float(lat), float(lon)


def _source_kind(city: dict) -> str:
    source_type = city.get("settlement_source_type")
    if source_type == "hko":
        return "hko_daily_extract"
    if source_type == "noaa":
        return "noaa_timeseries"
    if source_type == "cwa_station":
        return "cwa_station"
    return "wu_daily_airport"


def _station_for_city(city: dict, source_kind: str) -> str | None:
    if source_kind == "hko_daily_extract":
        return city.get("hko_station") or "HKO"
    if source_kind == "cwa_station":
        return city.get("cwa_station") or city.get("wu_station")
    if source_kind == "noaa_timeseries":
        if city.get("wu_station"):
            return city.get("wu_station")
        noaa = city.get("noaa") or {}
        return noaa.get("station") or noaa.get("site")
    return city.get("wu_station")


def build_manifest() -> dict:
    source = json.loads(ZEUS_CITIES.read_text(encoding="utf-8"))
    records = []
    for city in source["cities"]:
        lat, lon = _city_lat_lon(city)
        settlement_source = city.get("settlement_source")
        source_kind = _source_kind(city)
        station = _station_for_city(city, source_kind)
        records.append(
            {
                "city": city["name"],
                "unit": city.get("unit"),
                "timezone": city.get("timezone"),
                "airport_name": city.get("airport_name"),
                "settlement_source": settlement_source,
                "settlement_source_type": city.get("settlement_source_type"),
                "settlement_source_kind": source_kind,
                "station": station,
                "hko_station": city.get("hko_station"),
                "wu_station": city.get("wu_station"),
                "cwa_station": city.get("cwa_station"),
                "noaa": city.get("noaa"),
                "wu_pws": city.get("wu_pws"),
                "meteostat_station": city.get("meteostat_station"),
                "country_code": city.get("country_code"),
                "lat": lat,
                "lon": lon,
                "coordinate_basis": "zeus_config_settlement_metadata",
                "tigge_request": _request_area(lat, lon),
            }
        )

    source_kind_counts: dict[str, int] = {}
    for row in records:
        kind = str(row["settlement_source_kind"])
        source_kind_counts[kind] = source_kind_counts.get(kind, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": str(ZEUS_CITIES),
        "grid_resolution_deg": 0.5,
        "city_count": len(records),
        "settlement_aligned_city_count": len(records),
        "source_kind_counts": source_kind_counts,
        "cities": records,
    }


def write_outputs(manifest: dict, *, json_out: Path, md_out: Path) -> None:
    json_out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# TIGGE Full Coordinate Manifest",
        f"> Generated: {manifest['generated_at']}",
        "",
        f"This manifest covers the {manifest['city_count']}-city temperature universe using settlement metadata from `zeus/config/cities.json`.",
        "Coordinates are settlement-source aligned station coordinates, with source kinds preserved (`wu_daily_airport`, `noaa_timeseries`, `hko_daily_extract`, `cwa_station`).",
        "",
        "| City | Lat | Lon | Unit | Station | Source Kind | Basis | Timezone | Area (N/W/S/E) |",
        "| --- | ---: | ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for row in manifest["cities"]:
        req = row["tigge_request"]
        lines.append(
            "| {city} | {lat:.4f} | {lon:.4f} | {unit} | {station} | {kind} | {basis} | {timezone} | `{area}` |".format(
                city=row["city"],
                lat=row["lat"],
                lon=row["lon"],
                unit=row["unit"],
                station=row.get("station") or row.get("wu_station") or "",
                kind=row.get("settlement_source_kind") or "",
                basis=row.get("coordinate_basis", "existing_manifest"),
                timezone=row["timezone"],
                area=req["area"],
            )
        )
    md_out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=JSON_OUT)
    parser.add_argument("--md-out", type=Path, default=MD_OUT)
    parser.add_argument("--legacy-json-out", type=Path, default=LEGACY_JSON_OUT)
    parser.add_argument("--legacy-md-out", type=Path, default=LEGACY_MD_OUT)
    args = parser.parse_args()
    manifest = build_manifest()
    write_outputs(manifest, json_out=args.json_out, md_out=args.md_out)
    if args.legacy_json_out != args.json_out or args.legacy_md_out != args.md_out:
        write_outputs(manifest, json_out=args.legacy_json_out, md_out=args.legacy_md_out)
    print(
        json.dumps(
            {
                "json_out": str(args.json_out),
                "md_out": str(args.md_out),
                "legacy_json_out": str(args.legacy_json_out),
                "legacy_md_out": str(args.legacy_md_out),
                "city_count": manifest["city_count"],
                "settlement_aligned_city_count": manifest["settlement_aligned_city_count"],
                "source_kind_counts": manifest["source_kind_counts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
