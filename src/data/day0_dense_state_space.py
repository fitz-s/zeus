# Created: 2026-10-07
# Last reused or audited: 2026-10-07
# Authority basis: docs/authority/day0_dense_observation_probability.md (PR #536 theory);
#   artifacts/fast_obs_audit/dense_station_model/METHOD.md §5 (A/B backtest: B usable for
#   Tokyo, Helsinki, Singapore); operator 2026-10-07 (live, no shadow, legacy when absent);
#   coordinator correction 2026-10-07 (B_A from the settlement page only; Lucknow 2026-09-06).
"""Dense-observation state-space law for the settled Day0 extreme (pure; no IO).

Random variable.  S is the day's settlement tape: page rows over local instants.
H = max_{t in S} R(T_t), L = min_{t in S} R(T_t), with R the contract rounding.

Evidence classes at a decision (all receipt-gated by the caller):
  page row (t, k)           settlement-product row.  Exact: T_t in R^-1(k), and k joins the extreme.
                            The only semantic certificate: bins it excludes are structurally 0.
  provisional row (t, k, s) METAR integer from a mirror (AWC, Ogimet, a fast-admission route at a
                            METAR instant) that the page has not yet resolved.  With probability s
                            the page retains it (then T_t in R^-1(k) and k joins the extreme);
                            otherwise the page removes or corrects it, and the instant settles on
                            the page's own R(T_t), as if pending.  The mark
                            s 1{T in R^-1(k)} 1{k does not cross} + (1 - s) 1{R(T) does not cross}
                            (evidence column: s 1{T in R^-1(k)} + 1 - s) is the exact marginal for
                            independent per-row retention.  It never creates a 0/1.
  context row (t, k)        METAR integer outside the local day (the pre-midnight window): exact
                            interval evidence about T_t with no role in the extreme.
  pending instant t         scheduled routine instant with no row of either class: R(T_t) joins.
  SPECI hazard              Poisson rate lambda from ``speci_from`` to the day end: R(T_t) joins.
  dense reading (t, x)      quantised national-station value: likelihood only, never a boundary.

Latent temperature, on minutes from local midnight (window starts PRE_MIN earlier):
    T_t = m_t + a + r_t,   m_t = f_t + mu(h_t) + beta (f_t - S_4h f_t)
  a     static day offset ~ N(0, s2_static); zero variance is the one-scale model
  r     OU (tau, s2), exact transitions over any gap
  d     optional OU dense-error drift (tau_e, sd2)
Dense observation:  T_t + d_t + w in [x + b(h_t) - q/2, x + b(h_t) + q/2),
                    w ~ (1 - pi) N(0, s1^2) + pi N(0, s2^2).

Inference is a forward recursion on residual cells.  Within each cell, mass is uniform:
transitions, interval likelihoods and threshold kills are exact cell averages.  The cell
width is the one discretisation, and the tests bound its effect by refinement and against
importance-weighted Monte Carlo.  The static offset is integrated by Gauss-Hermite
quadrature, weighted by each node's evidence.  K threshold columns carry
G(k) = P(no tape value crosses k | evidence); an evidence column normalises them.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

import numpy as np
from scipy import sparse, special

GRID_MIN = 5.0
PRE_MIN = 180.0
SMOOTH_POINTS = 49  # centred 4 h running mean on the 5-min grid
CELL_C = 0.05
AXIS_HALF_WIDTH_SD = 7.0
AXIS_SNAP_C = 1.0
DRIFT_HALF_WIDTH_SD = 5.5
DRIFT_CELL_C = 0.1
STATIC_QUADRATURE_NODES = 9
DRIFT_FOLD_RATIO = 0.1  # sd2 below this share of the core variance folds into the white core
TRANSITION_DROP = 1e-15


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
    s2_static: float = 0.0  # static day offset variance (two-scale model, slow scale -> infinity)

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
        """Fold a negligible drift into the white core, variance conserved."""
        if self.sd2 == 0.0 or self.sd2 >= DRIFT_FOLD_RATIO * self.s1 ** 2:
            return self
        core = math.sqrt(self.s1 ** 2 + self.sd2)
        return DenseNoise(self.b_hour, core, max(self.s2, core), self.pi, self.quantum, self.tau_e, 0.0)


@dataclass(frozen=True)
class DenseModel:
    latent: DenseLatent
    noise: DenseNoise | None   # None: no dense channel (METAR-content observation only)
    mean: DenseMean
    speci_rate_per_min: float = 0.0

    def __post_init__(self) -> None:
        if not (self.speci_rate_per_min >= 0 and math.isfinite(self.speci_rate_per_min)):
            raise ValueError("DAY0_DENSE_SPECI_RATE_INVALID")


@dataclass(frozen=True)
class DenseDay:
    """One local day's admitted evidence at one decision; times are minutes from local midnight.

    ``forecast`` and ``hour`` are on ``grid_minutes(day_minutes)``.  Rows at the same 5-min slot
    and of the same class are merged by ``build_day`` (hull of their integers)."""

    metric: str
    day_minutes: float
    forecast: tuple[float, ...]
    hour: tuple[int, ...]
    page: tuple[tuple[float, int, int], ...]
    provisional: tuple[tuple[float, int, int, float], ...]
    dense: tuple[tuple[float, float], ...]
    pending: tuple[float, ...]
    speci_from: float
    context: tuple[tuple[float, int, int], ...] = ()

    def __post_init__(self) -> None:
        n = grid_minutes(self.day_minutes).size
        if (self.metric not in {"high", "low"} or len(self.forecast) != n or len(self.hour) != n
                or not np.isfinite(np.asarray(self.forecast, float)).all()
                or any(not 0 <= h < 24 for h in self.hour)
                or any(not 0 <= t < self.day_minutes for t in self.pending)
                or any(not 0 <= t < self.day_minutes for t, *_ in (*self.page, *self.provisional))
                or any(not -PRE_MIN <= t <= self.day_minutes for t, _ in self.dense)
                or any(not -PRE_MIN <= t < 0 for t, _, _ in self.context)
                or any(not 0.0 < s <= 1.0 for *_, s in self.provisional)
                or not -PRE_MIN <= self.speci_from <= self.day_minutes):
            raise ValueError("DAY0_DENSE_DAY_INVALID")

    @property
    def boundary_absorbing(self) -> int | None:
        """The page's own extreme: the semantic certificate."""
        if not self.page:
            return None
        return max(k_hi for _, _, k_hi in self.page) if self.metric == "high" else min(k_lo for _, k_lo, _ in self.page)


def grid_minutes(day_minutes: float) -> np.ndarray:
    return np.arange(-PRE_MIN, day_minutes + GRID_MIN / 2, GRID_MIN)


def smooth_4h(f: np.ndarray) -> np.ndarray:
    w = SMOOTH_POINTS
    pad = np.r_[np.full(w // 2, f[0]), f, np.full(w // 2, f[-1])]
    return np.convolve(pad, np.ones(w) / w, mode="valid")[: f.size]


def mean_path(forecast: Sequence[float], hour: Sequence[int], mean: DenseMean) -> np.ndarray:
    f = np.asarray(forecast, float)
    return f + np.asarray(mean.mu_hour, float)[np.asarray(hour, int)] + mean.beta * (f - smooth_4h(f))


def _slot(minute: float) -> int:
    return int(round((minute + PRE_MIN) / GRID_MIN))


# ---------------------------------------------------------------- stable cell averages

def _psi_neg(z: np.ndarray) -> np.ndarray:
    """psi(z) = z Phi(z) + phi(z) on z <= 0 (the antiderivative of Phi)."""
    return z * special.ndtr(z) + np.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def _psi_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """psi(a) - psi(b) without cancellation, via psi(z) = max(z, 0) + psi(-|z|)."""
    return (np.maximum(a, 0.0) - np.maximum(b, 0.0)) + (_psi_neg(-np.abs(a)) - _psi_neg(-np.abs(b)))


def _avg_cdf(edge, lo, hi, scale: float, sd: float) -> np.ndarray:
    """Mean of Phi((edge - scale r) / sd) over r uniform on [lo, hi); infinite edges are exact."""
    edge, lo, hi = np.broadcast_arrays(np.asarray(edge, float), np.asarray(lo, float), np.asarray(hi, float))
    out = np.where(edge == np.inf, 1.0, 0.0)
    fin = np.isfinite(edge)
    if fin.any():
        e, l_, h_ = edge[fin], lo[fin], hi[fin]
        spread = (h_ - l_) * scale
        flat = spread < 1e-7 * sd  # the cell maps to a point: the average is Phi at its image
        val = special.ndtr((e - scale * 0.5 * (l_ + h_)) / sd)
        wide = ~flat
        val[wide] = (sd / spread[wide]) * _psi_diff((e[wide] - scale * l_[wide]) / sd,
                                                     (e[wide] - scale * h_[wide]) / sd)
        out[fin] = val
    return np.clip(out, 0.0, 1.0)


@dataclass(frozen=True)
class _Axis:
    lo: float
    h: float
    n: int

    @property
    def edges(self) -> np.ndarray:
        return self.lo + self.h * np.arange(self.n + 1)

    @property
    def centers(self) -> np.ndarray:
        return self.lo + self.h * (np.arange(self.n) + 0.5)


_TRANSITIONS: dict[tuple, sparse.csr_matrix] = {}


def _transition(axis: _Axis, tau: float, s2: float, dt: float) -> sparse.csr_matrix:
    """Transposed OU cell kernel: (T^T)[j, i] = P(cell j at t + dt | uniform in cell i at t)."""
    key = (axis, tau, s2, round(dt, 6))
    cached = _TRANSITIONS.get(key)
    if cached is not None:
        return cached
    phi = math.exp(-dt / tau)
    sd = max(math.sqrt(s2 * (1.0 - phi * phi)), 1e-12)
    e = axis.edges.copy()
    e[0], e[-1] = -np.inf, np.inf
    cdf = _avg_cdf(e[None, :], axis.edges[:-1, None], axis.edges[1:, None], phi, sd)
    T = np.clip(np.diff(cdf, axis=1), 0.0, None)
    T[T < TRANSITION_DROP] = 0.0
    T /= T.sum(axis=1, keepdims=True)
    out = sparse.csr_matrix(T.T)
    if len(_TRANSITIONS) > 512:
        _TRANSITIONS.clear()
    _TRANSITIONS[key] = out
    return out


def _overlap(axis: _Axis, lo: float, hi: float) -> np.ndarray:
    """Share of each cell inside [lo, hi)."""
    e = axis.edges
    return np.clip((np.minimum(e[1:], hi) - np.maximum(e[:-1], lo)) / axis.h, 0.0, 1.0)


def thresholds_for(metric: str, bins: Sequence[tuple[float | None, float | None]]) -> list[int]:
    """Integer thresholds whose G(k) the bin partition needs."""
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


# ---------------------------------------------------------------- forward recursion

_CONTEXT, _PAGE, _PROVISIONAL, _DENSE, _PENDING, _SPECI, _END = range(7)


class _Recursion:
    """Forward recursion of one model at one static offset."""

    def __init__(self, model: DenseModel, day: DenseDay, m: np.ndarray, thresholds: Sequence[int],
                 offset: float, preimage: tuple[float, float], cell: float):
        self.model, self.day, self.m, self.off = model, day, m, offset
        self.lo_off, self.hi_off = preimage
        self.k = np.asarray(thresholds, float)
        lat = model.latent
        sd = math.sqrt(lat.s2)
        implied = [0.0]
        for t, k_lo, k_hi, *_ in (*day.page, *day.provisional, *day.context):
            base = m[_slot(t)] + offset
            implied += [k_lo + self.lo_off - base, k_hi + self.hi_off - base]
        implied += [x - m[_slot(t)] - offset for t, x in day.dense]
        half = max(AXIS_HALF_WIDTH_SD * sd, max(abs(v) for v in implied) + 1.5)
        half = AXIS_SNAP_C * math.ceil(half / AXIS_SNAP_C)
        self.r = _Axis(-half, cell, int(round(2 * half / cell)))
        noise = model.noise
        self.d: _Axis | None = None
        if noise is not None and noise.sd2 > 0:
            dh = DRIFT_CELL_C * math.ceil(DRIFT_HALF_WIDTH_SD * math.sqrt(noise.sd2) / DRIFT_CELL_C)
            self.d = _Axis(-dh, DRIFT_CELL_C, int(round(2 * dh / DRIFT_CELL_C)))

    def _prior(self) -> np.ndarray:
        pr = np.diff(special.ndtr(np.r_[-np.inf, self.r.edges[1:-1], np.inf] / math.sqrt(self.model.latent.s2)))
        if self.d is None:
            return pr[:, None, None]
        pd_ = np.diff(special.ndtr(np.r_[-np.inf, self.d.edges[1:-1], np.inf] / math.sqrt(self.model.noise.sd2)))
        return (pr[:, None] * pd_[None, :])[:, :, None]

    def _step(self, a: np.ndarray, dt: float) -> np.ndarray:
        if dt <= 1e-9:
            return a
        J, Jd, C = a.shape
        T = _transition(self.r, self.model.latent.tau, self.model.latent.s2, dt)
        a = (T @ a.reshape(J, Jd * C)).reshape(J, Jd, C)
        if self.d is not None:
            Td = _transition(self.d, self.model.noise.tau_e, self.model.noise.sd2, dt)
            a = np.ascontiguousarray(np.moveaxis((Td @ np.moveaxis(a, 1, 0).reshape(Jd, J * C)).reshape(Jd, J, C), 0, 1))
        return a

    def _dense_lik(self, t: float, x: float) -> np.ndarray:
        nz = self.model.noise
        g = _slot(t)
        c = x + nz.b_hour[self.day.hour[g]] - self.m[g] - self.off
        lo_r, hi_r = self.r.edges[:-1, None], self.r.edges[1:, None]
        shift = (np.zeros(1) if self.d is None else self.d.centers)[None, :]
        lik = np.zeros((self.r.n, shift.size))
        for w, s in ((1.0 - nz.pi, nz.s1), (nz.pi, nz.s2)):
            if w > 0:
                up = _avg_cdf(c + nz.quantum / 2 - shift, lo_r, hi_r, 1.0, s)
                dn = _avg_cdf(c - nz.quantum / 2 - shift, lo_r, hi_r, 1.0, s)
                lik += w * np.clip(up - dn, 0.0, None)
        return lik[:, :, None]

    def _interval(self, t: float, k_lo: int, k_hi: int) -> np.ndarray:
        base = self.m[_slot(t)] + self.off
        return _overlap(self.r, k_lo + self.lo_off - base, k_hi + self.hi_off - base)

    def _no_cross(self, t: float) -> np.ndarray:
        """(J, K): share of each cell whose report R(T_t) does not cross threshold k."""
        base = self.m[_slot(t)] + self.off
        e = self.r.edges
        if self.day.metric == "high":
            cut = self.k + self.hi_off - base
            return np.clip((cut[None, :] - e[:-1, None]) / self.r.h, 0.0, 1.0)
        cut = self.k + self.lo_off - base
        return np.clip((e[1:, None] - cut[None, :]) / self.r.h, 0.0, 1.0)

    def _row_allows(self, k_lo: int, k_hi: int) -> np.ndarray:
        """(K,): whether a tape value in [k_lo, k_hi] stays on the allowed side of each threshold."""
        return (self.k >= k_hi) if self.day.metric == "high" else (self.k <= k_lo)

    def run(self) -> tuple[np.ndarray, float]:
        """(G(k) per threshold, log evidence)."""
        day, K = self.day, self.k.size
        events: list[tuple[float, int, tuple]] = []
        events += [(float(t), _CONTEXT, (k_lo, k_hi)) for t, k_lo, k_hi in day.context]
        events += [(float(t), _PAGE, (k_lo, k_hi)) for t, k_lo, k_hi in day.page]
        events += [(float(t), _PROVISIONAL, (k_lo, k_hi, s)) for t, k_lo, k_hi, s in day.provisional]
        if self.model.noise is not None:
            events += [(float(t), _DENSE, (x,)) for t, x in day.dense]
        events += [(float(t), _PENDING, ()) for t in day.pending]
        lam = self.model.speci_rate_per_min
        if lam > 0:
            t = GRID_MIN * math.ceil(max(day.speci_from, 0.0) / GRID_MIN)
            while t < day.day_minutes:
                events.append((float(t), _SPECI, ()))
                t += GRID_MIN
        events.append((float(day.day_minutes), _END, ()))
        events.sort(key=lambda e: (e[0], e[1]))
        p_speci = 1.0 - math.exp(-lam * GRID_MIN)
        a = self._prior()
        t_prev, log_z = -PRE_MIN, 0.0

        def columns(a: np.ndarray) -> np.ndarray:
            return np.repeat(a, K + 1, axis=2) if a.shape[2] == 1 else a

        for t, kind, payload in events:
            a = self._step(a, t - t_prev)
            t_prev = t
            if kind == _DENSE:
                a = a * self._dense_lik(t, payload[0])
            elif kind == _CONTEXT:
                a = a * self._interval(t, *payload)[:, None, None]
            elif kind == _PAGE:
                a = columns(a) * self._interval(t, *payload)[:, None, None]
                a[:, :, 1:] *= self._row_allows(*payload)[None, None, :]
            elif kind == _PROVISIONAL:
                k_lo, k_hi, s = payload
                a = columns(a)
                inside = self._interval(t, k_lo, k_hi)[:, None, None]
                factor = np.empty((self.r.n, 1, K + 1))
                factor[:, :, :1] = s * inside + (1.0 - s)
                factor[:, :, 1:] = (s * inside * self._row_allows(k_lo, k_hi)[None, None, :]
                                    + (1.0 - s) * self._no_cross(t)[:, None, :])
                a = a * factor
            elif kind == _PENDING and K:
                a = columns(a)
                a[:, :, 1:] *= self._no_cross(t)[:, None, :]
            elif kind == _SPECI and K:
                a = columns(a)
                a[:, :, 1:] *= 1.0 - p_speci * (1.0 - self._no_cross(t))[:, None, :]
            z = float(a[:, :, 0].sum())
            if not (z > 0 and math.isfinite(z)):
                raise ValueError("DAY0_DENSE_EVIDENCE_IMPOSSIBLE")
            a = a / z
            log_z += math.log(z)
        if a.shape[2] == 1:
            return np.ones(K), log_z
        return np.clip(a[:, :, 1:].sum(axis=(0, 1)), 0.0, 1.0), log_z


def extreme_cdf(model: DenseModel, day: DenseDay, thresholds: Sequence[int], *,
                preimage: tuple[float, float] = (-0.5, 0.5), cell: float = CELL_C) -> np.ndarray:
    """G(k): HIGH P(H <= k | evidence), LOW P(L >= k | evidence), for each threshold k."""
    if model.noise is not None:
        model = DenseModel(model.latent, model.noise.folded(), model.mean, model.speci_rate_per_min)
    m = mean_path(day.forecast, day.hour, model.mean)
    s2a = model.latent.s2_static
    if s2a <= 0:
        return _Recursion(model, day, m, thresholds, 0.0, preimage, cell).run()[0]
    nodes, weights = np.polynomial.hermite_e.hermegauss(STATIC_QUADRATURE_NODES)
    sda = math.sqrt(s2a)
    gs, logs = [], []
    for node, w in zip(nodes, weights):
        g, lz = _Recursion(model, day, m, thresholds, float(node) * sda, preimage, cell).run()
        gs.append(g)
        logs.append(lz + math.log(w))
    logs_ = np.asarray(logs)
    post = np.exp(logs_ - logs_.max())
    return np.clip((post / post.sum()) @ np.asarray(gs), 0.0, 1.0)


def semantic_support(day: DenseDay, bins: Sequence[tuple[float | None, float | None]]) -> tuple[bool, ...]:
    """Per bin, whether the settlement page's own rows still allow it (the semantic certificate).

    False only where a received page row excludes the bin.  This is separate from statistical
    confidence: a probability that underflows to 0.0 under the model is not a semantic zero."""
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

    Bins the page's extreme excludes are zero by indicator, independent of floating point."""
    ks = thresholds_for(day.metric, bins)
    if not day.page and not day.provisional and not day.pending and model.speci_rate_per_min == 0.0:
        raise ValueError("DAY0_DENSE_NO_DATA_OUTCOME")
    G = dict(zip(ks, extreme_cdf(model, day, ks, preimage=preimage, cell=cell).tolist()))
    allowed = semantic_support(day, bins)
    high = day.metric == "high"
    q = np.zeros(len(bins))
    for i, (low, high_) in enumerate(bins):
        if high:
            top = 1.0 if high_ is None else G[int(round(high_))]
            bot = 0.0 if low is None else G[int(round(low)) - 1]
        else:
            top = 1.0 if low is None else G[int(round(low))]
            bot = 0.0 if high_ is None else G[int(round(high_)) + 1]
        q[i] = max(top - bot, 0.0) if allowed[i] else 0.0
    total = q.sum()
    if not total > 0:
        raise ValueError("DAY0_DENSE_BIN_TOPOLOGY_INVALID")
    return q / total


def build_day(*, metric: str, day_minutes: float, forecast: Iterable[float], hour: Iterable[int],
              page: Iterable[tuple[float, int]], provisional: Iterable[tuple[float, int, float]],
              dense: Iterable[tuple[float, float]], schedule: Iterable[float], speci_from: float,
              context: Iterable[tuple[float, int]] = ()) -> DenseDay:
    """Assemble a DenseDay at one decision.

    Same-slot rows of one class merge into the hull of their integers.  A provisional row is
    superseded by a page row at its slot and resolved-dropped when the page already has a later
    row.  A scheduled instant is pending only when it has no row of either class and the page
    has no later row (the page skipped it)."""

    def merge(rows):
        out: dict[int, list] = {}
        for t, k, *rest in rows:
            g = _slot(float(t))
            if g in out:
                cur = out[g]
                cur[1], cur[2] = min(cur[1], int(k)), max(cur[2], int(k))
                if rest:
                    cur[3] = min(cur[3], float(rest[0]))
            else:
                out[g] = [float(t), int(k), int(k), *(float(v) for v in rest)]
        return out

    page_slots = merge(page)
    context_slots = merge(context)
    last_page = max((v[0] for v in page_slots.values()), default=-math.inf)
    prov_slots = {g: v for g, v in merge(provisional).items() if g not in page_slots and v[0] > last_page}
    occupied = set(page_slots) | set(prov_slots)
    pending = tuple(sorted(float(t) for t in schedule
                           if 0 <= t < day_minutes and t > last_page and _slot(float(t)) not in occupied))
    return DenseDay(metric=metric, day_minutes=float(day_minutes), forecast=tuple(float(v) for v in forecast),
                    hour=tuple(int(h) for h in hour),
                    page=tuple(sorted((v[0], v[1], v[2]) for v in page_slots.values())),
                    provisional=tuple(sorted((v[0], v[1], v[2], v[3]) for v in prov_slots.values())),
                    dense=tuple(sorted((float(t), float(x)) for t, x in dense)), pending=pending,
                    speci_from=float(speci_from),
                    context=tuple(sorted((v[0], v[1], v[2]) for v in context_slots.values())))
