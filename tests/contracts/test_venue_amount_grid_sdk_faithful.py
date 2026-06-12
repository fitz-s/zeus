# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: venue invalid_amount rejection loop 2026-06-10
#   (live venue_command_events 39517e446ba94b60/5cec15b1de484fbb:
#    "the market buy orders maker amount supports a max accuracy of 2 decimals,
#     taker amount a max of 4 decimals"). py_clob_client_v2 builds maker/taker
#    with FLOAT round_down(shares,2)*round(price); the pre-submit precision
#    contract previously modelled maker as exact Decimal(shares)*Decimal(price)
#    and waved through shares (e.g. 8.7) that the SDK truncates to 8.69 -> a
#    3-decimal maker the venue rejects. These RELATIONSHIP tests pin the
#    cross-module invariant: the contract's notion of a venue-valid maker MUST
#    equal the maker the SDK actually sends.
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

# SDK-actual maker/taker builder (limit BUY path get_order_amounts), replicated
# from py_clob_client_v2.order_builder.builder.get_order_amounts for tick->config.
# Imported from the installed SDK so the test tracks the real venue-facing math.
from py_clob_client_v2.order_builder.helpers import (
    round_down as _sdk_round_down,
    round_normal as _sdk_round_normal,
    round_up as _sdk_round_up,
    decimal_places as _sdk_decimal_places,
)

# tick_size -> (price_dec, size_dec, amount_dec) from the SDK ROUNDING_CONFIG.
_SDK_ROUND_CONFIG = {
    "0.1": (1, 2, 3),
    "0.01": (2, 2, 4),
    "0.001": (3, 2, 5),
    "0.0001": (4, 2, 6),
}


def _sdk_limit_buy_maker_taker(shares: float, price: float, tick: str) -> tuple[float, float]:
    """Replicate py_clob_client_v2 get_order_amounts(BUY) -> (raw_maker, raw_taker)."""
    price_dec, size_dec, amount_dec = _SDK_ROUND_CONFIG[tick]
    raw_price = _sdk_round_normal(price, price_dec)
    raw_taker = _sdk_round_down(shares, size_dec)
    raw_maker = raw_taker * raw_price
    if _sdk_decimal_places(raw_maker) > amount_dec:
        raw_maker = _sdk_round_up(raw_maker, amount_dec + 4)
        if _sdk_decimal_places(raw_maker) > amount_dec:
            raw_maker = _sdk_round_down(raw_maker, amount_dec)
    return raw_maker, raw_taker


def _sdk_maker_taker_venue_valid(shares: float, price: float, tick: str) -> bool:
    """True iff the SDK-built maker has <=2 decimals and taker <=4 decimals."""
    raw_maker, raw_taker = _sdk_limit_buy_maker_taker(shares, price, tick)
    return _sdk_decimal_places(raw_maker) <= 2 and _sdk_decimal_places(raw_taker) <= 4


def test_regression_8p7_at_0p70_is_rejected_by_contract():
    """The exact live loop: 8.7 shares @ 0.70 must NOT be called venue-valid.

    SDK builds maker = round_down(8.7,2)*0.70 = 8.69*0.70 = 6.083 (3 decimals)
    -> venue_rejected_invalid_amount_400. The contract must reject it pre-submit.
    """
    # Sanity: the SDK genuinely truncates 8.7 -> 8.69 and yields a 3-dec maker.
    assert _sdk_round_down(8.7, 2) == 8.69
    assert not _sdk_maker_taker_venue_valid(8.7, 0.70, "0.01")

    err = venue_submit_amount_precision_error(
        direction="buy_no",
        final_limit_price=Decimal("0.70"),
        submitted_shares=Decimal("8.7"),
        order_type="FOK",
    )
    assert err is not None, "contract must reject SDK-truncated 8.7@0.70 maker"


def test_quantize_8p7_at_0p70_steps_down_to_sdk_valid_amount():
    """The grid quantizer rounds DOWN to a share count the SDK builds validly."""
    quantized = quantize_submit_shares_for_venue_at_most(
        "buy_no",
        Decimal("8.7"),
        final_limit_price=Decimal("0.70"),
        order_type="FOK",
    )
    assert quantized <= Decimal("8.7")
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
    assert _sdk_maker_taker_venue_valid(float(quantized), 0.70, "0.01")


def test_rejection_no_verbatim_retry_is_structurally_impossible():
    """K2: a same-class invalid_amount candidate cannot be re-derived verbatim.

    The live loop re-derived the SAME 8.7@0.70 intent every redecision cycle.
    With the SDK-faithful grid, deriving the venue size from the SAME
    (collateral, price) is deterministic AND always venue-legal, so the exact
    rejected payload (8.7@0.70 -> SDK maker 6.083) can never be reconstructed.

    Invariant: for the live (stake=6.09, price=0.70) inputs, the quantized
    share amount is venue-valid, is NOT the rejected 8.7, and is stable across
    repeated derivations (no oscillation that could resurrect the bad amount).
    """
    stake = Decimal("6.09")
    price = Decimal("0.70")
    raw_shares = stake / price  # 8.7 exactly -> the rejected amount

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
    # The rejected amount is structurally unreachable.
    assert quantized != Decimal("8.7")
    assert quantized <= raw_shares
    # And what IS derived is venue-legal in the real SDK builder.
    assert _sdk_maker_taker_venue_valid(float(quantized), 0.70, "0.01")
    assert (
        venue_submit_amount_precision_error(
            direction="buy_no",
            final_limit_price=price,
            submitted_shares=Decimal("8.7"),
            order_type="FOK",
            tick_size="0.01",
        )
        is not None
    )


@pytest.mark.parametrize("tick", ["0.1", "0.01", "0.001", "0.0001"])
def test_contract_validity_implies_sdk_validity_property(tick: str):
    """RELATIONSHIP: contract-valid shares => SDK-built amounts are venue-valid.

    Random (shares, price) on each tick grid. Any (shares, price) the contract
    calls valid must also be valid in the SDK's float builder. This is the
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
        assert _sdk_maker_taker_venue_valid(shares, price, tick), (
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
        assert _sdk_maker_taker_venue_valid(float(quantized), price, tick)
    assert produced > 0
