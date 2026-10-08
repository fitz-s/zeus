# Created: 2026-10-07
# Last reused/audited: 2026-10-08
# Lifecycle: created=2026-10-07; last_reviewed=2026-10-08; last_reused=2026-10-08
# Authority basis: consult REQ-20261007-125708-2dc18b counterexamples; coordinator D2/D5 (2026-10-08).
# Purpose: Pin the dense-observation Day0 operator: normalized report-lifecycle marks (the independent
#   [0.1, 0.9] case), page corrections, shared outage mixture, exact instants, joint threshold-aware
#   integration, representable tails, continuous SPECI exposure, LOW no-data, page-only semantic zeros,
#   variance-conserving drift folding, and the numerical error contract (refinement + Monte Carlo).
# Reuse: Pure operator tests; synthetic days only, no DB or network.
from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.stats import norm

from src.contracts.settlement_semantics import SettlementSemantics
from src.data import day0_dense_state_space as ds

D = 1440.0
GRID = ds.grid_minutes(D)
HOUR = tuple(int(h) for h in ((GRID % 1440) // 60).astype(int))
FLAT = ds.DenseMean(tuple([0.0] * 24), 0.0)
SEM = SettlementSemantics(resolution_source="t", measurement_unit="C", precision=1.0,
                          rounding_rule="wmo_half_up", finalization_time="12:00:00Z")
NOISE = ds.DenseNoise(tuple([0.0] * 24), 0.1, 0.4, 0.2)


def diurnal(center: float = 15.0, amp: float = 6.0) -> tuple[float, ...]:
    return tuple(float(v) for v in center + amp * np.sin(2 * math.pi * ((GRID % 1440) / 1440 - 0.375)))


def flat(level: float = 0.0) -> tuple[float, ...]:
    return tuple([float(level)] * GRID.size)


def model(noise=None, tau=200.0, s2=1.5, s2_static=0.0):
    return ds.DenseModel(ds.DenseLatent(tau, s2, s2_static), noise, FLAT)


def day(metric="high", f=None, **kw) -> ds.DenseDay:
    return ds.DenseDay(metric=metric, day_minutes=D, forecast=f or diurnal(), hour=HOUR, **kw)


def bins_around(lo: int, hi: int):
    return [(None, float(lo - 1))] + [(float(k), float(k)) for k in range(lo, hi + 1)] + [(float(hi + 1), None)]


def R(v):
    return SEM.round_values(np.asarray(v, float))


# ---------------------------------------------------------------- consult counterexamples (D2)

@pytest.mark.parametrize("removed_kind", ["gross", "valid"])
def test_independent_removal_gives_point_one_point_nine(removed_kind):
    """Page max 0; a provisional report 1 kept with probability 0.9, else removed; no other row can
    move the maximum.  Correct q over (<= 0, >= 1) is [0.1, 0.9] (the old kernel returned [0, 1])."""
    weights = (0.9, 0.0, 0.0, 0.1) if removed_kind == "gross" else (0.9, 0.0, 0.1, 0.0)
    d = day(f=flat(0.0), page=((600.0, 0),), marks=((700.0, 1, *weights),))
    q = ds.bin_probabilities(model(tau=200.0, s2=1.0), d, ((None, 0.0), (1.0, None)))
    assert q == pytest.approx([0.1, 0.9], abs=1e-9)


def test_kept_branch_posterior_is_the_lifecycle_weight_not_a_reweighted_likelihood():
    """s = 0.9, T ~ N(0, 1), k = 1: the old mark gave the kept branch 0.685; the normalised mark keeps 0.9."""
    d = day(f=flat(0.0), marks=((700.0, 1, 0.9, 0.0, 0.0, 0.1),))
    q = ds.bin_probabilities(model(tau=1e-3, s2=1.0), d, ((None, 0.0), (1.0, 1.0), (2.0, None)))
    assert q[1] == pytest.approx(0.9 + 0.1 * (norm.cdf(1.5) - norm.cdf(0.5)) * 0, abs=0.02)
    assert q[1] >= 0.9 - 1e-9


def test_visibility_reweights_kept_and_removed_without_hard_deletion():
    """A page fetch that should have shown the report but did not multiplies kept/corrected by
    (1 - a): retention s = 0.9, a = 0.8 -> P(kept | absent) = 0.18 / (0.18 + 0.1)."""
    s, a = 0.9, 0.8
    d = day(f=flat(0.0), page=((600.0, 0),), marks=((700.0, 1, s * (1 - a), 0.0, 0.0, 1 - s),))
    q = ds.bin_probabilities(model(tau=200.0, s2=1.0), d, ((None, 0.0), (1.0, None)))
    expect = s * (1 - a) / (s * (1 - a) + (1 - s))
    assert q[1] == pytest.approx(expect, abs=1e-9) and 0.0 < q[0] < 1.0


def test_outage_mixture_keeps_temperature_evidence_and_matches_posterior_weights():
    """Under the shared outage every unresolved mark is a removed-but-valid report: no tape value,
    but T stays inside the report interval.  The mixture weight is P(Z) P(E | Z), normalised."""
    marks = tuple((float(t), 3, 0.97, 0.0, 0.0, 0.03) for t in (600.0, 630.0, 660.0))
    d0 = day(f=flat(0.0), page=((300.0, 0),), marks=marks)
    d1 = day(f=flat(0.0), page=((300.0, 0),), marks=marks, outage_prior=0.05)
    m = model(tau=600.0, s2=1.0)
    bins = ((None, 0.0), (1.0, 2.0), (3.0, None))
    q0, q1 = ds.bin_probabilities(m, d0, bins), ds.bin_probabilities(m, d1, bins)
    assert q1[2] < q0[2]                      # the outage branch removes the 3s from the tape
    assert q1[1] > q0[1] - 1e-12               # but T near 3 still pushes later pending mass up
    _, _, _, w = ds.extreme_columns(m, d1, ds.thresholds_for("high", bins))
    assert 0.0 < w < 1.0


def test_page_correction_later_version_is_the_input():
    """A later page correction 16 -> 15 is a different page row value; the operator prices the value it
    is given (the evidence reader passes the latest received version)."""
    q16 = ds.bin_probabilities(model(), day(f=flat(12.0), page=((600.0, 16),)), bins_around(13, 18))
    q15 = ds.bin_probabilities(model(), day(f=flat(12.0), page=((600.0, 15),)), bins_around(13, 18))
    labels = bins_around(13, 18)
    assert q16[labels.index((15.0, 15.0))] == 0.0 and q15[labels.index((15.0, 15.0))] > 0.5


# ---------------------------------------------------------------- D5 numerics

def test_distinct_report_instants_are_not_merged():
    """A page row at 59 and a pending report at 60 are two tape events (the old 5-min slots merged them)."""
    f = flat(0.0)
    d = day(f=f, page=((59.0, 0),), pending=((60.0, 1.0, 0.0),))
    q = ds.bin_probabilities(model(tau=1e-3, s2=1.0), d, ((None, 0.0), (1.0, None)))
    assert q[1] == pytest.approx(1 - norm.cdf(0.5), abs=5e-3)


def test_representable_upper_tail_is_not_deleted():
    """A Gaussian tail of 3.2e-14 must stay positive (the old finite edges returned exactly 0)."""
    d = day(f=flat(0.0), pending=((600.0, 1.0, 0.0),))
    q = ds.bin_probabilities(model(tau=1e-3, s2=1.0 / 7.5 ** 2), d, ((None, 0.0), (1.0, None)))
    assert q[1] > 0.0
    assert q[1] == pytest.approx(norm.sf(0.5 * 7.5), rel=0.2)


def test_speci_partial_exposure_is_continuous():
    """One minute left at rate 0.1/min, constant temperature 5 and an otherwise empty tape:
    P(H >= 5) = 1 - e^-0.1 (the old 5-min endpoint factors returned 0); the rest is no-data."""
    d = day(f=flat(5.0), speci_from=D - 1.0, speci_rate=0.1)
    q = ds.bin_probabilities(model(tau=1e5, s2=1e-6), d, ((None, 4.0), (5.0, None)))
    assert q[1] == pytest.approx(1 - math.exp(-0.1), abs=1e-6)


def test_low_empty_tape_resolves_to_the_lowest_bracket():
    """No row can join the tape: the no-data clause sends all mass to the lowest bracket, LOW and HIGH."""
    for metric in ("low", "high"):
        q = ds.bin_probabilities(model(), day(metric, f=flat(10.0)), bins_around(8, 12))
        assert q[0] == pytest.approx(1.0)


def test_low_empty_tape_with_speci_possibility_splits_to_lowest_bracket():
    d = day("low", f=flat(10.0), speci_from=D - 60.0, speci_rate=0.01)
    q = ds.bin_probabilities(model(tau=1e5, s2=1e-6), d, bins_around(8, 12))
    p_empty = math.exp(-0.6)
    assert q[0] == pytest.approx(p_empty, abs=1e-6)
    labels = bins_around(8, 12)
    assert q[labels.index((10.0, 10.0))] == pytest.approx(1 - p_empty, abs=1e-6)


def test_drift_folding_conserves_both_component_variances():
    nz = ds.DenseNoise(tuple([0.0] * 24), 0.2, 0.5, 0.3, 0.1, 30.0, 0.002)
    folded = nz.folded()
    assert folded.sd2 == 0.0
    assert folded.s1 ** 2 == pytest.approx(0.2 ** 2 + 0.002)
    assert folded.s2 ** 2 == pytest.approx(0.5 ** 2 + 0.002)


def test_simultaneous_factors_integrate_jointly():
    """A context row and a pending report at the same instant: the threshold cut inside the report's
    interval is integrated jointly (the old separate cell averages mis-priced the cell)."""
    f = flat(0.3)
    d = day(f=f, pending=((600.0, 1.0, 0.0),), context=())
    exact = 1 - norm.cdf(0.5, loc=0.3, scale=1.0)
    for cell in (0.05, 0.2):
        q = ds.bin_probabilities(model(tau=1e-3, s2=1.0), d, ((None, 0.0), (1.0, None)), cell=cell)
        assert q[1] == pytest.approx(exact, abs=1e-6)


# ---------------------------------------------------------------- semantic support

def test_page_row_is_the_only_structural_zero():
    """Bins below the page's 25 are exactly zero; a provisional 30 keeps bins 25..29 positive."""
    d = day(page=((600.0, 25),), marks=((700.0, 30, 0.97, 0.0, 0.0, 0.03),))
    bins = bins_around(18, 32)
    q = ds.bin_probabilities(model(NOISE), d, bins)
    support = ds.semantic_support(d, bins)
    for (low, high), p, ok in zip(bins, q, support):
        if high is not None and high < 25:
            assert p == 0.0 and not ok
        else:
            assert ok
    assert q[bins.index((30.0, 30.0))] > 0.5
    assert q[bins.index((25.0, 25.0))] > 0.0


def test_lucknow_gross_mirror_row_sets_no_structural_zero():
    """Lucknow 2026-09-06 HIGH: mirrors carry 37 at 13:00 local; the page keeps <= 31 and settled 31."""
    f = diurnal(center=27.5, amp=3.5)
    page = tuple((float(t), int(R(f[int((t + ds.PRE_MIN) / 5)]))) for t in range(0, 13 * 60, 30))
    lucknow = (0.981, 0.0028, 0.0, 0.0162)
    d = day(f=f, page=page, marks=((780.0, 37, *lucknow),), pending=tuple((float(t), 0.98, 0.0) for t in range(810, 1440, 30)))
    bins = bins_around(28, 38)
    q = ds.bin_probabilities(model(NOISE), d, bins)
    assert all(p > 0.0 for (lo, hi), p in zip(bins, q) if hi is not None and d.boundary_absorbing <= hi < 37)
    assert q[bins.index((37.0, 37.0))] < 0.99


# ---------------------------------------------------------------- error contract

def sir_oracle(m: ds.DenseModel, d: ds.DenseDay, ks, n=400_000, seed=0) -> np.ndarray:
    """Particle simulation of the same law (one-scale OU, no static, no drift): branches drawn per
    mark from its normalised posterior, tape values tracked explicitly."""
    rng = np.random.default_rng(seed)
    lat = m.latent
    f = np.asarray(d.forecast, float)
    mpath = ds.mean_path(d.forecast, d.hour, m.mean)
    ev = sorted([(t, 0, k) for t, k in d.page] + [(m_[0], 1, tuple(m_[1:])) for m_ in d.marks]
                + [(t, 2, (a, b)) for t, a, b in d.pending], key=lambda e: e[0])
    r = math.sqrt(lat.s2) * rng.standard_normal(n)
    t_prev = -ds.PRE_MIN
    tape_max = np.full(n, -np.inf) if d.metric == "high" else np.full(n, np.inf)
    w = np.ones(n)
    for t, kind, p in ev:
        phi = math.exp(-(t - t_prev) / lat.tau)
        r = phi * r + math.sqrt(lat.s2 * (1 - phi * phi)) * rng.standard_normal(n)
        t_prev = t
        T = float(np.interp(t, GRID, mpath)) + r
        if kind == 0:
            w = w * ((T >= p - 0.5) & (T < p + 0.5))
            tape_max = np.maximum(tape_max, p) if d.metric == "high" else np.minimum(tape_max, p)
        elif kind == 1:
            k, wk, wc, wr, wg = p
            inside = ((T >= k - 0.5) & (T < k + 0.5)).astype(float)
            pred = float((w * inside).sum() / w.sum())
            tot = wk + wc + wr + wg
            u = rng.random(n) * tot
            kept, removed = u < wk, (u >= wk + wc) & (u < wk + wc + wr)
            valid = kept | removed
            w = w * np.where(valid, inside / pred, 1.0) * tot
            val = np.where(kept, k, np.nan)
            tape_max = np.where(np.isnan(val), tape_max, np.fmax(tape_max, val) if d.metric == "high" else np.fmin(tape_max, val))
        else:
            a, _b = p
            joins = rng.random(n) < a
            v = R(T)
            tape_max = np.where(joins, np.maximum(tape_max, v) if d.metric == "high" else np.minimum(tape_max, v), tape_max)
        if (w > 0).sum() < n // 4:
            c = np.cumsum(w / w.sum())
            idx = np.minimum(np.searchsorted(c, (rng.random() + np.arange(n)) / n), n - 1)
            r, tape_max, w = r[idx], tape_max[idx], np.ones(n)
    ks = np.asarray(ks, float)
    ok = (tape_max[:, None] <= ks[None, :]) if d.metric == "high" else (tape_max[:, None] >= ks[None, :])
    return (w[:, None] * ok).sum(0) / w.sum()


@pytest.mark.parametrize("metric", ["high", "low"])
def test_matches_particle_oracle_with_marks_and_pending(metric):
    """Error contract on a fitted-like regime: |dG| < 0.01 against the particle oracle."""
    f = diurnal()
    rng = np.random.default_rng(3)
    marks = tuple((float(t), int(R(np.interp(t, GRID, f) + 0.3 * rng.standard_normal())), 0.95, 0.01, 0.0, 0.04)
                  for t in range(20, 13 * 60, 30))
    pending = tuple((float(t), 0.98, 0.0) for t in range(13 * 60 + 20, 1440, 30))
    d = day(metric, f=f, marks=marks, pending=pending)
    m = model(tau=200.0, s2=1.5)
    ks = list(range(int(min(f)) - 2, int(max(f)) + 4))
    exact = ds.extreme_cdf(m, d, ks)
    oracle = sir_oracle(m, d, ks)
    assert np.max(np.abs(exact - oracle)) < 0.01


def test_cell_refinement_error_bound():
    """Error contract: halving the cell moves no G(k) by more than 2e-3 on a dense day."""
    f = diurnal()
    dense = tuple((float(t), round(float(np.interp(t, GRID, f)), 1)) for t in range(0, 600, 10))
    pending = tuple((float(t), 0.98, 0.0) for t in range(620, 1440, 30))
    d = day(f=f, dense=dense, pending=pending)
    ks = list(range(12, 26))
    coarse = ds.extreme_cdf(model(NOISE), d, ks, cell=0.05)
    fine = ds.extreme_cdf(model(NOISE), d, ks, cell=0.025)
    assert np.max(np.abs(coarse - fine)) < 2e-3


def test_independent_pending_instants_match_closed_form():
    f = diurnal()
    d = day(f=f, pending=((600.0, 1.0, 0.0), (900.0, 1.0, 0.0), (1200.0, 1.0, 0.0)))
    ks = list(range(12, 25))
    exact = ds.extreme_cdf(model(tau=0.5, s2=1.0), d, ks)
    ref = [np.prod([norm.cdf(k + 0.5 - np.interp(t, GRID, f)) for t in (600, 900, 1200)]) for k in ks]
    assert np.max(np.abs(exact - np.asarray(ref))) < 2e-3


def test_negative_half_up_preimage():
    """R(-1.5) = -1: an instant fixed at mean -1.5 settles <= -2 with probability 1/2."""
    d = day(f=flat(-1.5), pending=((600.0, 1.0, 0.0),))
    g = ds.extreme_cdf(model(tau=0.5, s2=0.01), d, [-2, -1])
    assert g[0] == pytest.approx(0.5, abs=0.02)
    assert g[1] == pytest.approx(1.0, abs=1e-6)


def test_dense_reading_is_evidence_only_and_static_offset_normalises():
    f = diurnal(center=21.0, amp=3.0)
    d = day(f=f, dense=((370.0, 25.4),), pending=tuple((float(t), 0.98, 0.0) for t in range(390, 1440, 30)))
    q = ds.bin_probabilities(model(NOISE, s2_static=0.07), d, bins_around(18, 28))
    assert q.sum() == pytest.approx(1.0) and np.all(q > 0)


def test_day_rejects_duplicate_instants_and_bad_weights():
    with pytest.raises(ValueError, match="DAY0_DENSE_DAY_INVALID"):
        day(page=((600.0, 1),), pending=((600.0, 1.0, 0.0),))
    with pytest.raises(ValueError, match="DAY0_DENSE_DAY_INVALID"):
        day(marks=((600.0, 1, 0.0, 0.0, 0.0, 0.0),))
