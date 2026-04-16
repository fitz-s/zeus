"""P14: Phantom position persistence bug — skip_voiding staleness guard.

Verifies that the skip_voiding guard in chain_reconciliation.reconcile()
correctly differentiates between:
  1. Transient API failure (chain returns 0, but position is fresh) → skip voiding
  2. Legitimate settlement (chain returns 0, position stale >6h) → allow voiding
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.state.chain_reconciliation import reconcile
from src.state.portfolio import INACTIVE_RUNTIME_STATES, Position, PortfolioState


def _make_portfolio(*positions: Position) -> PortfolioState:
    return PortfolioState(positions=list(positions))


def _make_position(
    trade_id: str = "test-001",
    token_id: str = "tok-abc",
    state: str = "holding",
    chain_verified_at: str = "",
    direction: str = "buy_yes",
    **kwargs,
) -> Position:
    defaults = dict(
        market_id="mkt-1",
        city="Chicago",
        cluster="cluster-1",
        target_date="2026-04-20",
        bin_label=">=80",
        size_usd=50.0,
        entry_price=0.55,
        p_posterior=0.6,
        edge=0.05,
        entered_at="2026-04-10T12:00:00+00:00",
        strategy="zeus",
        edge_source="ensemble",
        shares=90.0,
        chain_state="synced",
        chain_shares=90.0,
        chain_verified_at=chain_verified_at,
    )
    defaults.update(kwargs)
    return Position(
        trade_id=trade_id,
        token_id=token_id,
        direction=direction,
        **defaults,
    )


class TestP14PhantomPersistence:
    """skip_voiding guard must respect staleness window."""

    def test_fresh_position_skips_voiding(self):
        """Chain returns 0, position verified <6h ago → DON'T void (API glitch)."""
        fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        pos = _make_position(chain_verified_at=fresh_ts)
        portfolio = _make_portfolio(pos)

        stats = reconcile(portfolio, chain_positions=[])

        # Position should survive — skip_voiding should fire
        assert stats.get("voided", 0) == 0
        assert stats.get("skipped_void_incomplete_api", 0) >= 1
        assert any(p.trade_id == "test-001" for p in portfolio.positions)

    def test_stale_position_allows_voiding(self):
        """Chain returns 0, position verified >6h ago → VOID (settled on-chain)."""
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        pos = _make_position(chain_verified_at=stale_ts)
        portfolio = _make_portfolio(pos)

        stats = reconcile(portfolio, chain_positions=[])

        # Position should be voided — stale guard excludes it from skip_voiding
        assert stats["voided"] == 1

    def test_mixed_fresh_and_stale(self):
        """One fresh + one stale, chain empty → fresh survives, stale voids."""
        fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()

        pos_fresh = _make_position(
            trade_id="fresh-001",
            token_id="tok-fresh",
            chain_verified_at=fresh_ts,
        )
        pos_stale = _make_position(
            trade_id="stale-001",
            token_id="tok-stale",
            chain_verified_at=stale_ts,
        )
        portfolio = _make_portfolio(pos_fresh, pos_stale)

        stats = reconcile(portfolio, chain_positions=[])

        # Fresh position keeps skip_voiding=True, so BOTH survive
        # (skip_voiding is all-or-nothing: if any truly_active remains, skip all voiding)
        assert stats.get("voided", 0) == 0
        assert stats.get("skipped_void_incomplete_api", 0) >= 1

    def test_all_stale_positions_void(self):
        """Multiple stale positions, chain empty → all void."""
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()

        pos1 = _make_position(trade_id="s1", token_id="tok-1", chain_verified_at=stale_ts)
        pos2 = _make_position(trade_id="s2", token_id="tok-2", chain_verified_at=stale_ts)
        portfolio = _make_portfolio(pos1, pos2)

        stats = reconcile(portfolio, chain_positions=[])

        assert stats["voided"] == 2

    def test_no_chain_verified_at_counts_as_active(self):
        """Position with empty chain_verified_at → treated as active (conservative)."""
        pos = _make_position(chain_verified_at="")
        portfolio = _make_portfolio(pos)

        stats = reconcile(portfolio, chain_positions=[])

        assert stats.get("voided", 0) == 0
        assert stats.get("skipped_void_incomplete_api", 0) >= 1

    def test_chain_nonempty_bypasses_guard(self):
        """Chain returns positions → skip_voiding is False regardless of staleness."""
        from src.state.chain_reconciliation import ChainPosition

        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        pos = _make_position(chain_verified_at=stale_ts)
        portfolio = _make_portfolio(pos)

        chain = [ChainPosition(token_id="tok-abc", size=90.0, avg_price=0.55)]
        stats = reconcile(portfolio, chain_positions=chain)

        # Should sync, not void
        assert stats["synced"] == 1
        assert stats.get("voided", 0) == 0
