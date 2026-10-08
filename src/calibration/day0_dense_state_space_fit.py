# Created: 2026-10-07
# Last reused or audited: 2026-10-07
# Authority basis: artifacts/fast_obs_audit/dense_station_model/METHOD.md §5.3 (estimation on
#   training days only; walk-forward model choice); ported from state_space.py / run_state_space.py
#   at bc024fedd so a refit at that cutoff reproduces the backtest parameters.
"""Walk-forward estimation of the Day0 dense state-space parameters (pure; no IO).

Per city, from training days only (each day: forecast path on the 5-min grid, METAR integers,
dense readings):
  - dense noise: b by 3-h local block, core sd s1, outlier sd s2, outlier weight pi by the
    quantised interval-censored likelihood of same-instant pairs; the mixture is kept only if it
    wins AIC by more than 2;
  - dense-error drift: latent correlation of the dense-vs-METAR error at 1..4 settlement cadences
    (pairwise interval likelihood), fitted to a exp(-L / tau_e); variance conserved;
  - mean: mu(h) by local hour plus beta (f - S_4h f), least squares of M - f at METAR instants;
  - latent: OU autocovariance of the dense residual (lags 10-180 min one scale; 10-720 two scales),
    lag 0 never used.
Model order ({one, two scales} x {plain, shrunk mean}) is chosen by the caller's walk-forward
log-loss inside the training days.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy import optimize, special

from src.data.day0_dense_state_space import GRID_MIN, PRE_MIN, smooth_4h

TAU_F_MIN = 10.0
TAU_E_MAX = 1.0e4
_GLX, _GLW = np.polynomial.legendre.leggauss(24)


@dataclass(frozen=True)
class TrainingDay:
    """One local day on the 5-min grid; times are minutes from local midnight."""

    date: str
    day_minutes: float
    forecast: np.ndarray        # on grid -PRE_MIN .. day_minutes
    hour: np.ndarray            # local clock hour on the grid
    metar_t: np.ndarray         # METAR / SPECI instants
    metar_k: np.ndarray         # integers
    metar_routine: np.ndarray   # bool
    dense_t: np.ndarray
    dense_x: np.ndarray
    dense_hour: np.ndarray


# ---------------------------------------------------------------- likelihood primitives

def log_interval_prob(lo, hi):
    """log P(lo <= Z < hi) for a standard normal Z, stable in both tails."""
    lo, hi = np.broadcast_arrays(np.asarray(lo, float), np.asarray(hi, float))
    flip = lo > 0
    a, c = np.where(flip, -hi, lo), np.where(flip, -lo, hi)
    la, lc = special.log_ndtr(a), special.log_ndtr(c)
    return lc + np.log(-np.expm1(np.minimum(la - lc, -1e-300)))


def _psi(z):
    return z * special.ndtr(z) + np.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def uq_prob(k, x, sd, q):
    """P(k - 1/2 <= x + U + E < k + 1/2), U ~ Unif[-q/2, q/2), E ~ N(0, sd^2)."""
    h = q / 2
    hi, lo = k + 0.5 - x, k - 0.5 - x
    v = (sd / q) * (_psi((hi + h) / sd) - _psi((hi - h) / sd) - _psi((lo + h) / sd) + _psi((lo - h) / sd))
    return np.clip(v, 1e-300, 1.0)


def bvn_cdf(h, k, rho):
    """Phi2(h, k; rho) by 24-node Gauss-Legendre integration in the correlation."""
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
    """Latent correlation of consecutive interval-censored pairs (standardised limits)."""
    l1, u1, l2, u2 = (np.asarray(a, float) for a in (l1, u1, l2, u2))

    def nll(r):
        P = bvn_cdf(u1, u2, r) - bvn_cdf(l1, u2, r) - bvn_cdf(u1, l2, r) + bvn_cdf(l1, l2, r)
        return -float(np.sum(np.log(np.clip(P, 1e-300, None))))

    res = optimize.minimize_scalar(nll, bounds=(-bound, bound), method="bounded", options=dict(xatol=1e-5))
    return float(res.x)


# ---------------------------------------------------------------- dense noise

def fit_dense_noise(M, x, hour, quantum=0.1, mixture=True, s_floor=0.005):
    """ML of b by 3-h block, s1, s2 >= s1 and pi on same-instant pairs (quantised likelihood)."""
    M, x, blk = np.asarray(M, float), np.asarray(x, float), np.asarray(hour, int) // 3

    def unpack(th):
        b = th[:8]
        s1 = s_floor + math.exp(min(max(th[8], -12.0), 3.0))
        if not mixture:
            return b, s1, s1, 0.0
        return b, s1, s1 + math.exp(min(max(th[9], -12.0), 3.0)), 1 / (1 + math.exp(-min(max(th[10], -30.0), 30.0)))

    def nll(th):
        b, s1, s2, pi = unpack(th)
        xb = x + b[blk]
        p = (1 - pi) * uq_prob(M, xb, s1, quantum)
        if pi > 0:
            p = p + pi * uq_prob(M, xb, s2, quantum)
        return -float(np.sum(np.log(p)))

    b0 = np.array([float(np.mean((M - x)[blk == j])) if np.any(blk == j) else 0.0 for j in range(8)])
    starts = ([np.r_[b0, math.log(s)] for s in (0.05, 0.2, 0.5, 1.0)] if not mixture else
              [np.r_[b0, math.log(s1), math.log(ds), lg] for s1 in (0.05, 0.15, 0.3)
               for ds in (0.2, 0.6) for lg in (-2.5, -1.0)])
    best = None
    for th0 in starts:
        r = optimize.minimize(nll, th0, method="L-BFGS-B")
        if best is None or r.fun < best.fun:
            best = r
    r = optimize.minimize(nll, best.x, method="Nelder-Mead", options=dict(maxiter=20000, xatol=1e-6, fatol=1e-8))
    b, s1, s2, pi = unpack(r.x)
    npar = 9 + (2 if mixture else 0)
    return dict(b_block=[float(v) for v in b], s1=s1, s2=s2, pi=pi, nll=float(r.fun),
                aic=2 * float(r.fun) + 2 * npar, n=int(M.size), mixture=mixture)


def fit_error_drift(lags, rhos, sigma2):
    """rho(L) = a exp(-L / tau_e)  ->  drift variance a sigma2, white (1 - a) sigma2."""
    L, r = np.asarray(lags, float), np.clip(np.asarray(rhos, float), -0.99, 0.99)
    ok = np.isfinite(r)
    if ok.sum() == 0 or np.all(r[ok] <= 0.02):
        return dict(a=0.0, tau_e=1.0, sd2=0.0)

    def unpack(th):
        # Bounded exponents: a sign-alternating correlation (an identity channel) sends the
        # optimiser to a degenerate edge; the bounds keep that edge finite and inert.
        return float(special.expit(th[0])), math.exp(min(max(th[1], -20.0), 20.0))

    def loss(th):
        a, tau = unpack(th)
        return float(np.sum((r[ok] - a * np.exp(-L[ok] / tau)) ** 2))

    res = min((optimize.minimize(loss, [0.0, math.log(t0)], method="Nelder-Mead") for t0 in (15.0, 60.0, 240.0)),
              key=lambda x: x.fun)
    a, tau = unpack(res.x)
    tau = min(tau, TAU_E_MAX)
    return dict(a=a, tau_e=tau, sd2=a * sigma2)


# ---------------------------------------------------------------- latent OU

def acov(segments, step_min, lags_min):
    C, N = [], []
    for L in lags_min:
        h = int(round(L / step_min))
        num, n = 0.0, 0
        for u in segments:
            if u.size > h:
                a, b = (u[:-h], u[h:]) if h else (u, u)
                ok = np.isfinite(a) & np.isfinite(b)
                num += float(np.sum(a[ok] * b[ok]))
                n += int(ok.sum())
        C.append(num / n if n else np.nan)
        N.append(n)
    return np.asarray(C), np.asarray(N, float)


def fit_ou_acf(segments, step_min, lags_min, n_scales=1, subtract=None):
    """OU fit by n-weighted least squares on the empirical autocovariance (lag 0 excluded)."""
    lags = [L for L in lags_min if abs(L / step_min - round(L / step_min)) < 1e-9]
    C, N = acov(segments, step_min, lags)
    Lg = np.asarray(lags, float)
    if subtract is not None:
        C = C - np.array([subtract(L) for L in Lg])
    ok = np.isfinite(C) & (N > 0)

    def ex(v: float) -> float:
        # Bounded exponent: a slow scale running to infinity (a constant day offset) stays finite.
        return math.exp(min(max(float(v), -40.0), 40.0))

    def model(th):
        if n_scales == 1:
            return ex(th[0]) * np.exp(-Lg / ex(th[1]))
        tf = TAU_F_MIN + ex(th[1])
        ts = tf + ex(th[3])
        return ex(th[0]) * np.exp(-Lg / tf) + ex(th[2]) * np.exp(-Lg / ts)

    def loss(th):
        return float(np.sum(N[ok] * (C[ok] - model(th)) ** 2))

    c1 = max(float(C[ok][0]), 1e-4)
    starts = ([[math.log(c1), math.log(t)] for t in (30.0, 120.0, 480.0, 2000.0)] if n_scales == 1 else
              [[math.log(c1 * fr), math.log(tf), math.log(c1 * (1 - fr)), math.log(ts)]
               for fr in (0.2, 0.5) for tf in (10.0, 60.0) for ts in (300.0, 1500.0)])
    best = min((optimize.minimize(loss, s0, method="Nelder-Mead",
                                  options=dict(xatol=1e-7, fatol=1e-14, maxiter=20000)) for s0 in starts),
               key=lambda r: r.fun)
    th = best.x
    if n_scales == 1:
        return dict(tau_s=ex(th[1]), s2_s=ex(th[0]), tau_f=30.0, s2_f=0.0, n_scales=1)
    tf = TAU_F_MIN + ex(th[1])
    return dict(tau_f=tf, s2_f=ex(th[0]), tau_s=tf + ex(th[3]), s2_s=ex(th[2]), n_scales=2)


def latent_for_operator(ou: dict) -> dict:
    """Map a fitted OU to the operator's (tau, s2, s2_static).

    One scale: (tau_s, s2_s).  Two scales: the fast component is the OU; the slow one enters as a
    static day offset when its time scale exceeds four days (``tau_slow -> inf``, Tokyo).  A finite
    slow scale is not representable and is reported by the caller as not selectable."""
    if ou["n_scales"] == 1 or ou["s2_f"] <= 0:
        return dict(tau=float(ou["tau_s"]), s2=float(ou["s2_s"]), s2_static=0.0)
    if ou["tau_s"] < 4 * 1440:
        raise ValueError("DAY0_DENSE_FINITE_SLOW_SCALE_UNSUPPORTED")
    return dict(tau=float(ou["tau_f"]), s2=float(ou["s2_f"]), s2_static=float(ou["s2_s"]))


# ---------------------------------------------------------------- mean

def _grid_index(day: TrainingDay, t) -> np.ndarray:
    return np.clip(np.rint((np.asarray(t, float) + PRE_MIN) / GRID_MIN).astype(int), 0, day.forecast.size - 1)


def fit_mean(days: Sequence[TrainingDay], shrink: bool) -> dict:
    """mu(h) + beta (f - S_4h f) by least squares of M - f at in-day METAR instants."""
    Xs, ys = [], []
    for d in days:
        ind = (d.metar_t >= 0) & (d.metar_t < d.day_minutes)
        gi = _grid_index(d, d.metar_t[ind])
        X = np.zeros((gi.size, 25))
        X[np.arange(gi.size), d.hour[gi]] = 1
        X[:, 24] = (d.forecast - smooth_4h(d.forecast))[gi]
        Xs.append(X)
        ys.append(d.metar_k[ind] - d.forecast[gi])
    X, y = np.vstack(Xs), np.concatenate(ys)
    X = X if shrink else X[:, :24]
    coef = np.linalg.lstsq(X, y, rcond=None)[0]
    return dict(mu_hour=[float(v) for v in coef[:24]], beta=float(coef[24]) if shrink else 0.0)


def mean_path_for(day: TrainingDay, mean: dict) -> np.ndarray:
    f = day.forecast
    return f + np.asarray(mean["mu_hour"])[day.hour] + mean["beta"] * (f - smooth_4h(f))


# ---------------------------------------------------------------- estimation driver

def same_instant_pairs(days: Sequence[TrainingDay]):
    M, x, h = [], [], []
    for d in days:
        dmap = dict(zip(np.round(d.dense_t, 3), zip(d.dense_x, d.dense_hour)))
        for t, k in zip(d.metar_t, d.metar_k):
            if 0 <= t < d.day_minutes and round(float(t), 3) in dmap:
                v, hh = dmap[round(float(t), 3)]
                M.append(k)
                x.append(v)
                h.append(hh)
    return np.array(M, float), np.array(x, float), np.array(h, int)


def _drift_correlations(days, b_block, sigma, cad):
    lags, rhos = [], []
    for mult in (1, 2, 3, 4):
        L = cad * mult
        l1, u1, l2, u2 = [], [], [], []
        for d in days:
            dmap = dict(zip(np.round(d.dense_t, 3), zip(d.dense_x, d.dense_hour)))
            obs = {}
            for t, k, rt in zip(d.metar_t, d.metar_k, d.metar_routine):
                if rt and 0 <= t < d.day_minutes and round(float(t), 3) in dmap:
                    v, hh = dmap[round(float(t), 3)]
                    xb = v + b_block[hh // 3]
                    obs[round(float(t), 3)] = ((k - 0.5 - xb) / sigma, (k + 0.5 - xb) / sigma)
            for t, (lo, hi) in obs.items():
                o2 = obs.get(round(t + L, 3))
                if o2:
                    l1.append(lo), u1.append(hi), l2.append(o2[0]), u2.append(o2[1])
        if len(l1) > 50:
            lags.append(L)
            rhos.append(fit_rho(np.array(l1), np.array(u1), np.array(l2), np.array(u2)))
    return lags, rhos


def residual_segments(days, b_block, mean, step):
    segs = []
    for d in days:
        inday = (d.dense_t >= 0) & (d.dense_t < d.day_minutes)
        t, x, hh = d.dense_t[inday], d.dense_x[inday], d.dense_hour[inday]
        res = x + np.asarray(b_block)[hh // 3] - mean_path_for(d, mean)[_grid_index(d, t)]
        grid = np.arange(0, d.day_minutes, step)
        seg = np.full(grid.size, np.nan)
        pos = np.rint(t / step).astype(int)
        okp = (pos >= 0) & (pos < grid.size) & np.isclose(t, pos * step)
        seg[pos[okp]] = res[okp]
        segs.append(seg)
    return segs


def estimate(days: Sequence[TrainingDay], *, step: float, cadence: float, shrink: bool) -> dict:
    """All parameters of one model order from training days (backtest ``estimate``)."""
    M, x, h = same_instant_pairs(days)
    g = fit_dense_noise(M, x, h, mixture=False)
    mx = fit_dense_noise(M, x, h, mixture=True)
    noise_fit = mx if mx["aic"] < g["aic"] - 2 else g
    b_block = noise_fit["b_block"]
    lags, rhos = _drift_correlations(days, b_block, max(g["s1"], 0.02), cadence)
    core2 = noise_fit["s1"] ** 2
    drift = fit_error_drift(lags, rhos, core2) if lags else dict(a=0.0, tau_e=1.0, sd2=0.0)
    sd2 = drift["sd2"]
    s1w = math.sqrt(max(core2 - sd2, 0.02 ** 2))
    s2w = math.sqrt(max(noise_fit["s2"] ** 2 - sd2, s1w ** 2))
    mean = fit_mean(days, shrink)
    segs = residual_segments(days, b_block, mean, step)
    sub = (lambda L: sd2 * math.exp(-L / drift["tau_e"])) if sd2 > 0 else None
    one = fit_ou_acf(segs, step, [L for L in range(10, 190, 10) if L % step == 0], 1, sub)
    two = fit_ou_acf(segs, step, [L for L in range(10, 730, 10) if L % step == 0], 2, sub)
    return dict(noise=dict(b_hour=[float(b_block[hh // 3]) for hh in range(24)], s1=s1w, s2=s2w,
                           pi=float(noise_fit["pi"]), tau_e=max(float(drift["tau_e"]), 1.0), sd2=float(sd2)),
                noise_choice="mixture" if noise_fit is mx else "gauss", mean=mean, ou_one=one, ou_two=two,
                n_pairs=int(M.size), drift_lags=lags, drift_rhos=[float(v) for v in rhos])


# ---------------------------------------------------------------- report lifecycle (D2)

LAG_BINS_MIN = (10.0, 20.0, 40.0, 60.0, 120.0, 180.0)
OUTAGE_MIN_STATIONS = 4
GROSS_DEVIATION = 2


def _jeffreys(successes: float, n: float) -> float:
    return (successes + 0.5) / (n + 1.0)


def fit_lifecycle(reports: Sequence[dict], fetch_checks: Sequence[tuple[float, bool]]) -> dict:
    """Fit the settlement-page lifecycle of received reports (pooled over stations).

    ``reports``: one dict per received provisional report on a finalized page-covered day, with
      station, utc_hour (ISO hour), speci (bool), outcome in {kept, corrected, absent},
      delta (page - report, corrected only), neighbour_gap (|k - kept page values within 60 min|
      at its smallest, absent only; None when there is no neighbour).
    ``fetch_checks``: (lag_minutes, visible) for every intraday page fetch whose returned span
      covers a finally kept instant.

    Shared outage: UTC hours where at least OUTAGE_MIN_STATIONS stations lose reports.  A report in
    such an hour is an outage removal (still valid temperature evidence); the outage prior is the
    Jeffreys share of reports in outage hours.  Outside outages an absent report is gross when its
    integer sits GROSS_DEVIATION or more from every kept page value within 60 min, else a valid
    removal.  A report with no kept neighbour carries no deviation evidence and counts as a valid
    removal.  (Measured 2026-10-08 over C stations: the kept rate is 0.984 / 0.987 / 0.985 for
    reports 0 / 1 / >= 2 from their previous-hour reports, so deviation does not mark removal.)
    Branch probabilities per report kind are smoothed shares (half a count per branch).  Visibility
    a(lag) is the Jeffreys posterior predictive per lag bin, made non-decreasing by pooling adjacent
    violators."""
    hours: dict[str, set] = {}
    for r in reports:
        if r["outcome"] == "absent":
            hours.setdefault(r["utc_hour"], set()).add(r["station"])
    outage_hours = {h for h, s in hours.items() if len(s) >= OUTAGE_MIN_STATIONS}
    counts = {k: dict(kept=0.0, corrected=0.0, removed=0.0, gross=0.0) for k in ("routine", "speci")}
    deltas: dict[int, float] = {}
    n_outage = 0
    for r in reports:
        if r["utc_hour"] in outage_hours:
            n_outage += 1
            continue
        kind = "speci" if r["speci"] else "routine"
        if r["outcome"] == "absent":
            gap = r.get("neighbour_gap")
            counts[kind]["gross" if gap is not None and gap >= GROSS_DEVIATION else "removed"] += 1
        else:
            counts[kind][r["outcome"]] += 1
            if r["outcome"] == "corrected":
                deltas[int(r["delta"])] = deltas.get(int(r["delta"]), 0.0) + 1
    branches: dict[str, dict[str, float]] = {b: {} for b in ("kept", "corrected", "removed", "gross")}
    for kind, c in counts.items():
        total = sum(c.values()) + 2.0
        for b in c:
            branches[b][kind] = (c[b] + 0.5) / total
    support = sorted(set(deltas) | {-1, 1})
    dw = {d: deltas.get(d, 0.0) + 0.5 for d in support}
    tot = sum(dw.values())
    delta = [[d, dw[d] / tot] for d in support]
    by_bin = {u: [0.0, 0.0] for u in LAG_BINS_MIN}
    for lag, visible in fetch_checks:
        for u in LAG_BINS_MIN:
            if lag <= u:
                by_bin[u][0] += float(visible)
                by_bin[u][1] += 1.0
                break
    merged: list = []
    for u in LAG_BINS_MIN:  # pool adjacent violators: a(lag) is non-decreasing
        merged.append([by_bin[u][0], by_bin[u][1], [u]])
        while len(merged) > 1 and _jeffreys(merged[-2][0], merged[-2][1]) > _jeffreys(merged[-1][0], merged[-1][1]):
            s2, n2, u2 = merged.pop()
            merged[-1][0] += s2
            merged[-1][1] += n2
            merged[-1][2] += u2
    visibility = [[u, _jeffreys(s, n)] for s, n, us in merged for u in us]
    return dict(**branches, outage_prior=_jeffreys(n_outage, len(reports)), delta=delta, visibility=visibility,
                counts=counts, n_reports=len(reports), n_outage_reports=n_outage, outage_hours=sorted(outage_hours),
                n_fetch_checks=len(fetch_checks), visibility_counts={str(u): by_bin[u] for u in LAG_BINS_MIN})
