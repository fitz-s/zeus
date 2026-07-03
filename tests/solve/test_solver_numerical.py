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
    w0, payoff, caps, items = S._build_arrays(m, w, atom_ids)
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


def test_max_orders_cap_enforced():
    bins = tuple(f"b{j}" for j in range(20))
    q = np.random.default_rng(5).dirichlet(np.full(20, 1.0 / 20) * 400, size=400)
    items = [F.buy_item(f"it{j}", f"b{j}", 0.02, bins, max_units=2000) for j in range(20)]
    plan = _solve(F.menu(items), q, F.flat_wealth_state(bins, 500.0), bins=bins)
    assert len(plan.orders) <= 15
    if plan.orders:
        assert all(len(chunk) <= 15 for chunk in plan.repair_certificate.batch_partition)
