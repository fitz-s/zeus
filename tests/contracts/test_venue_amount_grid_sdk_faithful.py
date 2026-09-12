# Created: 2026-06-10
# Last reused or audited: 2026-09-12
# Authority basis: venue invalid_amount rejection loop 2026-06-10 and the
# installed SDK's Decimal OrderArgs path. These RELATIONSHIP tests pin the
# cross-module invariant: the contract's notion of a venue-valid maker MUST
# equal the maker the SDK actually sends.
"""Relationship tests: pre-submit amount grid == SDK-actual venue payload.

Cross-module invariant under test:

  For every (shares, price, tick) the pre-submit precision contract calls
  "venue-valid", the maker/taker amounts the py_clob_client_v2 order builder
  actually constructs are ALSO venue-valid (maker <= 2 decimals,
  taker <= 4 decimals). Contract-clean is necessary but NOT sufficient; the
  load-bearing property is contract == SDK-actual.
"""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from src.contracts.execution_intent import (
    quantize_submit_shares_for_venue_at_most,
    venue_submit_amount_precision_error,
)

# SDK-actual maker/taker builder (limit BUY path get_order_amounts), using the
# installed SDK directly so the test tracks its Decimal OrderArgs behavior.
from py_clob_client_v2.clob_types import RoundConfig
from py_clob_client_v2.order_builder.builder import OrderBuilder

# tick_size -> (price_dec, size_dec, amount_dec) from the SDK ROUNDING_CONFIG.
_SDK_ROUND_CONFIG = {
    "0.1": (1, 2, 3),
    "0.01": (2, 2, 4),
    "0.001": (3, 2, 5),
    "0.0001": (4, 2, 6),
}


def _sdk_limit_buy_maker_taker(
    shares: Decimal, price: Decimal, tick: str
) -> tuple[int, int]:
    """Return actual SDK token amounts for Decimal BUY OrderArgs."""
    price_dec, size_dec, amount_dec = _SDK_ROUND_CONFIG[tick]
    _side, maker_amount, taker_amount = OrderBuilder(None).get_order_amounts(
        "BUY",
        shares,
        price,
        RoundConfig(price=price_dec, size=size_dec, amount=amount_dec),
    )
    return maker_amount, taker_amount


def _sdk_maker_taker_venue_valid(
    shares: Decimal, price: Decimal, tick: str
) -> bool:
    """True iff actual SDK token amounts fit maker (2dp) and taker (4dp)."""
    maker_amount, taker_amount = _sdk_limit_buy_maker_taker(shares, price, tick)
    return maker_amount % 10_000 == 0 and taker_amount % 100 == 0


def test_decimal_inputs_preserve_8p7_at_0p70_sdk_amounts():
    """Decimal OrderArgs keep 8.7 shares and produce the legal 6.09 maker."""
    assert _sdk_limit_buy_maker_taker(
        Decimal("8.7"), Decimal("0.70"), "0.01"
    ) == (6_090_000, 8_700_000)
    assert _sdk_maker_taker_venue_valid(
        Decimal("8.7"), Decimal("0.70"), "0.01"
    )
    assert (
        venue_submit_amount_precision_error(
            direction="buy_no",
            final_limit_price=Decimal("0.70"),
            submitted_shares=Decimal("8.7"),
            order_type="FOK",
        )
        is None
    )


def test_quantize_8p7_at_0p70_steps_down_to_sdk_valid_amount():
    """The grid quantizer rounds DOWN to a share count the SDK builds validly."""
    quantized = quantize_submit_shares_for_venue_at_most(
        "buy_no",
        Decimal("8.7"),
        final_limit_price=Decimal("0.70"),
        order_type="FOK",
    )
    assert quantized == Decimal("8.7")
    assert quantized > Decimal("0")
    # Contract agrees it is valid...
    assert (
        venue_submit_amount_precision_error(
            direction="buy_no",
            final_limit_price=Decimal("0.70"),
            submitted_shares=quantized,
            order_type="FOK",
        )
        is None
    )
    # ...AND the SDK actually builds a venue-valid maker/taker for it.
    assert _sdk_maker_taker_venue_valid(
        quantized, Decimal("0.70"), "0.01"
    )


def test_decimal_2p01_amount_is_rejected_and_quantized_at_most():
    """Decimal 2.01 survives SDK size rounding and exposes the maker 3dp."""
    price = Decimal("0.58")
    raw_shares = Decimal("2.01")

    assert _sdk_limit_buy_maker_taker(raw_shares, price, "0.01") == (
        1_165_800,
        2_010_000,
    )
    assert not _sdk_maker_taker_venue_valid(raw_shares, price, "0.01")
    assert (
        venue_submit_amount_precision_error(
            direction="buy_yes",
            final_limit_price=price,
            submitted_shares=raw_shares,
            order_type="FOK",
            tick_size="0.01",
        )
        is not None
    )

    assert _sdk_limit_buy_maker_taker(
        raw_shares, Decimal("0.66"), "0.01"
    ) == (1_326_600, 2_010_000)

    derived = [
        quantize_submit_shares_for_venue_at_most(
            "buy_no",
            raw_shares,
            final_limit_price=price,
            order_type="FOK",
            tick_size="0.01",
        )
        for _ in range(5)
    ]
    # Deterministic across cycles (no verbatim-vs-corrected oscillation).
    assert len(set(derived)) == 1
    quantized = derived[0]
    # The rejected amount is structurally unreachable after the at-most repair.
    assert quantized == Decimal("2.00")
    assert quantized <= raw_shares
    # And what IS derived is venue-legal in the real SDK builder.
    assert _sdk_maker_taker_venue_valid(quantized, price, "0.01")
    assert (
        venue_submit_amount_precision_error(
            direction="buy_no",
            final_limit_price=price,
            submitted_shares=raw_shares,
            order_type="FOK",
            tick_size="0.01",
        )
        is not None
    )


@pytest.mark.parametrize("tick", ["0.1", "0.01", "0.001", "0.0001"])
def test_contract_validity_implies_sdk_validity_property(tick: str):
    """RELATIONSHIP: contract-valid shares => SDK-built amounts are venue-valid.

    Random (shares, price) on each tick grid. Any (shares, price) the contract
    calls valid must also be valid in the SDK's Decimal-input builder. This is the
    cross-module invariant whose violation produced the live rejection loop.
    """
    rng = random.Random(20260610)
    tick_dec = Decimal(tick)
    checked = 0
    for _ in range(1500):
        # price strictly inside (tick, 1-tick), on the tick grid
        lo = float(tick_dec)
        hi = 1.0 - lo
        price = round(rng.uniform(lo, hi) / lo) * lo
        price = round(price, 4)
        if not (lo < price < 1.0 - lo + 1e-12):
            continue
        # candidate shares: cents grid (the venue/Zeus share grid for FOK BUY)
        shares = round(rng.uniform(0.01, 500.0), 2)
        err = venue_submit_amount_precision_error(
            direction="buy_no",
            final_limit_price=Decimal(str(price)),
            submitted_shares=Decimal(str(shares)),
            order_type="FOK",
            tick_size=tick,
        )
        if err is not None:
            continue  # contract already rejects -> nothing to prove
        checked += 1
        assert _sdk_maker_taker_venue_valid(
            Decimal(str(shares)), Decimal(str(price)), tick
        ), (
            f"contract called shares={shares} price={price} tick={tick} valid "
            f"but SDK builds an invalid maker/taker"
        )
    assert checked > 0, "property test exercised no contract-valid candidates"


@pytest.mark.parametrize("tick", ["0.1", "0.01", "0.001", "0.0001"])
def test_quantizer_output_is_always_sdk_valid_property(tick: str):
    """RELATIONSHIP: quantize_..._at_most output is always SDK-venue-valid.

    For random stakes/prices, the rounded-DOWN quantizer never exceeds the
    requested shares AND the SDK builds a venue-valid payload for its output.
    """
    rng = random.Random(7)
    tick_dec = Decimal(tick)
    produced = 0
    for _ in range(800):
        lo = float(tick_dec)
        price = round(rng.uniform(lo, 1.0 - lo) / lo) * lo
        price = round(price, 4)
        if not (lo < price < 1.0):
            continue
        raw_shares = Decimal(str(round(rng.uniform(0.5, 500.0), 4)))
        try:
            quantized = quantize_submit_shares_for_venue_at_most(
                "buy_no",
                raw_shares,
                final_limit_price=Decimal(str(price)),
                order_type="FOK",
                tick_size=tick,
            )
        except ValueError:
            continue
        produced += 1
        assert quantized <= raw_shares  # never widen / never overspend
        assert quantized > Decimal("0")
        assert _sdk_maker_taker_venue_valid(
            quantized, Decimal(str(price)), tick
        )
    assert produced > 0
