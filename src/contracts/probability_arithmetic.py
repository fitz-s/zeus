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

IMPORTANT: ``one_minus`` is for legitimate complement-of-1 scalars: a *remaining*
fraction after a discount, a Kelly denominator ``1 - price``, or an explicitly
named point-outcome conversion such as q(NO event) from q(YES event). It is NOT a
licence to derive a NO-token executable price from a YES-token price, or to turn
a YES lower confidence bound into a NO lower confidence bound. The AST guard in
tests/test_probability_complement_ast_guard.py continues to forbid open-coded
``1 - x`` at live probability sites so every complement has to pass through a
named semantic boundary.
"""

from __future__ import annotations

import math


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


# z quantiles of the standard normal used by the Wilson lower bound.
Z_ONE_SIDED_95 = 1.6448536269514722
Z_TWO_SIDED_95 = 1.959963984540054


def wilson_lower_bound(successes: float, trials: float, *, z: float) -> float:
    """Wilson score lower bound on a binomial proportion ``successes / trials``.

    ``successes`` is clamped into ``[0, trials]`` and the bound into
    ``[0, successes / trials]``, so with no success it is exactly zero.  The
    closed form alone is ``centre - margin`` with ``centre == margin`` there,
    and that float difference leaves a ~7e-18 residue for many ``trials`` (3,
    6, 7, 12, 24, 48, ...): a rate nothing ever measured.  Live 2026-09-28 that
    residue minted a maker fill witness and failed every auction cut.
    ``trials`` may be fractional (a depth cushion as evidence count).
    """

    n = float(trials)
    if not n > 0.0 or not math.isfinite(n) or not z > 0.0:
        return 0.0
    p_hat = min(max(float(successes), 0.0), n) / n
    z2 = z * z
    centre = p_hat + z2 / (2.0 * n)
    margin = z * math.sqrt(p_hat * (1.0 - p_hat) / n + z2 / (4.0 * n * n))
    lower = (centre - margin) / (1.0 + z2 / n)
    if not math.isfinite(lower):
        return 0.0
    return min(max(lower, 0.0), p_hat)
