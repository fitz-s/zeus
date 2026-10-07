"""Dense station -> settlement value: measurement, hard-floor, nowcast and scoring mathematics (pure; no IO).

Notation (contract unit, deg C for every city here):
  R(v)   settlement rounding = src.contracts.settlement_semantics.SettlementSemantics(rounding_rule='wmo_half_up',
         precision=1).round_single  ->  floor(v + 0.5)   (2.5 -> 3, -0.5 -> 0, -1.5 -> -1, -2.51 -> -3)
  x(t)   dense reading at instant t;  M(t) METAR/SPECI integer at settlement instant t.

Measurement model   M(t) = R(x(t) + b + eps),  eps = sigma * Z,  Z ~ N(0,1) (baseline) or Student-t(nu).
  M = k  <=>  x + b + eps in [k - 0.5, k + 0.5)  <=>  Z in [(k - 0.5 - x - b)/sigma, (k + 0.5 - x - b)/sigma)
  so each pair contributes log P(lo <= Z < hi): interval-censored maximum likelihood (fit_icm).

Hard floor (high)   floor(t) = R(x(t) + b - m).  If eps(t) >= -m then M(t) >= floor(t), hence H_d >= floor(t).
Hard ceiling (low)  ceil(t)  = R(x(t) + b + m).  If eps(t) <=  m then M(t) <= ceil(t),  hence L_d <= ceil(t).
  False high floor  <=>  x + b - m >= H + 0.5  <=>  m <= c,  c = x + b - H - 0.5   (zero false iff m >  max c)
  False low ceiling <=>  x + b + m <  L - 0.5  <=>  m <  c,  c = L - 0.5 - x - b   (zero false iff m >= max c)
  The asymmetry (strict vs inclusive) is R's half-up edge: v = k + 0.5 rounds to k + 1.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from scipy import optimize, special, stats

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402

SEM = SettlementSemantics(resolution_source="dense_station_model", measurement_unit="C", precision=1.0,
                          rounding_rule="wmo_half_up", finalization_time="12:00:00Z")


def R(v):
    """Settlement rounding. Scalar -> SEM.round_single (the settlement-write gate); array -> SEM.round_values,
    the array form round_single wraps (identical formula)."""
    if np.ndim(v) == 0:
        return int(SEM.round_single(float(v)))
    a = np.asarray(v, dtype=float)
    return SEM.round_values(a.reshape(-1)).reshape(a.shape).astype(np.int64)


# ---------------------------------------------------------------- interval-censored likelihood

def log_interval_prob(lo, hi, family="gauss", nu=None):
    """log P(lo <= Z < hi) for standard Z (normal, or Student-t with nu dof); stable in both tails."""
    lo, hi = np.broadcast_arrays(np.asarray(lo, float), np.asarray(hi, float))
    flip = lo > 0  # symmetric law: P(lo <= Z < hi) = P(-hi < Z <= -lo), evaluated in the lower tail
    a, c = np.where(flip, -hi, lo), np.where(flip, -lo, hi)
    if family == "gauss":
        la, lc = special.log_ndtr(a), special.log_ndtr(c)
    elif family == "t":
        la, lc = stats.t.logcdf(a, nu), stats.t.logcdf(c, nu)
    else:
        raise ValueError(family)
    return lc + np.log(-np.expm1(np.minimum(la - lc, -1e-300)))


def num_hessian(f, x, h=1e-4):
    x = np.asarray(x, float)
    n = x.size
    H = np.zeros((n, n))
    for i in range(n):
        for j in range(i, n):
            ei, ej = np.zeros(n), np.zeros(n)
            ei[i] = h
            ej[j] = h
            H[i, j] = H[j, i] = (f(x + ei + ej) - f(x + ei - ej) - f(x - ei + ej) + f(x - ei - ej)) / (4 * h * h)
    return H


def fit_icm(k, offset, X=None, family="gauss", nu=None, sigma_bounds=(0.005, 20.0)):
    """Interval-censored ML for  k = R(offset + X @ beta + sigma * Z).

    k: observed integers; offset: known continuous part (the dense reading); X: covariates (default intercept -> b).
    Returns beta, sigma, standard errors (inverse numerical Hessian), nll, aic."""
    k = np.asarray(k, float)
    off = np.asarray(offset, float)
    n = k.size
    X = np.ones((n, 1)) if X is None else np.asarray(X, float).reshape(n, -1)
    p = X.shape[1]
    lb, ub = math.log(sigma_bounds[0]), math.log(sigma_bounds[1])

    def nll(th):
        mu = off + X @ th[:p]
        s = math.exp(min(max(th[p], lb - 5), ub + 5))
        return -float(np.sum(log_interval_prob((k - 0.5 - mu) / s, (k + 0.5 - mu) / s, family, nu)))

    r = k - off
    b0 = np.linalg.lstsq(X, r, rcond=None)[0]
    s0 = math.sqrt(max(float(np.var(r - X @ b0)) - 1 / 12, 0.01))
    th0 = np.r_[b0, min(max(math.log(s0), lb), ub)]
    res = optimize.minimize(nll, th0, method="L-BFGS-B", bounds=[(None, None)] * p + [(lb, ub)])
    th = res.x
    nm = optimize.minimize(nll, th, method="Nelder-Mead", options=dict(xatol=1e-7, fatol=1e-9, maxiter=5000))
    if nm.fun < nll(th) and lb <= nm.x[p] <= ub:
        th = nm.x
    f = nll(th)
    try:
        se = np.sqrt(np.clip(np.diag(np.linalg.inv(num_hessian(nll, th))), 0, None))
    except np.linalg.LinAlgError:
        se = np.full(p + 1, np.nan)
    sigma = math.exp(th[p])
    npar = p + 1 + (family == "t")
    return dict(family=family, nu=nu, beta=[float(v) for v in th[:p]], sigma=sigma,
                se_beta=[float(v) for v in se[:p]], se_sigma=float(sigma * se[p]), nll=f, n=int(n),
                n_params=int(npar), aic=2 * f + 2 * npar,
                at_sigma_bound=bool(th[p] <= lb + 1e-6 or th[p] >= ub - 1e-6))


def fit_icm_t(k, offset, X=None, nus=(1, 2, 3, 5, 8, 15, 30)):
    """Heavier-tailed alternative: Student-t eps, nu chosen by profile likelihood over a grid."""
    fits = [fit_icm(k, offset, X, family="t", nu=v) for v in nus]
    best = min(fits, key=lambda f: f["nll"])
    best["nu_profile_nll"] = {str(f["nu"]): round(f["nll"], 3) for f in fits}
    return best


def pmf_rows(mu, sigma, K, family="gauss", nu=None):
    """P(R(mu_i + sigma Z) = K_ij) for each row i and candidate integer K_ij."""
    mu = np.asarray(mu, float)[:, None]
    K = np.asarray(K, float)
    return np.exp(log_interval_prob((K - 0.5 - mu) / sigma, (K + 0.5 - mu) / sigma, family, nu))


def offset_table(k, x, b, sigma, family="gauss", nu=None, js=(-3, -2, -1, 0, 1, 2, 3)):
    """Goodness of fit on the operator's own statistic j = M - R(x): observed counts vs model-expected counts."""
    k = np.asarray(k, int)
    x = np.asarray(x, float)
    base = R(x)
    J = np.asarray(js)
    P = pmf_rows(x + b, sigma, base[:, None] + J[None, :], family, nu)
    obs = {int(j): int(np.sum(k - base == j)) for j in J}
    exp = {int(j): round(float(P[:, i].sum()), 1) for i, j in enumerate(J)}
    chi2 = sum((obs[j] - exp[j]) ** 2 / exp[j] for j in obs if exp[j] >= 5)
    return dict(observed=obs, expected=exp, outside=int(np.sum(np.abs(k - base) > max(J))), chi2_cells_ge5=round(chi2, 2))


def generalized_residual(k, mu, sigma):
    """E[eps | M = k] under the Gaussian measurement model (truncated-normal mean)."""
    k = np.asarray(k, float)
    lo, hi = (k - 0.5 - mu) / sigma, (k + 0.5 - mu) / sigma
    lp = log_interval_prob(lo, hi)
    lphi = lambda z: -0.5 * z * z - 0.5 * math.log(2 * math.pi)  # noqa: E731
    return sigma * (np.exp(lphi(lo) - lp) - np.exp(lphi(hi) - lp))


# ---------------------------------------------------------------- latent autocorrelation (pairwise likelihood)

_GLX, _GLW = np.polynomial.legendre.leggauss(24)


def bvn_cdf(h, k, rho):
    """Phi2(h, k; rho) = Phi(h)Phi(k) + int_0^rho phi2(h, k; r) dr  (24-node Gauss-Legendre in r)."""
    h = np.clip(np.asarray(h, float), -9, 9)
    k = np.clip(np.asarray(k, float), -9, 9)
    base = special.ndtr(h) * special.ndtr(k)
    if rho == 0:
        return base
    r = 0.5 * rho * (_GLX + 1)
    w = 0.5 * rho * _GLW
    hh, kk, one = h[..., None], k[..., None], 1 - r * r
    f = np.exp(-(hh * hh - 2 * r * hh * kk + kk * kk) / (2 * one)) / np.sqrt(one)
    return base + (f * w).sum(-1) / (2 * math.pi)


def fit_rho(l1, u1, l2, u2, bound=0.95):
    """Latent correlation of (Z_t, Z_t+1) from consecutive interval-censored pairs (standardised limits).
    Pairwise likelihood; the marginal terms do not depend on rho and are dropped."""
    l1, u1, l2, u2 = (np.asarray(a, float) for a in (l1, u1, l2, u2))

    def nll(r):
        P = bvn_cdf(u1, u2, r) - bvn_cdf(l1, u2, r) - bvn_cdf(u1, l2, r) + bvn_cdf(l1, l2, r)
        return -float(np.sum(np.log(np.clip(P, 1e-300, None))))

    res = optimize.minimize_scalar(nll, bounds=(-bound, bound), method="bounded", options=dict(xatol=1e-5))
    r, h = float(res.x), 1e-3
    d2 = (nll(min(r + h, bound)) - 2 * nll(r) + nll(max(r - h, -bound))) / (h * h)
    return dict(rho=r, se=float(1 / math.sqrt(d2)) if d2 > 0 else None, n_pairs=int(l1.size),
                at_bound=bool(abs(r) > bound - 1e-3))


# ---------------------------------------------------------------- hard floor / ceiling

def floor_high(xb, m):
    return R(np.asarray(xb, float) - m)


def ceil_low(xb, m):
    return R(np.asarray(xb, float) + m)


def false_mask(xb, truth, m, side):
    """Instants whose floor exceeds (high) / ceiling undercuts (low) the settled extreme of their day."""
    if side == "high":
        return floor_high(xb, m) > np.asarray(truth)
    return ceil_low(xb, m) < np.asarray(truth)


def false_days(day, xb, truth, m, side):
    return int(np.unique(np.asarray(day)[false_mask(xb, truth, m, side)]).size)


def critical_margin(xb, truth, side):
    """Exact boundary c* of the zero-false set: high needs m > c*, low needs m >= c*."""
    xb, truth = np.asarray(xb, float), np.asarray(truth, float)
    c = xb - truth - 0.5 if side == "high" else truth - 0.5 - xb
    return float(np.max(c))


def min_zero_false_margin(day, xb, truth, side, step=0.05, m_max=6.0):
    """Smallest m on the grid {0, step, 2 step, ...} with zero false days, found by direct application of R."""
    for m in np.round(np.arange(0.0, m_max + step / 2, step), 10):
        if false_days(day, xb, truth, m, side) == 0:
            return float(m)
    return None


# ---------------------------------------------------------------- probabilistic scoring

def brier_multi(P, K, obs):
    """Multi-category Brier: sum_k (p_k - o_k)^2 per case; an outcome outside the support K adds 1."""
    O = (np.asarray(K) == np.asarray(obs)[:, None]).astype(float)
    return float(np.mean(((P - O) ** 2).sum(1) + (1.0 - O.sum(1))))


def log_loss(P, K, obs, eps=1e-6):
    O = np.asarray(K) == np.asarray(obs)[:, None]
    p = np.where(O.any(1), (P * O).sum(1), 0.0)
    return float(np.mean(-np.log(np.clip(p, eps, None))))


def reliability(P, K, obs, edges=np.linspace(0, 1, 11)):
    """Reliability table over every (case, category) probability; ECE = n-weighted |mean p - observed freq|."""
    p = np.asarray(P).ravel()
    o = (np.asarray(K) == np.asarray(obs)[:, None]).ravel()
    idx = np.clip(np.digitize(p, edges) - 1, 0, len(edges) - 2)
    rows, ece = [], 0.0
    for i in range(len(edges) - 1):
        s = idx == i
        if s.any():
            mp, of = float(p[s].mean()), float(o[s].mean())
            rows.append(dict(bin=f"{edges[i]:.1f}-{edges[i + 1]:.1f}", n=int(s.sum()), mean_p=round(mp, 4), obs_freq=round(of, 4)))
            ece += s.sum() / p.size * abs(mp - of)
    return dict(table=rows, ece=round(ece, 5))


def quantiles(v, qs=(0.1, 0.5, 0.9)):
    v = np.asarray([x for x in v if x is not None and not (isinstance(x, float) and math.isnan(x))], float)
    if v.size == 0:
        return dict(n=0)
    out = {f"p{int(q * 100)}": round(float(np.quantile(v, q)), 2) for q in qs}
    out.update(n=int(v.size), earlier=int(np.sum(v > 0)), tie=int(np.sum(v == 0)), later=int(np.sum(v < 0)))
    return out
