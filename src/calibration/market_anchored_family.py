# Created: 2026-09-26
# Last reused or audited: 2026-09-26
# Authority basis: design review REQ-20260925-223704 §3 (ordered family CDF
#   correction, nested candidates, partial pooling, city-day weighting) and §8
#   (historical rows are diagnostics only); release test in
#   docs/operations/current/plans/day0_probability_repair_2026-09-25.md.
#   OFFLINE ONLY: no live solve, certificate or submit caller.
"""Market-anchored family CDF correction: nested candidates and their fitter.

A family is one ordered categorical outcome over K settlement bins. With raw
weather masses Q and a coherent market reference P, the beta-transformed
linear pool is

    H_k = w F_Q(k) + (1 - w) F_P(k),   F~_k = I_{H_k}(alpha, beta),
    R_k = F~_k - F~_{k-1},

and four nested candidates restrict it: MARKET_NULL (w=0, alpha=beta=1),
ARITHMETIC (alpha=beta=1), PRICE_ONLY (w=0) and BLP (all free). The market is
the null: a fitted weight near zero is a conclusion, not a failure.

Numerics: the pool is formed separately on the lower CDF and on the upper
survival function, each accumulated directly from masses, and a bin mass is
the difference of whichever transformed tail keeps both terms at most 1/2, so
a tail bin never cancels two numbers near one. Outputs are clipped at zero and
renormalized, so every served vector is a simplex. A NO leg complements the
prediction and the label together (``held_side``).

The fitter pools (logit w, log alpha, log beta) through one shared design:
intercept, metric, Day0 lane, metric x lane, metric-specific slopes in
log(1 + hours to local-day end), resolution class, and zero-mean Gaussian
station and era effects whose scales are re-estimated from the training fit
(one empirical-Bayes step). Fitting minimizes city-day-weighted terminal
categorical log loss (or RPS as the challenger) plus the Gaussian log prior.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.special import betainc, betaln, expit

MARKET_NULL = "market_null"
ARITHMETIC = "arithmetic"
PRICE_ONLY = "price_only"
BLP = "blp"
# Nested candidates in predeclared simplicity order (simplest first).
CANDIDATES = (MARKET_NULL, ARITHMETIC, PRICE_ONLY, BLP)
FREE_PARAMETERS = {
    MARKET_NULL: (),
    ARITHMETIC: ("w",),
    PRICE_ONLY: ("a", "b"),
    BLP: ("w", "a", "b"),
}
LOG_LOSS = "log_loss"
RPS = "rps"
OBJECTIVES = (LOG_LOSS, RPS)

LOG_EPS = 1e-12
DAY0 = "DAY0"
FORECAST = "FORECAST"
LEAD_CENTER = math.log1p(24.0)
GROUPS = ("station", "era")
_DIFF_STEP = 1e-4  # central-difference step in log alpha / log beta
_ETA_BOUND = 8.0  # link-scale predictors are clipped to +-8
_MASS_TOL = 1e-6
# Link-scale Gaussian prior (mean, sd) on each intercept; the weight leans to
# the market. Fixed effects get sd 1; random effects get their group's tau.
_INTERCEPT_PRIOR = {"w": (-2.0, 2.0), "a": (0.0, 1.0), "b": (0.0, 1.0)}
_FIXED_EFFECT_SD = 1.0
DEFAULT_TAU = 0.3
# Inverse-gamma(shape, scale) hyperprior on tau^2 for the empirical-Bayes step.
_TAU_PRIOR = (1.0, 0.02)


class FamilyMassError(ValueError):
    """A probability vector is not a finite, non-negative, normalized family."""


def as_family_masses(m) -> np.ndarray:
    """Validate an (n, K) mass matrix and renormalize each row exactly."""

    m = np.asarray(m, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2:
        raise FamilyMassError(f"family masses need shape (n, K>=2), got {m.shape}")
    if not np.all(np.isfinite(m)) or np.any(m < 0.0):
        raise FamilyMassError("family masses must be finite and non-negative")
    total = m.sum(axis=1, keepdims=True)
    if np.any(np.abs(total - 1.0) > _MASS_TOL):
        raise FamilyMassError("family masses must sum to 1")
    return m / total


def _tails(m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(mass at or below threshold k, mass strictly above it), k = 0..K-2."""

    lower = np.cumsum(m[:, :-1], axis=1)
    upper = np.cumsum(m[:, :0:-1], axis=1)[:, ::-1]
    return np.clip(lower, 0.0, 1.0), np.clip(upper, 0.0, 1.0)


def _stable_bin_mass(lo_f, hi_f, lo_s, hi_s):
    """Bin mass from transformed lower CDF f and upper survival s at its two
    thresholds, differencing whichever tail keeps both terms <= 1/2."""

    return np.where(
        hi_f <= 0.5,
        hi_f - lo_f,
        np.where(lo_s <= 0.5, lo_s - hi_s, 1.0 - lo_f - hi_s),
    )


def family_masses(Q, P, w, a, b) -> np.ndarray:
    """Served family vector R for per-row parameters (w, alpha, beta).

    ``w``, ``a`` and ``b`` broadcast over rows; ``Q`` is read only where w > 0.
    Always returns a simplex.
    """

    P = as_family_masses(P)
    n = P.shape[0]
    w = np.broadcast_to(np.asarray(w, dtype=float), (n,))
    a = np.broadcast_to(np.asarray(a, dtype=float), (n,))
    b = np.broadcast_to(np.asarray(b, dtype=float), (n,))
    if np.any((w < 0.0) | (w > 1.0)) or np.any(a <= 0.0) or np.any(b <= 0.0):
        raise ValueError("need 0 <= w <= 1 and alpha, beta > 0")
    Q = as_family_masses(Q) if np.any(w > 0.0) else P
    identity = (a == 1.0) & (b == 1.0)
    R = np.empty_like(P)
    wi = w[identity, None]
    R[identity] = wi * Q[identity] + (1.0 - wi) * P[identity]
    rest = ~identity
    if np.any(rest):
        ql, qu = _tails(Q[rest])
        pl, pu = _tails(P[rest])
        wr, ar, br = w[rest, None], a[rest, None], b[rest, None]
        f = betainc(ar, br, wr * ql + (1.0 - wr) * pl)
        s = betainc(br, ar, wr * qu + (1.0 - wr) * pu)
        m = f.shape[0]
        f = np.hstack([np.zeros((m, 1)), f, np.ones((m, 1))])
        s = np.hstack([np.ones((m, 1)), s, np.zeros((m, 1))])
        R[rest] = _stable_bin_mass(f[:, :-1], f[:, 1:], s[:, :-1], s[:, 1:])
    R = np.clip(R, 0.0, None)
    return R / R.sum(axis=1, keepdims=True)


# ---------------------------------------------------------------------------
# Scores. R is an (n, K) simplex, y the 0-based settled bin per row.
# ---------------------------------------------------------------------------


def categorical_log_loss(R: np.ndarray, y: np.ndarray) -> np.ndarray:
    return -np.log(np.maximum(R[np.arange(len(y)), y], LOG_EPS))


def ranked_probability_score(R: np.ndarray, y: np.ndarray) -> np.ndarray:
    cdf = np.cumsum(R[:, :-1], axis=1)
    step = (np.arange(R.shape[1] - 1)[None, :] >= y[:, None]).astype(float)
    return ((cdf - step) ** 2).sum(axis=1)


def full_simplex_brier(R: np.ndarray, y: np.ndarray) -> np.ndarray:
    onehot = np.zeros_like(R)
    onehot[np.arange(len(y)), y] = 1.0
    return ((R - onehot) ** 2).sum(axis=1)


def prediction_set_miss(R: np.ndarray, y: np.ndarray, mass: float = 0.9) -> np.ndarray:
    """1 where the settled bin lies outside the smallest highest-mass set
    holding ``mass``; ties rank the lower bin index first."""

    n, k = R.shape
    order = np.lexsort((np.broadcast_to(np.arange(k), (n, k)), -R), axis=1)
    cum = np.cumsum(np.take_along_axis(R, order, axis=1), axis=1)
    size = np.argmax(cum >= mass - 1e-12, axis=1) + 1
    rank = np.argmax(order == y[:, None], axis=1)
    return (rank >= size).astype(float)


def binary_log_loss(r, y) -> np.ndarray:
    r = np.clip(np.asarray(r, dtype=float), LOG_EPS, 1.0 - LOG_EPS)
    y = np.asarray(y, dtype=float)
    return -(y * np.log(r) + (1.0 - y) * np.log1p(-r))


def held_side(r_yes, y_yes, side) -> tuple[np.ndarray, np.ndarray]:
    """Held-side probability and label: a NO leg complements both together."""

    no = np.asarray(side) == "NO"
    r_yes = np.asarray(r_yes, dtype=float)
    y_yes = np.asarray(y_yes, dtype=float)
    return np.where(no, 1.0 - r_yes, r_yes), np.where(no, 1.0 - y_yes, y_yes)


def loss_slope_at_market(Q, P, y) -> np.ndarray:
    """Per-row d/dw of categorical log loss at w = 0: -(Q_Y - P_Y) / P_Y."""

    rows = np.arange(len(y))
    qy = as_family_masses(Q)[rows, y]
    py = as_family_masses(P)[rows, y]
    return -(qy - py) / np.maximum(py, LOG_EPS)


def fit_arithmetic_weight(Q, P, y, weights) -> float:
    """Unpooled arithmetic weight on [0, 1]. Log loss is convex in w, so the
    weight is exactly 0 when the slope at the market is non-negative."""

    rows = np.arange(len(y))
    qy = as_family_masses(Q)[rows, y]
    py = as_family_masses(P)[rows, y]
    s = np.asarray(weights, dtype=float)
    if float(s @ (-(qy - py) / np.maximum(py, LOG_EPS))) >= 0.0:
        return 0.0
    if float(s @ (-(qy - py) / np.maximum(qy, LOG_EPS))) <= 0.0:
        return 1.0
    return float(minimize_scalar(
        lambda w: float(s @ -np.log(np.maximum(w * qy + (1.0 - w) * py, LOG_EPS))),
        bounds=(0.0, 1.0), method="bounded", options={"xatol": 1e-7},
    ).x)


# ---------------------------------------------------------------------------
# Hierarchical design.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FamilyFeatures:
    metric: np.ndarray
    lane: np.ndarray
    hours_to_end: np.ndarray
    res_class: np.ndarray
    station: np.ndarray
    era: np.ndarray

    def take(self, idx) -> "FamilyFeatures":
        return FamilyFeatures(*(getattr(self, f)[idx] for f in self.__dataclass_fields__))


_FIXED_COLUMNS = ("intercept", "low", "day0", "low_x_day0", "lead_high", "lead_low")


@dataclass(frozen=True)
class DesignSpec:
    """Factor levels frozen from training; unseen levels get a zero effect."""

    res_levels: tuple[str, ...]
    station_levels: tuple[str, ...]
    era_levels: tuple[str, ...]

    @classmethod
    def from_features(cls, f: FamilyFeatures) -> "DesignSpec":
        res = sorted(set(f.res_class.tolist()))
        res.sort(key=lambda r: -int(np.sum(f.res_class == r)))  # reference = most common
        return cls(tuple(res), tuple(sorted(set(f.station.tolist()))),
                   tuple(sorted(set(f.era.tolist()))))

    @property
    def n_fixed(self) -> int:
        return len(_FIXED_COLUMNS) + len(self.res_levels) - 1

    def group_slice(self, group: str) -> slice:
        start = self.n_fixed + (0 if group == "station" else len(self.station_levels))
        size = len(self.station_levels if group == "station" else self.era_levels)
        return slice(start, start + size)

    def matrix(self, f: FamilyFeatures) -> np.ndarray:
        low = (f.metric == "low").astype(float)
        day0 = (f.lane == DAY0).astype(float)
        lead = np.log1p(np.maximum(np.asarray(f.hours_to_end, dtype=float), 0.0)) - LEAD_CENTER
        cols = [np.ones_like(low), low, day0, low * day0, (1.0 - low) * lead, low * lead]
        cols += [(f.res_class == r).astype(float) for r in self.res_levels[1:]]
        cols += [(f.station == s).astype(float) for s in self.station_levels]
        cols += [(f.era == e).astype(float) for e in self.era_levels]
        return np.column_stack(cols)

    def prior(self, param: str, tau: Mapping[tuple[str, str], float]):
        size = self.n_fixed + len(self.station_levels) + len(self.era_levels)
        mean, sd = np.zeros(size), np.full(size, _FIXED_EFFECT_SD)
        mean[0], sd[0] = _INTERCEPT_PRIOR[param]
        for group in GROUPS:
            sd[self.group_slice(group)] = tau[(param, group)]
        return mean, sd


def default_tau() -> dict[tuple[str, str], float]:
    return {(p, g): DEFAULT_TAU for p in ("w", "a", "b") for g in GROUPS}


# ---------------------------------------------------------------------------
# Fitting.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LabelView:
    """Settled-bin masses plus both tails at the two thresholds bracketing it."""

    qy: np.ndarray
    py: np.ndarray
    q: tuple[np.ndarray, ...]  # (lower lo, lower hi, upper lo, upper hi)
    p: tuple[np.ndarray, ...]

    @classmethod
    def build(cls, Q, P, y) -> "_LabelView":
        n = len(y)
        rows = np.arange(n)

        def gather(m):
            lower, upper = _tails(m)
            lo = np.hstack([np.zeros((n, 1)), lower, np.ones((n, 1))])
            up = np.hstack([np.ones((n, 1)), upper, np.zeros((n, 1))])
            return lo[rows, y], lo[rows, y + 1], up[rows, y], up[rows, y + 1]

        return cls(Q[rows, y], P[rows, y], gather(Q), gather(P))


def _pdf(x, a, b):
    inside = (x > 0.0) & (x < 1.0)
    xs = np.where(inside, x, 0.5)
    val = np.exp((a - 1.0) * np.log(xs) + (b - 1.0) * np.log1p(-xs) - betaln(a, b))
    return np.where(inside, val, 0.0)


def _term(x, dxdw, a, b):
    """I_x(a, b) with d/dw (analytic) and d/dlog a, d/dlog b (central)."""

    eh, emh = math.exp(_DIFF_STEP), math.exp(-_DIFF_STEP)
    return (
        betainc(a, b, x),
        _pdf(x, a, b) * dxdw,
        (betainc(a * eh, b, x) - betainc(a * emh, b, x)) / (2.0 * _DIFF_STEP),
        (betainc(a, b * eh, x) - betainc(a, b * emh, x)) / (2.0 * _DIFF_STEP),
    )


def _label_mass_and_grad(v: _LabelView, w, a, b, need_ab):
    if not need_ab:
        return w * v.qy + (1.0 - w) * v.py, (v.qy - v.py, 0.0, 0.0)
    h = [w * qi + (1.0 - w) * pi for qi, pi in zip(v.q, v.p)]
    dh = [qi - pi for qi, pi in zip(v.q, v.p)]
    f_lo = _term(h[0], dh[0], a, b)
    f_hi = _term(h[1], dh[1], a, b)
    # Survival I_h(b, a): its first shape slot is beta, so swap the two slots.
    s_lo = _term(h[2], dh[2], b, a)
    s_hi = _term(h[3], dh[3], b, a)
    s_lo = (s_lo[0], s_lo[1], s_lo[3], s_lo[2])
    s_hi = (s_hi[0], s_hi[1], s_hi[3], s_hi[2])
    lower = f_hi[0] <= 0.5
    upper = ~lower & (s_lo[0] <= 0.5)
    r = _stable_bin_mass(f_lo[0], f_hi[0], s_lo[0], s_hi[0])
    grads = tuple(
        np.where(lower, f_hi[i] - f_lo[i],
                 np.where(upper, s_lo[i] - s_hi[i], -f_lo[i] - s_hi[i]))
        for i in (1, 2, 3)
    )
    return r, grads


def _rps_and_grad(ql, pl, y, w, a, b, need_ab):
    w2 = w[:, None]
    h = w2 * ql + (1.0 - w2) * pl
    if need_ab:
        f, dw, da, db = _term(h, ql - pl, a[:, None], b[:, None])
    else:
        f, dw, da, db = h, ql - pl, 0.0, 0.0
    step = (np.arange(ql.shape[1])[None, :] >= y[:, None]).astype(float)
    resid = f - step
    loss = (resid ** 2).sum(axis=1)
    grads = tuple(2.0 * (resid * d).sum(axis=1) if np.ndim(d) else 0.0 for d in (dw, da, db))
    return loss, grads


@dataclass(frozen=True)
class FamilyFit:
    candidate: str
    objective: str
    design: DesignSpec | None
    theta: Mapping[str, np.ndarray]
    tau: Mapping[tuple[str, str], float] = field(default_factory=default_tau)
    value: float = 0.0
    converged: bool = True
    n_iter: int = 0

    def parameters(self, f: FamilyFeatures):
        n = len(f.metric)
        w, a, b = np.zeros(n), np.ones(n), np.ones(n)
        if self.theta:
            X = self.design.matrix(f)
            if "w" in self.theta:
                w = expit(np.clip(X @ self.theta["w"], -_ETA_BOUND, _ETA_BOUND))
            if "a" in self.theta:
                a = np.exp(np.clip(X @ self.theta["a"], -_ETA_BOUND, _ETA_BOUND))
                b = np.exp(np.clip(X @ self.theta["b"], -_ETA_BOUND, _ETA_BOUND))
        return w, a, b

    def predict(self, Q, P, f: FamilyFeatures) -> np.ndarray:
        if self.candidate == MARKET_NULL:
            return as_family_masses(P)
        return family_masses(Q, P, *self.parameters(f))


def _map_fit(candidate, objective, Q, P, y, s, X, design, tau, theta0, maxiter):
    names = FREE_PARAMETERS[candidate]
    need_ab = "a" in names
    p = X.shape[1]
    priors = {k: design.prior(k, tau) for k in names}
    if objective == LOG_LOSS:
        view = _LabelView.build(Q, P, y)
    else:
        ql, pl = _tails(Q)[0], _tails(P)[0]

    def unpack(theta):
        return {k: theta[i * p:(i + 1) * p] for i, k in enumerate(names)}

    def value_and_grad(theta):
        th = unpack(theta)
        n = X.shape[0]
        w = expit(np.clip(X @ th["w"], -_ETA_BOUND, _ETA_BOUND)) if "w" in th else np.zeros(n)
        a = np.exp(np.clip(X @ th["a"], -_ETA_BOUND, _ETA_BOUND)) if need_ab else np.ones(n)
        b = np.exp(np.clip(X @ th["b"], -_ETA_BOUND, _ETA_BOUND)) if need_ab else np.ones(n)
        if objective == LOG_LOSS:
            r, (gw, ga, gb) = _label_mass_and_grad(view, w, a, b, need_ab)
            rr = np.maximum(r, LOG_EPS)
            loss = -np.log(rr)
            inv = np.where(r > LOG_EPS, -1.0 / rr, 0.0)
            dl = {"w": inv * gw, "a": inv * ga, "b": inv * gb}
        else:
            loss, (gw, ga, gb) = _rps_and_grad(ql, pl, y, w, a, b, need_ab)
            dl = {"w": gw, "a": ga, "b": gb}
        value = float(s @ loss)
        grad = []
        for k in names:
            g = dl[k] * w * (1.0 - w) if k == "w" else dl[k]
            mean, sd = priors[k]
            dev = (th[k] - mean) / sd
            value += 0.5 * float(dev @ dev)
            grad.append(X.T @ (s * g) + dev / sd)
        return value, np.concatenate(grad)

    if theta0 is None:
        theta0 = np.concatenate([priors[k][0] for k in names])
    res = minimize(value_and_grad, theta0, jac=True, method="L-BFGS-B",
                   options={"maxiter": maxiter, "gtol": 1e-6})
    return unpack(res.x), float(res.fun), bool(res.success), int(res.nit)


def fit_family(
    candidate: str,
    Q,
    P,
    y,
    weights,
    features: FamilyFeatures,
    *,
    objective: str = LOG_LOSS,
    design: DesignSpec | None = None,
    tau: Mapping[tuple[str, str], float] | None = None,
    eb_steps: int = 1,
    init: FamilyFit | None = None,
    maxiter: int = 500,
) -> FamilyFit:
    """MAP fit of one nested candidate under city-day weights.

    With ``eb_steps`` > 0 the station and era scales are re-estimated from the
    fitted effects (posterior mode under an inverse-gamma hyperprior) and the
    model is refitted warm, so pooling strength comes from training data.
    """

    if candidate not in CANDIDATES or objective not in OBJECTIVES:
        raise ValueError(f"unknown candidate/objective {candidate!r}/{objective!r}")
    names = FREE_PARAMETERS[candidate]
    tau = dict(tau or default_tau())
    P = as_family_masses(P)
    Q = as_family_masses(Q) if "w" in names else P
    y = np.asarray(y, dtype=int)
    s = np.asarray(weights, dtype=float)
    if not names:
        R = P
        loss = categorical_log_loss(R, y) if objective == LOG_LOSS else ranked_probability_score(R, y)
        return FamilyFit(candidate, objective, None, {}, tau, float(s @ loss))
    design = design or DesignSpec.from_features(features)
    X = design.matrix(features)
    theta0 = None
    if init is not None and init.design == design and tuple(init.theta) == names:
        theta0 = np.concatenate([init.theta[k] for k in names])
    theta, value, ok, nit = _map_fit(candidate, objective, Q, P, y, s, X, design, tau,
                                     theta0, maxiter)
    for _ in range(eb_steps):
        shape, scale = _TAU_PRIOR
        for k in names:
            for g in GROUPS:
                u = theta[k][design.group_slice(g)]
                tau[(k, g)] = math.sqrt((scale + 0.5 * float(u @ u)) / (shape + 1.0 + 0.5 * len(u)))
        theta0 = np.concatenate([theta[k] for k in names])
        theta, value, ok, nit2 = _map_fit(candidate, objective, Q, P, y, s, X, design, tau,
                                          theta0, maxiter)
        nit += nit2
    return FamilyFit(candidate, objective, design, theta, tau, value, ok, nit)


def weighted_mean(x, s) -> float:
    total = float(np.sum(s))
    return float(np.sum(np.asarray(s) * np.asarray(x)) / total) if total > 0.0 else float("nan")
