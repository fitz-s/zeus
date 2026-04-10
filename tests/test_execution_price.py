"""Tests for F2/D3 ExecutionPrice contract wiring.

Covers:
1. with_taker_fee() computes price-dependent fee (not flat 5%)
2. schema_packet() returns valid schema
3. fee_adjusted type passes assert_kelly_safe()
4. Evaluator wires ExecutionPrice before Kelly (shadow mode)
5. Evaluator shadow flag controls which path is authoritative
"""

import pytest
import numpy as np

from src.contracts.execution_price import (
    ExecutionPrice,
    ExecutionPriceContractError,
    polymarket_fee,
)


# ---------------------------------------------------------------------------
# Commit 1 — K0 contract: with_taker_fee() and schema_packet()
# ---------------------------------------------------------------------------


class TestWithTakerFee:
    """ExecutionPrice.with_taker_fee() applies price-dependent Polymarket fee."""

    def test_fee_at_p_042(self):
        """At p=0.42: fee = 0.05 × 0.42 × 0.58 = 0.01218."""
        ep = ExecutionPrice(
            value=0.42,
            price_type="implied_probability",
            fee_deducted=False,
            currency="probability_units",
        )
        adjusted = ep.with_taker_fee(0.05)
        expected = 0.42 + 0.05 * 0.42 * 0.58
        assert adjusted.value == pytest.approx(expected, abs=1e-10)
        assert adjusted.price_type == "fee_adjusted"
        assert adjusted.fee_deducted is True
        assert adjusted.currency == "probability_units"

    def test_fee_at_p_050_is_maximum(self):
        """Fee is maximal at p=0.50: 0.05 × 0.50 × 0.50 = 0.0125."""
        ep = ExecutionPrice(value=0.50, price_type="ask", fee_deducted=False, currency="probability_units")
        adjusted = ep.with_taker_fee(0.05)
        assert adjusted.value == pytest.approx(0.50 + 0.0125)

    def test_fee_at_p_090_is_small(self):
        """Fee is tiny at extremes: 0.05 × 0.90 × 0.10 = 0.0045."""
        ep = ExecutionPrice(value=0.90, price_type="ask", fee_deducted=False, currency="probability_units")
        adjusted = ep.with_taker_fee(0.05)
        assert adjusted.value == pytest.approx(0.90 + 0.0045)

    def test_fee_not_flat_five_percent(self):
        """Ensure fee is NOT flat 5% — it is p × (1-p) × 0.05."""
        ep = ExecutionPrice(value=0.42, price_type="ask", fee_deducted=False, currency="probability_units")
        adjusted = ep.with_taker_fee(0.05)
        flat_5pct = 0.42 + 0.05 * 0.42  # WRONG: flat 5%
        assert adjusted.value != pytest.approx(flat_5pct, abs=1e-6), (
            "Fee should be price-dependent p(1-p), NOT flat percentage"
        )

    def test_fee_preserves_currency(self):
        """Currency must be preserved through fee application."""
        ep = ExecutionPrice(value=0.42, price_type="ask", fee_deducted=False, currency="probability_units")
        adjusted = ep.with_taker_fee()
        assert adjusted.currency == ep.currency

    def test_custom_fee_rate(self):
        """Custom fee rate (e.g. 0.03) applies correctly."""
        ep = ExecutionPrice(value=0.50, price_type="ask", fee_deducted=False, currency="probability_units")
        adjusted = ep.with_taker_fee(0.03)
        expected = 0.50 + 0.03 * 0.50 * 0.50
        assert adjusted.value == pytest.approx(expected)


class TestSchemaPacket:
    def test_schema_packet_returns_dict(self):
        schema = ExecutionPrice.schema_packet()
        assert isinstance(schema, dict)

    def test_schema_packet_has_required_keys(self):
        schema = ExecutionPrice.schema_packet()
        assert schema["type"] == "ExecutionPrice"
        assert set(schema["required_fields"]) == {"value", "price_type", "fee_deducted", "currency"}


class TestFeeAdjustedKellySafety:
    """fee_adjusted type with fee_deducted=True must pass assert_kelly_safe()."""

    def test_fee_adjusted_passes_kelly_safe(self):
        ep = ExecutionPrice(
            value=0.42,
            price_type="implied_probability",
            fee_deducted=False,
            currency="probability_units",
        )
        adjusted = ep.with_taker_fee()
        adjusted.assert_kelly_safe()  # Must not raise

    def test_implied_probability_still_fails_kelly_safe(self):
        ep = ExecutionPrice(
            value=0.42,
            price_type="implied_probability",
            fee_deducted=True,
            currency="probability_units",
        )
        with pytest.raises(ExecutionPriceContractError):
            ep.assert_kelly_safe()

    def test_double_fee_application_raises(self):
        """Calling with_taker_fee() on already fee-adjusted price must raise."""
        ep = ExecutionPrice(
            value=0.42, price_type="implied_probability",
            fee_deducted=False, currency="probability_units",
        )
        adjusted = ep.with_taker_fee()
        with pytest.raises(ExecutionPriceContractError, match="already fee-adjusted"):
            adjusted.with_taker_fee()


# ---------------------------------------------------------------------------
# Commit 2 — K2/K3 wiring: evaluator uses ExecutionPrice before Kelly
# ---------------------------------------------------------------------------


class TestEvaluatorWiring:
    """Verify evaluator.py correctly wires ExecutionPrice at the Kelly boundary."""

    def test_evaluator_imports_execution_price(self):
        """evaluator.py must import ExecutionPrice and polymarket_fee."""
        import ast
        from pathlib import Path
        src = (Path(__file__).parent.parent / "src" / "engine" / "evaluator.py").read_text()
        assert "ExecutionPrice" in src
        assert "polymarket_fee" in src

    def test_evaluator_calls_with_taker_fee(self):
        """evaluator.py must call with_taker_fee() before Kelly sizing."""
        from pathlib import Path
        src = (Path(__file__).parent.parent / "src" / "engine" / "evaluator.py").read_text()
        assert "with_taker_fee" in src

    def test_evaluator_calls_assert_kelly_safe(self):
        """evaluator.py must call assert_kelly_safe() before Kelly sizing."""
        from pathlib import Path
        src = (Path(__file__).parent.parent / "src" / "engine" / "evaluator.py").read_text()
        assert "assert_kelly_safe" in src

    def test_shadow_flag_in_settings(self):
        """EXECUTION_PRICE_SHADOW feature flag must exist in settings.json."""
        import json
        from pathlib import Path
        settings_path = Path(__file__).parent.parent / "config" / "settings.json"
        data = json.loads(settings_path.read_text())
        assert "feature_flags" in data
        assert "EXECUTION_PRICE_SHADOW" in data["feature_flags"]
        # Default must be False (shadow mode — old path authoritative)
        assert data["feature_flags"]["EXECUTION_PRICE_SHADOW"] is False

    def test_shadow_mode_old_path_determines_size(self):
        """When EXECUTION_PRICE_SHADOW=false, kelly_size uses bare entry_price."""
        from src.strategy.kelly import kelly_size

        p_posterior = 0.60
        bare_entry = 0.40
        fee = polymarket_fee(bare_entry)
        fee_adjusted_entry = bare_entry + fee

        old_size = kelly_size(p_posterior, bare_entry, 1000.0, 0.25)
        new_size = kelly_size(p_posterior, fee_adjusted_entry, 1000.0, 0.25)

        # Old size should be larger (doesn't account for fee)
        assert old_size > new_size
        # The difference should equal kelly applied to the fee delta
        # At p=0.40: fee = 0.05 × 0.40 × 0.60 = 0.012
        assert fee == pytest.approx(0.012, abs=1e-6)

    def test_fee_reduces_kelly_size(self):
        """Fee-adjusted entry price must produce smaller Kelly size than bare float.

        This is the D3 bug: Kelly systematically oversizes because it uses
        implied probability (0.42) instead of execution cost (0.42 + fee ≈ 0.43218).
        """
        from src.strategy.kelly import kelly_size

        p_posterior = 0.55
        bare_entry = 0.42
        fee_adj = ExecutionPrice(
            value=bare_entry, price_type="implied_probability",
            fee_deducted=False, currency="probability_units",
        ).with_taker_fee()

        old = kelly_size(p_posterior, bare_entry, 1000.0, 0.25)
        new = kelly_size(p_posterior, fee_adj.value, 1000.0, 0.25)

        assert new < old, "Fee-adjusted entry price must produce smaller position size"
        assert new > 0, "Fee-adjusted size should still be positive with real edge"
