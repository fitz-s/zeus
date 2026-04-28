"""Pre-sell collateral verification. Live safety mechanism.

Polymarket binary markets: selling YES shares requires (1 - price) * shares
as collateral locked. Without sufficient collateral, the sell order fails
on-chain, but the position is already marked as "exiting" locally.

This check is FAIL-CLOSED: if we can't verify collateral, we don't sell.
"""

import logging
from typing import Optional

from src.state.collateral_ledger import CollateralInsufficient, assert_sell_preflight

logger = logging.getLogger(__name__)


def check_sell_collateral(
    entry_price: float,
    shares: float,
    clob,
    *,
    token_id: str = "",
) -> tuple[bool, Optional[str]]:
    """Verify CTF outcome-token inventory for a sell.

    Returns: (can_sell, reason) — reason only set on failure.
    """
    if token_id:
        try:
            assert_sell_preflight(token_id, shares)
            return True, None
        except CollateralInsufficient as exc:
            return False, str(exc)

    # Legacy compatibility for tests/callers that do not have token identity.
    # Runtime exit paths pass token_id and therefore use CollateralLedger. This
    # fallback must not be treated as proof that pUSD can satisfy a CTF sell.
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
