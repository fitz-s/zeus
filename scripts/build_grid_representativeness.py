# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: zeus_grid_coordinate_precision_upgrade_v3.md (operator v3). Builds the per-(city,model)
#   grid-representativeness table: the open-meteo native CELL coord + elevation each model snaps the
#   precise station to, the haversine d_eff station->cell, and Δz=z_station-z_cell. These feed the v3
#   representativeness variance σ_repr²(d_eff,|Δz|,regime) into the Bayes fusion Σ + the elevation
#   correction. Read-only network; writes a config artifact only (no DB).
"""Persist config/grid_representativeness.json from open-meteo cell metadata per (city, model)."""
from __future__ import annotations

import json
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


def cell_meta(lat: float, lon: float, model: str) -> tuple[float, float, float] | None:
    # 2026-06-17 land-cell fix: the live fetch now uses cell_selection=land, so the
    # representativeness geometry (d_eff station->cell, delta_z) MUST be measured to the LAND
    # cell, not the nearest offshore cell. Mirrors BAYES_PRECISION_FUSION_CELL_SELECTION.
    q = urllib.parse.urlencode(
        {"latitude": lat, "longitude": lon, "models": model,
         "daily": "temperature_2m_max", "forecast_days": 1, "timezone": "UTC",
         "cell_selection": "land"}
    )
    try:
        with urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{q}", timeout=20) as r:
            d = json.load(r)
        if d.get("latitude") is None or d.get("longitude") is None:
            return None
        return float(d["latitude"]), float(d["longitude"]), (float(d["elevation"]) if d.get("elevation") is not None else None)
    except Exception:
        return None


def main() -> int:
    reg = json.loads(PRECISE.read_text())
    out: dict[str, dict] = {}
    for city, e in sorted(reg.items()):
        try:
            slat, slon = float(e["lat"]), float(e["lon"])
        except (TypeError, ValueError):
            continue
        zst = e.get("elevation_m")
        out[city] = {"station": e.get("station"), "lat": e["lat"], "lon": e["lon"], "elevation_m": zst, "models": {}}
        for model in MODELS:
            m = cell_meta(slat, slon, model)
            time.sleep(0.25)
            if m is None:
                continue
            cla, clo, cel = m
            d_eff = round(haversine_m(slat, slon, cla, clo), 1)
            dz = round(zst - cel, 1) if (zst is not None and cel is not None) else None
            out[city]["models"][model] = {
                "cell_lat": cla, "cell_lon": clo, "cell_elevation_m": cel,
                "d_eff_m": d_eff, "delta_z_m": dz,
            }
        print(f"{city:14s} {len([1 for v in out[city]['models'].values()])} models  "
              f"d_eff range {min((v['d_eff_m'] for v in out[city]['models'].values()), default=0):.0f}-"
              f"{max((v['d_eff_m'] for v in out[city]['models'].values()), default=0):.0f}m", flush=True)
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True))
    print(f"\nwrote {OUT} ({len(out)} cities)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
