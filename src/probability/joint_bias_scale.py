# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: docs/authority/statistical_calibration_authority_2026-06-12.txt (operator-run
#   clean-room consult REQ-20260612-174119), Step 1 — joint (b,k) interval-censored categorical
#   likelihood; DELETE variance-only k fit. Reference implementation: authority §1.1 cell_probs /
#   neg_loglik / fit_bias_scale (lines ~737-786). Proven result #1: k_wrong² = k_true² + (δ/σ)²,
#   so a variance-only spread fit absorbs unmodeled center bias → the over-confidence measured on
#   the real chain (served q 0.89 vs realized 0.72). This estimator restores honest dispersion by
#   estimating the location bias b and the scale k JOINTLY.
"""Joint bias+scale interval-censored calibration estimator (authority Step 1).

The likelihood is the categorical likelihood induced by integrating the latent predictive
N(mu + b, (k·sigma)) over each exchange bin's settlement-rounding preimage interval, evaluated at
the realized (winning) bin. Estimating b alongside k prevents the variance-only fit from folding a
location bias into the spread (the documented source of over-confidence). No uniform mixture (the
authority condemns the constant uniform pedestal); no hand-set constants — both b and k are fit by
maximum likelihood on settled cells.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

try:  # vectorized standard-normal CDF
    from scipy.special import ndtr as _ndtr  # type: ignore
    from scipy.optimize import minimize as _minimize  # type: ignore
except Exception:  # pragma: no cover - scipy is a hard dep of the calibration path
    _ndtr = None
    _minimize = None

_EPS = 1e-15


@dataclass(frozen=True)
class CalibrationCell:
    """One settled forecast cell.

    mu, sigma: the forecast center and base predictive sigma (same native unit as the bin edges).
    edges: the FULL ordered bin partition as (lo, hi) preimage boundaries; open tails use None.
    winning_index: index into ``edges`` of the bin the outcome settled into.
    """

    mu: float
    sigma: float
    edges: tuple[tuple[float | None, float | None], ...]
    winning_index: int

    def __post_init__(self):
        # normalize edges to a tuple of 2-tuples (allow list input)
        object.__setattr__(self, "edges", tuple((lo, hi) for lo, hi in self.edges))


def _winning_bounds(cells: list[CalibrationCell]):
    """Extract per-cell (mu, sigma, winning lo, winning hi) as numpy arrays; tails -> ±inf."""
    mu = np.array([c.mu for c in cells], dtype=float)
    sigma = np.array([c.sigma for c in cells], dtype=float)
    lo = np.empty(len(cells), dtype=float)
    hi = np.empty(len(cells), dtype=float)
    for i, c in enumerate(cells):
        wlo, whi = c.edges[c.winning_index]
        lo[i] = -np.inf if wlo is None else float(wlo)
        hi[i] = np.inf if whi is None else float(whi)
    return mu, sigma, lo, hi


def _winning_mass(mu, sigma, lo, hi, b, k):
    """Mass of the winning bin under N(mu+b, (k·sigma)) — the interval-censored likelihood term."""
    center = mu + b
    scale = np.maximum(k * sigma, _EPS)
    z_hi = (hi - center) / scale
    z_lo = (lo - center) / scale
    mass = _ndtr(z_hi) - _ndtr(z_lo)
    return np.clip(mass, _EPS, 1.0)


def neg_loglik_joint(theta, mu, sigma, lo, hi) -> float:
    b = float(theta[0])
    k = math.exp(float(theta[1]))
    return float(-np.sum(np.log(_winning_mass(mu, sigma, lo, hi, b, k))))


def neg_loglik_scale_only(theta, mu, sigma, lo, hi) -> float:
    k = math.exp(float(theta[0]))
    return float(-np.sum(np.log(_winning_mass(mu, sigma, lo, hi, 0.0, k))))


def fit_joint_bias_scale(cells: list[CalibrationCell]):
    """Joint (b,k) interval-censored MLE. Returns (b_hat, k_hat, result).

    This is the Step-1 fix: estimating b alongside k removes the bias-absorption that inflates a
    variance-only k and makes the served q over-confident.
    """
    if _minimize is None:
        raise RuntimeError("scipy required for joint_bias_scale fit")
    mu, sigma, lo, hi = _winning_bounds(cells)
    res = _minimize(
        neg_loglik_joint, np.array([0.0, 0.0]), args=(mu, sigma, lo, hi), method="L-BFGS-B"
    )
    b_hat = float(res.x[0])
    k_hat = float(math.exp(res.x[1]))
    return b_hat, k_hat, res


def fit_scale_only(cells: list[CalibrationCell]):
    """Variance-only k MLE (b forced to 0) — the contaminated current fit, kept for the gate/contrast.

    Returns (k_hat, result).
    """
    if _minimize is None:
        raise RuntimeError("scipy required for scale-only fit")
    mu, sigma, lo, hi = _winning_bounds(cells)
    res = _minimize(
        neg_loglik_scale_only, np.array([0.0]), args=(mu, sigma, lo, hi), method="L-BFGS-B"
    )
    k_hat = float(math.exp(res.x[0]))
    return k_hat, res
