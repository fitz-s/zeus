# Created: 2026-07-03
# Last reused/audited: 2026-07-03
"""W3 SOLVE numerical-safety guards (design packet §3 + consult REV-2).

zero-wealth atoms (typed error), full-cash corner, degenerate books (infeasible item not a
crash), log-domain safety (W_end > 0), and the ≤15-order batch cap.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.solve import solver as S
from src.solve.exits import ZeroWealthOutcomeError
from src.solve.kappa import Kappa, KappaPolicy
from tests.solve import support as F

ALPHA = 0.05


def _solve(menu, q_draws, wealth, *, bins=("y", "n")):
    sset = F.scenarios_single_family(bins, q_draws, alpha=ALPHA)
    return S.solve(
        menu,
        scenarios=F.StubScenarioService(sset),
        wealth=wealth,
        kappa_policy=KappaPolicy(kappa=Kappa.of("1.0"), downstream_haircut_alive=True),
        bands_by_family=F.bands(),
        q_version="qv_num",
    )


def test_zero_wealth_atom_raises_typed_error():
    bins = ("y", "n")
    w = F.wealth_state({F.atom_id("y"): 0.0, F.atom_id("n"): 100.0}, 100.0)
    with pytest.raises(ZeroWealthOutcomeError, match="non-positive endowment"):
        _solve(F.menu([F.buy_item("it", "y", 0.5, bins)]), F.two_bin_q_draws([0.7] * 32), w)


def test_negative_wealth_atom_raises_typed_error():
    bins = ("y", "n")
    w = F.wealth_state({F.atom_id("y"): -5.0, F.atom_id("n"): 100.0}, 100.0)
    with pytest.raises(ZeroWealthOutcomeError):
        _solve(F.menu([F.buy_item("it", "y", 0.5, bins)]), F.two_bin_q_draws([0.7] * 32), w)


def test_missing_atom_raises():
    bins = ("y", "n")
    w = F.wealth_state({F.atom_id("y"): 100.0}, 100.0)  # 'n' atom absent
    with pytest.raises(ZeroWealthOutcomeError, match="missing atoms"):
        _solve(F.menu([F.buy_item("it", "y", 0.5, bins)]), F.two_bin_q_draws([0.7] * 32), w)


def test_full_cash_corner_stays_feasible_and_bounded():
    bins = ("y", "n")
    m = F.menu([F.buy_item("it", "y", 0.02, bins, max_units=1_000_000)])
    w = F.flat_wealth_state(bins, 100.0)
    plan = _solve(m, F.two_bin_q_draws([0.97] * 64), w)
    assert plan.orders
    atom_ids = tuple(F.atom_id(b) for b in bins)
    w0, payoff, caps, costs, items = S._build_arrays(m, w, atom_ids)
    idx = {it.item_id: i for i, it in enumerate(items)}
    x = np.zeros(payoff.shape[0])
    for o in plan.orders:
        x[idx[o.menu_item_id]] = float(o.size)
    w_end = w0 + x @ payoff
    assert np.all(w_end > 0.0), f"a bin was driven to ruin: {w_end}"
    assert np.isfinite(plan.expected_delta_log_wealth)


def test_non_executable_items_excluded():
    bins = ("y", "n")
    good = F.buy_item("good", "y", 0.5, bins)
    dead = F.buy_item("dead", "y", 0.5, bins, executable=False)
    plan = _solve(F.menu([good, dead]), F.two_bin_q_draws([0.7] * 32), F.flat_wealth_state(bins, 100.0))
    assert all(o.menu_item_id != "dead" for o in plan.orders)


def test_all_non_executable_is_clean_no_trade():
    bins = ("y", "n")
    dead = F.buy_item("dead", "y", 0.5, bins, executable=False)
    plan = _solve(F.menu([dead]), F.two_bin_q_draws([0.7] * 32), F.flat_wealth_state(bins, 100.0))
    assert plan.orders == ()
    assert plan.no_trade_reason == "NO_EXECUTABLE_MENU_ITEMS"


def test_zero_depth_item_excluded():
    bins = ("y", "n")
    zd = F.buy_item("zd", "y", 0.5, bins, max_units=0.0)
    plan = _solve(F.menu([zd]), F.two_bin_q_draws([0.7] * 32), F.flat_wealth_state(bins, 100.0))
    assert plan.orders == ()
    assert plan.no_trade_reason == "NO_EXECUTABLE_MENU_ITEMS"


def test_executable_budget_binds_when_wealth_alone_would_not():
    # W_end > 0 does NOT imply affordability (consult REV-2 follow-up blocker). A large HOLDING
    # paying in atom n keeps W_end(n) positive far beyond what the $10 spendable cash can fund: the
    # wealth bound alone would allow ~220 units (spend $110), but the executable budget caps the
    # upfront cash outlay at $10. The plan must resize to the budget, never a negative budget.
    bins = ("y", "n")
    m = F.menu([F.buy_item("yy", "y", 0.5, bins, max_units=100000)])
    w = F.wealth_state({F.atom_id("y"): 10.0, F.atom_id("n"): 110.0}, 10.0)  # $100 holding pays in n
    plan = _solve(m, F.two_bin_q_draws([0.9] * 64), w)
    assert plan.orders, "a strong edge should trade"
    order = plan.orders[0]
    spend = float(order.size) * 0.5
    assert spend <= 10.0 + 1e-6, f"plan spent {spend} > cash 10 (budget not enforced)"
    assert plan.repair_certificate.budget_after_repair_usd >= 0.0
    # budget bit HARD: capped near 20 units (spend ~$10), far below the ~220 the wealth bound allows
    assert float(order.size) <= 25.0
    assert plan.repair_certificate.budget_after_repair_usd < 1.0


def test_tail_floor_stamped():
    bins = ("y", "n")
    m = F.menu([F.buy_item("it", "y", 0.4, bins, max_units=100000)])
    w = F.flat_wealth_state(bins, 200.0)
    # 64 draws at α=0.05 -> 3.2 tail draws -> below the floor of 20
    small = _solve(m, F.two_bin_q_draws([0.75] * 64), w)
    assert small.diagnostics["tail_floor_ok"] == 0.0
    assert abs(small.diagnostics["effective_tail_draws"] - 0.05 * 64) < 1e-9
    assert small.diagnostics["point_belief"] == 0.0
    # 500 draws -> 25 tail draws -> above the floor
    big = _solve(m, F.two_bin_q_draws([0.75] * 500), w)
    assert big.diagnostics["tail_floor_ok"] == 1.0


def test_point_belief_stamped():
    bins = ("y", "n")
    m = F.menu([F.buy_item("it", "y", 0.4, bins, max_units=100000)])
    plan = _solve(m, F.two_bin_q_draws([0.75]), F.flat_wealth_state(bins, 200.0))  # one draw
    assert plan.diagnostics["point_belief"] == 1.0


def test_projector_missing_atom_raises_coverage_error():
    from decimal import Decimal

    from src.solve.solver import PayoffCoverageError
    from src.solve.types import AtomPayoffProjector, MenuItem, SolveMenu
    bins = ("y", "n")
    # projector covers only the winning atom — the losing atom would silently default to 0.0
    bad = MenuItem(
        item_id="bad", kind="buy_yes", family_key=F.FAMILY, bin_id="y", route=None,
        executable=True, non_executable_reason=None,
        unit_payoff=AtomPayoffProjector(payoff_by_atom_id={F.atom_id("y"): 0.6}, unit_cost_usd=0.4),
        max_units=Decimal("1000"), min_tick_size=Decimal("0.01"), min_order_size=Decimal("0.01"),
    )
    menu = SolveMenu(family_key=F.FAMILY, items=(bad,), menu_hash="h")
    with pytest.raises(PayoffCoverageError, match="does not cover all atoms"):
        _solve(menu, F.two_bin_q_draws([0.7] * 32), F.flat_wealth_state(bins, 100.0))


def test_lower_cvar_zero_weight_ruin_row_is_finite():
    # A zero-weight row on a -inf (ruin) draw must not become 0*-inf=NaN (consult REV-2 follow-up).
    du = np.array([0.1, 0.2, -np.inf])
    weights = np.array([1.0, 1.0, 0.0])
    val = S._lower_cvar(du, weights, 0.5)
    assert np.isfinite(val), f"CVaR should be finite when the -inf row has zero weight, got {val}"


def test_max_orders_cap_enforced():
    bins = tuple(f"b{j}" for j in range(20))
    q = np.random.default_rng(5).dirichlet(np.full(20, 1.0 / 20) * 400, size=400)
    items = [F.buy_item(f"it{j}", f"b{j}", 0.02, bins, max_units=2000) for j in range(20)]
    plan = _solve(F.menu(items), q, F.flat_wealth_state(bins, 500.0), bins=bins)
    assert len(plan.orders) <= 15
    if plan.orders:
        assert all(len(chunk) <= 15 for chunk in plan.repair_certificate.batch_partition)
