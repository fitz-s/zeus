# Created: 2026-06-18
# Last reused or audited: 2026-06-18
# Authority basis: docs/evidence/coarse_global_removal/FINAL_no_shadow_execution_flow_2026-06-18.md §6.
#   The q_lcb empirical reliability guard is injected in family_decision_engine.decide() between
#   scoring and selection: a candidate whose reliability cell abstains gets a non-positive edge,
#   so the edge_lcb>0 filter rejects it (no trade).
"""RED-on-revert integration: the q_lcb reliability guard kills a trade in decide().

Reuses the exact tradeable family from test_family_decision_engine.py (which selects a YES_25
trade with positive edge + ΔU). With the guard INERT (no reliability artifact) the trade is
selected — byte-identical. With an injected MISCALIBRATED reliability table covering the
candidate cells, the guard abstains (q_safe=0 -> edge forced <= 0) and the decision becomes a
no-trade. Reverting the guard injection (serving band.q_lcb unconditionally) re-selects the
trade -> RED.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import src.decision.qlcb_reliability_guard as guard_mod
from src.decision.family_decision_engine import FamilyDecision
from src.probability.joint_q import build_joint_q
from src.strategy.utility_ranker import PortfolioExposureVector

from src.forecast.predictive_distribution_builder import PredictiveDistributionBuilder
from src.forecast.debias_authority import DebiasAuthority
from src.decision.family_decision_engine import forecast_bin_id
from src.execution.negrisk_routes import build_negrisk_route_set
from src.contracts.native_side_candidate import NativeSideCandidate

# Reuse the proven tradeable-family harness from the engine test.
from tests.decision.test_family_decision_engine import (
    _CAPTURED,
    _case,
    _engine,
    _family_book,
    _market_book,
    _matrix,
    _model_set,
    _no_obs,
    _outcome_space,
    _tick,
    _yes_sizing,
)


def _tradeable_family(monkeypatch):
    """Assemble the same tradeable family the engine test selects a YES_25 trade on."""
    case = _case()
    space = _outcome_space(case)
    model_set = _model_set([24.6, 25.0, 25.4], case)
    pd = PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, model_set, _no_obs(), has_fusion_capture=True
    )
    jq = build_joint_q(pd, space)
    assert forecast_bin_id(jq) == "b25"

    def factory(bin_id: str):
        fair = min(max(jq.q_by_bin_id.get(bin_id, 0.0), 0.02), 0.98)
        ya = _tick(max(fair * 0.5, 0.002))
        yb = _tick(max(ya - 0.01, 0.001))
        return _market_book(
            bin_id, yes_bid=yb, yes_ask=ya, no_bid=_tick(1 - ya), no_ask=_tick(1 - yb),
            size=5000.0,
        )

    fb = _family_book(space, factory)
    matrix = _matrix(space)
    exposure = PortfolioExposureVector.flat(matrix, baseline=Decimal("1000"))
    route_set = build_negrisk_route_set(fb, shares=Decimal("100"), enable_negrisk_routes=False)
    sizing: dict[tuple[str, str], NativeSideCandidate] = {}
    for b in space.bins:
        if not b.executable:
            continue
        t = b.bin_id
        qb = jq.q_by_bin_id[t]
        yr = route_set.direct_yes.get(t)
        if yr is not None and yr.executable:
            sizing[(t, "YES")] = _yes_sizing(
                space, t, q_point=float(qb), q_lcb=max(float(qb) - 0.03, 0.001),
                price=str(round(float(yr.avg_cost.value), 3)),
            )
    engine = _engine(monkeypatch=monkeypatch, model_set=model_set, obs=_no_obs(), family_book=fb)
    return engine, case, space, exposure, matrix, sizing


def _decide(engine, case, space, exposure, matrix, sizing):
    return engine.decide(
        case, space, snapshots={}, portfolio=exposure, matrix=matrix,
        captured_at_utc=_CAPTURED, sizing_candidates=sizing,
        max_stake_usd=Decimal("1000"), shares_for_routing=Decimal("100"),
    )


def test_guard_inert_keeps_the_trade(monkeypatch, tmp_path):
    # No reliability artifact -> INERT guard -> the trade is selected (byte-identical to today).
    monkeypatch.setattr(
        guard_mod, "_QLCB_OOF_RELIABILITY_PATH", str(tmp_path / "absent.json")
    )
    guard_mod.reset_reliability_cache()
    engine, case, space, exposure, matrix, sizing = _tradeable_family(monkeypatch)
    decision = _decide(engine, case, space, exposure, matrix, sizing)
    assert isinstance(decision, FamilyDecision)
    assert decision.selected is not None, "INERT guard must not block the trade"
    assert decision.no_trade_reason is None
    guard_mod.reset_reliability_cache()


def test_miscalibrated_cell_abstains_and_kills_the_trade(monkeypatch):
    # Inject a MISCALIBRATED reliability table: every high|L1 side-aware cell (YES/NO,
    # modal/nonmodal, every q_lcb bucket) has a deep but very LOW realized hit-rate (0.05).
    # For a candidate whose band q_lcb
    # is ~0.27, the Wilson lower bound L_g ≈ 0.035 deflates q_safe = min(band_q_lcb, L_g) ≈ 0.035,
    # so the guarded after-cost edge q_safe − cost < 0 and the candidate cannot trade. (A cell
    # whose realized frequency is far below the served q_lcb is the miscalibration the guard
    # exists to catch.) -> no positive-edge survivor -> no trade.
    bad_table: dict[str, tuple[int, float]] = {}
    for side in ("YES", "NO"):
        for pos in ("modal", "nonmodal"):
            for qb in range(len(guard_mod.QLCB_BUCKET_EDGES) - 1):
                bad_table[f"high|L1|{side}|{pos}|qb{qb}"] = (500, 0.05)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_CACHE", bad_table)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_LOADED", True)

    engine, case, space, exposure, matrix, sizing = _tradeable_family(monkeypatch)
    decision = _decide(engine, case, space, exposure, matrix, sizing)

    assert isinstance(decision, FamilyDecision)
    assert decision.selected is None, (
        "a miscalibrated q_lcb reliability cell must deflate the candidate -> no trade; the guard "
        "served band.q_lcb unconditionally (the guard injection was reverted)"
    )
    # The b25 YES candidate that WON under the inert guard now has a deflated (non-positive) edge.
    b25_yes = next(
        d for d in decision.candidate_decisions
        if d.economics.candidate_id == "YES:b25:DIRECT_YES:b25@100"
    )
    assert b25_yes.economics.edge_lcb <= 0.0, (
        "the winning b25 YES candidate kept a positive edge_lcb after a miscalibrated-cell "
        "deflation — the guard did not deflate it"
    )
    guard_mod.reset_reliability_cache()
