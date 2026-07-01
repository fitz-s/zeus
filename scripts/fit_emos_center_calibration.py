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
import subprocess
import sys
import tempfile
from collections import defaultdict

DECISION_LEAD = 1  # day-ahead: the primary traded decision lead (target_date − cycle_date, days)


def _source_commit():
    """Best-effort git HEAD for artifact provenance; None if unavailable (never raises)."""
    try:
        return subprocess.check_output(
            ["git", "-C", REPO, "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip() or None
    except Exception:
        return None


def _atomic_write_json(path, obj):
    """Write JSON via temp-file + fsync + os.replace so a concurrent reader never sees a partial
    file (a partial read fail-softs to identity, which would transiently switch the layer OFF)."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from src.calibration.emos import bin_probability_settlement  # noqa: E402
from src.calibration.emos_center_calibration import (  # noqa: E402
    ARTIFACT_AUTHORITY,
    apply_affine,
    apply_affine_in_support,
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


def _pool_bydate(recs, min_days):
    """recs {city:[(date,center,obs)]} -> pool {city:{date:(center,settle)}} for cities with
    >= min_days INDEPENDENT dates (the honest unit)."""
    return {c: {d: (x, s) for d, x, s in rc} for c, rc in recs.items() if len(rc) >= min_days}


def _global_date_lodo(pool):
    """Leave-one-GLOBAL-date-out: hold out a whole target_date across ALL cities, refit EB on the
    rest, score each city's held-date cell. Removes the cross-city EB-prior leakage a per-(city,date)
    fold leaves (the held date still informs the prior via other cities). {city: [per-date ΔMSE]}."""
    dates = sorted({d for c in pool for d in pool[c]})
    per_city = {c: [] for c in pool}
    for D in dates:
        eb = fit_affine_eb({c: [v for d, v in pool[c].items() if d != D] for c in pool})
        for c in pool:
            if D in pool[c]:
                x, s = pool[c][D]
                fa, fb = eb.get(c, (0.0, 1.0))
                per_city[c].append((s - x) ** 2 - (s - apply_affine(x, fa, fb)) ** 2)
    return per_city


def _reselect(pool, train_dates, min_days):
    """Refit EB + reselect served cities using ONLY train_dates (inner global-date LOO gate).
    {city:(a,b)} for the cities production WOULD serve on this training set."""
    sub = {c: {d: pool[c][d] for d in train_dates if d in pool[c]} for c in pool}
    sub = {c: v for c, v in sub.items() if len(v) >= min_days}
    if len(sub) < 3:
        return {}
    eb = fit_affine_eb({c: list(v.values()) for c, v in sub.items()})
    inner = _global_date_lodo(sub)
    selected = {}
    for c in sub:
        cells = inner.get(c, [])
        A, B = eb.get(c, (0.0, 1.0))
        if len(cells) >= 3 and not (abs(A) < 1e-9 and abs(B - 1.0) < 1e-9) \
                and statistics.mean(cells) > 0.0 and _date_block_lcb(cells) >= 0.0:
            selected[c] = (A, B)
    return selected


def _nested_portfolio(pool, min_days):
    """NESTED selection validation — the honest LAYER-enable gate (consult BLOCKER). Outer
    leave-one-GLOBAL-date-out; inside each fold RESELECT cities on the training dates only, apply
    that selected policy to the held date, pool ΔMSE over ALL cities' held cells (equal-weight
    portfolio). Bootstrap the per-date portfolio ΔMSE. The statistic is 'the policy that would have
    been selected WITHOUT this held block', not 'mean ΔMSE among cities selected on the full data'.
    Returns (mean, lcb95, median_reselected)."""
    dates = sorted({d for c in pool for d in pool[c]})
    port, counts = [], []
    for D in dates:
        selected = _reselect(pool, [x for x in dates if x != D], min_days)
        counts.append(len(selected))
        num, den = 0.0, 0
        for c in pool:
            if D in pool[c]:
                x, s = pool[c][D]
                den += 1
                if c in selected:
                    a, b = selected[c]
                    num += (s - x) ** 2 - (s - apply_affine(x, a, b)) ** 2
        if den:
            port.append(num / den)
    if len(port) < 3:
        return 0.0, float("-inf"), 0
    return statistics.mean(port), _date_block_lcb(port), (statistics.median(counts) if counts else 0)


def _served_sigma(conn, metric):
    """(city, date) -> the served settlement-facing σ at the day-ahead lead (settlement_sigma_floor_c,
    else anchor_sigma_c) — the σ the TRADED bin-q integrates with. Latest computed_at per cell."""
    latest = {}
    for r in conn.execute(
        "SELECT city,target_date,source_cycle_time,computed_at,provenance_json "
        "FROM forecast_posteriors WHERE temperature_metric=?", (metric,)
    ):
        city, td, sct, ca, prov = r[0], r[1], r[2], r[3], r[4]
        try:
            if (datetime.date.fromisoformat(td) - datetime.date.fromisoformat(str(sct)[:10])).days != DECISION_LEAD:
                continue
            p = json.loads(prov)
            sig = p.get("settlement_sigma_floor_c") or p.get("anchor_sigma_c")
        except Exception:
            sig = None
        if sig is None or float(sig) <= 0.0:
            continue
        k = (city, td)
        if k not in latest or ca > latest[k][0]:
            latest[k] = (ca, float(sig))
    return {k: v[1] for k, v in latest.items()}


def _decision_nested_portfolio(pool, sigma_map, min_days):
    """THE production gate (consult Q6 / live-score-mismatch): the DECISION-level nested test. Same
    nested global-date reselection as _nested_portfolio, but the score is the TRADED bin-q's log-loss
    on the REAL settled bin — identity μ vs corrected μ' both integrated by bin_probability_settlement
    at σ. ΔlogLoss>0 means the correction makes the traded probability sharper on the outcome that
    actually settled. Equal-weight portfolio over ALL cells (unselected contribute 0). center-MSE is
    necessary-not-sufficient; a live-capital correction must not harm this. (Served cities are non-HK
    → wmo_half_up integer bins.) Returns (mean, lcb95)."""
    dates = sorted({d for c in pool for d in pool[c]})
    port = []
    for D in dates:
        selected = _reselect(pool, [x for x in dates if x != D], min_days)
        num, den = 0.0, 0
        for c in pool:
            if D not in pool[c]:
                continue
            den += 1
            if c not in selected:
                continue
            sig = sigma_map.get((c, D))
            if sig is None or sig <= 0.0:
                continue
            mu, settle = pool[c][D]
            centers = [m for m, _ in pool[c].values()]
            a, b = selected[c]
            mu2 = apply_affine_in_support(mu, a, b, min(centers), max(centers))
            X = float(round(settle))
            q0 = min(max(bin_probability_settlement(mu, sig, X, X), 1e-9), 1.0 - 1e-9)
            q1 = min(max(bin_probability_settlement(mu2, sig, X, X), 1e-9), 1.0 - 1e-9)
            num += (-math.log(q0)) - (-math.log(q1))     # >0: correction lowers traded log-loss
        if den:
            port.append(num / den)
    if len(port) < 3:
        return 0.0, float("-inf")
    return statistics.mean(port), _date_block_lcb(port)


def _fit_metric(conn, metric, a):
    recs = _runtime_served_center(conn, metric)          # city -> [(date, center, obs)], one/date
    pool = _pool_bydate(recs, a.min_days)                # {city: {date: (center, settle)}}
    sigma_map = _served_sigma(conn, metric)
    eb_full = fit_affine_eb({c: list(v.values()) for c, v in pool.items()})
    per_city = _global_date_lodo(pool)                   # leak-free: leave-one-GLOBAL-date-out

    # center-MSE nested is necessary; the DECISION-level nested (traded-q log-loss on the real settled
    # bin) is the SUFFICIENT live-capital gate. The layer serves ONLY if the DECISION gate lcb>0.
    port_mean, port_lcb, med_resel = _nested_portfolio(pool, a.min_days)
    dec_mean, dec_lcb = _decision_nested_portfolio(pool, sigma_map, a.min_days)
    layer_ok = dec_lcb > 0.0

    cities_out = {}
    served_cells = []
    n_dates_all = []
    for city in sorted(recs):
        rc = recs[city]
        n_dates = len(rc)
        n_dates_all.append(n_dates)
        bias = round(statistics.mean(s - x for _, x, s in rc), 3) if rc else None
        if city not in pool:
            cities_out[city] = {"a": 0.0, "b": 1.0, "serve": False, "tier": None, "n_dates": n_dates,
                                "bias_c": bias, "x_lo": None, "x_hi": None,
                                "lodo_dmse": None, "lodo_dmse_lcb95": None}
            continue
        A, B = eb_full.get(city, (0.0, 1.0))
        centers = [x for x, _ in pool[city].values()]
        x_lo, x_hi = (round(min(centers), 3), round(max(centers), 3)) if centers else (None, None)
        cells = per_city.get(city, [])
        oos = statistics.mean(cells) if cells else 0.0
        lcb = _date_block_lcb(cells)
        is_identity = (abs(A) < 1e-9 and abs(B - 1.0) < 1e-9)
        city_ok = (not is_identity) and oos > 0.0 and lcb >= 0.0
        serve = bool(layer_ok and city_ok)               # serve ONLY if the layer passed nested selection
        tier = "production" if serve else ("canary" if city_ok else None)
        cities_out[city] = {
            "a": round(A, 5), "b": round(B, 5), "serve": serve, "tier": tier,
            "n_dates": n_dates, "bias_c": bias, "x_lo": x_lo, "x_hi": x_hi,
            "lodo_dmse": round(oos, 4), "lodo_dmse_lcb95": round(lcb, 4) if math.isfinite(lcb) else None,
        }
        if serve:
            served_cells += cells
    validation = {
        "layer_enabled": layer_ok,
        "gate": "decision_nested_logloss_lcb95>0 (traded-q log-loss on the real settled bin, held-out)",
        "decision_nested_logloss_dmse": round(dec_mean, 4),
        "decision_nested_logloss_lcb95": round(dec_lcb, 4) if math.isfinite(dec_lcb) else None,
        "nested_portfolio_dmse": round(port_mean, 4),
        "nested_portfolio_lcb95": round(port_lcb, 4) if math.isfinite(port_lcb) else None,
        "nested_median_reselected": med_resel,
        "served_pooled_global_date_dmse": round(statistics.mean(served_cells), 4) if served_cells else 0.0,
        "n_served": sum(1 for v in cities_out.values() if v["serve"]),
        "n_canary": sum(1 for v in cities_out.values() if v["tier"] == "canary"),
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
        metrics_out[m] = {"cities": cities_out, "served_lead": DECISION_LEAD}
        validations[m] = validation
        print(f"=== EMOS affine EB (basis=day-ahead served center, one/date, metric={m}) ===")
        print(f"LAYER {'ENABLED' if validation['layer_enabled'] else 'DISABLED'} by DECISION gate: "
              f"traded-q log-loss ΔdMSE={validation['decision_nested_logloss_dmse']:+.4f} "
              f"lcb95={validation['decision_nested_logloss_lcb95']} "
              f"(center-MSE nested lcb95={validation['nested_portfolio_lcb95']}, median reselected={validation['nested_median_reselected']:.0f})")
        print(f"served {validation['n_served']}/{validation['n_cities']} (canary {validation['n_canary']})  "
              f"global-date served-pooled ΔMSE={validation['served_pooled_global_date_dmse']:+.4f}  "
              f"median_dates/city={validation['median_dates_per_city']:.0f}")
        for city, d in sorted(cities_out.items(), key=lambda kv: -(kv[1]["lodo_dmse"] or -9)):
            if d["serve"]:
                print(f"  {city:14s} a={d['a']:>+7.2f} b={d['b']:>7.3f} bias={d['bias_c']:>+6.2f} "
                      f"gLODO_ΔMSE={d['lodo_dmse']:>+8.4f} range=[{d['x_lo']},{d['x_hi']}] dates={d['n_dates']}")

    tr = conn.execute(
        "SELECT MIN(target_date), MAX(target_date) FROM forecast_posteriors"
    ).fetchone()
    artifact = {
        "authority": ARTIFACT_AUTHORITY,
        "enabled": (not a.disabled),
        "served_lead": DECISION_LEAD,
        "basis": "runtime_served_center: forecast_posteriors.anchor_value_c, day-ahead lead, one/date (the value that feeds q)",
        "model": "affine_ngr_center: mu' = a + b*mu_served (per-unit, empirical-Bayes shrink-to-identity, DATA-DERIVED, no kappa/clamp)",
        "validation": "per-city leave-one-GLOBAL-date-out; LAYER served only when NESTED selection portfolio lower-CI > 0",
        "selection_protocol": "nested global-date: reselect cities inside each outer held-date fold; enable if portfolio lcb95 > 0",
        "support_guard": "apply_affine_in_support clamps mu to [x_lo,x_hi] (observed range) before the affine",
        "lead_guard": "served ONLY at served_lead; other leads return identity",
        "min_days": a.min_days,
        "source_commit": _source_commit(),
        "training_date_range": [tr[0], tr[1]] if tr else None,
        "metrics": metrics_out, "validation_by_metric": validations,
    }
    if a.dry_run:
        print("\n[dry-run] artifact NOT written.")
    else:
        _atomic_write_json(a.out, artifact)
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
