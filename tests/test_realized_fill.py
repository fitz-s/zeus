# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: midstream verdict v2 2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T5.d RealizedFill + fresh SlippageBps typed contracts NOT aliased on TemperatureDelta)

"""T5.d SlippageBps + RealizedFill typed-contract antibodies.

Covers:
- SlippageBps __post_init__ invariants (finite + non-negative
  magnitude + direction/value consistency: zero↔0; adverse/favorable↔>0)
- SlippageBps.fraction derives consistently (bps = fraction × 10000)
- SlippageBps.from_prices side-dependent direction semantics (buy
  adverse when actual > expected; sell adverse when actual < expected)
- SlippageBps.from_prices rejects cross-currency and non-positive
  expected
- RealizedFill __post_init__ invariants (currency match, side in
  {buy,sell}, shares finite > 0, trade_id non-empty)
- RealizedFill.from_prices derives slippage via SlippageBps.from_prices
  and threads it into the frozen record
"""

from __future__ import annotations

import math

import pytest

from src.contracts.execution_price import ExecutionPrice
from src.contracts.realized_fill import RealizedFill
from src.contracts.slippage_bps import SlippageBps


def _price(value: float, *, currency: str = "probability_units") -> ExecutionPrice:
    return ExecutionPrice(
        value=value,
        price_type="ask",
        fee_deducted=True,
        currency=currency,
    )


class TestSlippageBpsInvariants:
    """__post_init__ rejects every illegal combination."""

    def test_canonical_zero_slippage(self):
        s = SlippageBps(value_bps=0.0, direction="zero")
        assert s.value_bps == 0.0
        assert s.direction == "zero"

    def test_canonical_adverse_positive(self):
        s = SlippageBps(value_bps=25.0, direction="adverse")
        assert s.value_bps == 25.0
        assert s.direction == "adverse"

    def test_canonical_favorable_positive(self):
        s = SlippageBps(value_bps=5.0, direction="favorable")
        assert s.direction == "favorable"

    def test_nan_value_raises(self):
        with pytest.raises(ValueError, match="finite"):
            SlippageBps(value_bps=float("nan"), direction="adverse")

    def test_positive_infinity_raises(self):
        with pytest.raises(ValueError, match="finite"):
            SlippageBps(value_bps=float("inf"), direction="adverse")

    def test_negative_magnitude_raises(self):
        """Direction carries sign; value_bps must be non-negative."""
        with pytest.raises(ValueError, match="non-negative magnitude"):
            SlippageBps(value_bps=-10.0, direction="adverse")

    def test_zero_direction_with_nonzero_value_raises(self):
        with pytest.raises(ValueError, match="requires value_bps=0"):
            SlippageBps(value_bps=5.0, direction="zero")

    def test_adverse_direction_with_zero_value_raises(self):
        with pytest.raises(ValueError, match="incompatible with value_bps=0"):
            SlippageBps(value_bps=0.0, direction="adverse")

    def test_favorable_direction_with_zero_value_raises(self):
        with pytest.raises(ValueError, match="incompatible with value_bps=0"):
            SlippageBps(value_bps=0.0, direction="favorable")


class TestSlippageBpsFraction:
    """Derived property fraction = value_bps / 10000 (always non-negative)."""

    def test_zero_fraction(self):
        assert SlippageBps(value_bps=0.0, direction="zero").fraction == 0.0

    def test_one_bp_is_0001_fraction(self):
        assert SlippageBps(value_bps=1.0, direction="adverse").fraction == pytest.approx(0.0001)

    def test_100_bps_is_one_percent(self):
        assert SlippageBps(value_bps=100.0, direction="adverse").fraction == pytest.approx(0.01)


class TestSlippageBpsFromPrices:
    """Side-dependent direction semantics."""

    def test_buy_actual_higher_is_adverse(self):
        """BUY paying more than expected = adverse."""
        s = SlippageBps.from_prices(
            actual=_price(0.52),
            expected=_price(0.50),
            side="buy",
        )
        assert s.direction == "adverse"
        # 0.02 / 0.50 = 4% = 400 bps
        assert s.value_bps == pytest.approx(400.0)

    def test_buy_actual_lower_is_favorable(self):
        s = SlippageBps.from_prices(
            actual=_price(0.48),
            expected=_price(0.50),
            side="buy",
        )
        assert s.direction == "favorable"
        assert s.value_bps == pytest.approx(400.0)

    def test_sell_actual_higher_is_favorable(self):
        """SELL receiving more than expected = favorable."""
        s = SlippageBps.from_prices(
            actual=_price(0.52),
            expected=_price(0.50),
            side="sell",
        )
        assert s.direction == "favorable"
        assert s.value_bps == pytest.approx(400.0)

    def test_sell_actual_lower_is_adverse(self):
        s = SlippageBps.from_prices(
            actual=_price(0.48),
            expected=_price(0.50),
            side="sell",
        )
        assert s.direction == "adverse"
        assert s.value_bps == pytest.approx(400.0)

    def test_exact_fill_is_zero(self):
        s = SlippageBps.from_prices(
            actual=_price(0.50),
            expected=_price(0.50),
            side="buy",
        )
        assert s.direction == "zero"
        assert s.value_bps == 0.0

    def test_currency_mismatch_rejects(self):
        with pytest.raises(ValueError, match="currency mismatch"):
            SlippageBps.from_prices(
                actual=_price(0.5, currency="probability_units"),
                expected=ExecutionPrice(
                    value=0.5,
                    price_type="ask",
                    fee_deducted=True,
                    currency="usd",
                ),
                side="buy",
            )

    def test_invalid_side_rejects(self):
        with pytest.raises(ValueError, match="side must be"):
            SlippageBps.from_prices(
                actual=_price(0.5),
                expected=_price(0.5),
                side="hodl",  # type: ignore[arg-type]
            )

    def test_zero_expected_price_rejects(self):
        """Relative slippage requires expected > 0 (divide-by-zero guard)."""
        with pytest.raises(ValueError, match="expected_price.value must be > 0"):
            SlippageBps.from_prices(
                actual=_price(0.5),
                expected=_price(0.0),
                side="buy",
            )


class TestRealizedFillInvariants:
    """__post_init__ gates every construction."""

    def test_canonical_buy_fill(self):
        rf = RealizedFill.from_prices(
            execution_price=_price(0.52),
            expected_price=_price(0.50),
            side="buy",
            shares=100.0,
            trade_id="trade-123",
        )
        assert rf.side == "buy"
        assert rf.shares == 100.0
        assert rf.trade_id == "trade-123"
        assert rf.slippage.direction == "adverse"
        assert rf.slippage.value_bps == pytest.approx(400.0)

    def test_canonical_sell_fill(self):
        rf = RealizedFill.from_prices(
            execution_price=_price(0.48),
            expected_price=_price(0.50),
            side="sell",
            shares=50.0,
            trade_id="trade-sell",
        )
        assert rf.slippage.direction == "adverse"  # sell-side adverse = got less

    def test_currency_mismatch_raises(self):
        with pytest.raises(ValueError, match="currency mismatch"):
            RealizedFill(
                execution_price=_price(0.5, currency="probability_units"),
                expected_price=ExecutionPrice(
                    value=0.5, price_type="ask", fee_deducted=True, currency="usd"
                ),
                slippage=SlippageBps(value_bps=0.0, direction="zero"),
                side="buy",
                shares=10.0,
                trade_id="t-mismatch",
            )

    def test_invalid_side_raises(self):
        with pytest.raises(ValueError, match="side must be"):
            RealizedFill(
                execution_price=_price(0.5),
                expected_price=_price(0.5),
                slippage=SlippageBps(value_bps=0.0, direction="zero"),
                side="neither",  # type: ignore[arg-type]
                shares=10.0,
                trade_id="t",
            )

    def test_nan_shares_raises(self):
        with pytest.raises(ValueError, match="shares must be finite"):
            RealizedFill(
                execution_price=_price(0.5),
                expected_price=_price(0.5),
                slippage=SlippageBps(value_bps=0.0, direction="zero"),
                side="buy",
                shares=float("nan"),
                trade_id="t",
            )

    def test_zero_shares_raises(self):
        with pytest.raises(ValueError, match="shares must be > 0"):
            RealizedFill(
                execution_price=_price(0.5),
                expected_price=_price(0.5),
                slippage=SlippageBps(value_bps=0.0, direction="zero"),
                side="buy",
                shares=0.0,
                trade_id="t",
            )

    def test_negative_shares_raises(self):
        with pytest.raises(ValueError, match="shares must be > 0"):
            RealizedFill(
                execution_price=_price(0.5),
                expected_price=_price(0.5),
                slippage=SlippageBps(value_bps=0.0, direction="zero"),
                side="buy",
                shares=-5.0,
                trade_id="t",
            )

    def test_empty_trade_id_raises(self):
        with pytest.raises(ValueError, match="trade_id"):
            RealizedFill(
                execution_price=_price(0.5),
                expected_price=_price(0.5),
                slippage=SlippageBps(value_bps=0.0, direction="zero"),
                side="buy",
                shares=10.0,
                trade_id="",
            )


class TestRealizedFillFactoryConsistency:
    """from_prices derives slippage consistent with execution/expected/side."""

    def test_factory_produces_matching_slippage_buy_adverse(self):
        rf = RealizedFill.from_prices(
            execution_price=_price(0.55),
            expected_price=_price(0.50),
            side="buy",
            shares=10.0,
            trade_id="t",
        )
        assert rf.slippage.direction == "adverse"
        assert rf.slippage.value_bps == pytest.approx(1000.0)

    def test_factory_produces_matching_slippage_sell_favorable(self):
        rf = RealizedFill.from_prices(
            execution_price=_price(0.55),
            expected_price=_price(0.50),
            side="sell",
            shares=10.0,
            trade_id="t",
        )
        assert rf.slippage.direction == "favorable"
        assert rf.slippage.value_bps == pytest.approx(1000.0)

    def test_factory_zero_slippage_exact_fill(self):
        rf = RealizedFill.from_prices(
            execution_price=_price(0.50),
            expected_price=_price(0.50),
            side="buy",
            shares=10.0,
            trade_id="t",
        )
        assert rf.slippage.direction == "zero"
        assert rf.slippage.value_bps == 0.0

    def test_factory_inherits_currency_mismatch_rejection(self):
        with pytest.raises(ValueError, match="currency"):
            RealizedFill.from_prices(
                execution_price=_price(0.5, currency="probability_units"),
                expected_price=ExecutionPrice(
                    value=0.5, price_type="ask", fee_deducted=True, currency="usd"
                ),
                side="buy",
                shares=10.0,
                trade_id="t",
            )
