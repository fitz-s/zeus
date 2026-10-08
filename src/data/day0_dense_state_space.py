# Created: 2026-10-07
# Last reused or audited: 2026-10-08
# Authority basis: docs/authority/day0_dense_observation_probability.md (PR #536 theory);
#   consult REQ-20261007-125708-2dc18b (NO-GO) and coordinator D2/D5 (2026-10-08): normalized
#   report-lifecycle marks, shared outage latent, visibility likelihood, exact instants, joint
#   threshold-aware integration, stable tails, continuous SPECI exposure, no-data lowest bracket.
"""Dense-observation state-space law for the settled Day0 extreme (pure; no IO).

Random variable.  The settlement tape S is the set of report rows on the final page of the local
day.  H = max_{i in S} V_i, L = min_{i in S} V_i, where V_i is the page value of report i.  An
empty tape resolves to the lowest bracket (the contract's no-data clause), for HIGH and LOW.

Latent temperature, on minutes from local midnight (window starts PRE_MIN earlier):
    T_t = m_t + a + r_t,   m_t = f_t + mu(h_t) + beta (f_t - S_4h f_t)
  a     static day offset ~ N(0, s2_static), integrated by Gauss-Hermite with evidence weights
  r     OU (tau, s2), exact transitions over any gap
  d     optional OU dense-error drift (tau_e, sd2)
Every report and reading sits at its own exact instant; nothing is merged onto a grid slot.

Evidence and tape events at an instant (R = contract rounding, I_k = R^-1(k)):
  page row (t, k)        settlement-product row: T_t in I_k and k joins the tape.  The only
                         semantic certificate.
  mark (t, k, w)         a received report (AWC, Ogimet, native report route) the page has not
                         resolved.  Branches, with unnormalised weights w = lifecycle prior times
                         the page-visibility likelihood of what the fetches showed:
                           kept       T_t in I_k, tape value k
                           corrected  T_t in I_k, tape value k + delta
                           removed    T_t in I_k (a valid report the page dropped), no tape value
                           gross      no evidence about T, no tape value
                         T-evidence branches are divided by the forward predictive P(T_t in I_k),
                         so each branch's posterior given the past is proportional to its weight
                         (a gross report is distributed like the predictive), and the evidence
                         column integrates to the visibility likelihood sum(w).
  pending (t, w)         an unreported scheduled instant: kept/corrected with R(T_t) (+ delta),
                         else no tape value.  Not evidence about T.
  SPECI exposure         continuous hazard lam per minute from ``speci_from``: on each interval of
                         length dt a SPECI joins the tape with probability 1 - exp(-lam dt).
  dense reading (t, x)   quantised national-station value: likelihood only, never a tape value.
  context row (t, k)     METAR integer before local midnight: T_t in I_k, no tape role.
Shared outage latent Z.  Under Z = 1 the page dropped every unresolved mark in a shared outage:
each is a removed-but-valid report (weights (0, 0, 1, 0)).  With prior P(Z = 1) = ``outage_prior``,
q = sum_z w_z q_z and w_z is proportional to P(Z = z) P(E | Z = z), P(E | Z) the full evidence of
the recursion (visibility included).

Inference is a forward recursion on residual cells, mass uniform within a cell.  At an instant,
every factor is a piecewise-constant function of r (interval indicators, threshold cuts) times
the dense likelihood; the cell average of their product is computed in closed form, jointly and
per threshold column.  Columns per threshold k: S_k (no tape value crosses k) and X_k (some does),
updated without cancellation; plus the evidence column and the empty-tape column.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

import numpy as np
from scipy import special

GRID_MIN = 5.0
PRE_MIN = 180.0
SMOOTH_POINTS = 49  # centred 4 h running mean on the 5-min grid
CELL_C = 0.05
AXIS_HALF_WIDTH_SD = 10.0
AXIS_SNAP_C = 1.0
DRIFT_HALF_WIDTH_SD = 6.0
DRIFT_CELL_C = 0.1
STATIC_QUADRATURE_NODES = 9
DRIFT_FOLD_RATIO = 0.1  # sd2 below this share of the core variance folds into both white components
SPECI_STEP_MIN = 5.0


@dataclass(frozen=True)
class DenseMean:
    mu_hour: tuple[float, ...]  # 24 values by local clock hour
    beta: float

    def __post_init__(self) -> None:
        if len(self.mu_hour) != 24 or not all(math.isfinite(v) for v in (*self.mu_hour, self.beta)):
            raise ValueError("DAY0_DENSE_MEAN_INVALID")


@dataclass(frozen=True)
class DenseLatent:
    tau: float              # OU time scale, minutes
    s2: float               # OU stationary variance
    s2_static: float = 0.0  # static day offset variance

    def __post_init__(self) -> None:
        if not (self.tau > 0 and self.s2 > 0 and self.s2_static >= 0
                and all(math.isfinite(v) for v in (self.tau, self.s2, self.s2_static))):
            raise ValueError("DAY0_DENSE_LATENT_INVALID")


@dataclass(frozen=True)
class DenseNoise:
    b_hour: tuple[float, ...]  # 24 values added to the reading, by local clock hour
    s1: float                  # core white sd
    s2: float                  # outlier white sd (>= s1)
    pi: float                  # outlier weight
    quantum: float = 0.1
    tau_e: float = 1.0         # drift time scale, minutes
    sd2: float = 0.0           # drift stationary variance

    def __post_init__(self) -> None:
        if (len(self.b_hour) != 24 or not (0 < self.s1 <= self.s2) or not 0 <= self.pi < 1
                or not self.quantum > 0 or not self.tau_e > 0 or self.sd2 < 0
                or not all(math.isfinite(v) for v in (*self.b_hour, self.s1, self.s2, self.pi,
                                                       self.quantum, self.tau_e, self.sd2))):
            raise ValueError("DAY0_DENSE_NOISE_INVALID")

    def folded(self) -> "DenseNoise":
        """Fold a negligible drift into both white components, each variance conserved.

        The folded model drops the drift's serial dependence; folding applies only when the
        drift variance is under DRIFT_FOLD_RATIO of the core variance."""
        if self.sd2 == 0.0 or self.sd2 >= DRIFT_FOLD_RATIO * self.s1 ** 2:
            return self
        return DenseNoise(self.b_hour, math.sqrt(self.s1 ** 2 + self.sd2), math.sqrt(self.s2 ** 2 + self.sd2),
                          self.pi, self.quantum, self.tau_e, 0.0)


@dataclass(frozen=True)
class DenseModel:
    latent: DenseLatent
    noise: DenseNoise | None   # None: no dense channel (METAR-content observation only)
    mean: DenseMean


@dataclass(frozen=True)
class DenseDay:
    """One local day's admitted evidence at one cut; times are exact minutes from local midnight.

    ``forecast`` and ``hour`` are on ``grid_minutes(day_minutes)``.  ``marks`` rows are
    (t, k, w_kept, w_corrected, w_removed, w_gross), unnormalised; ``pending`` rows are
    (t, w_kept, w_corrected) with the rest no tape value; ``delta`` is the corrected-value
    offset distribution; ``speci_rate`` is the per-minute rate of SPECIs that join the tape."""

    metric: str
    day_minutes: float
    forecast: tuple[float, ...]
    hour: tuple[int, ...]
    page: tuple[tuple[float, int], ...] = ()
    marks: tuple[tuple[float, int, float, float, float, float], ...] = ()
    pending: tuple[tuple[float, float, float], ...] = ()
    context: tuple[tuple[float, int], ...] = ()
    dense: tuple[tuple[float, float], ...] = ()
    delta: tuple[tuple[int, float], ...] = ((-1, 0.5), (1, 0.5))
    speci_from: float = 0.0
    speci_rate: float = 0.0
    outage_prior: float = 0.0

    def __post_init__(self) -> None:
        n = grid_minutes(self.day_minutes).size
        ok = (self.metric in {"high", "low"} and len(self.forecast) == n and len(self.hour) == n
              and np.isfinite(np.asarray(self.forecast, float)).all()
              and all(0 <= h < 24 for h in self.hour)
              and all(0 <= t < self.day_minutes for t, _ in self.page)
              and all(0 <= m[0] < self.day_minutes and all(0.0 <= w <= 1.0 for w in m[2:6])
                      and sum(m[2:6]) > 0.0 for m in self.marks)
              and all(0 <= t < self.day_minutes and 0.0 <= a and 0.0 <= b and a + b <= 1.0 + 1e-12
                      for t, a, b in self.pending)
              and all(-PRE_MIN <= t < 0 for t, _ in self.context)
              and all(-PRE_MIN <= t <= self.day_minutes for t, _ in self.dense)
              and math.isclose(sum(p for _, p in self.delta), 1.0, abs_tol=1e-9)
              and -PRE_MIN <= self.speci_from <= self.day_minutes and self.speci_rate >= 0
              and 0.0 <= self.outage_prior < 1.0)
        times = [t for t, _ in self.page] + [m[0] for m in self.marks] + [t for t, *_ in self.pending]
        if not ok or len(times) != len(set(times)):
            raise ValueError("DAY0_DENSE_DAY_INVALID")

    @property
    def boundary_absorbing(self) -> int | None:
        """The page's own extreme: the semantic certificate."""
        if not self.page:
            return None
        ks = [k for _, k in self.page]
        return max(ks) if self.metric == "high" else min(ks)


def grid_minutes(day_minutes: float) -> np.ndarray:
    return np.arange(-PRE_MIN, day_minutes + GRID_MIN / 2, GRID_MIN)


def smooth_4h(f: np.ndarray) -> np.ndarray:
    w = SMOOTH_POINTS
    pad = np.r_[np.full(w // 2, f[0]), f, np.full(w // 2, f[-1])]
    return np.convolve(pad, np.ones(w) / w, mode="valid")[: f.size]


def mean_path(forecast: Sequence[float], hour: Sequence[int], mean: DenseMean) -> np.ndarray:
    f = np.asarray(forecast, float)
    return f + np.asarray(mean.mu_hour, float)[np.asarray(hour, int)] + mean.beta * (f - smooth_4h(f))


# ---------------------------------------------------------------- stable integrals

def _psi_neg(z: np.ndarray) -> np.ndarray:
    """psi(z) = z Phi(z) + phi(z) on z <= 0 (the antiderivative of Phi)."""
    return z * special.ndtr(z) + np.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def _psi_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """psi(a) - psi(b) without cancellation, via psi(z) = max(z, 0) + psi(-|z|)."""
    return (np.maximum(a, 0.0) - np.maximum(b, 0.0)) + (_psi_neg(-np.abs(a)) - _psi_neg(-np.abs(b)))


def _int_cdf(edge, lo, hi, sd: float) -> np.ndarray:
    """Integral over r in [lo, hi] of Phi((edge - r) / sd); exact for infinite edges."""
    edge, lo, hi = np.broadcast_arrays(np.asarray(edge, float), np.asarray(lo, float), np.asarray(hi, float))
    out = np.where(np.isposinf(edge), hi - lo, 0.0)
    fin = np.isfinite(edge)
    if fin.any():
        out = out.copy()
        out[fin] = sd * _psi_diff((edge[fin] - lo[fin]) / sd, (edge[fin] - hi[fin]) / sd)
    return np.clip(out, 0.0, None)


@dataclass(frozen=True)
class _Axis:
    lo: float
    h: float
    n: int

    @property
    def edges(self) -> np.ndarray:
        return self.lo + self.h * np.arange(self.n + 1)


_TRANSITIONS: dict[tuple, np.ndarray] = {}


def _transition(axis: _Axis, tau: float, s2: float, dt: float) -> np.ndarray:
    """Transposed OU cell kernel: (T^T)[j, i] = P(cell j at t + dt | uniform in cell i at t).

    The end cells are unbounded, so no transition mass is ever discarded."""
    key = (axis, tau, s2, round(dt, 9))
    cached = _TRANSITIONS.get(key)
    if cached is not None:
        return cached
    phi = math.exp(-dt / tau)
    sd = max(math.sqrt(s2 * (1.0 - phi * phi)), 1e-12)
    e = axis.edges.copy()
    e[0], e[-1] = -np.inf, np.inf
    lo, hi = axis.edges[:-1, None], axis.edges[1:, None]
    if phi * axis.h < 1e-9 * sd:  # the cell maps to a point
        mid = phi * 0.5 * (lo + hi)
        cdf = special.ndtr((e[None, :] - mid) / sd)
        ccdf = special.ndtr((mid - e[None, :]) / sd)
    else:
        # mean over x uniform in cell i of P(next < e_j | x) = (1 / (phi h)) int Phi((e_j - y) / sd) dy, y = phi x;
        # the upper tail from its own complement, so representable tail mass survives the difference
        cdf = _int_cdf(e[None, :], phi * lo, phi * hi, sd) / (phi * axis.h)
        ccdf = _int_cdf(-e[None, :], -phi * hi, -phi * lo, sd) / (phi * axis.h)
    T = np.where(cdf[:, 1:] <= 0.5, np.diff(cdf, axis=1), -np.diff(ccdf, axis=1))
    T = np.clip(T, 0.0, None)
    T /= T.sum(axis=1, keepdims=True)
    out = np.ascontiguousarray(T.T)
    if len(_TRANSITIONS) > 512:
        _TRANSITIONS.clear()
    _TRANSITIONS[key] = out
    return out


def thresholds_for(metric: str, bins: Sequence[tuple[float | None, float | None]]) -> list[int]:
    """Integer thresholds whose columns the bin partition needs."""
    ks: set[int] = set()
    for low, high in bins:
        if metric == "high":
            if high is not None:
                ks.add(int(round(high)))
            if low is not None:
                ks.add(int(round(low)) - 1)
        else:
            if low is not None:
                ks.add(int(round(low)))
            if high is not None:
                ks.add(int(round(high)) + 1)
    return sorted(ks)


# ---------------------------------------------------------------- piecewise factor algebra

class _Value:
    """A tape/evidence factor on one piece: evidence ev, no-cross s[k], cross c[k], empty-keep emp."""

    __slots__ = ("ev", "s", "c", "emp")

    def __init__(self, ev: float, s: np.ndarray, c: np.ndarray, emp: float):
        self.ev, self.s, self.c, self.emp = ev, s, c, emp

    def times(self, other: "_Value") -> "_Value":
        # (ev, s, c, emp) for two independent events at one instant; c without cancellation.
        return _Value(self.ev * other.ev, self.s * other.s, self.c * other.ev + self.s * other.c,
                      self.emp * other.emp)


class _Recursion:
    """Forward recursion of one model at one static offset under one outage state."""

    def __init__(self, model: DenseModel, day: DenseDay, m: np.ndarray, thresholds: Sequence[int],
                 offset: float, preimage: tuple[float, float], cell: float, outage: bool):
        self.model, self.day, self.off, self.outage = model, day, offset, outage
        self.grid = grid_minutes(day.day_minutes)
        self.m = m
        self.lo_off, self.hi_off = preimage
        self.k = np.asarray(thresholds, float)
        self.K = self.k.size
        lat = model.latent
        sd = math.sqrt(lat.s2)
        implied = [0.0]
        for t, k in (*day.page, *day.context):
            implied += [k + self.lo_off - self.base(t), k + self.hi_off - self.base(t)]
        for t, k, *_ in day.marks:
            implied += [k + self.lo_off - self.base(t), k + self.hi_off - self.base(t)]
        implied += [x - self.base(t) for t, x in day.dense]
        half = max(AXIS_HALF_WIDTH_SD * sd, max(abs(v) for v in implied) + 2.0)
        half = AXIS_SNAP_C * math.ceil(half / AXIS_SNAP_C)
        self.r = _Axis(-half, cell, int(round(2 * half / cell)))
        noise = model.noise
        self.d: _Axis | None = None
        if noise is not None and noise.sd2 > 0:
            dh = DRIFT_CELL_C * math.ceil(DRIFT_HALF_WIDTH_SD * math.sqrt(noise.sd2) / DRIFT_CELL_C)
            self.d = _Axis(-dh, DRIFT_CELL_C, int(round(2 * dh / DRIFT_CELL_C)))

    # -- geometry
    def base(self, t: float) -> float:
        return float(np.interp(t, self.grid, self.m)) + self.off

    def _hour(self, t: float) -> int:
        i = int(np.clip(np.searchsorted(self.grid, t, side="right") - 1, 0, self.grid.size - 1))
        return int(self.day.hour[i])

    def _prior(self) -> np.ndarray:
        pr = np.diff(special.ndtr(np.r_[-np.inf, self.r.edges[1:-1], np.inf] / math.sqrt(self.model.latent.s2)))
        if self.d is None:
            return pr[:, None]
        pd_ = np.diff(special.ndtr(np.r_[-np.inf, self.d.edges[1:-1], np.inf] / math.sqrt(self.model.noise.sd2)))
        return pr[:, None] * pd_[None, :]

    def _step(self, a: np.ndarray, dt: float) -> np.ndarray:
        if dt <= 1e-12:
            return a
        J, Jd, C = a.shape
        T = _transition(self.r, self.model.latent.tau, self.model.latent.s2, dt)
        a = (T @ a.reshape(J, Jd * C)).reshape(J, Jd, C)
        if self.d is not None:
            Td = _transition(self.d, self.model.noise.tau_e, self.model.noise.sd2, dt)
            a = np.ascontiguousarray(np.moveaxis((Td @ np.moveaxis(a, 1, 0).reshape(Jd, J * C)).reshape(Jd, J, C), 0, 1))
        return a

    # -- cell averages of piecewise functions times the dense likelihood
    def _piece_matrix(self, cuts: np.ndarray, dense_x: float | None, t: float) -> np.ndarray:
        """P[cell, d, piece] = (1/h) int over cell and piece of the dense likelihood (or 1)."""
        e = self.r.edges
        lo = np.maximum(e[:-1, None], cuts[None, :-1])
        hi = np.minimum(e[1:, None], cuts[None, 1:])
        width = np.clip(hi - lo, 0.0, None)
        jd = 1 if self.d is None else self.d.n
        if dense_x is None:
            return np.broadcast_to((width / self.r.h)[:, None, :], (self.r.n, jd, cuts.size - 1)).copy()
        nz = self.model.noise
        c = dense_x + nz.b_hour[self._hour(t)] - self.base(t)
        shift = np.zeros(1) if self.d is None else 0.5 * (self.d.edges[:-1] + self.d.edges[1:])
        lo_, hi_ = np.where(width > 0, lo, 0.0), np.where(width > 0, hi, 0.0)
        out = np.zeros((self.r.n, shift.size, cuts.size - 1))
        for w, s in ((1.0 - nz.pi, nz.s1), (nz.pi, nz.s2)):
            if w <= 0:
                continue
            for sign, edge in ((1.0, c + nz.quantum / 2), (-1.0, c - nz.quantum / 2)):
                val = _int_cdf((edge - shift)[None, :, None], lo_[:, None, :], hi_[:, None, :], s)
                out += sign * w * val
        out = np.clip(out, 0.0, None) * (width > 0)[:, None, :]
        return out / self.r.h

    # -- events at one instant
    def _events_value(self, x: float, t: float, events: list, norms: dict) -> _Value:
        K = self.K
        one, zero = np.ones(K), np.zeros(K)
        v = _Value(1.0, one, zero, 1.0)
        base = self.base(t)
        T = base + x
        high = self.day.metric == "high"

        def ok_value(val: float) -> np.ndarray:
            return (self.k >= val) if high else (self.k <= val)

        def ok_report(dlt: float = 0.0) -> np.ndarray:
            # R(T) + dlt stays on the allowed side; R(T) <= h - dlt iff T < h - dlt + hi_off (HIGH)
            if high:
                return (T < self.k - dlt + self.hi_off).astype(float)
            return (T >= self.k - dlt + self.lo_off).astype(float)

        def inside(k: int) -> float:
            return 1.0 if k + self.lo_off <= T < k + self.hi_off else 0.0

        for kind, payload in events:
            if kind == "page":
                ev = inside(payload)
                okv = ok_value(payload).astype(float)
                v = v.times(_Value(ev, ev * okv, ev * (1.0 - okv), 0.0))
            elif kind == "context":
                ev = inside(payload)
                v = v.times(_Value(ev, ev * one, zero, ev))
            elif kind == "mark":
                k, wk, wc, wr, wg = payload
                ins = inside(k) / norms[k] if norms[k] > 0 else 0.0
                okk = ok_value(k).astype(float)
                okc = sum(p * ok_value(k + dl) for dl, p in self.day.delta)
                ev = (wk + wc + wr) * ins + wg
                s = wk * ins * okk + wc * ins * okc + (wr * ins + wg) * one
                c = wk * ins * (1.0 - okk) + wc * ins * (1.0 - okc)
                v = v.times(_Value(ev, s, c, wr * ins + wg))
            elif kind == "pending":
                wk, wc = payload
                okr = ok_report()
                okc = sum(p * ok_report(dl) for dl, p in self.day.delta)
                absent = max(1.0 - wk - wc, 0.0)
                s = wk * okr + wc * okc + absent
                v = v.times(_Value(1.0, s, wk * (1.0 - okr) + wc * (1.0 - okc), absent))
            elif kind == "speci":
                p = payload
                okr = ok_report()
                v = v.times(_Value(1.0, 1.0 - p * (1.0 - okr), p * (1.0 - okr), 1.0 - p))
        return v

    def _cuts(self, t: float, events: list) -> np.ndarray:
        base = self.base(t)
        cuts = {-np.inf, np.inf}
        dl_all = [0.0] + [float(d) for d, _ in self.day.delta]
        for kind, payload in events:
            if kind in ("page", "context"):
                cuts |= {payload + self.lo_off - base, payload + self.hi_off - base}
            elif kind == "mark":
                k = payload[0]
                cuts |= {k + self.lo_off - base, k + self.hi_off - base}
            elif kind in ("pending", "speci"):
                off = self.hi_off if self.day.metric == "high" else self.lo_off
                for dl in (dl_all if kind == "pending" else [0.0]):
                    cuts |= {float(h) - dl + off - base for h in self.k}
        return np.asarray(sorted(cuts))

    def run(self) -> tuple[np.ndarray, np.ndarray, float, float]:
        """(S_k / C0, X_k / C0, E / C0, log evidence)."""
        day, K = self.day, self.K
        by_t: dict[float, list] = {}

        def add(t: float, item) -> None:
            by_t.setdefault(float(t), []).append(item)

        for t, k in day.context:
            add(t, ("context", int(k)))
        for t, k in day.page:
            add(t, ("page", int(k)))
        for t, k, wk, wc, wr, wg in day.marks:
            if self.outage:
                wk, wc, wr, wg = 0.0, 0.0, 1.0, 0.0
            add(t, ("mark", (int(k), wk, wc, wr, wg)))
        for t, wk, wc in day.pending:
            add(t, ("pending", (wk, wc)))
        dense_at = {float(t): float(x) for t, x in day.dense} if self.model.noise is not None else {}
        for t in dense_at:
            by_t.setdefault(t, [])
        lam = day.speci_rate
        speci: dict[float, float] = {}
        if lam > 0 and day.speci_from < day.day_minutes:
            t0 = max(day.speci_from, 0.0)
            edges = [t0] + [float(x) for x in np.arange(SPECI_STEP_MIN * math.ceil(t0 / SPECI_STEP_MIN + 1e-12),
                                                         day.day_minutes, SPECI_STEP_MIN) if x > t0] + [day.day_minutes]
            for a_, b_ in zip(edges, edges[1:]):
                if b_ > a_:
                    speci[b_] = 1.0 - math.exp(-lam * (b_ - a_))
        for t in speci:
            by_t.setdefault(t, [])
        by_t.setdefault(float(day.day_minutes), [])
        a = self._prior()[:, :, None] * np.r_[1.0, 1.0, np.ones(K), np.zeros(K)][None, None, :]
        t_prev, log_z = -PRE_MIN, 0.0
        for t in sorted(by_t):
            a = self._step(a, t - t_prev)
            t_prev = t
            events = list(by_t[t])
            if t in speci:
                events.append(("speci", speci[t]))
            dense_x = dense_at.get(t)
            if not events and dense_x is None:
                continue
            cuts = self._cuts(t, events)
            P = self._piece_matrix(cuts, dense_x, t)  # (J, Jd, pieces)
            # forward predictive of each mark's report interval, for branch normalisation
            norms: dict[int, float] = {}
            c0 = a[:, :, 0]
            total = float(c0.sum())
            base = self.base(t)
            for kind, payload in events:
                if kind == "mark":
                    k = payload[0]
                    lo, hi = k + self.lo_off - base, k + self.hi_off - base
                    e = self.r.edges
                    frac = np.clip((np.minimum(e[1:], hi) - np.maximum(e[:-1], lo)) / self.r.h, 0.0, 1.0)
                    norms[k] = float((c0 * frac[:, None]).sum()) / total if total > 0 else 0.0
            # a point inside each piece (its factor value is constant there)
            mids = [cuts[i + 1] - 1.0 if math.isinf(cuts[i]) else cuts[i] + 1.0 if math.isinf(cuts[i + 1])
                    else 0.5 * (cuts[i] + cuts[i + 1]) for i in range(cuts.size - 1)]
            vals = [self._events_value(float(x), t, events, norms) for x in mids]
            ev = np.asarray([v.ev for v in vals])
            emp = np.asarray([v.emp for v in vals])
            S = np.asarray([v.s for v in vals]).reshape(len(vals), K)
            C = np.asarray([v.c for v in vals]).reshape(len(vals), K)
            f_ev, f_emp = P @ ev, P @ emp
            f_s, f_c = P @ S, P @ C
            new = np.empty_like(a)
            new[:, :, 0] = a[:, :, 0] * f_ev
            new[:, :, 1] = a[:, :, 1] * f_emp
            new[:, :, 2:2 + K] = a[:, :, 2:2 + K] * f_s
            new[:, :, 2 + K:] = a[:, :, 2 + K:] * f_ev[:, :, None] + a[:, :, 2:2 + K] * f_c
            z = float(new[:, :, 0].sum())
            if not (z > 0 and math.isfinite(z)):
                raise ValueError("DAY0_DENSE_EVIDENCE_IMPOSSIBLE")
            a = new / z
            log_z += math.log(z)
        tot = a.sum(axis=(0, 1))
        c0 = tot[0]
        return tot[2:2 + K] / c0, tot[2 + K:] / c0, float(tot[1] / c0), log_z


def _columns(model: DenseModel, day: DenseDay, thresholds: Sequence[int], *, preimage, cell, outage: bool):
    """(S, X, E, log evidence) integrated over the static offset."""
    if model.noise is not None:
        model = DenseModel(model.latent, model.noise.folded(), model.mean)
    m = mean_path(day.forecast, day.hour, model.mean)
    s2a = model.latent.s2_static
    if s2a <= 0:
        return _Recursion(model, day, m, thresholds, 0.0, preimage, cell, outage).run()
    nodes, weights = np.polynomial.hermite_e.hermegauss(STATIC_QUADRATURE_NODES)
    weights = weights / weights.sum()
    sda = math.sqrt(s2a)
    out, logs = [], []
    for node, w in zip(nodes, weights):
        s, x, e, lz = _Recursion(model, day, m, thresholds, float(node) * sda, preimage, cell, outage).run()
        out.append((s, x, e))
        logs.append(lz + math.log(w))
    logs_ = np.asarray(logs)
    top = logs_.max()
    post = np.exp(logs_ - top)
    total = post.sum()
    post /= total
    return (post @ np.asarray([o[0] for o in out]), post @ np.asarray([o[1] for o in out]),
            float(post @ np.asarray([o[2] for o in out])), float(top + math.log(total)))


def extreme_columns(model: DenseModel, day: DenseDay, thresholds: Sequence[int], *,
                    preimage: tuple[float, float] = (-0.5, 0.5), cell: float = CELL_C):
    """(S_k, X_k, E, w_outage): HIGH S_k = P(no tape value > k), LOW S_k = P(no tape value < k);
    X_k the complement computed directly; E = P(empty tape); w_outage the posterior P(Z = 1)."""
    s0, x0, e0, l0 = _columns(model, day, thresholds, preimage=preimage, cell=cell, outage=False)
    if not day.marks or day.outage_prior <= 0.0:
        return s0, x0, e0, 0.0
    s1, x1, e1, l1 = _columns(model, day, thresholds, preimage=preimage, cell=cell, outage=True)
    a0, a1 = math.log1p(-day.outage_prior) + l0, math.log(day.outage_prior) + l1
    w = 1.0 / (1.0 + math.exp(a0 - a1)) if a1 > a0 else math.exp(a1 - a0) / (1.0 + math.exp(a1 - a0))
    return (1 - w) * s0 + w * s1, (1 - w) * x0 + w * x1, (1 - w) * e0 + w * e1, w


def extreme_cdf(model: DenseModel, day: DenseDay, thresholds: Sequence[int], *,
                preimage: tuple[float, float] = (-0.5, 0.5), cell: float = CELL_C) -> np.ndarray:
    """HIGH P(H <= k or empty), LOW P(L >= k or empty), for each threshold k."""
    s, _, _, _ = extreme_columns(model, day, thresholds, preimage=preimage, cell=cell)
    return np.clip(s, 0.0, 1.0)


def semantic_support(day: DenseDay, bins: Sequence[tuple[float | None, float | None]]) -> tuple[bool, ...]:
    """Per bin, whether the settlement page's own rows still allow it (the semantic certificate).

    False only where a received page row excludes the bin.  This is separate from statistical
    confidence: a probability that underflows under the model is not a semantic zero."""
    boundary = day.boundary_absorbing
    out = []
    for low, high in bins:
        if boundary is None:
            out.append(True)
        elif day.metric == "high":
            out.append(high is None or int(round(high)) >= boundary)
        else:
            out.append(low is None or int(round(low)) <= boundary)
    return tuple(out)


def bin_probabilities(model: DenseModel, day: DenseDay, bins: Sequence[tuple[float | None, float | None]], *,
                      preimage: tuple[float, float] = (-0.5, 0.5), cell: float = CELL_C) -> np.ndarray:
    """Settlement-bin probabilities for an ordered integer partition with open shoulders.

    Each bin is read from whichever column pair avoids cancellation; the empty tape goes to the
    lowest bracket; bins the page's extreme excludes are zero by indicator."""
    ks = thresholds_for(day.metric, bins)
    S, X, E, _ = extreme_columns(model, day, ks, preimage=preimage, cell=cell)
    s, x = dict(zip(ks, S.tolist())), dict(zip(ks, X.tolist()))
    allowed = semantic_support(day, bins)
    lowest = min(range(len(bins)), key=lambda i: -math.inf if bins[i][0] is None else bins[i][0])
    q = np.zeros(len(bins))
    for i, (low, high_) in enumerate(bins):
        if day.metric == "high":
            lo_k = None if low is None else int(round(low)) - 1
            hi_k = None if high_ is None else int(round(high_))
            if lo_k is None:
                val = s[hi_k] - E          # P(H <= hi, non-empty)
            elif hi_k is None:
                val = x[lo_k]              # P(H > lo - 1)
            else:
                val = x[lo_k] - x[hi_k] if x[lo_k] < 0.5 else s[hi_k] - s[lo_k]
        else:
            lo_k = None if low is None else int(round(low))
            hi_k = None if high_ is None else int(round(high_)) + 1
            if lo_k is None:
                val = x[hi_k]              # P(L < hi + 1)
            elif hi_k is None:
                val = s[lo_k] - E          # P(L >= lo, non-empty)
            else:
                val = x[hi_k] - x[lo_k] if x[hi_k] < 0.5 else s[lo_k] - s[hi_k]
        if i == lowest:
            val += E                       # no-data clause: the empty tape resolves to the lowest bracket
        q[i] = max(val, 0.0) if allowed[i] else 0.0
    total = q.sum()
    if not total > 0:
        raise ValueError("DAY0_DENSE_BIN_TOPOLOGY_INVALID")
    return q / total
