#!/usr/bin/env python3
# Created: 2026-07-01
# Last audited: 2026-07-01
# Authority basis: EMOS/NGR affine center calibration. Operator sentence #1 —
#   "使用真实参与概率计算的运行态组合数据进行精准的emos设计提升". The 运行态组合数据 IS the served fused
#   center forecast_posteriors.anchor_value_c (the value that actually feeds the live probability q) —
#   NOT a raw model endpoint (previous_runs is ECMWF ifs025 0.25° coarse, single_runs is a raw source).
#   READ-ONLY over state/zeus-forecasts.db; SOLE writer of state/emos_center_calibration.json.
"""Fit the per-city affine EMOS center calibration μ'=a+b·μ on the REAL runtime served center.

Basis: forecast_posteriors.anchor_value_c (the served fused center that feeds the live q) vs the
observed daily extreme (observations, the physical ground truth; venue settlement where present). ONE
served center per (city, target_date) at the day-ahead DECISION lead (the freshest cycle at that
lead) — the point that actually feeds the primary day-ahead trade. One-per-date because the served
bias grows with lead (a mixed-lead basis misstates it) AND because the many intra-day cycles are
correlated: the honest INDEPENDENT unit is the date, so per-city standard errors and validation are
computed on ~19 dates/city, not ~150 correlated rows.

Shrinkage is EMPIRICAL BAYES — DATA-DERIVED, no hand-set κ and no slope clamp (both were hard-coded
guesses; a city's shrink now follows its own sampling variance + the cross-city spread). Validation
is LEAVE-ONE-DATE-OUT with a date-block bootstrap; a city SERVES only when its OOS ΔMSE has a 95%
lower CI ≥ 0 (per-unit no-harm). A world-class city stays at identity; the served (a,b) sharpens as
the live history accrues.
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import random
import sqlite3
import statistics
import sys
from collections import defaultdict

DECISION_LEAD = 1  # day-ahead: the primary traded decision lead (target_date − cycle_date, days)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from src.calibration.emos_center_calibration import (  # noqa: E402
    ARTIFACT_AUTHORITY,
    apply_affine,
    fit_affine_eb,
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
    # Venue settlement is preferred WHERE PRESENT, but the ground truth does NOT require it: the
    # observed extreme (observations, below) is authoritative and complete on its own. Fail-soft so a
    # DB without settlement_outcomes (or with none for this metric) still yields the observed truth.
    try:
        for r in conn.execute(
            "SELECT city,target_date,settlement_value,settlement_unit FROM settlement_outcomes "
            "WHERE temperature_metric=? AND authority='VERIFIED' AND settlement_value IS NOT NULL", (metric,)
        ):
            truth[(r[0], r[1])] = _to_c(r[2], r[3])
    except sqlite3.OperationalError:
        pass  # no settlement_outcomes table -> observations-only ground truth
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
    """city -> [(target_date, served_center_c, observed_c)] : ONE served center per (city, date) at
    the day-ahead DECISION_LEAD, taken from the LATEST computed_at among that lead's cycles.

    One point per date is the honest INDEPENDENT unit: the served bias grows with lead (measured
    high: L0 −0.22 → L1 +0.22 → L2 +0.38), so a mixed-lead basis misstates the correction; and the
    many intra-day cycles at a date share near-identical centers + identical truth, so treating each
    row as independent deflates the standard errors (the flaw that made a hard clamp look necessary).
    Pinning the day-ahead lead makes the basis lead-consistent AND matches the center that feeds the
    primary traded decision."""
    truth = _observed_ground_truth(conn, metric)
    latest = defaultdict(dict)  # city -> date -> (computed_at, center_c)
    for r in conn.execute(
        "SELECT city,target_date,source_cycle_time,computed_at,provenance_json "
        "FROM forecast_posteriors WHERE temperature_metric=?", (metric,)
    ):
        city, td, sct, ca, prov = r[0], r[1], r[2], r[3], r[4]
        try:
            lead = (datetime.date.fromisoformat(td) - datetime.date.fromisoformat(str(sct)[:10])).days
        except Exception:
            continue
        if lead != DECISION_LEAD:
            continue
        try:
            av = json.loads(prov).get("anchor_value_c")
        except Exception:
            av = None
        if av is None:
            continue
        cur = latest[city].get(td)
        if cur is None or ca > cur[0]:
            latest[city][td] = (ca, float(av))
    recs = defaultdict(list)
    for city, dd in latest.items():
        for td, (ca, av) in dd.items():
            s = truth.get((city, td))
            if s is not None:
                recs[city].append((td, float(av), s))
    for c in recs:
        recs[c].sort()
    return recs


def _date_block_lcb(per_date_dmse, *, alpha=0.05, nboot=2000, seed=5):
    """95% lower CI of mean OOS ΔMSE by bootstrap over DATES. ``per_date_dmse`` = one ΔMSE value per
    held-out date (the independent unit — one served center per date at the decision lead). Resampling
    these values IS resampling dates. -inf if < 3 dates. This is the gate that kills per-row optimism
    (rows within a date are not independent, so a per-row CI over-states confidence)."""
    xs = list(per_date_dmse)
    if len(xs) < 3:
        return float("-inf")
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choice(xs) for _ in xs) for _ in range(nboot))
    return means[int(alpha * nboot)]


def _fit_metric(conn, metric, a):
    recs = _runtime_served_center(conn, metric)          # city -> [(date, center, obs)], one/date
    # Cities with enough INDEPENDENT dates enter the EB pool; others stay identity.
    pool = {c: rc for c, rc in recs.items() if len(rc) >= a.min_days}
    city_pairs = {c: [(x, s) for _, x, s in rc] for c, rc in pool.items()}
    eb_full = fit_affine_eb(city_pairs)                  # DATA-DERIVED shrink (no κ, no clamp)

    # Leave-one-DATE-out: for each (city, held date), refit EB on ALL cities minus that one point,
    # score the held date. EB is cross-city, so the whole pool is refit per fold (honest OOS).
    per_city_cells = {c: [] for c in pool}
    for c, rc in pool.items():
        for hd, xh, sh in rc:
            fold = {}
            for cc, rcc in pool.items():
                pts = [(x, s) for d, x, s in rcc if not (cc == c and d == hd)]
                if pts:
                    fold[cc] = pts
            fa, fb = fit_affine_eb(fold).get(c, (0.0, 1.0))
            per_city_cells[c].append((sh - xh) ** 2 - (sh - apply_affine(xh, fa, fb)) ** 2)

    cities_out = {}
    served_cells = []
    n_dates_all = []
    for city in sorted(recs):
        rc = recs[city]
        n_dates = len(rc)
        n_dates_all.append(n_dates)
        bias = round(statistics.mean(s - x for _, x, s in rc), 3) if rc else None
        if city not in pool:
            cities_out[city] = {"a": 0.0, "b": 1.0, "serve": False, "tier": None,
                                "n_dates": n_dates, "bias_c": bias,
                                "lodo_dmse": None, "lodo_dmse_lcb95": None}
            continue
        A, B = eb_full.get(city, (0.0, 1.0))
        cells = per_city_cells.get(city, [])
        oos = statistics.mean(cells) if cells else 0.0
        lcb = _date_block_lcb(cells)                     # one cell per date -> bootstrap over dates
        is_identity = (abs(A) < 1e-9 and abs(B - 1.0) < 1e-9)
        serve = bool((not is_identity) and oos > 0.0 and lcb >= 0.0)
        tier = "production" if serve else ("canary" if (not is_identity and oos > 0.0) else None)
        cities_out[city] = {
            "a": round(A, 5), "b": round(B, 5), "serve": serve, "tier": tier,
            "n_dates": n_dates, "bias_c": bias,
            "lodo_dmse": round(oos, 4), "lodo_dmse_lcb95": round(lcb, 4) if math.isfinite(lcb) else None,
        }
        if serve:
            served_cells += cells
    pooled = statistics.mean(served_cells) if served_cells else 0.0
    validation = {
        "served_pooled_lodo_dmse": round(pooled, 4),
        "n_served": sum(1 for v in cities_out.values() if v["serve"]),
        "n_cities": len(cities_out),
        "median_dates_per_city": statistics.median(n_dates_all) if n_dates_all else 0,
    }
    return cities_out, validation


def main() -> int:
    ap = argparse.ArgumentParser(description="Fit EB affine EMOS center calibration on the runtime served center.")
    ap.add_argument("--min-days", type=int, default=12,
                    help="min INDEPENDENT served runtime dates/city (one served center/date) to enter the EB pool.")
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
        print(f"=== EMOS affine EB (basis=day-ahead served center, one/date, metric={m}) ===")
        print(f"served {validation['n_served']}/{validation['n_cities']}  pooled LODO ΔMSE="
              f"{validation['served_pooled_lodo_dmse']:+.4f}  median_dates/city={validation['median_dates_per_city']:.0f}")
        for city, d in sorted(cities_out.items(), key=lambda kv: -(kv[1]["lodo_dmse"] or -9)):
            if d["serve"]:
                print(f"  {city:14s} a={d['a']:>+7.2f} b={d['b']:>7.3f} bias={d['bias_c']:>+6.2f} "
                      f"LODO_ΔMSE={d['lodo_dmse']:>+8.4f} dates={d['n_dates']}")

    artifact = {
        "authority": ARTIFACT_AUTHORITY,
        "enabled": (not a.disabled),
        "basis": "runtime_served_center: forecast_posteriors.anchor_value_c, day-ahead lead, one/date (the value that feeds q)",
        "model": "affine_ngr_center: mu' = a + b*mu_served (per-unit, empirical-Bayes shrink-to-identity, DATA-DERIVED, no kappa/clamp)",
        "validation": "leave-one-DATE-out OOS dMSE, date-block-bootstrap 95% lower-CI >= 0 (~19 independent dates/city)",
        "min_days": a.min_days,
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
