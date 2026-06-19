# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator law "每个城市都应该有最好的天气预报 / per-city best near-airport source".
#   Settlement-graded BEFORE/AFTER of the M1a select_models change (icon_global → icon_seamless as the
#   ICON-family rep) measured at the SELECTED-SET fused center, not just the ICON member. Read-only.
"""Center-warming before/after: fuse the SELECTED likelihood set (equal-weight, a defensible proxy
for the precision fusion's directional effect) under the OLD selection (icon_global ICON rep) vs the
NEW selection (icon_seamless), and compare both to VERIFIED settlement at lead-1.

OLD set := NEW selected set with icon_seamless swapped back to icon_global (the only member M1a changes;
EU cities where icon_d2 wins the ICON contest are unchanged → OLD == NEW). Aggregate MAE/bias vs
settlement quantifies how much the per-city-best ICON selection warms the served center toward truth.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict

from src.forecast import model_selection as ms

DB = "state/zeus-forecasts.db"
LEAD = 1


def to_c(v: float, unit: str) -> float:
    return (v - 32.0) / 1.8 if str(unit).upper().startswith("F") else v


def main() -> None:
    cities = {c["name"]: c for c in json.load(open("config/cities.json"))["cities"]}
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    # settlement truth (VERIFIED) keyed by (city, metric, target_date)
    settle: dict[tuple, float] = {}
    for city, td, sv, metric, unit, auth in c.execute(
        "SELECT city,target_date,settlement_value,temperature_metric,unit,authority "
        "FROM settlements WHERE settlement_value IS NOT NULL"
    ):
        if str(auth) != "VERIFIED":
            continue
        settle[(str(city), str(metric or "high"), str(td)[:10])] = to_c(float(sv), unit)

    # forecast values: (city, metric, td) -> {model: value_c} at lead-1
    fc: dict[tuple, dict[str, float]] = defaultdict(dict)
    for city, model, metric, td, v in c.execute(
        "SELECT city,model,metric,target_date,forecast_value_c FROM raw_model_forecasts "
        "WHERE lead_days=? AND forecast_value_c IS NOT NULL",
        (LEAD,),
    ):
        fc[(str(city), str(metric or "high"), str(td)[:10])][str(model)] = float(v)

    old_err: dict[str, list[float]] = defaultdict(list)
    new_err: dict[str, list[float]] = defaultdict(list)
    changed_cities: set[str] = set()

    for key, members in fc.items():
        city, _metric, _td = key
        s = settle.get(key)
        if s is None or city not in cities:
            continue
        cc = cities[city]
        sel = ms.select_models(
            present_models={m: members[m] for m in members},
            lat=float(cc["lat"]), lon=float(cc["lon"]), lead_days=LEAD,
        )
        new_set = [m for m in sel.used_models if m in members]
        if len(new_set) < 2:
            continue
        # OLD := swap icon_seamless -> icon_global (the only member M1a changes)
        old_set = list(new_set)
        if "icon_seamless" in old_set:
            changed_cities.add(city)
            old_set = [("icon_global" if m == "icon_seamless" else m) for m in old_set]
        old_vals = [members[m] for m in old_set if m in members]
        new_vals = [members[m] for m in new_set if m in members]
        if len(old_vals) < 2 or len(new_vals) < 2:
            continue
        old_c = statistics.mean(old_vals)
        new_c = statistics.mean(new_vals)
        old_err[city].append(old_c - s)
        new_err[city].append(new_c - s)

    # aggregate over the cities whose selected set actually changed
    print("Center (selected-set equal-weight mean) vs VERIFIED settlement @ lead-1 — OLD(icon_global) vs NEW(icon_seamless)")
    print(f"{'city':14s} {'n':>4s} | {'OLD MAE':>7s} {'NEW MAE':>7s} {'ΔMAE':>6s} | {'OLD bias':>8s} {'NEW bias':>8s}")
    all_old, all_new = [], []
    rows = []
    for city in sorted(changed_cities):
        o, n = old_err.get(city, []), new_err.get(city, [])
        if not o or not n:
            continue
        omae = statistics.mean(abs(x) for x in o); nmae = statistics.mean(abs(x) for x in n)
        obias = statistics.mean(o); nbias = statistics.mean(n)
        all_old += o; all_new += n
        rows.append((city, len(n), omae, nmae, nmae - omae, obias, nbias))
    for city, n, omae, nmae, dm, ob, nb in sorted(rows, key=lambda r: r[4]):
        print(f"{city:14s} {n:4d} | {omae:7.2f} {nmae:7.2f} {dm:+6.2f} | {ob:+8.2f} {nb:+8.2f}")
    if all_old:
        print(f"\nPOOLED over {len(rows)} changed cities (n={len(all_old)}):")
        print(f"  center MAE   OLD {statistics.mean(abs(x) for x in all_old):.3f}  ->  NEW {statistics.mean(abs(x) for x in all_new):.3f}")
        print(f"  center bias  OLD {statistics.mean(all_old):+.3f}  ->  NEW {statistics.mean(all_new):+.3f}  (closer to 0 = less cold)")


if __name__ == "__main__":
    main()
