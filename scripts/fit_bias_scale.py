#!/usr/bin/env python3
# Lifecycle: created=2026-06-12; last_reviewed=2026-06-12; last_reused=never
# Purpose: JOINT per-city location-bias b_loc + global spread-scale k fit by interval-censored
#   categorical MLE (+ empirical-Bayes shrinkage of b_loc), replacing the variance-only k fit that
#   PROVABLY absorbed unmodeled center bias (k_wrong^2 = k_true^2 + (delta/sigma)^2).
# Reuse: Re-run weekly so (b,k) track settled-data growth; review state/bias_scale_fit.json + the
#   --gate walk-forward evidence BEFORE wiring the materializer (operator-gated deploy, Step 1).
# Authority basis: docs/authority/statistical_calibration_authority_2026-06-12.txt Task 1.1 +
#   Migration order Step 1 (interval-censored categorical likelihood, identifiability proof, EB
#   shrinkage S_l = tau^2/(tau^2+s_l^2), prequential paired log-loss gate). Data pipeline REUSED from
#   scripts/fit_sigma_scale.py (provenance-audited 2026-06-12: CURRENT_REUSABLE — same VERIFIED
#   forecast_posteriors join, mode=ro, lead-bucketed freshest-posterior, bin/winning-index/preimage
#   parsing; that script's k-only Bernoulli objective is the proven pathology this one replaces, but
#   its data plumbing is correct and leak-disciplined).
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
        parsed = _parse_cell(q_json_text)
        if parsed is None:
            continue
        items, mode_index, step = parsed
        q_mode = items[mode_index][1]
        sigma_impl = _sigma_implied(q_mode, half_step=step / 2.0)
        if sigma_impl is None:
            continue
        won_index = _winning_index(items, winning_bin, sval, step=step)
        if won_index is None:
            continue
        lo, hi = _cell_edges(items, mode_index, step)
        q_materialized = np.asarray([it[1] for it in items], dtype=float)
        cells.append({
            "city": city, "target_date": tdate, "bucket": bucket,
            "n_bins": len(items), "sigma_impl": float(sigma_impl), "mode_index": int(mode_index),
            "won_index": int(won_index), "step": float(step),
            "edges_lo": lo, "edges_hi": hi,
            "q_materialized": q_materialized,
            "mode_deg": items[mode_index][2],
            "won_deg": items[won_index][2],
            "won_label": items[won_index][0],
        })
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


def _fit_joint(cells, city_index, n_cities: int, prior_b0=None, prior_tau=None):
    """Joint MLE/MAP over [b_loc..., log_k] by L-BFGS-B. Returns (b_hat[n_cities], k_hat, res)."""
    theta0 = np.r_[np.zeros(n_cities), 0.0]  # b=0, log_k=0 (k=1) — the regression anchor
    if _HAVE_SCIPY and _scipy_minimize is not None:
        res = _scipy_minimize(
            _neg_loglik, theta0,
            args=(cells, city_index, n_cities, prior_b0, prior_tau),
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
    """
    h = 0.05  # degree units; bins are 1C/2F so this is a fine, stable step

    def nll_city(b: float) -> float:
        return sum(
            -math.log(max(float(_cell_probs(cell, b, k)[cell["won_index"]]), EPS))
            for cell in cells_city)

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

    def nll_logk(lk: float) -> float:
        return _neg_loglik(np.r_[b_hat, lk], cells, city_index, n_cities)

    f0 = nll_logk(log_k_hat)
    fp = nll_logk(log_k_hat + h)
    fm = nll_logk(log_k_hat - h)
    curv = (fp - 2.0 * f0 + fm) / (h * h)
    if not math.isfinite(curv) or curv <= 1e-9:
        return None
    sd = 1.0 / math.sqrt(curv)
    return [round(float(math.exp(log_k_hat - 1.96 * sd)), 4),
            round(float(math.exp(log_k_hat + 1.96 * sd)), 4)]


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
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.fcst}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute(_FIT_QUERY)
        rows = cur.fetchall()
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
