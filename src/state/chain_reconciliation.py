"""Chain reconciliation: 3 rules. Chain is truth. Portfolio is cache.

Blueprint v2 §5: Three sources of truth WILL disagree.
Chain > Chronicler > Portfolio. Always.

Rules:
1. Local + chain match → SYNCED
2. Local but NOT on chain → VOID immediately (don't ask why)
3. Chain but NOT local → QUARANTINE (low confidence, 48h forced exit eval)

Paper mode: skip (no chain to reconcile).
Live mode: MANDATORY every cycle before any trading.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.config import settings
from src.state.portfolio import Position, PortfolioState, void_position

logger = logging.getLogger(__name__)


@dataclass
class ChainPosition:
    """On-chain position data from CLOB API."""
    token_id: str
    size: float
    avg_price: float
    condition_id: str = ""


def reconcile(portfolio: PortfolioState, chain_positions: list[ChainPosition]) -> dict:
    """Three rules. No reasoning about WHY. Chain is truth.

    Returns: {"synced": int, "voided": int, "quarantined": int}
    """
    if settings.mode == "paper":
        return {"synced": 0, "voided": 0, "quarantined": 0, "skipped": "paper_mode"}

    chain_by_token = {cp.token_id: cp for cp in chain_positions}
    local_tokens = set()
    stats = {"synced": 0, "voided": 0, "quarantined": 0}

    for pos in list(portfolio.positions):
        tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
        local_tokens.add(tid)

        chain = chain_by_token.get(tid)
        if chain is None:
            # Rule 2: Local but NOT on chain → VOID immediately
            logger.warning("PHANTOM: %s not on chain → voiding", pos.trade_id)
            void_position(portfolio, pos.trade_id, "PHANTOM_NOT_ON_CHAIN")
            stats["voided"] += 1
        else:
            # Rule 1: check size match
            if abs(chain.size - (pos.size_usd / pos.entry_price if pos.entry_price > 0 else 0)) > 0.01:
                logger.warning("SIZE MISMATCH: %s local vs chain", pos.trade_id)
                # Update from chain (chain is truth)
            pos.state = "holding"
            stats["synced"] += 1

    # Rule 3: Chain but NOT local → QUARANTINE
    for tid, chain in chain_by_token.items():
        if tid not in local_tokens:
            logger.warning("QUARANTINE: chain token %s...%s not in portfolio",
                           tid[:8], tid[-4:])
            quarantine_pos = Position(
                trade_id=f"quarantine_{tid[:8]}",
                market_id=chain.condition_id,
                city="UNKNOWN", cluster="Other",
                target_date="UNKNOWN", bin_label="UNKNOWN",
                direction="buy_yes",  # Unknown direction — conservative
                size_usd=chain.size * chain.avg_price,
                entry_price=chain.avg_price,
                p_posterior=chain.avg_price,
                edge=0.0,
                entered_at=datetime.now(timezone.utc).isoformat(),
                token_id=tid,
                state="holding",  # Will be evaluated by monitor
                strategy="QUARANTINED",
            )
            portfolio.positions.append(quarantine_pos)
            stats["quarantined"] += 1

    return stats
