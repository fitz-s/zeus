# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: docs/authority/statistical_calibration_addendum_2026-06-13.md
#   (A2 — BH/FDR on the trading path CONDEMNED; replacement = posterior
#   expected-log-utility thresholding + correlation-aware EB selection
#   shrinkage; D3 — Tweedie nonparametric upgrade at N>=200, winner's-curse
#   slope diagnostic) + consult2_era_contamination_fdr_maker_2026-06-13_raw.txt
#   Q3 (full math + reference impls eb_shrink_edges / local_false_sign_rate /
#   select_by_posterior_utility / effective_num_tests_from_cov) + Fable
#   double-review consult2_crossvalidation_fable5_2026-06-13.md (CONVERGENT,
#   family-clustered tau^2 refinement: subtract average within-family
#   covariance). Plan: docs/evidence/plans/2026-06-13_c2_c3_trading_math.md.
"""Selection-stage shrinkage and posterior-utility licensing (C2).

Replacement math for the vacuous {0,1}-p-value BH/FDR gate on the live
trading path. The BH gate consumes degenerate p-values in {0,1} (every 0
passes, every 1 fails — a literal no-op multiplicity correction; see
``src/engine/event_reactor_adapter.py`` lines 9854/9876) and, even with
continuous p-values, mutually exclusive sum-to-one bins violate PRDS so
BH is invalid; FDR controls E[V/R], not bankroll log growth — the wrong
objective entirely.

The replacement (authority A2) is honest math, NOT a throttle (NO-CAPS law):

  * ``lfsr``                — local false-sign rate P(e <= 0 | D), the
                             posterior replacement for the p-value column.
  * ``eb_shrink_edges``     — correlation-aware normal-normal Empirical-Bayes
                             shrinkage over the day's candidate universe,
                             correcting winner's curse (the selected raw max
                             edge is inflated by ~s*sqrt(2 ln N_eff) under the
                             null). Family-clustered tau^2 (Fable refinement):
                             when family labels are supplied, the average
                             within-family covariance is subtracted from the
                             cross-sectional variance so mutually-exclusive bin
                             anticorrelation does not masquerade as prior spread.
  * ``tweedie_shrink``      — nonparametric KDE shrinkage, LICENSED only at
                             N>=200 candidates/day (asserted), else falls back
                             to normal-normal.
  * ``select_license``      — trade iff the shrunk posterior edge clears e_min
                             with P(e > e_min | D) >= pi_min AND the posterior
                             expected log growth at the Kelly fraction > 0.

This module is pure (numpy only) and has no Zeus runtime imports, so it is
safe to import from the reactor, scripts, and tests alike.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

# Tweedie's formula is a nonparametric (KDE) estimator of the marginal score;
# it is only trustworthy when the candidate cross-section is large enough to
# estimate that density. Authority D3: license at daily N >= 200, else fall
# back to normal-normal EB. This is a data-sufficiency licence (honest math),
# not an artificial cap on trading.
TWEEDIE_MIN_CANDIDATES = 200

# Default licensing constants (authority A2; settings-overridable at the call
# site). pi_min is the posterior probability the shrunk edge clears e_min.
DEFAULT_PI_MIN = 0.90


# ---------------------------------------------------------------------------
# Local false-sign rate (posterior replacement for the p-value column)
# ---------------------------------------------------------------------------
def lfsr(
    edge_samples: Sequence[float] | np.ndarray | None = None,
    *,
    e_hat: float | None = None,
    s: float | None = None,
    threshold: float = 0.0,
) -> float:
    """Local false-sign rate: P(e <= threshold | D).

    Two input modes:

      * ``edge_samples`` — posterior draws of the edge for ONE candidate
        (1-D array). lfsr = empirical fraction at or below ``threshold``.
      * ``(e_hat, s)``   — normal approximation N(e_hat, s^2) when posterior
        samples are unavailable: lfsr = Phi((threshold - e_hat) / s).

    This is the per-candidate quantity stamped on receipts in place of the
    degenerate FDR p-value. A small lfsr means the posterior is confident the
    edge is positive (the certified-probability authority backs the trade);
    lfsr -> 1 means the candidate has no certified edge.
    """
    if edge_samples is not None:
        arr = np.asarray(edge_samples, dtype=float).ravel()
        if arr.size == 0:
            raise ValueError("lfsr: edge_samples is empty")
        return float(np.mean(arr <= threshold))
    if e_hat is None or s is None:
        raise ValueError("lfsr requires edge_samples OR (e_hat, s)")
    s = float(s)
    if s <= 0.0:
        # Degenerate posterior: a point mass. Sign is deterministic.
        return 1.0 if float(e_hat) <= threshold else 0.0
    z = (threshold - float(e_hat)) / s
    return float(0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))


# ---------------------------------------------------------------------------
# Effective number of (independent) tests from a covariance matrix
# ---------------------------------------------------------------------------
def effective_num_tests_from_cov(cov: np.ndarray) -> float:
    """Li-Ji-style effective test count from the correlation eigenvalues.

    N_eff = (sum lambda_i)^2 / sum lambda_i^2 over the correlation-matrix
    eigenvalues. Correlated candidates count as fewer than N independent
    tests; this drives the winner's-curse expected-max sanity check.
    """
    S = np.asarray(cov, dtype=float)
    d = np.sqrt(np.maximum(np.diag(S), 1e-15))
    R = S / np.outer(d, d)
    vals = np.linalg.eigvalsh(0.5 * (R + R.T))
    vals = np.maximum(vals, 0.0)
    denom = float(np.sum(vals**2))
    if denom <= 0.0:
        return 1.0
    return float((vals.sum() ** 2) / denom)


def expected_max_standard_normal(n_eff: float) -> float:
    """E[max of n_eff iid standard normals] (extreme-value approximation).

    Authority A2 winner's-curse sanity: at N_eff=24 ~ 1.79; N_eff=288 ~ 2.73.
    """
    n = max(float(n_eff), 1.000001)
    a = math.sqrt(2.0 * math.log(n))
    b = (math.log(math.log(n)) + math.log(4.0 * math.pi)) / (2.0 * a)
    return a - b


# ---------------------------------------------------------------------------
# Correlation-aware normal-normal Empirical-Bayes shrinkage
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EBShrinkResult:
    """Result of an EB shrinkage fit over a candidate universe."""

    shrunk_mean: np.ndarray          # posterior mean m_j (the de-cursed edge)
    posterior_sd: np.ndarray         # sqrt(diag(V)) per candidate
    posterior_cov: np.ndarray        # full posterior covariance V
    mu: float                        # EB grand mean
    tau2: float                      # EB prior variance


def _family_clustered_tau2_floor(
    e_hat: np.ndarray, families: Sequence[object] | None
) -> float:
    """Cross-sectional variance with average within-family covariance removed.

    Fable failure-mode (i): families are mutually-exclusive sum-to-one bins, so
    the within-family edge estimates are negatively correlated. Pooling them as
    if exchangeable INFLATES the naive cross-sectional variance and therefore
    tau^2 (the prior spread), under-shrinking. The correction subtracts the
    average within-family covariance of e_hat from the total variance to get an
    honest between-family prior-variance scale. Clamped at 0 (a prior variance
    cannot be negative). Returns the de-biased variance scale; the EB optimizer
    still searches the full grid, but this floors the grid's upper bound so the
    fit cannot chase within-family anticorrelation as if it were signal.
    """
    total_var = float(np.var(e_hat))
    if families is None:
        return total_var
    fam = list(families)
    if len(fam) != len(e_hat):
        raise ValueError("families length must match e_hat length")
    # Average within-family covariance = mean over families of the mean
    # off-diagonal sample covariance within that family.
    within_cov_terms: list[float] = []
    grand = float(np.mean(e_hat))
    by_fam: dict[object, list[int]] = {}
    for idx, f in enumerate(fam):
        by_fam.setdefault(f, []).append(idx)
    for members in by_fam.values():
        if len(members) < 2:
            continue
        centered = np.asarray([e_hat[i] - grand for i in members], dtype=float)
        # mean off-diagonal product = (sum^2 - sum_of_squares) / (k*(k-1))
        s = float(centered.sum())
        ss = float(np.sum(centered**2))
        k = len(members)
        off_diag_mean = (s * s - ss) / (k * (k - 1))
        within_cov_terms.append(off_diag_mean)
    if not within_cov_terms:
        return total_var
    avg_within_cov = float(np.mean(within_cov_terms))
    return max(total_var - avg_within_cov, 0.0)


def eb_shrink_edges(
    e_hat: Sequence[float] | np.ndarray,
    s: Sequence[float] | np.ndarray,
    R: np.ndarray | None = None,
    *,
    families: Sequence[object] | None = None,
    tau2_grid: np.ndarray | None = None,
) -> EBShrinkResult:
    """Correlation-aware normal-normal EB shrinkage over the day's universe.

    Model:  e_hat | e ~ N(e, S);  e ~ N(mu*1, tau^2 R).

    ``s`` are the per-candidate posterior SDs (S = diag(s^2) unless a full
    noise covariance is implied by R). ``R`` is the candidate correlation
    matrix (identity if omitted). ``families`` supplies the family label per
    candidate for the Fable within-family covariance subtraction in the tau^2
    grid construction.

    Returns the posterior (shrunk) means, per-candidate posterior SDs, the full
    posterior covariance, and the fitted (mu, tau^2). When tau^2 collapses to 0
    the universe carries no between-candidate signal and every shrunk mean is
    the grand mean (maximal shrinkage — the correct winner's-curse limit).
    """
    e_hat = np.asarray(e_hat, dtype=float).ravel()
    s = np.asarray(s, dtype=float).ravel()
    N = e_hat.size
    if N == 0:
        raise ValueError("eb_shrink_edges: empty candidate universe")
    if s.size != N:
        raise ValueError("eb_shrink_edges: s must match e_hat length")
    one = np.ones(N)
    S = np.diag(np.maximum(s**2, 1e-15))
    if R is None:
        R = np.eye(N)
    else:
        R = np.asarray(R, dtype=float)
        if R.shape != (N, N):
            raise ValueError("eb_shrink_edges: R must be (N, N)")

    if tau2_grid is None:
        avg_noise = float(np.mean(np.diag(S)))
        # Fable family-clustered de-biased variance scale floors the grid top.
        empirical = _family_clustered_tau2_floor(e_hat, families)
        hi = max(100.0 * avg_noise, 100.0 * empirical, 1e-8)
        tau2_grid = np.r_[0.0, np.exp(np.linspace(np.log(1e-12), np.log(hi), 500))]

    best: tuple[float, float, float] | None = None  # (nll, mu, tau2)
    for tau2 in tau2_grid:
        C = S + tau2 * R
        C = 0.5 * (C + C.T)
        sign, logdet = np.linalg.slogdet(C)
        if sign <= 0:
            continue
        Cinv_one = np.linalg.solve(C, one)
        mu = float((one @ np.linalg.solve(C, e_hat)) / (one @ Cinv_one))
        resid = e_hat - mu * one
        nll = 0.5 * (logdet + float(resid @ np.linalg.solve(C, resid)))
        if best is None or nll < best[0]:
            best = (nll, mu, float(tau2))

    assert best is not None, "eb_shrink_edges: tau2 grid produced no valid fit"
    _, mu, tau2 = best

    if tau2 <= 1e-15:
        m = mu * one
        V = np.zeros((N, N))
        return EBShrinkResult(m, np.zeros(N), V, mu, 0.0)

    Sinv = np.linalg.inv(S)
    Rinv = np.linalg.inv(R)
    prec = Sinv + (1.0 / tau2) * Rinv
    V = np.linalg.inv(prec)
    m = V @ (Sinv @ e_hat + (1.0 / tau2) * Rinv @ (mu * one))
    post_sd = np.sqrt(np.maximum(np.diag(V), 0.0))
    return EBShrinkResult(m, post_sd, V, mu, tau2)


# ---------------------------------------------------------------------------
# Tweedie nonparametric shrinkage (licensed at N >= 200)
# ---------------------------------------------------------------------------
def tweedie_shrink(
    e_hat: Sequence[float] | np.ndarray,
    s: Sequence[float] | np.ndarray,
    *,
    bandwidth: float | None = None,
) -> np.ndarray:
    """Tweedie's-formula selection-bias correction (Efron 2011).

    E[e | e_hat] = e_hat + s^2 * d/de_hat log f(e_hat), where f is the marginal
    density of all candidate edges, estimated by a Gaussian KDE.

    LICENSED only at N >= TWEEDIE_MIN_CANDIDATES (authority D3): the KDE score
    is unreliable below that, so this asserts and the caller must fall back to
    ``eb_shrink_edges``. The licence is a data-sufficiency gate (honest math),
    never an artificial trading cap.
    """
    e_hat = np.asarray(e_hat, dtype=float).ravel()
    s = np.asarray(s, dtype=float).ravel()
    N = e_hat.size
    assert N >= TWEEDIE_MIN_CANDIDATES, (
        f"tweedie_shrink licensed only at N>={TWEEDIE_MIN_CANDIDATES} "
        f"candidates/day (got {N}); fall back to eb_shrink_edges"
    )
    if s.size != N:
        raise ValueError("tweedie_shrink: s must match e_hat length")
    # Silverman bandwidth for the marginal density of e_hat.
    if bandwidth is None:
        std = float(np.std(e_hat))
        iqr = float(np.subtract(*np.percentile(e_hat, [75, 25])))
        spread = min(std, iqr / 1.349) if iqr > 0 else std
        spread = spread if spread > 0 else 1.0
        bandwidth = 0.9 * spread * N ** (-1.0 / 5.0)
    h = float(bandwidth)
    # Gaussian KDE density f and its derivative f' at each e_hat point.
    diffs = (e_hat[:, None] - e_hat[None, :]) / h
    kern = np.exp(-0.5 * diffs**2) / (math.sqrt(2.0 * math.pi) * h)
    f = kern.mean(axis=1)
    fprime = (-(diffs / h) * kern).mean(axis=1)
    f = np.maximum(f, 1e-300)
    score = fprime / f  # d/de_hat log f(e_hat)
    return e_hat + s**2 * score


# ---------------------------------------------------------------------------
# Posterior expected-log-growth and trade license
# ---------------------------------------------------------------------------
def kelly_fraction_binary(
    q: float | np.ndarray, price: float | np.ndarray, f_cap: float = 0.05
) -> np.ndarray:
    """Bankroll fraction for a binary contract (no fees), clipped to [0, f_cap]."""
    q = np.asarray(q, dtype=float)
    price = np.asarray(price, dtype=float)
    f = (q - price) / np.maximum(1.0 - price, 1e-15)
    return np.clip(f, 0.0, f_cap)


def expected_log_growth_binary(
    q: float | np.ndarray, price: float | np.ndarray, f: float | np.ndarray
) -> np.ndarray:
    """Posterior expected log growth from spending fraction f on the contract."""
    q = np.asarray(q, dtype=float)
    price = np.asarray(price, dtype=float)
    b = (1.0 - price) / np.maximum(price, 1e-15)
    return q * np.log1p(f * b) + (1.0 - q) * np.log1p(-np.asarray(f, dtype=float))


@dataclass(frozen=True)
class SelectionLicense:
    """The posterior-utility trade license for one candidate."""

    licensed: bool
    edge_shrunk: float
    edge_shrunk_posterior_sd: float
    lfsr: float
    prob_edge: float                 # P(e > e_min | D)
    expected_log_growth: float
    kelly_fraction: float
    reason: str                      # PASS or a typed decline reason


def select_license(
    *,
    edge_shrunk: float,
    edge_shrunk_posterior_sd: float,
    q_posterior: float,
    price: float,
    e_min: float = 0.0,
    pi_min: float = DEFAULT_PI_MIN,
    f_cap: float = 0.05,
    edge_samples: Sequence[float] | np.ndarray | None = None,
) -> SelectionLicense:
    """Honest posterior-utility trade license (authority A2 replacement gate).

    Trade iff BOTH hold:

      1. P(e > e_min | D) >= pi_min  — the shrunk posterior edge clears the
         action threshold with high posterior probability. Computed from
         ``edge_samples`` when supplied, else the normal approximation
         N(edge_shrunk, posterior_sd^2).
      2. posterior expected log growth at the Kelly fraction > 0 — the trade
         grows the bankroll in expectation under the calibrated posterior.

    This is the bankroll-growth objective the FDR gate failed to address. It
    is honest math: there is no artificial cap — a candidate with a genuine,
    confident, growth-positive edge is always licensed (NO-CAPS law).
    """
    if edge_samples is not None:
        arr = np.asarray(edge_samples, dtype=float).ravel()
        prob_edge = float(np.mean(arr > e_min))
        sign_rate = float(np.mean(arr <= 0.0))
    else:
        sd = float(edge_shrunk_posterior_sd)
        if sd <= 0.0:
            prob_edge = 1.0 if edge_shrunk > e_min else 0.0
            sign_rate = 0.0 if edge_shrunk > 0.0 else 1.0
        else:
            z = (e_min - float(edge_shrunk)) / sd
            prob_edge = 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
            z0 = (0.0 - float(edge_shrunk)) / sd
            sign_rate = 0.5 * (1.0 + math.erf(z0 / math.sqrt(2.0)))

    f = float(kelly_fraction_binary(q_posterior, price, f_cap=f_cap))
    growth = float(expected_log_growth_binary(q_posterior, price, f))

    if prob_edge < pi_min:
        reason = f"PROB_EDGE_BELOW_PI_MIN:{prob_edge:.4f}<{pi_min:.4f}"
        licensed = False
    elif growth <= 0.0:
        reason = f"NON_POSITIVE_LOG_GROWTH:{growth:.6f}"
        licensed = False
    else:
        reason = "PASS"
        licensed = True

    return SelectionLicense(
        licensed=licensed,
        edge_shrunk=float(edge_shrunk),
        edge_shrunk_posterior_sd=float(edge_shrunk_posterior_sd),
        lfsr=sign_rate,
        prob_edge=prob_edge,
        expected_log_growth=growth,
        kelly_fraction=f,
        reason=reason,
    )
