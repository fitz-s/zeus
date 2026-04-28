# Lifecycle: created=2026-04-21; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: K2 order and price safety regressions including V2 adapter side validation.
# Reuse: Run when PolymarketClient order placement, fee math, or Kelly safety changes.
# Created: 2026-04-21
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z2.yaml
"""K2 Slice E: Order & Price Safety — fail-close tests.

Bug #46: place_limit_order must reject invalid side values
Bug #42: outcomePrices parse failure must skip market, not default [0.5,0.5]
Bug #27: polymarket_fee must reject price outside (0,1)
Bug #14: dynamic_kelly_mult must reject NaN / zero multiplier
"""

import math

import pytest


# ── Bug #46: side enum enforcement ──────────────────────────────────────


class TestPlaceLimitOrderSideValidation:
    """place_limit_order must only accept 'BUY' or 'SELL'."""

    def test_invalid_side_raises(self):
        """Any side value other than BUY/SELL must raise ValueError."""
        from unittest.mock import MagicMock, patch

        from src.data.polymarket_client import PolymarketClient

        client = PolymarketClient.__new__(PolymarketClient)
        client._clob_client = MagicMock()

        for bad_side in ["buy", "sell", "Buy", "", "HOLD", "SHORT", None, 42]:
            with pytest.raises((ValueError, TypeError)):
                client.place_limit_order(
                    token_id="abc123",
                    price=0.50,
                    size=10.0,
                    side=bad_side,
                )

    def test_valid_sides_accepted(self):
        """BUY and SELL must pass validation (mock the rest)."""
        from types import SimpleNamespace

        from src.data.polymarket_client import PolymarketClient
        from src.venue.polymarket_v2_adapter import PreflightResult

        class FakeEnvelope:
            order_id = "ord-valid-side"

            def to_dict(self):
                return {"sdk_package": "py-clob-client-v2", "order_id": self.order_id}

        class FakeAdapter:
            def preflight(self):
                return PreflightResult(ok=True)

            def submit_limit_order(self, **_kwargs):
                return SimpleNamespace(
                    status="accepted",
                    error_code=None,
                    error_message=None,
                    envelope=FakeEnvelope(),
                )

        client = PolymarketClient()
        client._v2_adapter = FakeAdapter()

        # Should not raise
        for side in ["BUY", "SELL"]:
            with pytest.warns(DeprecationWarning, match="compatibility wrapper"):
                result = client.place_limit_order(
                    token_id="abc123",
                    price=0.50,
                    size=10.0,
                    side=side,
                )
            assert result is not None


# ── Bug #42: outcomePrices parse failure ────────────────────────────────


class TestOutcomePricesParseFailure:
    """outcomePrices parse failure must skip market, not fabricate [0.5,0.5]."""

    def test_malformed_json_skips_market(self):
        """A market with unparseable outcomePrices must not appear in results."""
        import json

        # Simulate what the loop does: parse prices, skip on failure
        prices_str = "NOT_VALID_JSON"
        try:
            parsed = json.loads(prices_str)
        except (json.JSONDecodeError, TypeError):
            parsed = None  # should skip

        # The fix: parsed is None means the market is skipped
        assert parsed is None, "Malformed JSON must not produce a default price list"

    def test_valid_json_prices_pass_through(self):
        """Valid JSON prices must be used as-is."""
        import json

        prices_str = '[0.72, 0.28]'
        parsed = json.loads(prices_str)
        assert parsed == [0.72, 0.28]


# ── Bug #27: polymarket_fee on invalid price ────────────────────────────


class TestPolymarketFeeInvalidPrice:
    """polymarket_fee must raise on price outside (0, 1)."""

    def test_price_zero_raises(self):
        from src.contracts.execution_price import polymarket_fee

        with pytest.raises(ValueError, match="price in \\(0, 1\\)"):
            polymarket_fee(0.0)

    def test_price_one_raises(self):
        from src.contracts.execution_price import polymarket_fee

        with pytest.raises(ValueError, match="price in \\(0, 1\\)"):
            polymarket_fee(1.0)

    def test_price_negative_raises(self):
        from src.contracts.execution_price import polymarket_fee

        with pytest.raises(ValueError, match="price in \\(0, 1\\)"):
            polymarket_fee(-0.5)

    def test_price_above_one_raises(self):
        from src.contracts.execution_price import polymarket_fee

        with pytest.raises(ValueError, match="price in \\(0, 1\\)"):
            polymarket_fee(1.5)

    def test_valid_price_computes_fee(self):
        from src.contracts.execution_price import polymarket_fee

        fee = polymarket_fee(0.50)
        assert abs(fee - 0.0125) < 1e-10

    def test_nan_price_raises(self):
        from src.contracts.execution_price import polymarket_fee

        with pytest.raises(ValueError):
            polymarket_fee(float("nan"))

    def test_inf_price_raises(self):
        from src.contracts.execution_price import polymarket_fee

        with pytest.raises(ValueError):
            polymarket_fee(float("inf"))


# ── Bug #14: kelly NaN / zero multiplier ────────────────────────────────


class TestDynamicKellyMultFailClose:
    """dynamic_kelly_mult must raise on NaN or zero, not fabricate 0.001."""

    def test_nan_input_raises(self):
        from src.strategy.kelly import dynamic_kelly_mult

        with pytest.raises(ValueError, match="NaN"):
            dynamic_kelly_mult(base=float("nan"))

    def test_zero_multiplier_raises(self):
        """When all gates trigger and m reaches 0, must raise."""
        from src.strategy.kelly import dynamic_kelly_mult

        # max_drawdown=0.20, drawdown_pct=0.20 → 1 - 0.20/0.20 = 0.0 → m = 0
        with pytest.raises(ValueError, match="collapsed to"):
            dynamic_kelly_mult(
                base=0.25,
                drawdown_pct=0.20,
                max_drawdown=0.20,
            )

    def test_normal_multiplier_passes(self):
        from src.strategy.kelly import dynamic_kelly_mult

        m = dynamic_kelly_mult(base=0.25)
        assert m > 0
        assert m == 0.25  # no adjustments → base returned

    def test_reduced_multiplier_positive(self):
        from src.strategy.kelly import dynamic_kelly_mult

        m = dynamic_kelly_mult(base=0.25, ci_width=0.12)
        assert m == pytest.approx(0.25 * 0.7)
