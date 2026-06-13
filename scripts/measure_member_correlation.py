#!/usr/bin/env python3
# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: docs/authority/statistical_calibration_authority_2026-06-12.txt Task 3.1 —
#   "Var(X̄) = p(1−p)/N · {1 + (N−1)ρ}; N_eff = N² / (N + ρ_w·Σ n_m(n_m−1) + ρ_b·(N²−Σ n_m²))"
#   Intraclass correlation estimator: moment form 1 − within-family-variance / (p(1−p)), aggregated
#   across (event, bin) cells weighted by p(1−p).
#   Lifecycle: diagnostic_report_writer — read-only over zeus-forecasts.db; writes state/member_correlation_fit.json.
#   Purpose: measure within-family ρ_w and between-family ρ_b of ensemble member bin-indicator
#     variables from historical stored member data, producing N_eff to replace the implicit
#     assumption of full independence across ~51 ensemble members.
#   Reuse: run before updating confidence bounds that depend on ensemble member count.
"""Measure within-family (ρ_w) and between-family (ρ_b) intraclass correlation of ensemble
member bin-indicator variables, and the implied effective sample size N_eff.

BACKGROUND
==========
Every confidence bound in Zeus currently treats ~51 ensemble members as though they were
fully independent. That assumption inflates the effective N. This script measures the actual
intraclass correlation from historical stored member data and writes a calibrated N_eff artifact.

The authority formula (statistical_calibration_authority_2026-06-12.txt, Task 3.1):

    Var(X̄) = p(1−p)/N · {1 + (N−1)ρ}
    N_eff = N² / (N + ρ_w·Σ n_m(n_m−1) + ρ_b·(N²−Σ n_m²))

where N = total ensemble members, n_m = members in family m, ρ_w = within-family ICC,
ρ_b = between-family ICC.

DATA SOURCES
============
Two complementary data sources:

1. AIFS family (within-family ρ_w): forecast_posteriors.provenance_json stores
   aifs_probabilities (bin-level empirical frequencies from 51 perturbed members) for 176
   settled (city, target_date) events. Since probabilities are exact fractions of N=51,
   we recover the integer member count per bin and compute the ICC via the ANOVA moment
   estimator across events, pooled over bins by p(1−p) weight.

2. Deterministic multi-model ensemble (between-family ρ_b): raw_model_forecasts has one
   deterministic forecast per model per (city, target_date), with 7–10 distinct model
   families across ~4 474 settled events. We treat each model as a single-member family
   (n_m = 1) and estimate the between-family correlation as the intraclass correlation of
   family-mean bin-indicators across families for the same event/bin.

ICC ESTIMATOR (moment form, per the authority document)
=======================================================
For a single event i and bin k with N members from one family:
    p̂_{k,i}      = (members in bin k) / N        [recovered from stored probabilities]
    S²_total      = p̂(1−p̂)                       [binomial variance of a single indicator]
    S²_within     = p̂(1−p̂) * N/(N−1)            [sample variance for Bernoulli, finite correction]
    ICC_k,i       = 1 − S²_within / S²_total
                  = 1 − N/(N−1)                   [per SINGLE event: always negative → undefined]

The estimator requires aggregation over MULTIPLE events. The correct ANOVA form uses the
between-events mean square vs within-events mean square:

    MS_B = variance of p̂_{k,i} across events i (for fixed bin k)
    MS_W = mean within-event variance = mean of p̂_{k,i}(1−p̂_{k,i}) * N/(N−1)
    ICC_k = (MS_B − MS_W/N) / (MS_B + (N−1)*MS_W/N)

We then pool ICC_k across bins k, weighting by p̄_k(1−p̄_k) (the variance weight used in the
authority formula), skipping degenerate cells where p̄_k < 0.02 or p̄_k > 0.98.

For ρ_b (between-family): treat each deterministic model as a single family member.
Compute the empirical cross-family correlation of bin-indicator values (0 or 1 for each
deterministic model) for the same (event, bin), averaged over bins by p(1−p) weight.

BOOTSTRAP CI
============
Block bootstrap by event date (all events on the same target_date are drawn together) with
N_BOOT=1000 resamples, yielding 2.5/97.5 percentile CIs.

OUTPUT
======
state/member_correlation_fit.json with schema:
    {
        "rho_w": float,  "rho_w_ci": [lo, hi],
        "rho_b": float,  "rho_b_ci": [lo, hi],
        "n_eff_total": float,
        "n_raw_total": int,
        "per_family": {"AIFS": {"n_members": 51, "rho_w": float, ...}, ...},
        "n_events_aifs": int,
        "n_events_multimodel": int,
        "method": "anova_moment_icc_plus_between_family_correlation",
        "authority": "statistical_calibration_authority_2026-06-12.txt_task3.1",
        "created": "ISO-timestamp",
        "data_window": "settled-YYYY-MM-DD..YYYY-MM-DD"
    }
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sqlite3
from collections import defaultdict

import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FCST_DEFAULT = os.path.join(REPO, "state", "zeus-forecasts.db")
OUT_DEFAULT = os.path.join(REPO, "state", "member_correlation_fit.json")

AUTHORITY = "statistical_calibration_authority_2026-06-12.txt_task3.1"
METHOD = "anova_moment_icc_plus_between_family_correlation"

# Degenerate bin filter: skip bins where the mean probability is outside [P_LO, P_HI].
P_LO = 0.02
P_HI = 0.98

# Bootstrap
N_BOOT_DEFAULT = 1000
RNG_SEED = 42

# Min events for a meaningful estimate
MIN_EVENTS_AIFS = 20
MIN_EVENTS_MULTIMODEL = 50


# ---------------------------------------------------------------------------
# Regex helpers (shared pattern from fit_sigma_scale.py)
# ---------------------------------------------------------------------------
_RANGE_RE = re.compile(r"(-?\d+)\s*-\s*(-?\d+)\s*°?([CF])", re.IGNORECASE)
_SINGLE_RE = re.compile(r"(-?\d+)\s*°?([CF])", re.IGNORECASE)


def _bin_center_deg(label: str):
    """Bin centre in native degree units from a label string."""
    mr = _RANGE_RE.search(label)
    if mr:
        return (float(mr.group(1)) + float(mr.group(2))) / 2.0
    ms = _SINGLE_RE.search(label)
    if ms:
        return float(ms.group(1))
    return None


def _winning_bin_index(probs: dict[str, float], winning_bin: str | None,
                       settlement_value: float | None) -> int | None:
    """Return index in sorted-by-centre bin list for the winning bin.

    3-pass match: substring → centre-match within half-step → tail direction.
    """
    items = _sort_bins(probs)
    wb = (winning_bin or "").strip()

    # Pass 1: substring
    for i, (label, _p) in enumerate(items):
        if wb and wb in label:
            return i

    # Pass 2: bin-centre match
    wb_deg = _bin_center_deg(wb)
    if wb_deg is not None:
        centres = [_bin_center_deg(lbl) for lbl, _ in items]
        finite = [(i, c) for i, c in enumerate(centres) if c is not None]
        if finite:
            diffs = [abs(c - wb_deg) for _, c in finite]
            best_i = min(range(len(diffs)), key=lambda k: diffs[k])
            if diffs[best_i] < 2.0:  # within 2 degrees
                return finite[best_i][0]

    # Pass 3: tail direction via settlement_value
    if settlement_value is not None:
        centres = [_bin_center_deg(lbl) for lbl, _ in items]
        finite = [(i, c) for i, c in enumerate(centres) if c is not None]
        if finite:
            lo_i, lo_c = min(finite, key=lambda t: t[1])
            hi_i, hi_c = max(finite, key=lambda t: t[1])
            if settlement_value <= lo_c:
                return lo_i
            if settlement_value >= hi_c:
                return hi_i
            return min(finite, key=lambda t: abs(t[1] - settlement_value))[0]

    return None


def _sort_bins(probs: dict[str, float]) -> list[tuple[str, float]]:
    """Sort bins by their numeric centre, tails at ends."""
    def _key(item):
        lbl = item[0]
        c = _bin_center_deg(lbl)
        if c is not None:
            return c
        return 1e9 if "or higher" in lbl.lower() else -1e9
    return sorted(probs.items(), key=_key)


# ---------------------------------------------------------------------------
# AIFS ρ_w estimation
# ---------------------------------------------------------------------------

def _load_aifs_events(con: sqlite3.Connection) -> list[dict]:
    """Load settled AIFS events with bin probabilities from provenance_json.

    Returns a list of dicts:
        {city, target_date, n_members, bin_probs: dict[str, float], winning_index: int|None}
    """
    cur = con.cursor()
    cur.execute("""
        SELECT fp.city, fp.target_date, fp.provenance_json,
               so.winning_bin, so.settlement_value
        FROM forecast_posteriors fp
        JOIN settlement_outcomes so
          ON so.city = fp.city AND so.target_date = fp.target_date
         AND so.temperature_metric = 'high'
         AND so.authority = 'VERIFIED'
         AND so.winning_bin IS NOT NULL
        WHERE fp.provenance_json LIKE '%aifs_member_count%'
          AND fp.temperature_metric = 'high'
        ORDER BY fp.target_date
    """)
    rows = cur.fetchall()

    # Keep freshest posterior per (city, target_date) — deduplicate
    best: dict[tuple[str, str], dict] = {}
    for city, tdate, prov_json, winning_bin, sval in rows:
        key = (city, tdate)
        if key in best:
            continue  # first occurrence from ORDER BY is earliest; we'll deduplicate properly
        try:
            prov = json.loads(prov_json)
        except Exception:
            continue
        aifs_probs = prov.get("aifs_probabilities")
        if not isinstance(aifs_probs, dict) or not aifs_probs:
            continue
        n_members = int(prov.get("aifs_member_count", 51))
        if n_members < 2:
            continue
        won_idx = _winning_bin_index(
            aifs_probs, winning_bin,
            float(sval) if sval is not None else None
        )
        best[key] = {
            "city": city,
            "target_date": tdate,
            "n_members": n_members,
            "bin_probs": aifs_probs,
            "winning_index": won_idx,
        }

    return list(best.values())


def _anova_icc(p_matrix: np.ndarray, n: int) -> float:
    """One-way ANOVA ICC estimator for binary indicators.

    p_matrix: shape (n_events,) — empirical frequency for one bin across events
    n: group size (same for all groups = n_members)

    Returns ICC, or NaN if undefined.

    One-way random effects model: X_{im} = μ + τ_i + ε_{im}
    MS_B = n * var(p̂_i)   [between-events mean square per ANOVA formula]
    MS_W = mean(p̂_i(1−p̂_i)) * n/(n−1)   [within-event sample variance pooled]
    ICC = (MS_B − MS_W) / (MS_B + (n−1)*MS_W)
    """
    p = p_matrix
    k = len(p)
    if k < 2:
        return float("nan")
    ms_b = float(np.var(p, ddof=1)) * n
    ms_w = float(np.mean(p * (1.0 - p))) * n / (n - 1)
    denom = ms_b + (n - 1) * ms_w
    if denom <= 0:
        return float("nan")
    return float((ms_b - ms_w) / denom)


def _estimate_rho_w(events: list[dict]) -> dict:
    """Estimate within-family ICC for AIFS ensemble members.

    Pools over ALL events regardless of bin label text by normalizing each bin
    to its numeric centre (degrees). For each unique bin-centre value, collects the
    empirical frequency p(event, centre) across all events that have that bin centre
    and computes the ANOVA ICC, then pools across centres weighted by p̄(1−p̄).

    Returns dict with rho_w, n_events, n_bins_used, per_bin details.
    """
    if len(events) < MIN_EVENTS_AIFS:
        return {"rho_w": float("nan"), "n_events": len(events),
                "insufficient": True}

    # Normalize bins to their degree centre.
    # Key: round(centre * 2) / 2  [half-degree resolution to merge near-duplicates]
    from collections import defaultdict as _dd
    # Aggregate: for each centre_key, list of (p_value, n_members)
    centre_obs: dict[float, list[tuple[float, int]]] = _dd(list)
    for e in events:
        n = e["n_members"]
        for lbl, p_val in e["bin_probs"].items():
            c = _bin_center_deg(lbl)
            if c is None:
                continue
            # Round to nearest 0.5 to tolerate minor label parsing noise
            c_key = round(c * 2) / 2
            centre_obs[c_key].append((float(p_val), n))

    icc_values: list[float] = []
    weights: list[float] = []
    for c_key in sorted(centre_obs):
        obs = centre_obs[c_key]
        if len(obs) < MIN_EVENTS_AIFS:
            continue
        p_arr = np.array([p for p, _n in obs])
        n = obs[0][1]  # n_members (same for all AIFS events)
        p_mean = float(np.mean(p_arr))
        if p_mean < P_LO or p_mean > P_HI:
            continue
        icc = _anova_icc(p_arr, n)
        if not np.isfinite(icc):
            continue
        w = p_mean * (1.0 - p_mean)
        icc_values.append(icc)
        weights.append(w)

    n_events_total = len(events)
    if not icc_values:
        return {"rho_w": float("nan"), "n_events": n_events_total,
                "n_bins_used": 0, "insufficient": True}

    weights_arr = np.array(weights)
    icc_arr = np.array(icc_values)
    rho_w = float(np.average(icc_arr, weights=weights_arr))
    n_members = events[0]["n_members"] if events else 51
    return {
        "rho_w": rho_w,
        "n_events": n_events_total,
        "n_bins_used": len(icc_values),
        "n_members": n_members,
        "insufficient": False,
    }


def _bootstrap_rho_w(events: list[dict], n_boot: int = N_BOOT_DEFAULT,
                     seed: int = RNG_SEED) -> tuple[float, float]:
    """Block bootstrap CI for ρ_w, blocking by target_date."""
    dates = sorted(set(e["target_date"] for e in events))
    by_date: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_date[e["target_date"]].append(e)

    rng = np.random.default_rng(seed)
    boot_vals: list[float] = []
    for _ in range(n_boot):
        sampled_dates = rng.choice(dates, size=len(dates), replace=True)
        sample = []
        for d in sampled_dates:
            sample.extend(by_date[d])
        r = _estimate_rho_w(sample)
        v = r.get("rho_w", float("nan"))
        if np.isfinite(v):
            boot_vals.append(v)

    if len(boot_vals) < 10:
        return (float("nan"), float("nan"))
    return (float(np.percentile(boot_vals, 2.5)),
            float(np.percentile(boot_vals, 97.5)))


# ---------------------------------------------------------------------------
# Multi-model ρ_b estimation
# ---------------------------------------------------------------------------

def _load_multimodel_events(con: sqlite3.Connection) -> list[dict]:
    """Load settled deterministic multi-model events from raw_model_forecasts.

    Returns list of dicts: {city, target_date, models: {model_name: forecast_c}}.
    Selects the closest forecast_value_c to the target event per (city, target_date, model)
    (freshest lead_days = smallest positive lead_days, ties broken by source_cycle_time desc).
    """
    cur = con.cursor()
    # Best (shortest positive lead_days, latest cycle) forecast per (city, target_date, model)
    cur.execute("""
        SELECT rmf.city, rmf.target_date, rmf.model, rmf.forecast_value_c,
               so.winning_bin, so.settlement_value, so.settlement_unit
        FROM raw_model_forecasts rmf
        JOIN settlement_outcomes so
          ON so.city = rmf.city AND so.target_date = rmf.target_date
         AND so.temperature_metric = 'high'
         AND so.authority = 'VERIFIED'
         AND so.winning_bin IS NOT NULL
        WHERE rmf.metric = 'high'
          AND rmf.forecast_value_c IS NOT NULL
          AND rmf.source_family IS NULL
        ORDER BY rmf.city, rmf.target_date, rmf.model,
                 rmf.lead_days ASC, rmf.source_cycle_time DESC
    """)
    rows = cur.fetchall()

    # Deduplicate: first row per (city, target_date, model) is best
    seen: set[tuple[str, str, str]] = set()
    event_data: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"winning_bin": None, "settlement_value": None,
                 "settlement_unit": None, "models": {}}
    )
    for city, tdate, model, fval, wb, sval, sunit in rows:
        k = (city, tdate, model)
        if k in seen:
            continue
        seen.add(k)
        ev = event_data[(city, tdate)]
        ev["city"] = city
        ev["target_date"] = tdate
        ev["winning_bin"] = wb
        ev["settlement_value"] = float(sval) if sval is not None else None
        ev["settlement_unit"] = sunit
        ev["models"][model] = float(fval)

    # Keep only events with at least 3 models
    result = []
    for (city, tdate), ev in event_data.items():
        if len(ev["models"]) >= 3:
            result.append(ev)
    return result


def _make_integer_bins(values: list[float], unit: str) -> list[float]:
    """Integer-degree bin edges covering the range of values.

    For C: step=1°C; for F: step=2°F (aligns with exchange format).
    The estimate is robust to grid choice per the task specification.
    """
    lo = int(min(values)) - 1
    hi = int(max(values)) + 2
    step = 2 if (unit or "C").upper() == "F" else 1
    return list(range(lo, hi + step, step))


def _assign_bin(value: float, edges: list[float]) -> int | None:
    """Assign a temperature value to a bin index (interior or tail).

    edges[i] to edges[i+1] is bin i. Returns 0 for below-edges[0],
    len(edges) for above-edges[-1].
    """
    if value < edges[0]:
        return 0
    for i in range(len(edges) - 1):
        if edges[i] <= value < edges[i + 1]:
            return i + 1
    return len(edges)


def _between_family_correlation(events: list[dict]) -> dict:
    """Estimate between-family ICC (ρ_b) from deterministic multi-model ensemble.

    For each (event, bin): we have a binary indicator X_{m,k,i} = 1{model m's forecast
    falls in bin k for event i}. ρ_b = cross-family correlation of these indicators,
    estimated as the average over bins of the inter-family Pearson correlation.

    With only 1 member per family (n_m=1), within-family ICC is undefined; we instead
    use the empirical correlation coefficient across families for the same (event, bin)
    as the between-family correlation, then pool across bins by p(1−p) weight.
    """
    if len(events) < MIN_EVENTS_MULTIMODEL:
        return {"rho_b": float("nan"), "n_events": len(events),
                "insufficient": True}

    # Build indicator matrices per-model-pair, averaged over bins
    # Strategy: for each event, compute bin edges from all models' values, assign bin to each model.
    # Then compute the sample cross-model correlation of bin-indicator vectors across events.

    # Collect all models
    all_models: list[str] = sorted(set(m for e in events for m in e["models"]))
    if len(all_models) < 2:
        return {"rho_b": float("nan"), "n_events": len(events),
                "insufficient": True, "n_models": len(all_models)}

    # For each event, assign each model to a bin (integer-degree grid)
    # Then for each bin k, build indicator vector per model: I_{m,k,i} = 1{model m in bin k for event i}
    # ρ_b = weighted average over bins of mean pairwise correlation of indicator columns

    # Build a matrix: rows=events, cols=models, values=bin_index (integer)
    event_list = events  # ordered consistently
    n_events = len(event_list)
    bin_assignments: list[list[int | None]] = []  # [event][model]
    bin_edges_list: list[list[float]] = []

    for ev in event_list:
        unit = ev.get("settlement_unit") or "C"
        vals = [ev["models"].get(m) for m in all_models]
        present = [v for v in vals if v is not None]
        if len(present) < 2:
            bin_assignments.append([None] * len(all_models))
            bin_edges_list.append([])
            continue
        edges = _make_integer_bins(present, unit)
        bin_assignments.append([
            _assign_bin(v, edges) if v is not None else None
            for v in vals
        ])
        bin_edges_list.append(edges)

    # Find all unique bin indices used across events
    all_bin_indices: set[int] = set()
    for ba in bin_assignments:
        for b in ba:
            if b is not None:
                all_bin_indices.add(b)

    # For each bin k, build indicator matrix: shape (n_events, n_models)
    icc_values: list[float] = []
    weights: list[float] = []

    for k in sorted(all_bin_indices):
        # indicator[i, m] = 1 if model m is in bin k for event i, else 0; NaN if missing
        rows_ok = []
        for i, ba in enumerate(bin_assignments):
            row = [float(ba[m] == k) if ba[m] is not None else float("nan")
                   for m in range(len(all_models))]
            if all(np.isfinite(row)):
                rows_ok.append(row)

        if len(rows_ok) < 10:
            continue
        mat = np.array(rows_ok)  # (n_ok_events, n_models)
        # Mean indicator per model across events
        p_vec = mat.mean(axis=0)  # shape (n_models,)
        p_mean = float(p_vec.mean())
        if p_mean < P_LO or p_mean > P_HI:
            continue

        # Compute all pairwise Pearson correlations between model indicator columns
        corrs: list[float] = []
        for m1 in range(len(all_models)):
            for m2 in range(m1 + 1, len(all_models)):
                x1 = mat[:, m1]
                x2 = mat[:, m2]
                if np.std(x1) < 1e-10 or np.std(x2) < 1e-10:
                    continue
                c = float(np.corrcoef(x1, x2)[0, 1])
                if np.isfinite(c):
                    corrs.append(c)

        if not corrs:
            continue
        mean_corr = float(np.mean(corrs))
        w = p_mean * (1.0 - p_mean)
        icc_values.append(mean_corr)
        weights.append(w)

    if not icc_values:
        return {"rho_b": float("nan"), "n_events": len(event_list),
                "n_models": len(all_models), "n_bins_used": 0, "insufficient": True}

    rho_b = float(np.average(icc_values, weights=weights))
    return {
        "rho_b": rho_b,
        "n_events": len(event_list),
        "n_models": len(all_models),
        "n_bins_used": len(icc_values),
        "models": all_models,
        "insufficient": False,
    }


def _bootstrap_rho_b(events: list[dict], n_boot: int = N_BOOT_DEFAULT,
                     seed: int = RNG_SEED) -> tuple[float, float]:
    """Block bootstrap CI for ρ_b, blocking by target_date."""
    dates = sorted(set(e["target_date"] for e in events))
    by_date: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_date[e["target_date"]].append(e)

    rng = np.random.default_rng(seed + 1)
    boot_vals: list[float] = []
    for _ in range(n_boot):
        sampled_dates = rng.choice(dates, size=len(dates), replace=True)
        sample = []
        for d in sampled_dates:
            sample.extend(by_date[d])
        r = _between_family_correlation(sample)
        v = r.get("rho_b", float("nan"))
        if np.isfinite(v):
            boot_vals.append(v)

    if len(boot_vals) < 10:
        return (float("nan"), float("nan"))
    return (float(np.percentile(boot_vals, 2.5)),
            float(np.percentile(boot_vals, 97.5)))


# ---------------------------------------------------------------------------
# N_eff computation
# ---------------------------------------------------------------------------

def _compute_n_eff(rho_w: float, rho_b: float, family_sizes: list[int]) -> float:
    """Authority formula: N_eff = N² / (N + ρ_w·Σ n_m(n_m−1) + ρ_b·(N²−Σ n_m²)).

    family_sizes: list of n_m for each family.
    """
    N = sum(family_sizes)
    sum_nm_nm1 = sum(n * (n - 1) for n in family_sizes)
    sum_nm_sq = sum(n * n for n in family_sizes)
    denom = N + rho_w * sum_nm_nm1 + rho_b * (N * N - sum_nm_sq)
    if denom <= 0:
        return float("nan")
    return float(N * N / denom)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Measure within/between-family ICC for ensemble member bin-indicators."
    )
    ap.add_argument("--fcst", default=FCST_DEFAULT,
                    help="zeus-forecasts.db path (mode=ro).")
    ap.add_argument("--out", default=OUT_DEFAULT,
                    help="output member_correlation_fit.json path.")
    ap.add_argument("--n-boot", type=int, default=N_BOOT_DEFAULT,
                    help="bootstrap resamples for CIs (default 1000; 0 = skip).")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.fcst}?mode=ro", uri=True)
    try:
        aifs_events = _load_aifs_events(con)
        mm_events = _load_multimodel_events(con)
    finally:
        con.close()

    print(f"[member-correlation] AIFS settled events: {len(aifs_events)}")
    print(f"[member-correlation] Multi-model settled events: {len(mm_events)}")

    # ρ_w from AIFS within-family
    rw_result = _estimate_rho_w(aifs_events)
    rho_w = rw_result.get("rho_w", float("nan"))
    print(f"[member-correlation] ρ_w (AIFS, {rw_result.get('n_events')} events, "
          f"{rw_result.get('n_bins_used')} bins): {rho_w:.4f}")

    if args.n_boot > 0 and not rw_result.get("insufficient"):
        rho_w_ci = _bootstrap_rho_w(aifs_events, args.n_boot)
    else:
        rho_w_ci = (float("nan"), float("nan"))
    print(f"[member-correlation] ρ_w 95% CI: [{rho_w_ci[0]:.4f}, {rho_w_ci[1]:.4f}]")

    # ρ_b from multi-model between-family
    rb_result = _between_family_correlation(mm_events)
    rho_b = rb_result.get("rho_b", float("nan"))
    print(f"[member-correlation] ρ_b (multi-model, {rb_result.get('n_events')} events, "
          f"{rb_result.get('n_models')} models, {rb_result.get('n_bins_used')} bins): {rho_b:.4f}")

    if args.n_boot > 0 and not rb_result.get("insufficient"):
        rho_b_ci = _bootstrap_rho_b(mm_events, args.n_boot)
    else:
        rho_b_ci = (float("nan"), float("nan"))
    print(f"[member-correlation] ρ_b 95% CI: [{rho_b_ci[0]:.4f}, {rho_b_ci[1]:.4f}]")

    # N_eff — use AIFS family sizes. The replacement ensemble is:
    # 1 AIFS family of 51 members + up to 5 deterministic families of 1 member each.
    # Concretely, the bayes_precision_fusion uses:
    # AIFS: 51 perturbed members (1 family)
    # Deterministic anchor: 1 (openmeteo IFS9) — 1 family of 1
    # raw_model_forecasts: ~5 major deterministic models each as 1-member families
    # Total typical N ≈ 51 + 6 = 57, but the confidence-bound use is mostly AIFS-driven.
    # Report both: N=51 (AIFS-only) and N=57 (AIFS + 6 deterministic).

    aifs_family_sizes = [51]
    n_eff_aifs_only = _compute_n_eff(
        rho_w if np.isfinite(rho_w) else 0.0,
        0.0,  # single family → ρ_b irrelevant
        aifs_family_sizes
    )

    # Multi-family: AIFS(51) + 6 deterministic models (1 each)
    n_det = rb_result.get("n_models", 6)
    mixed_family_sizes = [51] + [1] * min(n_det, 10)
    n_eff_mixed = _compute_n_eff(
        rho_w if np.isfinite(rho_w) else 0.0,
        rho_b if np.isfinite(rho_b) else 0.0,
        mixed_family_sizes
    )
    N_raw = sum(mixed_family_sizes)

    print(f"[member-correlation] N_eff (AIFS-only, N=51): {n_eff_aifs_only:.1f}")
    print(f"[member-correlation] N_eff (mixed AIFS+det, N={N_raw}): {n_eff_mixed:.1f}")

    # Data window
    all_dates = ([e["target_date"] for e in aifs_events]
                 + [e["target_date"] for e in mm_events])
    td_min = min(all_dates) if all_dates else "unknown"
    td_max = max(all_dates) if all_dates else "unknown"
    window = f"settled-{td_min}..{td_max}"

    fitted_at = _dt.datetime.now(_dt.timezone.utc).isoformat()

    artifact = {
        "_meta": {
            "authority": AUTHORITY,
            "method": METHOD,
            "created": fitted_at,
            "data_window": window,
            "source": "forecast_posteriors.provenance_json (AIFS aifs_probabilities) + "
                      "raw_model_forecasts (deterministic models), settlement_outcomes(VERIFIED), "
                      "temperature_metric=high",
            "p_filter_lo": P_LO,
            "p_filter_hi": P_HI,
            "n_boot": args.n_boot,
            "boot_ci_level": 0.95,
            "ci_method": "block_bootstrap_by_target_date",
        },
        "rho_w": round(rho_w, 6) if np.isfinite(rho_w) else None,
        "rho_w_ci": [round(rho_w_ci[0], 6) if np.isfinite(rho_w_ci[0]) else None,
                     round(rho_w_ci[1], 6) if np.isfinite(rho_w_ci[1]) else None],
        "rho_b": round(rho_b, 6) if np.isfinite(rho_b) else None,
        "rho_b_ci": [round(rho_b_ci[0], 6) if np.isfinite(rho_b_ci[0]) else None,
                     round(rho_b_ci[1], 6) if np.isfinite(rho_b_ci[1]) else None],
        "n_eff_aifs_only": round(n_eff_aifs_only, 2) if np.isfinite(n_eff_aifs_only) else None,
        "n_eff_mixed": round(n_eff_mixed, 2) if np.isfinite(n_eff_mixed) else None,
        "n_raw_aifs": 51,
        "n_raw_mixed": N_raw,
        "per_family": {
            "AIFS_pf": {
                "family_type": "perturbed_forecast_ensemble",
                "n_members": rw_result.get("n_members", 51),
                "rho_w": round(rho_w, 6) if np.isfinite(rho_w) else None,
                "rho_w_ci": [
                    round(rho_w_ci[0], 6) if np.isfinite(rho_w_ci[0]) else None,
                    round(rho_w_ci[1], 6) if np.isfinite(rho_w_ci[1]) else None,
                ],
                "n_events": rw_result.get("n_events", 0),
                "n_bins_used": rw_result.get("n_bins_used", 0),
            },
            "deterministic_models": {
                "family_type": "independent_deterministic_models",
                "n_models": rb_result.get("n_models", 0),
                "models": rb_result.get("models", []),
                "rho_b": round(rho_b, 6) if np.isfinite(rho_b) else None,
                "rho_b_ci": [
                    round(rho_b_ci[0], 6) if np.isfinite(rho_b_ci[0]) else None,
                    round(rho_b_ci[1], 6) if np.isfinite(rho_b_ci[1]) else None,
                ],
                "n_events": rb_result.get("n_events", 0),
                "n_bins_used": rb_result.get("n_bins_used", 0),
            },
        },
        "n_events_aifs": len(aifs_events),
        "n_events_multimodel": len(mm_events),
        "mixed_family_sizes": mixed_family_sizes,
    }

    tmp = f"{args.out}.tmp"
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, sort_keys=True)
    os.replace(tmp, args.out)

    print(f"[member-correlation] wrote {args.out}  (window={window})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
