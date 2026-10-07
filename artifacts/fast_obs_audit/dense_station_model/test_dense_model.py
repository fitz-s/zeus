"""Tests for the dense-station settlement model (synthetic data only; no network, no DB).

Run:  python -m pytest artifacts/fast_obs_audit/dense_station_model/test_dense_model.py -q -p no:cacheprovider
"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dense_model as dm  # noqa: E402
import state_space as ss  # noqa: E402
from ss_engine import run_day  # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402


# ---------------------------------------------------------------- R (settlement rounding)

@pytest.mark.parametrize("v,k", [(2.5, 3), (2.49, 2), (-0.5, 0), (-0.51, -1), (-1.5, -1), (-2.5, -2), (-2.51, -3),
                                 (0.0, 0), (-0.0, 0), (31.5, 32), (-11.5, -11), (14.4999, 14)])
def test_R_matches_round_single_including_negatives(v, k):
    sem = SettlementSemantics(resolution_source="t", measurement_unit="C", precision=1.0, rounding_rule="wmo_half_up",
                              finalization_time="12:00:00Z")
    assert dm.R(v) == k == int(sem.round_single(v))


def test_R_vectorised_equals_scalar():
    v = np.round(np.random.default_rng(0).uniform(-30, 40, 5000), 2)
    assert np.array_equal(dm.R(v), np.array([dm.R(float(x)) for x in v]))


# ---------------------------------------------------------------- interval-censored MLE

@pytest.mark.parametrize("b,sigma", [(0.3, 0.15), (-0.2, 0.4), (0.0, 0.05)])
def test_icm_recovers_b_and_sigma(b, sigma):
    rng = np.random.default_rng(1)
    x = np.round(rng.uniform(-10, 30, 20000), 1)
    k = dm.R(x + b + sigma * rng.standard_normal(x.size))
    f = dm.fit_icm(k, x)
    assert abs(f["beta"][0] - b) < 4 * f["se_beta"][0] + 0.005
    assert abs(f["sigma"] - sigma) < 4 * f["se_sigma"] + 0.005


def test_icm_t_recovers_location_under_heavy_tails():
    rng = np.random.default_rng(2)
    x = np.round(rng.uniform(0, 25, 20000), 1)
    k = dm.R(x + 0.1 + 0.15 * rng.standard_t(3, x.size))
    g, t = dm.fit_icm(k, x), dm.fit_icm_t(k, x)
    assert t["aic"] < g["aic"]
    assert abs(t["beta"][0] - 0.1) < 0.02


def test_offset_table_fits_correct_model():
    rng = np.random.default_rng(3)
    x = np.round(rng.uniform(0, 25, 20000), 1)
    k = dm.R(x + 0.05 + 0.2 * rng.standard_normal(x.size))
    f = dm.fit_icm(k, x)
    tab = dm.offset_table(k, x, f["beta"][0], f["sigma"])
    assert tab["chi2_cells_ge5"] < 15


def test_fit_rho_recovers_latent_autocorrelation():
    rng = np.random.default_rng(4)
    n, rho, s = 6000, 0.6, 0.3
    e = np.empty(n)
    e[0] = rng.standard_normal()
    for i in range(1, n):
        e[i] = rho * e[i - 1] + math.sqrt(1 - rho * rho) * rng.standard_normal()
    x = np.round(rng.uniform(0, 20, n), 1)
    k = dm.R(x + s * e)
    lo, hi = (k - 0.5 - x) / s, (k + 0.5 - x) / s
    f = dm.fit_rho(lo[:-1], hi[:-1], lo[1:], hi[1:])
    assert abs(f["rho"] - rho) < 4 * f["se"]


# ---------------------------------------------------------------- hard floor margin

def test_floor_margin_search_is_exact_and_minimal():
    rng = np.random.default_rng(5)
    nd, per = 60, 48
    day = np.repeat(np.arange(nd), per)
    x = rng.normal(15, 4, nd * per)
    M = dm.R(x + 0.25 * rng.standard_normal(x.size))
    H = np.array([M[day == d].max() for d in range(nd)])[day]
    L = np.array([M[day == d].min() for d in range(nd)])[day]
    for side, T in (("high", H), ("low", L)):
        c = dm.critical_margin(x, T, side)
        m = dm.min_zero_false_margin(day, x, T, side, step=0.01)
        assert dm.false_days(day, x, T, m, side) == 0
        if m >= 0.01:
            assert dm.false_days(day, x, T, round(m - 0.01, 10), side) > 0
        assert (m > c - 1e-9) if side == "high" else (m >= c - 1e-9)


def test_floor_edge_convention_follows_half_up():
    # high: x + b - m = H + 0.5 rounds UP to H + 1 -> false; low: x + b + m = L - 0.5 rounds to L -> not false
    assert dm.false_mask(np.array([10.5]), np.array([10]), 0.0, "high")[0]
    assert not dm.false_mask(np.array([9.5]), np.array([10]), 0.0, "low")[0]
    assert dm.false_mask(np.array([9.49]), np.array([10]), 0.0, "low")[0]


# ---------------------------------------------------------------- filter primitives

def test_tn_moments_match_quadrature_and_tails():
    from scipy import integrate, stats
    for m, P, a, c in ((0.3, 0.8, -0.2, 0.7), (-2.0, 0.1, 1.0, 2.0), (5.0, 2.0, -math.inf, 0.0)):
        pdf = lambda x: stats.norm.pdf(x, m, math.sqrt(P))  # noqa: E731
        lo, hi = max(a, m - 40 * math.sqrt(P)), min(c, m + 40 * math.sqrt(P))
        z = integrate.quad(pdf, lo, hi)[0]
        mu = integrate.quad(lambda x: x * pdf(x), lo, hi)[0] / z
        v = integrate.quad(lambda x: (x - mu) ** 2 * pdf(x), lo, hi)[0] / z
        got = ss.tn_moments(m, P, a, c)
        assert abs(got[0] - mu) < 1e-6 and abs(got[1] - v) < 1e-6
    hi_m, hi_v = ss.tn_moments(0.0, 1.0, 8.0, 9.0)
    assert 8.0 < hi_m < 8.2 and 0 < hi_v < 0.02


def test_tn_sample_matches_moments():
    rng = np.random.default_rng(6)
    x = ss.tn_sample(np.full(200000, 0.4), 0.7, -0.1, 0.5, rng.random(200000))
    m, v = ss.tn_moments(0.4, 0.49, -0.1, 0.5)
    assert x.min() >= -0.1 and x.max() < 0.5
    assert abs(x.mean() - m) < 3e-3 and abs(x.var() - v) < 3e-3


def test_mc_extreme_pmf_matches_exact_independent_case():
    # independent instants: OU with tau -> 0 between instants; compare to the exact product formula
    lat = ss.Latent(tau_s=1e-6, s2_s=0.7 ** 2)
    centers = np.array([20.2, 20.6, 21.1, 20.9])
    rng = np.random.default_rng(7)
    N = 40000
    V = dm.R(centers[None, :] + 0.7 * rng.standard_normal((N, 4)))
    ks, p = np.unique(V.max(1), return_counts=True)
    k2, p2 = ss.extreme_pmf_independent_exact("high", -math.inf, centers, 0.7)
    exact = dict(zip(k2.tolist(), p2.tolist()))
    for k, c in zip(ks.tolist(), p.tolist()):
        assert abs(c / N - exact.get(k, 0.0)) < 0.01
    assert lat.phi(5.0)[0] < 1e-12


def test_a0_pmf_respects_received_boundary():
    ks, p = ss.a0_pmf("high", 21, [20.0, 21.5], 0.2, 1.0)
    assert abs(p.sum() - 1) < 1e-12 and p[ks < 21].sum() == 0
    ks, p = ss.a0_pmf("low", 12, [13.0, 12.5], -0.1, 1.0)
    assert abs(p.sum() - 1) < 1e-12 and p[ks > 12].sum() == 0


# ---------------------------------------------------------------- state-space engine vs exact grid oracle

@pytest.fixture(scope="module")
def syn():
    lat = ss.Latent(tau_s=180.0, s2_s=0.8 ** 2)
    days, segs = ss.simulate_days(6, lat, 0.15, seed=3, quantum=0.001)
    noise = ss.DenseNoise(b_hour=tuple([0.0] * 24), s1=0.15, s2=0.15, pi=0.0, quantum=0.001, lag=0.0)
    return lat, days, segs, noise


@pytest.mark.parametrize("arm", ["A", "B"])
def test_smc_matches_grid_oracle(syn, arm):
    lat, days, _, noise = syn
    d = days[2]
    dec = [480.0, 780.0, 900.0, 1100.0]
    out = run_day(d, lat, noise, 0.0, dec, arm, N=20000, seed=1)
    for o, t0 in zip(out, dec):
        orc = ss.grid_oracle(d, lat.tau_s, lat.s2_s, noise.s1 ** 2, t0, use_dense=arm == "B", K=401)
        for side in ("high", "low"):
            k1, p1 = o[side]
            k2, p2 = orc[side]
            a = dict(zip(k1.tolist(), p1.tolist()))
            b = dict(zip(k2.tolist(), p2.tolist()))
            tv = 0.5 * sum(abs(a.get(k, 0) - b.get(k, 0)) for k in set(a) | set(b))
            assert tv < 0.03, (arm, t0, side, tv)


def test_no_dense_B_equals_A(syn):
    lat, days, _, noise = syn
    d = days[1]
    d.dense_ok = False
    try:
        a = run_day(d, lat, noise, 0.0, [600.0, 900.0], "A", N=5000, seed=2)
        b = run_day(d, lat, noise, 0.0, [600.0, 900.0], "B", N=5000, seed=2)
    finally:
        d.dense_ok = True
    for x, y in zip(a, b):
        for side in ("high", "low"):
            assert np.array_equal(x[side][0], y[side][0]) and np.allclose(x[side][1], y[side][1])


def test_received_boundary_is_never_violated(syn):
    lat, days, _, noise = syn
    for arm in ("A", "B", "A_tn", "B_g12"):
        for o in run_day(days[3], lat, noise, 0.0, list(np.arange(360.0, 1440.0, 60.0)), arm, N=3000, seed=4):
            if np.isfinite(o["Bh"]):
                ks, p = o["high"]
                assert p[ks < o["Bh"]].sum() == 0
            if np.isfinite(o["Bl"]):
                ks, p = o["low"]
                assert p[ks > o["Bl"]].sum() == 0


def test_dense_reading_at_pending_metar_instant_nearly_fixes_it():
    """A dense reading AT a pending METAR instant (sigma small) makes that instant's rounded value nearly known."""
    lat = ss.Latent(tau_s=120.0, s2_s=1.0)
    days, _ = ss.simulate_days(3, lat, 0.05, seed=9, quantum=0.001)
    d = days[1]
    noise = ss.DenseNoise(b_hour=tuple([0.0] * 24), s1=0.05, s2=0.05, pi=0.0, quantum=0.001, lag=0.0)
    lag_m = 20.0  # METAR arrives 20 min after its instant; dense arrives at once
    # the afternoon settlement instant whose dense reading sits farthest from a rounding edge
    cand = [int(g) for g in d.si if 720.0 <= d.gt[g] <= 960.0 and np.any(d.di == g)]
    edge = [abs((d.dx[d.di == g][0] + 0.5) % 1.0 - 0.5) for g in cand]
    g = cand[int(np.argmax(edge))]
    t_inst = float(d.gt[g])
    t0 = t_inst + 1.0  # METAR for t_inst not yet received, dense reading at t_inst is
    x_at = d.dx[d.di == g][0]
    k_true = dm.R(x_at)
    assert max(edge) > 0.2
    # P(high >= k_true): the pending instant alone reaches k_true with near certainty once its dense reading is in
    reach = {}
    for arm in ("A", "B"):
        ks, p = run_day(d, lat, noise, lag_m, [t0], arm, N=20000, seed=5)[0]["high"]
        reach[arm] = float(p[ks >= k_true].sum())
    assert reach["B"] > 0.97
    assert reach["B"] >= reach["A"] - 1e-9


# ---------------------------------------------------------------- estimation on synthetic data

def test_ou_parameters_recovered_from_dense_series():
    lat = ss.Latent(tau_s=150.0, s2_s=1.2 ** 2)
    _, segs = ss.simulate_days(120, lat, 0.1, seed=11)
    f = ss.fit_ou_acf(segs, 10, list(range(10, 190, 10)), n_scales=1)
    assert abs(f["tau_s"] - 150.0) / 150.0 < 0.25
    assert abs(math.sqrt(f["s2_s"]) - 1.2) / 1.2 < 0.15


def test_two_scale_recovered_and_nests_one_scale():
    lat = ss.Latent(tau_s=600.0, s2_s=1.0, tau_f=40.0, s2_f=0.5)
    _, segs = ss.simulate_days(150, lat, 0.1, seed=12)
    two = ss.fit_ou_acf(segs, 10, list(range(10, 730, 10)), n_scales=2)
    one = ss.fit_ou_acf(segs, 10, list(range(10, 730, 10)), n_scales=1)
    assert two["r2"] >= one["r2"]
    assert abs(two["tau_f"] - 40.0) / 40.0 < 0.5 and abs(two["tau_s"] - 600.0) / 600.0 < 0.5


def test_dense_noise_fit_recovers_bias_and_sigma_with_quantisation():
    rng = np.random.default_rng(13)
    T = rng.uniform(0, 25, 20000)
    M = dm.R(T)
    x = np.round((T - 0.12 + 0.2 * rng.standard_normal(T.size)) / 0.1) * 0.1
    hour = rng.integers(0, 24, T.size)
    f = ss.fit_dense_noise(M, x, hour, mixture=False)
    assert all(abs(b - 0.12) < 0.03 for b in f["b_block"])
    assert abs(f["s1"] - 0.2) < 0.03


def test_B_beats_A_when_dense_is_informative():
    """Known parameters, synthetic truth, informative regime (hourly METAR arriving 10 min late, 10-min dense with
    sd 0.08 arriving after 1 min, tau 60 min): dense factors improve log-loss of the settled extreme on every
    bootstrap resample of days."""
    lat = ss.Latent(tau_s=60.0, s2_s=1.0)
    days, _ = ss.simulate_days(16, lat, 0.08, seed=21, metar_minutes=(0,))
    noise = ss.DenseNoise(b_hour=tuple([0.0] * 24), s1=0.08, s2=0.08, pi=0.0, quantum=0.1, lag=1.0)
    per_day = []
    for d in days[1:]:
        dec = list(np.arange(480.0, 1380.0, 20.0))
        ll = {}
        for arm in ("A", "B"):
            ll[arm] = np.mean([ss.score(*o[side], d.settled[side])["ll"]
                               for o in run_day(d, lat, noise, 10.0, dec, arm, N=4000, seed=6) for side in ("high", "low")])
        per_day.append(ll["B"] - ll["A"])
    per_day = np.array(per_day)
    boots = np.random.default_rng(0).choice(per_day, (2000, per_day.size)).mean(1)
    assert per_day.mean() < 0 and np.quantile(boots, 0.975) < 0
