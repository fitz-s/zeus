# Created: 2026-07-03
# Last reused/audited: 2026-07-03
"""Type-level validators + invariants (consult REV-2 blocker 5 + ruling 6).

JointOutcomeScenarioSet constructor validation (finite / simplex / weights / dtype / hash),
the Kappa Decimal value object + double-shading guard, and the SolutionPlan repair-certificate
invariant (a non-empty plan must carry a certificate proving repaired ΔU > 0).
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest

from src.solve.kappa import Kappa, KappaPolicy, promotion_window_policy
from src.solve.types import (
    JointOutcomeAtom,
    JointOutcomeScenarioSet,
    PlannedOrder,
    RepairCertificate,
    ScenarioValidationError,
    SolutionPlan,
)


def _atoms(n):
    return [JointOutcomeAtom.of({"fam": f"b{j}"}) for j in range(n)]


def _valid_draws():
    return np.array([[0.6, 0.4], [0.55, 0.45], [0.7, 0.3]], dtype=np.float64)


def _build(**over):
    kw = dict(
        atoms=_atoms(2),
        q_draws=_valid_draws(),
        semantics="POSTERIOR_Q_DRAWS",
        alpha=0.05,
        provider="p",
        provider_version="v1",
        band_hashes_by_family={"fam": "h"},
    )
    kw.update(over)
    return JointOutcomeScenarioSet.build(**kw)


# --- scenario validators ----------------------------------------------------

def test_build_canonicalizes_dtype_and_hashes():
    s = _build(q_draws=[[0.6, 0.4], [0.55, 0.45], [0.7, 0.3]])  # list -> float64
    assert s.q_draws.dtype == np.float64
    assert len(s.scenario_hash) == 64


def test_non_simplex_rows_rejected():
    with pytest.raises(ScenarioValidationError, match="simplex"):
        _build(q_draws=np.array([[0.6, 0.5], [0.7, 0.3]], dtype=np.float64))


def test_row_sum_normalization_enforced_for_all_semantics():
    # A probability measure over the fixed atom axis must normalize regardless of provenance
    # (consult REV-2 verifier finding): sum=0 (zeroed scenario) and sum=5 (5x inflation) rows
    # must be rejected under PRODUCT_MEASURE and MEASURED_JOINT, not just POSTERIOR_Q_DRAWS.
    for semantics in ("PRODUCT_MEASURE", "MEASURED_JOINT"):
        with pytest.raises(ScenarioValidationError, match="simplex"):
            _build(semantics=semantics, q_draws=np.array([[0.0, 0.0], [0.7, 0.3]], dtype=np.float64))
        with pytest.raises(ScenarioValidationError, match="simplex"):
            _build(semantics=semantics, q_draws=np.array([[3.0, 2.0], [0.7, 0.3]], dtype=np.float64))
        # a properly-normalized set of the same semantics is accepted
        _build(semantics=semantics, q_draws=np.array([[0.6, 0.4], [0.7, 0.3]], dtype=np.float64))


def test_negative_probability_rejected():
    with pytest.raises(ScenarioValidationError, match="negative"):
        _build(q_draws=np.array([[1.2, -0.2], [0.7, 0.3]], dtype=np.float64))


def test_non_finite_rejected():
    bad = np.array([[np.nan, 0.5], [0.7, 0.3]], dtype=np.float64)
    with pytest.raises(ScenarioValidationError, match="non-finite"):
        _build(q_draws=bad)


def test_shape_mismatch_rejected():
    with pytest.raises(ScenarioValidationError, match="does not match"):
        _build(atoms=_atoms(3))  # 3 atoms but 2-column draws


def test_degenerate_alpha_rejected():
    with pytest.raises(ScenarioValidationError, match="DEGENERATE_ALPHA"):
        _build(alpha=1.5)


def test_bad_draw_weights_rejected():
    with pytest.raises(ScenarioValidationError, match="draw_weights"):
        _build(draw_weights=np.array([-1.0, 1.0, 1.0]))


def test_hash_covers_weights_and_provider():
    base = _build().scenario_hash
    assert _build(draw_weights=np.array([1.0, 2.0, 1.0])).scenario_hash != base
    assert _build(provider_version="v2").scenario_hash != base
    assert _build(semantics="PRODUCT_MEASURE").scenario_hash != base


# --- kappa value object -----------------------------------------------------

def test_kappa_bounds_and_canonical():
    assert Kappa.of("1.0").as_float() == 1.0
    assert Kappa.of("0.5").canonical() == "0.5"
    assert Kappa.of("1.0").canonical() == "1"
    with pytest.raises(ValueError):
        Kappa.of("0")
    with pytest.raises(ValueError):
        Kappa.of("1.5")


def test_kappa_policy_double_shading_forbidden():
    assert promotion_window_policy().kappa.value == Decimal("1")
    with pytest.raises(ValueError, match="double-shading"):
        KappaPolicy(kappa=Kappa.of("0.5"), downstream_haircut_alive=True)
    # κ<1 is valid once the haircut is dead (W5 posture)
    KappaPolicy(kappa=Kappa.of("0.5"), downstream_haircut_alive=False)


# --- SolutionPlan repair-certificate invariant ------------------------------

def _order():
    return PlannedOrder(
        order_id="o1", menu_item_id="it", kind="buy_yes", side="buy", token_id=None,
        price=None, size=Decimal("10"), q_version="qv", safe_prefix_index=0, snapshot_id=None,
    )


def _cert(repaired):
    return RepairCertificate(
        continuous_objective=0.1, repaired_objective=repaired, chosen_source="joint",
        worst_price_model="m", tick_size_deltas={}, min_size_promoted=(), dropped_items=(),
        batch_partition=(("o1",),), safe_prefix_objective_bounds=(repaired,), budget_after_repair_usd=100.0,
    )


def _plan(**over):
    kw = dict(
        plan_id="p", family_key="fam", orders=(_order(),), expected_delta_log_wealth=0.1,
        delta_u_baseline_top1=0.05, kappa_applied=1.0, correlation_rail="caps",
        scenario_provider="p", scenario_sample_hash="h", menu_hash="mh", q_version="qv",
        no_trade_reason=None, repair_certificate=_cert(0.1),
    )
    kw.update(over)
    return SolutionPlan(**kw)


def test_nonempty_plan_requires_positive_certificate():
    _plan()  # ok
    with pytest.raises(ValueError, match="RepairCertificate"):
        _plan(repair_certificate=None)
    with pytest.raises(ValueError, match="RepairCertificate"):
        _plan(repair_certificate=_cert(-0.01))


def test_planned_order_validation():
    _order()  # ok
    with pytest.raises(ValueError, match="q_version"):
        PlannedOrder(order_id="o1", menu_item_id="it", kind="buy_yes", side="buy", token_id=None,
                     price=None, size=Decimal("10"), q_version="", safe_prefix_index=0, snapshot_id=None)
    with pytest.raises(ValueError, match="size"):
        PlannedOrder(order_id="o1", menu_item_id="it", kind="buy_yes", side="buy", token_id=None,
                     price=None, size=Decimal("0"), q_version="qv", safe_prefix_index=0, snapshot_id=None)
    with pytest.raises(ValueError, match="safe_prefix_index"):
        PlannedOrder(order_id="o1", menu_item_id="it", kind="buy_yes", side="buy", token_id=None,
                     price=None, size=Decimal("10"), q_version="qv", safe_prefix_index=-1, snapshot_id=None)
    with pytest.raises(ValueError, match="order_id"):
        PlannedOrder(order_id="", menu_item_id="it", kind="buy_yes", side="buy", token_id=None,
                     price=None, size=Decimal("10"), q_version="qv", safe_prefix_index=0, snapshot_id=None)


def test_nonempty_plan_rejects_negative_budget_and_unsafe_prefix():
    # executable-budget proof: negative budget after repair is refused
    bad_budget = _cert(0.1)
    bad_budget = RepairCertificate(
        continuous_objective=0.1, repaired_objective=0.1, chosen_source="joint", worst_price_model="m",
        tick_size_deltas={}, min_size_promoted=(), dropped_items=(), batch_partition=(("o1",),),
        safe_prefix_objective_bounds=(0.1,), budget_after_repair_usd=-5.0,
    )
    with pytest.raises(ValueError, match="budget_after_repair_usd"):
        _plan(repair_certificate=bad_budget)
    # safe-prefix positivity: a prefix that leaves worsening exposure is refused
    unsafe = RepairCertificate(
        continuous_objective=0.1, repaired_objective=0.1, chosen_source="joint", worst_price_model="m",
        tick_size_deltas={}, min_size_promoted=(), dropped_items=(), batch_partition=(("o1",),),
        safe_prefix_objective_bounds=(-0.02, 0.1), budget_after_repair_usd=100.0,
    )
    with pytest.raises(ValueError, match="safe-prefix"):
        _plan(repair_certificate=unsafe)


def test_notrade_plan_requires_reason_and_no_orders():
    SolutionPlan(
        plan_id="p", family_key="fam", orders=(), expected_delta_log_wealth=0.0,
        delta_u_baseline_top1=0.0, kappa_applied=1.0, correlation_rail="caps",
        scenario_provider="p", scenario_sample_hash="h", menu_hash="mh", q_version="qv",
        no_trade_reason="NO_EDGE", repair_certificate=None,
    )
    with pytest.raises(ValueError, match="no_trade_reason"):
        _plan(orders=(_order(),), no_trade_reason="x")  # orders + reason both set
