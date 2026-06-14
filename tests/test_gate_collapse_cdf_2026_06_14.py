# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: gate-mass collapse waves C/D (.omc/research/zeus_gate_removal_list_2026-06-13.md
#   Tier-C C1/C2 + Tier-D D1-D5); operator no-caps law (memory: no-caps-no-overengineering-2026-06-12).
#
# RELATIONSHIP TESTS (cross-module invariants + removal antibodies), NOT feature tests.
# They pin WHY each gate was removable and act as the antibody so a later session cannot
# silently re-introduce a banned cap/haircut or a redundant re-check.
#
#   C1/C2 (byte-identity): the reactor re-checks were duplicates of the upstream candidate
#     admission gates; for any admitted candidate's inputs the upstream functions return None,
#     so the reactor re-check could never fire. Pinned by asserting None on positive-EV inputs.
#   D1/D2 (direction-neutral): the 20-min reentry + 1-hr cooldown time-bans were DELETED; the
#     functions must stay gone (removal antibody is in tests/test_churn_defense.py::TestTimeBansRemoved).
#   D3/D4/D5 (decision-neutral, magnitude grows): the continuous Kelly haircuts were DELETED;
#     this file pins that the banned multiplies stay absent AND the honest gates they sat next to
#     (binary source-quality rejection, DDD Rail-1 HALT, gross/variance cluster throttles) stay live.

import inspect
import pathlib

import pytest

from src.strategy.live_inference.live_admission import (
    live_lcb_consistency_rejection_reason,
    live_capital_efficiency_rejection_reason,
)
import src.engine.evaluator as evaluator_module


def _evaluator_source() -> str:
    return pathlib.Path(evaluator_module.__file__).read_text()


# ---------------------------------------------------------------------------
# C1 / C2 — byte-identity: the reactor re-checks duplicated the upstream gate.
# ---------------------------------------------------------------------------
def test_c1_lcb_consistency_returns_none_for_admitted_inputs():
    """An admitted candidate has q_lcb <= q_posterior (consistent), so the upstream
    gate returns None. The receipt carries verbatim copies, so the removed reactor
    re-check could never have fired for an admitted candidate."""
    assert live_lcb_consistency_rejection_reason(q_direction=0.65, q_lcb=0.55) is None


def test_c2_capital_efficiency_returns_none_for_admitted_inputs():
    """An admitted candidate has positive conservative EV ((q_lcb - price)/price > 0),
    so the upstream capital-efficiency gate returns None — the removed reactor re-check
    was unreachable for admitted candidates."""
    assert live_capital_efficiency_rejection_reason(
        q_lcb=0.65, execution_price=0.45, trade_score=0.10
    ) is None


def test_c_reactor_recheck_strings_removed():
    """Removal antibody: the redundant reactor re-checks must stay gone."""
    reactor_src = pathlib.Path(
        evaluator_module.__file__
    ).parent.parent.joinpath("events", "reactor.py").read_text()
    assert "lcb_consistency_reason = live_lcb_consistency_rejection_reason" not in reactor_src
    assert "capital_efficiency_reason = live_capital_efficiency_rejection_reason" not in reactor_src
    # The buy_no conservative stanza (distinct receipt provenance) MUST remain.
    assert "live_buy_no_conservative_evidence_rejection_reason" in reactor_src


# ---------------------------------------------------------------------------
# D3 — source-quality Kelly haircut removed; binary gate stays.
# ---------------------------------------------------------------------------
def test_d3_source_quality_haircut_multiply_removed():
    src = _evaluator_source()
    assert "km *= source_quality_haircut" not in src, (
        "D3 banned continuous source-quality Kelly haircut must stay removed (no-caps law)")
    # Honest binary no-data => no-trade gate STAYS.
    assert "_source_quality_policy_rejection" in src


# ---------------------------------------------------------------------------
# D4 — DDD Rail-2 continuous discount removed; Rail-1 HALT stays.
# ---------------------------------------------------------------------------
def test_d4_ddd_rail2_haircut_removed():
    src = _evaluator_source()
    assert "km *= max(0.0, one_minus(ddd_discount))" not in src, (
        "D4 banned DDD Rail-2 continuous Kelly haircut must stay removed (no-caps law)")
    assert "ddd_discount" not in src, "ddd_discount must be fully removed, not left dangling"
    # Honest Rail-1 HALT (binary no-trade on catastrophic coverage) STAYS.
    assert "DDD_RAIL1_HALT" in src
    assert "evaluate_ddd_for_decision" in src


# ---------------------------------------------------------------------------
# D5 — global-heat double-throttle removed; gross/variance throttles + heat-in-Kelly stay.
# ---------------------------------------------------------------------------
def test_d5_heat_double_throttle_removed_others_kept():
    src = _evaluator_source()
    assert "global_heat_throttled_50pct" not in src, (
        "D5 redundant global-heat risk_throttle must stay removed (already in dynamic_kelly_mult)")
    # The gross_exp and variance_exp cluster throttles are DISTINCT quantities — they STAY.
    assert "regime_throttled_gross_50pct" in src
    assert "regime_throttled_variance_50pct" in src


def test_d5_heat_still_flows_into_kelly():
    """Removing the second heat application is safe only because dynamic_kelly_mult
    still ingests portfolio_heat (the single, honest heat attenuation)."""
    from src.strategy.kelly import dynamic_kelly_mult
    params = inspect.signature(dynamic_kelly_mult).parameters
    assert "portfolio_heat" in params, (
        "dynamic_kelly_mult must still take portfolio_heat — it is the surviving heat input")
