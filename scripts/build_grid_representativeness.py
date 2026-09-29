# Created: 2026-06-17
# Last audited: 2026-09-29
# Authority basis: replacement_final_form_2026_06_09.md §1d: response-bound
# geometry roles. Default Open-Meteo elevation is target DEM, not native height.
"""Persist default-downscaled product metadata, never a native-height penalty.

station_precise_coords is an airport reference registry, not proof of the
settlement sensor's ground height. This generator does not certify that height.
Read-only network; explicit invocation writes the config artifact only (no DB).
"""
from __future__ import annotations

import json
import hashlib
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PRECISE = REPO / "config" / "station_precise_coords.json"
OUT = REPO / "config" / "grid_representativeness.json"
# the forecast models the fusion may use (finest-first per provider family + globals)
MODELS = [
    "ecmwf_ifs", "gfs_global", "gfs_hrrr", "icon_global", "icon_eu", "icon_d2",
    "ukmo_global_deterministic_10km", "ukmo_uk_deterministic_2km", "gem_global",
    "gem_hrdps_continental", "jma_seamless", "meteofrance_arome_france_hd", "ncep_nbm_conus",
]


def haversine_m(la1: float, lo1: float, la2: float, lo2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def cell_meta(lat: float, lon: float, model: str, *, station: str) -> dict | None:
    params = {"latitude": lat, "longitude": lon, "models": model,
         "daily": "temperature_2m_max", "forecast_days": 1, "timezone": "UTC",
         "cell_selection": "land"}
    q = urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{q}", timeout=20) as r:
            entity_bytes = r.read()
        d = json.loads(entity_bytes)
        # One requested model must produce the single-model, Celsius extrema
        # response shape, not an error or a different/multi-model product.
        if (not isinstance(d, dict) or d.get("latitude") is None or d.get("longitude") is None
                or not isinstance(d.get("daily"), dict)
                or "temperature_2m_max" not in d["daily"]
                or not isinstance(d.get("daily_units"), dict)
                or d["daily_units"].get("temperature_2m_max") != "°C"):
            return None
        cla, clo = float(d["latitude"]), float(d["longitude"])
        dem = float(d["elevation"]) if d.get("elevation") is not None else None
        if not all(math.isfinite(v) for v in (cla, clo)) or (dem is not None and not math.isfinite(dem)):
            return None
        return {
            "height_role": "default_downscaled_target_dem",
            "target_dem_elevation_m": dem,
            "response_lat": cla, "response_lon": clo,
            "binding": {
                "station": station, "model": model, "product": "openmeteo/v1/forecast",
                "request_policy": params, "requested_lat": lat, "requested_lon": lon,
                "raw_response_sha256": hashlib.sha256(entity_bytes).hexdigest(),
            },
            "native_cell_elevation_m": None,
            "station_ground_height_m": None,
        }
    except (OSError, TypeError, ValueError):
        return None


def main() -> int:
    reg = json.loads(PRECISE.read_text())
    out: dict[str, dict] = {"_provenance": {
        "schema": "grid_geometry_roles_v1",
        "station_registry_role": "airport_reference_not_sensor_ground",
    }}
    for city, e in sorted(reg.items()):
        try:
            slat, slon = float(e["lat"]), float(e["lon"])
        except (TypeError, ValueError):
            continue
        zst = e.get("elevation_m")
        out[city] = {"station": e.get("station"), "lat": e["lat"], "lon": e["lon"], "elevation_m": zst, "models": {}}
        for model in MODELS:
            m = cell_meta(slat, slon, model, station=str(e.get("station") or ""))
            time.sleep(0.25)
            if m is None:
                continue
            m["reference_distance_m"] = round(haversine_m(slat, slon, m["response_lat"], m["response_lon"]), 1)
            out[city]["models"][model] = m
        print(f"{city:14s} {len([1 for v in out[city]['models'].values()])} models  "
              f"reference distance range {min((v['reference_distance_m'] for v in out[city]['models'].values()), default=0):.0f}-"
              f"{max((v['reference_distance_m'] for v in out[city]['models'].values()), default=0):.0f}m", flush=True)
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True))
    print(f"\nwrote {OUT} ({len(out) - 1} cities; default-downscaled metadata only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
