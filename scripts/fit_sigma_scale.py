#!/usr/bin/env python3
# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: OPERATOR LAW 2026-06-12 "没有一个人可以在没有数学支持下决定一个 hard coded value" —
#   the σ-scale correction k (and the uniform-mixture weight w) must be FITTED by maximum likelihood,
#   never operator-picked or hardcoded. Supersedes the settings key `replacement_sigma_scale_k_c`.
#   Data + model basis: docs/operations/c3_sigma_calibration_surface_2026-06-12.md (the replacement
#   posterior is ~2.5x too peaked for C cities; a scaled Normal alone cannot fit all distances because
#   realized frequency is nearly FLAT across d=0,1,2 — so we fit a Normal(σ·k) ⊕ Uniform(w) mixture).
#   Precedent pattern: scripts/fit_settlement_sigma_floor.py (#20) — fit script is the artifact's ONLY
#   writer; the materializer READS the artifact (authority string sigma_scale_fit_v1_mle below).
"""Fit the σ-scale k AND uniform-mixture weight w by MAXIMUM LIKELIHOOD on settled-cell outcomes.

WHY (operator law): the replacement fused-Normal posterior is too peaked — for C cities the mode bin
is assigned mean q≈0.43-0.46 but wins only ~0.17 of the time (posterior ~2.5x too peaked). The single
operator-picked number `replacement_sigma_scale_k_c` violated the law "no hand-set value without math
support". This estimator replaces it with a fitted artifact: (k, w) chosen to MAXIMISE the Bernoulli
log-likelihood of every (settled cell, bin) win/loss pair, with profile-likelihood CIs and a hard
REFUSAL when a unit family has < MIN_CELLS settled cells (so F cities — n≈47 < 60 — refuse today).

MODEL
  For each settled cell we reconstruct the locally-Normal σ implied by the materialized posterior,
  then re-integrate a WIDENED Normal mixed with a uniform floor:

    q_adjusted(bin) = (1 - w) · q_normal_rescaled(bin; k) + w · (1 / n_bins)

  where q_normal_rescaled re-integrates the per-cell locally-Normal approximation with σ·k.

  σ back-out (SAME approximation the calibration surface used):
    The materialized mode-bin probability is, for an interior bin of half-width half=step/2 centred
    at the mode,
      q_mode ≈ Φ(+half/σ_impl) − Φ(−half/σ_impl)                     (units: native label degree)
    ⇒ σ_impl = half / Φ⁻¹((q_mode + 1) / 2).
    This treats the posterior as locally Normal in temperature units. It is an APPROXIMATION
    (multimodal/skewed posteriors get an imperfect σ_impl) but it is the SAME one the surface fit used,
    so k composes with the documented evidence.

  q_normal_rescaled(bin; k): with μ at the mode-bin centre and σ = σ_impl·k, integrate the Normal over
  each bin's settlement preimage (interior bin → [c−mode−half, c−mode+half); the two open shoulders
  integrate the outward tail), then renormalise over the family so Σ q_normal_rescaled = 1 before
  mixing. k=1, w=0 reproduces the materialized locally-Normal shape (the regression anchor).

  LIKELIHOOD (the fit objective):
    LL(k, w) = Σ_cells Σ_bins [ won·log(q_adj) + (1−won)·log(1−q_adj) ]
  where won ∈ {0,1} is whether the bin is the settled winning bin. Bernoulli per (cell, bin) pair —
  this is the proper scoring rule for "did THIS bin win", and is what makes (k, w) identifiable
  separately (k controls peakedness, w lifts the flat tails). q_adj is clipped to [EPS, 1−EPS].

FIT
  scipy.optimize (L-BFGS-B, multi-start) when available; else a fine 2-D grid (k∈[1.0,3.5] step .05,
  w∈[0,0.30] step .01) + local refine. CIs: profile-likelihood 95% (Δ(−logL)=1.92, χ²₁ 0.95) by
  default — walking outward from the MLE — OR nonparametric bootstrap over CELLS (--bootstrap N).

REFUSAL
  If a unit family has < MIN_CELLS settled cells, the family entry is written with fitted=false,
  k=1.0, w=0.0 and a refusal reason — the materializer stays INERT for that family. F cities refuse
  today (n≈47 < 60), which is exactly right (the surface flagged F n=25 insufficient).

DATA  (mirrors the calibration surface exactly)
  forecast_posteriors ⋈ settlement_outcomes(authority=VERIFIED), temperature_metric='high', mode=ro.
  Lead buckets: A_24h = computed_at 12–36h before target_date 00:00 UTC; B_48h = 36–60h. The FRESHEST
  posterior within each (city, target_date, bucket) is used. Unit family from settlement_outcomes
  .settlement_unit ('C' / 'F'). Winning-bin match: substring, else centre±half-step, else tail
  direction (the surface's 3-pass match). Tail (open-shoulder) bins are KEPT for the likelihood.

UNIT NOTE
  C-unit cities' q_json labels are °C with 1°C interior bins. F-unit cities' labels are °F with 2°F
  interior bins ("between 68-69°F"). The grid STEP (1°C vs 2°F) is inferred per-cell from the median
  spacing of interior centres, so the σ back-out + Normal re-integration are unit-correct for both. The
  fitted k is a dimensionless multiplier on σ regardless of unit. C and F are fit SEPARATELY so each
  family's k/w reflect its own settled cells (and F can refuse independently).

READ-ONLY over state/zeus-forecasts.db. Writes state/sigma_scale_fit.json via the sanctioned
atomic-replace path. This script is the artifact's ONLY writer. Run weekly (recommend) so k tracks
data growth; the artifact's data_window makes every refit auditable.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import re
import sqlite3
from collections import defaultdict

import numpy as np

try:  # scipy is present in this venv; the grid path is the documented fallback if it is ever absent.
    from scipy.optimize import minimize as _scipy_minimize  # type: ignore
    from scipy.stats import norm as _scipy_norm  # type: ignore
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover - fallback path
    _scipy_minimize = None
    _scipy_norm = None
    _HAVE_SCIPY = False

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FCST_DEFAULT = os.path.join(REPO, "state", "zeus-forecasts.db")
OUT_DEFAULT = os.path.join(REPO, "state", "sigma_scale_fit.json")

# --- thresholds / contract constants -------------------------------------------------------------
MIN_CELLS_DEFAULT = 60          # < this many settled cells in a unit family → REFUSE (family inert)
K_LO, K_HI, K_STEP = 1.0, 3.5, 0.05   # grid-fallback search box for k
W_LO, W_HI, W_STEP = 0.0, 0.30, 0.01  # grid-fallback search box for w
EPS = 1e-9                      # q_adj clip so log() is finite
SIGMA_IMPL_MIN = 0.15           # clamp back-out σ (in STEP units); surface min observed ≈0.150
SIGMA_IMPL_MAX = 6.0            # clamp to the surface's observed span (in STEP units)
AUTHORITY = "sigma_scale_fit_v1_mle"
LEAD_BUCKETS = {"A_24h": (12.0, 36.0), "B_48h": (36.0, 60.0)}
DATA_WINDOW_METRIC = "high"

# Canonical join SQL (hashed into provenance for lineage; mirrors the surface + floor scripts).
_FIT_QUERY = (
    "SELECT fp.city, fp.target_date, fp.source_cycle_time, fp.computed_at, fp.q_json, "
    "       so.winning_bin, so.settlement_value, so.settlement_unit "
    "FROM forecast_posteriors fp "
    "JOIN settlement_outcomes so "
    "  ON so.city=fp.city AND so.target_date=fp.target_date "
    " AND so.temperature_metric=fp.temperature_metric "
    "WHERE fp.temperature_metric='high' "
    "  AND so.authority='VERIFIED' AND so.winning_bin IS NOT NULL"
)

# Parse degree tokens out of a bin question. Handles BOTH unit families:
#   C-unit (1°C interior bins): "...be 19°C on June 9?"            -> centre 19, step 1
#   F-unit (2°F interior bins): "...be between 68-69°F on June 8?" -> centre 68.5, step 2
#                               "...be 80°F on ...?"               -> centre 80
_RANGE_RE = re.compile(r"(-?\d+)\s*-\s*(-?\d+)\s*°?([CF])", re.IGNORECASE)
_SINGLE_RE = re.compile(r"(-?\d+)\s*°?([CF])", re.IGNORECASE)


def _phi(x: float) -> float:
    """Standard-normal CDF (scipy if present, else math.erf)."""
    if _scipy_norm is not None:
        return float(_scipy_norm.cdf(x))
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


try:  # vectorized standard-normal CDF for the hot integration path (C-level; ~100x scalar norm.cdf)
    from scipy.special import ndtr as _ndtr  # type: ignore
    def _phi_vec(x):  # numpy array in/out
        return _ndtr(x)
except Exception:  # pragma: no cover - numpy-only fallback
    def _phi_vec(x):
        from numpy import vectorize as _vec  # local import; only hit if scipy.special is absent
        return _vec(lambda v: 0.5 * (1.0 + math.erf(v / math.sqrt(2.0))))(x)


def _phi_inv(p: float) -> float:
    """Standard-normal inverse CDF (scipy if present, else a rational approximation)."""
    p = min(max(p, 1e-12), 1.0 - 1e-12)
    if _scipy_norm is not None:
        return float(_scipy_norm.ppf(p))
    # Acklam's rational approximation (sufficient for the σ back-out).
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _lead_hours(target_date: str, source_cycle_time: str) -> float | None:
    """Hours from source_cycle_time to target_date 00:00 UTC. None on parse failure."""
    try:
        td = _dt.datetime.fromisoformat(str(target_date)[:10]).replace(tzinfo=_dt.timezone.utc)
        sct = _dt.datetime.fromisoformat(str(source_cycle_time).replace("Z", "+00:00"))
        if sct.tzinfo is None:
            sct = sct.replace(tzinfo=_dt.timezone.utc)
        return (td - sct).total_seconds() / 3600.0
    except Exception:
        return None


def _bucket_for_lead(lead_h: float | None) -> str | None:
    if lead_h is None:
        return None
    for name, (lo, hi) in LEAD_BUCKETS.items():
        if lo <= lead_h < hi:
            return name
    return None


def _bin_center_deg(label: str):
    """Representative degree (bin CENTRE) parsed from a label, unit-agnostic.

    Range "A-B" -> midpoint (A+B)/2; single "X" -> X. Returns None if no degree token is present.
    """
    mr = _RANGE_RE.search(label)
    if mr:
        a, b = float(mr.group(1)), float(mr.group(2))
        return (a + b) / 2.0
    ms = _SINGLE_RE.search(label)
    if ms:
        return float(ms.group(1))
    return None


def _parse_cell(q_json_text: str):
    """Parse a posterior's q_json into an ORDERED grid + open-shoulder flags + grid step.

    Returns (items, mode_index, step) where items = [(label, q, center_deg_or_None, is_open)] ordered
    by centre with the two open shoulders at the ends; step = the interior grid spacing in the label's
    native degree unit (1 for C-unit 1°C bins, 2 for F-unit 2°F bins), inferred from the median spacing
    of consecutive interior centres. None if unparseable.
    """
    try:
        q = json.loads(q_json_text)
    except Exception:
        return None
    if not isinstance(q, dict) or not q:
        return None
    items = []
    for label, prob in q.items():
        try:
            p = float(prob)
        except Exception:
            p = 0.0
        deg = _bin_center_deg(label)
        is_open = ("or below" in label.lower()) or ("or higher" in label.lower())
        items.append([label, p, deg, is_open])

    def _sort_key(it):
        label, _p, deg, _open = it
        lower = label.lower()
        if deg is not None:
            return deg
        return 1e9 if "or higher" in lower else -1e9
    items.sort(key=_sort_key)

    interiors = [it[2] for it in items if it[2] is not None and not it[3]]
    if len(interiors) < 2:
        return None
    diffs = np.diff(sorted(interiors))
    diffs = diffs[diffs > 0]
    step = float(np.median(diffs)) if diffs.size else 1.0
    if not (step > 0 and math.isfinite(step)):
        step = 1.0
    mode_index = max(range(len(items)), key=lambda i: items[i][1])
    return items, mode_index, step


def _winning_index(items, winning_bin: str, settlement_value, step: float = 1.0):
    """3-pass winning-bin match (surface method), step-aware. Returns the matched bin index or None."""
    wb = (winning_bin or "").strip()
    # Pass 1: substring (winning_bin token appears in the label, e.g. "80-81°F" in "between 80-81°F").
    for i, (label, _p, deg, _open) in enumerate(items):
        if wb and wb in label:
            return i
    # Pass 2: bin-centre match within half a step (range "80-81" -> centre 80.5).
    wb_deg = _bin_center_deg(wb)
    if wb_deg is not None:
        tol = step / 2.0 + 1e-6
        best = None
        for i, (label, _p, deg, _open) in enumerate(items):
            if deg is not None and abs(deg - wb_deg) <= tol:
                if best is None or abs(deg - wb_deg) < best[1]:
                    best = (i, abs(deg - wb_deg))
        if best is not None:
            return best[0]
    # Pass 3: tail direction via settlement_value vs interior grid span.
    try:
        sv = float(settlement_value)
    except Exception:
        return None
    interiors = [(i, it[2]) for i, it in enumerate(items) if it[2] is not None]
    if not interiors:
        return None
    lo_i, lo_deg = min(interiors, key=lambda t: t[1])
    hi_i, hi_deg = max(interiors, key=lambda t: t[1])
    if sv <= lo_deg:
        return lo_i if not items[lo_i][3] else 0
    if sv >= hi_deg:
        return hi_i if not items[hi_i][3] else len(items) - 1
    return min(interiors, key=lambda t: abs(t[1] - sv))[0]


def _sigma_implied(q_mode: float, half_step: float = 0.5) -> float | None:
    """Back out σ (in the label's native degree unit) from the mode-bin probability.

    For an interior bin of half-width ``half_step`` (= step/2; 0.5°C for C, 1.0°F for F) centred at the
    mode under a locally-Normal posterior:
      q_mode = 2Φ(half/σ) − 1  ⇒  σ = half / Φ⁻¹((q_mode+1)/2).
    Clamped to [SIGMA_IMPL_MIN·step, SIGMA_IMPL_MAX·step]. None for a degenerate (q_mode≈1 or ≤0) mode.
    """
    if not (0.0 < q_mode < 1.0):
        return None
    z = _phi_inv((q_mode + 1.0) / 2.0)
    if z <= 0 or not math.isfinite(z):
        return None
    sigma = half_step / z
    step = half_step * 2.0
    return float(min(max(sigma, SIGMA_IMPL_MIN * step), SIGMA_IMPL_MAX * step))


_NEG_INF = -1e18  # finite sentinels for ±∞ integration bounds (x/σ → ∓40 ⇒ Φ=0/1 to machine eps)
_POS_INF = 1e18


def _cell_edges(items, mode_index: int, step: float = 1.0):
    """Precompute per-bin integration edges (DEGREE units, offset from the mode centre) for a cell.

    Returns (lo, hi) numpy arrays where Normal mass = Φ(hi/σ) − Φ(lo/σ) with σ in the SAME degree unit.
    half = step/2 is the bin half-width. Done ONCE per cell so the hot path only does vectorized Φ.
      - interior bin centred at c:        [c − mode − half, c − mode + half)
      - open-low  ("or below") shoulder:  (−∞, c − mode + half)
      - open-high ("or higher") shoulder: [c − mode − half, +∞)
    """
    mode_deg = items[mode_index][2]
    half = step / 2.0
    los, his = [], []
    for label, _p, deg, is_open in items:
        lower = label.lower()
        if is_open and "or below" in lower and deg is not None:
            los.append(_NEG_INF); his.append((deg - mode_deg) + half)
        elif is_open and "or higher" in lower and deg is not None:
            los.append((deg - mode_deg) - half); his.append(_POS_INF)
        elif deg is not None:
            j = deg - mode_deg
            los.append(j - half); his.append(j + half)
        else:
            los.append(0.0); his.append(0.0)
    return np.asarray(los, dtype=float), np.asarray(his, dtype=float)


def _masses_from_edges(lo, hi, sigma: float):
    """Vectorized Normal mass per bin from precomputed (lo, hi) edge arrays. Renormalised to sum 1."""
    masses = _phi_vec(hi / sigma) - _phi_vec(lo / sigma)
    total = float(masses.sum())
    if not (total > 0 and math.isfinite(total)):
        return np.full(lo.shape, 1.0 / lo.shape[0])
    return masses / total


def _normal_rescaled_masses(items, mode_index: int, sigma: float, step: float = 1.0) -> list[float]:
    """Re-integrate a Normal centred at the mode-bin centre with σ over each bin's preimage.

    Renormalised masses summing to 1 over the family. Scalar-friendly wrapper around the vectorized
    edge integration (used by the per-distance calibration table + the test surface).
    """
    lo, hi = _cell_edges(items, mode_index, step)
    return list(_masses_from_edges(lo, hi, sigma))


def _build_cells(rows):
    """Return ({unit: [cell_dict, ...]}, window). Each cell precomputes edges for the hot fit path."""
    # Freshest posterior per (city, target_date, bucket).
    best: dict = {}
    for city, tdate, sct, comp, q_json_text, winning_bin, sval, sunit in rows:
        bucket = _bucket_for_lead(_lead_hours(tdate, sct))
        if bucket is None:
            continue
        key = (city, tdate, bucket)
        prev = best.get(key)
        if prev is None or str(comp) > str(prev[3]):
            best[key] = (city, tdate, sct, comp, q_json_text, winning_bin, sval, sunit, bucket)

    cells_by_unit: dict = defaultdict(list)
    td_min: str | None = None
    td_max: str | None = None
    for (city, tdate, sct, comp, q_json_text, winning_bin, sval, sunit, bucket) in best.values():
        unit = (sunit or "C").upper()
        if unit not in ("C", "F"):
            unit = "C"
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
        td_min = tdate if td_min is None else min(td_min, tdate)
        td_max = tdate if td_max is None else max(td_max, tdate)
        cells_by_unit[unit].append({
            "city": city, "target_date": tdate, "bucket": bucket,
            "n_bins": len(items), "sigma_impl": sigma_impl, "mode_index": mode_index,
            "items": items, "won_index": won_index, "step": step,
            "edges_lo": lo, "edges_hi": hi,
        })
    window = f"settled-{td_min}..{td_max}" if td_min and td_max else "settled-empty"
    return cells_by_unit, window


# --- likelihood ----------------------------------------------------------------------------------

def _cell_q_adjusted(cell, k: float, w: float):
    """q_adjusted per bin for one cell at (k, w): (1-w)·Normal(σ·k) + w·uniform. Vectorized."""
    sigma = cell["sigma_impl"] * k
    base = _masses_from_edges(cell["edges_lo"], cell["edges_hi"], sigma)
    u = 1.0 / cell["n_bins"]
    return (1.0 - w) * base + w * u


def _neg_log_likelihood(cells, k: float, w: float) -> float:
    """−LL over all (cell, bin) Bernoulli win/loss pairs. +inf for invalid (k,w).

    Per bin: won → −log(q_adj); lost → −log(1−q_adj). Vectorized per cell over the bin array.
    """
    if not (k >= 1.0 - 1e-9 and 0.0 - 1e-9 <= w <= 1.0):
        return float("inf")
    total = 0.0
    for cell in cells:
        q_adj = np.clip(_cell_q_adjusted(cell, k, w), EPS, 1.0 - EPS)
        won = cell["won_index"]
        total -= float(np.log(1.0 - q_adj).sum())
        total -= float(math.log(q_adj[won])) - float(math.log(1.0 - q_adj[won]))
    return total


def _fit_grid(cells) -> tuple[float, float, float]:
    """Coarse 2-D grid then local refine. Returns (k_hat, w_hat, nll)."""
    ks = np.round(np.arange(K_LO, K_HI + 1e-9, K_STEP), 4)
    ws = np.round(np.arange(W_LO, W_HI + 1e-9, W_STEP), 4)
    best = (1.0, 0.0, _neg_log_likelihood(cells, 1.0, 0.0))
    for k in ks:
        for w in ws:
            nll = _neg_log_likelihood(cells, float(k), float(w))
            if nll < best[2]:
                best = (float(k), float(w), nll)
    k0, w0, _ = best
    for k in np.round(np.arange(max(K_LO, k0 - K_STEP), k0 + K_STEP + 1e-9, K_STEP / 5), 4):
        for w in np.round(np.arange(max(W_LO, w0 - W_STEP), min(W_HI, w0 + W_STEP) + 1e-9, W_STEP / 5), 4):
            nll = _neg_log_likelihood(cells, float(k), float(w))
            if nll < best[2]:
                best = (float(k), float(w), nll)
    return best


def _fit_mle(cells) -> tuple[float, float, float]:
    """Fit (k, w) by ML. scipy when available (multi-start), else the grid fallback."""
    if _HAVE_SCIPY and _scipy_minimize is not None:
        best = None
        starts = [(1.0, 0.0), (2.0, 0.05), (2.5, 0.10), (3.0, 0.15)]
        for k0, w0 in starts:
            try:
                res = _scipy_minimize(
                    lambda x: _neg_log_likelihood(cells, x[0], x[1]),
                    x0=np.array([k0, w0]),
                    method="L-BFGS-B",
                    bounds=[(1.0, K_HI), (0.0, 0.5)],
                )
                if res.success or np.isfinite(res.fun):
                    cand = (float(res.x[0]), float(res.x[1]), float(res.fun))
                    if best is None or cand[2] < best[2]:
                        best = cand
            except Exception:
                continue
        if best is not None and math.isfinite(best[2]):
            # Guard against a flat-region scipy stall: compare to a quick grid and take the better.
            gk, gw, gnll = _fit_grid(cells)
            return (gk, gw, gnll) if gnll < best[2] else best
    return _fit_grid(cells)


def _profile_ci(cells, k_hat: float, w_hat: float, nll_hat: float):
    """Profile-likelihood 95% CIs for k and w (Δ(−LL) = 1.92, χ²₁ 0.95).

    The profile −LL is unimodal in each parameter about the MLE, so each CI is a contiguous interval.
    We walk OUTWARD from the optimum in each direction and stop at the first point above the threshold
    (profiling out the OTHER parameter on a coarse grid at each step).
    """
    thresh = nll_hat + 1.920729
    W_PROFILE = np.round(np.arange(W_LO, 0.5 + 1e-9, 0.02), 4)
    K_PROFILE = np.round(np.arange(K_LO, K_HI + 1e-9, 0.05), 4)

    def nll_k(v):  # profile out w
        return min(_neg_log_likelihood(cells, float(v), float(w)) for w in W_PROFILE)

    def nll_w(v):  # profile out k
        return min(_neg_log_likelihood(cells, float(k), float(v)) for k in K_PROFILE)

    def _walk(center, lo_bound, hi_bound, step, nll_at):
        lo = center
        v = center
        while v - step >= lo_bound - 1e-9:
            v = round(v - step, 4)
            if nll_at(v) <= thresh:
                lo = v
            else:
                break
        hi = center
        v = center
        while v + step <= hi_bound + 1e-9:
            v = round(v + step, 4)
            if nll_at(v) <= thresh:
                hi = v
            else:
                break
        return [round(lo, 4), round(hi, 4)]

    return {
        "k": _walk(k_hat, K_LO, K_HI, 0.01, nll_k),
        "w": _walk(w_hat, W_LO, 0.5, 0.005, nll_w),
    }


def _bootstrap_ci(cells, n_resamples: int, seed: int = 12):
    """Nonparametric bootstrap over CELLS. Returns {'k':[lo,hi],'w':[lo,hi]} 2.5/97.5 percentiles."""
    rng = np.random.default_rng(seed)
    arr = list(cells)
    n = len(arr)
    ks, ws = [], []
    for _ in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        sample = [arr[i] for i in idx]
        k, w, _nll = _fit_grid(sample)  # grid is robust for resamples (no scipy stalls)
        ks.append(k)
        ws.append(w)
    return {
        "k": [round(float(np.percentile(ks, 2.5)), 4), round(float(np.percentile(ks, 97.5)), 4)],
        "w": [round(float(np.percentile(ws, 2.5)), 4), round(float(np.percentile(ws, 97.5)), 4)],
    }


def _calibration_table(cells, k: float, w: float):
    """Per-distance calibration AT (k,w): mean q_adj vs realized win freq, by |bin − mode| in STEP units."""
    agg: dict = defaultdict(lambda: {"sum_q": 0.0, "wins": 0, "n": 0})
    for cell in cells:
        q_adj = _cell_q_adjusted(cell, k, w)
        mode_index = cell["mode_index"]
        won = cell["won_index"]
        for i, q in enumerate(q_adj):
            deg_i = cell["items"][i][2]
            deg_mode = cell["items"][mode_index][2]
            is_open = cell["items"][i][3]
            if deg_i is None or deg_mode is None or is_open:
                dist = "tail"
            else:
                d = int(round(abs(deg_i - deg_mode) / cell["step"]))  # distance in STEP units
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


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fit σ-scale (k) + uniform-mixture (w) by MLE on settled cells (operator law 2026-06-12)."
    )
    ap.add_argument("--fcst", default=FCST_DEFAULT, help="zeus-forecasts.db (forecast_posteriors + settlement_outcomes).")
    ap.add_argument("--out", default=OUT_DEFAULT, help="output sigma_scale_fit.json path.")
    ap.add_argument("--min-cells", type=int, default=MIN_CELLS_DEFAULT, help="min settled cells per unit family to fit (else refuse).")
    ap.add_argument("--bootstrap", type=int, default=0, help="if >0, nonparametric bootstrap CIs over cells with this many resamples (else profile-likelihood CIs).")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.fcst}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute(_FIT_QUERY)
        rows = cur.fetchall()
    finally:
        con.close()

    cells_by_unit, window = _build_cells(rows)
    qhash = hashlib.sha256((_FIT_QUERY + f"|window={window}").encode("utf-8")).hexdigest()[:16]
    fitted_at = _dt.datetime.now(_dt.timezone.utc).isoformat()

    families: dict = {}
    for unit in ("C", "F"):
        cells = cells_by_unit.get(unit, [])
        n_cells = len(cells)
        if n_cells < args.min_cells:
            families[unit] = {
                "fitted": False,
                "k": 1.0, "w": 0.0,
                "n_cells": n_cells,
                "refusal_reason": f"INSUFFICIENT_CELLS:{n_cells}<{args.min_cells}",
                "fitted_at": fitted_at,
                "lead_buckets": list(LEAD_BUCKETS.keys()),
                "method": "refused_insufficient_n",
                "data_window": window,
                "ci": {"k": None, "w": None},
            }
            continue
        k_hat, w_hat, nll_hat = _fit_mle(cells)
        if args.bootstrap > 0:
            ci = _bootstrap_ci(cells, args.bootstrap)
            ci_method = f"bootstrap_cells_{args.bootstrap}"
        else:
            ci = _profile_ci(cells, k_hat, w_hat, nll_hat)
            ci_method = "profile_likelihood_95"
        families[unit] = {
            "fitted": True,
            "k": round(k_hat, 4), "w": round(w_hat, 4),
            "n_cells": n_cells,
            "neg_log_likelihood": round(nll_hat, 4),
            "ci": ci, "ci_method": ci_method,
            "fitted_at": fitted_at,
            "lead_buckets": list(LEAD_BUCKETS.keys()),
            "method": ("mle_scipy_lbfgsb" if _HAVE_SCIPY else "mle_grid_2d"),
            "data_window": window,
            "calibration_at_fit": _calibration_table(cells, k_hat, w_hat),
            "calibration_at_k1_w0": _calibration_table(cells, 1.0, 0.0),
        }

    prov_basis = json.dumps(
        {u: {kk: families[u].get(kk) for kk in ("fitted", "k", "w", "n_cells")} for u in families},
        sort_keys=True,
    ) + f"|qhash={qhash}|authority={AUTHORITY}"
    provenance_hash = hashlib.sha256(prov_basis.encode("utf-8")).hexdigest()[:16]

    table = {
        "_meta": {
            "authority": AUTHORITY,
            "created": fitted_at,
            "method": "max_likelihood_bernoulli_normal_scale_plus_uniform_mixture",
            "model": "q_adj(bin) = (1-w)*Normal(sigma_impl*k) + w*(1/n_bins)",
            "sigma_back_out": "q_mode = 2*Phi(half/sigma_impl)-1 ; sigma_impl = half/Phi^-1((q_mode+1)/2)",
            "min_cells": int(args.min_cells),
            "lead_buckets": LEAD_BUCKETS,
            "metric": DATA_WINDOW_METRIC,
            "data_window": window,
            "source": "forecast_posteriors ⋈ settlement_outcomes(authority=VERIFIED), high metric, no-leak lead-bucketed",
            "source_query_hash": qhash,
            "provenance_hash": provenance_hash,
            "scipy_available": bool(_HAVE_SCIPY),
        },
        "families": families,
    }

    tmp = f"{args.out}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=2, sort_keys=True)
    os.replace(tmp, args.out)

    print(f"[sigma-scale] wrote {args.out}  (window={window}, provenance={provenance_hash})")
    for unit in ("C", "F"):
        fam = families[unit]
        if fam["fitted"]:
            print(
                f"    {unit}: FITTED k={fam['k']} w={fam['w']} n_cells={fam['n_cells']} "
                f"CI_k={fam['ci']['k']} CI_w={fam['ci']['w']} [{fam['ci_method']}]"
            )
        else:
            print(f"    {unit}: REFUSED ({fam['refusal_reason']}) -> materializer stays inert")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
