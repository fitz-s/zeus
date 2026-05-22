# Lifecycle: created=2026-05-22; last_reviewed=2026-05-22; last_reused=never
# Purpose: Relationship tests for phi() fee formula and venue_fee_rate() config contract (§11.5)
# Reuse: verify config/settings.json fee_rate entry is current before trusting phi numerical assertions
"""Relationship tests for src/strategy/fees.py — canonical fee function phi.

§0: phi = C · feeRate · p(1−p), feeRate from config, maker fee = 0.
§11.5: F_i(q) = Σ r · p · (1−p) · Δq; r MUST be sourced from live venue config.

These tests are RELATIONSHIP tests — they verify the contract between the fee
function and the Polymarket fee formula, and between phi and the config system.
They are written BEFORE the implementation (TDD as required by operator).
"""

from __future__ import annotations

from decimal import Decimal

import pytest


# ---------------------------------------------------------------------------
# Test 1: phi formula matches Polymarket spec across price × size grid
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("shares,price,fee_rate", [
    (Decimal("1"), Decimal("0.1"), Decimal("0.05")),
    (Decimal("1"), Decimal("0.3"), Decimal("0.05")),
    (Decimal("1"), Decimal("0.5"), Decimal("0.05")),
    (Decimal("1"), Decimal("0.7"), Decimal("0.05")),
    (Decimal("1"), Decimal("0.9"), Decimal("0.05")),
    (Decimal("10"), Decimal("0.1"), Decimal("0.05")),
    (Decimal("10"), Decimal("0.5"), Decimal("0.05")),
    (Decimal("100"), Decimal("0.3"), Decimal("0.05")),
    (Decimal("100"), Decimal("0.7"), Decimal("0.05")),
    # Non-default fee_rate
    (Decimal("1"), Decimal("0.5"), Decimal("0.02")),
    (Decimal("50"), Decimal("0.5"), Decimal("0.03")),
])
def test_phi_formula_grid(shares: Decimal, price: Decimal, fee_rate: Decimal) -> None:
    """phi(shares, price, fee_rate) == shares * fee_rate * price * (1 - price).

    Relationship: phi output must exactly match Polymarket formula §11.5.
    """
    from src.strategy.fees import phi

    expected = shares * fee_rate * price * (Decimal("1") - price)
    result = phi(shares, price, fee_rate)
    assert result == expected, (
        f"phi({shares}, {price}, {fee_rate}) = {result}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# Test 2: phi = 0 for maker (fee_rate = 0)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("shares,price", [
    (Decimal("1"), Decimal("0.1")),
    (Decimal("1"), Decimal("0.5")),
    (Decimal("100"), Decimal("0.9")),
])
def test_phi_maker_is_zero(shares: Decimal, price: Decimal) -> None:
    """Maker fee = 0 — phi(q, p, Decimal('0')) == 0 for any q, p.

    Relationship: §0 states maker fee = 0; phi must respect this by returning
    exactly Decimal('0') when fee_rate=0, with no special branching required.
    """
    from src.strategy.fees import phi

    result = phi(shares, price, Decimal("0"))
    assert result == Decimal("0"), (
        f"phi({shares}, {price}, Decimal('0')) = {result!r}, expected Decimal('0')"
    )


# ---------------------------------------------------------------------------
# Test 3: phi returns Decimal type (not float)
# ---------------------------------------------------------------------------

def test_phi_decimal_arithmetic() -> None:
    """phi result is Decimal, not float.

    Relationship: Decimal arithmetic required to avoid floating-point
    accumulation in multi-leg cost sums (neg_risk_basket, center_buy, etc.).
    """
    from src.strategy.fees import phi

    result = phi(Decimal("10"), Decimal("0.5"), Decimal("0.05"))
    assert isinstance(result, Decimal), (
        f"phi must return Decimal, got {type(result).__name__}"
    )


# ---------------------------------------------------------------------------
# Test 4: venue_fee_rate() sourced from config, not hardcoded
# ---------------------------------------------------------------------------

def test_venue_fee_rate_from_config() -> None:
    """venue_fee_rate() reads settings['exit']['fee_rate'] from config.

    Relationship: fee_rate MUST be sourced from config per §11.5 code-provenance
    rule. Direct hardcoding of 0.05 is forbidden.
    """
    from src.strategy.fees import venue_fee_rate
    from src.config import exit_fee_rate

    # venue_fee_rate() must agree with the canonical config accessor
    assert venue_fee_rate() == Decimal(str(exit_fee_rate())), (
        "venue_fee_rate() must return the same value as exit_fee_rate() from config"
    )


# ---------------------------------------------------------------------------
# Test 5: phi with venue_fee_rate() matches expected at default config value
# ---------------------------------------------------------------------------

def test_phi_with_venue_fee_rate() -> None:
    """phi(shares, price, venue_fee_rate()) agrees with manual fee_rate=0.05 at default config.

    Relationship: the fee accessor path must produce the same result as explicit
    fee_rate when config is at default (0.05).
    """
    from src.strategy.fees import phi, venue_fee_rate

    shares = Decimal("10")
    price = Decimal("0.5")
    rate = venue_fee_rate()
    result = phi(shares, price, rate)
    expected = shares * rate * price * (Decimal("1") - price)
    assert result == expected


# ---------------------------------------------------------------------------
# Test 6: phi boundary — price approaching 0 or 1 yields near-zero fee
# ---------------------------------------------------------------------------

def test_phi_boundary_prices() -> None:
    """Fee approaches 0 at extreme prices — phi is highest at p=0.5.

    Relationship: Polymarket fee is price-dependent (not flat); fee_rate × p × (1−p)
    has maximum at p=0.5.
    """
    from src.strategy.fees import phi

    rate = Decimal("0.05")
    shares = Decimal("1")

    fee_at_half = phi(shares, Decimal("0.5"), rate)
    fee_at_low = phi(shares, Decimal("0.1"), rate)
    fee_at_high = phi(shares, Decimal("0.9"), rate)

    assert fee_at_half > fee_at_low, "fee at p=0.5 must exceed p=0.1"
    assert fee_at_half > fee_at_high, "fee at p=0.5 must exceed p=0.9"
    # p=0.1 and p=0.9 are symmetric: p(1-p) = 0.09 in both cases
    assert fee_at_low == fee_at_high, "phi(p=0.1) == phi(p=0.9) by symmetry"
