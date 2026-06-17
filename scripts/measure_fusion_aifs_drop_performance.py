# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator directive 2026-06-17 ("drop aifs ... run me test on the new fusion's data
#   performance"; "每一个城市计算=openmeteo9km+超精细"). Settlement-graded backtest proving the
#   9km-IFS + finest-regional fusion beats the globals-only (and the colder AIFS-anchored) center.
"""Settlement-graded fusion performance test: globals-only vs 9km+ultra-fine finest-fusion vs VERIFIED settlement.

Read-only. For each VERIFIED settled (city, target_date, metric) in the window, reconstruct the
lead-1 latest-cycle-per-model member values and compare two fused centers to the settlement value
(native unit): (A) globals-only mean (the currently-served coarse set) and (B) the 9km ecmwf_ifs
anchor + the finest eligible provider-family representative per city (gfs_hrrr/icon_d2/ukmo_uk/
gem_hrdps where in-domain+lead-ok, else the global). Reports bias + MAE overall and for the
ultra-fine subset, per city. This is the AIFS-drop justification: the ultra-fine center removes the
cold-cell drag the AIFS anchor amplifies.

Usage: python scripts/measure_fusion_aifs_drop_performance.py [--days 30] [--lead 1]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from pathlib import Path

from src.forecast.model_selection import (
    PROVIDER_FAMILIES,
    _REGIONAL_DOMAIN_KEY,
    regional_eligible,
)

REPO = Path(__file__).resolve().parents[1]
FORECASTS_DB = REPO / "state" / "zeus-forecasts.db"
CITIES = REPO / "config" / "cities.json"
GLOBALS = ["ecmwf_ifs", "gfs_global", "icon_global", "ukmo_global_deterministic_10km", "gem_global", "jma_seamless"]
ULTRA = {"gfs_hrrr", "icon_d2", "ukmo_uk_deterministic_2km", "meteofrance_arome_france_hd", "gem_hrdps_continental"}


def c_to_native(v: float, unit: str) -> float:
    return v if unit == "C" else v * 9.0 / 5.0 + 32.0


def finest_reps(lat: float, lon: float, lead: int) -> list[str]:
    """9km ecmwf_ifs anchor + the finest eligible representative per provider family."""
    reps = ["ecmwf_ifs"]
    for fam in PROVIDER_FAMILIES:
        for m in fam:
            if m in _REGIONAL_DOMAIN_KEY:
                if regional_eligible(m, lat=lat, lon=lon, lead_days=lead):
                    reps.append(m)
                    break
            else:
                reps.append(m)
                break
    return list(dict.fromkeys(reps))


def _stat(xs: list[float]) -> tuple[float | None, float | None]:
    if not xs:
        return None, None
    return statistics.mean(xs), statistics.mean(abs(v) for v in xs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--lead", type=int, default=1)
    args = ap.parse_args()

    cj = {c["name"]: c for c in json.loads(CITIES.read_text())["cities"]}
    con = sqlite3.connect(f"file:{FORECASTS_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    cells = con.execute(
        """SELECT city, target_date, temperature_metric metric, settlement_value sv
           FROM settlement_outcomes
           WHERE authority='VERIFIED' AND settlement_value IS NOT NULL
             AND target_date >= date('now', ?)""",
        (f"-{args.days} day",),
    ).fetchall()

    glob_err: list[float] = []
    fine_err: list[float] = []
    per_city: dict[str, dict] = {}
    for r in cells:
        city, td, metric, sv = r["city"], r["target_date"], r["metric"], r["sv"]
        c = cj.get(city)
        if not c:
            continue
        unit = c.get("unit", "C")
        lat, lon = float(c["lat"]), float(c["lon"])
        mv = con.execute(
            """SELECT model, forecast_value_c fv FROM raw_model_forecasts r
               WHERE city=? AND metric=? AND target_date=? AND lead_days=?
                 AND source_cycle_time=(SELECT MAX(source_cycle_time) FROM raw_model_forecasts r2
                    WHERE r2.city=r.city AND r2.metric=r.metric AND r2.target_date=r.target_date
                      AND r2.model=r.model AND r2.lead_days=r.lead_days)""",
            (city, metric, td, args.lead),
        ).fetchall()
        vals = {m["model"]: m["fv"] for m in mv if m["fv"] is not None}
        if not vals:
            continue
        g = [vals[m] for m in GLOBALS if m in vals]
        reps = finest_reps(lat, lon, args.lead)
        f = [vals[m] for m in reps if m in vals]
        if not g or not f:
            continue
        ge = c_to_native(statistics.mean(g), unit) - sv
        fe = c_to_native(statistics.mean(f), unit) - sv
        glob_err.append(ge)
        fine_err.append(fe)
        d = per_city.setdefault(city, {"g": [], "f": [], "u": bool(set(reps) & ULTRA)})
        d["g"].append(ge)
        d["f"].append(fe)
    con.close()

    gb, gm = _stat(glob_err)
    fb, fm = _stat(fine_err)
    print(f"=== Fusion AIFS-drop performance (n={len(glob_err)} settled cells, last {args.days}d, lead-{args.lead}) ===")
    print(f"  globals-only  : bias={gb:+.3f}  MAE={gm:.3f}")
    print(f"  9km+ultrafine : bias={fb:+.3f}  MAE={fm:.3f}  (Δbias={fb-gb:+.3f}, ΔMAE={fm-gm:+.3f})")
    gu = [v for d in per_city.values() if d["u"] for v in d["g"]]
    fu = [v for d in per_city.values() if d["u"] for v in d["f"]]
    gub, gum = _stat(gu)
    fub, fum = _stat(fu)
    print(f"\n  ULTRA-FINE subset ({sum(1 for d in per_city.values() if d['u'])} cities, {len(gu)} cells):")
    print(f"    globals-only  : bias={gub:+.3f} MAE={gum:.3f}")
    print(f"    9km+ultrafine : bias={fub:+.3f} MAE={fum:.3f}  (Δbias={fub-gub:+.3f}, ΔMAE={fum-gum:+.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
