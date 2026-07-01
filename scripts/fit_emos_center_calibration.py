#!/usr/bin/env python3
# Created: 2026-07-01
# Last audited: 2026-07-01
# Authority basis: EMOS/NGR affine center calibration. Operator sentence #1 —
#   "使用真实参与概率计算的运行态组合数据进行精准的emos设计提升". The 运行态组合数据 IS the served fused
#   center forecast_posteriors.anchor_value_c (the value that actually feeds the live probability q) —
#   NOT a raw model endpoint (previous_runs is ECMWF ifs025 0.25° coarse, single_runs is a raw source).
#   READ-ONLY over state/zeus-forecasts.db; SOLE writer of state/emos_center_calibration.json.
"""Fit the per-city affine EMOS center calibration μ'=a+b·μ on the REAL runtime served center.

Basis: forecast_posteriors.anchor_value_c (the served fused center, latest cycle per city/date) vs
the observed daily extreme (observations, the physical ground truth; venue settlement where present).
This is the exact product that participates in the live q — no previous_runs↔single_runs product gap,
so no transfer bridge is needed. Same-product ⇒ one honest basis.

The runtime history is short (~20 served days/city today), so validation is LEAVE-ONE-OUT (walk-forward
needs ~25 prior). A city SERVES only when its LOO OOS ΔMSE has a 95% lower CI ≥ 0 (per-unit no-harm).
Shrink-to-identity keeps the correction TINY on this thin data (a world-class city stays at identity);
the served (a,b) is fit on ALL runtime days and sharpens as the live history accrues.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from src.calibration.emos_center_calibration import (  # noqa: E402
    ARTIFACT_AUTHORITY,
    DEFAULT_KAPPA,
    apply_affine,
    fit_affine,
)
from src.config import runtime_state_path  # noqa: E402
from src.state.db import get_forecasts_connection_read_only  # noqa: E402

OUT_DEFAULT = str(runtime_state_path("emos_center_calibration.json"))
_OBS_COL = {"high": "high_temp", "low": "low_temp"}


def _to_c(v, u):
    v = float(v)
    return (v - 32.0) * 5.0 / 9.0 if str(u).strip().lower() in ("f", "degf", "fahrenheit") else v


def _observed_ground_truth(conn, metric):
    """(city, target_date) -> daily extreme in degC. VENUE settlement where it exists (authoritative
    traded truth), else the OBSERVED extreme from `observations`. observations carries BOTH extremes
    for all 54 cities and matches venue settlement 100% within 0.6C where a market exists; deduped
    preferring wu_icao_history (the wunderground source the venue settles from)."""
    truth = {}
    for r in conn.execute(
        "SELECT city,target_date,settlement_value,settlement_unit FROM settlement_outcomes "
        "WHERE temperature_metric=? AND authority='VERIFIED' AND settlement_value IS NOT NULL", (metric,)
    ):
        truth[(r[0], r[1])] = _to_c(r[2], r[3])
    col = _OBS_COL[metric]
    best = {}
    for r in conn.execute(
        f"SELECT city, target_date, {col} AS v, unit, source FROM observations WHERE {col} IS NOT NULL"
    ):
        if r[2] is None:
            continue
        k = (r[0], r[1])
        is_wu = 1 if r[4] == "wu_icao_history" else 0
        if k not in best or is_wu > best[k][1]:
            best[k] = (_to_c(r[2], r[3]), is_wu)
    for k, (val, _) in best.items():
        truth.setdefault(k, val)
    return truth


def _runtime_served_center(conn, metric):
    """city -> [(target_date, served_center_c, observed_c)] from the RUNTIME served center
    (forecast_posteriors.anchor_value_c, latest computed_at per city/date) — the value that feeds q."""
    latest = {}
    for r in conn.execute(
        "SELECT city,target_date,computed_at,provenance_json FROM forecast_posteriors "
        "WHERE temperature_metric=?", (metric,)
    ):
        k = (r[0], r[1])
        if k not in latest or r[2] > latest[k][0]:
            try:
                av = json.loads(r[3]).get("anchor_value_c")
            except Exception:
                av = None
            latest[k] = (r[2], av)
    truth = _observed_ground_truth(conn, metric)
    recs = defaultdict(list)
    for (city, td), (ca, av) in latest.items():
        if av is None:
            continue
        s = truth.get((city, td))
        if s is not None:
            recs[city].append((td, float(av), s))
    for c in recs:
        recs[c].sort()
    return recs


def _loo_dmse(pairs, kappa):
    """Leave-one-out OOS per-cell ΔMSE for the shrunk affine (thin-data honest validation)."""
    out = []
    for i in range(len(pairs)):
        tr = pairs[:i] + pairs[i + 1:]
        a, b = fit_affine(tr, kappa=kappa)
        cc, s = pairs[i]
        out.append((s - cc) ** 2 - (s - apply_affine(cc, a, b)) ** 2)
    return out


def _lower_ci(xs, *, alpha=0.05, nboot=1000, seed=5):
    if len(xs) < 2:
        return float("-inf")
    rng = random.Random(seed)
    return sorted(statistics.mean(rng.choice(xs) for _ in xs) for _ in range(nboot))[int(alpha * nboot)]


def _fit_metric(conn, metric, a):
    recs = _runtime_served_center(conn, metric)
    cities_out = {}
    served_cells = []
    for city in sorted(recs):
        rc = recs[city]
        pairs = [(c, s) for _, c, s in rc]
        n = len(pairs)
        if n < a.min_days:
            cities_out[city] = {"a": 0.0, "b": 1.0, "serve": False, "tier": None, "n": n,
                                "bias_c": (round(statistics.mean(s - c for c, s in pairs), 3) if pairs else None),
                                "loo_dmse": None, "loo_dmse_lcb95": None}
            continue
        A, B = fit_affine(pairs, kappa=a.kappa)          # served coefficients (all runtime days)
        loo = _loo_dmse(pairs, a.kappa)
        oos = statistics.mean(loo)
        lcb = _lower_ci(loo)
        is_identity = (abs(A) < 1e-9 and abs(B - 1.0) < 1e-9)
        serve = bool((not is_identity) and oos > 0.0 and lcb >= 0.0)
        tier = "production" if serve else ("canary" if (not is_identity and oos > 0.0) else None)
        cities_out[city] = {
            "a": round(A, 5), "b": round(B, 5), "serve": serve, "tier": tier, "n": n,
            "bias_c": round(statistics.mean(s - c for c, s in pairs), 3),
            "loo_dmse": round(oos, 4), "loo_dmse_lcb95": round(lcb, 4) if math.isfinite(lcb) else None,
        }
        if serve:
            served_cells += loo
    pooled = statistics.mean(served_cells) if served_cells else 0.0
    validation = {
        "served_pooled_loo_dmse": round(pooled, 4),
        "n_served": sum(1 for v in cities_out.values() if v["serve"]),
        "n_cities": len(cities_out),
        "median_days_per_city": statistics.median([len(rc) for rc in recs.values()]) if recs else 0,
    }
    return cities_out, validation


def main() -> int:
    ap = argparse.ArgumentParser(description="Fit affine EMOS center calibration on the runtime served center.")
    ap.add_argument("--kappa", type=float, default=DEFAULT_KAPPA)
    ap.add_argument("--min-days", type=int, default=12, help="min served runtime days/city to attempt a fit.")
    ap.add_argument("--metric", default="both", choices=["high", "low", "both"])
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--disabled", action="store_true", help="write enabled=false (kill switch OFF).")
    a = ap.parse_args()

    conn = get_forecasts_connection_read_only()
    metrics = ["high", "low"] if a.metric == "both" else [a.metric]
    metrics_out, validations = {}, {}
    for m in metrics:
        cities_out, validation = _fit_metric(conn, m, a)
        metrics_out[m] = {"cities": cities_out}
        validations[m] = validation
        print(f"=== EMOS affine (basis=runtime served center, metric={m}, κ={a.kappa}) ===")
        print(f"served {validation['n_served']}/{validation['n_cities']}  pooled LOO ΔMSE="
              f"{validation['served_pooled_loo_dmse']:+.4f}  median_days/city={validation['median_days_per_city']:.0f}")
        for city, d in sorted(cities_out.items(), key=lambda kv: -(kv[1]["loo_dmse"] or -9)):
            if d["serve"]:
                print(f"  {city:14s} a={d['a']:>+7.2f} b={d['b']:>7.3f} bias={d['bias_c']:>+6.2f} LOO_ΔMSE={d['loo_dmse']:>+8.4f} n={d['n']}")

    artifact = {
        "authority": ARTIFACT_AUTHORITY,
        "enabled": (not a.disabled),
        "basis": "runtime_served_center: forecast_posteriors.anchor_value_c (the value that feeds q)",
        "model": "affine_ngr_center: mu' = a + b*mu_served (per-unit, shrunk-to-identity, slope-clamped)",
        "validation": "leave-one-out OOS dMSE, 95% lower-CI >= 0 (runtime history ~20 days/city)",
        "kappa": a.kappa, "min_days": a.min_days,
        "metrics": metrics_out, "validation_by_metric": validations,
    }
    if a.dry_run:
        print("\n[dry-run] artifact NOT written.")
    else:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2, sort_keys=True)
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
