"""Tests for bare float seam elimination at Kelly and exit boundaries. §P9.7, INV-12, D3."""
import ast
import inspect
from pathlib import Path

import pytest

from src.contracts.execution_price import ExecutionPrice, ExecutionPriceContractError
from src.contracts.expiring_assumption import ExpiringAssumption
from src.strategy.kelly import kelly_size

ZEUS_ROOT = Path(__file__).parent.parent
KELLY_PY = ZEUS_ROOT / "src" / "strategy" / "kelly.py"
EXIT_TRIGGERS_PY = ZEUS_ROOT / "src" / "execution" / "exit_triggers.py"


# ---------------------------------------------------------------------------
# ExecutionPrice construction
# ---------------------------------------------------------------------------

class TestExecutionPriceConstruction:

    def test_vwmp_fee_included_constructs(self):
        ep = ExecutionPrice(
            value=0.42,
            price_type="vwmp",
            fee_deducted=True,
            currency="probability_units",
        )
        assert ep.value == pytest.approx(0.42)
        assert ep.fee_deducted is True

    def test_ask_price_constructs(self):
        ep = ExecutionPrice(
            value=0.40,
            price_type="ask",
            fee_deducted=False,
            currency="probability_units",
        )
        assert ep.price_type == "ask"

    def test_negative_value_raises(self):
        with pytest.raises(ValueError):
            ExecutionPrice(value=-0.01, price_type="vwmp", fee_deducted=True, currency="probability_units")

    def test_probability_units_over_one_raises(self):
        with pytest.raises(ValueError):
            ExecutionPrice(value=1.01, price_type="ask", fee_deducted=True, currency="probability_units")

    def test_implied_probability_type_allowed_at_construction(self):
        """implied_probability is valid at construction; assert_kelly_safe() catches misuse."""
        ep = ExecutionPrice(
            value=0.40,
            price_type="implied_probability",
            fee_deducted=False,
            currency="probability_units",
        )
        assert ep.price_type == "implied_probability"


# ---------------------------------------------------------------------------
# assert_kelly_safe — INV-12 contract
# ---------------------------------------------------------------------------

class TestNoBareFloatAtKellyBoundary:
    """ExecutionPrice.assert_kelly_safe() enforces D3/INV-12."""

    def test_implied_probability_fails_kelly_safe(self):
        """implied_probability type is not a valid Kelly entry cost — fails."""
        ep = ExecutionPrice(
            value=0.40,
            price_type="implied_probability",
            fee_deducted=True,
            currency="probability_units",
        )
        with pytest.raises(ExecutionPriceContractError, match="implied_probability"):
            ep.assert_kelly_safe()

    def test_fee_not_deducted_fails_kelly_safe(self):
        """fee_deducted=False at Kelly boundary causes oversizing — fails."""
        ep = ExecutionPrice(
            value=0.40,
            price_type="ask",
            fee_deducted=False,
            currency="probability_units",
        )
        with pytest.raises(ExecutionPriceContractError, match="fee"):
            ep.assert_kelly_safe()

    def test_usd_currency_fails_kelly_safe(self):
        """currency='usd' at Kelly boundary fails — Kelly needs probability_units."""
        ep = ExecutionPrice(
            value=0.40,
            price_type="vwmp",
            fee_deducted=True,
            currency="usd",
        )
        with pytest.raises(ExecutionPriceContractError, match="currency|probability"):
            ep.assert_kelly_safe()

    def test_safe_execution_price_passes(self):
        """vwmp + fee_deducted + probability_units is Kelly-safe."""
        ep = ExecutionPrice(
            value=0.42,
            price_type="vwmp",
            fee_deducted=True,
            currency="probability_units",
        )
        ep.assert_kelly_safe()  # Must not raise

    def test_ask_fee_included_passes(self):
        """ask + fee_deducted=True + probability_units passes."""
        ep = ExecutionPrice(
            value=0.41,
            price_type="ask",
            fee_deducted=True,
            currency="probability_units",
        )
        ep.assert_kelly_safe()  # Must not raise

    def test_error_message_is_informative(self):
        """Error from implied_probability names the violation."""
        ep = ExecutionPrice(
            value=0.40,
            price_type="implied_probability",
            fee_deducted=False,
            currency="probability_units",
        )
        with pytest.raises(ExecutionPriceContractError) as exc_info:
            ep.assert_kelly_safe()
        msg = str(exc_info.value)
        assert "INV-12" in msg or "Kelly" in msg

    def test_implied_prob_understates_actual_cost(self):
        """Execution price (ask+fee) > implied probability — documents D3 gap."""
        implied_prob = 0.40
        execution = ExecutionPrice(
            value=0.42,  # ask + ~5% taker fee
            price_type="ask",
            fee_deducted=True,
            currency="probability_units",
        )
        assert execution.value > implied_prob, (
            "Execution price must exceed implied probability (fee+slippage). "
            "This documents the D3 systematic Kelly oversizing."
        )

    def test_kelly_size_remains_numeric_kernel(self):
        """Kelly stays numeric; evaluator owns the ExecutionPrice boundary."""
        size = kelly_size(
            p_posterior=0.60,
            entry_price=0.40,
            bankroll=1000.0,
            kelly_mult=0.25,
        )
        assert size > 0.0, "kelly_size remains a numeric kernel behind the evaluator seam"


# ---------------------------------------------------------------------------
# Exit trigger thresholds use named callables and ExpiringAssumption
# ---------------------------------------------------------------------------

class TestNoBareFloatInExitTriggerThresholds:

    def test_exit_triggers_threshold_functions_exist(self):
        """portfolio.py exports named threshold functions."""
        from src.state.portfolio import (
            buy_no_edge_threshold,
            buy_yes_edge_threshold,
            conservative_forward_edge,
            consecutive_confirmations,
        )
        assert callable(buy_no_edge_threshold)
        assert callable(buy_yes_edge_threshold)
        assert callable(conservative_forward_edge)

    def test_threshold_functions_return_numeric(self):
        """Threshold functions return float or ExpiringAssumption."""
        from src.state.portfolio import buy_no_edge_threshold
        result = buy_no_edge_threshold(entry_ci_width=0.10)
        assert isinstance(result, (float, int, ExpiringAssumption)), (
            f"buy_no_edge_threshold() returned {type(result).__name__}"
        )

    def test_expiring_assumption_is_used_in_portfolio_thresholds(self):
        """portfolio.py uses ExpiringAssumption for at least one threshold."""
        portfolio_py = ZEUS_ROOT / "src" / "state" / "portfolio.py"
        if not portfolio_py.exists():
            pytest.skip("portfolio.py not found")
        source = portfolio_py.read_text()
        assert "ExpiringAssumption" in source, (
            "portfolio.py should use ExpiringAssumption for at least one threshold. "
            "P9 requires thresholds traced to ExpiringAssumption or ProvenanceRecord."
        )

    def test_kelly_size_entry_price_currently_bare_float_annotation(self):
        """Document current state: entry_price annotated as float (INV-12 violation).

        This PASSES now. Update when evaluator.py wires ExecutionPrice at the seam.
        """
        source = KELLY_PY.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef,)) and node.name == "kelly_size":
                for arg in node.args.args:
                    if arg.arg == "entry_price":
                        ann = arg.annotation
                        # Current state: float annotation (the violation)
                        if ann is not None and isinstance(ann, ast.Name) and ann.id == "float":
                            return  # Expected current state — test passes
                        elif ann is None:
                            return  # No annotation — also pre-seam
                        else:
                            # Annotation exists and is not bare float
                            pytest.fail(
                                "entry_price annotation has changed. "
                                "Update this test to verify it's ExecutionPrice."
                            )
                return  # entry_price param found but no annotation
        pytest.skip("kelly_size not found in kelly.py")
