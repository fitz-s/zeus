#!/usr/bin/env python3
"""Audit TIGGE manifest rows against zeus/config/cities.json settlement metadata."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CITIES = ROOT / "config" / "cities.json"
DEFAULT_MANIFEST = ROOT / "51 source data" / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
DEFAULT_AUTHORITY = ROOT / "51 source data" / "docs" / "weather_settlement_station_authority_coordinates.json"
DEFAULT_OUTPUT = ROOT / "51 source data" / "tmp" / "tigge_mx2t6_source_manifest_audit.json"
DEFAULT_COORDINATE_THRESHOLD_M = 100.0


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


def _haversine_m(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    radius_m = 6_371_000.0
    dlat = math.radians(lat_b - lat_a)
    dlon = math.radians(lon_b - lon_a)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat_a))
        * math.cos(math.radians(lat_b))
        * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * radius_m * math.asin(math.sqrt(a))


def _load_authority_by_city(authority_path: Path | None) -> dict[str, dict]:
    if authority_path is None:
        return {}
    authority_obj = json.loads(authority_path.read_text(encoding="utf-8"))
    return {str(row["city"]): row for row in authority_obj.get("cities", [])}


def audit(
    cities_path: Path,
    manifest_path: Path,
    *,
    authority_path: Path | None = DEFAULT_AUTHORITY,
    coordinate_threshold_m: float = DEFAULT_COORDINATE_THRESHOLD_M,
) -> dict:
    cities_obj = json.loads(cities_path.read_text(encoding="utf-8"))
    manifest_obj = json.loads(manifest_path.read_text(encoding="utf-8"))
    authority_by_city = _load_authority_by_city(authority_path)

    cities_by_name = {str(row["name"]): row for row in cities_obj["cities"]}
    manifest_by_name = {str(row["city"]): row for row in manifest_obj["cities"]}

    city_names = set(cities_by_name)
    manifest_names = set(manifest_by_name)

    missing_in_manifest = sorted(city_names - manifest_names)
    extra_in_manifest = sorted(manifest_names - city_names)

    field_mismatches: list[dict] = []
    authority_coordinate_mismatches: list[dict] = []
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

        authority = authority_by_city.get(city_name)
        if authority is not None:
            authority_lat = authority.get("authority_lat")
            authority_lon = authority.get("authority_lon")
            if authority_lat is None or authority_lon is None:
                authority_coordinate_mismatches.append(
                    {"city": city_name, "field": "authority_coordinate", "reason": "missing_authority_lat_lon"}
                )
            else:
                distance_m = _haversine_m(
                    expected_lat,
                    expected_lon,
                    float(authority_lat),
                    float(authority_lon),
                )
                if distance_m > coordinate_threshold_m:
                    authority_coordinate_mismatches.append(
                        {
                            "city": city_name,
                            "field": "authority_coordinate",
                            "actual": {"lat": expected_lat, "lon": expected_lon},
                            "expected": {"lat": float(authority_lat), "lon": float(authority_lon)},
                            "distance_m": round(distance_m, 3),
                            "threshold_m": coordinate_threshold_m,
                            "authority_url": authority.get("authority_url"),
                        }
                    )

    source_kind_counts: dict[str, int] = {}
    for row in manifest_obj["cities"]:
        kind = str(row.get("settlement_source_kind"))
        source_kind_counts[kind] = source_kind_counts.get(kind, 0) + 1

    missing_in_authority = sorted(city_names - set(authority_by_city)) if authority_by_city else []

    ok = (
        not missing_in_manifest
        and not extra_in_manifest
        and not field_mismatches
        and not missing_in_authority
        and not authority_coordinate_mismatches
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cities_path": str(cities_path),
        "manifest_path": str(manifest_path),
        "authority_path": str(authority_path) if authority_path is not None else None,
        "coordinate_threshold_m": coordinate_threshold_m,
        "city_count_cities_json": len(city_names),
        "city_count_manifest": len(manifest_names),
        "city_count_authority": len(authority_by_city) if authority_by_city else None,
        "source_kind_counts_manifest": source_kind_counts,
        "missing_in_manifest": missing_in_manifest,
        "extra_in_manifest": extra_in_manifest,
        "missing_in_authority": missing_in_authority,
        "field_mismatches": field_mismatches,
        "authority_coordinate_mismatches": authority_coordinate_mismatches,
        "mismatch_count": len(field_mismatches) + len(authority_coordinate_mismatches) + len(missing_in_authority),
        "ok": ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cities-path", type=Path, default=DEFAULT_CITIES)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--authority-path", type=Path, default=DEFAULT_AUTHORITY)
    parser.add_argument("--coordinate-threshold-m", type=float, default=DEFAULT_COORDINATE_THRESHOLD_M)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--allow-mismatch", action="store_true")
    args = parser.parse_args()

    result = audit(
        args.cities_path,
        args.manifest_path,
        authority_path=args.authority_path,
        coordinate_threshold_m=args.coordinate_threshold_m,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["ok"] or args.allow_mismatch:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
