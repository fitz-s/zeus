#!/usr/bin/env python3
# Lifecycle: created=2026-06-12; last_reviewed=2026-06-13; last_reused=2026-06-13
# Purpose: JOINT per-city location-bias b_loc + global spread-scale k fit by interval-censored
#   categorical MLE (+ empirical-Bayes shrinkage of b_loc), replacing the variance-only k fit that
#   PROVABLY absorbed unmodeled center bias (k_wrong^2 = k_true^2 + (delta/sigma)^2).
#   --era-mode (added 2026-06-13, addendum C1) makes the FULL settled history usable: it joins the
#   short-retention live forecast_posteriors AND the 2.4-year calibration_pairs historical bin
#   distributions as DISTINCT ERAS (model terms, not filters), then does a pooled/free-era/EB-partial-
#   pooling triple fit + era-effect LRT + parametric-bootstrap boundary test. SHIPPED estimator is
#   ALWAYS EB partial pooling (addendum D1, supersedes the A5 pretest switch — pretest estimators have
#   unbounded relative risk near the null); the LRT/bootstrap p stay as REPORTED OBSERVATIONS only.
#   This is the deploy unlock for the 6117 historical settlements. NOTE (won-bin provenance fix
#   2026-06-13): the historical era matches its won bin by the calibration_pairs OWN outcome=1 label via
#   EXACT equality — NOT the coarse market winning_bin and NOT _winning_index's substring search, which
#   COLLIDES on the wide -40..+61 historical grid ('12°C' substring-matches '-12°C') and produced a
#   spurious ~24-step / -11° pseudo-bias before the fix.
# Reuse: Re-run weekly so (b,k) track settled-data growth; review state/bias_scale_fit.json + the
#   --gate walk-forward evidence BEFORE wiring the materializer (operator-gated deploy, Step 1).
#   Last reused or audited: 2026-06-13.
# Authority basis: docs/authority/statistical_calibration_authority_2026-06-12.txt Task 1.1 +
#   Migration order Step 1 (interval-censored categorical likelihood, identifiability proof, EB
#   shrinkage S_l = tau^2/(tau^2+s_l^2), prequential paired log-loss gate);
#   docs/authority/statistical_calibration_addendum_2026-06-13.md A5 (era-aware EB partial pooling,
#   decision rule), A6 (DISPUTED exclusion, nee QUARANTINED — renamed 2026-07-11 per
#   docs/rebuild/quarantine_excision_2026-07-11.md §T2b), A9 (dual log-loss + RPS report), C1 (the era-mode
#   deploy unlock); reference impls era_lrt / eb_era_diag / eb_era_full_given_sigma /
#   step_change_time_decay_bias from consult2_era_contamination_fdr_maker_2026-06-13_raw.txt Q1.
#   Data pipeline REUSED from scripts/fit_sigma_scale.py (provenance-audited 2026-06-12:
#   CURRENT_REUSABLE — same VERIFIED forecast_posteriors join, mode=ro, lead-bucketed freshest-
#   posterior, bin/winning-index/preimage parsing; that script's k-only Bernoulli objective is the
#   proven pathology this one replaces, but its data plumbing is correct and leak-disciplined).
"""Fit per-city bias b_loc + global scale k by INTERVAL-CENSORED CATEGORICAL MLE on settled cells.

WHY (authority Task 1.1): Zeus's predictive bin distribution is N(mu*, (sigma_pred*k)^2) integrated
over each bin's settlement rounding preimage. The current k=1.5833 was fitted variance-only — and the
authority PROVES k_wrong^2 = k_true^2 + (delta/sigma)^2, so unmodeled per-city center bias delta was
absorbed into k, flattening the modal bin (the 7/7 sell-the-mode loss class). The fix is a JOINT fit:
one location bias b_loc per city + one global log-parameterized k, by the proper categorical likelihood
over the ACTUAL exchange bins (one multinomial draw per settled cell — the bin that won), NOT a per-bin
Bernoulli. (b_loc, k) are jointly identifiable given >= 2 finite bin boundaries (proof in authority).

MODEL  (authority cell_probs / neg_loglik):
  For settled cell i with implied local sigma_i (backed out of the materialized mode-bin prob, the SAME
  approximation fit_sigma_scale uses) and mode-centred preimage edges (a_k, b_k] in degree units:
    pi_ik(b_loc, k) = Phi((b_k - b_loc) / (k*sigma_i)) - Phi((a_k - b_loc) / (k*sigma_i))
  with open tails handled by +/-inf edges. The cell's center mu_i is the mode-bin centre, so in the
  edge frame mu_i = 0 and b_loc shifts the center; k widens the scale. Joint MLE:
    theta = [b_loc (one per city), log_k (global)]; minimize -sum_i log pi_{i, won_i} by L-BFGS-B.

EMPIRICAL-BAYES SHRINKAGE of b_loc (authority 1.3):
  Per city, s_l^2 = inverse observed Fisher information for b_loc (numerical 2nd derivative of that
  city's 1-D bias NLL at its MLE, with the global k fixed at k_hat). Assume b_l ~ N(b0, tau_b^2);
  estimate (b0, tau_b) by MARGINAL likelihood over a grid (Gaussian-approx city likelihoods
  N(b_raw_l, s_l^2)); shrink b_shrunk_l = b0 + S_l*(b_raw_l - b0), S_l = tau_b^2/(tau_b^2 + s_l^2).
  Shrinkage is what stops 50-sample cities from re-contaminating k with location noise.

WALK-FORWARD GATE (--gate, authority Step 1):
  Expanding-window temporal split (fit on settled days <= t, score day t+1; >=70/30 by date, fit NEVER
  sees a scored outcome). Paired prequential log-loss d_i = -log p_old(Y_i) + log p_new(Y_i):
    OLD = current pipeline distribution N(mu*, (sigma*1.5833)^2) (no bias) mixed with uniform w (as
          currently configured in sigma_scale_fit.json), evaluated over the same preimage bins.
    NEW = N(mu* + b_shrunk_city, (sigma*k_new)^2), NO uniform mixture.
  Report mean(d), day-block bootstrap SE + lower bound; modal-class reliability
  |sum 1{Y=mode}/sum p_mode - 1| old vs new (must shrink); and the 7 modal-NO loss-family replay
  (old-q vs new-q for the bin that actually won).

READ-ONLY over state/zeus-forecasts.db (file:...?mode=ro). Writes state/bias_scale_fit.json via atomic
replace. Does NOT overwrite sigma_scale_fit.json and does NOT wire the materializer (deploy is the
operator-gated step AFTER gate review). This script is the bias_scale_fit.json artifact's ONLY writer.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict

import numpy as np

# Ensure the sibling scripts/ dir is importable when run as a path (not a module).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- REUSE the audited data pipeline from fit_sigma_scale.py -------------------------------------
# Provenance verdict 2026-06-12: CURRENT_REUSABLE. These helpers solve bin parsing, winning-index,
# preimage edges, the sigma back-out, lead bucketing, and freshest-posterior dedup with no-leak
# discipline. Import (not copy) so the two fitters cannot drift apart. fit_sigma_scale only opens the
# DB inside its own main(), so importing it performs NO connection.
import fit_sigma_scale as _fss  # noqa: E402
from fit_sigma_scale import (  # noqa: E402
    _FIT_QUERY,
    _bucket_for_lead,
    _cell_edges,
    _lead_hours,
    _parse_cell,
    _phi_vec,
    _sigma_implied,
    _winning_index,
)

try:
    from scipy.optimize import minimize as _scipy_minimize  # type: ignore
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover - scipy is present in .venv; grid fallback documented below
    _scipy_minimize = None
    _HAVE_SCIPY = False

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FCST_DEFAULT = os.path.join(REPO, "state", "zeus-forecasts.db")
OUT_DEFAULT = os.path.join(REPO, "state", "bias_scale_fit.json")
SIGMA_SCALE_FIT = os.path.join(REPO, "state", "sigma_scale_fit.json")

EPS = 1e-15
K_OLD_DEFAULT = 1.5833        # the certified-contaminated variance-only k this fit supersedes
MIN_CELLS_TOTAL = 200         # authority: thousands ideal; refuse a global k fit below this
GATE_TRAIN_FRAC = 0.70        # >=70/30 temporal split for the walk-forward gate
AUTHORITY = "interval_censored_categorical_mle_eb"
SCHEMA = "bias_scale_fit"

# --- era-mode (addendum C1) ----------------------------------------------------------------------
# An ERA is a (data_version / pipeline regime / settlement-semantics regime) label. Eras are MODEL
# TERMS, never filters (addendum A5). Two joinable eras exist in zeus-forecasts.db (data audit 06-13):
#   ERA_LIVE  = forecast_posteriors  (live AIFS-sampled posterior, data_version *_v1; ~1-week retention,
#               2026-06-08..). The existing _FIT_QUERY path.
#   ERA_HIST  = calibration_pairs    (historical calibration-transport bin distributions,
#               error_model_family='full_transport_v1'; 2024-01-01..2026-05-25; joins ~82% of the 5786
#               VERIFIED 'high' settlements). This is the deploy unlock for the full settled history.
# Both reconstruct the SAME cell shape (q dict -> _parse_cell -> implied sigma + preimage edges + won
# index) so a single likelihood serves both; only the era LABEL differs.
ERA_LIVE = "live_posterior_v1"
ERA_HIST = "cal_full_transport_v1"
ERA_HIST_EMF = "full_transport_v1"   # calibration_pairs.error_model_family selecting the historical era
ERA_MIN_CELLS = 30                   # per-era minimum to attempt a free-era MLE (else pool that era)
ERA_BOOTSTRAP_REPS = 200             # parametric bootstrap replicates for the Sigma_era=0 boundary test
EPSILON_POOL = 0.01                  # addendum A5: full-pool license ceiling on UCB95 of max bin-prob
                                     # shift (= half of the 0.02 min actionable edge); documented default
P_ERA_POOL = 0.10                    # addendum A5: full-pool requires p_era >= 0.10
EPSILON_EDGE = 0.02                  # min actionable edge; newest-era-only license = 1.96*max se < eps/2

# calibration_pairs historical-era extraction. One self-consistent posterior per settled cell: the
# decision_group with the single highest p_raw (a coherent group whose p_raw sums to 1 — verified
# 2026-06-13). VERIFIED settlement join supplies winning_bin + settlement_value + unit.
_ERA_HIST_QUERY = (
    "SELECT cp.city, cp.target_date, cp.decision_group_id, cp.range_label, cp.p_raw, cp.outcome, "
    "       so.winning_bin, so.settlement_value, so.settlement_unit "
    "FROM calibration_pairs cp "
    "JOIN settlement_outcomes so "
    "  ON so.city=cp.city AND so.target_date=cp.target_date "
    " AND so.temperature_metric=cp.temperature_metric "
    "WHERE cp.temperature_metric='high' "
    "  AND cp.error_model_family=? "
    "  AND so.authority='VERIFIED' AND so.winning_bin IS NOT NULL "
    # ORDER BY (city,date) so the builder can stream one settled cell at a time and never hold the
    # whole 48M-row table in memory (the inner join already restricts to ~5.8k settled cells, but each
    # cell carries ~1k decision-group×bin rows -> streaming keeps peak memory to one cell's rows).
    "ORDER BY cp.city, cp.target_date"
)

# The 7 known modal-NO loss families (city, target_date, metric='high'). Replayed deterministically
# in the gate: old-q vs new-q for the bin that ACTUALLY won. Rows not yet settled are reported absent.
LOSS_FAMILIES = [
    ("Hong Kong", "2026-06-12"),
    ("Karachi", "2026-06-12"),
    ("Kuala Lumpur", "2026-06-12"),
    ("Denver", "2026-06-12"),
    ("Hong Kong", "2026-06-08"),     # earlier HK 30C class
    ("Karachi", "2026-06-10"),       # earlier Karachi 37C class
    ("Kuala Lumpur", "2026-06-08"),  # earlier KL 33C class
]


# --- cell construction (keeps CITY; otherwise mirrors fit_sigma_scale._build_cells) --------------

def _build_cells_with_city(rows):
    """Freshest posterior per (city, target_date, bucket) -> per-cell dicts that KEEP the city.

    Each cell carries mode-centred preimage edges (degree units), the implied local sigma, the won
    bin index, and the materialized per-bin q (for the OLD-distribution baseline in the gate). The
    cell's center mu is the mode-bin centre, so edges are already mode-relative and b_loc/mu enter
    as an additive shift in the SAME degree unit.
    """
    best: dict = {}
    for city, tdate, sct, comp, q_json_text, winning_bin, sval, sunit in rows:
        bucket = _bucket_for_lead(_lead_hours(tdate, sct))
        if bucket is None:
            continue
        key = (city, tdate, bucket)
        prev = best.get(key)
        if prev is None or str(comp) > str(prev[3]):
            best[key] = (city, tdate, sct, comp, q_json_text, winning_bin, sval, sunit, bucket)

    cells = []
    for (city, tdate, sct, comp, q_json_text, winning_bin, sval, sunit, bucket) in best.values():
        cell = _cell_from_q_dict(q_json_text, city, tdate, winning_bin, sval, era=ERA_LIVE,
                                 bucket=bucket)
        if cell is not None:
            cells.append(cell)
    return cells


def _cell_from_q_dict(q_json_text, city, tdate, winning_bin, sval, era, bucket=None,
                      exact_won_label=None):
    """Build ONE cell dict from a q-distribution JSON ({label: prob}) + settlement join fields.

    This is the SINGLE cell constructor shared by both eras: the live forecast_posteriors q_json and the
    reconstructed-from-calibration_pairs q dict flow through the SAME _parse_cell / _sigma_implied /
    _cell_edges helpers. The only thing that differs between eras is the `era` label stamped on the cell.
    Returns None if the cell is unparseable / degenerate.

    WON-BIN MATCHING: when `exact_won_label` is given (the calibration_pairs era passes its native
    outcome=1 range_label), the won index is found by EXACT label equality. This bypasses
    _winning_index's substring/center search, which COLLIDES on a wide grid (e.g. the historical
    -40..+61 grid: substring '12°C' matches '-12°C' first -> a spurious ~24-step offset, the root cause
    of the -11° pseudo-bias found 2026-06-13). The live era keeps the _winning_index path (its ~11-bin
    grid never collides) by leaving exact_won_label=None.
    """
    parsed = _parse_cell(q_json_text)
    if parsed is None:
        return None
    items, mode_index, step = parsed
    q_mode = items[mode_index][1]
    sigma_impl = _sigma_implied(q_mode, half_step=step / 2.0)
    if sigma_impl is None:
        return None
    won_index = None
    if exact_won_label is not None:
        for i, it in enumerate(items):
            if it[0] == exact_won_label:
                won_index = i
                break
    if won_index is None:
        won_index = _winning_index(items, winning_bin, sval, step=step)
    if won_index is None:
        return None
    lo, hi = _cell_edges(items, mode_index, step)
    q_materialized = np.asarray([it[1] for it in items], dtype=float)
    return {
        "city": city, "target_date": tdate, "bucket": bucket, "era": era,
        "n_bins": len(items), "sigma_impl": float(sigma_impl), "mode_index": int(mode_index),
        "won_index": int(won_index), "step": float(step),
        "edges_lo": lo, "edges_hi": hi,
        "q_materialized": q_materialized,
        "mode_deg": items[mode_index][2],
        "won_deg": items[won_index][2],
        "won_label": items[won_index][0],
    }


def _build_hist_cells(rows):
    """Reconstruct historical-era cells from calibration_pairs rows joined to VERIFIED settlements.

    rows = an iterable of (city, target_date, decision_group_id, range_label, p_raw, outcome,
    winning_bin, settlement_value, settlement_unit). One coherent posterior per settled cell: the
    decision_group whose single highest p_raw is the max over the cell (its p_raw sums to 1, a
    self-consistent distribution — verified 2026-06-13). The chosen group's {range_label: p_raw} is a
    q-distribution fed through the SAME _cell_from_q_dict path as the live era. DISPUTED settlements
    never appear here (the SQL filters authority='VERIFIED'); they are counted separately for A6.

    WON-BIN PROVENANCE (critical — fixed 2026-06-13 after the won-minus-mode audit): the FINE
    calibration_pairs grid (e.g. 83-84°F) is NOT the same frame as the live market's COARSE settlement
    winning_bin (e.g. '62°F or higher' lumps everything >=62 into one bin). Substring-matching the coarse
    market bin against the fine grid lands the won index ~9-27 STEPS below the mode -> a spurious -11°
    'bias'. The authoritative won bin in the calibration_pairs frame is its OWN `outcome=1` row. We use
    that range_label as the winning bin (fine-grid native), falling back to settlement_value only when no
    outcome flag exists. The coarse market winning_bin is ignored for this era.

    STREAMING: when `rows` is ordered by (city, target_date) (the _ERA_HIST_QUERY ORDER BY), this flushes
    each completed cell and holds only the current cell's decision-group rows in memory — so the 48M-row
    historical table never lands in RAM at once. It is also correct for an unordered in-memory list (the
    cell flushes still group correctly, just with higher transient memory) so the unit tests can pass a
    short list directly.
    """
    cells = []
    cur_key = None
    groups: dict = defaultdict(dict)        # grp -> {label: p}
    grp_max: dict = defaultdict(float)      # grp -> max p_raw in that group
    grp_won: dict = defaultdict(lambda: None)   # grp -> range_label where outcome==1 (fine-grid won bin)
    meta = (None, None)                     # (settlement_winning_bin, settlement_value) fallback only

    def _flush(key, groups, grp_max, grp_won, meta):
        if key is None or not groups:
            return None
        best_grp = max(groups.keys(), key=lambda g: grp_max[g])
        qd = groups[best_grp]
        if not qd:
            return None
        city, tdate = key
        # Won bin = the chosen group's OWN outcome=1 label (fine-grid native), matched by EXACT label
        # equality (exact_won_label) to avoid the wide-grid substring collision. settlement_value is the
        # fallback the _winning_index path uses only if the outcome label is missing. We NEVER pass the
        # coarse market winning_bin here (it lives in a different, coarser bin frame).
        won_label = grp_won.get(best_grp)
        return _cell_from_q_dict(json.dumps(qd), city, tdate, None, meta[1], era=ERA_HIST,
                                 bucket=None, exact_won_label=won_label)

    for city, tdate, grp, label, p_raw, outcome, winning_bin, sval, sunit in rows:
        key = (city, tdate)
        if key != cur_key:
            cell = _flush(cur_key, groups, grp_max, grp_won, meta)
            if cell is not None:
                cells.append(cell)
            cur_key = key
            groups = defaultdict(dict)
            grp_max = defaultdict(float)
            grp_won = defaultdict(lambda: None)
            meta = (winning_bin, sval)
        try:
            p = float(p_raw)
        except (TypeError, ValueError):
            p = 0.0
        groups[grp][label] = p
        if p > grp_max[grp]:
            grp_max[grp] = p
        try:
            if int(outcome) == 1:
                grp_won[grp] = label
        except (TypeError, ValueError):
            pass

    cell = _flush(cur_key, groups, grp_max, grp_won, meta)
    if cell is not None:
        cells.append(cell)
    return cells


# --- interval-censored categorical likelihood (authority cell_probs / neg_loglik) ----------------

def _rounding_sigma_floor(step: float) -> float:
    """Rounding-implied minimum SD: SD of uniform-on-one-bin = bin_width/(2*sqrt(3)).

    Authority cross-validation 2026-06-12: the settlement rounding alone implies a spread of at least
    the uniform-over-one-bin SD; k*sigma_pred must never fall below it. Replaces the legacy 1.0 floor.
    """
    return float(step) / (2.0 * math.sqrt(3.0))


def _cell_probs(cell, b_loc: float, k: float, apply_floor: bool = False) -> np.ndarray:
    """pi_ik = Phi((b_k - b_loc)/(k*sigma)) - Phi((a_k - b_loc)/(k*sigma)) over the cell's bins.

    Edges are mode-centred (mu_i = 0 in this frame); b_loc shifts the center, k widens the scale.
    When apply_floor, the effective scale is floored at the rounding-implied sigma_min
    (= step/(2*sqrt(3))). The decision (floor hit?) is recorded on the cell for artifact accounting.
    Renormalised (numerical only; the partition over a full bin set already sums to 1).
    """
    scale = k * cell["sigma_impl"]
    if apply_floor:
        floor = _rounding_sigma_floor(cell.get("step", 1.0))
        if scale < floor:
            cell["_floor_hit"] = True
            scale = floor
    z_hi = (cell["edges_hi"] - b_loc) / scale
    z_lo = (cell["edges_lo"] - b_loc) / scale
    p = _phi_vec(z_hi) - _phi_vec(z_lo)
    p = np.clip(p, EPS, 1.0)
    s = float(p.sum())
    return p / s if s > 0 else np.full(p.shape, 1.0 / p.shape[0])


def _neg_loglik(theta, cells, city_index, n_cities: int,
                prior_b0=None, prior_tau=None) -> float:
    """-sum_i log pi_{i, won_i} over all settled cells. Joint over [b_loc(n_cities), log_k].

    Optional Gaussian MAP prior on b_loc (empirical-Bayes prior fed back as a ridge).
    """
    b_loc = np.asarray(theta[:n_cities], dtype=float)
    log_k = float(theta[n_cities])
    if not math.isfinite(log_k) or log_k > 3.0 or log_k < -3.0:
        return 1e18
    k = math.exp(log_k)
    nll = 0.0
    for ci, cell in enumerate(cells):
        p = _cell_probs(cell, b_loc[city_index[ci]], k)
        nll -= math.log(max(float(p[cell["won_index"]]), EPS))
    if prior_b0 is not None and prior_tau is not None and prior_tau > 0:
        nll += 0.5 * float(np.sum(((b_loc - prior_b0) / prior_tau) ** 2))
    return nll


def _winbin_arrays(cells, city_index):
    """Precompute stacked WON-bin edges + city indices for the vectorized NLL (one pass, cached).

    The interval-censored likelihood only needs pi_{won} = Phi((hi_won - b)/scale) - Phi((lo_won - b)/
    scale): the full-partition normaliser is exactly 1 for a complete open-tailed bin tiling (verified
    2026-06-13 — Phi telescopes, +/-inf tails give 1), so renormalisation is a machine-eps no-op and the
    other bins never enter the won-bin likelihood. This collapses the per-cell Python loop in
    _neg_loglik to TWO vectorized Phi calls over all cells -> ~100x on the 5.7k-cell historical era.
    """
    n = len(cells)
    lo = np.empty(n, dtype=float)
    hi = np.empty(n, dtype=float)
    sig = np.empty(n, dtype=float)
    cidx = np.asarray(city_index, dtype=np.intp)
    for i, cell in enumerate(cells):
        wi = cell["won_index"]
        lo[i] = cell["edges_lo"][wi]
        hi[i] = cell["edges_hi"][wi]
        sig[i] = cell["sigma_impl"]
    return lo, hi, sig, cidx


def _neg_loglik_vec(theta, lo, hi, sig, cidx, n_cities, prior_b0=None, prior_tau=None) -> float:
    """Vectorized -sum log pi_{won} over precomputed won-bin arrays. Mirrors _neg_loglik exactly."""
    b_loc = np.asarray(theta[:n_cities], dtype=float)
    log_k = float(theta[n_cities])
    if not math.isfinite(log_k) or log_k > 3.0 or log_k < -3.0:
        return 1e18
    scale = math.exp(log_k) * sig
    b_cell = b_loc[cidx]
    p = _phi_vec((hi - b_cell) / scale) - _phi_vec((lo - b_cell) / scale)
    p = np.clip(p, EPS, 1.0)
    nll = float(-np.sum(np.log(p)))
    if prior_b0 is not None and prior_tau is not None and prior_tau > 0:
        nll += 0.5 * float(np.sum(((b_loc - prior_b0) / prior_tau) ** 2))
    return nll


def _fit_joint(cells, city_index, n_cities: int, prior_b0=None, prior_tau=None):
    """Joint MLE/MAP over [b_loc..., log_k] by L-BFGS-B. Returns (b_hat[n_cities], k_hat, res).

    Uses the vectorized won-bin NLL (_neg_loglik_vec) — identical objective, ~100x faster on large eras.
    """
    theta0 = np.r_[np.zeros(n_cities), 0.0]  # b=0, log_k=0 (k=1) — the regression anchor
    if _HAVE_SCIPY and _scipy_minimize is not None:
        lo, hi, sig, cidx = _winbin_arrays(cells, city_index)
        res = _scipy_minimize(
            _neg_loglik_vec, theta0,
            args=(lo, hi, sig, cidx, n_cities, prior_b0, prior_tau),
            method="L-BFGS-B",
            bounds=[(-15.0, 15.0)] * n_cities + [(-1.5, 1.5)],
        )
        b_hat = np.asarray(res.x[:n_cities], dtype=float)
        k_hat = float(math.exp(res.x[n_cities]))
        return b_hat, k_hat, res
    # Fallback: coordinate ascent (no scipy). Rarely hit; .venv has scipy.
    return _fit_joint_grid(cells, city_index, n_cities)


def _fit_joint_grid(cells, city_index, n_cities: int):  # pragma: no cover - documented fallback
    """Coordinate descent over b_loc (per city) + a 1-D log_k line search. scipy-free safety net."""
    b = np.zeros(n_cities)
    log_k = 0.0
    cells_by_city = defaultdict(list)
    for ci, cell in enumerate(cells):
        cells_by_city[city_index[ci]].append(cell)
    for _ in range(40):
        for c, cl in cells_by_city.items():
            grid = np.linspace(b[c] - 5.0, b[c] + 5.0, 41)
            best = min(grid, key=lambda v: sum(
                -math.log(max(float(_cell_probs(cell, v, math.exp(log_k))[cell["won_index"]]), EPS))
                for cell in cl))
            b[c] = float(best)
        kgrid = np.linspace(log_k - 0.4, log_k + 0.4, 41)
        log_k = float(min(kgrid, key=lambda lk: _neg_loglik(
            np.r_[b, lk], cells, city_index, n_cities)))
    return b, float(math.exp(log_k)), None


# --- per-city Fisher information + empirical-Bayes shrinkage (authority 1.3) ----------------------

def _city_bias_fisher(cells_city, b_raw: float, k: float) -> float:
    """Inverse observed Fisher information s_l^2 for one city's bias (numerical 2nd derivative).

    s_l^2 = 1 / (d^2/db^2)[-loglik_city](b_raw). NLL_city(b) = -sum log pi_{i,won}. Central difference.
    Vectorized over the city's won-bin arrays (same value as the per-cell loop; just batched Phi).
    """
    h = 0.05  # degree units; bins are 1C/2F so this is a fine, stable step
    lo = np.fromiter((c["edges_lo"][c["won_index"]] for c in cells_city), float, len(cells_city))
    hi = np.fromiter((c["edges_hi"][c["won_index"]] for c in cells_city), float, len(cells_city))
    sig = np.fromiter((c["sigma_impl"] for c in cells_city), float, len(cells_city))
    scale = k * sig

    def nll_city(b: float) -> float:
        p = np.clip(_phi_vec((hi - b) / scale) - _phi_vec((lo - b) / scale), EPS, 1.0)
        return float(-np.sum(np.log(p)))

    f0 = nll_city(b_raw)
    fp = nll_city(b_raw + h)
    fm = nll_city(b_raw - h)
    curv = (fp - 2.0 * f0 + fm) / (h * h)  # observed Fisher information I_l
    if not math.isfinite(curv) or curv <= 1e-9:
        return float("inf")  # uninformative city -> s_l^2 = inf -> S_l = 0 -> shrink fully to b0
    return 1.0 / curv


def _eb_prior(b_raw_by_city: dict, s2_by_city: dict):
    """Estimate (b0, tau_b) by MARGINAL likelihood (authority 1.3 grid form).

    Gaussian-approx each city likelihood as b_raw_l ~ N(b_l, s_l^2), b_l ~ N(b0, tau_b^2). The marginal
    is b_raw_l ~ N(b0, s_l^2 + tau_b^2); maximize sum_l log N(b_raw_l; b0, s_l^2+tau_b^2) over a grid.
    Cities with s_l^2 = inf carry no information and are dropped from the prior fit.
    """
    items = [(b_raw_by_city[c], s2_by_city[c]) for c in b_raw_by_city
             if math.isfinite(s2_by_city[c])]
    if len(items) < 2:
        b0 = float(np.mean([b for b, _ in items])) if items else 0.0
        return b0, 0.5  # weak default tau when too few informative cities
    braw = np.asarray([b for b, _ in items], dtype=float)
    s2 = np.asarray([v for _, v in items], dtype=float)
    b0_grid = np.linspace(float(braw.min()) - 1.0, float(braw.max()) + 1.0, 121)
    # tau grid spans 0 (full pooling) up to ~2x the spread of raw biases
    tau_hi = max(1.0, 2.0 * float(np.std(braw)) + 1.0)
    tau_grid = np.linspace(0.0, tau_hi, 121)
    best = (0.0, 0.5, -1e18)
    for b0 in b0_grid:
        for tau in tau_grid:
            v = s2 + tau * tau
            ll = float(np.sum(-0.5 * np.log(2.0 * math.pi * v) - 0.5 * (braw - b0) ** 2 / v))
            if ll > best[2]:
                best = (float(b0), float(tau), ll)
    return best[0], best[1]


def _shrink(b_raw: float, s2: float, b0: float, tau: float):
    """Gaussian EB shrinkage: S_l = tau^2/(tau^2+s_l^2); b_shrunk = b0 + S_l*(b_raw - b0)."""
    if not math.isfinite(s2):
        return b0, 0.0
    denom = tau * tau + s2
    s_factor = (tau * tau) / denom if denom > 0 else 0.0
    return b0 + s_factor * (b_raw - b0), float(s_factor)


# --- OLD distribution (current pipeline) for the gate baseline -----------------------------------

def _load_old_w(path: str = SIGMA_SCALE_FIT) -> float:
    """Uniform-mixture weight w from the CURRENT sigma_scale_fit.json (the configured OLD pipeline).

    Uses the C family's fitted w as the representative current configuration (the mixture the live
    pipeline applies). Falls back to 0.0 if the artifact is unreadable. The OLD k is the contaminated
    1.5833 by authority; we read w from the same artifact for an honest like-for-like baseline.
    """
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        fam = d.get("families", {})
        for u in ("C", "F"):
            e = fam.get(u, {})
            if e.get("fitted") and e.get("w") is not None:
                return float(e["w"])
    except Exception:
        pass
    return 0.0


def _q_old(cell, k_old: float, w: float) -> np.ndarray:
    """OLD per-bin distribution: N(mu*, (sigma*k_old)^2) (NO bias) mixed with uniform w."""
    base = _cell_probs(cell, 0.0, k_old)  # b_loc = 0: the old pipeline applies no center bias
    u = 1.0 / cell["n_bins"]
    return (1.0 - w) * base + w * u


def _q_new(cell, b_shrunk: float, k_new: float) -> np.ndarray:
    """NEW per-bin distribution: N(mu* + b_shrunk, (sigma*k_new)^2), NO uniform mixture.

    The effective scale is floored at the rounding-implied sigma_min (= bin_width/(2*sqrt(3))).
    """
    return _cell_probs(cell, b_shrunk, k_new, apply_floor=True)


# --- walk-forward gate (authority Step 1) --------------------------------------------------------

def _pit_value(q: np.ndarray, won_index: int) -> float:
    """Midpoint (mean) PIT of the observed winning bin under a categorical predictive q.

    PIT = sum_{j<won} q_j + 0.5 * q_won. Under a well-calibrated model the PIT values are ~Uniform(0,1);
    a center-peaked overdispersed model (too-wide) yields PITs CLUSTERED near 0.5 (interior), a
    too-narrow model yields a U-shape. Returns the midpoint PIT in [0,1].
    """
    below = float(np.sum(q[:won_index]))
    return below + 0.5 * float(q[won_index])


def _pit_chisquare(pits: list[float], n_bins: int = 10) -> float:
    """Chi-square of a PIT sample against Uniform(0,1) over n_bins equal-width deciles.

    Larger => farther from uniform (worse calibration). Returns the chi-square statistic.
    """
    if not pits:
        return float("nan")
    counts, _ = np.histogram(np.clip(pits, 0.0, 1.0), bins=n_bins, range=(0.0, 1.0))
    expected = len(pits) / n_bins
    if expected <= 0:
        return float("nan")
    return float(np.sum((counts - expected) ** 2 / expected))


def _run_gate(cells, city_index, cities, k_old: float, w_old: float, seed: int = 17):
    """Expanding-window temporal split; paired prequential log-loss + modal reliability + replay.

    Fit (b,k)+EB on the TRAIN fold (days <= cut), score the TEST fold (days > cut). The fit never sees
    a scored outcome. Returns (gate_dict, b_shrunk_train, b0_train, k_train) on success, else a
    status-only dict.
    """
    order = sorted({c["target_date"] for c in cells})
    # forecast_posteriors retention is short (≈1 week), so the settled-join window is thin. We still
    # run the minimal temporal split possible (>=2 train days, >=1 test day) and flag THIN_WINDOW so
    # the operator reads the gate numbers as indicative, not robust. < 3 days cannot split at all.
    if len(order) < 3:
        return {"status": "INSUFFICIENT_DAYS", "n_days": len(order)}
    thin = len(order) < 8  # authority/cross-val expects a fuller window for a robust prequential gate
    cut_idx = max(1, int(round(GATE_TRAIN_FRAC * len(order))) - 1)
    cut_idx = min(cut_idx, len(order) - 2)  # guarantee >=1 test day
    cut_date = order[cut_idx]
    train = [c for c in cells if c["target_date"] <= cut_date]
    test = [c for c in cells if c["target_date"] > cut_date]
    if not train or not test:
        return {"status": "EMPTY_FOLD", "cut_date": cut_date}

    # Fit on TRAIN only (rebuild a contiguous city index over the cities present in train).
    train_cities = sorted({c["city"] for c in train})
    tcidx = {city: i for i, city in enumerate(train_cities)}
    train_city_index = [tcidx[c["city"]] for c in train]
    b_train, k_train, _res = _fit_joint(train, train_city_index, len(train_cities))
    # EB on TRAIN.
    cells_by_city_tr = defaultdict(list)
    for ci, cell in enumerate(train):
        cells_by_city_tr[train_cities[train_city_index[ci]]].append(cell)
    b_raw_tr = {city: float(b_train[tcidx[city]]) for city in train_cities}
    s2_tr = {city: _city_bias_fisher(cells_by_city_tr[city], b_raw_tr[city], k_train)
             for city in train_cities}
    b0_tr, tau_tr = _eb_prior(b_raw_tr, s2_tr)
    b_shrunk_tr = {city: _shrink(b_raw_tr[city], s2_tr[city], b0_tr, tau_tr)[0]
                   for city in train_cities}

    # Score TEST: paired prequential log-loss d_i = -log p_old(Y) + log p_new(Y).
    d_list = []
    day_block = defaultdict(list)
    old_mode_num = old_mode_den = 0.0
    new_mode_num = new_mode_den = 0.0
    old_modal_q_sum = new_modal_q_sum = 0.0   # mean modal-bin q trajectory (old ~0.22, new 0.30-0.34)
    pits_old, pits_new = [], []               # PIT/rank-histogram samples
    floor_hits = 0
    for cell in test:
        cell.pop("_floor_hit", None)
        # New bias for a city unseen in train -> b0_tr (full shrinkage to the pooled mean).
        b_sh = b_shrunk_tr.get(cell["city"], b0_tr)
        q_old = _q_old(cell, k_old, w_old)
        q_new = _q_new(cell, b_sh, k_train)
        if cell.pop("_floor_hit", False):
            floor_hits += 1
        won = cell["won_index"]
        p_old = max(float(q_old[won]), EPS)
        p_new = max(float(q_new[won]), EPS)
        d = -math.log(p_old) + math.log(p_new)
        d_list.append(d)
        day_block[cell["target_date"]].append(d)
        # Modal-class reliability accumulators.
        mode_i = cell["mode_index"]
        old_mode_num += float(q_old[mode_i])
        new_mode_num += float(q_new[mode_i])
        won_is_mode = 1.0 if won == mode_i else 0.0
        old_mode_den += won_is_mode
        new_mode_den += won_is_mode
        # Modal-q trajectory + PIT.
        old_modal_q_sum += float(q_old[mode_i])
        new_modal_q_sum += float(q_new[mode_i])
        pits_old.append(_pit_value(q_old, won))
        pits_new.append(_pit_value(q_new, won))

    d_arr = np.asarray(d_list, dtype=float)
    mean_d = float(d_arr.mean())

    # Day-BLOCK bootstrap SE + 5% lower bound (resample whole days to respect within-day correlation).
    days = list(day_block.keys())
    rng = np.random.default_rng(seed)
    boot_means = []
    for _ in range(2000):
        pick = rng.integers(0, len(days), size=len(days))
        vals = []
        for j in pick:
            vals.extend(day_block[days[j]])
        if vals:
            boot_means.append(float(np.mean(vals)))
    boot = np.asarray(boot_means, dtype=float)
    se_d = float(boot.std(ddof=1)) if boot.size > 1 else float("nan")
    lb_d = float(np.percentile(boot, 5.0)) if boot.size else float("nan")

    # Modal-class reliability: |sum 1{Y=mode} / sum p_mode - 1|. Must shrink old -> new.
    old_rel = abs(old_mode_den / old_mode_num - 1.0) if old_mode_num > 0 else float("nan")
    new_rel = abs(new_mode_den / new_mode_num - 1.0) if new_mode_num > 0 else float("nan")

    n_pos = int((d_arr > 0).sum())
    n_test = len(test)
    pit_chi_old = _pit_chisquare(pits_old)
    pit_chi_new = _pit_chisquare(pits_new)
    k_floor_gate = k_old * 0.85   # = 1.34 for k_old=1.5833 (authority cross-val hard gate)
    gate = {
        "status": "OK",
        "thin_window": bool(thin),
        "thin_window_note": ("forecast_posteriors retention (~1 week) caps the settled-join window; "
                             "gate numbers are INDICATIVE not robust below ~8 settled days"),
        "split": "expanding_window_temporal",
        "train_frac": GATE_TRAIN_FRAC,
        "cut_date": cut_date,
        "n_train_cells": len(train), "n_test_cells": n_test,
        "n_days_test": len(days),
        "k_old": round(k_old, 4), "w_old": round(w_old, 4),
        "k_new_trainfold": round(k_train, 4),
        "b0_trainfold": round(b0_tr, 4), "tau_trainfold": round(tau_tr, 4),
        "sigma_floor_basis": "step/(2*sqrt(3)) rounding-implied uniform-on-one-bin SD",
        "new_dist_floor_hits": floor_hits,
        "new_dist_floor_hit_frac": round(floor_hits / n_test, 4) if n_test else None,
        "paired_prequential_logloss": {
            "mean_d": round(mean_d, 5),
            "se_d_dayblock_bootstrap": round(se_d, 5) if math.isfinite(se_d) else None,
            "lower_bound_5pct": round(lb_d, 5) if math.isfinite(lb_d) else None,
            "frac_cells_improved": round(n_pos / len(d_arr), 4) if d_arr.size else None,
            "interpretation": "d>0 means NEW assigns higher prob to the realized bin than OLD",
            "deploy_signal": bool(math.isfinite(lb_d) and lb_d > 0.0),
        },
        "modal_class_reliability": {
            "old": round(old_rel, 5) if math.isfinite(old_rel) else None,
            "new": round(new_rel, 5) if math.isfinite(new_rel) else None,
            "shrinks": bool(math.isfinite(old_rel) and math.isfinite(new_rel) and new_rel < old_rel),
            "definition": "|sum 1{Y=mode} / sum p_mode - 1|",
        },
        "k_reduction_gate": {
            "k_old": round(k_old, 4),
            "k_new": round(k_train, 4),
            "threshold_max": round(k_floor_gate, 4),
            "passed": bool(k_train < k_floor_gate),
            "note": ("PASS = joint fit pulled k below 0.85*k_old, consistent with bias-absorption being "
                     "the dominant pathology. FAIL = k did NOT drop; bias-absorption was NOT dominant "
                     "over this window (do NOT force the fit — report it)."),
        },
        "pit_rank_histogram": {
            "old_chisquare_vs_uniform": round(pit_chi_old, 4) if math.isfinite(pit_chi_old) else None,
            "new_chisquare_vs_uniform": round(pit_chi_new, 4) if math.isfinite(pit_chi_new) else None,
            "new_closer_to_uniform": bool(math.isfinite(pit_chi_old) and math.isfinite(pit_chi_new)
                                          and pit_chi_new < pit_chi_old),
            "definition": "chi-square of midpoint-PIT sample against Uniform(0,1) over 10 deciles",
        },
        "modal_q_trajectory": {
            "old_mean_modal_q": round(old_modal_q_sum / n_test, 4) if n_test else None,
            "new_mean_modal_q": round(new_modal_q_sum / n_test, 4) if n_test else None,
            "expected_old_approx": 0.22,
            "expected_new_range": [0.30, 0.34],
        },
    }
    return gate, b_shrunk_tr, b0_tr, k_train


def _replay_loss_families(cells_by_key, b_shrunk_full: dict, b0_full: float,
                          k_old: float, w_old: float, k_new: float):
    """Deterministic replay of the 7 modal-NO loss families: old-q vs new-q for the WON bin.

    Uses the FULL-fit shrunk biases (the production artifact) so the replay reflects what the deployed
    fit would assign. Families not yet settled (no cell) are reported as ABSENT.
    """
    rows = []
    for city, tdate in LOSS_FAMILIES:
        cell = cells_by_key.get((city, tdate))
        if cell is None:
            rows.append({"city": city, "target_date": tdate, "status": "ABSENT_OR_UNSETTLED"})
            continue
        b_sh = b_shrunk_full.get(city, b0_full)
        q_old = _q_old(cell, k_old, w_old)
        q_new = _q_new(cell, b_sh, k_new)
        won = cell["won_index"]
        rows.append({
            "city": city, "target_date": tdate, "status": "SETTLED",
            "won_bin": cell["won_label"],
            "won_is_mode": bool(won == cell["mode_index"]),
            "dist_from_mode_steps": (None if cell["won_deg"] is None or cell["mode_deg"] is None
                                     else int(round(abs(cell["won_deg"] - cell["mode_deg"]) / cell["step"]))),
            "q_old_won": round(float(q_old[won]), 4),
            "q_new_won": round(float(q_new[won]), 4),
            "b_shrunk_city": round(float(b_sh), 4),
            "delta_q": round(float(q_new[won] - q_old[won]), 4),
        })
    return rows


def _k_ci_from_hessian(cells, city_index, n_cities, b_hat, k_hat):
    """95% CI on k from the curvature of the full NLL wrt log_k at the MLE (delta method).

    Var(log_k) = 1 / I(log_k); CI on log_k is +/-1.96*sqrt(Var); exponentiate to k-space.
    """
    log_k_hat = math.log(k_hat)
    h = 0.02
    lo, hi, sig, cidx = _winbin_arrays(cells, city_index)

    def nll_logk(lk: float) -> float:
        return _neg_loglik_vec(np.r_[b_hat, lk], lo, hi, sig, cidx, n_cities)

    f0 = nll_logk(log_k_hat)
    fp = nll_logk(log_k_hat + h)
    fm = nll_logk(log_k_hat - h)
    curv = (fp - 2.0 * f0 + fm) / (h * h)
    if not math.isfinite(curv) or curv <= 1e-9:
        return None
    sd = 1.0 / math.sqrt(curv)
    return [round(float(math.exp(log_k_hat - 1.96 * sd)), 4),
            round(float(math.exp(log_k_hat + 1.96 * sd)), 4)]


# =================================================================================================
# ERA-AWARE PARTIAL POOLING (addendum C1 / A5; reference impls from consult2 Q1) ===================
# =================================================================================================
#
# Eras are MODEL TERMS (A5): the FULL settled history enters as partially-pooled evidence, never as
# naively pooled rows. We fit THREE models over the era-labelled cells:
#   (a) POOLED   — one global (b_city, log k) over ALL eras (the existing joint MLE).
#   (b) FREE-ERA — independent (b_city_e, log k_e) per era.
#   (c) EB       — hierarchical N(phi0, Sigma_era) on the era summary phi_e = (b0_e, log k_e), with
#                  diagonal Sigma via marginal-likelihood grid (eb_era_diag), posterior
#                  phi_tilde_e = phi0 + Sigma(Sigma+V_e)^-1 (phi_hat_e - phi0). Per-city biases are EB-
#                  shrunk WITHIN each era toward that era's b0.
# Era-effect tests: fixed-effect LRT 2*(ll_free - ll_pooled) ~ chi2_{(E-1)p} (era_lrt) AND a
# parametric bootstrap for the Sigma_era=0 boundary (simulate from the pooled fit, refit free, compare
# the LRT stat) because the variance-component null is on the boundary (Self-Liang).


def step_change_time_decay_bias(delta: float, lam: float, n_new: int) -> float:
    """Bias of an exponential time-decay estimator after a step change (consult2 Q1.4, A5).

    INFINITE pre-step window form: target is the newest-era theta_1 = theta_0 + delta; an exponential
    time-decay weighting carries the stale pre-step level forward, leaving residual bias = -delta *
    lam^n_new (EXACT, n0->inf). This is WHY era dummies (zero step bias) beat time decay for KNOWN
    pipeline/settlement breaks: at lam=0.99,n=300 ~5% of the step persists; lam=0.995 ~22%.
    For the FINITE pre-step window form see step_change_decay_contamination_share (addendum D6).
    """
    return -float(delta) * (float(lam) ** int(n_new))


def step_change_decay_contamination_share(lam: float, n_new: int, n_old: int) -> float:
    """EXACT contamination share of an exp-decay estimator over a FINITE old-era window (addendum D6).

    With n_new newest-era points and n_old old-era points under geometric weights lam^age, the fraction
    of the estimator's total weight that lands on the (contaminating) OLD era is
        s = lam^{n1} (1 - lam^{n0}) / (1 - lam^{n1+n0}),   n1=n_new, n0=n_old.
    The residual step bias is then -delta * s. As n0 -> inf this reduces to lam^{n1} (the infinite-window
    form above); as lam -> 1 (no decay) s -> n0/(n1+n0) (simple count share). Era dummies give s = 0.
    """
    lam = float(lam)
    n1, n0 = int(n_new), int(n_old)
    if lam == 1.0:
        return n0 / (n1 + n0) if (n1 + n0) > 0 else 0.0
    denom = 1.0 - lam ** (n1 + n0)
    if denom == 0.0:
        return 0.0
    return (lam ** n1) * (1.0 - lam ** n0) / denom


def _mean_rps(cells, q_fn) -> float:
    """Mean ranked probability score over cells (addendum A9 dual-report metric).

    RPS = sum_j (cumQ_j - cumO_j)^2 where cumQ is the predictive CDF over ordered bins and cumO is the
    step CDF of the realized bin (0 below the won bin, 1 from it on). q_fn(cell)->per-bin probs. Lower
    is better. Ordered-bin native objective; reported alongside log-loss, optimizer NOT switched to it.
    """
    if not cells:
        return float("nan")
    total = 0.0
    for cell in cells:
        q = np.asarray(q_fn(cell), dtype=float)
        cumq = np.cumsum(q)
        cumo = np.zeros_like(cumq)
        cumo[cell["won_index"]:] = 1.0
        total += float(np.sum((cumq - cumo) ** 2))
    return total / len(cells)


def _logloss(cells, q_fn) -> float:
    """Mean interval-censored negative log-likelihood per cell under q_fn (the licensing metric)."""
    if not cells:
        return float("nan")
    tot = 0.0
    for cell in cells:
        q = np.asarray(q_fn(cell), dtype=float)
        tot -= math.log(max(float(q[cell["won_index"]]), EPS))
    return tot / len(cells)


def _fit_era_block(cells_era):
    """Fit one era's joint (b_city, log k) MLE + the era-summary phi_e=(b0_e, log k_e) and its variance.

    Returns a dict: cities, b_raw_by_city, b_shrunk_by_city (EB within era), b0_e, tau_b_e, k_e,
    log_k_e, ll (era log-lik at its OWN MLE), phi_hat=[b0_e, log k_e], V_diag=[var(b0_e), var(log k_e)],
    n. b0_e variance = tau_b^2/n_informative + mean(s2)/n (EB grand-mean variance proxy); log k_e
    variance from the era NLL curvature (delta method).
    """
    cities = sorted({c["city"] for c in cells_era})
    cidx = {city: i for i, city in enumerate(cities)}
    ci = [cidx[c["city"]] for c in cells_era]
    n_cities = len(cities)
    b_hat, k_e, _res = _fit_joint(cells_era, ci, n_cities)
    log_k_e = math.log(k_e)
    _lo, _hi, _sig, _cidx = _winbin_arrays(cells_era, ci)
    ll = -_neg_loglik_vec(np.r_[b_hat, log_k_e], _lo, _hi, _sig, _cidx, n_cities)

    cells_by_city = defaultdict(list)
    for j, cell in enumerate(cells_era):
        cells_by_city[cities[ci[j]]].append(cell)
    b_raw = {city: float(b_hat[cidx[city]]) for city in cities}
    s2 = {city: _city_bias_fisher(cells_by_city[city], b_raw[city], k_e) for city in cities}
    b0_e, tau_e = _eb_prior(b_raw, s2)
    b_shrunk = {city: _shrink(b_raw[city], s2[city], b0_e, tau_e)[0] for city in cities}

    # Variance of the era summary phi_e.
    fin_s2 = [v for v in s2.values() if math.isfinite(v)]
    n_inf = len(fin_s2)
    var_b0 = ((tau_e * tau_e) + (float(np.mean(fin_s2)) if fin_s2 else 1.0)) / max(n_inf, 1)
    var_logk = _logk_variance(cells_era, ci, n_cities, b_hat, log_k_e)
    return {
        "cities": cities, "b_raw_by_city": b_raw, "b_shrunk_by_city": b_shrunk,
        "s2_by_city": s2, "b0_e": float(b0_e), "tau_b_e": float(tau_e),
        "k_e": float(k_e), "log_k_e": float(log_k_e), "ll": float(ll),
        "phi_hat": [float(b0_e), float(log_k_e)],
        "V_diag": [float(var_b0), float(var_logk)],
        "n": len(cells_era),
    }


def _fit_era_loglik(cells_era) -> float:
    """Fast era log-lik at its OWN joint (b_city, log k) MLE — no Fisher/EB (bootstrap hot path).

    Returns max_phi ll over the era's cells. Used inside the parametric bootstrap where only the LRT
    statistic 2*(ll_free - ll_pooled) is needed, so the per-replicate Fisher-info + EB grand-mean work
    in _fit_era_block is pure waste. Same MLE, ~3x cheaper per era per replicate.
    """
    cities = sorted({c["city"] for c in cells_era})
    cidx = {city: i for i, city in enumerate(cities)}
    ci = [cidx[c["city"]] for c in cells_era]
    b_hat, k_e, _res = _fit_joint(cells_era, ci, len(cities))
    lo, hi, sig, cidxa = _winbin_arrays(cells_era, ci)
    return -_neg_loglik_vec(np.r_[b_hat, math.log(k_e)], lo, hi, sig, cidxa, len(cities))


def _logk_variance(cells, city_index, n_cities, b_hat, log_k_hat) -> float:
    """Var(log k) from the NLL curvature wrt log_k at the MLE (delta method); finite fallback 1.0."""
    h = 0.02
    lo, hi, sig, cidx = _winbin_arrays(cells, city_index)

    def nll_logk(lk: float) -> float:
        return _neg_loglik_vec(np.r_[b_hat, lk], lo, hi, sig, cidx, n_cities)

    f0 = nll_logk(log_k_hat)
    fp = nll_logk(log_k_hat + h)
    fm = nll_logk(log_k_hat - h)
    curv = (fp - 2.0 * f0 + fm) / (h * h)
    if not math.isfinite(curv) or curv <= 1e-9:
        return 1.0
    return 1.0 / curv


def _eb_era_diag(phi_hat, V_diag):
    """Diagonal EB normal-means shrinkage over eras (adapted from consult2 eb_era_diag, Q1.2).

    phi_hat: (E, p) era summary MLEs on the transformed scale. V_diag: (E, p) inverse-Hessian variances.
    For each coordinate j, fit the hierarchical N(phi0_j, tau2_j) by marginal likelihood over a tau2
    grid (incl. 0 = full pooling), then phi_tilde = posterior mean. Returns (post_mean, post_var, phi0,
    tau2). tau2_j -> 0 collapses an era to the pooled mean (the single-era / no-era-effect limit).
    """
    phi_hat = np.asarray(phi_hat, dtype=float)
    V_diag = np.asarray(V_diag, dtype=float)
    E, p = phi_hat.shape
    positive = V_diag[V_diag > 0]
    lo = max(float(np.min(positive)) * 1e-4, 1e-12) if positive.size else 1e-12
    hi = max(float(np.var(phi_hat, axis=0).max()) * 100.0,
             (float(np.max(positive)) * 100.0 if positive.size else lo * 10), lo * 10)
    tau2_grid = np.r_[0.0, np.exp(np.linspace(math.log(lo), math.log(hi), 400))]

    phi0 = np.zeros(p)
    tau2 = np.zeros(p)
    post_mean = np.zeros_like(phi_hat)
    post_var = np.zeros_like(phi_hat)
    for j in range(p):
        y = phi_hat[:, j]
        s2 = np.maximum(V_diag[:, j], 1e-15)
        best = None
        for t2 in tau2_grid:
            v = s2 + t2
            w = 1.0 / v
            muj = float(np.sum(w * y) / np.sum(w))
            nll = 0.5 * float(np.sum(np.log(v) + (y - muj) ** 2 / v))
            if best is None or nll < best[0]:
                best = (nll, float(t2), muj)
        _, t2, muj = best
        phi0[j] = muj
        tau2[j] = t2
        if t2 <= 1e-15:
            post_mean[:, j] = muj
            post_var[:, j] = 0.0
        else:
            prec = 1.0 / s2 + 1.0 / t2
            post_var[:, j] = 1.0 / prec
            post_mean[:, j] = post_var[:, j] * (y / s2 + muj / t2)
    return post_mean, post_var, phi0, tau2


def _era_lrt(ll_free: float, ll_pooled: float, n_eras: int, n_params_per_era: int):
    """Fixed-effect era LRT (consult2 era_lrt): stat = 2(ll_free - ll_pooled), df = (E-1)*p."""
    stat = 2.0 * (ll_free - ll_pooled)
    df = (n_eras - 1) * n_params_per_era
    return float(stat), int(df)


def _chi2_sf(stat: float, df: int):
    """Upper-tail chi-square p-value. scipy if present; else a regularized-gamma series fallback."""
    if df <= 0:
        return float("nan")
    try:
        from scipy.stats import chi2 as _chi2  # type: ignore
        return float(_chi2.sf(stat, df))
    except Exception:
        # Regularized upper incomplete gamma Q(df/2, stat/2) via series/continued-fraction (Numerical
        # Recipes gammq). Adequate for a reported p-value when scipy is unavailable.
        a = df / 2.0
        x = stat / 2.0
        if x < 0 or a <= 0:
            return float("nan")
        if x == 0:
            return 1.0
        if x < a + 1.0:
            ap = a
            s = 1.0 / a
            d = s
            for _ in range(500):
                ap += 1.0
                d *= x / ap
                s += d
                if abs(d) < abs(s) * 1e-12:
                    break
            gln = math.lgamma(a)
            return 1.0 - s * math.exp(-x + a * math.log(x) - gln)
        b = x + 1.0 - a
        c = 1e30
        d = 1.0 / b
        h = d
        for i in range(1, 500):
            an = -i * (i - a)
            b += 2.0
            d = an * d + b
            if abs(d) < 1e-30:
                d = 1e-30
            c = b + an / c
            if abs(c) < 1e-30:
                c = 1e-30
            d = 1.0 / d
            de = d * c
            h *= de
            if abs(de - 1.0) < 1e-12:
                break
        gln = math.lgamma(a)
        return h * math.exp(-x + a * math.log(x) - gln)


def _era_bootstrap_p(cells_by_era, pooled_b_by_city, pooled_k, observed_stat,
                     reps: int = ERA_BOOTSTRAP_REPS, seed: int = 12345):
    """Parametric bootstrap p-value for the Sigma_era=0 boundary test (Self-Liang; consult2 Q1.1).

    The variance-component null lies on the boundary, so the naive chi2 reference is wrong. Simulate
    `reps` datasets UNDER THE POOLED FIT (each cell's won bin redrawn from the pooled categorical
    probabilities, holding the geometry fixed), refit pooled + free-era on each, and record the LRT
    stat. p = (1 + #{stat_boot >= observed}) / (reps + 1). Eras with too few cells are pooled in both
    the simulate and the refit so the df matches the observed test.
    """
    rng = np.random.default_rng(seed)
    eras = sorted(cells_by_era.keys())
    # Flatten once; remember era + the pooled probability vector per cell.
    flat = []
    for era in eras:
        for cell in cells_by_era[era]:
            p_pool = _cell_probs(cell, pooled_b_by_city.get(cell["city"], 0.0), pooled_k)
            flat.append((era, cell, p_pool))

    count_ge = 0
    n_params_per_era = 2
    for _ in range(reps):
        # Redraw won bins under the pooled model.
        sim_by_era = defaultdict(list)
        for era, cell, p_pool in flat:
            won = int(rng.choice(len(p_pool), p=p_pool))
            sc = dict(cell)
            sc["won_index"] = won
            sim_by_era[era].append(sc)
        sim_all = [c for era in eras for c in sim_by_era[era]]
        # Pooled refit.
        cities = sorted({c["city"] for c in sim_all})
        cidx = {city: i for i, city in enumerate(cities)}
        ci = [cidx[c["city"]] for c in sim_all]
        b_p, k_p, _ = _fit_joint(sim_all, ci, len(cities))
        _plo, _phi, _psig, _pcidx = _winbin_arrays(sim_all, ci)
        ll_pool = -_neg_loglik_vec(np.r_[b_p, math.log(k_p)], _plo, _phi, _psig, _pcidx, len(cities))
        # Free-era refit (only eras meeting the cell minimum get a free block; others fold into pooled).
        ll_free = 0.0
        n_free_eras = 0
        for era in eras:
            ce = sim_by_era[era]
            if len(ce) < ERA_MIN_CELLS:
                # Contribute this era's cells at the POOLED params (no extra freedom).
                cset = sorted({c["city"] for c in ce})
                cmap = {c: i for i, c in enumerate(cset)}
                cci = [cmap[c["city"]] for c in ce]
                bb = np.array([b_p[cidx[c]] if c in cidx else 0.0 for c in cset])
                flo, fhi, fsig, fcidx = _winbin_arrays(ce, cci)
                ll_free += -_neg_loglik_vec(np.r_[bb, math.log(k_p)], flo, fhi, fsig, fcidx, len(cset))
                continue
            ll_free += _fit_era_loglik(ce)   # fast log-lik-only era MLE (bootstrap hot path)
            n_free_eras += 1
        stat_boot, _df = _era_lrt(ll_free, ll_pool, n_eras=max(n_free_eras + 1, 2),
                                  n_params_per_era=n_params_per_era)
        if stat_boot >= observed_stat:
            count_ge += 1
    return float((1 + count_ge) / (reps + 1))


def _max_traded_bin_shift_ucb(cells_by_era, pooled_b_by_city, pooled_k, free_blocks):
    """UCB95 of the max traded-bin probability shift between POOLED and FREE-ERA fits (addendum A5).

    For each cell, compare the pooled q vs that era's free-era q and take the max |delta| over bins; the
    decision-scale era impact is the max over cells, and we add a 1.96*sd Wald upper bound across cells.
    Used by the decision rule: full pooling licensed only if this UCB95 < EPSILON_POOL.
    """
    per_era_max = {}
    for era, blk in free_blocks.items():
        shifts = []
        for cell in cells_by_era[era]:
            q_pool = _cell_probs(cell, pooled_b_by_city.get(cell["city"], 0.0), pooled_k)
            b_free = blk["b_shrunk_by_city"].get(cell["city"], blk["b0_e"])
            q_free = _cell_probs(cell, b_free, blk["k_e"])
            shifts.append(float(np.max(np.abs(q_free - q_pool))))
        if shifts:
            arr = np.asarray(shifts)
            mx = float(arr.max())
            sd = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
            per_era_max[era] = {"max_shift": round(mx, 5),
                                "ucb95": round(mx + 1.96 * sd / math.sqrt(arr.size), 5)}
    overall = max((v["ucb95"] for v in per_era_max.values()), default=float("nan"))
    return per_era_max, float(overall)


def _run_era_mode(cells, k_old, w_old, seed: int = 17, bootstrap_reps: int = ERA_BOOTSTRAP_REPS):
    """Triple fit (pooled / free-era / EB) + LRT + bootstrap + addendum-A5 decision rule + RPS report.

    cells already carry an `era` label. Returns the era_mode artifact block. bootstrap_reps defaults to
    the production ERA_BOOTSTRAP_REPS (>=200, addendum requirement); tests may lower it for speed.
    """
    cells_by_era = defaultdict(list)
    for c in cells:
        cells_by_era[c["era"]].append(c)
    eras = sorted(cells_by_era.keys())
    n_by_era = {e: len(cells_by_era[e]) for e in eras}
    # Free-era blocks only for eras meeting the cell minimum; thinner eras still POOL (still modeled).
    free_eras = [e for e in eras if n_by_era[e] >= ERA_MIN_CELLS]

    # (a) POOLED fit over ALL eras.
    cities = sorted({c["city"] for c in cells})
    cidx = {city: i for i, city in enumerate(cities)}
    ci_all = [cidx[c["city"]] for c in cells]
    b_pool, k_pool, _ = _fit_joint(cells, ci_all, len(cities))
    pooled_b_by_city = {city: float(b_pool[cidx[city]]) for city in cities}
    _alo, _ahi, _asig, _acidx = _winbin_arrays(cells, ci_all)
    ll_pooled = -_neg_loglik_vec(np.r_[b_pool, math.log(k_pool)], _alo, _ahi, _asig, _acidx, len(cities))

    # (b) FREE-ERA fit per era.
    free_blocks = {e: _fit_era_block(cells_by_era[e]) for e in free_eras}
    ll_free = sum(blk["ll"] for blk in free_blocks.values())
    # Eras below the minimum contribute at the pooled params (no extra df, keeps the LRT honest).
    for e in eras:
        if e not in free_blocks:
            ce = cells_by_era[e]
            cset = sorted({c["city"] for c in ce})
            cmap = {c: i for i, c in enumerate(cset)}
            cci = [cmap[c["city"]] for c in ce]
            bb = np.array([pooled_b_by_city.get(c, 0.0) for c in cset])
            flo, fhi, fsig, fcidx = _winbin_arrays(ce, cci)
            ll_free += -_neg_loglik_vec(np.r_[bb, math.log(k_pool)], flo, fhi, fsig, fcidx, len(cset))

    # (c) EB partial pooling over the era summaries phi_e = (b0_e, log k_e).
    eb_block = None
    if len(free_blocks) >= 2:
        ordered = sorted(free_blocks.keys())
        phi_hat = np.array([free_blocks[e]["phi_hat"] for e in ordered])
        V_diag = np.array([free_blocks[e]["V_diag"] for e in ordered])
        post_mean, post_var, phi0, tau2 = _eb_era_diag(phi_hat, V_diag)
        eb_block = {"era_order": ordered, "phi0": [round(float(x), 5) for x in phi0],
                    "tau2": [round(float(x), 6) for x in tau2], "per_era": {}}
        for i, e in enumerate(ordered):
            b0_tilde, logk_tilde = float(post_mean[i, 0]), float(post_mean[i, 1])
            # Per-city EB-shrunk biases re-centered toward the EB era mean b0_tilde.
            blk = free_blocks[e]
            b_city_eb = {}
            for city in blk["cities"]:
                s2c = blk["s2_by_city"][city]
                # shrink the era's raw city bias toward the EB era center using the era tau.
                b_city_eb[city] = round(_shrink(blk["b_raw_by_city"][city], s2c, b0_tilde,
                                                blk["tau_b_e"])[0], 4)
            eb_block["per_era"][e] = {
                "b0_eb": round(b0_tilde, 4), "k_eb": round(math.exp(logk_tilde), 4),
                "log_k_eb": round(logk_tilde, 4),
                "post_var_b0": round(float(post_var[i, 0]), 6),
                "post_var_logk": round(float(post_var[i, 1]), 6),
                "b_shrunk_per_city": b_city_eb,
            }

    # EB log-lik on its own data (sum over eras of each era's ll under its EB-pooled params).
    ll_eb = None
    if eb_block is not None:
        ll_eb = 0.0
        for e, info in eb_block["per_era"].items():
            ce = cells_by_era[e]
            ll_eb += -_logloss(ce, lambda c, _i=info: _cell_probs(
                c, _i["b_shrunk_per_city"].get(c["city"], _i["b0_eb"]), _i["k_eb"])) * len(ce)

    # Era-effect tests (fixed-effect LRT). E = number of distinct fitted era-blocks (each contributing a
    # free (b0_e, log k_e) pair) plus one if any thin era folds into the pooled baseline. df = (E-1)*p,
    # p=2. Floor E at 2 so a single free era vs pooled still yields the canonical 1*p df.
    E_total = max(len(free_blocks), 1) + (1 if any(e not in free_blocks for e in eras) else 0)
    stat, df = _era_lrt(ll_free, ll_pooled, n_eras=max(E_total, 2), n_params_per_era=2)
    p_lrt = _chi2_sf(stat, df) if df > 0 and math.isfinite(stat) else float("nan")

    boot_p = float("nan")
    if len(free_blocks) >= 1 and math.isfinite(stat):
        boot_p = _era_bootstrap_p(cells_by_era, pooled_b_by_city, k_pool, observed_stat=stat,
                                  reps=bootstrap_reps, seed=seed)

    # Decision-scale era impact (addendum A5): UCB95 of max traded-bin prob shift pooled vs free.
    # REPORTED OBSERVATION ONLY (addendum D1) — no longer a pool/no-pool switch.
    per_era_shift, shift_ucb95 = _max_traded_bin_shift_ucb(cells_by_era, pooled_b_by_city, k_pool,
                                                           free_blocks)

    # SHIPPED ESTIMATOR = EB partial pooling, ALWAYS (addendum D1, supersedes the A5 pretest switch).
    # Pretest estimators (branch full-vs-EB on the LRT/score/bootstrap p) have UNBOUNDED relative risk
    # near the null, so we never switch on the tests. Instead we ALWAYS ship EB: Sigma_era -> 0 makes EB
    # collapse to full pooling automatically (no era effect), real era effects make it near-separate, and
    # EB -> newest-era MLE as newest-era n grows. The LRT p, bootstrap p, and decision-scale shift stay
    # in the artifact purely as OBSERVATIONS (reported, never branched on).
    p_era = boot_p if math.isfinite(boot_p) else p_lrt
    verdict = "EB_PARTIAL_POOLING"
    verdict_reason = ("addendum D1: ALWAYS ship EB partial pooling — never pretest-switch full-vs-EB "
                      "(unbounded relative risk near the null). Sigma_era->0 auto-collapses EB to full "
                      "pooling; era effects make it near-separate. LRT/bootstrap p + decision-scale shift "
                      "below are REPORTED OBSERVATIONS only.")
    # Observation-only flags (what a pretest WOULD have said — for human review, not used by the fit).
    diag_stat_absent = (math.isfinite(p_era) and p_era >= P_ERA_POOL)
    diag_pract_absent = (math.isfinite(shift_ucb95) and shift_ucb95 < EPSILON_POOL)
    diag_pretest_would_say = ("FULL_POOLING" if (diag_stat_absent and diag_pract_absent)
                              else "EB_PARTIAL_POOLING")

    # Dual objective report (addendum A9): log-loss AND RPS for each model on ALL era cells.
    def _q_pool(c):
        return _cell_probs(c, pooled_b_by_city.get(c["city"], 0.0), k_pool)

    def _q_free(c):
        blk = free_blocks.get(c["era"])
        if blk is None:
            return _q_pool(c)
        return _cell_probs(c, blk["b_shrunk_by_city"].get(c["city"], blk["b0_e"]), blk["k_e"])

    def _q_eb(c):
        if eb_block is None or c["era"] not in eb_block["per_era"]:
            return _q_pool(c)
        info = eb_block["per_era"][c["era"]]
        return _cell_probs(c, info["b_shrunk_per_city"].get(c["city"], info["b0_eb"]), info["k_eb"])

    dual = {
        "pooled": {"logloss": round(_logloss(cells, _q_pool), 5), "rps": round(_mean_rps(cells, _q_pool), 5)},
        "free_era": {"logloss": round(_logloss(cells, _q_free), 5), "rps": round(_mean_rps(cells, _q_free), 5)},
        "eb": {"logloss": round(_logloss(cells, _q_eb), 5), "rps": round(_mean_rps(cells, _q_eb), 5)},
    }
    # Held-out RPS/log-loss on a temporal walk-forward split PER ERA (leak-free dual report, A9).
    holdout = _era_holdout_dual(cells_by_era, free_eras)

    per_era_out = {}
    for e in eras:
        if e in free_blocks:
            blk = free_blocks[e]
            per_era_out[e] = {
                "n": n_by_era[e], "fitted": "free",
                "b0_e": round(blk["b0_e"], 4), "k_e": round(blk["k_e"], 4),
                "tau_b_e": round(blk["tau_b_e"], 4),
                "b_shrunk_per_city": {c: round(v, 4) for c, v in blk["b_shrunk_by_city"].items()},
                "phi_hat": [round(x, 5) for x in blk["phi_hat"]],
                "V_diag": [round(x, 6) for x in blk["V_diag"]],
            }
        else:
            per_era_out[e] = {"n": n_by_era[e], "fitted": "pooled_thin",
                              "note": f"era below {ERA_MIN_CELLS}-cell minimum; folded into pooled fit"}

    return {
        "schema_block": "era_mode",
        "eras": eras,
        "n_by_era": n_by_era,
        "free_eras": free_eras,
        "era_definition": {
            ERA_LIVE: "forecast_posteriors live AIFS-sampled posterior (data_version *_v1, ~1wk retention)",
            ERA_HIST: f"calibration_pairs historical bins (error_model_family={ERA_HIST_EMF!r}, 2024-..)",
        },
        "pooled": {"k": round(k_pool, 4), "ll": round(ll_pooled, 3),
                   "b_by_city": {c: round(v, 4) for c, v in pooled_b_by_city.items()}},
        "free_era_loglik": round(ll_free, 3),
        "eb_loglik": (round(ll_eb, 3) if ll_eb is not None else None),
        "per_era": per_era_out,
        "eb_partial_pooling": eb_block,
        "shipped_estimator": "eb_partial_pooling",   # addendum D1: EB is ALWAYS the shipped estimate
        "lrt": {"stat": round(stat, 4) if math.isfinite(stat) else None, "df": df,
                "p_value": round(p_lrt, 5) if math.isfinite(p_lrt) else None,
                "role": "REPORTED_DIAGNOSTIC_ONLY (addendum D1; never branched on)",
                "definition": "2*(ll_free - ll_pooled) ~ chi2_{(E-1)p}, p=2 (b0,log k) per era"},
        "boundary_bootstrap": {"reps": bootstrap_reps, "p_value": round(boot_p, 5) if math.isfinite(boot_p) else None,
                               "role": "REPORTED_DIAGNOSTIC_ONLY (addendum D1; never branched on)",
                               "definition": "parametric bootstrap under pooled fit (Self-Liang boundary test for Sigma_era=0)"},
        "decision_scale_shift": {"per_era_ucb95": per_era_shift, "max_ucb95": round(shift_ucb95, 5) if math.isfinite(shift_ucb95) else None,
                                 "epsilon_pool": EPSILON_POOL, "role": "REPORTED_DIAGNOSTIC_ONLY (addendum D1)"},
        "decision_rule": {
            "verdict": verdict, "reason": verdict_reason,
            "law": "addendum_D1_always_eb",
            "p_era_used_for_diagnostic": round(p_era, 5) if math.isfinite(p_era) else None,
            "p_era_source": "bootstrap" if math.isfinite(boot_p) else "lrt_chi2",
            "pretest_would_have_said": diag_pretest_would_say,
            "pretest_superseded_note": ("the A5 pretest full-vs-EB switch is SUPERSEDED by D1: pretest "
                                        "estimators have unbounded relative risk near the null; we always "
                                        "ship EB (it auto-collapses to full pooling when Sigma_era->0)."),
            "thresholds_for_diagnostic": {"p_era_min": P_ERA_POOL, "epsilon_pool": EPSILON_POOL, "epsilon_edge": EPSILON_EDGE},
            "newest_era_only_note": ("EB -> newest-era MLE as newest-era n grows (shrinkage O(1/n)); "
                                     "EB partial pooling dominates newest-only at n in 300-500 (A5)."),
        },
        "dual_objective_report": {"in_sample": dual, "holdout_walk_forward": holdout,
                                  "note": "addendum A9: report BOTH interval log-loss and RPS; optimizer NOT switched to RPS"},
    }


def _era_holdout_dual(cells_by_era, free_eras):
    """Per-era expanding-window split: fit free block on train days, score test days (log-loss + RPS).

    Leak-free dual report (A9). Eras with <3 settled days cannot split and are reported INSUFFICIENT.
    """
    out = {}
    for era in free_eras:
        cells = cells_by_era[era]
        days = sorted({c["target_date"] for c in cells})
        if len(days) < 3:
            out[era] = {"status": "INSUFFICIENT_DAYS", "n_days": len(days)}
            continue
        cut = days[max(1, int(round(GATE_TRAIN_FRAC * len(days))) - 1)]
        cut = min(cut, days[-2])
        train = [c for c in cells if c["target_date"] <= cut]
        test = [c for c in cells if c["target_date"] > cut]
        if not train or not test:
            out[era] = {"status": "EMPTY_FOLD"}
            continue
        blk = _fit_era_block(train)

        def _q(c, _b=blk):
            return _cell_probs(c, _b["b_shrunk_by_city"].get(c["city"], _b["b0_e"]), _b["k_e"])

        out[era] = {"status": "OK", "cut_date": cut, "n_train": len(train), "n_test": len(test),
                    "test_logloss": round(_logloss(test, _q), 5), "test_rps": round(_mean_rps(test, _q), 5)}
    return out


def _write(path: str, obj) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Joint per-city bias b_loc + global scale k by interval-censored categorical MLE "
                    "(authority Task 1.1 / Migration Step 1)."
    )
    ap.add_argument("--fcst", default=FCST_DEFAULT, help="zeus-forecasts.db (forecast_posteriors + settlement_outcomes).")
    ap.add_argument("--out", default=OUT_DEFAULT, help="output state/bias_scale_fit.json path.")
    ap.add_argument("--k-old", type=float, default=K_OLD_DEFAULT, help="OLD variance-only k for the gate baseline (default 1.5833).")
    ap.add_argument("--min-cells", type=int, default=MIN_CELLS_TOTAL, help="min total settled cells to fit a global k (else refuse).")
    ap.add_argument("--sigma-scale-fit", default=SIGMA_SCALE_FIT, help="path to the CURRENT sigma_scale_fit.json for the OLD-baseline uniform-mixture w.")
    ap.add_argument("--gate", action="store_true", help="compute + store the walk-forward Step-1 gate evidence.")
    ap.add_argument("--era-mode", action="store_true", dest="era_mode",
                    help="ALSO join the historical calibration_pairs era so the FULL settled history is "
                         "usable; pooled/free-era/EB-partial-pooling triple fit + LRT + boundary "
                         "bootstrap + addendum-A5 decision rule (addendum C1 deploy unlock).")
    ap.add_argument("--era-bootstrap-reps", type=int, default=ERA_BOOTSTRAP_REPS, dest="era_bootstrap_reps",
                    help=f"parametric-bootstrap replicates for the Sigma_era=0 boundary test "
                         f"(default {ERA_BOOTSTRAP_REPS}; addendum requires >=200 for the deploy artifact).")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.fcst}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute(_FIT_QUERY)
        rows = cur.fetchall()
        # Era-mode loads the historical calibration_pairs era + counts DISPUTED exclusions (A6).
        era_cells = []
        disputed_excluded = None
        if args.era_mode:
            # Count DISPUTED first (separate cursor state), then STREAM the historical era so the
            # 48M-row calibration_pairs join never materializes in RAM (rows arrive ordered by city,date).
            disputed_excluded = cur.execute(
                "SELECT COUNT(*) FROM settlement_outcomes "
                "WHERE temperature_metric='high' AND authority='DISPUTED'").fetchone()[0]
            # addendum D2: per-era disputed counts (a disputed (city,date) overlapping each era's
            # date span) + whether provenance_json carries a recoverable ambiguity set (the union of
            # plausible bins from competing sources) that a future CAR interval-widening upgrade could use.
            disputed_provenance_has_ambiguity = cur.execute(
                "SELECT COUNT(*) FROM settlement_outcomes "
                "WHERE temperature_metric='high' AND authority='DISPUTED' "
                "  AND provenance_json IS NOT NULL "
                "  AND (provenance_json LIKE '%ambig%' OR provenance_json LIKE '%candidate%' "
                "    OR provenance_json LIKE '%competing%')").fetchone()[0]
            hist_cur = con.cursor()
            hist_cur.execute(_ERA_HIST_QUERY, (ERA_HIST_EMF,))
            era_cells = _build_hist_cells(hist_cur)
            hist_cur.close()
    finally:
        con.close()

    cells = _build_cells_with_city(rows)
    n_total = len(cells)
    fitted_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    td_dates = sorted({c["target_date"] for c in cells})
    window = f"settled-{td_dates[0]}..{td_dates[-1]}" if td_dates else "settled-empty"
    w_old = _load_old_w(args.sigma_scale_fit)

    if n_total < args.min_cells:
        artifact = {
            "schema": SCHEMA, "fitted": False, "fitted_at": fitted_at,
            "n_total": n_total, "refusal_reason": f"INSUFFICIENT_CELLS:{n_total}<{args.min_cells}",
            "data_window": window, "basis": AUTHORITY,
        }
        _write(args.out, artifact)
        print(f"[bias-scale] REFUSED: {n_total} < {args.min_cells} settled cells -> no fit written")
        return 0

    cities = sorted({c["city"] for c in cells})
    cidx = {city: i for i, city in enumerate(cities)}
    city_index = [cidx[c["city"]] for c in cells]
    n_cities = len(cities)

    # Pass 1: joint MLE (no prior).
    b_hat, k_hat, _res = _fit_joint(cells, city_index, n_cities)

    # Per-city raw bias, sample count, inverse Fisher info.
    cells_by_city = defaultdict(list)
    for ci, cell in enumerate(cells):
        cells_by_city[cities[city_index[ci]]].append(cell)
    b_raw_by_city = {city: float(b_hat[cidx[city]]) for city in cities}
    n_by_city = {city: len(cells_by_city[city]) for city in cities}
    s2_by_city = {city: _city_bias_fisher(cells_by_city[city], b_raw_by_city[city], k_hat)
                  for city in cities}

    # Empirical-Bayes prior + shrinkage.
    b0, tau_b = _eb_prior(b_raw_by_city, s2_by_city)
    per_city = {}
    b_shrunk_full = {}
    for city in cities:
        b_shrunk, s_factor = _shrink(b_raw_by_city[city], s2_by_city[city], b0, tau_b)
        b_shrunk_full[city] = b_shrunk
        per_city[city] = {
            "b_raw": round(b_raw_by_city[city], 4),
            "b_shrunk": round(b_shrunk, 4),
            "n": n_by_city[city],
            "s2": (round(s2_by_city[city], 6) if math.isfinite(s2_by_city[city]) else None),
            "shrink_factor": round(s_factor, 4),
        }

    # k CI from the joint Hessian.
    k_ci = _k_ci_from_hessian(cells, city_index, n_cities, b_hat, k_hat)

    # Walk-forward gate (optional but the deploy evidence).
    gate = None
    if args.gate:
        g = _run_gate(cells, city_index, cities, args.k_old, w_old)
        gate = g[0] if isinstance(g, tuple) else g  # tuple -> (gate, ...); else status-only

    # Full-set sigma-floor accounting: how many cells would have k*sigma below the rounding floor
    # under the NEW distribution (informs whether the floor binds in production).
    full_floor_hits = 0
    for cell in cells:
        cell.pop("_floor_hit", None)
        _q_new(cell, b_shrunk_full.get(cell["city"], b0), k_hat)
        if cell.pop("_floor_hit", False):
            full_floor_hits += 1

    # 7-loss replay (uses FULL-fit shrunk biases — the production artifact's biases).
    cells_by_key = {(c["city"], c["target_date"]): c for c in cells}
    replay = _replay_loss_families(cells_by_key, b_shrunk_full, b0, args.k_old, w_old, k_hat)

    # --- ERA-MODE (addendum C1): combine the live + historical eras and run the triple fit ----------
    era_mode_block = None
    if args.era_mode:
        all_era_cells = list(cells) + list(era_cells)   # live cells already labelled ERA_LIVE
        live_days = sorted({c["target_date"] for c in cells})
        hist_days = sorted({c["target_date"] for c in era_cells})
        data_audit = {
            "binding_constraint": ("forecast_posteriors retains ~1 week -> only the live era joins there; "
                                   "the historical settlements join calibration_pairs (different pipeline "
                                   "era) -> the full settled history becomes usable as partially-pooled "
                                   "evidence (A5)."),
            "sources": {
                ERA_LIVE: {"table": "forecast_posteriors", "join": "(city,target_date,temperature_metric)",
                           "mu_center": "q_json mode-bin centre", "sigma": "implied from mode-bin prob",
                           "n_cells": len(cells),
                           "date_range": (f"{live_days[0]}..{live_days[-1]}" if live_days else None)},
                ERA_HIST: {"table": "calibration_pairs", "filter": f"error_model_family={ERA_HIST_EMF!r}",
                           "join": "(city,target_date,temperature_metric) -> settlement_outcomes VERIFIED",
                           "mu_center": "chosen decision_group range_label mode (max p_raw)",
                           "sigma": "implied from mode-bin p_raw", "n_cells": len(era_cells),
                           "date_range": (f"{hist_days[0]}..{hist_days[-1]}" if hist_days else None)},
            },
            "disputed_settlements_excluded_high": disputed_excluded,
            "dispute_treatment": "EXCLUDE (addendum A6 primary): authority='VERIFIED' filter drops them; never down-weighted.",
            "dispute_car_note": (  # addendum D2: future CAR interval-widening hook
                f"{disputed_provenance_has_ambiguity} of {disputed_excluded} disputed 'high' "
                "rows carry an ambiguity/candidate/competing marker in provenance_json. If the plausible-"
                "bin set is recoverable, the sanctioned FUTURE upgrade is CAR interval-widening (censor "
                "over [min lower, max upper] of the competing bins) instead of exclusion. NOT implemented "
                "this pass (primary = EXCLUDE); recorded for the next iteration."),
            "window_widening": (f"live-only days={len(live_days)} -> era-mode joinable days="
                                f"{len(sorted({c['target_date'] for c in all_era_cells}))}"),
        }
        if len(all_era_cells) >= ERA_MIN_CELLS:
            era_mode_block = _run_era_mode(all_era_cells, args.k_old, w_old,
                                           bootstrap_reps=args.era_bootstrap_reps)
            era_mode_block["data_audit"] = data_audit
            era_mode_block["disputed_excluded"] = disputed_excluded
            era_mode_block["thin_window"] = bool(len(cells) < 60)  # live era still thin; kept for semantics
        else:
            era_mode_block = {"schema_block": "era_mode", "status": "INSUFFICIENT_ERA_CELLS",
                              "n_total_era_cells": len(all_era_cells), "data_audit": data_audit}

    qhash = hashlib.sha256((_FIT_QUERY + f"|window={window}").encode("utf-8")).hexdigest()[:16]
    artifact = {
        "schema": SCHEMA,
        "fitted": True,
        "fitted_at": fitted_at,
        "n_total": n_total,
        "n_cities": n_cities,
        "k_global": round(k_hat, 4),
        "k_global_ci95": k_ci,
        "b0": round(b0, 4),
        "tau_b": round(tau_b, 4),
        "per_city": per_city,
        "walk_forward_gate": gate,
        "era_mode": era_mode_block,
        "loss_family_replay": replay,
        "data_window": window,
        "basis": AUTHORITY,
        "k_old_baseline": round(args.k_old, 4),
        "w_old_baseline": round(w_old, 4),
        "sigma_floor_basis": "step/(2*sqrt(3)) rounding-implied uniform-on-one-bin SD",
        "new_dist_floor_hits_full": full_floor_hits,
        "new_dist_floor_hit_frac_full": round(full_floor_hits / n_total, 4) if n_total else None,
        "lead_buckets": list(_fss.LEAD_BUCKETS.keys()),
        "source": "forecast_posteriors ⋈ settlement_outcomes(authority=VERIFIED), high metric, no-leak lead-bucketed freshest-posterior",
        "source_query_hash": qhash,
        "optimizer": ("scipy_lbfgsb" if _HAVE_SCIPY else "coordinate_descent_grid"),
        "note": "Materializer NOT wired; deploy is operator-gated AFTER --gate review (Migration Step 1).",
    }
    _write(args.out, artifact)

    # --- printed summary -------------------------------------------------------------------------
    print(f"[bias-scale] wrote {args.out}  (window={window}, n_total={n_total}, n_cities={n_cities})")
    print(f"    k_global = {k_hat:.4f}  CI95={k_ci}   (OLD variance-only k = {args.k_old})")
    print(f"    EB prior: b0 = {b0:.4f}, tau_b = {tau_b:.4f}")
    notable = sorted(cities, key=lambda c: -abs(per_city[c]["b_shrunk"]))[:8]
    print("    notable per-city biases (b_shrunk, n):")
    for c in notable:
        pc = per_city[c]
        print(f"        {c:<16} b_raw={pc['b_raw']:+.3f}  b_shrunk={pc['b_shrunk']:+.3f}  n={pc['n']}  S={pc['shrink_factor']}")
    if gate and gate.get("status") == "OK":
        pp = gate["paired_prequential_logloss"]
        mr = gate["modal_class_reliability"]
        kg = gate["k_reduction_gate"]
        pit = gate["pit_rank_histogram"]
        mq = gate["modal_q_trajectory"]
        print("    --- WALK-FORWARD GATE (Step 1) ---")
        if gate.get("thin_window"):
            print(f"        ** THIN_WINDOW: {gate['n_days_test']} test days — gate is INDICATIVE, not robust **")
        print(f"        split cut_date={gate['cut_date']}  train={gate['n_train_cells']} test={gate['n_test_cells']} cells ({gate['n_days_test']} test days)")
        print(f"        mean d = {pp['mean_d']:+.5f}  SE={pp['se_d_dayblock_bootstrap']}  LB(5%)={pp['lower_bound_5pct']}  frac_improved={pp['frac_cells_improved']}")
        print(f"        DEPLOY SIGNAL (LB>0): {pp['deploy_signal']}")
        print(f"        modal reliability  old={mr['old']}  new={mr['new']}  shrinks={mr['shrinks']}")
        print(f"        k-reduction gate: k_old={kg['k_old']} k_new={kg['k_new']} (must be < {kg['threshold_max']})  PASSED={kg['passed']}")
        if not kg['passed']:
            print("           !! k did NOT drop below 0.85*k_old -> bias-absorption was NOT dominant over this window (reported, not forced)")
        print(f"        PIT chi-square vs uniform: old={pit['old_chisquare_vs_uniform']} new={pit['new_chisquare_vs_uniform']}  new_closer_to_uniform={pit['new_closer_to_uniform']}")
        print(f"        modal-q trajectory: old_mean={mq['old_mean_modal_q']} (~{mq['expected_old_approx']}) new_mean={mq['new_mean_modal_q']} (target {mq['expected_new_range']})")
        print(f"        NEW sigma-floor hits: {gate['new_dist_floor_hits']}/{gate['n_test_cells']} ({gate['new_dist_floor_hit_frac']})")
    elif gate:
        print(f"    --- WALK-FORWARD GATE: {gate.get('status')} ---")
    print("    --- 7-LOSS-FAMILY REPLAY (old-q vs new-q for the WON bin) ---")
    for r in replay:
        if r["status"] != "SETTLED":
            print(f"        {r['city']:<16} {r['target_date']}  {r['status']}")
        else:
            print(f"        {r['city']:<16} {r['target_date']}  won={r['won_bin']:<16} "
                  f"q_old={r['q_old_won']:.4f} -> q_new={r['q_new_won']:.4f}  "
                  f"(d={r['delta_q']:+.4f}, b_city={r['b_shrunk_city']:+.3f}, dist_from_mode={r['dist_from_mode_steps']})")

    if era_mode_block is not None:
        print("    --- ERA-MODE (addendum C1: full-history era-aware partial pooling) ---")
        em = era_mode_block
        da = em.get("data_audit", {})
        print(f"        DISPUTED settlements excluded (A6): {em.get('disputed_excluded')}")
        print(f"        window widening: {da.get('window_widening')}")
        if em.get("status") == "INSUFFICIENT_ERA_CELLS":
            print(f"        status={em['status']} n_total_era_cells={em['n_total_era_cells']}")
        else:
            print(f"        eras: {em['eras']}   n_by_era={em['n_by_era']}   free_eras={em['free_eras']}")
            print(f"        pooled k={em['pooled']['k']}  ll_pooled={em['pooled']['ll']}")
            for e, info in em["per_era"].items():
                if info.get("fitted") == "free":
                    print(f"          era {e:<22} n={info['n']:<5} k_e={info['k_e']}  b0_e={info['b0_e']}")
                else:
                    print(f"          era {e:<22} n={info['n']:<5} {info.get('note','')}")
            lrt = em["lrt"]; bb = em["boundary_bootstrap"]; dr = em["decision_rule"]
            print(f"        LRT stat={lrt['stat']} df={lrt['df']} p={lrt['p_value']}   bootstrap p={bb['p_value']} ({bb['reps']} reps)")
            print(f"        decision-scale max UCB95 bin-shift={em['decision_scale_shift']['max_ucb95']} (eps_pool={EPSILON_POOL})")
            print(f"        DECISION: {dr['verdict']}  ({dr['reason']})")
            ds = em["dual_objective_report"]["in_sample"]
            print(f"        in-sample logloss/RPS  pooled={ds['pooled']}  free={ds['free_era']}  eb={ds['eb']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
