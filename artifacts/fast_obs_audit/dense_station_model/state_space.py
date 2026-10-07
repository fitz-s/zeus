"""State-space model of the settled daily extreme with dense-station observations (pure math; no IO).

Latent (contract unit, deg C) on a 5-minute grid from local day start - 180 min to the local day end:
    T_t = f_t + mu(h_t) + a_t + c_t
      f      forecast path (hourly, linear interpolation);  mu(h) mean forecast residual by local hour (training days)
      a, c   independent OU components (slow: tau_s, s2_s; fast: tau_f, s2_f). One-scale model: s2_f = 0.
             Both are kept in the state: their sum is not Markov.
    d_t      OU drift of the dense-station error (tau_e, sd2); sd2 = 0 means white dense error only.
Observations (exact likelihoods)
    METAR / SPECI integer M at t:   M = R(T_t)  ->  T_t in [M - 0.5, M + 0.5)      (R = settlement half-up rounding)
    dense reading x (quantum q):    continuous reading x* = T_t - b(h_t) + d_t + e_t, e ~ (1-pi) N(0, s1^2) + pi N(0, s2^2)
                                    x = Q_q(x*)  ->  T_t + d_t + e_t in [x + b - q/2, x + b + q/2)
Settled extreme:  H = max(B, max over pending scheduled routine instants of R(T)),  L = min(B, min ...)
    B = extreme of the METAR / SPECI integers already received; pending = the routine clock schedule not yet received
    (never the realised future rows); future SPECIs are not modelled.

Engine 'smc' (exact likelihood, Monte Carlo posterior): fully adapted particle filter. At a step with observations the
per-particle predictive of each observed linear functional is Gaussian; the functional is drawn from its truncated
predictive (weight = the exact interval probability), the state is conditioned on it, mixture components are drawn
with their exact posterior odds. Received information is processed in time order; at decision time tau0 the cloud is
continued with the faster channel, recording R(T) at pending instants (path space), then OU paths are simulated to the
day end. Engines 'kf_tn' / 'kf_g12' (approximation arms): Gaussian assumed-density filter for the main pass (METAR
update = exact truncated-normal moment match, or Gaussian N(M, 1/12)), sampled into particles at the decision.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import optimize, special, stats

from dense_model import R, log_interval_prob

GRID_MIN = 5.0
PRE_MIN = 180.0
LL_FLOOR = 1e-4
TAU_F_MIN = 10.0     # fast OU component: below the dense step it is not identifiable from lags >= 10 min
TAU_E_MAX = 1.0e4    # dense-error drift: beyond a week it is a constant offset
HM = np.array([1.0, 1.0, 0.0])  # METAR observes a + c
HD = np.array([1.0, 1.0, 1.0])  # dense observes a + c + d (+ white)


@dataclass(frozen=True)
class Latent:
    tau_s: float
    s2_s: float
    tau_f: float = 30.0
    s2_f: float = 0.0
    tau_e: float = 30.0
    sd2: float = 0.0

    def phi(self, dt):
        return np.array([math.exp(-dt / self.tau_s), math.exp(-dt / self.tau_f), math.exp(-dt / self.tau_e)])

    def var(self):
        return np.array([self.s2_s, self.s2_f, self.sd2])

    def q(self, dt):
        p = self.phi(dt)
        return self.var() * (1 - p * p)

    def acov(self, L):
        return self.s2_s * np.exp(-np.asarray(L) / self.tau_s) + self.s2_f * np.exp(-np.asarray(L) / self.tau_f)


@dataclass(frozen=True)
class DenseNoise:
    b_hour: tuple          # 24 values, bias added to the reading, by local clock hour
    s1: float              # core white sd
    s2: float              # outlier sd (>= s1)
    pi: float              # outlier weight
    quantum: float = 0.1
    lag: float = 0.0       # receipt lag, minutes

    def total_var(self):
        return (1 - self.pi) * self.s1 ** 2 + self.pi * self.s2 ** 2


# ---------------------------------------------------------------- truncated normal

def tn_sample(m, s, lo, hi, U):
    """Draw N(m, s^2) truncated to [lo, hi) by inversion, evaluated in the lower tail (flip when the cell is above m)."""
    m = np.asarray(m, float)
    a, b = (lo - m) / s, (hi - m) / s
    a, b = np.broadcast_arrays(a, b)
    flip = a > 0
    a2, b2 = np.where(flip, -b, a), np.where(flip, -a, b)
    la, lb = special.log_ndtr(a2), special.log_ndtr(b2)
    with np.errstate(divide="ignore"):
        lp = np.logaddexp(la + np.log1p(-U), lb + np.log(U))
    z = special.ndtri(np.exp(lp))
    z = np.where(np.isfinite(z), z, np.where(np.isfinite(b2), b2, a2))
    z = np.clip(z, a2, b2)
    return m + s * np.where(flip, -z, z)


def tn_moments(m, P, a, c):
    """Mean and variance of N(m, P) truncated to [a, c); stable in both tails."""
    s = math.sqrt(P)
    al = -math.inf if a == -math.inf else (a - m) / s
    be = math.inf if c == math.inf else (c - m) / s
    flip = al > 0
    if flip:
        al, be = -be, -al
    lb, la = float(special.log_ndtr(be)), float(special.log_ndtr(al))
    if la - lb > -1e-12:
        mu, var = (be if be <= 0 else al), 1e-6
    else:
        logz = lb + math.log1p(-math.exp(la - lb))
        pa = 0.0 if al == -math.inf else math.exp(-0.5 * al * al - logz) / math.sqrt(2 * math.pi)
        pb = 0.0 if be == math.inf else math.exp(-0.5 * be * be - logz) / math.sqrt(2 * math.pi)
        mu = pa - pb
        var = max(1.0 + (0.0 if al == -math.inf else al * pa) - (0.0 if be == math.inf else be * pb) - mu * mu, 1e-9)
    if flip:
        mu = -mu
    return m + s * mu, P * var


def sqrtm_psd(C):
    w, V = np.linalg.eigh((C + C.T) / 2)
    return V * np.sqrt(np.clip(w, 0, None))


# ---------------------------------------------------------------- SMC primitives

def obs_update(mu, groups, h, rs, ws, lo, hi, rng, point=None):
    """Fully adapted update for one observation of v = h.x + noise, noise ~ sum_j ws[j] N(0, rs[j]).

    mu: (N, n) per-particle conditional means (modified in place); groups: list of (index array, common cov Q).
    Interval observation v in [lo, hi), or a Gaussian point observation v = point. Returns (dlogw, new groups)."""
    dlogw = np.zeros(mu.shape[0])
    out = []
    for idx, Q in groups:
        m = mu[idx] @ h
        Qh = Q @ h
        hQh = float(h @ Qh)
        lps = []
        for r in rs:
            s = math.sqrt(hQh + r)
            lps.append(log_interval_prob((lo - m) / s, (hi - m) / s) if point is None else stats.norm.logpdf(point, m, s))
        if len(rs) == 1:
            comp = np.zeros(idx.size, int)
            tot = lps[0]
        else:
            a0, a1 = math.log(ws[0]) + lps[0], math.log(ws[1]) + lps[1]
            tot = np.logaddexp(a0, a1)
            comp = (rng.random(idx.size) < np.exp(a1 - tot)).astype(int)
        dlogw[idx] = tot
        for j, r in enumerate(rs):
            sel_mask = comp == j
            if not sel_mask.any():
                continue
            sel = idx[sel_mask]
            S = hQh + r
            msel = m[sel_mask]
            v = np.full(sel.size, point) if point is not None else tn_sample(msel, math.sqrt(S), lo, hi, rng.random(sel.size))
            K = Qh / S
            mu[sel] = mu[sel] + np.outer(v - msel, K)
            out.append((sel, Q - np.outer(K, Qh)))
    return dlogw, out


def draw(mu, groups, rng):
    X = mu.copy()
    Z = rng.standard_normal(mu.shape)
    for idx, Q in groups:
        X[idx] += Z[idx] @ sqrtm_psd(Q).T
    return X


def ess(logw):
    w = np.exp(logw - logw.max())
    return w.sum() ** 2 / np.sum(w * w)


def resample_idx(logw, rng):
    w = np.exp(logw - logw.max())
    c = np.cumsum(w / w.sum())
    N = w.size
    return np.minimum(np.searchsorted(c, (rng.random() + np.arange(N)) / N), N - 1)


def _cut(day, t0, lag):
    """Largest grid index whose observation is received at t0 under a receipt lag (minutes)."""
    return int(math.floor((t0 - lag + PRE_MIN) / GRID_MIN + 1e-9))


# ---------------------------------------------------------------- A0 (no state space)

def a0_pmf(side, B, fut_f, mean, sd):
    """A0, a crude stand-in for the current form: final = max(B, R(V)) (low: min), V ~ N(max (min) remaining forecast +
    mean residual, sd); no conditioning on the latest observations. Exact pmf."""
    if len(fut_f) == 0:
        return np.array([int(B)]), np.array([1.0])
    c = (max(fut_f) if side == "high" else min(fut_f)) + mean
    lo, hi = int(math.floor(c - 10 * sd)) - 1, int(math.ceil(c + 10 * sd)) + 1
    if np.isfinite(B):
        lo, hi = min(lo, int(B)), max(hi, int(B))
    ks = np.arange(lo, hi + 1)
    px = stats.norm.cdf((ks + 0.5 - c) / sd) - stats.norm.cdf((ks - 0.5 - c) / sd)
    if np.isfinite(B):
        b = int(B)
        if side == "high":
            px = np.where(ks < b, 0.0, px)
            px[ks == b] = stats.norm.cdf((b + 0.5 - c) / sd)
        else:
            px = np.where(ks > b, 0.0, px)
            px[ks == b] = stats.norm.sf((b - 0.5 - c) / sd)
    px = np.clip(px, 0, None)
    return ks, px / px.sum()


def a0_day(day, lag_m, decisions, a0):
    out = []
    for t0 in decisions:
        gm = _cut(day, t0, lag_m)
        rin = (day.mi <= gm) & day.m_in
        Bh = float(day.mk[rin].max()) if rin.any() else -math.inf
        Bl = float(day.mk[rin].min()) if rin.any() else math.inf
        pend = day.si[day.si > gm]
        fut = day.f[pend]
        h = int(day.hour[min(_cut(day, t0, 0), day.gt.size - 1)])
        res = {}
        for side, B in (("high", Bh), ("low", Bl)):
            mean, sd = a0[side].get(h, a0[side]["all"])
            res[side] = a0_pmf(side, B, fut, mean, sd)
        out.append(dict(t0=t0, Bh=Bh, Bl=Bl, **res))
    return out


def a0_targets(day, lag_m, decisions):
    """Training rows for A0: (local hour, realised extreme over pending routine instants - forecast extreme), per side."""
    rows = []
    obs = {}
    for j in range(day.mi.size):
        obs.setdefault(int(day.mi[j]), int(day.mk[j]))
    for t0 in decisions:
        gm = _cut(day, t0, lag_m)
        pend = [g for g in day.si[day.si > gm] if g in obs]
        if not pend:
            continue
        v = np.array([obs[g] for g in pend])
        f = day.f[pend]
        h = int(day.hour[min(_cut(day, t0, 0), day.gt.size - 1)])
        rows.append((h, float(v.max() - f.max()), float(v.min() - f.min())))
    return rows


def fit_a0(rows, sd_floor=0.3):
    out = {}
    arr = np.array(rows, float)
    for k, side in ((1, "high"), (2, "low")):
        d = {"all": (float(arr[:, k].mean()), max(float(arr[:, k].std()), sd_floor))}
        for h in range(24):
            s = arr[arr[:, 0] == h, k]
            if s.size >= 20:
                d[h] = (float(s.mean()), max(float(s.std()), sd_floor))
        out[side] = d
    return out


# ---------------------------------------------------------------- grid oracle (tests): one-scale OU, exact recursion

def grid_oracle(day, tau, s2, r_dense, t0, lag_m=0.0, lag_d=0.0, use_dense=True, K=601, width=7.0):
    """Finite-state forward recursion for a one-scale OU on a fine residual grid (cells of width 2*width*s/(K-1)),
    exact METAR cell censoring and Gaussian (unquantised) dense likelihood at the cell representatives; P(H <= k) and
    P(L >= k) by indicator columns at pending instants. Returns {'high': (ks, p), 'low': (ks, p)}."""
    s = math.sqrt(s2)
    u = np.linspace(-width * s, width * s, K)
    du = u[1] - u[0]
    edges = np.r_[-np.inf, (u[:-1] + u[1:]) / 2, np.inf]

    def trans(dt):
        ph = math.exp(-dt / tau)
        sd = math.sqrt(s2 * (1 - ph * ph))
        cdf = stats.norm.cdf((edges[None, :] - ph * u[:, None]) / sd)
        T = np.diff(cdf, axis=1)
        return T / T.sum(1, keepdims=True)

    T5 = trans(GRID_MIN)
    gm = _cut(day, t0, lag_m)
    gd = _cut(day, t0, lag_d) if use_dense else -10 ** 9
    pend = day.si[day.si > gm]
    rin = (day.mi <= gm) & day.m_in
    Bh = float(day.mk[rin].max()) if rin.any() else -math.inf
    Bl = float(day.mk[rin].min()) if rin.any() else math.inf
    alpha = stats.norm.pdf(u / s)
    alpha /= alpha.sum()
    lo_k = int(math.floor(day.fmu.min() - width * s)) - 2
    hi_k = int(math.ceil(day.fmu.max() + width * s)) + 2
    ks = np.arange(lo_k, hi_k + 1)
    Ah = np.tile(alpha[:, None], (1, ks.size))
    Al = Ah.copy()
    last = max(int(day.si.max()), gm, gd)
    for g in range(0, last + 1):
        if g:
            Ah, Al = T5.T @ Ah, T5.T @ Al
        lik = np.ones(K)
        if use_dense:
            for j in np.nonzero((day.di == g) & (day.di <= gd))[0]:
                z = day.dx[j] + day.db[j] - day.fmu[g]
                lik *= stats.norm.pdf(z, u, math.sqrt(r_dense))
        for j in np.nonzero((day.mi == g) & (day.mi <= gm))[0]:
            lik *= R(day.fmu[g] + u) == day.mk[j]
        Ah, Al = Ah * lik[:, None], Al * lik[:, None]
        if g in set(pend.tolist()):
            v = R(day.fmu[g] + u)
            Ah *= (v[:, None] <= ks[None, :])
            Al *= (v[:, None] >= ks[None, :])
        nz = Ah[:, -1].sum()
        Ah, Al = Ah / nz, Al / nz
    cdf_h = Ah.sum(0)
    sf_l = Al.sum(0)
    if np.isfinite(Bh):
        cdf_h = np.where(ks < Bh, 0.0, cdf_h)
    if np.isfinite(Bl):
        sf_l = np.where(ks > Bl, 0.0, sf_l)
    ph = np.diff(np.r_[0.0, cdf_h])
    pl = sf_l - np.r_[sf_l[1:], 0.0]
    ph, pl = np.clip(ph, 0, None), np.clip(pl, 0, None)
    return {"high": (ks, ph / ph.sum()), "low": (ks, pl / pl.sum())}


def extreme_pmf_independent_exact(side, B, centers, sd):
    """Exact pmf when the pending instants are independent N(center_i, sd^2) (test oracle)."""
    centers = np.asarray(centers, float)
    lo = int(math.floor(centers.min() - 10 * sd)) - 1
    hi = int(math.ceil(centers.max() + 10 * sd)) + 1
    if np.isfinite(B):
        lo, hi = min(lo, int(B)), max(hi, int(B))
    ks = np.arange(lo, hi + 1)
    if side == "high":
        cdf = np.prod(stats.norm.cdf((ks[:, None] + 0.5 - centers[None, :]) / sd), axis=1)
        if np.isfinite(B):
            cdf = np.where(ks < int(B), 0.0, cdf)
        p = np.diff(np.r_[0.0, cdf])
    else:
        sf = np.prod(stats.norm.sf((ks[:, None] - 0.5 - centers[None, :]) / sd), axis=1)
        if np.isfinite(B):
            sf = np.where(ks > int(B), 0.0, sf)
        p = sf - np.r_[sf[1:], 0.0]
    p = np.clip(p, 0, None)
    return ks, p / p.sum()


# ---------------------------------------------------------------- estimation

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
    """OU fit by n-weighted least squares on the empirical autocovariance at the given lags (lag 0 is never used: it
    carries the dense white noise). One scale: s2 exp(-L/tau). Two scales: s2_s exp(-L/tau_s) + s2_f exp(-L/tau_f),
    tau_f < tau_s. subtract(L): known non-latent autocovariance (dense error drift) removed first."""
    lags = [L for L in lags_min if abs(L / step_min - round(L / step_min)) < 1e-9]
    C, N = acov(segments, step_min, lags)
    Lg = np.asarray(lags, float)
    if subtract is not None:
        C = C - np.array([subtract(L) for L in Lg])
    ok = np.isfinite(C) & (N > 0)

    def model(th):
        if n_scales == 1:
            return math.exp(th[0]) * np.exp(-Lg / math.exp(th[1]))
        tf = TAU_F_MIN + math.exp(th[1])
        ts = tf + math.exp(th[3])
        return math.exp(th[0]) * np.exp(-Lg / tf) + math.exp(th[2]) * np.exp(-Lg / ts)

    def loss(th):
        return float(np.sum(N[ok] * (C[ok] - model(th)) ** 2))

    c1 = max(float(C[ok][0]), 1e-4)
    starts = ([[math.log(c1), math.log(t)] for t in (30.0, 120.0, 480.0, 2000.0)] if n_scales == 1 else
              [[math.log(c1 * fr), math.log(tf), math.log(c1 * (1 - fr)), math.log(ts)]
               for fr in (0.2, 0.5) for tf in (10.0, 60.0) for ts in (300.0, 1500.0)])
    best = min((optimize.minimize(loss, s0, method="Nelder-Mead", options=dict(xatol=1e-7, fatol=1e-14, maxiter=20000))
                for s0 in starts), key=lambda r: r.fun)
    th = best.x
    fit = model(th)
    w = N[ok] / N[ok].sum()
    ss_res = float(np.sum(w * (C[ok] - fit[ok]) ** 2))
    ss_tot = float(np.sum(w * (C[ok] - np.sum(w * C[ok])) ** 2))
    if n_scales == 1:
        lat = dict(tau_s=math.exp(th[1]), s2_s=math.exp(th[0]), tau_f=30.0, s2_f=0.0)
    else:
        tf = TAU_F_MIN + math.exp(th[1])
        lat = dict(tau_f=tf, s2_f=math.exp(th[0]), tau_s=tf + math.exp(th[3]), s2_s=math.exp(th[2]))
    return dict(**lat, lags=lags, acov=[round(float(x), 4) for x in C], fit=[round(float(x), 4) for x in fit],
                n_pairs=[int(x) for x in N], r2=(1 - ss_res / ss_tot) if ss_tot > 0 else None, n_scales=n_scales)


def fit_error_drift(lags, rhos, sigma2):
    """Dense-error drift from latent correlations of the dense-vs-METAR error at settlement-instant lags:
    rho(L) = a exp(-L / tau_e)  ->  sd2 = a sigma2 (drift part), (1 - a) sigma2 white."""
    L, r = np.asarray(lags, float), np.clip(np.asarray(rhos, float), -0.99, 0.99)
    ok = np.isfinite(r)
    if ok.sum() == 0 or np.all(r[ok] <= 0.02):
        return dict(a=0.0, tau_e=1.0, sd2=0.0)

    def loss(th):
        a, tau = 1 / (1 + math.exp(-th[0])), math.exp(th[1])
        return float(np.sum((r[ok] - a * np.exp(-L[ok] / tau)) ** 2))

    res = min((optimize.minimize(loss, [0.0, math.log(t0)], method="Nelder-Mead") for t0 in (15.0, 60.0, 240.0)),
              key=lambda x: x.fun)
    a, tau = 1 / (1 + math.exp(-res.x[0])), min(math.exp(res.x[1]), TAU_E_MAX)
    return dict(a=a, tau_e=tau, sd2=a * sigma2)


def _psi(z):
    return z * special.ndtr(z) + np.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def uq_prob(k, x, sd, q):
    """P(k - 0.5 <= x + U + E < k + 0.5), U ~ Unif[-q/2, q/2), E ~ N(0, sd^2): a quantised dense reading x against the
    integer k (flat prior of the continuous reading inside its quantum)."""
    h = q / 2
    hi, lo = k + 0.5 - x, k - 0.5 - x
    v = (sd / q) * (_psi((hi + h) / sd) - _psi((hi - h) / sd) - _psi((lo + h) / sd) + _psi((lo - h) / sd))
    return np.clip(v, 1e-300, 1.0)


def fit_dense_noise(M, x, hour, quantum=0.1, mixture=True, s_floor=0.005):
    """ML of b by 3-hour local block (8 values), core sd s1, outlier sd s2 >= s1 and outlier weight pi, on
    same-instant pairs: P(M | x) = (1 - pi) uq(M, x + b, s1) + pi uq(M, x + b, s2)."""
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
    # multi-start: the quantised likelihood is flat in sigma near its floor, a single local start can stall there
    starts = ([np.r_[b0, math.log(s)] for s in (0.05, 0.2, 0.5, 1.0)] if not mixture else
              [np.r_[b0, math.log(s1), math.log(ds), lg] for s1 in (0.05, 0.15, 0.3) for ds in (0.2, 0.6) for lg in (-2.5, -1.0)])
    best = None
    for th0 in starts:
        r = optimize.minimize(nll, th0, method="L-BFGS-B")
        if best is None or r.fun < best.fun:
            best = r
    r = optimize.minimize(nll, best.x, method="Nelder-Mead", options=dict(maxiter=20000, xatol=1e-6, fatol=1e-8))
    b, s1, s2, pi = unpack(r.x)
    npar = 9 + (2 if mixture else 0)
    return dict(b_block=[float(v) for v in b], s1=s1, s2=s2, pi=pi, nll=float(r.fun), aic=2 * float(r.fun) + 2 * npar,
                n=int(M.size), mixture=mixture)


# ---------------------------------------------------------------- scoring

def score(ks, p, truth):
    """(log loss with LL_FLOOR, multi-category Brier, top prob, top correct, P(truth), randomized-PIT bounds)."""
    ks, p = np.asarray(ks), np.asarray(p, float)
    hit = ks == truth
    pt = float(p[hit].sum()) if hit.any() else 0.0
    brier = float(np.sum(p ** 2) - 2 * pt + 1)
    j = int(np.argmax(p))
    below = float(p[ks < truth].sum())
    return dict(ll=-math.log(max(pt, LL_FLOOR)), brier=brier, top=float(p[j]), top_ok=bool(ks[j] == truth), pt=pt,
                pit_lo=below, pit_hi=below + pt)


# ---------------------------------------------------------------- synthetic days (tests)

def simulate_days(n_days, lat, s_dense, seed=0, metar_minutes=(20, 50), dense_step=10, f_amp=6.0, f_mean=15.0,
                  quantum=0.1, bias=0.0):
    """Synthetic local days (UTC = local, 24 h): f = diurnal sine; T = f + a + c on a 1-minute grid (continuous across
    days); METAR at metar_minutes -> R(T); dense every dense_step min -> Q_q(T - bias + N(0, s_dense^2)).
    Returns (days, residual segments x + bias - f per day on the dense grid)."""
    rng = np.random.default_rng(seed)
    total = (n_days + 1) * 1440 + 180
    comps = np.zeros((total, 2))
    for k, (tau, s2) in enumerate(((lat.tau_s, lat.s2_s), (lat.tau_f, lat.s2_f))):
        if s2 <= 0:
            continue
        ph = math.exp(-1.0 / tau)
        e = rng.standard_normal(total) * math.sqrt(s2 * (1 - ph * ph))
        u = np.empty(total)
        u[0] = math.sqrt(s2) * rng.standard_normal()
        for i in range(1, total):
            u[i] = ph * u[i - 1] + e[i]
        comps[:, k] = u
    tmin = np.arange(total) - 180.0
    f_all = f_mean + f_amp * np.sin(2 * math.pi * ((tmin % 1440) / 1440.0 - 0.375))
    T = f_all + comps.sum(1)
    days, segs = [], []
    for d in range(n_days):
        a = d * 1440
        idx = lambda t: (np.asarray(t) + a + 180).astype(int)  # noqa: E731
        gt = np.arange(-PRE_MIN, 1440 + GRID_MIN / 2, GRID_MIN)
        mt = np.array([t for t in range(-180, 1440) if (t % 60) in metar_minutes], float)
        mk = R(T[idx(mt)])
        sched = mt[mt >= 0]
        dt_ = np.arange(-180, 1440, dense_step, dtype=float)
        xc = T[idx(dt_)] - bias + s_dense * rng.standard_normal(dt_.size)
        dx = np.round(xc / quantum) * quantum
        settled = {"high": int(mk[mt >= 0].max()), "low": int(mk[mt >= 0].min())}
        hour = ((gt % 1440) // 60).astype(int)
        from ss_engine import Day  # noqa: PLC0415  (engine module imports this one)
        days.append(Day(1440, f_all[idx(gt)], np.zeros(24), hour, mt, mk, sched, dt_, dx, np.full(dt_.size, bias),
                        settled=settled, era={"high": "syn", "low": "syn"}, label=f"syn{d}"))
        segs.append(dx[dt_ >= 0] + bias - f_all[idx(dt_[dt_ >= 0])])
    return days, segs
