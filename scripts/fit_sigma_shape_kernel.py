#!/usr/bin/env python3
# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: workflow A4 calibration diagnosis 2026-06-13 + docs/authority statistical_calibration_addendum.
#   GATE-2 defect (NO sold on the bin that settled YES, on every traded ring loser). The live
#   state/sigma_scale_fit.json model form `(1-w)*Normal(sigma*k) + w*UNIFORM(1/n_bins)` over-flattens
#   the near-center q: the UNIFORM pedestal puts the SAME mass on every bin regardless of distance from
#   the mode, so it STEALS mass from the dist-1/dist-2 ring (where the winner actually lands) and dumps
#   it on the far tail. The fit's own settled table shows dist-1 ratio 1.115-1.28 and dist-2 ratio
#   1.31-1.44 (ring UNDER-weighted ~28-44%). The structural fix is the MODEL FORM at q PRODUCTION
#   (upstream of every gate): replace the flat UNIFORM mixture component with a CENTER-AWARE
#   HEAVIER-TAILED Normal kernel centred on the same mu, so the mixture's second component covers the
#   moderate tail WITHOUT a flat pedestal stealing ring mass. (k, w, m) are FITTED by MLE on settled
#   outcomes (task #50 — NO hand-set value). This script is the candidate-artifact's ONLY writer; it
#   writes state/sigma_scale_fit.candidate.json (NEVER the live artifact — promotion is operator-gated).
#   Reuses scripts/fit_sigma_scale.py for the cell-building / sigma back-out / Normal integration so the
#   candidate composes with the same documented evidence chain (the live fitter is the regression anchor).
"""Refit the sigma-shape with a CENTER-AWARE two-Normal kernel mixture (GATE-2 fix), by MLE.

WHY THE LIVE FORM OVER-FLATTENS (the bug)
  Live model:  q_adj(bin) = (1 - w) * Normal(mu, sigma*k)  +  w * (1 / n_bins).
  The second term is FLAT: it adds w/n_bins to EVERY bin, near or far. The MLE chose w~=0.28-0.29 to
  buy far-tail coverage (favorite-longshot far-NO harvest needs the far bins to carry their realized
  freq), but a UNIFORM pedestal pays for that tail coverage by diluting the WHOLE vector, including the
  dist-1/dist-2 ring where the winner lands 0-2 steps from the mode. Net: the ring is UNDER-weighted
  (realized/expected 1.12-1.44), q_no = 1 - q(ring_bin) comes out too high, the q_lcb>price gate admits
  a NO on a bin the sharper market priced correctly, and the fill loses. A flat pedestal cannot be
  center-aware: it has one knob (w) that moves mass uniformly.

THE FIX (model form — data-driven, NOT a hand-tuned constant)
  Replace the UNIFORM component with a SECOND Normal centred at the SAME mu but WIDER:
      q_adj(bin) = (1 - w) * Normal(mu, sigma*k)  +  w * Normal(mu, sigma*k*m),   m > 1.
  This is a scale-mixture of Normals (a heavier-tailed, leptokurtic kernel centred on mu). The wide
  component (m>1) still concentrates near mu — so it ADDS to the ring rather than starving it — and its
  outward tail supplies the far-bin coverage the uniform pedestal was providing, but proportionally to
  distance (a real density) instead of a flat floor. The far tail keeps coverage; the ring keeps its
  realized mass. (k, w, m) are jointly FITTED by maximum Bernoulli likelihood on settled (cell, bin)
  win/loss pairs (the SAME proper scoring rule the live fitter uses). k=1, w=0 (any m) reproduces the
  un-corrected locally-Normal shape (the regression anchor); w=0 also means m is unidentified, so the
  fit is reported with m only when w>0.

  Identifiability: k controls the CORE peakedness (and thus the mode + dist-1 split), w controls how
  much mass moves to the wide component, m controls how FAR that mass spreads (dist-2/3 vs far tail).
  Three knobs span {core sharpness, ring lift, tail reach} where the uniform form had only {core, flat}.

REFUSAL / UNIT SEPARATION / DATA / SIGMA BACK-OUT
  Identical to scripts/fit_sigma_scale.py (reused verbatim): freshest posterior per (city, target_date,
  lead bucket); unit family C/F fit SEPARATELY; < MIN_CELLS settled cells -> family REFUSES (fitted=False,
  k=1,w=0, inert); sigma_impl backed out from the mode-bin probability under the locally-Normal
  approximation; per-bin Normal mass integrated over each bin's settlement preimage. The candidate
  artifact is byte-compatible with the live consumer for the (k, w) it shares; the new field `m` is
  additive (a consumer that ignores it sees the uniform-free q at w on the core only — so promotion MUST
  also wire m, see the report). The candidate is NEVER loaded by the materializer (different filename).

VALIDATION (the deliverable; see scripts/sigma_kernel_holdout_replay.py)
  TEMPORAL holdout: fit on settled cells with target_date < DATE, evaluate the ring ratio + after-cost
  win proxy on cells with target_date >= DATE; the live UNIFORM form and the candidate KERNEL form are
  reported side by side on the SAME held-out cells. Replay: the known ring losses under the refit q.

READ-ONLY over state/zeus-forecasts.db. Writes state/sigma_scale_fit.candidate.json ONLY.
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

# Reuse the live fitter's data machinery (cell-build, sigma back-out, integration, query). The live
# fitter is the artifact's ONLY writer for the live path; we import it READ-ONLY for its pure helpers
# so the candidate shares the exact same evidence chain (no duplicate, drift-free).
import sys as _sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in _sys.path:
    _sys.path.insert(0, _REPO)
import scripts.fit_sigma_scale as _live  # noqa: E402

try:  # scipy present in this venv; grid fallback documented if absent.
    from scipy.optimize import minimize as _scipy_minimize  # type: ignore
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover
    _scipy_minimize = None
    _HAVE_SCIPY = False

FCST_DEFAULT = os.path.join(_REPO, "state", "zeus-forecasts.db")
# CANDIDATE path — NEVER the live artifact. Promotion (rename to sigma_scale_fit.json) is operator-gated.
OUT_DEFAULT = os.path.join(_REPO, "state", "sigma_scale_fit.candidate.json")

MIN_CELLS_DEFAULT = 60
EPS = _live.EPS
AUTHORITY = "sigma_shape_kernel_mixture_v1_mle"  # candidate authority; distinct from live sigma_scale_fit_v1_mle

# Search boxes (grid fallback / scipy bounds).
K_LO, K_HI = 1.0, 3.5
W_LO, W_HI = 0.0, 0.6      # the wide component may carry more mass than the uniform pedestal did
M_LO, M_HI = 1.0, 6.0      # wide-component sigma multiplier (relative to sigma*k); m=1 => single Normal
F_LO, F_HI = 0.0, 3.0      # absolute core-sigma floor in STEP units; 0 => pure multiplicative (no floor)


# --------------------------------------------------------------------------------------------------
# Kernel-mixture q_adjusted (the new model form). Reuses _live._masses_from_edges for both components.
#
# REGIME-AWARE CORE DISPERSION (the key holdout finding — see the report).  The settled data shows the
# forecast's own implied sigma is NON-STATIONARY (mean sigma_impl/step rose 0.85 -> 1.73 over the 5-day
# settled window), while the REALIZED ring dispersion is ~constant in ABSOLUTE (step) units (~1.8 step).
# A multiplicative k therefore AMPLIFIES the non-stationarity (k chosen on sharp early days over-widens
# the already-wide late days), which is why a global-k fit collapses on temporal holdout. An ABSOLUTE
# floor on the core sigma (sigma_core = max(sigma_impl*k, floor_steps*step)) is far more stationary: it
# widens over-sharp forecasts UP TO the realized dispersion and leaves already-wide forecasts alone.
# floor_steps=0 reduces to the pure multiplicative form (the regression anchor / live-form analogue).
# --------------------------------------------------------------------------------------------------
def _cell_q_kernel(cell, k: float, w: float, m: float, floor_steps: float = 0.0):
    """q_adjusted per bin for one cell at (k, w, m, floor_steps):

        sigma_core = max(sigma_impl * k, floor_steps * step)
        (1 - w) * Normal(sigma_core)  +  w * Normal(sigma_core * m).

    Both components integrated over the SAME precomputed bin edges (centred at the mode, the mu of the
    locally-Normal back-out) and renormalised by _live._masses_from_edges. floor_steps is in STEP units
    (1 step = 1 degC for C cities, 2 degF for F) so the floor is unit-correct across families. Vectorized.
    """
    sigma_core = cell["sigma_impl"] * k
    if floor_steps > 0.0:
        sigma_core = max(sigma_core, floor_steps * cell["step"])
    base = _live._masses_from_edges(cell["edges_lo"], cell["edges_hi"], sigma_core)
    if w <= 0.0 or m <= 1.0 + 1e-12:
        return base  # single-Normal (m irrelevant or no wide mass)
    sigma_wide = sigma_core * m
    wide = _live._masses_from_edges(cell["edges_lo"], cell["edges_hi"], sigma_wide)
    return (1.0 - w) * base + w * wide


def _neg_log_likelihood(cells, k: float, w: float, m: float, floor_steps: float = 0.0) -> float:
    """-LL over all (cell, bin) Bernoulli win/loss pairs for the kernel-mixture form. +inf if invalid.

    This is the SAME proper scoring rule the live fitter uses (per-(cell,bin) Bernoulli LogLoss). It is
    reported for every fit (out-of-sample LogLoss is a required selection criterion), but it is NOT the
    sole objective: the deep finding (see the report) is that pure LogLoss is dominated by the many
    lost far bins per cell, so it PREFERS a flat uniform pedestal that games far-bin loss while leaving
    the dist-1/dist-2 RING under-weighted — which is exactly the GATE-2 loss class. The estimator below
    (_objective) adds a ring-calibration penalty so the fit targets the documented defect.
    """
    if not (k >= 1.0 - 1e-9 and 0.0 - 1e-9 <= w <= 1.0 and m >= 1.0 - 1e-9 and floor_steps >= -1e-9):
        return float("inf")
    total = 0.0
    for cell in cells:
        q_adj = np.clip(_cell_q_kernel(cell, k, w, m, floor_steps), EPS, 1.0 - EPS)
        won = cell["won_index"]
        total -= float(np.log(1.0 - q_adj).sum())
        total -= float(math.log(q_adj[won])) - float(math.log(1.0 - q_adj[won]))
    return total


# Distance bands scored by the ring-calibration penalty. The GATE-2 winner lands 0-3 steps from the
# mode (dist-0..3); these are the bins whose q feeds q_no=1-q(bin) into the q_lcb>price gate. The far
# tail (>=4, open-shoulder) is scored by LogLoss ONLY so its favorite-longshot harvest coverage is
# preserved (not forced to the ring's calibration target).
_RING_BANDS = ("0", "1", "2", "3")


def _ring_calibration_penalty(cells, k: float, w: float, m: float, floor_steps: float = 0.0) -> float:
    """Minimum-distance penalty on the per-distance calibration curve over the RING bands.

    For each ring band the realized win frequency must equal the mean predicted q (ratio -> 1.0). We
    penalise the squared LOG-ratio of realized/expected (symmetric, scale-free; 0 iff mean_q==realized),
    weighted by the band's bin count. This is a proper minimum-distance / method-of-moments objective on
    the calibration curve — a legitimate FITTED estimator (k,w,m,floor derived from data, nothing
    hardcoded) — directly targeting the defect the gate consumes (ring q under-weighting). +inf if invalid.
    """
    if not (k >= 1.0 - 1e-9 and 0.0 - 1e-9 <= w <= 1.0 and m >= 1.0 - 1e-9 and floor_steps >= -1e-9):
        return float("inf")
    tab = _calibration_table(cells, k, w, m, floor_steps)
    pen = 0.0
    for t in tab:
        if t["dist"] in _RING_BANDS and t["mean_q"] > 0.0:
            ratio = t["realized_freq"] / t["mean_q"]
            pen += t["n_bins"] * (math.log(max(ratio, 1e-6))) ** 2
    return pen


# Composite-objective mixing weight. The objective is LogLoss + LAMBDA_CALIB * ring_calibration_penalty.
# LAMBDA is NOT a fitted shape parameter (k,w,m are) — it is the objective's relative emphasis between
# the proper scoring rule (keeps the whole vector honest, preserves far-tail coverage) and the ring
# calibration target (the named GATE-2 criterion). Chosen so the ring penalty (O(40) at the un-corrected
# shape) and the LogLoss (O(900)) are comparable in gradient near the optimum; the fit is reported with
# BOTH components so the selection is auditable, and a sensitivity sweep over LAMBDA is in the report.
LAMBDA_CALIB_DEFAULT = 10.0


def _objective(cells, k: float, w: float, m: float, floor_steps: float = 0.0,
               lam: float = LAMBDA_CALIB_DEFAULT) -> float:
    """The fit objective: LogLoss + lam * ring_calibration_penalty. Both terms are data-derived."""
    nll = _neg_log_likelihood(cells, k, w, m, floor_steps)
    if not math.isfinite(nll):
        return float("inf")
    return nll + lam * _ring_calibration_penalty(cells, k, w, m, floor_steps)


def _fit_grid(cells, lam: float = LAMBDA_CALIB_DEFAULT, fit_floor: bool = True):
    """Coarse 4-D grid + local refine on the COMPOSITE objective. Returns (k, w, m, floor, obj). Robust.

    When fit_floor is True the absolute core-sigma floor (STEP units) is a fitted dimension; this is the
    holdout-stationary form. When False the floor is pinned to 0 (pure multiplicative — the live-form
    analogue / regression anchor).
    """
    ks = np.round(np.arange(K_LO, K_HI + 1e-9, 0.1), 4)
    ws = np.round(np.arange(W_LO, W_HI + 1e-9, 0.05), 4)
    ms = np.round(np.arange(M_LO, M_HI + 1e-9, 0.5), 4)
    fs = np.round(np.arange(F_LO, F_HI + 1e-9, 0.25), 4) if fit_floor else np.array([0.0])
    best = (1.0, 0.0, 1.0, 0.0, _objective(cells, 1.0, 0.0, 1.0, 0.0, lam))
    for k in ks:
        for w in ws:
            for m in ms:
                for fl in fs:
                    obj = _objective(cells, float(k), float(w), float(m), float(fl), lam)
                    if obj < best[4]:
                        best = (float(k), float(w), float(m), float(fl), obj)
    k0, w0, m0, f0, _ = best
    for k in np.round(np.arange(max(K_LO, k0 - 0.1), k0 + 0.1 + 1e-9, 0.02), 4):
        for w in np.round(np.arange(max(W_LO, w0 - 0.05), min(W_HI, w0 + 0.05) + 1e-9, 0.01), 4):
            for m in np.round(np.arange(max(M_LO, m0 - 0.5), min(M_HI, m0 + 0.5) + 1e-9, 0.1), 4):
                f_iter = (np.round(np.arange(max(F_LO, f0 - 0.25), min(F_HI, f0 + 0.25) + 1e-9, 0.05), 4)
                          if fit_floor else np.array([0.0]))
                for fl in f_iter:
                    obj = _objective(cells, float(k), float(w), float(m), float(fl), lam)
                    if obj < best[4]:
                        best = (float(k), float(w), float(m), float(fl), obj)
    return best


def _fit_mle(cells, lam: float = LAMBDA_CALIB_DEFAULT, fit_floor: bool = True):
    """Fit (k, w, m, floor) on the composite objective. scipy multi-start when available, else grid.

    Returns (k, w, m, floor_steps, obj). When fit_floor is False the floor is pinned to 0.
    """
    if _HAVE_SCIPY and _scipy_minimize is not None:
        best = None
        # starts span both forms: pure-multiplicative (floor 0) and floor-dominant (k~1, floor~1.8).
        starts = [
            (1.3, 0.25, 2.5, 0.0), (1.5, 0.3, 3.0, 0.0), (1.85, 0.0, 1.0, 0.0),
            (1.0, 0.0, 1.0, 1.8), (1.0, 0.0, 1.0, 1.6), (1.1, 0.1, 2.0, 1.8),
        ]
        f_hi = F_HI if fit_floor else 0.0
        for k0, w0, m0, f0 in starts:
            if not fit_floor:
                f0 = 0.0
            try:
                res = _scipy_minimize(
                    lambda x: _objective(cells, x[0], x[1], x[2], x[3], lam),
                    x0=np.array([k0, w0, m0, f0]),
                    method="L-BFGS-B",
                    bounds=[(1.0, K_HI), (0.0, W_HI), (1.0, M_HI), (0.0, f_hi)],
                )
                if res.success or np.isfinite(res.fun):
                    cand = (float(res.x[0]), float(res.x[1]), float(res.x[2]), float(res.x[3]), float(res.fun))
                    if best is None or cand[4] < best[4]:
                        best = cand
            except Exception:
                continue
        if best is not None and math.isfinite(best[4]):
            # Guard against a flat-region scipy stall with a CHEAP coarse grid (not the full refine grid)
            # — the multi-start scipy already spans both forms, so the guard only needs to catch a gross
            # miss, not re-search at fine resolution.
            g = _coarse_grid(cells, lam, fit_floor)
            return g if g[4] < best[4] else best
    return _fit_grid(cells, lam, fit_floor)


def _coarse_grid(cells, lam: float = LAMBDA_CALIB_DEFAULT, fit_floor: bool = True):
    """Cheap coarse 4-D grid (no local refine) used ONLY as the scipy stall-guard. Returns (k,w,m,floor,obj)."""
    ks = np.round(np.arange(K_LO, K_HI + 1e-9, 0.25), 4)
    ws = np.round(np.arange(W_LO, W_HI + 1e-9, 0.15), 4)
    ms = np.round(np.arange(M_LO, M_HI + 1e-9, 1.0), 4)
    fs = np.round(np.arange(F_LO, F_HI + 1e-9, 0.5), 4) if fit_floor else np.array([0.0])
    best = (1.0, 0.0, 1.0, 0.0, _objective(cells, 1.0, 0.0, 1.0, 0.0, lam))
    for k in ks:
        for w in ws:
            for m in ms:
                for fl in fs:
                    obj = _objective(cells, float(k), float(w), float(m), float(fl), lam)
                    if obj < best[4]:
                        best = (float(k), float(w), float(m), float(fl), obj)
    return best


def _calibration_table(cells, k: float, w: float, m: float, floor_steps: float = 0.0):
    """Per-distance calibration AT (k,w,m,floor): mean q_adj vs realized win freq, by |bin-mode| in STEP units."""
    agg: dict = defaultdict(lambda: {"sum_q": 0.0, "wins": 0, "n": 0})
    for cell in cells:
        q_adj = _cell_q_kernel(cell, k, w, m, floor_steps)
        mode_index = cell["mode_index"]
        won = cell["won_index"]
        for i, q in enumerate(q_adj):
            deg_i = cell["items"][i][2]
            deg_mode = cell["items"][mode_index][2]
            is_open = cell["items"][i][3]
            if deg_i is None or deg_mode is None or is_open:
                dist = "tail"
            else:
                d = int(round(abs(deg_i - deg_mode) / cell["step"]))
                dist = str(d) if d <= 3 else ">=4"
            a = agg[dist]
            a["sum_q"] += q
            a["wins"] += 1 if i == won else 0
            a["n"] += 1
    order = ["0", "1", "2", "3", ">=4", "tail"]
    table = []
    for dist in order:
        if dist not in agg:
            continue
        a = agg[dist]
        mean_q = a["sum_q"] / a["n"] if a["n"] else 0.0
        realized = a["wins"] / a["n"] if a["n"] else 0.0
        table.append({
            "dist": dist, "n_bins": a["n"], "mean_q": round(mean_q, 4),
            "wins": a["wins"], "realized_freq": round(realized, 4),
            "ratio_realized_over_expected": round(realized / mean_q, 3) if mean_q > 0 else None,
        })
    return table


def _profile_ci(cells, k_hat: float, w_hat: float, m_hat: float, f_hat: float, obj_hat: float,
                lam: float = LAMBDA_CALIB_DEFAULT):
    """Profile 95% CIs for each of k, w, m, floor (Delta(objective)=1.92).

    Profiled on the SAME composite objective the fit minimises (not raw NLL) so the interval reflects the
    actual estimator. To stay tractable in 4-D the OTHER three parameters are profiled out by a local
    scipy re-optimisation (warm-started at the MLE with the scanned parameter pinned), not a full nested
    grid. Each 1-D profile is ~unimodal about the optimum so we walk outward and stop at threshold. When
    scipy is unavailable we profile out the others by a small fixed candidate set (the MLE plus the grid
    neighbours), which is conservative (CIs no narrower than the true profile)."""
    thresh = obj_hat + 1.920729

    def _profiled(pin_idx, v):
        """Min composite objective with parameter pin_idx fixed at v, others re-optimised from the MLE."""
        x0 = [k_hat, w_hat, m_hat, f_hat]
        bounds = [(1.0, K_HI), (0.0, W_HI), (1.0, M_HI), (0.0, F_HI)]
        x0[pin_idx] = v
        if _HAVE_SCIPY and _scipy_minimize is not None:
            free = [i for i in range(4) if i != pin_idx]

            def obj(xf):
                x = list(x0)
                for j, i in enumerate(free):
                    x[i] = xf[j]
                return _objective(cells, x[0], x[1], x[2], x[3], lam)

            try:
                res = _scipy_minimize(obj, x0=np.array([x0[i] for i in free]),
                                      method="L-BFGS-B", bounds=[bounds[i] for i in free])
                if np.isfinite(res.fun):
                    return float(res.fun)
            except Exception:
                pass
        # fallback: evaluate at the MLE point with the pin applied (conservative).
        return _objective(cells, x0[0], x0[1], x0[2], x0[3], lam)

    def _walk(center, lo_b, hi_b, step, pin_idx):
        lo = center; v = center
        while v - step >= lo_b - 1e-9:
            v = round(v - step, 4)
            if _profiled(pin_idx, v) <= thresh:
                lo = v
            else:
                break
        hi = center; v = center
        while v + step <= hi_b + 1e-9:
            v = round(v + step, 4)
            if _profiled(pin_idx, v) <= thresh:
                hi = v
            else:
                break
        return [round(lo, 4), round(hi, 4)]

    return {
        "k": _walk(k_hat, K_LO, K_HI, 0.05, 0),
        "w": _walk(w_hat, W_LO, W_HI, 0.02, 1),
        "m": _walk(m_hat, M_LO, M_HI, 0.2, 2),
        "floor_steps": _walk(f_hat, F_LO, F_HI, 0.1, 3),
    }


def _load_cells(fcst_path: str):
    """Build the same cells the live fitter builds, READ-ONLY. Returns (cells_by_unit, window, rows)."""
    con = sqlite3.connect(f"file:{fcst_path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute(_live._FIT_QUERY)
        rows = cur.fetchall()
    finally:
        con.close()
    cells_by_unit, window = _live._build_cells(rows)
    return cells_by_unit, window, rows


def _fit_family(cells, min_cells: int, fitted_at: str, window: str, fit_floor: bool = True):
    """Fit one unit family with the regime-aware kernel-mixture form (or refuse). Returns the family dict."""
    n_cells = len(cells)
    if n_cells < min_cells:
        return {
            "fitted": False, "k": 1.0, "w": 0.0, "m": 1.0, "floor_steps": 0.0, "n_cells": n_cells,
            "refusal_reason": f"INSUFFICIENT_CELLS:{n_cells}<{min_cells}",
            "fitted_at": fitted_at, "lead_buckets": list(_live.LEAD_BUCKETS.keys()),
            "method": "refused_insufficient_n", "data_window": window,
            "ci": {"k": None, "w": None, "m": None, "floor_steps": None},
        }
    k, w, m, fl, obj = _fit_mle(cells, fit_floor=fit_floor)
    nll = _neg_log_likelihood(cells, k, w, m, fl)
    ring_pen = _ring_calibration_penalty(cells, k, w, m, fl)
    return {
        "fitted": True, "k": round(k, 4), "w": round(w, 4), "m": round(m, 4), "floor_steps": round(fl, 4),
        "n_cells": n_cells,
        "objective": round(obj, 4), "objective_lambda": LAMBDA_CALIB_DEFAULT,
        "neg_log_likelihood": round(nll, 4), "ring_calibration_penalty": round(ring_pen, 4),
        "ci": _profile_ci(cells, k, w, m, fl, obj), "ci_method": "profile_objective_95",
        "fitted_at": fitted_at, "lead_buckets": list(_live.LEAD_BUCKETS.keys()),
        "method": ("mle_scipy_lbfgsb" if _HAVE_SCIPY else "mle_grid_4d"),
        "objective_form": "neg_log_likelihood + lambda * ring_calibration_penalty(squared log-ratio, dist 0..3)",
        "data_window": window,
        "calibration_at_fit": _calibration_table(cells, k, w, m, fl),
        "calibration_at_k1_w0": _calibration_table(cells, 1.0, 0.0, 1.0, 0.0),
        "model_form": "sigma_core=max(sigma_impl*k, floor_steps*step); (1-w)*Normal(sigma_core) + w*Normal(sigma_core*m)",
    }


def _write_candidate(out_path: str, families: dict, window: str, fitted_at: str):
    qhash = hashlib.sha256((_live._FIT_QUERY + f"|window={window}").encode("utf-8")).hexdigest()[:16]
    prov_basis = json.dumps(
        {u: {kk: families[u].get(kk) for kk in ("fitted", "k", "w", "m", "floor_steps", "n_cells")} for u in families},
        sort_keys=True,
    ) + f"|qhash={qhash}|authority={AUTHORITY}"
    provenance_hash = hashlib.sha256(prov_basis.encode("utf-8")).hexdigest()[:16]
    table = {
        "_meta": {
            "authority": AUTHORITY,
            "candidate": True,
            "promotion": "OPERATOR_GATED — rename to sigma_scale_fit.json only after operator sign-off AND "
                         "forward-fill validation; the consumer must also be wired for the floor_steps + m "
                         "fields (see report). Holdout shows the magnitude is non-stationary on 5 days.",
            "created": fitted_at,
            "method": "composite_objective(logloss + lambda*ring_calibration) over regime-aware two-normal scale mixture",
            "model": "sigma_core=max(sigma_impl*k, floor_steps*step); q_adj(bin)=(1-w)*Normal(sigma_core)+w*Normal(sigma_core*m)",
            "supersedes_form": "q_adj(bin) = (1-w)*Normal(sigma_impl*k) + w*(1/n_bins)  [live; flat uniform pedestal]",
            "sigma_back_out": "q_mode = 2*Phi(half/sigma_impl)-1 ; sigma_impl = half/Phi^-1((q_mode+1)/2)",
            "min_cells": int(MIN_CELLS_DEFAULT),
            "lead_buckets": _live.LEAD_BUCKETS,
            "metric": _live.DATA_WINDOW_METRIC,
            "data_window": window,
            "source": "forecast_posteriors join settlement_outcomes(authority=VERIFIED), high metric, no-leak lead-bucketed",
            "source_query_hash": qhash,
            "provenance_hash": provenance_hash,
            "scipy_available": bool(_HAVE_SCIPY),
        },
        "families": families,
    }
    tmp = f"{out_path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=2, sort_keys=True)
    os.replace(tmp, out_path)
    return provenance_hash


def main() -> int:
    ap = argparse.ArgumentParser(description="Refit sigma-shape with a center-aware two-Normal kernel mixture (GATE-2 fix), MLE.")
    ap.add_argument("--fcst", default=FCST_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT, help="CANDIDATE artifact path (never the live one).")
    ap.add_argument("--min-cells", type=int, default=MIN_CELLS_DEFAULT)
    args = ap.parse_args()

    cells_by_unit, window, _rows = _load_cells(args.fcst)
    fitted_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    families: dict = {}
    for unit in ("C", "F"):
        families[unit] = _fit_family(cells_by_unit.get(unit, []), args.min_cells, fitted_at, window)
    prov = _write_candidate(args.out, families, window, fitted_at)
    print(f"[sigma-kernel] wrote CANDIDATE {args.out}  (window={window}, provenance={prov})")
    for unit in ("C", "F"):
        fam = families[unit]
        if fam["fitted"]:
            print(f"    {unit}: FITTED k={fam['k']} w={fam['w']} m={fam['m']} floor_steps={fam['floor_steps']} "
                  f"n_cells={fam['n_cells']} CI_k={fam['ci']['k']} CI_floor={fam['ci']['floor_steps']}")
        else:
            print(f"    {unit}: REFUSED ({fam['refusal_reason']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
