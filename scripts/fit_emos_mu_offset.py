#!/usr/bin/env python3
# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: pr408 review C1+C2 #3 HIGH (2026-06-14): ENFORCE the one-signed μ-offset
#   contract — activation now additionally requires offset_c<0 AND every walk-forward train
#   delta used for OOS <0, and persists offset_sign_ok + median_residual_c. Plus:
#   D4 emos_mu_bias_probe.md (the EMOS-served μ* lands COLD for per-city cold cities:
#   Tokyo|MAM median −1.89°C, n verified-settled) + law 8 (the live center must be airport-settlement
#   honest). Discriminating probe (scripts/probe_emos_mu_correction_D4.py, run 2026-06-14): applying
#   the S2 grid-representativeness de-bias to x̄ BEFORE the EMOS formula OVER-corrects every city by
#   ~+3°C (b·offset) and WORSENS CRPS — the EMOS intercept `a` ALREADY absorbed the grid-cold offset
#   at fit time, so an x̄-side de-bias double-counts. The mechanism that EARNS it OOS is a
#   residual-grounded μ-OFFSET measured DIRECTLY on (μ*_EMOS − settlement) — equivalent to recalibrating
#   the per-cell EMOS intercept on clean VERIFIED settlement, with a walk-forward do-no-harm gate.
#
# WHAT THIS IS NOT: NOT the broken previous_runs-vs-single_runs anchor-offset fitter
#   (anchor_representativeness_debias / the SHADOW replacement lane). That measures the IFS single-run
#   ANCHOR residual and feeds the fusion prior, which does NOT reach the live EMOS calibrator. THIS
#   measures the LIVE EMOS μ* (a + b·x̄_ensemble, the center the doc proved is served) against VERIFIED
#   settlement, and corrects ONLY the EMOS center.
#
# METHOD (settlement-residual-grounded, WALK-FORWARD, embargoed, no leakage):
#   1. Reconstruct μ* = a + b·x̄ per (city, target_date, high) from ensemble_snapshots
#      (contributes_to_target_extrema=1, shortest genuine-forecast lead in [24,144]h, members→°C by
#      per-city members_unit) using the LIVE emos_calibration.json params. settled_c from
#      settlement_outcomes(authority=VERIFIED) via settlement_unit→°C. residual r = μ* − settled_c.
#   2. NO-LEAK: target_date ≤ asof; the EMOS lead is a genuine forecast (≥24h) so μ* could not see
#      the settlement. The OFFSET δ_cell = median(r over the cell's residuals ≤ asof) — robust to
#      large-miss outliers; a constant per-cell shift (intercept recal), NOT a per-day fit.
#   3. WALK-FORWARD DO-NO-HARM GATE per (city,season): for each settled day i, fit δ on strictly-prior
#      embargoed residuals (≥ MIN_TRAIN), score B0 (no correction) vs C (μ−δ) by |mean residual| AND
#      Gaussian CRPS on the held-out day i. A cell is `activated` ONLY when, over ≥ MIN_OOS held-out
#      days: |mean OOS residual| improves by ≥ RES_MARGIN AND mean OOS CRPS improves by ≥ CRPS_MARGIN.
#      This is the GATE — a cold cell whose correction does not EARN it OOS stays unactivated (today's
#      behavior, fail-closed).
#   4. MATERIALITY: only cells with mean(r) < COLD_THRESHOLD (−0.5°C) over n ≥ MIN_N are candidates.
#      EMOS-absorbed cities (residual ≈ 0) and WARM cells (residual > 0) are NEVER corrected — the
#      offset is one-signed-honest: we only WARM a measured-cold center, never cool a warm one here
#      (a warm overshoot like SF|JJA is a SEPARATE anomaly, explicitly out of scope per the doc).
#   5. The stored δ uses ALL residuals ≤ asof (the gate validated the SHAPE on walk-forward OOS; the
#      deployed estimate uses the full clean history for the lowest-variance shift). EB-style guard:
#      activation requires the per-cell mean SE to be small (n ≥ MIN_N) so a thin cell cannot activate.
#
# READ-ONLY over state/zeus-forecasts.db (ensemble_snapshots + settlement_outcomes, VERIFIED only) and
# state/emos_calibration.json. Writes state/emos_mu_offset.json via atomic replace. Run as new
# settlements arrive (recommend daily, after the σ-floor fit).
"""Fit the per-(city,season,metric) EMOS μ-offset correction table from settlement residuals.

Output: state/emos_mu_offset.json
  {"_meta": {...},
   "cells": {"City|SEASON|metric": {"offset_c": float, "n": int, "mean_residual_c": float,
             "activated": bool, "oos": {"n": int, "res_before": float, "res_after": float,
             "crps_before": float, "crps_after": float}, "window": str}}}
All values °C. The consumer src.calibration.emos.emos_mu_offset returns offset_c for an `activated`
cell; build_emos_q applies μ_corr = μ* − offset_c (a cold center, offset_c<0, is WARMED). Unactivated
or absent cell → None → no correction (today's behavior; fail-closed).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import sqlite3
from collections import defaultdict

import numpy as np
from scipy.stats import norm

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("ZEUS_STATE_DIR", os.path.join(REPO, "state"))
DB_DEFAULT = os.path.join(STATE, "zeus-forecasts.db")
EMOS_DEFAULT = os.path.join(STATE, "emos_calibration.json")
OUT_DEFAULT = os.path.join(STATE, "emos_mu_offset.json")

# Gate / materiality thresholds.
COLD_THRESHOLD = -0.5      # only a measured-cold cell (mean residual < this) is a candidate
MIN_N = 8                  # adequate settled n for a cell-level verdict (mean SE small enough)
MIN_TRAIN = 8              # walk-forward: min strictly-prior embargoed residuals to fit δ
MIN_OOS = 5                # require this many held-out days before activation (thin → fail-closed)
RES_MARGIN = 0.15          # |OOS residual| must improve by at least this (°C) to count
CRPS_MARGIN = 0.01         # OOS mean CRPS must improve by at least this (°C) to count
EMBARGO_DAYS = 1           # forecast cycle/observation embargo (EMOS lead is ≥24h already)
LEAD_MIN_H, LEAD_MAX_H = 24.0, 144.0


def season(mm) -> str:
    mm = int(mm)
    return "DJF" if mm in (12, 1, 2) else "MAM" if mm in (3, 4, 5) else "JJA" if mm in (6, 7, 8) else "SON"


def to_c(v, u):
    if v is None:
        return None
    u = (u or "").upper()
    if u in ("F", "DEGF"):
        return (v - 32.0) * 5.0 / 9.0
    if u == "K":
        return v - 273.15
    return v


def crps_gaussian(mu: float, sigma: float, y: float) -> float:
    if sigma <= 0:
        return abs(mu - y)
    z = (y - mu) / sigma
    return float(sigma * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1.0 / math.sqrt(math.pi)))


# Canonical residual reconstruction is in Python (μ* = a + b·x̄ needs the per-cell EMOS params +
# per-city member-unit handling), so we hash the SQL + asof + the emos table digest into provenance.
_SETTLE_SQL = (
    "SELECT city,target_date,temperature_metric,settlement_value,settlement_unit "
    "FROM settlement_outcomes WHERE authority='VERIFIED' AND settlement_value IS NOT NULL"
)
_ENS_SQL = (
    "SELECT city,target_date,temperature_metric,lead_hours,members_json,members_unit "
    "FROM ensemble_snapshots WHERE contributes_to_target_extrema=1"
)


def load_rows(db_path: str, emos_cells: dict, *, asof: _dt.date, metric: str = "high"):
    """Return [{city,season,date,mu,sig,settled}] no-leak VERIFIED residual records for `metric`."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute(_SETTLE_SQL)
        settle = {}
        for c, d, m, v, u in cur.fetchall():
            if m != metric:
                continue
            sc = to_c(float(v), u)
            if sc is not None:
                settle[(c, str(d)[:10])] = sc
        cur.execute(_ENS_SQL)
        best = {}
        for c, d, m, lh, mj, mu_unit in cur.fetchall():
            if m != metric:
                continue
            try:
                lh = float(lh)
            except (TypeError, ValueError):
                continue
            if not (LEAD_MIN_H <= lh <= LEAD_MAX_H):
                continue
            key = (c, str(d)[:10])
            if key not in best or lh < best[key][0]:
                best[key] = (lh, mj, mu_unit)
    finally:
        con.close()

    rows = []
    for (c, d), (lh, mj, mu_unit) in best.items():
        if (c, d) not in settle:
            continue
        try:
            td = _dt.date.fromisoformat(d)
        except (ValueError, TypeError):
            continue
        if td > asof:
            continue
        try:
            members = np.asarray(json.loads(mj), dtype=float)
        except Exception:
            continue
        if members.size < 2:
            continue
        if (str(mu_unit or "")).strip().lower() in ("degf", "f"):
            members = (members - 32.0) / 1.8
        xbar = float(np.mean(members))
        s2 = max(float(np.var(members, ddof=1)), 1e-6)
        seas = season(td.month)
        cell = emos_cells.get(f"{c}|{seas}|{metric}")
        if not cell or cell.get("served") != "emos":
            continue
        try:
            a, b, cc, dd, ee = (float(x) for x in cell["params"])
        except Exception:
            continue
        mu = a + b * xbar
        try:
            sig = math.sqrt(math.exp(cc + dd * math.log(s2) + ee * (lh / 24.0)))
        except (ValueError, OverflowError):
            continue
        if not (sig > 0.0):
            continue
        rows.append({"city": c, "season": seas, "date": td, "mu": mu, "sig": sig,
                     "settled": settle[(c, d)]})
    rows.sort(key=lambda r: r["date"])
    return rows


def gate_cell(cr: list) -> dict:
    """Walk-forward do-no-harm gate for one (city,season) cell's residual records.

    Returns dict(activated, offset_c, n, mean_residual_c, median_residual_c, offset_sign_ok,
    oos{...}). δ = median(all residuals); activated only when ALL hold:
      * the cell is materially cold (mean residual < COLD_THRESHOLD) with n ≥ MIN_N;
      * the walk-forward OOS shows the offset reduces |mean residual| by ≥ RES_MARGIN AND mean
        CRPS by ≥ CRPS_MARGIN over ≥ MIN_OOS held-out days, without over-correcting; AND
      * ONE-SIGNED CONTRACT (pr408 review C1+C2 #3 HIGH, 2026-06-14): the stored offset is
        STRICTLY NEGATIVE (offset_c < 0 — only a cold center is WARMED; an offset ≥ 0 would
        COOL the center, the wrong direction) AND EVERY walk-forward TRAIN delta used for the
        OOS evaluation is < 0. A skewed cell (cold MEAN but non-negative MEDIAN, or a train
        window whose median flipped warm) is rejected — the correction must never push a
        center the wrong way. ``offset_sign_ok`` records whether the stored offset is < 0.
    """
    cr = sorted(cr, key=lambda r: r["date"])
    n = len(cr)
    res_all = np.array([r["mu"] - r["settled"] for r in cr], dtype=float)
    mean_res = float(res_all.mean()) if n else 0.0
    offset = float(np.median(res_all)) if n else 0.0  # robust per-cell shift (intercept recal)
    # ONE-SIGNED: only a strictly-cold (negative) median offset is a legitimate WARMing shift.
    offset_sign_ok = bool(offset < 0.0)

    out = {
        "offset_c": round(offset, 4),
        "n": int(n),
        "mean_residual_c": round(mean_res, 4),
        # median_residual_c == the stored offset (δ = median residual); persisted explicitly so
        # the one-signed contract is auditable from the artifact without recomputation.
        "median_residual_c": round(offset, 4),
        "offset_sign_ok": offset_sign_ok,
        "activated": False,
        "oos": None,
    }
    if n < MIN_N or mean_res >= COLD_THRESHOLD:
        return out  # not materially cold / too thin → never activate (fail-closed, leave alone)

    b0_res, c_res, b0_crps, c_crps = [], [], [], []
    train_deltas: list[float] = []
    for i, r in enumerate(cr):
        train = [p for p in cr[:i] if (r["date"] - p["date"]).days >= EMBARGO_DAYS]
        if len(train) < MIN_TRAIN:
            continue
        delta = float(np.median([p["mu"] - p["settled"] for p in train]))
        train_deltas.append(delta)
        y = r["settled"]
        b0_res.append(r["mu"] - y)
        c_res.append((r["mu"] - delta) - y)
        b0_crps.append(crps_gaussian(r["mu"], r["sig"], y))
        c_crps.append(crps_gaussian(r["mu"] - delta, r["sig"], y))

    if len(c_res) < MIN_OOS:
        return out  # not enough held-out evaluations → fail-closed

    res_before = float(np.mean(b0_res))
    res_after = float(np.mean(c_res))
    crps_before = float(np.mean(b0_crps))
    crps_after = float(np.mean(c_crps))
    # ONE-SIGNED: every train delta that actually warmed a held-out day must be a COLD (<0)
    # shift. If any OOS train window's median flipped warm, the offset is not one-signed-honest.
    all_train_deltas_cold = bool(train_deltas) and all(d < 0.0 for d in train_deltas)
    out["oos"] = {
        "n": len(c_res),
        "res_before": round(res_before, 4),
        "res_after": round(res_after, 4),
        "crps_before": round(crps_before, 4),
        "crps_after": round(crps_after, 4),
        "all_train_deltas_cold": all_train_deltas_cold,
    }
    improves_res = abs(res_after) <= abs(res_before) - RES_MARGIN
    improves_crps = crps_after <= crps_before - CRPS_MARGIN
    # Anti-overcorrection: the corrected OOS residual must not FLIP to a larger-magnitude warm bias.
    not_overcorrected = abs(res_after) < abs(res_before)
    out["activated"] = bool(
        improves_res
        and improves_crps
        and not_overcorrected
        and offset_sign_ok               # stored offset strictly cold
        and all_train_deltas_cold        # every OOS train delta strictly cold
    )
    return out


def fit(rows: list) -> dict:
    by_cs = defaultdict(list)
    for r in rows:
        by_cs[(r["city"], r["season"])].append(r)
    cells = {}
    td_min = min((r["date"] for r in rows), default=None)
    td_max = max((r["date"] for r in rows), default=None)
    window = (f"residual-{td_min.isoformat()}..{td_max.isoformat()}"
              if td_min and td_max else "residual-empty")
    for (city, seas), cr in sorted(by_cs.items()):
        res = gate_cell(cr)
        res["window"] = window
        cells[f"{city}|{seas}|high"] = res
    return cells


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fit the EMOS μ-offset correction table from VERIFIED settlement residuals (D4)."
    )
    ap.add_argument("--db", default=DB_DEFAULT)
    ap.add_argument("--emos", default=EMOS_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--asof", default=None, help="as-of date YYYY-MM-DD (default: today).")
    ap.add_argument("--metric", default="high")
    args = ap.parse_args()

    asof = _dt.date.fromisoformat(args.asof) if args.asof else _dt.date.today()
    emos_cells = json.load(open(args.emos))["cells"]
    rows = load_rows(args.db, emos_cells, asof=asof, metric=args.metric)
    if not rows:
        print(f"[mu-offset] no no-leak VERIFIED residual rows in {args.db} — refusing to write a table")
        return 2

    cells = fit(rows)
    activated = {k: v for k, v in cells.items() if v.get("activated")}

    qhash = hashlib.sha256(
        (_SETTLE_SQL + "||" + _ENS_SQL + f"|asof={asof.isoformat()}|metric={args.metric}").encode()
    ).hexdigest()[:16]
    for cell in cells.values():
        cell["source_query_hash"] = qhash

    res_all = np.array([r["mu"] - r["settled"] for r in rows], dtype=float)
    table = {
        "_meta": {
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "method": "emos-mu-settlement-residual-median; walk-forward do-no-harm OOS gate",
            "model": "mu_corr = (a + b*xbar) - offset_c   (cold center offset_c<0 is WARMED)",
            "authority": "emos_mu_offset_v1_residual",
            "source": "ensemble_snapshots(contributes_to_target_extrema=1) reconstructed μ* "
                      "⋈ settlement_outcomes(authority=VERIFIED), no-leak (lead≥24h, target_date≤asof)",
            "cold_threshold_c": COLD_THRESHOLD,
            "min_n": MIN_N,
            "min_train": MIN_TRAIN,
            "min_oos": MIN_OOS,
            "res_margin_c": RES_MARGIN,
            "crps_margin_c": CRPS_MARGIN,
            "embargo_days": EMBARGO_DAYS,
            "lead_window_h": [LEAD_MIN_H, LEAD_MAX_H],
            "asof": asof.isoformat(),
            "metric": args.metric,
            "source_query_hash": qhash,
            "residual_pairs_total": int(res_all.size),
            "residual_mean_c": round(float(np.mean(res_all)), 4),
            "cells_total": len(cells),
            "cells_activated": len(activated),
        },
        "cells": cells,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    tmp = f"{args.out}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=2, sort_keys=True)
    os.replace(tmp, args.out)

    print(f"[mu-offset] wrote {len(cells)} cells ({len(activated)} ACTIVATED) -> {args.out} "
          f"(asof={asof}, residual_pairs={res_all.size})")
    for k, v in sorted(activated.items()):
        o = v["oos"]
        print(f"    ACTIVATE {k:22s} offset={v['offset_c']:+.3f}°C n={v['n']:2d}  "
              f"OOS n={o['n']:2d} res {o['res_before']:+.2f}→{o['res_after']:+.2f} "
              f"crps {o['crps_before']:.2f}→{o['crps_after']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
