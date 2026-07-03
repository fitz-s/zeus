# Created: 2026-07-03
# Last reused/audited: 2026-07-03
"""SolveEngineShim.decide() — phase-1 shim + LegacyDecisionProjection (W3.3 sub-slice 3).

The shim composes an inner engine for the FamilyDecision scaffolding and REPLACES the selection
with the joint solver, re-scoring the primary leg standalone at its post-haircut size. Tests
inject a fake engine returning a real FamilyDecision with lightweight fake sub-objects, a fake
route surface, and injected spendable cash — no live readers/ledger required.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import numpy as np

from src.decision.family_decision_engine import CandidateDecision, FamilyDecision
from src.decision.payoff_vector import CandidateEconomics
from src.solve.solver import SolveEngineShim, validate_family_decision_contract
from tests.solve import support as F


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


def _route(route_id, bin_id, side, cost, max_shares=5000, shares=5000):
    return SimpleNamespace(
        route_id=route_id, route_type="DIRECT_YES", instrument=_FakeInstrument(bin_id, side),
        shares=Decimal(str(shares)), avg_cost=SimpleNamespace(value=float(cost)),
        max_shares=Decimal(str(max_shares)),
        legs=(SimpleNamespace(bin_id=bin_id, token_id=f"tok_{side}_{bin_id}"),),
        executable=True, reason=None,
    )


def _market(bin_id):
    lad = SimpleNamespace(levels=(), min_tick_size=Decimal("0.01"), min_order_size=Decimal("0.01"))
    return SimpleNamespace(bin_id=bin_id, yes_asks=lad, yes_bids=lad, no_asks=lad, no_bids=lad)


def _family_book(bin_ids):
    omega = SimpleNamespace(bins=[SimpleNamespace(bin_id=b) for b in bin_ids])
    return SimpleNamespace(omega=omega, markets={b: _market(b) for b in bin_ids})


def _route_set(direct_yes):
    return SimpleNamespace(
        direct_yes=direct_yes, direct_no={}, synthetic_not_i={},
        pair_arbs=(), full_basket_arbs=(), conversion_routes=(),
    )


def _econ(route_id):
    return CandidateEconomics(
        candidate_id=route_id, point_ev=0.1, edge_lcb=0.05, delta_u_at_min=0.01,
        optimal_stake_usd=Decimal("1"), optimal_delta_u=0.02, q_dot_payoff=0.5,
        cost=SimpleNamespace(value=0.45), route_id=route_id,
    )


def _band(bin_ids, win_probs, sample_hash="h", alpha=0.05):
    samples = F.two_bin_q_draws(win_probs) if len(bin_ids) == 2 else np.asarray(win_probs, dtype=np.float64)
    joint_q = SimpleNamespace(omega=SimpleNamespace(bins=[SimpleNamespace(bin_id=b) for b in bin_ids]))
    return SimpleNamespace(samples=samples, joint_q=joint_q, sample_hash=sample_hash, alpha=alpha)


def _legacy_decision(bin_ids, route_ids, band, *, joint_q_none=False):
    candidates = tuple(_econ(r) for r in route_ids)
    cds = tuple(
        CandidateDecision(
            route=SimpleNamespace(route_id=r), economics=e, direction_law_ok=True,
            coherence_allows=False, robust_trade_score=0.1,
        )
        for r, e in zip(route_ids, candidates)
    )
    return FamilyDecision(
        decision_id="tokyo@t", case=SimpleNamespace(family_id="fam"), predictive=SimpleNamespace(),
        omega=SimpleNamespace(bins=[SimpleNamespace(bin_id=b) for b in bin_ids]),
        joint_q=None if joint_q_none else SimpleNamespace(identity_hash="qv_abc"),
        band=None if joint_q_none else band,
        family_book=None if joint_q_none else _family_book(bin_ids),
        market_coherence=SimpleNamespace(), candidates=candidates, selected=None,
        no_trade_reason="INELIGIBLE" if joint_q_none else None, receipt_hash="legacy_hash",
        candidate_decisions=cds, market_implied_q=SimpleNamespace(), portfolio_comparisons=(),
    )


class _FakeEngine:
    def __init__(self, decision):
        self._decision = decision

    def decide(self, case, omega, snapshots, **kw):
        return self._decision


def _shim(legacy, route_set, *, spendable=100.0):
    return SolveEngineShim(
        engine=_FakeEngine(legacy),
        route_set_builder=lambda fb, shares, enable_negrisk_routes: route_set,
        enable_negrisk_routes=False,
        spendable_cash_provider=lambda: spendable,
        ledger_snapshot_id_provider=lambda: "snap1",
    )


def _decide(shim, bin_ids=("y", "n"), portfolio_wealth=100.0):
    portfolio = SimpleNamespace(a=lambda b: Decimal(str(portfolio_wealth)))
    omega = SimpleNamespace(bins=[SimpleNamespace(bin_id=b) for b in bin_ids])
    return shim.decide(
        SimpleNamespace(family_id="fam"), omega,
        {}, portfolio=portfolio, matrix=SimpleNamespace(), captured_at_utc=None,
        sizing_candidates={}, max_stake_usd=None, shares_for_routing=Decimal("100"),
        served_joint_q=None, served_band=None, served_payoff_q_lcb_by_side=None,
    )


# --- tests ------------------------------------------------------------------

def test_shim_trades_and_stamps_projection():
    bin_ids = ("y", "n")
    band = _band(bin_ids, [0.75] * 64)  # clear edge on y vs cost 0.45
    legacy = _legacy_decision(bin_ids, ("r_y",), band)
    rs = _route_set({"y": _route("r_y", "y", "YES", 0.45)})
    shim = _shim(legacy, rs)
    d = _decide(shim)
    assert d.selected is not None
    assert d.selected.route_id == "r_y"
    assert d.no_trade_reason is None
    # projection stamped into selected: optimal_delta_u == standalone primary ΔU
    proj = shim.last_projection
    assert proj is not None and proj.phase1_tradeable
    assert d.selected.optimal_delta_u == proj.standalone_primary_delta_u > 0.0
    # coherence lockstep: every candidate_decision emits coherence_allows=True
    assert all(cd.coherence_allows is True for cd in d.candidate_decisions)
    assert d.receipt_hash and d.receipt_hash != "legacy_hash"
    validate_family_decision_contract(d)  # loud if any consumer field broke


def test_shim_no_trade_on_zero_edge():
    bin_ids = ("y", "n")
    band = _band(bin_ids, [0.45] * 64)  # 0.45 win vs 0.45 cost -> no edge
    legacy = _legacy_decision(bin_ids, ("r_y",), band)
    rs = _route_set({"y": _route("r_y", "y", "YES", 0.45)})
    d = _decide(_shim(legacy, rs))
    assert d.selected is None
    assert d.no_trade_reason is not None
    validate_family_decision_contract(d)


def test_shim_no_trade_on_negative_standalone_primary_hedge():
    # A two-leg diversification hedge: each leg alone is negative, the pair is positive. The solver
    # FINDS it but rejects it as UNSAFE_PREFIX_DECOMPOSITION (the primary leg alone is negative), so
    # the shim never submits a primary leg that is only good because of an unexecuted hedge.
    bin_ids = ("b0", "b1", "b2")
    n = 200
    c0 = np.tile([0.66, 0.29, 0.05], (n // 2, 1))
    c1 = np.tile([0.29, 0.66, 0.05], (n // 2, 1))
    samples = np.vstack([c0, c1])
    joint_q = SimpleNamespace(omega=SimpleNamespace(bins=[SimpleNamespace(bin_id=b) for b in bin_ids]))
    band = SimpleNamespace(samples=samples, joint_q=joint_q, sample_hash="h", alpha=0.1)
    legacy = _legacy_decision(bin_ids, ("L0", "L1"), band)
    rs = _route_set({"b0": _route("L0", "b0", "YES", 0.40), "b1": _route("L1", "b1", "YES", 0.40)})
    d = _decide(_shim(legacy, rs), bin_ids=bin_ids, portfolio_wealth=200.0)
    assert d.selected is None
    assert d.no_trade_reason == "UNSAFE_PREFIX_DECOMPOSITION"
    validate_family_decision_contract(d)


def test_shim_ineligible_passthrough():
    legacy = _legacy_decision(("y", "n"), ("r_y",), None, joint_q_none=True)
    rs = _route_set({"y": _route("r_y", "y", "YES", 0.45)})
    d = _decide(_shim(legacy, rs))
    # ineligible legacy no-trade passes straight through, still contract-valid
    assert d.no_trade_reason == "INELIGIBLE"
    assert d.selected is None
    validate_family_decision_contract(d)


def test_shim_phase1_gate_no_trade_when_primary_leg_not_in_menu():
    # If the primary order's item is not in the (executable) menu, the phase-1 gate refuses.
    from src.solve.types import PlannedOrder, RepairCertificate, SolutionPlan, SolveMenu
    shim = SolveEngineShim(engine=_FakeEngine(None))
    ay, an = F.atom_id("y"), F.atom_id("n")
    menu = SolveMenu(family_key="fam", items=(), menu_hash="mh")  # empty menu -> item not found
    wealth = F.flat_wealth_state(("y", "n"), 100.0)
    order = PlannedOrder(
        order_id="o", menu_item_id="ghost", kind="buy_yes", side="buy", token_id=None, price=None,
        size=Decimal("10"), q_version="qv", safe_prefix_index=0, snapshot_id=None,
    )
    cert = RepairCertificate(
        continuous_objective=0.1, repaired_objective=0.1, chosen_source="joint", worst_price_model="m",
        tick_size_deltas={}, min_size_promoted=(), dropped_items=(), batch_partition=(("o",),),
        safe_prefix_objective_bounds=(0.1,), budget_after_repair_usd=100.0,
    )
    plan = SolutionPlan(
        plan_id="p", family_key="fam", orders=(order,), expected_delta_log_wealth=0.1,
        delta_u_baseline_top1=0.05, kappa_applied=1.0, correlation_rail="caps", scenario_provider="p",
        scenario_sample_hash="h", menu_hash="mh", q_version="qv", no_trade_reason=None,
        repair_certificate=cert,
    )
    svc = F.StubScenarioService(F.scenarios_single_family(("y", "n"), F.two_bin_q_draws([0.7] * 16)))
    from dataclasses import replace as _replace

    from src.solve.types import LegacyDecisionProjection
    selected, reason, proj = shim._project_primary_leg(
        plan=plan, menu=menu, wealth=wealth, scenarios=svc, bands_by_family=F.bands(),
        atom_ids=(ay, an), econ_by_route={}, replace=_replace,
        LegacyDecisionProjection=LegacyDecisionProjection,
    )
    assert selected is None
    assert reason == "PHASE1_PRIMARY_LEG_NOT_TRADEABLE"
    assert not proj.phase1_tradeable
