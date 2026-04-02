"""Pre-sell collateral verification. Live safety mechanism.

Polymarket binary markets: selling YES shares requires (1 - price) * shares
as collateral locked. Without sufficient collateral, the sell order fails
on-chain, but the position is already marked as "exiting" locally.

This check is FAIL-CLOSED: if we can't verify collateral, we don't sell.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def check_sell_collateral(
    entry_price: float,
    shares: float,
    clob,
) -> tuple[bool, Optional[str]]:
    """Verify wallet has enough collateral to sell.

    Returns: (can_sell, reason) — reason only set on failure.
    """
    try:
        balance = float(clob.get_balance())
    except Exception as exc:
        # Can't check → don't sell (fail-closed)
        return False, f"balance_fetch_failed: {exc}"

    required = (1.0 - entry_price) * shares
    if required < 0:
        required = 0.0  # Edge case: entry_price > 1.0 shouldn't happen but be safe

    if balance < required:
        logger.warning(
            "COLLATERAL INSUFFICIENT: need $%.2f, have $%.2f (entry=%.3f, shares=%.2f)",
            required, balance, entry_price, shares,
        )
        return False, f"need ${required:.2f}, have ${balance:.2f}"

    return True, None
