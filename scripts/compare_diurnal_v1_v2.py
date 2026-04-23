#!/usr/bin/env python3
# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: .omc/plans/observation-instants-migration-iter3.md Phase 3
#                  gate ("Expected: per-city p_high_set shape changes 0.5-2°F
#                  vs v1"); step7_phase3_closeout.md.
"""Shape-delta analysis: v1 (legacy openmeteo_archive_hourly) vs v2
(observation_instants_v2 station-native) diurnal curves.

Plan v3 Phase 3 predicted per-city ``p_high_set`` shape changes of
0.5-2°F moving from grid-snap openmeteo to station-native WU/Ogimet/
Meteostat data. This script quantifies that delta so Phase 3 closure
can cite measured evidence instead of an unvalidated prediction.

Method
------
1. Read the current ``diurnal_curves`` table (already rebuilt from v2
   via ``scripts/etl_diurnal_curves.py``).
2. Compute a "v1-equivalent" diurnal curve directly from legacy
   ``observation_instants`` using the same aggregation logic as the
   legacy ETL: ``avg(temp_current)`` per (city, season, hour),
   ``mean(1 if running_max >= final_high else 0)`` per cell.
3. Join v1 and v2 on (city, season, hour). Report:
   - per-city median |Δavg_temp| and |Δp_high_set|
   - fleet-wide distribution stats (p50, p90, p99, max)
   - cities where the Phase 3 prediction (0.5–2°F) is confirmed

No DB writes. Purely read-only.

Usage
-----
::

    python scripts/compare_diurnal_v1_v2.py
    python scripts/compare_diurnal_v1_v2.py --json > /tmp/shape_delta.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = _REPO_ROOT / "state" / "zeus-world.db"


def _season_from_month(month: int, lat: float) -> str:
    """Mirror src.calibration.manager.season_from_month with simple signs."""
    if lat >= 0:
        if month in (12, 1, 2): return "DJF"
        if month in (3, 4, 5): return "MAM"
        if month in (6, 7, 8): return "JJA"
        return "SON"
    # Southern hemisphere: swap DJF↔JJA, MAM↔SON
    if month in (12, 1, 2): return "JJA"
    if month in (3, 4, 5): return "SON"
    if month in (6, 7, 8): return "DJF"
    return "MAM"


def _compute_v1_shape(conn: sqlite3.Connection, city_lat: dict[str, float]) -> dict:
    """Return {(city, season, hour): (avg_temp, p_high_set, n_samples)}
    computed directly from legacy observation_instants. Mirrors
    etl_diurnal_curves.py aggregation logic (temp_current as sample,
    running_max for high_set comparison)."""
    rows = conn.execute(
        """
        SELECT city, target_date, local_timestamp, temp_current, running_max
        FROM observation_instants
        WHERE temp_current IS NOT NULL
          AND is_missing_local_hour = 0
          AND is_ambiguous_local_hour = 0
        ORDER BY city, target_date, utc_timestamp
        """
    ).fetchall()
    # Per day aggregation first (one sample per (city, date, hour))
    canonical: dict[tuple[str, str, int], list[tuple[float, Optional[float]]]] = defaultdict(list)
    for r in rows:
        city = str(r["city"])
        td = str(r["target_date"])
        try:
            hour = datetime.fromisoformat(str(r["local_timestamp"])).hour
        except ValueError:
            continue
        temp = float(r["temp_current"])
        rmax = float(r["running_max"]) if r["running_max"] is not None else temp
        canonical[(city, td, hour)].append((temp, rmax))

    # Aggregate per (city, target_date) to compute final_high
    per_day: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for (city, td, hour), samples in canonical.items():
        # mean of temp, max of running_max among sources
        temp = sum(t for t, _ in samples) / len(samples)
        rmax = max((rm if rm is not None else t) for t, rm in samples)
        per_day[(city, td)].append({"hour": hour, "temp": temp, "rmax": rmax})

    # For each (city, season, hour): collect temps + high_set indicators
    seasonal_temps: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    seasonal_high_set: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    for (city, td), day_samples in per_day.items():
        try:
            month = int(td.split("-")[1])
        except (ValueError, IndexError):
            continue
        lat = city_lat.get(city, 0.0)
        season = _season_from_month(month, lat)
        final_high = max(s["rmax"] for s in day_samples)
        for s in day_samples:
            seasonal_temps[(city, season, s["hour"])].append(s["temp"])
            high_set = 1.0 if s["rmax"] >= final_high - 1e-9 else 0.0
            seasonal_high_set[(city, season, s["hour"])].append(high_set)

    out: dict[tuple[str, str, int], tuple[float, float, int]] = {}
    for key, temps in seasonal_temps.items():
        if len(temps) < 5:
            continue
        avg = sum(temps) / len(temps)
        hs = seasonal_high_set.get(key, [])
        p_hs = sum(hs) / len(hs) if hs else 0.0
        out[key] = (avg, p_hs, len(temps))
    return out


def _read_v2_shape(conn: sqlite3.Connection) -> dict:
    """Return {(city, season, hour): (avg_temp, p_high_set, n_samples)} from
    the current diurnal_curves table (already v2-sourced)."""
    rows = conn.execute(
        "SELECT city, season, hour, avg_temp, p_high_set, n_samples "
        "FROM diurnal_curves"
    ).fetchall()
    return {
        (r["city"], r["season"], int(r["hour"])):
            (float(r["avg_temp"]), float(r["p_high_set"] or 0.0), int(r["n_samples"]))
        for r in rows
    }


def _lat_for_city_map() -> dict[str, float]:
    """Load city → lat mapping from config/cities.json. Cities with missing
    lat (11 US entries in cities.json) default to +40 (northern hemisphere)
    which is safe for season_from_month on US latitudes."""
    cities_path = _REPO_ROOT / "config" / "cities.json"
    with cities_path.open() as fh:
        data = json.load(fh)
    out: dict[str, float] = {}
    for c in data["cities"]:
        lat = c.get("lat")
        out[c["name"]] = float(lat) if lat is not None else 40.0
    return out


def compare(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    city_lat = _lat_for_city_map()
    v1 = _compute_v1_shape(conn, city_lat)
    v2 = _read_v2_shape(conn)
    conn.close()

    # Join on (city, season, hour) where both have samples
    joined_keys = set(v1.keys()) & set(v2.keys())
    only_v1 = set(v1.keys()) - set(v2.keys())
    only_v2 = set(v2.keys()) - set(v1.keys())

    # Per-cell deltas
    per_city_avg_deltas: dict[str, list[float]] = defaultdict(list)
    per_city_phs_deltas: dict[str, list[float]] = defaultdict(list)
    all_avg_deltas: list[float] = []
    all_phs_deltas: list[float] = []

    for k in joined_keys:
        city = k[0]
        v1_avg, v1_phs, _ = v1[k]
        v2_avg, v2_phs, _ = v2[k]
        d_avg = abs(v2_avg - v1_avg)
        d_phs = abs(v2_phs - v1_phs)
        per_city_avg_deltas[city].append(d_avg)
        per_city_phs_deltas[city].append(d_phs)
        all_avg_deltas.append(d_avg)
        all_phs_deltas.append(d_phs)

    def _stats(values: list[float]) -> dict:
        if not values:
            return {"n": 0}
        s = sorted(values)
        return {
            "n": len(s),
            "min": round(s[0], 4),
            "p50": round(s[len(s) // 2], 4),
            "p90": round(s[int(len(s) * 0.9)], 4),
            "p99": round(s[int(len(s) * 0.99)], 4),
            "max": round(s[-1], 4),
            "mean": round(sum(s) / len(s), 4),
            "stdev": round(statistics.stdev(s), 4) if len(s) > 1 else 0.0,
        }

    per_city_summary = {}
    for city, ds in per_city_avg_deltas.items():
        phs = per_city_phs_deltas.get(city, [])
        per_city_summary[city] = {
            "cells": len(ds),
            "median_abs_avg_temp_delta": round(statistics.median(ds), 4),
            "max_abs_avg_temp_delta": round(max(ds), 4),
            "median_abs_p_high_set_delta": round(statistics.median(phs), 4) if phs else None,
            "max_abs_p_high_set_delta": round(max(phs), 4) if phs else None,
        }

    # Phase 3 prediction check: 0.5–2 temp unit band (°F or °C depending on city).
    # Per-city median in [0.5, 2.0] confirms the prediction.
    prediction_confirmed = sorted([
        c for c, s in per_city_summary.items()
        if 0.5 <= s["median_abs_avg_temp_delta"] <= 2.0
    ])
    prediction_below = sorted([
        c for c, s in per_city_summary.items()
        if s["median_abs_avg_temp_delta"] < 0.5
    ])
    prediction_above = sorted([
        c for c, s in per_city_summary.items()
        if s["median_abs_avg_temp_delta"] > 2.0
    ])

    return {
        "coverage": {
            "v1_cells": len(v1),
            "v2_cells": len(v2),
            "joined_cells": len(joined_keys),
            "only_v1_cells": len(only_v1),
            "only_v2_cells": len(only_v2),
        },
        "fleet_avg_temp_delta_stats": _stats(all_avg_deltas),
        "fleet_p_high_set_delta_stats": _stats(all_phs_deltas),
        "per_city": per_city_summary,
        "phase3_prediction_0p5_to_2_check": {
            "confirmed_cities": prediction_confirmed,
            "below_0p5_cities": prediction_below,
            "above_2_cities": prediction_above,
            "confirmed_count": len(prediction_confirmed),
            "total_cities_joined": len(per_city_summary),
        },
    }


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare v1 vs v2 diurnal curves")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--json", action="store_true", help="Emit JSON report")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    report = compare(args.db)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    # Human-readable summary
    print("observation_instants_v2 Phase 3 — diurnal shape delta (v1 → v2)")
    print("=" * 70)
    cov = report["coverage"]
    print(
        f"coverage: v1={cov['v1_cells']} cells  v2={cov['v2_cells']} cells  "
        f"joined={cov['joined_cells']}  (only_v1={cov['only_v1_cells']}, "
        f"only_v2={cov['only_v2_cells']})"
    )
    print()
    print("Fleet-wide |Δavg_temp|:")
    for k, v in report["fleet_avg_temp_delta_stats"].items():
        print(f"  {k:8s} = {v}")
    print()
    print("Fleet-wide |Δp_high_set|:")
    for k, v in report["fleet_p_high_set_delta_stats"].items():
        print(f"  {k:8s} = {v}")
    print()
    pred = report["phase3_prediction_0p5_to_2_check"]
    print(
        f"Phase 3 prediction check (0.5 ≤ median |Δavg_temp| ≤ 2.0): "
        f"{pred['confirmed_count']}/{pred['total_cities_joined']} cities confirmed"
    )
    print(f"  confirmed   ({len(pred['confirmed_cities'])}): "
          f"{', '.join(pred['confirmed_cities'])}")
    print(f"  below 0.5   ({len(pred['below_0p5_cities'])}): "
          f"{', '.join(pred['below_0p5_cities'])}")
    print(f"  above 2.0   ({len(pred['above_2_cities'])}): "
          f"{', '.join(pred['above_2_cities'])}")
    print()
    print("Per-city top movers (by median |Δavg_temp|):")
    print(f"  {'city':20s}  cells  med|Δtemp|  max|Δtemp|  med|Δp_hs|")
    top = sorted(
        report["per_city"].items(),
        key=lambda kv: kv[1]["median_abs_avg_temp_delta"],
        reverse=True,
    )[:12]
    for city, s in top:
        print(
            f"  {city:20s}  {s['cells']:5d}  "
            f"{s['median_abs_avg_temp_delta']:9.3f}  "
            f"{s['max_abs_avg_temp_delta']:9.3f}  "
            f"{s['median_abs_p_high_set_delta']:9.3f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
