# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: midstream verdict v2 2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T5.b TickSize typed contract + exit-path NaN closure for T5.a-LOW follow-up)

"""T5.b TickSize typed contract antibodies.

Coverage:
- Contract __post_init__ invariants: NaN / -inf / 0 / negative / > 0.5
  all raise ValueError at construction.
- Derived properties (min_valid_price / max_valid_price) match the
  tick unit.
- clamp_to_valid_range behavior: in-range pass-through, below-floor
  lifts to min, above-ceiling drops to max, NaN propagates (lenient
  per docstring; downstream typed contracts reject).
- Factory classmethod returns Polymarket canonical $0.01 tick.
- Module constant POLYMARKET_WEATHER_TICK is a valid TickSize instance
  with value=0.01 and currency='probability_units'.
- Exit-path integration: execute_exit_order rejects non-finite
  limit_price BEFORE tick clamp (closes surrogate T5.a-LOW finding
  about NaN propagation through max/min).
"""

from __future__ import annotations

import math

import pytest

from src.contracts.tick_size import (
    POLYMARKET_WEATHER_TICK,
    TickSize,
)


class TestTickSizeInvariants:
    """__post_init__ rejects every non-contract value."""

    def test_canonical_polymarket_tick_constructs(self):
        ts = TickSize(value=0.01, currency="probability_units")
        assert ts.value == 0.01
        assert ts.currency == "probability_units"

    def test_nan_value_raises(self):
        with pytest.raises(ValueError, match="finite"):
            TickSize(value=float("nan"), currency="probability_units")

    def test_positive_infinity_raises(self):
        with pytest.raises(ValueError, match="finite"):
            TickSize(value=float("inf"), currency="probability_units")

    def test_negative_infinity_raises(self):
        with pytest.raises(ValueError, match="finite"):
            TickSize(value=float("-inf"), currency="probability_units")

    def test_zero_raises(self):
        with pytest.raises(ValueError, match="> 0"):
            TickSize(value=0.0, currency="probability_units")

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="> 0"):
            TickSize(value=-0.01, currency="probability_units")

    def test_value_exceeding_half_raises_degenerate(self):
        with pytest.raises(ValueError, match="degenerate"):
            TickSize(value=0.51, currency="probability_units")

    def test_value_at_half_boundary_is_valid(self):
        """0.5 is the degeneracy edge — value=0.5 gives
        min_valid_price=0.5 == max_valid_price=0.5, a single-price
        market. Accepted at the boundary; rejected strictly above."""
        ts = TickSize(value=0.5, currency="probability_units")
        assert ts.min_valid_price == 0.5
        assert ts.max_valid_price == 0.5


class TestTickSizeDerivedProperties:
    """min_valid_price / max_valid_price derive from value consistently."""

    def test_polymarket_tick_derives_01_99(self):
        ts = TickSize(value=0.01, currency="probability_units")
        assert ts.min_valid_price == 0.01
        assert ts.max_valid_price == pytest.approx(0.99)

    def test_finer_tick_derives_narrower_floor_wider_ceiling(self):
        ts = TickSize(value=0.001, currency="probability_units")
        assert ts.min_valid_price == 0.001
        assert ts.max_valid_price == pytest.approx(0.999)


class TestClampToValidRange:
    """clamp_to_valid_range preserves in-range values + clamps extremes."""

    def test_in_range_passes_through(self):
        ts = TickSize(value=0.01, currency="probability_units")
        assert ts.clamp_to_valid_range(0.5) == 0.5

    def test_below_floor_lifts_to_min_valid(self):
        ts = TickSize(value=0.01, currency="probability_units")
        assert ts.clamp_to_valid_range(0.005) == 0.01
        assert ts.clamp_to_valid_range(-1.0) == 0.01
        assert ts.clamp_to_valid_range(0.0) == 0.01

    def test_above_ceiling_drops_to_max_valid(self):
        ts = TickSize(value=0.01, currency="probability_units")
        assert ts.clamp_to_valid_range(0.995) == pytest.approx(0.99)
        assert ts.clamp_to_valid_range(1.5) == pytest.approx(0.99)
        assert ts.clamp_to_valid_range(1.0) == pytest.approx(0.99)

    def test_exact_floor_stays_at_floor(self):
        ts = TickSize(value=0.01, currency="probability_units")
        assert ts.clamp_to_valid_range(0.01) == 0.01

    def test_exact_ceiling_stays_at_ceiling(self):
        ts = TickSize(value=0.01, currency="probability_units")
        assert ts.clamp_to_valid_range(0.99) == pytest.approx(0.99)

    def test_nan_input_produces_fake_valid_price_callers_must_guard(self):
        """Python ``max(0.01, min(0.99, NaN))`` returns 0.99 — NaN
        comparisons are always False so ``min(0.99, NaN) == 0.99`` and
        ``max(0.01, 0.99) == 0.99``. The clamp therefore silently
        produces a SPURIOUS valid-looking price from NaN input rather
        than propagating the error. This is the exact failure mode
        surrogate flagged in the T5.a LOW finding about
        ``execute_exit_order:269`` — and the reason T5.b's executor
        exit path guards ``math.isfinite(limit_price)`` BEFORE calling
        ``clamp_to_valid_range``.

        Callers that tolerate silent-coercion behavior (e.g., the
        semantic-types compute helper whose downstream T5.a
        ExecutionPrice boundary rejects NaN) can keep this lenient
        path; callers that cannot must guard upstream."""
        ts = TickSize(value=0.01, currency="probability_units")
        result = ts.clamp_to_valid_range(float("nan"))
        assert result == pytest.approx(0.99)


class TestFactoryAndModuleConstant:
    """for_market + POLYMARKET_WEATHER_TICK expose canonical authority."""

    def test_factory_no_args_returns_polymarket_canonical(self):
        ts = TickSize.for_market()
        assert ts.value == 0.01
        assert ts.currency == "probability_units"

    def test_factory_accepts_market_id(self):
        ts = TickSize.for_market(market_id="condition-abc")
        assert ts.value == 0.01

    def test_factory_accepts_token_id(self):
        ts = TickSize.for_market(token_id="token-xyz")
        assert ts.value == 0.01

    def test_module_constant_is_canonical_instance(self):
        assert POLYMARKET_WEATHER_TICK.value == 0.01
        assert POLYMARKET_WEATHER_TICK.currency == "probability_units"
        # Equality between factory result and module constant —
        # frozen dataclass instances with identical fields are equal.
        assert POLYMARKET_WEATHER_TICK == TickSize.for_market()


class TestExitPathNaNGuard:
    """execute_exit_order rejects non-finite limit_price before the
    tick clamp — closes surrogate T5.a-LOW (exit-side NaN propagation
    through max/min clamp) inside T5.b scope."""

    def test_exit_rejects_nan_current_price(self):
        from src.execution.executor import ExitOrderIntent, execute_exit_order

        intent = ExitOrderIntent(
            trade_id="t-nan",
            token_id="tok-x",
            shares=5.0,
            current_price=float("nan"),
            best_bid=None,
        )
        result = execute_exit_order(intent)
        assert result.status == "rejected"
        assert "malformed_limit_price" in result.reason

    def test_exit_rejects_positive_infinity(self):
        from src.execution.executor import ExitOrderIntent, execute_exit_order

        intent = ExitOrderIntent(
            trade_id="t-pinf",
            token_id="tok-x",
            shares=5.0,
            current_price=float("inf"),
            best_bid=None,
        )
        result = execute_exit_order(intent)
        assert result.status == "rejected"
        assert "malformed_limit_price" in result.reason

    def test_exit_rejects_negative_infinity(self):
        from src.execution.executor import ExitOrderIntent, execute_exit_order

        intent = ExitOrderIntent(
            trade_id="t-ninf",
            token_id="tok-x",
            shares=5.0,
            current_price=float("-inf"),
            best_bid=None,
        )
        result = execute_exit_order(intent)
        assert result.status == "rejected"
        assert "malformed_limit_price" in result.reason
