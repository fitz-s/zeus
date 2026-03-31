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


@dataclass(frozen=True)
class ChainPositionView:
    """Immutable per-cycle snapshot of chain state.

    Built once per cycle from chain API. All downstream code reads from this
    snapshot, never from live API calls mid-cycle. Prevents inconsistent reads
    when chain state changes during a cycle.
    """
    positions: tuple  # tuple of ChainPosition (frozen requires immutable)
    fetched_at: str = ""
    is_stale: bool = False

    @staticmethod
    def from_chain_positions(
        chain_positions: list[ChainPosition],
        fetched_at: str = "",
    ) -> "ChainPositionView":
        return ChainPositionView(
            positions=tuple(chain_positions),
            fetched_at=fetched_at or datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def empty() -> "ChainPositionView":
        return ChainPositionView(positions=(), is_stale=True)

    def _by_token(self) -> dict:
        return {cp.token_id: cp for cp in self.positions}

    def has_token(self, token_id: str) -> bool:
        return any(cp.token_id == token_id for cp in self.positions)

    def get_position(self, token_id: str):
        for cp in self.positions:
            if cp.token_id == token_id:
                return cp
        return None


def reconcile(portfolio: PortfolioState, chain_positions: list[ChainPosition]) -> dict:
    """Three rules. No reasoning about WHY. Chain is truth.

    Returns: {"synced": int, "voided": int, "quarantined": int}

    Safety: if chain returns 0 positions but local has N, the API likely
    returned incomplete data. Skip voiding to prevent false PHANTOM kills.
    """
    chain_by_token = {cp.token_id: cp for cp in chain_positions}
    local_tokens = set()
    stats = {"synced": 0, "voided": 0, "quarantined": 0, "updated": 0, "skipped_pending": 0}
    now = datetime.now(timezone.utc).isoformat()

    # Count non-pending local positions for incomplete-response guard
    active_local = sum(
        1 for p in portfolio.positions
        if p.state != "pending_tracked"
        and (p.token_id if p.direction == "buy_yes" else p.no_token_id)
    )
    # If chain returned 0 positions but we have active local positions,
    # the API response is likely incomplete. Skip voiding.
    skip_voiding = active_local > 0 and len(chain_positions) == 0
    if skip_voiding:
        logger.warning(
            "INCOMPLETE CHAIN RESPONSE: 0 chain positions but %d local active. "
            "Skipping Rule 2 (void) to prevent false PHANTOM kills.",
            active_local,
        )
        stats["skipped_void_incomplete_api"] = active_local

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
            if skip_voiding:
                continue  # Don't void — API response is suspect
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

    # Rule 3: Chain but NOT local → QUARANTINE (skip ignored tokens)
    ignored = set(getattr(portfolio, "ignored_tokens", []) or [])
    for tid, chain in chain_by_token.items():
        if tid in ignored:
            continue  # Token was redeemed/expired/manually closed — don't resurrect
        if tid not in local_tokens:
            logger.warning("QUARANTINE: chain token %s...%s not in portfolio",
                           tid[:8], tid[-4:])
            quarantine_pos = Position(
                trade_id=f"quarantine_{tid[:8]}",
                market_id=chain.condition_id,
                city="UNKNOWN", cluster="Other",
                target_date="UNKNOWN", bin_label="UNKNOWN",
                direction="unknown",
                size_usd=chain.cost or (chain.size * chain.avg_price),
                entry_price=chain.avg_price,
                p_posterior=chain.avg_price,
                edge=0.0,
                entered_at=datetime.now(timezone.utc).isoformat(),
                token_id=tid,
                state="holding",
                strategy="",
                edge_source="",
                cost_basis_usd=chain.cost or (chain.size * chain.avg_price),
                shares=chain.size,
                chain_state="quarantined",
                chain_shares=chain.size,
                chain_verified_at=now,
                condition_id=chain.condition_id,
                quarantined_at=now,
            )
            portfolio.positions.append(quarantine_pos)
            stats["quarantined"] += 1

    return stats


QUARANTINE_TIMEOUT_HOURS = 48


def check_quarantine_timeouts(portfolio: PortfolioState) -> int:
    """Expire quarantined positions after 48 hours.

    Expired positions become eligible for exit evaluation.
    Returns: number of positions expired.
    """
    now = datetime.now(timezone.utc)
    expired = 0

    for pos in portfolio.positions:
        if pos.chain_state != "quarantined":
            continue
        if not pos.quarantined_at:
            continue

        try:
            quarantined_dt = datetime.fromisoformat(
                pos.quarantined_at.replace("Z", "+00:00")
            )
        except ValueError:
            continue

        hours_quarantined = (now - quarantined_dt).total_seconds() / 3600
        if hours_quarantined > QUARANTINE_TIMEOUT_HOURS:
            logger.warning(
                "QUARANTINE EXPIRED: %s held for %.0fh — forcing exit evaluation",
                pos.trade_id, hours_quarantined,
            )
            pos.chain_state = "quarantine_expired"
            expired += 1

    return expired
