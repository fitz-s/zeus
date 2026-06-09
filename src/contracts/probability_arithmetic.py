# Created: 2026-06-07
# Last reused/audited: 2026-06-07
# Authority basis: PR_SPEC.md §2 FIX-5a (un-rewrite gratuitous (1/x-1)*x back to a
#   named helper with a docstring; de-obfuscate complement-of-1 arithmetic).
"""Named, documented scalar arithmetic helpers for probability/price math.

Background (§0.2): commit 16c35e7445 rewrote ``1 - x`` as the byte-different but
value-identical ``(1.0 / x - 1.0) * x`` purely to slip past the AST complement
guard. That obfuscation is illegible and, worse, it normalizes "write the
complement in a shape the guard can't see" as an acceptable move. These helpers
restore intent: a function name says WHAT the value is, and the value-equivalence
test (tests/test_one_minus_value_equivalence.py) makes the obfuscated shape a
detectable regression.

IMPORTANT: ``one_minus`` is for legitimate complement-of-1 scalars (a *remaining*
fraction after a discount, a Kelly denominator ``1 - price``). It is NOT a licence
to derive a Polymarket NO-side probability from a YES posterior — that remains
forbidden (Polymarket YES/NO are independent executable assets). The AST guard in
tests/test_probability_complement_ast_guard.py continues to forbid ``1 - x`` at the
live probability sites; these helpers exist for the non-probability scalar math
(discounts, Kelly denominators, payout odds) that was gratuitously obfuscated.
"""

from __future__ import annotations


def one_minus(x: float) -> float:
    """Return the complement-of-one scalar ``1 - x``.

    Use for a *remaining* multiplier after a fractional discount, or a Kelly
    denominator ``1 - price``. This is the readable, intent-revealing form of the
    value that 16c35e7445 obfuscated as ``(1.0 / x - 1.0) * x``.
    """

    return 1.0 - float(x)


def payout_odds(price: float) -> float:
    """Return the binary payout odds / max ROI for a unit stake at ``price``.

    ``payout_odds(price) = (1 - price) / price = 1/price - 1``. This is genuine
    odds arithmetic (NOT a complement-of-1); it answers "how many dollars of
    profit per dollar staked if the contract settles to 1". ``price`` must be in
    the open interval (0, 1).
    """

    p = float(price)
    if p <= 0.0 or p >= 1.0:
        raise ValueError("payout_odds requires price in the open interval (0, 1)")
    return (1.0 - p) / p
