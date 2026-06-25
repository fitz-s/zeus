# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: docs/archive/2026-Q2/operations_historical/consolidated_systemic_overhaul_2026-06-11.md K1.1/K1.2
"""K1 antibodies for the twin formulas that must stay equivalent or unified.

K1.1 audit verdicts pinned here:
- fee shape: polymarket_fee (float) and FeeModel.fee_per_share (Decimal) are a
  DELIBERATE numeric-space twin (the cost curve never drops to float, spec §5.4).
  Twin is licensed ONLY while this golden-equivalence matrix holds.
- min-order share sizing: cert builder + reactor depth-guard previously kept
  byte-parity BY COMMENT (Bug B); both now dispatch to ONE function — pinned by
  identity + AST checks.
- relative spread: certificates/execution.py previously re-implemented the
  formula line-for-line; now dispatches to mode_consistent_ev.relative_spread.
"""

import ast
import inspect
from decimal import Decimal

import pytest

from src.contracts.executable_cost_curve import FeeModel
from src.contracts.execution_price import polymarket_fee


@pytest.mark.parametrize("fee_rate", [0.0, 0.02, 0.05, 0.10])
@pytest.mark.parametrize(
    "price", [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]
)
def test_fee_shape_float_decimal_golden_equivalence(price, fee_rate):
    """The two fee-shape implementations agree to float precision on a dense grid."""
    float_fee = polymarket_fee(price, fee_rate)
    decimal_fee = FeeModel(fee_rate=Decimal(str(fee_rate))).fee_per_share(
        Decimal(str(price))
    )
    assert float_fee == pytest.approx(float(decimal_fee), abs=1e-12)


def test_min_order_share_sizing_is_one_function():
    """K1.1: the reactor depth-guard dispatches to the cert builder's
    desired_shares_for_reserved_notional — no local max()/division copy."""
    import src.decision_kernel.certificates.execution as execution_module

    shared = execution_module.desired_shares_for_reserved_notional
    assert callable(shared)
    # The cert builder body uses it (AST: call by name inside the builder).
    builder_src = inspect.getsource(
        execution_module.build_final_intent_certificate_from_actionable
    )
    assert "desired_shares_for_reserved_notional(" in builder_src
    # The adapter region references the shared symbol and the old local formula
    # pattern is dead.
    with open("src/engine/event_reactor_adapter.py", encoding="utf-8") as fh:
        adapter_src = fh.read()
    assert "desired_shares_for_reserved_notional" in adapter_src
    assert "_min_order_size_f, _reserved_notional_f / _limit_price_f" not in adapter_src


@pytest.mark.parametrize(
    "min_order,notional,limit,expected",
    [
        (1.0, 0.0, 0.5, 1.0),
        (5.0, 1.0, 0.5, 5.0),
        (1.0, 10.0, 0.5, 20.0),
        (1.0, 10.0, 0.0, 1.0),  # zero limit -> min order (fail-safe branch)
        (1.0, 5.0, 0.6, max(1.0, 5.0 / 0.6)),  # float division IS the contract
    ],
)
def test_desired_shares_golden_matrix(min_order, notional, limit, expected):
    from src.decision_kernel.certificates.execution import (
        desired_shares_for_reserved_notional,
    )

    assert desired_shares_for_reserved_notional(min_order, notional, limit) == expected


def test_relative_spread_at_entry_dispatches_to_shared_formula():
    """K1.1: certificates/execution._relative_spread_at_entry must CALL the shared
    relative_spread and not carry a local mid/spread formula."""
    import src.decision_kernel.certificates.execution as execution_module

    src = inspect.getsource(execution_module._relative_spread_at_entry)
    tree = ast.parse(src)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "relative_spread" in calls
    # No local arithmetic re-implementation: division would betray a local formula.
    assert not any(
        isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
        for node in ast.walk(tree)
    ), "_relative_spread_at_entry re-implements the spread formula locally"


@pytest.mark.parametrize(
    "bid,ask",
    [
        (0.40, 0.60),
        (0.01, 0.99),
        (0.50, 0.50),
        (None, 0.60),
        (0.40, None),
        (0.0, 0.60),
        (0.60, 0.40),  # crossed
        (float("nan"), 0.60),
    ],
)
def test_relative_spread_golden_equivalence_at_both_seams(bid, ask):
    """Same inputs through both seams produce the same relative spread."""
    import src.decision_kernel.certificates.execution as execution_module
    from src.strategy.live_inference.mode_consistent_ev import relative_spread

    expected = relative_spread(bid, ask)
    got = execution_module._relative_spread_at_entry(bid, ask, None)
    assert got == expected
