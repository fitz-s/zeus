#!/usr/bin/env python3
# Lifecycle: created=2026-06-05; last_reviewed=2026-06-05; last_reused=2026-06-05
# Purpose: Fit the EMPIRICAL settlement σ-floor table from VERIFIED settlement_outcomes.
# Reuse: inspect architecture/script_manifest.yaml and review the generated JSON before enabling.
# Authority basis: q=1.000 investigation 2026-06-05; EMPIRICAL settlement σ-floor, iron rule 5
#   (overconfidence = ruin). The EMOS σ-model σ = √exp(c + d·logS² + e·lead) is SYSTEMICALLY
#   under-dispersed (median σ_emos/σ_settled = 0.49 across 66% of EMOS-served cells). The correct
#   dispersion floor is the EMPIRICAL settlement std per (city, season, metric) — but the NAÏVE raw
#   same-season std OVER-widens by conflating the intra-season warming trend, so we use the
#   DETRENDED residual std of a trailing window. Output feeds src.calibration.emos.settlement_sigma_floor
#   which floors σ universally at k·σ_settled (k=0.8): max() only WIDENS σ → lower q_lcb → fewer
#   overconfident bets; can NEVER tighten or create a wrong-side trade.
#
# READ-ONLY over state/zeus-forecasts.db.settlement_outcomes (authority='VERIFIED' only — Fitz
# constraint #4: UNVERIFIED/QUARANTINED data does not enter the computation chain). Writes
# state/settlement_sigma_floor.json. Run as new settlements arrive (recommend daily, post-settlement).
"""Fit the EMPIRICAL settlement σ-floor table.

Per (city, season, metric):
  - season key = NH month-season (DJF/MAM/JJA/SON) via the same month-only convention as
    src.calibration.emos.emos_season / fit_emos_calibration.season — NOT hemisphere-aware (a
    lat-flipped season would key the OPPOSITE-season cell for SH cities).
  - σ_settled_floor = the DETRENDED std of settled values in a trailing 45-day CROSS-SEASON window
    per (city, metric): subtract a linear-in-day fit, take the residual std. Detrending removes the
    intra-window warming/cooling trend that would otherwise inflate the std (the investigation proved
    the raw same-season std over-widens).
  - For a (city, season, metric) with ≥ MIN_IN_SEASON in-season settled days, use the in-season
    DETRENDED std; else fall back to the 45-day cross-season detrended std (validated by the
    investigation). Require ≥ MIN_N points overall, else OMIT the cell (no floor → caller keeps σ).

All values are stored in °C.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sqlite3
from collections import defaultdict

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FCST_DEFAULT = os.path.join(REPO, "state", "zeus-forecasts.db")
OUT_DEFAULT = os.path.join(REPO, "state", "settlement_sigma_floor.json")

# Window + thresholds (investigation 2026-06-05).
WINDOW_DAYS_DEFAULT = 45          # trailing cross-season window for the detrended-std fallback
MIN_IN_SEASON_DEFAULT = 8         # ≥ this many in-season days → use the in-season detrended std
MIN_N_DEFAULT = 10                # overall minimum sample → else OMIT the cell (no floor)
K_DEFAULT = 0.8                   # σ_eff = max(model_σ, k·σ_settled)


def to_c(v, u):
    """Convert a settlement value to °C. Matches fit_emos_calibration.to_c."""
    if v is None:
        return None
    u = (u or "").upper()
    if u in ("F", "DEGF"):
        return (v - 32.0) * 5.0 / 9.0
    if u == "K":
        return v - 273.15
    return v  # C / DEGC / blank → assume °C


def season(mm) -> str:
    """NH month-season (DJF/MAM/JJA/SON). Identical to emos_season / fit_emos_calibration.season."""
    mm = int(mm)
    return "DJF" if mm in (12, 1, 2) else "MAM" if mm in (3, 4, 5) else "JJA" if mm in (6, 7, 8) else "SON"


def detrended_std(days: "np.ndarray", values: "np.ndarray") -> float:
    """Residual std of ``values`` after removing a LINEAR-in-day trend.

    A linearly-trending series has a large RAW std (driven by the trend) but a small RESIDUAL std
    (the day-to-day noise about the trend). The σ-floor must be the residual std: the trend is a
    deterministic seasonal march, NOT forecast uncertainty, so it must not inflate the dispersion
    floor (the investigation proved the raw same-season std over-widens).

    Fits ``value ≈ a + b·day`` by least squares, returns the two-parameter residual standard error
    (sqrt(SSE / (n - 2))). For < 3 points or a degenerate design (all days equal) the trend is
    unidentifiable → falls back to the raw std.
    """
    d = np.asarray(days, dtype=float)
    v = np.asarray(values, dtype=float)
    n = v.size
    if n < 2:
        return 0.0
    if n < 3 or float(np.ptp(d)) == 0.0:
        # cannot identify a slope with <3 points or zero day-spread → raw std (best available)
        return float(np.std(v, ddof=1))
    # least-squares linear fit value ~ a + b*day
    A = np.vstack([np.ones_like(d), d]).T
    coef, *_ = np.linalg.lstsq(A, v, rcond=None)
    resid = v - A @ coef
    # ddof=2: two parameters (intercept, slope) are consumed by the fit, so the residual std
    # uses (n-2) degrees of freedom: sqrt(SSE / (n-2)).
    return float(np.sqrt(float(np.sum(resid ** 2)) / max(n - 2, 1)))


def _load_settlements(fcst_path: str):
    """Return {(city, metric): [(date, value_c), ...]} for VERIFIED settlements only, sorted by date.

    Fitz constraint #4: only authority='VERIFIED' rows enter the chain; UNVERIFIED/QUARANTINED are
    excluded. Values are converted to °C via the row's settlement_unit.
    """
    con = sqlite3.connect(f"file:{fcst_path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT city, target_date, temperature_metric, settlement_value, settlement_unit "
            "FROM settlement_outcomes "
            "WHERE authority = 'VERIFIED' AND settlement_value IS NOT NULL"
        )
        rows = cur.fetchall()
    finally:
        con.close()
    out: dict = defaultdict(list)
    for city, tdate, metric, val, unit in rows:
        if not city or not tdate or not metric:
            continue
        vc = to_c(val, unit)
        if vc is None or not np.isfinite(vc):
            continue
        try:
            d = _dt.date.fromisoformat(str(tdate)[:10])
        except ValueError:
            continue
        out[(city, str(metric).lower())].append((d, float(vc)))
    for key in out:
        out[key].sort(key=lambda t: t[0])
    return out


def fit_floors(
    settlements: dict,
    *,
    asof: _dt.date,
    window_days: int = WINDOW_DAYS_DEFAULT,
    min_in_season: int = MIN_IN_SEASON_DEFAULT,
    min_n: int = MIN_N_DEFAULT,
) -> dict:
    """Compute {cell_key: {sigma_floor_c, n, window}} for every (city, season, metric) cell.

    For each (city, metric):
      - Take the trailing ``window_days`` cross-season window ending at ``asof``.
      - The CROSS-SEASON detrended std is the fallback floor for every season of that (city, metric).
      - For each season with ≥ ``min_in_season`` in-window in-season days, prefer the IN-SEASON
        detrended std. A season with < ``min_n`` points (in-season or via fallback) is OMITTED.
    """
    cells: dict = {}
    window_start = asof - _dt.timedelta(days=window_days)
    epoch = window_start  # day-0 reference for the linear-in-day fit
    for (city, metric), recs in settlements.items():
        win = [(d, v) for (d, v) in recs if window_start <= d <= asof]
        if len(win) < min_n:
            continue
        win_days = np.array([(d - epoch).days for d, _ in win], dtype=float)
        win_vals = np.array([v for _, v in win], dtype=float)
        cross_std = detrended_std(win_days, win_vals)
        if not (cross_std > 0.0) or not np.isfinite(cross_std):
            continue
        # group the in-window points by NH month-season
        by_season: dict = defaultdict(list)
        for d, v in win:
            by_season[season(d.month)].append((d, v))
        for seas in ("DJF", "MAM", "JJA", "SON"):
            pts = by_season.get(seas, [])
            if len(pts) >= min_in_season:
                sd = np.array([(d - epoch).days for d, _ in pts], dtype=float)
                sv = np.array([v for _, v in pts], dtype=float)
                in_std = detrended_std(sd, sv)
                if in_std > 0.0 and np.isfinite(in_std):
                    cells[f"{city}|{seas}|{metric}"] = {
                        "sigma_floor_c": round(float(in_std), 4),
                        "n": int(len(pts)),
                        "window": f"in-season-detrended-{window_days}d",
                    }
                    continue
            # fallback: cross-season detrended std (only if the overall window met min_n above)
            cells[f"{city}|{seas}|{metric}"] = {
                "sigma_floor_c": round(float(cross_std), 4),
                "n": int(len(win)),
                "window": f"cross-season-detrended-{window_days}d",
            }
    return cells


def main() -> int:
    ap = argparse.ArgumentParser(description="Fit the EMPIRICAL settlement σ-floor table (#q1000 2026-06-05).")
    ap.add_argument("--fcst", default=FCST_DEFAULT, help="zeus-forecasts.db path (settlement_outcomes).")
    ap.add_argument("--out", default=OUT_DEFAULT, help="output settlement_sigma_floor.json path.")
    ap.add_argument("--asof", default=None, help="as-of date YYYY-MM-DD (default: today / max settled date).")
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS_DEFAULT)
    ap.add_argument("--min-in-season", type=int, default=MIN_IN_SEASON_DEFAULT)
    ap.add_argument("--min-n", type=int, default=MIN_N_DEFAULT)
    ap.add_argument("--k", type=float, default=K_DEFAULT, help="k_default written to _meta (σ_eff = max(σ, k·σ_settled)).")
    args = ap.parse_args()

    settlements = _load_settlements(args.fcst)
    if not settlements:
        print(f"[sigma-floor] no VERIFIED settlements in {args.fcst} — refusing to write empty table")
        return 2

    if args.asof:
        asof = _dt.date.fromisoformat(args.asof)
    else:
        # use the latest settled date present (so a stale clock doesn't drop the freshest window)
        asof = max(d for recs in settlements.values() for d, _ in recs)

    cells = fit_floors(
        settlements,
        asof=asof,
        window_days=args.window_days,
        min_in_season=args.min_in_season,
        min_n=args.min_n,
    )
    table = {
        "_meta": {
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "method": "detrended-settlement-std",
            "k_default": float(args.k),
            "window_days": int(args.window_days),
            "min_in_season": int(args.min_in_season),
            "min_n": int(args.min_n),
            "asof": asof.isoformat(),
            "authority": "settlement_sigma_floor_v1",
            "source": "settlement_outcomes(authority=VERIFIED)",
        },
        "cells": cells,
    }
    # atomic write (tmp + os.replace), the Zeus state-update convention
    tmp = f"{args.out}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=2, sort_keys=True)
    os.replace(tmp, args.out)

    print(f"[sigma-floor] wrote {len(cells)} cells -> {args.out} (asof={asof}, window={args.window_days}d, k={args.k})")
    # report a few reference cells the investigation pinned
    refs = ["Tel Aviv|JJA|high", "Paris|MAM|high", "Milan|MAM|high", "Paris|JJA|high",
            "Singapore|MAM|high", "London|JJA|high"]
    for key in refs:
        c = cells.get(key)
        if c:
            print(f"    {key:24s} σ_settled={c['sigma_floor_c']:.3f}°C  k·σ={args.k * c['sigma_floor_c']:.3f}°C  "
                  f"n={c['n']}  [{c['window']}]")
        else:
            print(f"    {key:24s} (omitted — < min_n or no window)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
