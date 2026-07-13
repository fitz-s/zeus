# Created: 2026-07-03
# Last reused/audited: 2026-07-03
"""Joint outcome atom axis — the consult REV-2 refactor's load-bearing behavior.

The objective must SEE correlation: two joint beliefs with IDENTICAL per-family marginals but
different joint atom masses give different robust ΔU for a correlated stake (a marginal-only
representation could not tell them apart). Multi-family SOLVE fails closed until C4. Plus an
adapter → solve() end-to-end wiring check.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest

from src.solve import solver as S
from src.solve.kappa import Kappa, KappaPolicy
from src.solve.menu_adapter import build_solve_menu
from src.solve.scenario_service import MultiFamilyJointUnavailableError, TransitionalIndependentProduct
from tests.solve import support as F


def test_objective_sees_correlation_identical_marginals_differ_by_joint():
    # Two families A,B (2 bins each) -> 4 joint atoms; two bets: buy A=a0 and buy B=b0 (cost 0.4).
    # Atom order: (a0,b0),(a0,b1),(a1,b0),(a1,b1).
    c = 0.4
    payoff_a = np.array([1 - c, 1 - c, -c, -c])   # A=a0 pays where A=a0
    payoff_b = np.array([1 - c, -c, 1 - c, -c])   # B=b0 pays where B=b0
    payoff = np.vstack([payoff_a, payoff_b])       # (2 items, 4 atoms)
    w0 = np.full(4, 100.0)
    x = np.array([20.0, 20.0])                     # stake both bets

    # identical marginals qA=(0.6,0.4), qB=(0.6,0.4); different joints:
    independent = np.array([[0.36, 0.24, 0.24, 0.16]])   # product measure
    comonotone = np.array([[0.60, 0.00, 0.00, 0.40]])    # perfect positive dependence
    weights = np.ones(1)

    u_indep = S._objective(x, w0, payoff, independent, weights, 0.5)
    u_comono = S._objective(x, w0, payoff, comonotone, weights, 0.5)

    # marginals are identical, yet the objective differs — the atom axis captures dependence
    assert abs(u_indep - u_comono) > 1e-3
    # comonotone concentrates both-lose mass -> strictly worse log-growth for the diversified stake
    assert u_comono < u_indep


def test_multi_family_solve_fails_closed():
    def _band(bin_ids, alpha=0.05):
        bins = [SimpleNamespace(bin_id=b) for b in bin_ids]
        return SimpleNamespace(
            samples=np.random.default_rng(1).dirichlet([4, 4], size=16),
            joint_q=SimpleNamespace(omega=SimpleNamespace(bins=bins)),
            sample_hash="h",
            alpha=alpha,
        )

    menu = F.menu([F.buy_item("it", "y", 0.5, ("y", "n"))])
    wealth = F.flat_wealth_state(("y", "n"), 100.0)
    with pytest.raises(MultiFamilyJointUnavailableError):
        S.solve(
            menu,
            scenarios=TransitionalIndependentProduct(),
            wealth=wealth,
            kappa_policy=KappaPolicy(kappa=Kappa.of("1.0"), downstream_haircut_alive=True),
            bands_by_family={"famA": _band(("y", "n")), "famB": _band(("p", "q"))},
            q_version="qv",
        )


# --- adapter -> solve() end-to-end ------------------------------------------

class _FakeInstrument:
    def __init__(self, bin_id, side):
        self.bin_id, self.side = bin_id, side

    def payoff_vector(self, omega):
        ids = [b.bin_id for b in omega.bins]
        e = np.zeros(len(ids))
        i = ids.index(self.bin_id)
        if self.side == "YES":
            e[i] = 1.0
        else:
            e[:] = 1.0
            e[i] = 0.0
        return e


def _route(route_id, bin_id, side, cost, max_shares):
    return SimpleNamespace(
        route_id=route_id, route_type="DIRECT_YES", instrument=_FakeInstrument(bin_id, side),
        shares=Decimal("100"), avg_cost=SimpleNamespace(value=float(cost)), max_shares=Decimal(str(max_shares)),
        legs=(SimpleNamespace(bin_id=bin_id, token_id=f"tok_{side}_{bin_id}"),), executable=True, reason=None,
    )


def _market(bin_id):
    lad = SimpleNamespace(levels=(), min_tick_size=Decimal("0.01"), min_order_size=Decimal("0.01"))
    return SimpleNamespace(bin_id=bin_id, yes_asks=lad, yes_bids=lad, no_bids=lad)


def test_adapter_to_solver_end_to_end():
    omega = SimpleNamespace(bins=[SimpleNamespace(bin_id="y"), SimpleNamespace(bin_id="n")])
    fb = SimpleNamespace(omega=omega, markets={"y": _market("y"), "n": _market("n")})
    rs = SimpleNamespace(
        direct_yes={"y": _route("r_y", "y", "YES", 0.45, 5000)}, direct_no={}, synthetic_not_i={},
        pair_arbs=(), full_basket_arbs=(), conversion_routes=(),
    )
    w = F.flat_wealth_state(("y", "n"), 200.0)
    menu = build_solve_menu(rs, family_key="fam", family_book=fb, wealth=w)
    sset = F.scenarios_single_family(("y", "n"), F.two_bin_q_draws([0.72] * 64), alpha=0.05)
    plan = S.solve(
        menu, scenarios=F.StubScenarioService(sset), wealth=w,
        kappa_policy=KappaPolicy(kappa=Kappa.of("1.0"), downstream_haircut_alive=True),
        bands_by_family=F.bands(), q_version="qv_e2e",
    )
    assert plan.orders, "adapted menu with a clear edge should trade"
    order = plan.orders[0]
    assert order.menu_item_id == "r_y"
    assert order.side == "buy"
    assert order.token_id == "tok_YES_y"
    assert order.q_version == "qv_e2e"
    assert order.ledger_snapshot_id == "ledger_test"
    assert plan.menu_hash == menu.menu_hash
    assert plan.repair_certificate.repaired_objective > 0.0
