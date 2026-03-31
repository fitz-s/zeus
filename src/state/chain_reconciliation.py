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
from datetime import datetime, timezone

from src.state.portfolio import Position, PortfolioState, void_position

logger = logging.getLogger(__name__)


@dataclass
class ChainPosition:
    """On-chain position data from CLOB API."""
    token_id: str
    size: float
    avg_price: float
    cost: float = 0.0
    condition_id: str = ""


def reconcile(portfolio: PortfolioState, chain_positions: list[ChainPosition]) -> dict:
    """Three rules. No reasoning about WHY. Chain is truth.

    Returns: {"synced": int, "voided": int, "quarantined": int}
    """
    chain_by_token = {cp.token_id: cp for cp in chain_positions}
    local_tokens = set()
    stats = {"synced": 0, "voided": 0, "quarantined": 0, "updated": 0, "skipped_pending": 0}
    now = datetime.now(timezone.utc).isoformat()

    for pos in list(portfolio.positions):
        if pos.state == "pending_tracked":
            stats["skipped_pending"] += 1
            continue

        tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
        if not tid:
            continue
        local_tokens.add(tid)

        chain = chain_by_token.get(tid)
        if chain is None:
            # Rule 2: Local but NOT on chain → VOID immediately
            logger.warning("PHANTOM: %s not on chain → voiding", pos.trade_id)
            void_position(portfolio, pos.trade_id, "PHANTOM_NOT_ON_CHAIN")
            stats["voided"] += 1
        else:
            local_shares = pos.effective_shares
            pos.chain_state = "synced"
            pos.chain_shares = chain.size
            pos.chain_verified_at = now
            pos.condition_id = pos.condition_id or chain.condition_id
            if abs(chain.size - local_shares) > 0.01:
                logger.warning("SIZE MISMATCH: %s local %.4f vs chain %.4f", pos.trade_id, local_shares, chain.size)
                pos.shares = chain.size
                if chain.avg_price > 0:
                    pos.entry_price = chain.avg_price
                if chain.cost > 0:
                    pos.cost_basis_usd = chain.cost
                    pos.size_usd = chain.cost
                stats["updated"] += 1
            if pos.state in {"entered", "holding", "day0_window", "unknown"}:
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
                size_usd=chain.cost or (chain.size * chain.avg_price),
                entry_price=chain.avg_price,
                p_posterior=chain.avg_price,
                edge=0.0,
                entered_at=datetime.now(timezone.utc).isoformat(),
                token_id=tid,
                state="holding",  # Will be evaluated by monitor
                strategy="QUARANTINED",
                cost_basis_usd=chain.cost or (chain.size * chain.avg_price),
                shares=chain.size,
                chain_state="quarantined",
                chain_shares=chain.size,
                chain_verified_at=now,
                condition_id=chain.condition_id,
            )
            portfolio.positions.append(quarantine_pos)
            stats["quarantined"] += 1

    return stats
