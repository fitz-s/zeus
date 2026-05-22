# Created: 2026-05-22
# Last reused/audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §0
#                  + docs/reference/zeus_math_spec.md §11.5
"""Canonical fee function phi — shared by all Zeus strategies.

§0 (STRATEGY_TAXONOMY_DIRECTIVE): phi = C · feeRate · p(1−p)
§11.5 (zeus_math_spec): F_i(q) = Σ r · p · (1−p) · Δq; r MUST be sourced from
    live venue config / fee schedule, never hardcoded.

Design decisions:
- Decimal arithmetic throughout to avoid floating-point accumulation in
  multi-leg cost sums (neg_risk_basket: Σ F_i over N bins).
- fee_rate MUST be passed explicitly or obtained via venue_fee_rate(); no
  hardcoded default. This is a code-provenance requirement.
- Maker fee = 0: phi(q, p, Decimal('0')) == 0 automatically from the formula;
  no special-case branching.
"""

from __future__ import annotations

from decimal import Decimal


def phi(shares: Decimal, price: Decimal, fee_rate: Decimal) -> Decimal:
    """Polymarket taker fee for a position of `shares` at `price`.

    Formula: phi = shares × fee_rate × price × (1 − price)

    Args:
        shares: Number of shares (quantity, C in formula). Must be >= 0.
        price: Execution price in probability units ∈ (0, 1).
        fee_rate: Venue taker fee rate. Use venue_fee_rate() for live config value.
            Maker orders use Decimal('0') — fee is 0 automatically from the formula.

    Returns:
        Decimal fee cost in probability units (same space as price).

    Raises:
        ValueError: if price not in (0, 1) or shares < 0 or fee_rate < 0.
    """
    if shares < Decimal("0"):
        raise ValueError(f"shares must be >= 0, got {shares}")
    if not (Decimal("0") < price < Decimal("1")):
        raise ValueError(f"price must be in (0, 1), got {price}")
    if fee_rate < Decimal("0"):
        raise ValueError(f"fee_rate must be >= 0, got {fee_rate}")

    return shares * fee_rate * price * (Decimal("1") - price)


def venue_fee_rate() -> Decimal:
    """Return the venue taker fee rate as Decimal, sourced from config.

    Reads settings['exit']['fee_rate'] — the canonical Polymarket fee rate
    stored in config/settings.json. See src/config.exit_fee_rate() for
    the float accessor and its bounds-checking logic.

    Authority: zeus_math_spec.md §11.5 — r MUST be sourced from live venue config.
    """
    from src.config import exit_fee_rate as _exit_fee_rate

    return Decimal(str(_exit_fee_rate()))
