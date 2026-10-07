# Created: 2026-10-07
# Last reused/audited: 2026-10-07
# Lifecycle: created=2026-10-07; last_reviewed=2026-10-07; last_reused=2026-10-07
# Purpose: Pin the dense-observation Day0 state-space operator: exactness against a Monte
#   Carlo oracle, page-only semantic zeros, provisional rows as retention marks, dense
#   readings as evidence only, LOW recursion, DST day lengths and negative half-up rounding.
# Reuse: Pure operator tests; synthetic days only, no DB or network.
from __future__ import annotations

import math

import numpy as np
import pytest

from src.contracts.settlement_semantics import SettlementSemantics
from src.data import day0_dense_state_space as ds

D = 1440.0
GRID = ds.grid_minutes(D)
HOUR = ((GRID % 1440) // 60).astype(int)
FLAT_MEAN = ds.DenseMean(tuple([0.0] * 24), 0.0)
SCHED = tuple(float(t) for t in range(0, 1440) if t % 60 in (20, 50))
SEM = SettlementSemantics(resolution_source="t", measurement_unit="C", precision=1.0,
                          rounding_rule="wmo_half_up", finalization_time="12:00:00Z")


def diurnal(center: float = 15.0, amp: float = 6.0) -> np.ndarray:
    return center + amp * np.sin(2 * math.pi * ((GRID % 1440) / 1440 - 0.375))


def R(v):
    return SEM.round_values(np.asarray(v, float))


def bins_around(lo: int, hi: int) -> list[tuple[float | None, float | None]]:
    return [(None, float(lo - 1))] + [(float(k), float(k)) for k in range(lo, hi + 1)] + [(float(hi + 1), None)]


def model(noise=None, tau=200.0, s2=1.5, s2_static=0.0, speci=0.0):
    return ds.DenseModel(ds.DenseLatent(tau, s2, s2_static), noise, FLAT_MEAN, speci)


NOISE = ds.DenseNoise(tuple([0.0] * 24), 0.1, 0.4, 0.2)


def mc_oracle(m: ds.DenseModel, day: ds.DenseDay, ks, n=200_000, seed=0):
    """Sequential importance resampling of the same joint law (one-scale, no drift, no SPECI).

    Particles are resampled on each evidence factor; each threshold column carries its own
    relative factor (no-cross indicator, or a provisional row's allow-ratio) through resampling."""
    rng = np.random.default_rng(seed)
    f = ds.mean_path(day.forecast, day.hour, m.mean)
    lat = m.latent
    events = sorted([(t, 0, (lo, hi)) for t, lo, hi in day.page]
                    + [(t, 1, (lo, hi, s)) for t, lo, hi, s in day.provisional]
                    + ([(t, 2, (x,)) for t, x in day.dense] if m.noise is not None else [])
                    + [(t, 3, ()) for t in day.pending], key=lambda e: (e[0], e[1]))
    r = math.sqrt(lat.s2) * rng.standard_normal(n)
    t_prev = -ds.PRE_MIN
    ks = np.asarray(ks, float)
    col = np.ones((n, ks.size))
    high = day.metric == "high"

    def resample(w):
        c = np.cumsum(w / w.sum())
        idx = np.minimum(np.searchsorted(c, (rng.random() + np.arange(n)) / n), n - 1)
        return idx

    for t, kind, p in events:
        phi = math.exp(-(t - t_prev) / lat.tau)
        r = phi * r + math.sqrt(lat.s2 * (1 - phi * phi)) * rng.standard_normal(n)
        t_prev = t
        T = f[ds._slot(t)] + r
        if kind in (0, 1):
            inside = ((T >= p[0] - 0.5) & (T < p[1] + 0.5)).astype(float)
            allow = ((ks >= p[1]) if high else (ks <= p[0])).astype(float)
            s = 1.0 if kind == 0 else p[2]
            w = s * inside + (1 - s)
            v = R(T)
            own = ((v[:, None] <= ks[None, :]) if high else (v[:, None] >= ks[None, :])).astype(float)
            ratio = np.divide(s * inside[:, None] * allow[None, :] + (1 - s) * own, w[:, None],
                              out=np.zeros((n, ks.size)), where=w[:, None] > 0)
            idx = resample(w)
            r, col = r[idx], (col * ratio)[idx]
        elif kind == 2:
            nz = m.noise
            c = p[0] + nz.b_hour[day.hour[ds._slot(t)]] - f[ds._slot(t)]
            w = sum(wt * (_ndtr((c + nz.quantum / 2 - r) / s) - _ndtr((c - nz.quantum / 2 - r) / s))
                    for wt, s in ((1 - nz.pi, nz.s1), (nz.pi, nz.s2)) if wt > 0)
            idx = resample(w)
            r, col = r[idx], col[idx]
        else:
            v = R(T)
            col *= ((v[:, None] <= ks[None, :]) if high else (v[:, None] >= ks[None, :])).astype(float)
    return col.mean(0)


def _ndtr(z):
    from scipy.special import ndtr
    return ndtr(z)


def day_of(metric="high", page=(), provisional=(), dense=(), schedule=SCHED, now=13 * 60 + 5, f=None):
    return ds.build_day(metric=metric, day_minutes=D, forecast=diurnal() if f is None else f, hour=HOUR,
                        page=page, provisional=provisional, dense=dense, schedule=schedule, speci_from=now)


# ---------------------------------------------------------------- exactness

@pytest.mark.parametrize("metric", ["high", "low"])
def test_recursion_matches_monte_carlo_oracle(metric):
    f = diurnal()
    now = 13 * 60 + 5
    rng = np.random.default_rng(3)
    prov = [(t, int(R(f[ds._slot(t)] + 0.3 * rng.standard_normal())), 0.95) for t in SCHED if t < now - 60]
    dense = [(float(t), round(float(f[ds._slot(t)] + 0.4 * rng.standard_normal()), 1)) for t in range(-180, now, 10)]
    day = day_of(metric, provisional=prov, dense=dense, f=f, now=now)
    m = model(NOISE)
    ks = list(range(int(f.min()) - 3, int(f.max()) + 5))
    exact = ds.extreme_cdf(m, day, ks)
    oracle = mc_oracle(m, day, ks)
    assert np.max(np.abs(exact - oracle)) < 0.012


def test_cell_refinement_is_stable():
    f = diurnal()
    dense = [(float(t), round(float(f[ds._slot(t)]), 1)) for t in range(0, 600, 10)]
    day = day_of(dense=dense, f=f, now=605)
    ks = list(range(12, 26))
    coarse = ds.extreme_cdf(model(NOISE), day, ks, cell=0.05)
    fine = ds.extreme_cdf(model(NOISE), day, ks, cell=0.025)
    assert np.max(np.abs(coarse - fine)) < 2e-3


def test_independent_pending_instants_match_closed_form():
    """Very short tau makes pending instants independent: G(k) = prod Phi((k + 1/2 - m_t) / s)."""
    f = diurnal()
    day = day_of(schedule=(600.0, 900.0, 1200.0), now=0)
    m = model(tau=0.5, s2=1.0)
    ks = list(range(12, 25))
    exact = ds.extreme_cdf(m, day, ks)
    from scipy.stats import norm
    ref = [np.prod([norm.cdf(k + 0.5 - f[ds._slot(t)]) for t in (600, 900, 1200)]) for k in ks]
    assert np.max(np.abs(exact - np.asarray(ref))) < 2e-3


# ---------------------------------------------------------------- semantic support

def test_page_row_is_the_only_structural_zero_high():
    f = diurnal()
    day = day_of(page=[(600.0, 25)], f=f, now=615)
    q = ds.bin_probabilities(model(NOISE), day, bins_around(18, 28))
    labels = [b for b in bins_around(18, 28)]
    for (low, high), p in zip(labels, q):
        if high is not None and high < 25:
            assert p == 0.0
    assert q.sum() == pytest.approx(1.0)


def test_page_row_is_the_only_structural_zero_low():
    day = day_of("low", page=[(300.0, 7)], now=315)
    q = ds.bin_probabilities(model(NOISE), day, bins_around(2, 12))
    for (low, high), p in zip(bins_around(2, 12), q):
        if low is not None and low > 7:
            assert p == 0.0


HALF_HOURLY = tuple(float(t) for t in range(0, 1440, 30))


def test_provisional_gross_row_never_creates_a_structural_zero():
    """Lucknow 2026-09-06 HIGH shape: mirrors carry 37 at 13:00 local (07:30Z); the page keeps
    30/31 around it and settled 31.  The isolated gross row must not zero any bin below 37 and
    must leave q(31) where the page and forecast put it."""
    f = diurnal(center=27.5, amp=3.5)  # peaks 31 at 15:00 local
    now = 13 * 60 + 5
    page = [(t, int(R(f[ds._slot(t)]))) for t in HALF_HOURLY if t < 13 * 60]
    bins = bins_around(28, 38)
    with_row = day_of(page=page, provisional=[(780.0, 37, 0.97)], schedule=HALF_HOURLY, f=f, now=now)
    without = day_of(page=page, schedule=HALF_HOURLY, f=f, now=now)
    q = ds.bin_probabilities(model(NOISE), with_row, bins)
    q_ref = ds.bin_probabilities(model(NOISE), without, bins)
    page_max = with_row.boundary_absorbing
    assert all(p > 0.0 for (low, high), p in zip(bins, q)
               if high is not None and page_max <= high < 37)
    i31 = bins.index((31.0, 31.0))
    assert q[i31] > 0.3
    assert q[i31] == pytest.approx(q_ref[i31], rel=1e-3)


def test_provisional_row_shifts_mass_strongly_but_not_to_zero():
    """Tokyo 760918 shape: a fast route's 25.2 at a METAR instant (06:00Z = 15:00 local), no page
    row yet.  Bins <= 24 lose most of their mass but none is structurally zero."""
    f = diurnal(center=22.0, amp=2.0)
    now = 15 * 60 + 5
    rows = [(t, int(R(f[ds._slot(t)]))) for t in HALF_HOURLY if t < 15 * 60]
    day_none = day_of(provisional=[(t, k, 0.97) for t, k in rows], schedule=HALF_HOURLY, f=f, now=now)
    day_row = day_of(provisional=[(t, k, 0.97) for t, k in rows] + [(900.0, 25, 0.97)],
                     schedule=HALF_HOURLY, f=f, now=now)
    bins = bins_around(20, 27)
    q0 = ds.bin_probabilities(model(NOISE), day_none, bins)
    q1 = ds.bin_probabilities(model(NOISE), day_row, bins)
    below = [i for i, (low, high) in enumerate(bins) if high is not None and high <= 24]
    assert q0[below].sum() > 0.3
    assert q1[below].sum() < 0.25 * q0[below].sum()
    assert all(q1[i] > 0.0 for i in below)
    page_day = day_of(page=[(900.0, 25)], schedule=HALF_HOURLY, f=f, now=now)
    q2 = ds.bin_probabilities(model(NOISE), page_day, bins)
    assert all(q2[i] == 0.0 for i in below)


def test_dense_reading_never_sets_a_boundary():
    f = diurnal(center=21.0, amp=3.0)
    day = day_of(dense=[(370.0, 25.4)], f=f, now=6 * 60 + 15)
    q = ds.bin_probabilities(model(NOISE), day, bins_around(20, 27))
    assert all(p > 0.0 for p in q)


def test_page_supersedes_and_resolves_provisional_rows():
    day = ds.build_day(metric="high", day_minutes=D, forecast=diurnal(), hour=HOUR,
                       page=[(560.0, 19), (620.0, 20)], provisional=[(560.0, 25, 0.9), (590.0, 30, 0.9), (650.0, 21, 0.9)],
                       dense=(), schedule=SCHED, speci_from=660.0)
    assert [p[0] for p in day.provisional] == [650.0]
    assert 590.0 not in day.pending and 680.0 in day.pending


# ---------------------------------------------------------------- reductions and geometry

def test_absent_dense_equals_metar_only_marginal():
    day = day_of(provisional=[(560.0, 20, 0.95)], now=600)
    ks = list(range(15, 26))
    assert np.array_equal(ds.extreme_cdf(model(None), day, ks), ds.extreme_cdf(model(NOISE), day, ks))


@pytest.mark.parametrize("minutes", [1380.0, 1500.0])
def test_dst_day_lengths(minutes):
    grid = ds.grid_minutes(minutes)
    f = 15 + 0 * grid
    hour = ((grid % 1440) // 60).astype(int) % 24
    sched = [float(t) for t in range(0, int(minutes)) if t % 60 in (20, 50)]
    day = ds.build_day(metric="high", day_minutes=minutes, forecast=f, hour=hour, page=(), provisional=(),
                       dense=(), schedule=sched + [minutes + 20.0], speci_from=0.0)
    assert max(day.pending) < minutes and len(day.pending) == len(sched)
    q = ds.bin_probabilities(model(), day, bins_around(12, 20))
    assert q.sum() == pytest.approx(1.0)


def test_negative_half_up_preimage():
    """R(-1.5) = -1: an instant fixed at mean -1.5 settles <= -2 with probability 1/2."""
    f = np.full(GRID.size, -1.5)
    day = day_of(schedule=(600.0,), now=0, f=f)
    g = ds.extreme_cdf(model(tau=0.5, s2=0.01), day, [-2, -1])
    assert g[0] == pytest.approx(0.5, abs=0.02)
    assert g[1] == pytest.approx(1.0, abs=1e-6)


def test_speci_hazard_only_widens_the_upper_tail():
    day = day_of(page=[(t, 15) for t in SCHED if t < 600], now=605)
    bins = bins_around(14, 24)
    q0 = ds.bin_probabilities(model(NOISE), day, bins)
    q1 = ds.bin_probabilities(model(NOISE, speci=1 / 60), day, bins)
    cdf0, cdf1 = np.cumsum(q0), np.cumsum(q1)
    assert np.all(cdf1 <= cdf0 + 1e-12)


def test_static_offset_quadrature_runs_and_normalises():
    day = day_of(dense=[(float(t), 18.0) for t in range(0, 600, 10)], now=605)
    q = ds.bin_probabilities(model(NOISE, s2_static=0.07), day, bins_around(14, 26))
    assert q.sum() == pytest.approx(1.0) and np.all(q >= 0)


def test_matches_backtest_grid_oracle_on_identical_inputs():
    """The shipped operator reproduces the backtest's exact grid oracle (state_space.grid_oracle,
    bc024fedd) on the same day: one-scale OU, exact METAR cells, Gaussian dense likelihood."""
    import sys
    from pathlib import Path

    here = Path(__file__).resolve().parents[1] / "artifacts" / "fast_obs_audit" / "dense_station_model"
    sys.path.insert(0, str(here))
    try:
        import state_space as bss
        from ss_engine import Day
    finally:
        sys.path.remove(str(here))
    f = diurnal()
    now = 12 * 60 + 5
    rng = np.random.default_rng(11)
    metar = [(t, int(R(f[ds._slot(t)] + 0.3 * rng.standard_normal()))) for t in SCHED if t < now - 30]
    dense = [(float(t), round(float(f[ds._slot(t)] + 0.3 * rng.standard_normal()), 1)) for t in range(0, now - 30, 10)]
    sd = 0.3
    m = ds.DenseModel(ds.DenseLatent(200.0, 1.5), ds.DenseNoise(tuple([0.0] * 24), sd, sd, 0.0, 1e-6), FLAT_MEAN)
    day = day_of(page=metar, dense=dense, f=f, now=now - 30)
    bins = bins_around(14, 24)
    q = ds.bin_probabilities(m, day, bins, cell=0.0125)
    bday = Day(D, f, None, HOUR, [t for t, _ in metar], [k for _, k in metar], list(SCHED),
               [t for t, _ in dense], [x for _, x in dense], [0.0] * len(dense), fmu=f)
    ks, p = bss.grid_oracle(bday, 200.0, 1.5, sd ** 2, now - 30, K=1601)["high"]
    pk = dict(zip(ks.tolist(), p.tolist()))
    ref = []
    for low, high in bins:
        lo = -10 ** 6 if low is None else int(low)
        hi = 10 ** 6 if high is None else int(high)
        ref.append(sum(v for k, v in pk.items() if lo <= k <= hi))
    assert np.max(np.abs(q - np.asarray(ref))) < 1e-3  # measured 2.6e-4


def test_partial_page_fetch_does_not_resolve_earlier_instants():
    """Helsinki 2026-10-07: the intraday page fetch returned 17:20-19:50 local only; the morning's
    mirrored 14 C METARs (13:50-16:50 local) were outside it and must stay on the tape."""
    f = diurnal(center=11.0, amp=3.0)
    morning = [(t, 14 if 13 * 60 <= t else int(R(f[ds._slot(t)])), 0.99) for t in SCHED if t <= 16 * 60 + 50]
    page = [(t, 13) for t in SCHED if 17 * 60 <= t <= 19 * 60 + 50]
    day = ds.build_day(metric="high", day_minutes=D, forecast=f, hour=HOUR, page=page, provisional=morning,
                       dense=(), schedule=SCHED, speci_from=20 * 60.0, page_windows=((17 * 60 + 20.0, 19 * 60 + 50.0),))
    assert len(day.provisional) == len(morning)
    q = dict(zip(bins_around(10, 16), ds.bin_probabilities(model(NOISE), day, bins_around(10, 16))))
    assert q[(14.0, 14.0)] > 0.9
    covered = ds.build_day(metric="high", day_minutes=D, forecast=f, hour=HOUR, page=page, provisional=morning,
                           dense=(), schedule=SCHED, speci_from=20 * 60.0, page_windows=((0.0, 19 * 60 + 50.0),))
    assert covered.provisional == ()  # a fetch spanning them resolves them as dropped
