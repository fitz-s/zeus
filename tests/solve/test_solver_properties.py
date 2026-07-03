# Created: 2026-07-03
# Last reused/audited: 2026-07-03
"""W3 SOLVE math-core acceptance — the property anchors (design packet §4, consult REV-2).

(a) solver ≥ top-1 picker in the SAME feasible set (post-repair) on EVERY fixture;
(b) zero-edge → zero-stake;
(c) monotone in q; (d) endowment coherence; (e) κ<1 shrinks stakes monotonically;
(f) discrete repair never emits a plan whose re-evaluated ΔU ≤ 0 (adversarial sign-flip),
    and every non-empty plan carries a RepairCertificate proving it;
plus the consult REV-2 amendments: the objective is CONCAVE lower-tail CVaR (the solver reaches
the GLOBAL optimum — proven against brute force — and does NOT inherit the payoff_vector
quantile-of-concave unimodality assertion, whose non-concavity is exhibited by a counterexample).

All deterministic: solve() never samples; fixture families draw from a seeded generator.
"""

from __future__ import annotations

import numpy as np

from src.solve import solver as S
from src.solve.kappa import Kappa, KappaPolicy
from tests.solve import support as F

ALPHA = 0.05
DOM_TOL = 1e-9


def _solve(menu, q_draws, wealth, *, kappa="1.0", haircut=True, bins=("y", "n"), alpha=ALPHA):
    sset = F.scenarios_single_family(bins, q_draws, alpha=alpha)
    svc = F.StubScenarioService(sset)
    policy = KappaPolicy(kappa=Kappa.of(kappa), downstream_haircut_alive=haircut)
    return S.solve(menu, scenarios=svc, wealth=wealth, kappa_policy=policy, bands_by_family=F.bands(), q_version="qv_test")


def _arrays(menu, wealth, bins):
    atom_ids = tuple(F.atom_id(b) for b in bins)
    return S._build_arrays(menu, wealth, atom_ids)


# --- (a) dominance ----------------------------------------------------------

def _random_fixture(rng):
    n_bins = int(rng.integers(2, 5))
    bins = tuple(f"b{j}" for j in range(n_bins))
    n_draws = int(rng.integers(96, 256))
    conc = float(rng.uniform(40, 140))
    true_q = rng.dirichlet(np.full(n_bins, 3.0))
    q_draws = rng.dirichlet(true_q * conc, size=n_draws)
    n_items = int(rng.integers(1, 7))
    items = []
    for i in range(n_items):
        j = int(rng.integers(0, n_bins))
        cost = float(np.clip(true_q[j] - rng.uniform(-0.10, 0.18), 0.02, 0.95))
        items.append(F.buy_item(f"it{i}", bins[j], cost, bins, max_units=float(rng.uniform(50, 5000))))
    return F.menu(items), q_draws, F.flat_wealth_state(bins, float(rng.uniform(80, 500))), bins


def test_a_solver_dominates_top1_on_every_fixture():
    rng = np.random.default_rng(20260703)
    n = 40
    traded = 0
    for _ in range(n):
        m, q, w, bins = _random_fixture(rng)
        plan = _solve(m, q, w, bins=bins)
        assert plan.expected_delta_log_wealth >= plan.delta_u_baseline_top1 - DOM_TOL, (
            f"DOMINANCE VIOLATION: {plan.expected_delta_log_wealth} < {plan.delta_u_baseline_top1}"
        )
        assert plan.diagnostics["continuous_delta_u_joint"] >= plan.diagnostics["continuous_delta_u_top1"] - DOM_TOL
        if plan.orders:
            traded += 1
    assert traded >= n // 3, f"only {traded}/{n} fixtures traded"


def test_a_joint_strictly_beats_top1_when_diversification_helps():
    bins = ("b0", "b1", "b2")
    q = np.random.default_rng(7).dirichlet([70, 70, 60], size=500)
    items = [F.buy_item("y0", "b0", 0.20, bins, max_units=5000), F.buy_item("y1", "b1", 0.20, bins, max_units=5000)]
    plan = _solve(F.menu(items), q, F.flat_wealth_state(bins, 300.0), bins=bins)
    assert plan.diagnostics["continuous_delta_u_joint"] > plan.diagnostics["continuous_delta_u_top1"] + 1e-6
    assert len(plan.orders) == 2


# --- (b) zero / negative edge -> zero stake ---------------------------------

def test_b_zero_edge_zero_stake():
    bins = ("y", "n")
    plan = _solve(F.menu([F.buy_item("it", "y", 0.5, bins)]), F.two_bin_q_draws([0.5] * 64), F.flat_wealth_state(bins, 100.0))
    assert plan.orders == ()
    assert plan.no_trade_reason is not None
    assert plan.expected_delta_log_wealth == 0.0


def test_b_negative_edge_zero_stake():
    bins = ("y", "n")
    plan = _solve(F.menu([F.buy_item("it", "y", 0.6, bins)]), F.two_bin_q_draws([0.4] * 64), F.flat_wealth_state(bins, 100.0))
    assert plan.orders == ()


# --- (c) monotone in q ------------------------------------------------------

def test_c_optimal_stake_monotone_in_q():
    bins = ("y", "n")
    m = F.menu([F.buy_item("it", "y", 0.5, bins, max_units=100000)])
    w = F.flat_wealth_state(bins, 100.0)
    w0, payoff, caps, _ = _arrays(m, w, bins)
    prev = -1.0
    stakes = []
    for p in (0.52, 0.56, 0.60, 0.66, 0.72, 0.80):
        q = F.two_bin_q_draws(np.clip(p + np.array([-0.03, -0.015, 0.0, 0.015, 0.03]), 0.01, 0.99))
        _, _, x_top1, _ = S._optimize_continuous(w0, payoff, caps, q, np.ones(q.shape[0]), 0.2)
        stakes.append(float(x_top1.sum()))
        assert stakes[-1] >= prev - 1e-9, f"stake dropped as q rose: {stakes}"
        prev = stakes[-1]
    assert stakes[-1] > stakes[0] + 1e-6


# --- (d) endowment coherence ------------------------------------------------

def test_d_held_position_shrinks_marginal_appetite():
    bins = ("y", "n")
    m = F.menu([F.buy_item("it", "y", 0.5, bins, max_units=100000)])
    q = F.two_bin_q_draws([0.70] * 32)

    def _stake(wealth_by_atom):
        w = F.wealth_state(wealth_by_atom, 100.0)
        w0, payoff, caps, _ = _arrays(m, w, bins)
        _, _, x_top1, _ = S._optimize_continuous(w0, payoff, caps, q, np.ones(q.shape[0]), ALPHA)
        return float(x_top1.sum())

    stake_a = _stake({F.atom_id("y"): 100.0, F.atom_id("n"): 100.0})
    stake_b = _stake({F.atom_id("y"): 400.0, F.atom_id("n"): 100.0})  # extra held claim in y
    assert stake_a > 0.0
    assert stake_b <= stake_a - 1e-6


# --- (e) kappa monotonicity -------------------------------------------------

def test_e_kappa_shrinks_stakes_monotonically():
    bins = ("y", "n")
    m = F.menu([F.buy_item("it", "y", 0.5, bins, max_units=100000)])
    w = F.flat_wealth_state(bins, 100.0)
    q = F.two_bin_q_draws([0.72] * 64)
    prev = None
    for k in ("1.0", "0.75", "0.5", "0.25"):
        haircut = k == "1.0"
        plan = _solve(m, q, w, kappa=k, haircut=haircut)
        units = float(sum(o.size for o in plan.orders))
        assert plan.kappa_applied == float(k)
        if prev is not None:
            assert units <= prev + 1e-9, f"κ={k} did not shrink stakes: {units} > {prev}"
        prev = units


# --- (f) discrete repair never emits a losing plan --------------------------

def test_f_repair_emits_no_trade_when_min_order_size_flips_sign():
    bins = ("y", "n")
    # thin edge -> tiny optimal stake; huge per-item min_order_size overshoots the profitable region
    m = F.menu([F.buy_item("it", "y", 0.5, bins, max_units=100000, min_order_size=80.0)])
    plan = _solve(m, F.two_bin_q_draws([0.52] * 200), F.flat_wealth_state(bins, 100.0))
    assert plan.orders == ()
    assert plan.no_trade_reason == "NO_IMPROVING_DISCRETE_PLAN"
    assert plan.diagnostics["continuous_delta_u_top1"] > 0.0  # the continuous optimum WAS positive


def test_f_every_emitted_plan_positive_reeval_and_certified():
    rng = np.random.default_rng(99)
    checked = 0
    for _ in range(30):
        m, q, w, bins = _random_fixture(rng)
        plan = _solve(m, q, w, bins=bins)
        if not plan.orders:
            continue
        checked += 1
        assert plan.repair_certificate is not None
        assert plan.repair_certificate.repaired_objective > 0.0
        atom_ids = tuple(F.atom_id(b) for b in bins)
        w0, payoff, caps, items = S._build_arrays(m, w, atom_ids)
        idx = {it.item_id: i for i, it in enumerate(items)}
        x = np.zeros(payoff.shape[0])
        for o in plan.orders:
            x[idx[o.menu_item_id]] = float(o.size)
        u = S._objective(x, w0, payoff, q, np.ones(q.shape[0]), ALPHA)
        assert u > 0.0
        assert np.all(w0 + x @ payoff > 0.0)  # log-domain safety
        assert abs(u - plan.expected_delta_log_wealth) < 1e-9
    assert checked >= 5


def test_f_safe_prefix_dense_and_bounds_present():
    bins = ("b0", "b1", "b2")
    q = np.random.default_rng(3).dirichlet([70, 70, 60], size=400)
    items = [F.buy_item("y0", "b0", 0.20, bins, max_units=5000), F.buy_item("y1", "b1", 0.20, bins, max_units=5000)]
    plan = _solve(F.menu(items), q, F.flat_wealth_state(bins, 300.0), bins=bins)
    assert sorted(o.safe_prefix_index for o in plan.orders) == list(range(len(plan.orders)))
    cert = plan.repair_certificate
    assert len(cert.safe_prefix_objective_bounds) == len(plan.orders)
    assert cert.batch_partition and all(len(chunk) <= 15 for chunk in cert.batch_partition)
    assert all(o.q_version == "qv_test" for o in plan.orders)


# --- CVaR concavity + global optimality + VaR counterexample ----------------

def test_cvar_objective_concave_along_rays():
    # The property the solver relies on: the CVaR objective is concave, so coordinate ascent
    # reaches the global optimum. Sample stake rays and assert no concavity violation.
    rng = np.random.default_rng(11)
    bins = ("b0", "b1", "b2")
    q = rng.dirichlet([30, 30, 25], size=200)
    w = F.flat_wealth_state(bins, 200.0)
    m = F.menu([F.buy_item(f"it{j}", bins[j], 0.25, bins, max_units=3000) for j in range(3)])
    w0, payoff, caps, _ = _arrays(m, w, bins)
    weights = np.ones(q.shape[0])
    for _ in range(20):
        d = rng.random(3)
        # scale ray to stay inside the feasible box (keep every atom wealth > 0)
        hi = min(S._feasible_hi(i, np.zeros(3), w0, payoff, caps) / max(d[i], 1e-9) for i in range(3))
        ts = np.linspace(0, 0.98 * hi, 60)
        f = np.array([S._objective(t * d, w0, payoff, q, weights, ALPHA) for t in ts])
        viol = [i for i in range(1, len(f) - 1) if f[i] < 0.5 * (f[i - 1] + f[i + 1]) - 1e-7]
        assert not viol, f"CVaR objective non-concave on a ray at indices {viol}"


def test_solver_matches_bruteforce_global_optimum_1d():
    bins = ("y", "n")
    q = F.two_bin_q_draws([0.68] * 80)
    w = F.flat_wealth_state(bins, 100.0)
    m = F.menu([F.buy_item("it", "y", 0.5, bins, max_units=100000)])
    w0, payoff, caps, _ = _arrays(m, w, bins)
    weights = np.ones(q.shape[0])
    hi = S._feasible_hi(0, np.zeros(1), w0, payoff, caps)
    grid = np.linspace(0, hi, 4000)
    brute = max(S._objective(np.array([x]), w0, payoff, q, weights, ALPHA) for x in grid)
    _, u_joint, _, _ = S._optimize_continuous(w0, payoff, caps, q, weights, ALPHA)
    assert u_joint >= brute - 1e-6  # coordinate ascent reached the global optimum


def test_solver_matches_bruteforce_global_optimum_3d_coupled():
    # The real stress (verifier finding): three items whose payoffs are COUPLED through the
    # shared joint atoms — item i's marginal value depends on the other stakes — so a 1-D
    # optimizer is not enough. A coarse 3-D brute-force grid must NOT beat cyclic coordinate
    # ascent. If it ever does, that is a STOP-and-report (the global-optimality claim is false),
    # NOT a tolerance widen.
    import itertools

    bins = ("b0", "b1", "b2")
    q = np.random.default_rng(4242).dirichlet([45, 45, 45], size=96)  # ~uniform -> all edges live
    w = F.flat_wealth_state(bins, 60.0)
    m = F.menu([F.buy_item(f"it{j}", bins[j], 0.20, bins, max_units=100000) for j in range(3)])
    w0, payoff, caps, _ = _arrays(m, w, bins)
    weights = np.ones(q.shape[0])
    alpha = 0.1

    x_joint, u_joint, _, _ = S._optimize_continuous(w0, payoff, caps, q, weights, alpha)

    # coarse 3-D grid over each item's feasible range
    axes = [np.linspace(0, S._feasible_hi(i, np.zeros(3), w0, payoff, caps), 26) for i in range(3)]
    grid_best = -np.inf
    grid_arg = None
    for combo in itertools.product(*axes):
        u = S._objective(np.array(combo), w0, payoff, q, weights, alpha)
        if u > grid_best:
            grid_best = u
            grid_arg = combo

    # coordinate ascent (fine) must reach or exceed anything the coarse grid found
    assert u_joint >= grid_best - 1e-6, (
        f"STOP: 3-D brute-force grid ({grid_best:.8f} at {grid_arg}) BEAT coordinate ascent "
        f"({u_joint:.8f}) — the global-optimality claim under coordinate coupling is FALSE"
    )
    # the optimum is genuinely multi-item (coupling actually exercised, not a 1-D corner)
    assert int((x_joint > 1e-6).sum()) >= 2
    assert sum(1 for v in grid_arg if v > 1e-6) >= 2


def test_var_nonconcave_where_cvar_stays_concave():
    # Direct counterexample (consult REV-2): the α-quantile (VaR) of concave draws is NOT
    # concave, so a unimodality-only optimizer on it can fail; lower-tail CVaR stays concave.
    t = np.linspace(0.0, 1.0, 201)
    a = np.array([2.777, 2.91, 1.861, 0.973])
    mm = np.array([0.943, 0.551, 0.12, 0.472])
    b = np.array([0.779, 0.868, -0.284, 0.143])
    draws = np.array([-a[j] * (t - mm[j]) ** 2 + b[j] for j in range(4)])  # 4 concave-in-t draws
    M = draws.T  # (nt, 4)
    w = np.ones(4)
    alpha = 0.3
    var = np.quantile(M, alpha, axis=1)
    cvar = np.array([S._lower_cvar(M[i], w, alpha) for i in range(len(t))])

    def viol(f):
        return sum(1 for i in range(1, len(f) - 1) if f[i] < 0.5 * (f[i - 1] + f[i + 1]) - 1e-9)

    assert viol(var) >= 2, "expected the VaR/quantile objective to be non-concave"
    assert viol(cvar) == 0, "the CVaR objective must stay concave (the solver relies on it)"


def test_repair_certificate_fields_populated():
    bins = ("y", "n")
    m = F.menu([F.buy_item("it", "y", 0.45, bins, max_units=5000)])
    plan = _solve(m, F.two_bin_q_draws([0.72] * 64), F.flat_wealth_state(bins, 200.0))
    cert = plan.repair_certificate
    assert cert.worst_price_model == "avg_cost_size_aware_depth_capped_v1"
    assert "it" in cert.tick_size_deltas
    assert cert.continuous_objective > 0.0
    assert cert.budget_after_repair_usd < 200.0  # cash was spent buying
