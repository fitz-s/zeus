# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL PR G §6
"""Fill simulator antibody tests.

Coverage
--------
1.  BUY uses ask book, not midpoint — midpoint-priced expectation FAILS.
2.  SELL uses bid book, not midpoint.
3.  Midpoint-priced expectation FAILS (explicit proof we don't use midpoint).
4.  FOK no-full-fill → CANCELLED, filled_size=0.
5.  FAK partial fill → PARTIAL + cancelled_remainder == unfilled portion.
6.  min_order_size enforcement → REJECTED.
7.  tick_size enforcement → REJECTED on mis-ticked limit_price.
8.  Fees change avg cost (fees > 0 when fee_rate > 0 and filled).
9.  orderbook_hash mismatch → REJECTED.
10. market_resolved=True → REJECTED.
11. Multi-level walk produces correct volume-weighted avg_price.
12. GTC partial remainder not counted as cancelled.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.backtest.fill_simulator import SimulatedFill, simulate_fill


# ---------------------------------------------------------------------------
# Fixtures / shared book helpers
# ---------------------------------------------------------------------------

ASKS = [
    {"price": "0.51", "size": "100"},
    {"price": "0.52", "size": "200"},
    {"price": "0.53", "size": "300"},
]

BIDS = [
    {"price": "0.50", "size": "100"},
    {"price": "0.49", "size": "200"},
    {"price": "0.48", "size": "300"},
]

MIDPOINT = 0.505  # (0.51 best ask + 0.50 best bid) / 2

DEFAULT_KWARGS = dict(
    tick_size=0.01,
    min_order_size=1.0,
    fee_rate=0.0,
    bids=BIDS,
    asks=ASKS,
)


# ---------------------------------------------------------------------------
# 1. BUY uses ask book
# ---------------------------------------------------------------------------

def test_buy_uses_ask_book():
    """BUY fills from the ASK side at 0.51 (best ask), NOT 0.505 (midpoint)."""
    result = simulate_fill(
        side="buy",
        order_type="GTC",
        limit_price=0.55,
        requested_size=50,
        **DEFAULT_KWARGS,
    )
    assert result.fill_status == "FILLED"
    assert result.avg_price is not None
    # Best ask is 0.51; avg_price must be >= 0.51
    assert result.avg_price >= Decimal("0.51"), (
        f"Expected avg_price >= 0.51 (best ask), got {result.avg_price}"
    )
    # Avg price must NOT equal midpoint
    assert result.avg_price != Decimal(str(MIDPOINT))


# ---------------------------------------------------------------------------
# 2. SELL uses bid book
# ---------------------------------------------------------------------------

def test_sell_uses_bid_book():
    """SELL fills from the BID side at 0.50 (best bid), NOT 0.505 (midpoint)."""
    result = simulate_fill(
        side="sell",
        order_type="GTC",
        limit_price=0.45,
        requested_size=50,
        **DEFAULT_KWARGS,
    )
    assert result.fill_status == "FILLED"
    assert result.avg_price is not None
    # Best bid is 0.50; avg_price must be <= 0.50
    assert result.avg_price <= Decimal("0.50"), (
        f"Expected avg_price <= 0.50 (best bid), got {result.avg_price}"
    )
    assert result.avg_price != Decimal(str(MIDPOINT))


# ---------------------------------------------------------------------------
# 3. Midpoint-priced expectation FAILS (explicit proof)
# ---------------------------------------------------------------------------

def test_buy_midpoint_expectation_fails():
    """Proves simulator doesn't use midpoint: expecting avg==midpoint is WRONG."""
    result = simulate_fill(
        side="buy",
        order_type="GTC",
        limit_price=0.55,
        requested_size=50,
        **DEFAULT_KWARGS,
    )
    assert result.avg_price is not None
    # This assertion MUST fail if we used midpoint — it proves we don't
    assert result.avg_price != Decimal(str(MIDPOINT)), (
        "avg_price equals midpoint — simulator is using midpoint instead of ask!"
    )


# ---------------------------------------------------------------------------
# 4. FOK no-full-fill → CANCELLED, filled_size=0
# ---------------------------------------------------------------------------

def test_fok_no_full_fill_cancelled():
    """FOK requesting more than available depth → CANCELLED with zero fill."""
    # Only 100+200+300=600 shares available; request 700
    result = simulate_fill(
        side="buy",
        order_type="FOK",
        limit_price=0.99,
        requested_size=700,
        **DEFAULT_KWARGS,
    )
    assert result.fill_status == "CANCELLED"
    assert result.filled_size == Decimal("0")
    assert result.cancelled_remainder == Decimal("700")
    assert result.avg_price is None
    assert result.fees == Decimal("0")


def test_fok_full_fill():
    """FOK with enough depth → FILLED."""
    result = simulate_fill(
        side="buy",
        order_type="FOK",
        limit_price=0.99,
        requested_size=100,
        **DEFAULT_KWARGS,
    )
    assert result.fill_status == "FILLED"
    assert result.filled_size == Decimal("100")


# ---------------------------------------------------------------------------
# 5. FAK partial → PARTIAL + cancelled_remainder
# ---------------------------------------------------------------------------

def test_fak_partial_fill():
    """FAK with limit cutting off higher levels → PARTIAL, remainder cancelled."""
    # limit=0.51 means only the first ask level (100 @ 0.51) is accessible
    result = simulate_fill(
        side="buy",
        order_type="FAK",
        limit_price=0.51,
        requested_size=150,  # want 150, only 100 at/below 0.51
        **DEFAULT_KWARGS,
    )
    assert result.fill_status == "PARTIAL"
    assert result.filled_size == Decimal("100")
    assert result.cancelled_remainder == Decimal("50")
    assert result.avg_price == Decimal("0.51")


# ---------------------------------------------------------------------------
# 6. min_order_size enforced → REJECTED
# ---------------------------------------------------------------------------

def test_min_order_size_rejected():
    """Requesting less than min_order_size → REJECTED."""
    result = simulate_fill(
        side="buy",
        order_type="GTC",
        limit_price=0.55,
        requested_size=0.5,  # below min_order_size=1
        tick_size=0.01,
        min_order_size=1.0,
        fee_rate=0.0,
        bids=BIDS,
        asks=ASKS,
    )
    assert result.fill_status == "REJECTED"
    assert "min_order_size" in result.reason


# ---------------------------------------------------------------------------
# 7. tick_size enforcement → REJECTED on mis-ticked limit
# ---------------------------------------------------------------------------

def test_tick_size_rejected():
    """limit_price not aligned to tick_size → REJECTED."""
    result = simulate_fill(
        side="buy",
        order_type="GTC",
        limit_price=0.515,  # not a 0.01 tick boundary
        requested_size=50,
        tick_size=0.01,
        min_order_size=1.0,
        fee_rate=0.0,
        bids=BIDS,
        asks=ASKS,
    )
    assert result.fill_status == "REJECTED"
    assert "tick" in result.reason


# ---------------------------------------------------------------------------
# 8. Fees change filled cost
# ---------------------------------------------------------------------------

def test_fees_applied():
    """With fee_rate > 0, fees > 0 and reduce net shares."""
    result_no_fee = simulate_fill(
        side="buy",
        order_type="GTC",
        limit_price=0.99,
        requested_size=100,
        tick_size=0.01,
        min_order_size=1.0,
        fee_rate=0.0,
        bids=BIDS,
        asks=ASKS,
    )
    result_fee = simulate_fill(
        side="buy",
        order_type="GTC",
        limit_price=0.99,
        requested_size=100,
        tick_size=0.01,
        min_order_size=1.0,
        fee_rate=0.05,
        bids=BIDS,
        asks=ASKS,
    )
    assert result_fee.fees > Decimal("0"), "Fee should be positive with fee_rate=0.05"
    assert result_no_fee.fees == Decimal("0"), "No fee expected with fee_rate=0.0"
    # avg_price is gross (same book, same fill); only fees differ
    assert result_fee.avg_price == result_no_fee.avg_price
    # Polymarket formula: fee = fee_rate * p * (1-p) * filled_size
    # For p=0.51, fee_rate=0.05, filled=100: 0.05 * 0.51 * 0.49 * 100 = 1.2495
    expected_fee = Decimal("0.05") * Decimal("0.51") * Decimal("0.49") * Decimal("100")
    assert abs(result_fee.fees - expected_fee) < Decimal("1e-8")


# ---------------------------------------------------------------------------
# 9. orderbook_hash mismatch → REJECTED
# ---------------------------------------------------------------------------

def test_orderbook_hash_mismatch_rejected():
    """Stale book guard: expected_hash != orderbook_hash → REJECTED."""
    result = simulate_fill(
        side="buy",
        order_type="GTC",
        limit_price=0.55,
        requested_size=50,
        tick_size=0.01,
        min_order_size=1.0,
        fee_rate=0.0,
        bids=BIDS,
        asks=ASKS,
        orderbook_hash="aaaa",
        expected_hash="bbbb",
    )
    assert result.fill_status == "REJECTED"
    assert result.reason == "orderbook_hash_mismatch"


def test_orderbook_hash_match_allowed():
    """Matching hashes do not block the fill."""
    result = simulate_fill(
        side="buy",
        order_type="GTC",
        limit_price=0.55,
        requested_size=50,
        tick_size=0.01,
        min_order_size=1.0,
        fee_rate=0.0,
        bids=BIDS,
        asks=ASKS,
        orderbook_hash="same",
        expected_hash="same",
    )
    assert result.fill_status != "REJECTED"


# ---------------------------------------------------------------------------
# 10. market_resolved → REJECTED
# ---------------------------------------------------------------------------

def test_market_resolved_rejected():
    """Cannot fill a resolved market."""
    result = simulate_fill(
        side="buy",
        order_type="GTC",
        limit_price=0.55,
        requested_size=50,
        tick_size=0.01,
        min_order_size=1.0,
        fee_rate=0.0,
        bids=BIDS,
        asks=ASKS,
        market_resolved=True,
    )
    assert result.fill_status == "REJECTED"
    assert result.reason == "market_resolved"


# ---------------------------------------------------------------------------
# 11. Multi-level walk: volume-weighted avg_price
# ---------------------------------------------------------------------------

def test_multilevel_avg_price():
    """Buying across two levels produces correct VWAP."""
    # 100 @ 0.51 + 50 @ 0.52 = fill 150
    # notional = 100*0.51 + 50*0.52 = 51 + 26 = 77
    # avg = 77 / 150 = 0.51333...
    result = simulate_fill(
        side="buy",
        order_type="GTC",
        limit_price=0.99,
        requested_size=150,
        tick_size=0.01,
        min_order_size=1.0,
        fee_rate=0.0,
        bids=BIDS,
        asks=ASKS,
    )
    assert result.fill_status == "FILLED"
    assert result.levels_consumed == 2
    expected_avg = Decimal("77") / Decimal("150")
    assert abs(result.avg_price - expected_avg) < Decimal("1e-10"), (
        f"Expected avg {expected_avg}, got {result.avg_price}"
    )


# ---------------------------------------------------------------------------
# 12. GTC partial: remainder is NOT cancelled
# ---------------------------------------------------------------------------

def test_gtc_partial_remainder_not_cancelled():
    """GTC partial fill does not populate cancelled_remainder."""
    # limit=0.51 → only 100 shares available; request 150
    result = simulate_fill(
        side="buy",
        order_type="GTC",
        limit_price=0.51,
        requested_size=150,
        **DEFAULT_KWARGS,
    )
    assert result.fill_status == "PARTIAL"
    assert result.filled_size == Decimal("100")
    assert result.cancelled_remainder == Decimal("0"), (
        "GTC remainder rests — it should NOT be marked cancelled"
    )
