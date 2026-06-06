#!/usr/bin/env python3
"""Audit TIGGE manifest rows against zeus/config/cities.json settlement metadata."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path("/Users/leofitz/.openclaw/workspace-venus")
DEFAULT_CITIES = ROOT / "zeus" / "config" / "cities.json"
DEFAULT_MANIFEST = ROOT / "51 source data" / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
DEFAULT_OUTPUT = ROOT / "51 source data" / "tmp" / "tigge_mx2t6_source_manifest_audit.json"


def _city_lat_lon(city: dict) -> tuple[float, float]:
    noaa = city.get("noaa") or {}
    lat = city.get("lat") if city.get("lat") is not None else noaa.get("lat")
    lon = city.get("lon") if city.get("lon") is not None else noaa.get("lon")
    if lat is None or lon is None:
        raise ValueError(f"Missing lat/lon for {city.get('name')}")
    return float(lat), float(lon)


def _expected_source_kind(city: dict) -> str:
    source_type = city.get("settlement_source_type")
    if source_type == "hko":
        return "hko_daily_extract"
    if source_type == "noaa":
        return "noaa_timeseries"
    if source_type == "cwa_station":
        return "cwa_station"
    return "wu_daily_airport"


def _site_from_settlement_source(url: str | None) -> str | None:
    if not url:
        return None
    query = parse_qs(urlparse(url).query)
    values = query.get("site")
    if values:
        return values[0]
    return None


def _expected_station(city: dict, source_kind: str) -> str | None:
    if source_kind == "hko_daily_extract":
        return city.get("hko_station") or "HKO"
    if source_kind == "cwa_station":
        return city.get("cwa_station") or city.get("wu_station")
    if source_kind == "noaa_timeseries":
        return city.get("wu_station") or _site_from_settlement_source(city.get("settlement_source"))
    return city.get("wu_station")


def _norm_station(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def audit(cities_path: Path, manifest_path: Path) -> dict:
    cities_obj = json.loads(cities_path.read_text(encoding="utf-8"))
    manifest_obj = json.loads(manifest_path.read_text(encoding="utf-8"))

    cities_by_name = {str(row["name"]): row for row in cities_obj["cities"]}
    manifest_by_name = {str(row["city"]): row for row in manifest_obj["cities"]}

    city_names = set(cities_by_name)
    manifest_names = set(manifest_by_name)

    missing_in_manifest = sorted(city_names - manifest_names)
    extra_in_manifest = sorted(manifest_names - city_names)

    field_mismatches: list[dict] = []
    for city_name in sorted(city_names & manifest_names):
        city = cities_by_name[city_name]
        manifest_row = manifest_by_name[city_name]
        expected_lat, expected_lon = _city_lat_lon(city)
        expected_kind = _expected_source_kind(city)
        expected_station = _norm_station(_expected_station(city, expected_kind))

        checks = {
            "lat": (float(manifest_row.get("lat")), expected_lat),
            "lon": (float(manifest_row.get("lon")), expected_lon),
            "timezone": (manifest_row.get("timezone"), city.get("timezone")),
            "unit": (manifest_row.get("unit"), city.get("unit")),
            "settlement_source_kind": (manifest_row.get("settlement_source_kind"), expected_kind),
            "station": (_norm_station(manifest_row.get("station")), expected_station),
        }
        for field, (actual, expected) in checks.items():
            if field in {"lat", "lon"}:
                if abs(float(actual) - float(expected)) > 1e-7:
                    field_mismatches.append(
                        {"city": city_name, "field": field, "actual": actual, "expected": expected}
                    )
            elif actual != expected:
                field_mismatches.append(
                    {"city": city_name, "field": field, "actual": actual, "expected": expected}
                )

    source_kind_counts: dict[str, int] = {}
    for row in manifest_obj["cities"]:
        kind = str(row.get("settlement_source_kind"))
        source_kind_counts[kind] = source_kind_counts.get(kind, 0) + 1

    ok = not missing_in_manifest and not extra_in_manifest and not field_mismatches
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cities_path": str(cities_path),
        "manifest_path": str(manifest_path),
        "city_count_cities_json": len(city_names),
        "city_count_manifest": len(manifest_names),
        "source_kind_counts_manifest": source_kind_counts,
        "missing_in_manifest": missing_in_manifest,
        "extra_in_manifest": extra_in_manifest,
        "field_mismatches": field_mismatches,
        "mismatch_count": len(field_mismatches),
        "ok": ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cities-path", type=Path, default=DEFAULT_CITIES)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--allow-mismatch", action="store_true")
    args = parser.parse_args()

    result = audit(args.cities_path, args.manifest_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["ok"] or args.allow_mismatch:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
