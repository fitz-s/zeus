# Created: 2026-04-07
# Last reused/audited: 2026-04-30
# Lifecycle: created=2026-04-07; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: Protect pricing/probability/execution seam boundaries from bare-float authority drift.
# Reuse: Reaudit against architecture/invariants.yaml and negative_constraints.yaml before extending.
# Authority basis: docs/operations/task_2026-04-30_reality_semantics_refactor_package/PHASE_0A_EXECUTION_PLAN.md
"""Tests for bare float seam elimination at Kelly and exit boundaries. §P9.7, INV-12, D3."""
import ast
from dataclasses import fields
import inspect
from pathlib import Path

import numpy as np
import pytest

from src.contracts.execution_intent import FinalExecutionIntent
from src.contracts.execution_price import ExecutionPrice, ExecutionPriceContractError
from src.contracts.expiring_assumption import ExpiringAssumption
from src.contracts.hold_value import HoldValue, HoldValueCostDeclarationError
from src.contracts.vig_treatment import VigTreatment
from src.strategy.kelly import kelly_size
from src.strategy.market_fusion import (
    LEGACY_POSTERIOR_MODE,
    MODEL_ONLY_POSTERIOR_MODE,
    YES_FAMILY_DEVIG_SHADOW_MODE,
    MarketPriorDistribution,
    compute_posterior,
)

ZEUS_ROOT = Path(__file__).parent.parent
KELLY_PY = ZEUS_ROOT / "src" / "strategy" / "kelly.py"
EXIT_TRIGGERS_PY = ZEUS_ROOT / "src" / "execution" / "exit_triggers.py"
PORTFOLIO_PY = ZEUS_ROOT / "src" / "state" / "portfolio.py"


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

    def test_nan_value_raises(self):
        with pytest.raises(ValueError, match="finite"):
            ExecutionPrice(value=float("nan"), price_type="vwmp", fee_deducted=True, currency="probability_units")

    def test_inf_value_raises(self):
        with pytest.raises(ValueError, match="finite"):
            ExecutionPrice(value=float("inf"), price_type="ask", fee_deducted=True, currency="probability_units")

    def test_neg_inf_value_raises(self):
        with pytest.raises(ValueError, match="finite"):
            ExecutionPrice(value=float("-inf"), price_type="bid", fee_deducted=False, currency="usd")

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


class TestVigTreatmentSeam:
    def test_model_only_posterior_rejects_raw_market_quote_vector(self):
        with pytest.raises(TypeError, match="model_only_v1 posterior cannot accept"):
            compute_posterior(
                np.array([0.6, 0.4]),
                np.array([0.42, 0.58]),
                0.5,
                posterior_mode=MODEL_ONLY_POSTERIOR_MODE,
            )

    def test_corrected_prior_mode_rejects_raw_vwmp_vector(self):
        with pytest.raises(TypeError, match="raw quote/VWMP vectors are forbidden"):
            compute_posterior(
                np.array([0.6, 0.4]),
                np.array([0.42, 0.58]),
                0.5,
                posterior_mode=YES_FAMILY_DEVIG_SHADOW_MODE,
            )

    def test_corrected_prior_mode_requires_named_market_prior_identity(self):
        prior = MarketPriorDistribution(
            probabilities=(0.45, 0.55),
            bin_labels=("70-71F", "72-73F"),
            prior_id="prior:test:complete-yes-family",
            estimator_version=YES_FAMILY_DEVIG_SHADOW_MODE,
            source_quote_hashes=("a" * 64,),
            family_complete=True,
            side_convention="YES_FAMILY",
            vig_treatment="yes_family_devig",
            freshness_status="FRESH",
            liquidity_filter_status="PASS",
            neg_risk_policy="none",
            validated_for_live=False,
        )

        posterior = compute_posterior(
            np.array([0.6, 0.4]),
            prior,
            0.5,
            posterior_mode=YES_FAMILY_DEVIG_SHADOW_MODE,
        )

        assert posterior == pytest.approx(np.array([0.525, 0.475]))

    def test_compute_posterior_rejects_zero_market_vector(self):
        with pytest.raises(ValueError, match="sum <= 0"):
            compute_posterior(
                np.array([0.6, 0.4]),
                np.array([0.0, 0.0]),
                0.5,
                posterior_mode=LEGACY_POSTERIOR_MODE,
                allow_legacy_quote_prior=True,
            )

    def test_compute_posterior_rejects_negative_market_component(self):
        with pytest.raises(ValueError, match="non-negative"):
            compute_posterior(
                np.array([0.6, 0.4]),
                np.array([-0.1, 1.1]),
                0.5,
                posterior_mode=LEGACY_POSTERIOR_MODE,
                allow_legacy_quote_prior=True,
            )

    def test_vig_treatment_constructor_rejects_negative_raw_component(self):
        with pytest.raises(ValueError, match="non-negative"):
            VigTreatment(
                raw_market_prices=np.array([-0.1, 1.1]),
                vig_factor=1.0,
                clean_prices=np.array([-0.1, 1.1]),
                applied_before_blend=True,
            )


class TestHoldValueSeam:
    def test_hold_value_requires_fee_and_time_declarations(self):
        with pytest.raises(HoldValueCostDeclarationError):
            HoldValue(
                gross_value=10.0,
                fee_cost=0.0,
                time_cost=0.0,
                net_value=10.0,
                costs_declared=[],
            )

    def test_hold_value_compute_declares_zero_costs_explicitly(self):
        hold = HoldValue.compute(gross_value=10.0, fee_cost=0.0, time_cost=0.0)

        assert hold.net_value == pytest.approx(10.0)
        assert hold.costs_declared == ["fee", "time"]

    def test_exit_ev_gate_uses_hold_value_contract(self):
        portfolio_src = PORTFOLIO_PY.read_text()
        exit_src = EXIT_TRIGGERS_PY.read_text()

        assert "HoldValue.compute" in portfolio_src
        assert "HoldValue.compute" in exit_src


class TestTailTreatmentSeam:
    def test_market_fusion_routes_tail_scale_through_tail_treatment(self):
        source = (ZEUS_ROOT / "src" / "strategy" / "market_fusion.py").read_text()

        assert "DEFAULT_TAIL_TREATMENT" in source
        assert "float(alpha) * DEFAULT_TAIL_TREATMENT.scale_factor" in source


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

    def test_fee_adjusted_implied_probability_fails_corrected_kelly_authority(self):
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

    def test_final_execution_intent_excludes_probability_and_quote_recompute_inputs(self):
        intent_fields = {field.name for field in fields(FinalExecutionIntent)}
        forbidden = {
            "p_posterior",
            "p_market",
            "vwmp",
            "entry_price",
            "bin_edge",
            "edge",
            "posterior",
            "market_prior",
        }

        assert forbidden.isdisjoint(intent_fields)
        assert {
            "final_limit_price",
            "snapshot_id",
            "snapshot_hash",
            "cost_basis_id",
            "cost_basis_hash",
        } <= intent_fields

    def test_kelly_size_with_typed_entry_price(self):
        """Kelly accepts typed ExecutionPrice; evaluator owns the wrapping boundary."""
        from src.contracts.execution_price import ExecutionPrice
        ep = ExecutionPrice(
            value=0.40,
            price_type="fee_adjusted",
            fee_deducted=True,
            currency="probability_units",
        )
        size = kelly_size(
            p_posterior=0.60,
            entry_price=ep,
            bankroll=1000.0,
            kelly_mult=0.25,
        )
        assert size > 0.0, "kelly_size with valid ExecutionPrice returns positive size"


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

    def test_monitor_bootstrap_refresh_keeps_held_quote_out_of_corrected_posterior(self):
        source = (ZEUS_ROOT / "src" / "engine" / "monitor_refresh.py").read_text()
        tree = ast.parse(source)
        refresh_fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "refresh_position"
        )
        market_analysis_calls = [
            node
            for node in ast.walk(refresh_fn)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", "")) == "MarketAnalysis"
        ]

        assert market_analysis_calls, "refresh_position should rebuild monitor bootstrap context through MarketAnalysis"
        for call in market_analysis_calls:
            posterior_keywords = [
                keyword for keyword in call.keywords if keyword.arg == "posterior_mode"
            ]
            assert len(posterior_keywords) == 1
            assert ast.unparse(posterior_keywords[0].value) == "MODEL_ONLY_POSTERIOR_MODE"
            assert not any(
                keyword.arg == "allow_legacy_quote_prior"
                for keyword in call.keywords
            )

    def test_kelly_size_entry_price_requires_execution_price(self):
        """Phase 10E DT#5 / R-BW (strict-antibody flip 2026-04-20):
        kelly_size.entry_price annotation is now strictly `ExecutionPrice` —
        bare float backward-compat is REMOVED. Pre-P10E this was a union
        `float | ExecutionPrice`; P10E tightens to ExecutionPrice-only per R10.

        Antibody shape (structural AST): parse kelly.py and assert
        `entry_price` annotation is EXACTLY `ExecutionPrice` (no `float |`).
        """
        source = KELLY_PY.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef,)) and node.name == "kelly_size":
                for arg in node.args.args:
                    if arg.arg == "entry_price":
                        ann = arg.annotation
                        if ann is None:
                            pytest.fail(
                                "Phase 10E R-BW: kelly_size.entry_price has no "
                                "annotation. Must be `ExecutionPrice` (strict)."
                            )
                        ann_text = ast.unparse(ann)
                        has_bare_float_union = "float" in ann_text and "|" in ann_text
                        has_execution_price = "ExecutionPrice" in ann_text
                        assert has_execution_price, (
                            f"Phase 10E R-BW: kelly_size.entry_price annotation "
                            f"must be `ExecutionPrice` (strict); got: {ann_text!r}"
                        )
                        assert not has_bare_float_union, (
                            f"Phase 10E R-BW: kelly_size.entry_price must NOT be "
                            f"a float union — bare-float path removed in P10E; "
                            f"got: {ann_text!r}"
                        )
                        return
                pytest.fail(
                    "Phase 10E R-BW: kelly_size signature has no entry_price param."
                )
        pytest.skip("kelly_size not found in kelly.py")
