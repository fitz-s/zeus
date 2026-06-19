# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator design law "每个城市都应该有最好的天气预报 / per-city best near-airport
#   source"; docs/polyweather_city_source_overlay_verified.csv. Read-only settlement-graded
#   per-city per-model MAE/bias table — the empirical VALIDATOR for the per-city-best selection
#   (selection KEY is physical cell-distance×resolution; this proves every city has a faithful
#   source and ranks the open-meteo multi-model set by settlement fidelity at the decision lead).
"""Per-city per-model settlement MAE/bias at the decision lead (lead-1).

For each (city, model) it compares the model's forecast_value_c at lead_days==1 against the
settled value (unit-corrected: F-settled cities -> C), over all settled target_dates, and reports
n, bias, MAE. Ranks models per city -> the per-city BEST near-airport source by settlement fidelity.

Output: docs/evidence/per_city_source/per_city_model_mae.md (table) +
        docs/evidence/per_city_source/per_city_model_mae.json (machine-readable selection input).
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

DB = "state/zeus-forecasts.db"
OUT_MD = "docs/evidence/per_city_source/per_city_model_mae.md"
OUT_JSON = "docs/evidence/per_city_source/per_city_model_mae.json"
LEAD = 1
MIN_N = 8  # need at least this many settled comparisons to trust a model's MAE for a city


def city_units() -> dict[str, str]:
    units: dict[str, str] = {}
    try:
        cities = json.load(open("config/cities.json"))["cities"]
        for c in cities:
            for nm in [c.get("name")] + list(c.get("aliases") or []) + list(c.get("slug_names") or []):
                if nm:
                    units[str(nm)] = str(c.get("unit", "C") or "C")
    except Exception as e:  # noqa: BLE001
        print("WARN city units:", e)
    return units


def to_c(v: float, unit: str) -> float:
    return (v - 32.0) / 1.8 if str(unit).upper().startswith("F") else v


def main() -> None:
    units = city_units()
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)  # read_only_ro_uri: SELECT-only analysis

    # settlement truth keyed by (city, metric, target_date). settlements uses
    # `temperature_metric` ('high'/'low') == raw_model_forecasts.metric; unit is 'F'/'C';
    # authority must be VERIFIED (drop QUARANTINED).
    settle: dict[tuple, tuple[float, str]] = {}
    scols = [r[1] for r in c.execute("PRAGMA table_info(settlements)")]
    has_unit = "unit" in scols
    has_auth = "authority" in scols
    q = (
        "SELECT city, target_date, settlement_value, temperature_metric"
        + (", unit" if has_unit else "")
        + (", authority" if has_auth else "")
        + " FROM settlements WHERE settlement_value IS NOT NULL"
    )
    for row in c.execute(q).fetchall():
        city, td, sv, metric = row[0], row[1], row[2], row[3]
        idx = 4
        unit = row[idx] if has_unit else units.get(str(city), "C")
        idx += 1 if has_unit else 0
        auth = row[idx] if has_auth else "VERIFIED"
        if sv is None or (has_auth and str(auth) != "VERIFIED"):
            continue
        settle[(str(city), str(metric or "high"), str(td)[:10])] = (to_c(float(sv), unit), str(unit))

    # forecasts at lead-1
    errs: dict[tuple, list[float]] = defaultdict(list)  # (city, model) -> [forecast_c - settled_c]
    rows = c.execute(
        """
        SELECT city, model, metric, target_date, forecast_value_c
        FROM raw_model_forecasts
        WHERE lead_days = ? AND forecast_value_c IS NOT NULL
        """,
        (LEAD,),
    ).fetchall()
    for city, model, metric, td, fc in rows:
        key = (str(city), str(metric or "high"), str(td)[:10])
        s = settle.get(key)
        if s is None:
            continue
        errs[(str(city), str(model))].append(float(fc) - s[0])

    # aggregate
    per_city: dict[str, list[dict]] = defaultdict(list)
    for (city, model), e in errs.items():
        if len(e) < MIN_N:
            continue
        bias = statistics.mean(e)
        mae = statistics.mean(abs(x) for x in e)
        per_city[city].append({"model": model, "n": len(e), "bias_c": round(bias, 2), "mae_c": round(mae, 2)})

    out_json: dict[str, dict] = {}
    lines = ["# Per-city per-model settlement MAE @ lead-1 (unit-corrected to °C)", "",
             f"DB={DB}  min_n={MIN_N}  (validator for per-city-best selection; every city's BEST row is its settlement-faithful near-airport source)", ""]
    faithful = 0
    for city in sorted(per_city):
        ranked = sorted(per_city[city], key=lambda d: d["mae_c"])
        best = ranked[0]
        out_json[city] = {"best": best, "ranked": ranked}
        if best["mae_c"] <= 1.5:
            faithful += 1
        lines.append(f"## {city} — BEST: {best['model']} (MAE {best['mae_c']}°C, bias {best['bias_c']}, n={best['n']})")
        for d in ranked:
            lines.append(f"  {d['mae_c']:5.2f}  bias {d['bias_c']:+5.2f}  n={d['n']:3d}  {d['model']}")
        lines.append("")
    header_summary = f"\nCities with a <=1.5°C-MAE best source: {faithful}/{len(per_city)}\n"
    lines.insert(3, header_summary)

    Path(OUT_MD).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT_MD).write_text("\n".join(lines))
    Path(OUT_JSON).write_text(json.dumps(out_json, indent=1, sort_keys=True))
    print(f"cities={len(per_city)}  faithful(<=1.5C)={faithful}")
    print(f"wrote {OUT_MD} + {OUT_JSON}")


if __name__ == "__main__":
    main()
