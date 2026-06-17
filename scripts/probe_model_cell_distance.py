# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator law "每个城市都应该有最好的天气预报 / 最贴近机场METAR的来源, 最细到欧洲2km;
#   fusion = add finer stations CLOSER to the airport, not farther; weight by resolution × inverse
#   cell-distance-to-airport". The PHYSICAL selection key for per-city near-airport model selection.
"""ONE-TIME static probe: per (city, model), fetch the Open-Meteo NEAREST grid cell coordinate and
compute its haversine distance to the airport (settlement) coordinate.

A model's native grid is fixed and the airport coordinate is fixed, so cell-distance is STATIC — probe
once, store, reuse (re-probe only when the city/model set changes). NOT a per-cycle call (no API DoS):
one minimal request per (city, model), rate-limited. Writes state/per_city_model_cell_distance.json
(city -> {model: dist_km}) + a readable docs/evidence/per_city_source/cell_distance_map.md.

This is the operator's data-precision key: the cold members (jma_seamless offshore-snap for coastal/
S-Asia airports, gem_global ~15km) are the FAR-cell ones; the near-fine models track settlement.
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

OUT_JSON = "state/per_city_model_cell_distance.json"
OUT_MD = "docs/evidence/per_city_source/cell_distance_map.md"
BASE = "https://api.open-meteo.com/v1/forecast"
SLEEP_S = 0.35  # ~3 req/s — well under any rate limit; one-time run

# OM model ids (decorrelated globals + regionals). ecmwf anchor uses ecmwf_ifs025 for the cell probe
# (the live IFS9 0.1° anchor distance is separately tracked by openmeteo_ecmwf_ifs9_precision_guard).
MODELS = [
    "ecmwf_ifs025", "gfs_global", "icon_global", "icon_seamless", "gem_global",
    "jma_seamless", "ukmo_global_deterministic_10km", "icon_eu", "ncep_nbm_conus",
    "icon_d2", "meteofrance_arome_france_hd", "ukmo_uk_deterministic_2km",
    "gfs_hrrr", "gem_hrdps_continental",
]


def hav(la1: float, lo1: float, la2: float, lo2: float) -> float:
    r = 6371.0
    p = math.pi / 180
    a = (math.sin((la2 - la1) * p / 2) ** 2
         + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def cell_distance(lat: float, lon: float, model: str) -> float | None:
    q = urllib.parse.urlencode(
        {"latitude": lat, "longitude": lon, "models": model, "hourly": "temperature_2m", "forecast_days": 1}
    )
    try:
        with urllib.request.urlopen(f"{BASE}?{q}", timeout=20) as r:
            d = json.load(r)
        cla, clo = d.get("latitude"), d.get("longitude")
        if cla is None or clo is None:
            return None
        return round(hav(lat, lon, float(cla), float(clo)), 2)
    except Exception:
        return None


def main() -> None:
    cities = json.load(open("config/cities.json"))["cities"]
    out: dict[str, dict[str, float | None]] = {}
    for cc in cities:
        name = cc["name"]
        lat, lon = float(cc["lat"]), float(cc["lon"])
        per: dict[str, float | None] = {}
        for m in MODELS:
            per[m] = cell_distance(lat, lon, m)
            time.sleep(SLEEP_S)
        out[name] = per
        near = sorted((v, m) for m, v in per.items() if v is not None)
        far = sorted(((v, m) for m, v in per.items() if v is not None), reverse=True)
        nstr = ", ".join(f"{m.split('_')[0]}={v}" for v, m in near[:3])
        fstr = ", ".join(f"{m.split('_')[0]}={v}" for v, m in far[:3])
        print(f"{name:14s} near[{nstr}]  far[{fstr}]")

    Path(OUT_JSON).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT_JSON).write_text(json.dumps(out, indent=1, sort_keys=True))

    lines = ["# Per-city per-model grid-cell distance to airport (km) — Open-Meteo nearest cell", "",
             "Static physical key for per-city near-airport selection. Far cells (coarse/offshore-snap) =",
             "the cold members; near-fine cells track settlement. Probe: scripts/probe_model_cell_distance.py", ""]
    for name in sorted(out):
        per = out[name]
        row = "  ".join(f"{m.split('_')[0][:5]}={per[m]}" for m in MODELS if per[m] is not None)
        lines.append(f"## {name}\n  {row}\n")
    Path(OUT_MD).write_text("\n".join(lines))
    print(f"\nwrote {OUT_JSON} + {OUT_MD}")


if __name__ == "__main__":
    main()
