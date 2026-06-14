#!/usr/bin/env python3
# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis:
#   docs/evidence/investigation_2026-06-13/cold_bias_metadata_root.md (ROOT = per-city 9km
#     grid-cell-vs-settlement-station representativeness offset, −2.18°C Tokyo … +2.48°C
#     Karachi, two-sign, lead-stable, raw-anchor-resident — NOT a global constant).
#   docs/evidence/investigation_2026-06-13/percity_corrected_oos.md (per-city δ is the right
#     SHAPE but OVERFITS when fit on the thin live single_runs anchor: n_prior 1–4 → net-worse.
#     The decisive missing ingredient is HISTORY DEPTH, not a better estimator).
#   operator law 2026-06-12 "没有一个人可以在没有数学支持下决定一个 hard coded value":
#     δ_city MUST be FITTED, never operator-picked / hardcoded; persisted as an auditable,
#     re-fittable artifact (mirrors state/sigma_scale_fit.json, state/bias_scale_fit.json).
#   IRON RULE #3 / SPEC §5 (no-leak): residual basis is the SAME (anchor − VERIFIED settlement)
#     the live walk-forward de-bias uses (src/data/bayes_precision_fusion_history_provider.py).
#
# WHAT THIS FITS — a per-city representativeness de-bias δ_city, SAFE on thin data:
#   δ_city = EB-shrunk robust per-city offset of (OpenMeteo IFS9 anchor_c − settlement_c),
#   fit on the FULL previous_runs anchor history (n = 23..890 settled rows/city, vs the live
#   single_runs anchor's ~6 dates that overfit). Two guards make it do-no-harm:
#     (1) ACTIVATION GUARD: δ_city is `activated` ONLY when n >= N_MIN (the per-city mean's SE
#         is then small). Below N_MIN the live family-level de-bias is used (the materializer
#         falls back — no per-city shift).
#     (2) EMPIRICAL-BAYES SHRINK toward 0 by the offset's OWN SE: δ_city = λ·median, where
#         λ = τ²/(τ²+SE²) (τ² = between-city dispersion of the true offset). Thin/noisy cities
#         shrink gently toward 0; well-sampled cities (SE→0) get λ→1, full correction.
#
# READ-ONLY w.r.t. live DBs (mode=ro&immutable=1); writes ONLY the JSON artifact. No live-path
# change here. Re-run as history grows; review the artifact + the walk-forward report before the
# operator wires/promotes it (the materializer loader is fail-soft and activation-gated).
"""Fit the per-city anchor representativeness de-bias δ_city (the law-8 foundation fix).

Sign convention (matches the materializer's `bias_shift_c` contract `corrected = raw - shift`):
  δ_city = median(anchor_c - settlement_c)  [EB-shrunk]
  so a COLD-biased anchor (anchor < settlement → δ<0) yields corrected = raw - δ = raw + |δ|
  (warms), and a HOT-biased anchor (δ>0) cools. The materializer subtracts δ_city from the raw
  OM9 anchor center BEFORE fusion, so the de-bias propagates into the fused posterior μ*.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FCST = os.path.join(REPO, "state", "zeus-forecasts.db")
OUT = os.path.join(REPO, "state", "anchor_representativeness_debias.json")

ANCHOR_MODEL = "ecmwf_ifs"          # the OpenMeteo IFS9 deterministic anchor that feeds fusion.
ENDPOINT = "previous_runs"          # fixed-lead train history (SPEC §3) — the DEEP history.
# N_MIN: the activation threshold. Picked from the data so the per-city mean's SE is small:
# at the observed per-city residual sd (~1.3–3.1°C), n=30 gives SE ≈ sd/√30 ≈ 0.24–0.57°C,
# i.e. the offset estimate is resolved to well under one 1°C settlement bin. Below this we
# fall back to the family-level de-bias (do no harm). Operator-overridable via --n-min.
N_MIN_DEFAULT = 30
N_FIT_MIN = 8       # need >=8 rows even to estimate a city's median/SE for the τ² pool.


def _settlement_to_celsius(value: float, unit: str | None) -> float:
    """F settlement → °C before the residual (anchor forecast_value_c is always °C)."""
    return (float(value) - 32.0) * 5.0 / 9.0 if (unit or "").upper() == "F" else float(value)


def _gather_residuals(con: sqlite3.Connection, metric: str) -> dict[str, list[float]]:
    """Per-city list of (anchor_c − settlement_c) over the FULL previous_runs VERIFIED history.

    Lead-POOLED: the cold_bias root proves the representativeness offset is lead-stable (it is a
    spatial grid-cell-vs-station displacement, not a forecast-skill artifact), so pooling all
    leads maximizes power without confounding. Same no-leak gate as the live history provider:
    endpoint='previous_runs', settlement authority='VERIFIED'.
    """
    cur = con.cursor()
    cur.row_factory = sqlite3.Row
    rows = cur.execute(
        """
        SELECT r.city AS city,
               r.forecast_value_c AS fc,
               s.settlement_value AS sv,
               s.settlement_unit AS su
        FROM raw_model_forecasts AS r
        JOIN settlement_outcomes AS s
          ON s.city = r.city
         AND s.target_date = r.target_date
         AND s.temperature_metric = r.metric
        WHERE r.metric = ?
          AND r.model = ?
          AND r.endpoint = ?
          AND s.authority = 'VERIFIED'
          AND s.settlement_value IS NOT NULL
        """,
        (metric, ANCHOR_MODEL, ENDPOINT),
    ).fetchall()
    out: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        try:
            res = float(r["fc"]) - _settlement_to_celsius(r["sv"], r["su"])
        except Exception:
            continue
        if math.isfinite(res):
            out[r["city"]].append(res)
    return out


def _fit_metric(residuals: dict[str, list[float]], n_min: int) -> dict:
    """EB-shrunk, activation-guarded δ_city table for one metric family.

    τ² (between-city dispersion of the TRUE offset) is the variance of the per-city robust
    medians across cities with >= N_FIT_MIN samples. EB shrink: λ_city = τ²/(τ²+SE²_city).
    """
    medians = {c: statistics.median(v) for c, v in residuals.items() if len(v) >= N_FIT_MIN}
    pool = list(medians.values())
    tau2 = statistics.pvariance(pool) if len(pool) > 1 else 1.0
    cities: dict[str, dict] = {}
    for city, v in residuals.items():
        n = len(v)
        med = statistics.median(v)
        mean = statistics.mean(v)
        sd = statistics.pstdev(v) if n > 1 else 0.0
        se = sd / math.sqrt(n) if n > 0 else float("inf")
        denom = tau2 + se * se
        lam = (tau2 / denom) if denom > 0 else 0.0
        delta = lam * med  # shrink the robust median toward 0 by its own SE
        activated = bool(n >= n_min and math.isfinite(delta))
        cities[city] = {
            "delta_c": round(float(delta), 4),
            "median_raw_c": round(float(med), 4),
            "mean_raw_c": round(float(mean), 4),
            "n": int(n),
            "sd_c": round(float(sd), 4),
            "se_c": round(float(se), 4) if math.isfinite(se) else None,
            "lambda_shrink": round(float(lam), 4),
            "activated": activated,
        }
    return {
        "tau2_between_city": round(float(tau2), 4),
        "tau_between_city": round(float(math.sqrt(tau2)), 4),
        "n_min": int(n_min),
        "n_cities": len(cities),
        "n_activated": sum(1 for e in cities.values() if e["activated"]),
        "cities": dict(sorted(cities.items())),
    }


def _walk_forward_report(con: sqlite3.Connection, metric: str, n_min: int) -> dict:
    """Strict expanding-window check: fit δ_city on rows with target_date < cut, score the
    held-out tail's |anchor − settlement| with vs without the (activated) correction.

    do-no-harm evidence: corrected MAE should be <= raw MAE on the activated cities, and the
    thin (<N_min, non-activated) cities are unchanged by construction.
    """
    cur = con.cursor()
    cur.row_factory = sqlite3.Row
    rows = cur.execute(
        """
        SELECT r.city AS city, r.target_date AS td,
               r.forecast_value_c AS fc, s.settlement_value AS sv, s.settlement_unit AS su
        FROM raw_model_forecasts AS r
        JOIN settlement_outcomes AS s
          ON s.city=r.city AND s.target_date=r.target_date AND s.temperature_metric=r.metric
        WHERE r.metric=? AND r.model=? AND r.endpoint=?
          AND s.authority='VERIFIED' AND s.settlement_value IS NOT NULL
        ORDER BY r.target_date
        """,
        (metric, ANCHOR_MODEL, ENDPOINT),
    ).fetchall()
    recs = []
    for r in rows:
        try:
            res = float(r["fc"]) - _settlement_to_celsius(r["sv"], r["su"])
        except Exception:
            continue
        if math.isfinite(res):
            recs.append((str(r["td"]), r["city"], res))
    if not recs:
        return {"status": "NO_DATA"}
    dates = sorted({d for d, _, _ in recs})
    cut = dates[int(len(dates) * 0.7)] if len(dates) > 3 else dates[-1]
    train, test = defaultdict(list), defaultdict(list)
    for d, c, res in recs:
        (train if d < cut else test)[c].append(res)
    medians = {c: statistics.median(v) for c, v in train.items() if len(v) >= N_FIT_MIN}
    pool = list(medians.values())
    tau2 = statistics.pvariance(pool) if len(pool) > 1 else 1.0
    raw_abs, corr_abs, n_act_cells = [], [], 0
    for c, vs in test.items():
        tv = train.get(c, [])
        n = len(tv)
        if n >= N_FIT_MIN:
            med = statistics.median(tv)
            sd = statistics.pstdev(tv) if n > 1 else 0.0
            se = sd / math.sqrt(n) if n > 0 else float("inf")
            denom = tau2 + se * se
            lam = (tau2 / denom) if denom > 0 else 0.0
            delta = lam * med
            activated = n >= n_min
        else:
            delta, activated = 0.0, False
        for res in vs:
            raw_abs.append(abs(res))
            corr_abs.append(abs(res - delta) if activated else abs(res))
            if activated:
                n_act_cells += 1
    return {
        "status": "OK",
        "cut_date": cut,
        "n_test_cells": len(raw_abs),
        "n_activated_test_cells": n_act_cells,
        "raw_mae_c": round(statistics.mean(raw_abs), 4) if raw_abs else None,
        "corrected_mae_c": round(statistics.mean(corr_abs), 4) if corr_abs else None,
        "do_no_harm": (statistics.mean(corr_abs) <= statistics.mean(raw_abs) + 1e-9)
        if raw_abs else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Fit per-city anchor representativeness de-bias δ_city.")
    ap.add_argument("--db", default=FCST, help="zeus-forecasts.db path")
    ap.add_argument("--out", default=OUT, help="output artifact path")
    ap.add_argument("--n-min", type=int, default=N_MIN_DEFAULT, help="activation threshold (min settled rows/city)")
    ap.add_argument("--metrics", nargs="+", default=["high", "low"], help="metric families to fit")
    args = ap.parse_args()

    uri = f"file:{os.path.abspath(args.db)}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True, timeout=25)
    try:
        families: dict[str, dict] = {}
        reports: dict[str, dict] = {}
        for metric in args.metrics:
            resid = _gather_residuals(con, metric)
            if not resid:
                continue
            fam = _fit_metric(resid, args.n_min)
            fam["fitted"] = True
            fam["walk_forward"] = _walk_forward_report(con, metric, args.n_min)
            families[str(metric).lower()] = fam
            reports[str(metric).lower()] = fam["walk_forward"]
    finally:
        con.close()

    artifact = {
        "_meta": {
            "schema": "anchor_representativeness_debias",
            "authority": "anchor_grid_representativeness_eb_shrunk_v1",
            "created": datetime.now(timezone.utc).isoformat(),
            "anchor_model": ANCHOR_MODEL,
            "endpoint": ENDPOINT,
            "residual": "anchor_forecast_value_c - settlement_value_in_C (VERIFIED)",
            "estimator": "EB-shrunk robust per-city median; lambda = tau^2/(tau^2+SE^2); lead-pooled",
            "activation": f"activated iff n >= n_min ({args.n_min}); else family-level de-bias fallback (no per-city shift)",
            "sign": "delta_c = anchor - settlement; materializer applies corrected = raw - delta_c (bias_shift_c contract)",
            "source": "raw_model_forecasts(previous_runs) JOIN settlement_outcomes(VERIFIED), lead-pooled",
            "promotion": "OPERATOR_GATED — review walk-forward do_no_harm + activation set, then wire the "
            "materializer loader (src/calibration/anchor_representativeness_debias.py) and the activation flag.",
        },
        "families": families,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2)

    print(f"wrote {args.out}")
    for metric, fam in families.items():
        wf = fam.get("walk_forward", {})
        print(
            f"[{metric}] cities={fam['n_cities']} activated={fam['n_activated']} "
            f"tau={fam['tau_between_city']} | WF cut={wf.get('cut_date')} "
            f"raw_mae={wf.get('raw_mae_c')} corr_mae={wf.get('corrected_mae_c')} "
            f"do_no_harm={wf.get('do_no_harm')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
