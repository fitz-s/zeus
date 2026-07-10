# Created: 2026-07-03
# Last reused/audited: 2026-07-09
"""SolveEngineShim.decide() — phase-1 shim + LegacyDecisionProjection (W3.3 sub-slice 3).

The shim composes an inner engine for the FamilyDecision scaffolding and REPLACES the selection
with the joint solver, re-scoring the primary leg standalone at its post-haircut size. Tests
inject a fake engine returning a real FamilyDecision with lightweight fake sub-objects, a fake
route surface, and injected spendable cash — no live readers/ledger required.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import numpy as np

from src.contracts.executable_cost_curve import BookLevel, ExecutableCostCurve, FeeModel
from src.contracts.execution_price import ExecutionPrice
from src.contracts.native_side_candidate import NativeSideCandidate
from src.decision.family_decision_engine import CandidateDecision, FamilyDecision
from src.decision.payoff_vector import CandidateEconomics
from src.solve.solver import SolveEngineShim, validate_family_decision_contract
from src.strategy.utility_ranker import FamilyPayoffMatrix, PortfolioExposureVector
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
        route_id=route_id, route_type=f"DIRECT_{side}", instrument=_FakeInstrument(bin_id, side),
        shares=Decimal(str(shares)),
        avg_cost=ExecutionPrice(
            value=float(cost), price_type="fee_adjusted", fee_deducted=True,
            currency="probability_units",
        ),
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


def _route_set(direct_yes, direct_no=None):
    return SimpleNamespace(
        direct_yes=direct_yes, direct_no=direct_no or {}, synthetic_not_i={},
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
    joint_q = SimpleNamespace(
        omega=SimpleNamespace(bins=[SimpleNamespace(bin_id=b) for b in bin_ids]),
        q=np.mean(samples, axis=0), identity_hash="qv_abc",
    )
    return SimpleNamespace(samples=samples, joint_q=joint_q, sample_hash=sample_hash, alpha=alpha)


def _legacy_decision(bin_ids, routes, band, *, joint_q_none=False):
    route_costs = tuple(routes)
    omega = SimpleNamespace(bins=[SimpleNamespace(bin_id=b) for b in bin_ids])
    candidate_routes = tuple(
        SimpleNamespace(
            candidate_id=route.route_id,
            instrument=route.instrument,
            route_cost=route,
            payoff_vector=route.instrument.payoff_vector(omega),
            side=route.instrument.side,
            bin_id=route.instrument.bin_id,
        )
        for route in route_costs
    )
    candidates = tuple(_econ(route.route_id) for route in route_costs)
    cds = tuple(
        CandidateDecision(
            route=route, economics=e, direction_law_ok=True,
            coherence_allows=False, robust_trade_score=0.1,
        )
        for route, e in zip(candidate_routes, candidates)
    )
    return FamilyDecision(
        decision_id="tokyo@t", case=SimpleNamespace(family_id="fam"), predictive=SimpleNamespace(),
        omega=SimpleNamespace(bins=[SimpleNamespace(bin_id=b) for b in bin_ids]),
        joint_q=None if joint_q_none else band.joint_q,
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
    matrix = FamilyPayoffMatrix.over_bins(bin_ids)
    portfolio = PortfolioExposureVector.flat(
        matrix, baseline=Decimal(str(portfolio_wealth))
    )
    legacy = shim._engine._decision
    sizing_candidates = {}
    if legacy.band is not None:
        for decision in legacy.candidate_decisions:
            route = decision.route
            cost = Decimal(str(route.route_cost.avg_cost.value))
            curve = ExecutableCostCurve(
                token_id=f"tok_{route.side}_{route.bin_id}", side=route.side,
                snapshot_id="snap", book_hash=f"book_{route.side}_{route.bin_id}",
                levels=(BookLevel(price=cost, size=Decimal("100000")),),
                fee_model=FeeModel(fee_rate=Decimal("0")), min_tick=Decimal("0.01"),
                min_order_size=Decimal("0.01"), quote_ttl=timedelta(seconds=10),
            )
            payoff_samples = legacy.band.samples @ route.payoff_vector
            q_point = float(legacy.joint_q.q @ route.payoff_vector)
            sizing_candidates[(route.bin_id, route.side)] = NativeSideCandidate.tradeable(
                family_key="fam", bin_id=route.bin_id, side=route.side,
                token_id=curve.token_id, condition_id=f"cond_{route.bin_id}",
                q_point=q_point,
                q_lcb=min(q_point, float(np.quantile(payoff_samples, legacy.band.alpha))),
                probability_uncertainty=None, executable_cost_curve=curve,
                forecast_snapshot_id="forecast", market_snapshot_id="market",
                hypothesis_id=f"hyp_{route.side}_{route.bin_id}",
            )
    omega = SimpleNamespace(bins=[SimpleNamespace(bin_id=b) for b in bin_ids])
    return shim.decide(
        SimpleNamespace(family_id="fam"), omega,
        {}, portfolio=portfolio, matrix=matrix, captured_at_utc=None,
        sizing_candidates=sizing_candidates, max_stake_usd=None, shares_for_routing=Decimal("100"),
        served_joint_q=None, served_band=None, served_payoff_q_lcb_by_side=None,
    )


# --- tests ------------------------------------------------------------------

def test_shim_trades_and_stamps_projection(monkeypatch):
    # Pin the downstream-haircut factor to a known < 1 so the projection is re-scored at a strictly
    # SMALLER post-haircut size than the joint plan — makes the projection/plan distinction
    # deterministic regardless of the ambient config kelly_multiplier.
    import src.solve.solver as S
    monkeypatch.setattr(S, "_read_config_kelly_multiplier", lambda: 0.5)

    bin_ids = ("y", "n")
    band = _band(bin_ids, [0.75] * 64)  # clear edge on y vs cost 0.45
    route = _route("r_y", "y", "YES", 0.45)
    legacy = _legacy_decision(bin_ids, (route,), band)
    rs = _route_set({"y": route})
    shim = _shim(legacy, rs)
    d = _decide(shim)
    assert d.selected is not None
    assert d.selected.route_id == "r_y"
    assert d.no_trade_reason is None
    # projection stamped into selected: optimal_delta_u == standalone primary ΔU
    proj = shim.last_projection
    assert proj is not None and proj.phase1_tradeable
    assert d.selected.optimal_delta_u == proj.standalone_primary_delta_u > 0.0
    # DISTINCTION (verifier finding): the projection ΔU (single primary leg re-scored at the
    # post-haircut-SMALLER size) must NOT be the joint plan's ΔU. The concave objective at a
    # strictly smaller stake is strictly less, so this is the tighter `<` variant — a refactor that
    # sourced both from plan.expected_delta_log_wealth would violate it.
    assert shim.last_plan is not None
    assert d.selected.optimal_delta_u < shim.last_plan.expected_delta_log_wealth
    assert d.selected.optimal_delta_u != shim.last_plan.expected_delta_log_wealth
    # coherence lockstep: every candidate_decision emits coherence_allows=True
    assert all(cd.coherence_allows is True for cd in d.candidate_decisions)
    assert d.receipt_hash and d.receipt_hash != "legacy_hash"
    from src.engine import event_reactor_adapter as era
    from src.engine import qkernel_spine_bridge as bridge

    selected_decision = next(
        cd for cd in d.candidate_decisions if cd.economics.route_id == d.selected.route_id
    )
    serialized = bridge._candidate_qkernel_execution_economics_payload(
        d,
        selected_decision,
        selected=d.selected,
    )
    assert serialized is not None
    assert era._qkernel_current_state_solve_economics(serialized) is True
    validate_family_decision_contract(d)  # loud if any consumer field broke


def test_shim_no_trade_on_zero_edge():
    bin_ids = ("y", "n")
    band = _band(bin_ids, [0.45] * 64)  # 0.45 win vs 0.45 cost -> no edge
    route = _route("r_y", "y", "YES", 0.45)
    legacy = _legacy_decision(bin_ids, (route,), band)
    rs = _route_set({"y": route})
    d = _decide(_shim(legacy, rs))
    assert d.selected is None
    assert d.no_trade_reason is not None
    validate_family_decision_contract(d)


def test_shim_yes_no_mirror_uses_one_current_state_rule():
    """YES_y and NO_n are the same payoff, so equal costs must score identically."""

    from dataclasses import replace

    bin_ids = ("y", "n")
    band = _band(bin_ids, [0.70] * 64)
    yes = _route("yes_y", "y", "YES", 0.45)
    no = _route("no_n", "n", "NO", 0.45)
    legacy = _legacy_decision(bin_ids, (yes, no), band)
    legacy = replace(
        legacy,
        candidate_decisions=tuple(
            replace(
                decision,
                direction_law_ok=False,
                coherence_allows=False,
                q_lcb_guard_basis="HISTORICAL_RELIABILITY",
                q_lcb_guard_abstained=True,
                selection_guard_basis="HISTORICAL_SELECTION",
                selection_guard_abstained=True,
            )
            for decision in legacy.candidate_decisions
        ),
    )
    decision = _decide(
        _shim(legacy, _route_set({"y": yes}, {"n": no})),
        bin_ids=bin_ids,
    )

    by_side = {candidate.route.side: candidate for candidate in decision.candidate_decisions}
    assert set(by_side) == {"YES", "NO"}
    assert by_side["YES"].economics.point_ev == by_side["NO"].economics.point_ev
    assert by_side["YES"].economics.edge_lcb == by_side["NO"].economics.edge_lcb
    # The selected leg is later re-stamped at the post-haircut execution size, so compare the
    # pre-size local utility boundary rather than the selected projection's final objective.
    assert by_side["YES"].economics.delta_u_at_min == by_side["NO"].economics.delta_u_at_min
    for candidate in by_side.values():
        assert candidate.direction_law_ok is True
        assert candidate.coherence_allows is True
        assert candidate.q_lcb_guard_basis == "CURRENT_POSTERIOR_BAND"
        assert candidate.selection_guard_basis == "CURRENT_POSTERIOR_BAND"
        assert candidate.q_lcb_guard_abstained is False
        assert candidate.selection_guard_abstained is False


def test_shim_no_trade_on_negative_standalone_primary_hedge():
    # A two-leg diversification hedge: each leg alone is negative, the pair is positive. The solver
    # FINDS it but rejects it as UNSAFE_PREFIX_DECOMPOSITION (the primary leg alone is negative), so
    # the shim never submits a primary leg that is only good because of an unexecuted hedge.
    bin_ids = ("b0", "b1", "b2")
    n = 200
    c0 = np.tile([0.66, 0.29, 0.05], (n // 2, 1))
    c1 = np.tile([0.29, 0.66, 0.05], (n // 2, 1))
    samples = np.vstack([c0, c1])
    joint_q = SimpleNamespace(
        omega=SimpleNamespace(bins=[SimpleNamespace(bin_id=b) for b in bin_ids]),
        q=np.mean(samples, axis=0), identity_hash="qv_abc",
    )
    band = SimpleNamespace(samples=samples, joint_q=joint_q, sample_hash="h", alpha=0.1)
    route0 = _route("L0", "b0", "YES", 0.40)
    route1 = _route("L1", "b1", "YES", 0.40)
    legacy = _legacy_decision(bin_ids, (route0, route1), band)
    rs = _route_set({"b0": route0, "b1": route1})
    d = _decide(_shim(legacy, rs), bin_ids=bin_ids, portfolio_wealth=200.0)
    assert d.selected is None
    assert d.no_trade_reason == "UNSAFE_PREFIX_DECOMPOSITION"
    validate_family_decision_contract(d)


def test_shim_ineligible_passthrough():
    route = _route("r_y", "y", "YES", 0.45)
    legacy = _legacy_decision(("y", "n"), (route,), None, joint_q_none=True)
    rs = _route_set({"y": route})
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
