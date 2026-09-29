# Created: 2026-04-13
# Last reused/audited: 2026-04-29
# Authority basis: midstream verdict v2 2026-04-23 plus DSA-09 stale EXECUTION_PRICE_SHADOW cleanup
"""Tests for F2/D3 ExecutionPrice contract wiring.

Covers:
1. with_taker_fee() computes price-dependent fee (not flat 5%)
2. schema_packet() returns valid schema
3. fee_adjusted type passes assert_kelly_safe()
4. Evaluator wires ExecutionPrice before Kelly
5. Evaluator default settings make fee-adjusted sizing authoritative
"""

import pytest
import numpy as np
from decimal import Decimal

from src.contracts.alpha_decision import AlphaDecision
from src.contracts.execution_price import (
    ExecutionPrice,
    ExecutionPriceContractError,
    polymarket_fee,
)
from src.contracts.effective_kelly_context import EffectiveKellyContext
from src.strategy.market_analysis_family_scan import FullFamilyHypothesis


def _neutral_context() -> EffectiveKellyContext:
    """TIGHT/DEEP context with haircut=1.0 — neutral for tests that don't
    exercise microstructure haircut logic."""
    return EffectiveKellyContext(
        spread_usd=Decimal("0.02"),
        depth_at_best_ask=500,
        order_type="FOK",
        fee_erased=False,
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

    def test_polymarket_fee_rejects_nan(self):
        """polymarket_fee must reject NaN price."""
        with pytest.raises(ValueError, match="finite"):
            polymarket_fee(float("nan"))

    def test_polymarket_fee_rejects_inf(self):
        """polymarket_fee must reject infinite price."""
        with pytest.raises(ValueError, match="finite"):
            polymarket_fee(float("inf"))

    def test_polymarket_fee_rejects_nan_fee_rate(self):
        """polymarket_fee must reject NaN fee_rate."""
        with pytest.raises(ValueError, match="finite"):
            polymarket_fee(0.5, fee_rate=float("nan"))

    def test_polymarket_fee_rejects_inf_fee_rate(self):
        """polymarket_fee must reject infinite fee_rate."""
        with pytest.raises(ValueError, match="finite"):
            polymarket_fee(0.5, fee_rate=float("inf"))


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
        """evaluator.py must wire ExecutionPrice; fee enters via with_taker_fee.

        The direct polymarket_fee import pin rotted when the taker fold
        (Wave-2 item 8) routed all fee math through
        ExecutionPrice.with_taker_fee (which delegates to polymarket_fee
        internally) — the law is the fee APPLICATION, not the import name.
        """
        from pathlib import Path
        src = (Path(__file__).parent.parent / "src" / "engine" / "evaluator.py").read_text()
        assert "ExecutionPrice" in src
        assert "with_taker_fee" in src

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

    def test_shadow_flag_removed_from_evaluator(self):
        """P10E: EXECUTION_PRICE_SHADOW shadow-off path is removed from evaluator.
        _size_at_execution_price_boundary no longer accepts feature_flags param.
        """
        from pathlib import Path
        src = (Path(__file__).parent.parent / "src" / "engine" / "evaluator.py").read_text()
        # The shadow-off branch is gone — no raw_size fallback path
        assert "EXECUTION_PRICE_SHADOW" not in src, (
            "P10E: EXECUTION_PRICE_SHADOW must be removed from evaluator.py; "
            "shadow-off path deleted in P10E."
        )

    def test_settings_do_not_expose_execution_price_shadow_flag(self):
        """DSA-09: stale rollback flag must not remain in operator settings."""
        from src.config import Settings

        flags = Settings()["feature_flags"]
        assert "EXECUTION_PRICE_SHADOW" not in flags

    def test_evaluator_always_uses_fee_adjusted_size(self):
        """P10E: _size_at_execution_price_boundary always returns fee-adjusted size."""
        from src.engine.evaluator import _size_at_execution_price_boundary

        p_posterior = 0.60
        bare_entry = 0.40
        fee = polymarket_fee(bare_entry)
        fee_adjusted_entry = bare_entry + fee

        authoritative_size = _size_at_execution_price_boundary(
            p_posterior=p_posterior,
            entry_price=bare_entry,
            fee_rate=0.05,
            sizing_bankroll=1000.0,
            kelly_multiplier=0.25,
            effective_context=_neutral_context(),
        )
        # Must equal the fee-adjusted size (not the larger bare-float size)
        from src.contracts.execution_price import ExecutionPrice
        ep = ExecutionPrice(
            value=fee_adjusted_entry, price_type="fee_adjusted",
            fee_deducted=True, currency="probability_units",
        )
        from src.strategy.kelly import kelly_size
        expected = kelly_size(p_posterior, ep, 1000.0, 0.25)
        assert authoritative_size == pytest.approx(expected)
        # The fee itself must be correct
        assert fee == pytest.approx(0.012, abs=1e-6)

    def test_shadow_off_path_raises_on_feature_flags_kwarg(self):
        """P10E: _size_at_execution_price_boundary no longer accepts feature_flags."""
        from src.engine.evaluator import _size_at_execution_price_boundary
        import inspect
        sig = inspect.signature(_size_at_execution_price_boundary)
        assert "feature_flags" not in sig.parameters, (
            "P10E: feature_flags param must be removed from "
            "_size_at_execution_price_boundary (shadow-off path deleted)."
        )

    def test_fee_reduces_kelly_size(self):
        """Fee-adjusted entry price must produce smaller Kelly size than raw implied prob.

        This is the D3 bug: Kelly systematically oversizes because it uses
        implied probability (0.42) instead of execution cost (0.42 + fee ≈ 0.43218).

        P10E: both calls use typed ExecutionPrice. Comparison is between
        fee_adjusted (correct) and a hypothetical bare-probability typed price
        that would still pass assert_kelly_safe — we use the evaluator seam to
        show the difference.
        """
        from src.engine.evaluator import _size_at_execution_price_boundary

        p_posterior = 0.55
        bare_entry = 0.42
        fee_adj = ExecutionPrice(
            value=bare_entry, price_type="implied_probability",
            fee_deducted=False, currency="probability_units",
        ).with_taker_fee()

        # fee_adj is the correct ExecutionPrice; lower fee_rate → larger size (fee=0)
        size_no_fee = _size_at_execution_price_boundary(
            p_posterior=p_posterior, entry_price=bare_entry,
            fee_rate=0.0, sizing_bankroll=1000.0, kelly_multiplier=0.25,
            effective_context=_neutral_context(),
        )
        size_with_fee = _size_at_execution_price_boundary(
            p_posterior=p_posterior, entry_price=bare_entry,
            fee_rate=0.05, sizing_bankroll=1000.0, kelly_multiplier=0.25,
            effective_context=_neutral_context(),
        )

        assert size_with_fee < size_no_fee, "Fee-adjusted entry price must produce smaller position size"
        assert size_with_fee > 0, "Fee-adjusted size should still be positive with real edge"


class TestPolymarketFeeRateClient:
    def test_get_fee_rate_reads_token_fee_schedule(self, monkeypatch):
        from src.data import polymarket_client

        # Clear module-level fee-rate cache to prevent cross-test cache bleeding.
        polymarket_client._FEE_RATE_CACHE.pop("token-1", None)

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"feeSchedule": {"feesEnabled": True, "feeRate": "0.072"}}

        calls = []

        def fake_get(url, *, params, timeout):
            calls.append((url, params, timeout))
            return Response()

        monkeypatch.setattr(polymarket_client.httpx, "get", fake_get)
        client = object.__new__(polymarket_client.PolymarketClient)

        assert client.get_fee_rate("token-1") == pytest.approx(0.072)
        assert calls == [
            (
                f"{polymarket_client.CLOB_BASE}/fee-rate",
                {"token_id": "token-1"},
                15.0,
            )
        ]

    def test_get_fee_rate_returns_zero_when_fees_disabled(self, monkeypatch):
        from src.data import polymarket_client

        # Clear module-level fee-rate cache so a prior test's cache entry for
        # "token-1" (feesEnabled=True, feeRate=0.072) doesn't bleed into this
        # test via the 30-minute TTL hit.
        polymarket_client._FEE_RATE_CACHE.pop("token-1", None)

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"feeSchedule": {"feesEnabled": False, "feeRate": "0.05"}}

        monkeypatch.setattr(polymarket_client.httpx, "get", lambda *args, **kwargs: Response())
        client = object.__new__(polymarket_client.PolymarketClient)

        assert client.get_fee_rate("token-1") == 0.0
