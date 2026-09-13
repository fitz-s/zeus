#!/usr/bin/env python3
# Lifecycle: created=2026-06-05; last_reviewed=2026-06-10; last_reused=2026-06-10
# Purpose: Fit the EMPIRICAL settlement σ-floor table from FORECAST RESIDUALS (fused center vs
#   VERIFIED settlement), NOT from the climatological std of settled values.
# Authority basis: floor-recalibration 2026-06-10 (/tmp/floor_recal_report.md; /tmp/deep_verify_report.md
#   Verification A); supersedes the 2026-06-05 detrended-settled-value method.
#
# WHY THE REWRITE (Fitz #4 — data provenance category error):
#   The floor exists as an ANTIBODY against fused-σ OVERCONFIDENCE: the predictive sigma claims a
#   q_lcb tighter than realized coverage warrants. The CORRECT lower bound on a residual-calibrated
#   predictive sigma is therefore the EMPIRICAL DISPERSION OF THE FORECAST RESIDUAL
#   r = (settled_value − fused_center) — how far the center actually lands from truth — NOT the
#   climatological day-to-day spread of the settled values. The OLD method computed the detrended
#   std of SETTLED VALUES (e.g. Paris|JJA|high 5.407°C ×0.8 = 4.33°C effective), ~2.9× the empirical
#   1.2-1.5°C forecast residual, inflating the floored predictive sigma and (a) suppressing genuine
#   interior edges everywhere (no-trade contributor) and (b) inflating open-ended catch-all bins (the
#   Paris ≥26 wrong trade; separately neutralised by the catch-all topology invariant a8a1c80536).
#   Flooring a residual-calibrated sigma with a settled-value std is a CATEGORY ERROR — the floor's
#   source semantics ≠ the quantity it floors.
#
# METHOD (the most correct recalibration):
#   1. residual r = (settled_c − fused_center_c) for every posterior whose target_date settled to a
#      VERIFIED outcome, NO-LEAK: source_cycle_time strictly before target_date (lead ≥ 1 day).
#      Source: forecast_posteriors.provenance_json.anchor_value_c ⋈ settlement_outcomes(VERIFIED).
#   2. estimator = MAD-σ ABOUT ZERO = 1.4826 · median(|r|). Robust to large-miss outliers (median,
#      not squared) AND keeps any systematic bias (measured about ZERO, not about the residual mean)
#      — the floor must bound TOTAL miss including bias, since an overconfident σ that ignores a known
#      cold/warm bias is exactly the failure it insures against. The plain std is outlier-inflated; a
#      trimmed std discards the very large-miss tail the floor exists to insure against.
#   3. cohort fallback ladder (data is thin: per city×metric n≈3-6 << robust threshold):
#        TIER 1 city×metric  if n ≥ MIN_COHORT_N → city×metric residual MAD-σ
#        TIER 2 metric pool  else if metric-pool n ≥ MIN_COHORT_N → all-city same-metric MAD-σ
#        TIER 3 global pool   else → all-city all-metric MAD-σ
#      Season is carried in the cell KEY only (the consumer keys city|SEASON|metric); there is < 1
#      season of fused-residual history so season-specific residual pooling is not yet supportable
#      (documented future refinement once ≥ 1 settled year of residuals exists).
#   4. lower bound: max(σ_floor_raw, ABSOLUTE_FLOOR_C=1.0) — the chain law's 1.0°C absolute floor STAYS.
#   5. k_default = 1.0: the residual MAD-σ IS the calibrated floor (no haircut justified — we are no
#      longer shrinking an over-wide climatological std). The consumer formula k·sigma_floor_c is
#      UNCHANGED; only the stored value's meaning and k change. sigma_floor_c stored = the floor itself.
#
# READ-ONLY over state/zeus-forecasts.db (forecast_posteriors + settlement_outcomes, authority=VERIFIED
# only — Fitz #4: UNVERIFIED/DISPUTED do not enter the chain). Writes state/settlement_sigma_floor.json
# via the script's sanctioned atomic-replace path. Run as new settlements arrive (recommend daily).
#
# WINDOW (2026-09-13 — trailing, not cumulative): residuals are drawn from target_date in
# (asof - trailing_days, asof], --trailing-days default 60. A fixed-start cumulative window never
# drops old data, so every refit becomes more dominated by stale residuals over time; verified this
# flips the direction of a refit for at least one city (a fixed-start 2026-06-10..09-12 cumulative
# refit LOWERED Denver's floor against a fresh 08-23..09-12 residual MAD that says it should rise).
# ONE RESIDUAL PER EVENT: forecast_posteriors carries many rows per (city, target_date, metric) —
# one per forecast cycle — so a raw join weights the floor by how often a family was materialized,
# not by evidence. Each event's residual is the MEDIAN over its lead-1 (day-before) cycles, not the
# single cycle closest to settlement — the last cycle of the day is the most-converged (and so
# artificially tightest) anchor by construction, which understates the dispersion an earlier
# same-day decision actually faced (see _load_residuals docstring for the measured effect).
"""Fit the EMPIRICAL settlement σ-floor table from FORECAST RESIDUALS.

Output: state/settlement_sigma_floor.json
  {"_meta": {...}, "cells": {"City|SEASON|metric": {"sigma_floor_c": float, "n": int,
   "cohort_tier": "city_metric|metric|global", "estimator": "mad_sigma_about_zero",
   "window": "<residual target_date span>", "source_query_hash": "<sha256[:16]>"}}}
All values °C. The consumer src.calibration.emos.settlement_sigma_floor floors σ universally at
σ_eff = max(model_σ, k_default·sigma_floor_c); with k_default=1.0 the stored value IS the floor.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sqlite3
from collections import defaultdict

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FCST_DEFAULT = os.path.join(REPO, "state", "zeus-forecasts.db")
OUT_DEFAULT = os.path.join(REPO, "state", "settlement_sigma_floor.json")

# Recalibration thresholds (floor-recalibration 2026-06-10).
MIN_COHORT_N_DEFAULT = 20         # ≥ this many residuals in a cohort → use that cohort's MAD-σ
MIN_GLOBAL_N_DEFAULT = 10         # overall minimum residual sample → else REFUSE (no table)
ABSOLUTE_FLOOR_C = 1.0            # chain law: predictive σ floor never below 1.0°C
K_DEFAULT = 1.0                   # residual MAD-σ IS the floor (no haircut); consumer formula unchanged
MAD_TO_SIGMA = 1.4826            # MAD → σ scale for a Normal (1/Φ⁻¹(0.75))
SEASONS = ("DJF", "MAM", "JJA", "SON")

# TRAILING WINDOW (2026-09-13 — replaces the fixed-start cumulative window). The original window
# (residual-2026-06-10..asof) never dropped old data, so every refit became increasingly dominated
# by stale residuals (verified: a 2026-09-12 cumulative refit DECREASED Denver's floor 1.79->1.49
# against a fresh 08-23..09-12 lead-1..2 residual MAD of 1.71, i.e. the correct direction is UP).
# 60 days is the smallest trailing window that keeps TIER1 (n>=MIN_COHORT_N_DEFAULT) for cities at
# roughly 1-3 forecast leads/day settling most days -- ~60-180 raw residual rows before per-event
# dedup, comfortably above 20 for any city that settles at least ~1 day in 3. It is short enough
# that a settlement-law or station change is felt within weeks, not baked in for a full season.
TRAILING_DAYS_DEFAULT = 60


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

    RETAINED UTILITY (no longer on the floor path): a linearly-trending series has a large RAW std
    (driven by the trend) but a small RESIDUAL std (day-to-day noise about the trend). Fits
    ``value ≈ a + b·day`` by least squares, returns the two-parameter residual standard error
    sqrt(SSE/(n-2)). For < 3 points or a degenerate design falls back to the raw std. Kept because
    downstream observations + the legacy detrend relationship test still import it; the floor itself
    now derives from forecast residuals (mad_sigma_about_zero), not the detrended settled-value std.
    """
    d = np.asarray(days, dtype=float)
    v = np.asarray(values, dtype=float)
    n = v.size
    if n < 2:
        return 0.0
    if n < 3 or float(np.ptp(d)) == 0.0:
        return float(np.std(v, ddof=1))
    A = np.vstack([np.ones_like(d), d]).T
    coef, *_ = np.linalg.lstsq(A, v, rcond=None)
    resid = v - A @ coef
    return float(np.sqrt(float(np.sum(resid ** 2)) / max(n - 2, 1)))


def mad_sigma_about_zero(residuals: "np.ndarray") -> float:
    """Robust σ estimate from forecast residuals: 1.4826 · median(|r|), centred at ZERO.

    THE floor estimator. Median-absolute (not squared) → not blown out by 1-2 large misses; centred
    at ZERO (not the residual mean) so a systematic forecast bias is INCLUDED in the dispersion the
    floor bounds (an overconfident σ that ignores a known cold/warm bias is exactly the overconfidence
    the floor insures against). Returns 0.0 for an empty input.
    """
    r = np.asarray(residuals, dtype=float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return 0.0
    return float(MAD_TO_SIGMA * float(np.median(np.abs(r))))


# Canonical no-leak residual-join SQL. Hashed into provenance so a future session can prove the
# table's lineage. NO-LEAK: source_cycle_time strictly before target_date (the forecast could not
# have seen the settlement). VERIFIED settlements only (Fitz #4). The trailing-window bound is
# pushed into the WHERE clause (not a python post-filter) so the query itself, not just the
# python-side loop, is bounded — the fixed-start cumulative query this replaces scanned the full
# multi-month history on every refit.
_RESIDUAL_QUERY = (
    "SELECT fp.city, fp.temperature_metric, fp.target_date, "
    "       json_extract(fp.provenance_json,'$.anchor_value_c') AS center, "
    "       so.settlement_value, so.settlement_unit, fp.source_cycle_time "
    "FROM forecast_posteriors fp "
    "JOIN settlement_outcomes so "
    "  ON so.city=fp.city AND so.target_date=fp.target_date "
    " AND so.temperature_metric=fp.temperature_metric "
    "WHERE json_extract(fp.provenance_json,'$.anchor_value_c') IS NOT NULL "
    "  AND so.authority='VERIFIED' AND so.settlement_value IS NOT NULL "
    "  AND fp.target_date > ? AND fp.target_date <= ?"
)


def _load_residuals(fcst_path: str, *, asof: _dt.date, trailing_days: int):
    """Return [(city, metric, target_date, residual_c)] for no-leak VERIFIED residual pairs.

    residual = settled_c − fused_center_c, ONE PER SETTLED EVENT: forecast_posteriors carries many
    rows per (city, target_date, metric) — one per forecast cycle — so joining and MAD-ing the raw
    rows weights the floor by how often a family was materialized, not by evidence (a city computed
    every hour dominates one computed daily).

    EVENT REPRESENTATIVE (2026-09-13, revised — R-P review on 2c7e03a75): the event's residual is
    the MEDIAN over its LEAD-1 cycles (source_cycle_time exactly one calendar day before
    target_date), falling back to the median over ALL no-leak cycles for that event when it has no
    lead-1 cycle. An earlier version took the single LATEST no-leak cycle (closest to settlement) as
    the representative — that always resolves to the last lead-1 cycle of the day (each event has
    dozens of forecast_posteriors rows on its lead-1 day), which is the MOST-CONVERGED anchor of the
    day by construction (accuracy improves intraday as the target approaches) and so is measurably
    TIGHTER than the dispersion a decision made earlier that same lead-1 day actually faced — the
    wrong direction for a floor that exists to bound realized miss (verified: dedup-to-latest gave
    Busan 2.11 vs the same-day lead-1-pooled 3.20, LA 1.55 vs 1.82, SF 1.11 vs 1.35, Denver 1.32 vs
    the pooled 1.49 — itself still below the fresh ground-truth MAD of 1.71). The median over the
    day's lead-1 cycles is a single per-event number (no forecast-cycle-count weighting) that is NOT
    an extremum, so it does not inherit the last-cycle's artificially tight convergence.

    TRAILING WINDOW: target_date in (asof − trailing_days, asof] — replaces the old fixed-start
    cumulative window so a refit is dominated by recent regime, not increasingly-stale history.
    NO-LEAK guard: keep only pairs whose source_cycle_time DATE is strictly before the target_date
    (lead ≥ 1 day). Settlement value converted to °C via settlement_unit (Fitz #4: VERIFIED only).
    """
    window_start = asof - _dt.timedelta(days=trailing_days)
    con = sqlite3.connect(f"file:{fcst_path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute(_RESIDUAL_QUERY, (window_start.isoformat(), asof.isoformat()))
        rows = cur.fetchall()
    finally:
        con.close()
    per_event: dict = defaultdict(list)  # (city, metric, target_date) -> [(lead_days, residual_c)]
    for city, metric, tdate, center, sval, sunit, sct in rows:
        if not city or not tdate or not metric or center is None:
            continue
        try:
            td = _dt.date.fromisoformat(str(tdate)[:10])
            cyc = _dt.date.fromisoformat(str(sct)[:10])
        except (ValueError, TypeError):
            continue
        if cyc >= td:            # NO-LEAK: forecast cycle must predate the target date
            continue
        if td > asof or td <= window_start:   # belt-and-suspenders on top of the SQL bound
            continue
        # UNIT DISCIPLINE: the fused center is provenance.anchor_value_c — ALREADY °C by contract
        # (named *_c). ONLY the settlement_value carries settlement_unit and must be converted. A
        # blanket to_c() on the center would F→C-convert an already-°C value for F-settled cities
        # (159 C vs 26 F rows in the live join) and manufacture a spurious ~50°C residual.
        c = float(center)
        s = to_c(float(sval), sunit)
        if s is None or not (np.isfinite(c) and np.isfinite(s)):
            continue
        key = (str(city), str(metric).lower(), td)
        lead_days = (td - cyc).days
        per_event[key].append((lead_days, float(s) - float(c)))
    out: list = []
    for (city, metric, td), items in per_event.items():
        lead1 = [r for (ld, r) in items if ld == 1]
        pool = lead1 if lead1 else [r for (_, r) in items]
        out.append((city, metric, td, float(np.median(np.asarray(pool, dtype=float)))))
    return out


def fit_floors(
    residuals: list,
    *,
    min_cohort_n: int = MIN_COHORT_N_DEFAULT,
) -> dict:
    """Compute {City|SEASON|metric: cell} from forecast residuals via the cohort fallback ladder.

    Cells are emitted for EVERY (city, metric) observed × all four seasons (the consumer keys by the
    target_date's season, so all four must resolve). Each cell's σ_floor_raw is resolved:
      TIER 1 city×metric  if that cohort's n ≥ min_cohort_n
      TIER 2 metric pool  else if the all-city same-metric pool n ≥ min_cohort_n
      TIER 3 global pool   else
    Then max(σ_floor_raw, ABSOLUTE_FLOOR_C). The residual cohort is season-agnostic (< 1 season of
    history); season lives in the key only.
    """
    by_city_metric: dict = defaultdict(list)
    by_metric: dict = defaultdict(list)
    global_pool: list = []
    td_min: _dt.date | None = None
    td_max: _dt.date | None = None
    for city, metric, td, r in residuals:
        by_city_metric[(city, metric)].append(r)
        by_metric[metric].append(r)
        global_pool.append(r)
        td_min = td if td_min is None else min(td_min, td)
        td_max = td if td_max is None else max(td_max, td)
    window = (
        f"residual-{td_min.isoformat()}..{td_max.isoformat()}"
        if td_min and td_max
        else "residual-empty"
    )
    global_arr = np.asarray(global_pool, dtype=float)
    global_sigma = mad_sigma_about_zero(global_arr)

    def _resolve(city: str, metric: str) -> tuple[float, int, str]:
        cm = by_city_metric.get((city, metric), [])
        if len(cm) >= min_cohort_n:
            return mad_sigma_about_zero(np.asarray(cm, dtype=float)), len(cm), "city_metric"
        mp = by_metric.get(metric, [])
        if len(mp) >= min_cohort_n:
            return mad_sigma_about_zero(np.asarray(mp, dtype=float)), len(mp), "metric"
        return global_sigma, int(global_arr.size), "global"

    cells: dict = {}
    for (city, metric) in sorted(by_city_metric.keys()):
        sigma_raw, n, tier = _resolve(city, metric)
        sigma_floor = max(float(sigma_raw), ABSOLUTE_FLOOR_C)
        for seas in SEASONS:
            cells[f"{city}|{seas}|{metric}"] = {
                "sigma_floor_c": round(sigma_floor, 4),
                "n": int(n),
                "cohort_tier": tier,
                "estimator": "mad_sigma_about_zero",
                "window": window,
            }
    return cells


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fit the EMPIRICAL settlement σ-floor table from FORECAST RESIDUALS (recal 2026-06-10)."
    )
    ap.add_argument("--fcst", default=FCST_DEFAULT, help="zeus-forecasts.db (forecast_posteriors + settlement_outcomes).")
    ap.add_argument("--out", default=OUT_DEFAULT, help="output settlement_sigma_floor.json path.")
    ap.add_argument("--asof", default=None, help="as-of date YYYY-MM-DD (default: today; settlements after it are dropped).")
    ap.add_argument("--min-cohort-n", type=int, default=MIN_COHORT_N_DEFAULT)
    ap.add_argument("--min-global-n", type=int, default=MIN_GLOBAL_N_DEFAULT)
    ap.add_argument("--k", type=float, default=K_DEFAULT, help="k_default written to _meta (σ_eff = max(σ, k·sigma_floor_c)).")
    ap.add_argument(
        "--trailing-days", type=int, default=TRAILING_DAYS_DEFAULT,
        help="residual window is (asof - trailing_days, asof] (default 60; replaces the old fixed-start cumulative window).",
    )
    args = ap.parse_args()

    asof = _dt.date.fromisoformat(args.asof) if args.asof else _dt.date.today()
    window_start = asof - _dt.timedelta(days=args.trailing_days)
    residuals = _load_residuals(args.fcst, asof=asof, trailing_days=args.trailing_days)
    if len(residuals) < args.min_global_n:
        print(
            f"[sigma-floor] only {len(residuals)} no-leak VERIFIED residual pairs in {args.fcst} "
            f"(< min_global_n={args.min_global_n}) — refusing to write a low-confidence table"
        )
        return 2

    cells = fit_floors(residuals, min_cohort_n=args.min_cohort_n)

    # source_query_hash: lineage proof (canonical residual SQL + asof + trailing_days). Stamped on
    # every cell.
    qhash = hashlib.sha256(
        (_RESIDUAL_QUERY + f"|asof={asof.isoformat()}|trailing_days={args.trailing_days}").encode("utf-8")
    ).hexdigest()[:16]
    for cell in cells.values():
        cell["source_query_hash"] = qhash

    global_arr = np.asarray([r for *_, r in residuals], dtype=float)
    table = {
        "_meta": {
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "method": "forecast-residual-mad-sigma",
            "estimator": "mad_sigma_about_zero",
            "k_default": float(args.k),
            "min_cohort_n": int(args.min_cohort_n),
            "absolute_floor_c": float(ABSOLUTE_FLOOR_C),
            "asof": asof.isoformat(),
            "trailing_days": int(args.trailing_days),
            "window": f"trailing-{args.trailing_days}d:{window_start.isoformat()}..{asof.isoformat()}",
            "authority": "settlement_sigma_floor_v2_residual",
            "source": "forecast_posteriors.anchor_value_c ⋈ settlement_outcomes(authority=VERIFIED), no-leak, one-per-event",
            "source_query_hash": qhash,
            "residual_pairs_total": int(global_arr.size),
            "residual_mean_c": round(float(np.mean(global_arr)), 4),
            "residual_std_c": round(float(np.std(global_arr, ddof=1)), 4) if global_arr.size > 1 else None,
            "global_mad_sigma_c": round(mad_sigma_about_zero(global_arr), 4),
        },
        "cells": cells,
    }
    tmp = f"{args.out}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=2, sort_keys=True)
    os.replace(tmp, args.out)

    print(
        f"[sigma-floor] wrote {len(cells)} cells -> {args.out} "
        f"(asof={asof}, residual_pairs={global_arr.size}, global_mad_sigma="
        f"{mad_sigma_about_zero(global_arr):.3f}°C, k={args.k})"
    )
    refs = ["Tel Aviv|JJA|high", "Paris|MAM|high", "Milan|JJA|high", "Paris|JJA|high",
            "London|JJA|high", "Paris|JJA|low"]
    for key in refs:
        c = cells.get(key)
        if c:
            print(f"    {key:22s} σ_floor={c['sigma_floor_c']:.3f}°C  n={c['n']}  [tier={c['cohort_tier']}]")
        else:
            print(f"    {key:22s} (omitted — no residual pairs for this city/metric)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
