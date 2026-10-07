"""Inference engine for the settled-extreme state-space model (see state_space.py for the model statement).

State x = (a, c, d): slow OU, fast OU, dense-error drift. METAR observes a + c (+ f + mu) through the rounding cell;
a dense reading observes a + c + d + white mixture noise through its quantum cell.

Decision at tau0 (all clocks UTC; receipt rule: a METAR is received when its observation time + lag_m <= tau0, a dense
reading when its stamp + lag_d <= tau0):
  main pass  every observation with grid index <= g* = min(gm, gd) (both channels complete), in time order;
  continue   the faster channel over (g*, max(gm, gd)], recording R(T) at pending settlement instants on the path;
  forward    OU simulation over the remaining pending instants to the day end.
The pmf of the settled high and low is the weighted histogram of max(B, path max) / min(B, path min).

Engines: 'smc' -- fully adapted particle filter (exact interval likelihoods; Monte Carlo only in the posterior
representation); 'kf_tn' / 'kf_g12' -- Gaussian assumed-density main pass (approximation arms; METAR update = exact
truncated-normal moment match / Gaussian N(M, 1/12)), sampled into the same continuation and forward simulation.
A and B arms share every parameter, every random stream and every decision rule; B only adds dense likelihood factors.
"""
from __future__ import annotations

import math

import numpy as np

from dense_model import R
from state_space import HD, HM, GRID_MIN, PRE_MIN, _cut, draw, ess, obs_update, resample_idx, sqrtm_psd, tn_moments


class Day:
    """One local day; time in minutes from the local day start (UTC-anchored; D = true local-day length).

    f, hour on the grid gt = -180 .. D step 5; METAR rows (mt minutes, mk int) incl. SPECIs; sched_t: routine clock
    schedule inside [0, D); dense rows (dt_ minutes on the 5-min grid, dx reading, db bias for that row)."""

    def __init__(self, D, f, mu_h, hour, mt, mk, sched_t, dt_, dx, db, settled=None, era=None, label="", dense_ok=True,
                 fmu=None):
        self.D = float(D)
        self.gt = np.arange(-PRE_MIN, D + GRID_MIN / 2, GRID_MIN)
        n = self.gt.size
        self.f = np.asarray(f, float)
        self.hour = np.asarray(hour, int)
        if fmu is not None:  # latent mean supplied directly (f + mu(h) + wiggle shrinkage)
            self.fmu = np.asarray(fmu, float)
        else:
            self.fmu = self.f + (np.asarray(mu_h, float)[self.hour] if mu_h is not None else 0.0)
        gi = lambda t: np.clip(np.rint((np.asarray(t, float) + PRE_MIN) / GRID_MIN).astype(int), 0, n - 1)  # noqa: E731
        mt, mk = np.asarray(mt, float), np.asarray(mk, int)
        self.mt, self.mk, self.mi = mt, mk, gi(mt)
        self.m_in = (mt >= 0) & (mt < self.D)
        self.si = np.unique(gi(np.asarray(sched_t, float)))
        dt_ = np.asarray(dt_, float)
        on = np.isclose((dt_ + PRE_MIN) / GRID_MIN, np.rint((dt_ + PRE_MIN) / GRID_MIN))
        self.di, self.dx, self.db = gi(dt_[on]), np.asarray(dx, float)[on], np.asarray(db, float)[on]
        # METAR integers merged per grid slot: a SPECI within 2.5 min of another report shares its 5-min point;
        # the observation is the hull of the cells (exact when the integers agree, conservative otherwise).
        self.mslot = {}
        for g, k in zip(self.mi, mk):
            lo, hi = self.mslot.get(int(g), (k, k))
            self.mslot[int(g)] = (min(lo, k), max(hi, k))
        self.settled = settled or {}
        self.era = era or {}
        self.label = label
        self.dense_ok = dense_ok

    def with_mu(self, mu_h):
        d = object.__new__(Day)
        d.__dict__.update(self.__dict__)
        d.fmu = self.f + np.asarray(mu_h, float)[self.hour]
        return d


def _metar_cell(day, g, ks_rec):
    lo, hi = min(ks_rec), max(ks_rec)
    return lo - 0.5 - day.fmu[g], hi + 0.5 - day.fmu[g]


def _pmf(vals, w):
    vals = vals.astype(np.int64)
    lo = int(vals.min())
    cnt = np.bincount(vals - lo, weights=w)
    ks = np.arange(lo, lo + cnt.size)
    keep = cnt > 0
    p = cnt[keep]
    return ks[keep], p / p.sum()


def _events(day, use_dense):
    """grid index -> (dense row ids, METAR row ids)."""
    ev = {}
    for j, g in enumerate(day.mi):
        ev.setdefault(int(g), ([], []))[1].append(j)
    if use_dense:
        for j, g in enumerate(day.di):
            ev.setdefault(int(g), ([], []))[0].append(j)
    return ev


class _Smc:
    def __init__(self, lat, noise, N, rng):
        self.lat, self.N, self.rng = lat, N, rng
        self.rs = [noise.s1 ** 2, noise.s2 ** 2] if noise.pi > 0 else [noise.s1 ** 2]
        self.ws = [1 - noise.pi, noise.pi] if noise.pi > 0 else [1.0]
        self.q = noise.quantum
        self.X = np.sqrt(lat.var()) * rng.standard_normal((N, 3))
        self.logw = np.zeros(N)
        self.g = -1  # the stationary prior sits one step before the first grid point

    def copy(self, rng):
        c = object.__new__(_Smc)
        c.__dict__.update(self.__dict__)
        c.X, c.logw, c.rng = self.X.copy(), self.logw.copy(), rng
        return c

    def step(self, day, g, dense_rows, metar_ks, auto_resample=True):
        dt = (g - self.g) * GRID_MIN
        if dt > 0:
            mu = self.X * self.lat.phi(dt)
            groups = [(np.arange(self.N), np.diag(self.lat.q(dt)))]
        else:  # observation at the current state's own instant: condition the particle cloud directly
            mu = self.X.copy()
            groups = [(np.arange(self.N), np.diag(np.full(3, 1e-12)))]
        for j in dense_rows:
            c = day.dx[j] + day.db[j] - day.fmu[g]
            dl, groups = obs_update(mu, groups, HD, self.rs, self.ws, c - self.q / 2, c + self.q / 2, self.rng)
            self.logw += dl
        if metar_ks:
            lo, hi = _metar_cell(day, g, metar_ks)
            dl, groups = obs_update(mu, groups, HM, [1e-12], [1.0], lo, hi, self.rng)
            self.logw += dl
        self.X = draw(mu, groups, self.rng)
        self.g = g
        if auto_resample and ess(self.logw) < self.N / 2:
            self.resample()

    def resample(self, extra=()):
        ri = resample_idx(self.logw, self.rng)
        self.X = self.X[ri]
        self.logw = np.zeros(self.N)
        return [e[ri] for e in extra]


class _Kf:
    def __init__(self, lat, noise, mode):
        self.lat, self.mode = lat, mode
        self.r = noise.total_var() + noise.quantum ** 2 / 12
        self.m, self.P = np.zeros(3), np.diag(lat.var())
        self.g = -1

    def step(self, day, g, dense_rows, metar_ks):
        dt = (g - self.g) * GRID_MIN
        if dt > 0:
            F = self.lat.phi(dt)
            self.m, self.P = F * self.m, self.P * np.outer(F, F) + np.diag(self.lat.q(dt))
        for j in dense_rows:
            z = day.dx[j] + day.db[j] - day.fmu[g]
            S = float(HD @ self.P @ HD) + self.r
            K = self.P @ HD / S
            self.m, self.P = self.m + K * (z - self.m @ HD), self.P - np.outer(K, HD @ self.P)
        if metar_ks:
            lo, hi = _metar_cell(day, g, metar_ks)
            Ph = self.P @ HM
            S = float(HM @ Ph)
            if self.mode == "tn":
                mn, vn = tn_moments(float(self.m @ HM), S, lo, hi)
                k = Ph / S
                self.m, self.P = self.m + k * (mn - self.m @ HM), self.P + (vn - S) * np.outer(k, k)
            else:
                S2 = S + 1.0 / 12.0
                K = Ph / S2
                self.m, self.P = self.m + K * ((lo + hi) / 2 - self.m @ HM), self.P - np.outer(K, Ph)
        self.g = g

    def to_smc(self, noise, N, rng):
        s = _Smc.__new__(_Smc)
        s.lat, s.N, s.rng = self.lat, N, rng
        s.rs = [noise.s1 ** 2, noise.s2 ** 2] if noise.pi > 0 else [noise.s1 ** 2]
        s.ws = [1 - noise.pi, noise.pi] if noise.pi > 0 else [1.0]
        s.q = noise.quantum
        s.X = self.m + rng.standard_normal((N, 3)) @ sqrtm_psd(self.P).T
        s.logw = np.zeros(N)
        s.g = self.g
        return s


def run_day(day, lat, noise, lag_m, decisions, arm, N=20000, seed=0):
    """pmfs of the settled high and low at each decision time (minutes from the local day start, increasing).

    arm: 'A' / 'B' (smc; METAR only / METAR + dense), 'A_tn' / 'B_tn', 'A_g12' / 'B_g12' (Gaussian main pass).
    Returns [dict(t0, high=(ks, p), low=(ks, p), Bh, Bl, ess)]."""
    use_dense = arm.startswith("B") and day.dense_ok
    engine = "smc" if "_" not in arm else arm.split("_")[1]
    ev = _events(day, use_dense)
    order = sorted(ev)
    st = _Smc(lat, noise, N, np.random.default_rng([seed, 11])) if engine == "smc" else _Kf(lat, noise, engine)
    p = 0
    out = []
    for t0 in decisions:
        gm = _cut(day, t0, lag_m)
        gd = _cut(day, t0, noise.lag) if use_dense else -(10 ** 9)
        gstar = min(gm, gd) if use_dense else gm
        while p < len(order) and order[p] <= gstar:
            g = order[p]
            dj, mj = ev[g]
            st.step(day, g, dj, [int(day.mk[j]) for j in mj])
            p += 1
        drng = np.random.default_rng([seed, int(round(t0 * 10)), 3])
        s = st.copy(drng) if engine == "smc" else st.to_smc(noise, N, drng)
        rin = (day.mi <= gm) & day.m_in
        Bh = float(day.mk[rin].max()) if rin.any() else -math.inf
        Bl = float(day.mk[rin].min()) if rin.any() else math.inf
        pend = day.si[day.si > gm]
        if pend.size == 0:
            out.append(dict(t0=t0, high=(np.array([int(Bh)]), np.array([1.0])), low=(np.array([int(Bl)]), np.array([1.0])),
                            Bh=Bh, Bl=Bl, ess=float(N)))
            continue
        rhi, rlo = np.full(N, -np.inf), np.full(N, np.inf)
        pend_set = set(int(g) for g in pend)
        cont = {}
        if use_dense and gd > gm:  # dense runs ahead of the METAR feed
            for j in np.nonzero((day.di > max(gm, s.g)) & (day.di <= gd))[0]:
                cont.setdefault(int(day.di[j]), ([], []))[0].append(j)
            for g in pend[pend <= gd]:
                cont.setdefault(int(g), ([], []))
        elif use_dense and gm > gd:  # METAR feed runs ahead of dense
            for j in np.nonzero((day.mi > max(gd, s.g)) & (day.mi <= gm))[0]:
                cont.setdefault(int(day.mi[j]), ([], []))[1].append(j)
        for g in sorted(cont):
            if g <= s.g and g not in pend_set:
                continue
            dj, mj = cont[g]
            s.step(day, g, dj, [int(day.mk[j]) for j in mj], auto_resample=False)
            if g in pend_set:
                v = R(day.fmu[g] + s.X[:, 0] + s.X[:, 1]).astype(float)
                rhi, rlo = np.maximum(rhi, v), np.minimum(rlo, v)
            if s.logw.max() > s.logw.min() and ess(s.logw) < N / 2:
                rhi, rlo = s.resample((rhi, rlo))
        A = s.X[:, :2].copy()
        gc = s.g
        for g in pend[pend > gc]:
            dt = (g - gc) * GRID_MIN
            A = A * s.lat.phi(dt)[:2] + np.sqrt(s.lat.q(dt)[:2]) * drng.standard_normal((N, 2))
            v = R(day.fmu[g] + A[:, 0] + A[:, 1]).astype(float)
            rhi, rlo = np.maximum(rhi, v), np.minimum(rlo, v)
            gc = g
        w = np.exp(s.logw - s.logw.max())
        hi_v = np.maximum(rhi, Bh) if np.isfinite(Bh) else rhi
        lo_v = np.minimum(rlo, Bl) if np.isfinite(Bl) else rlo
        out.append(dict(t0=t0, high=_pmf(hi_v, w), low=_pmf(lo_v, w), Bh=Bh, Bl=Bl, ess=float(ess(s.logw))))
    return out
