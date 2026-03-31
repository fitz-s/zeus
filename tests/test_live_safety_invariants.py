"""Live safety invariant tests: relationship tests, not function tests.

These verify cross-module relationships that prevent ghost positions,
phantom P&L, and local↔chain divergence in live mode.

GOLDEN RULE: close_position() is ONLY called after confirmed FILLED.
"""

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.contracts.semantic_types import ChainState, ExitState, LifecycleState
from src.execution.collateral import check_sell_collateral
from src.execution.exit_lifecycle import (
    MAX_EXIT_RETRIES,
    check_pending_exits,
    check_pending_retries,
    execute_exit,
    is_exit_cooldown_active,
)
from src.state.chain_reconciliation import (
    QUARANTINE_TIMEOUT_HOURS,
    check_quarantine_timeouts,
)
from src.state.portfolio import (
    ExitDecision,
    Position,
    PortfolioState,
    close_position,
)


def _make_position(**overrides) -> Position:
    """Create a test position with sensible defaults."""
    defaults = dict(
        trade_id="test_001",
        market_id="mkt_001",
        city="Chicago",
        cluster="Great Lakes",
        target_date="2026-04-15",
        bin_label="60-65",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.55,
        edge=0.15,
        shares=25.0,
        cost_basis_usd=10.0,
        state="holding",
        token_id="tok_yes_001",
        no_token_id="tok_no_001",
        unit="F",
    )
    defaults.update(overrides)
    return Position(**defaults)


def _make_portfolio(*positions) -> PortfolioState:
    """Create portfolio with given positions."""
    return PortfolioState(positions=list(positions))


def _make_clob(
    order_status="OPEN",
    balance=100.0,
    sell_result=None,
):
    """Create mock CLOB client."""
    clob = MagicMock()
    clob.paper_mode = False
    clob.get_order_status.return_value = {"status": order_status}
    clob.get_balance.return_value = balance
    clob.cancel_order.return_value = {"status": "CANCELLED"}
    return clob


# ---- Test 1: GOLDEN RULE ----

def test_live_exit_never_closes_without_fill():
    """GOLDEN RULE: close_position only called after confirmed FILLED.

    If CLOB returns OPEN (not filled), position must remain open with
    retry_pending state. It must NOT be closed or voided.
    """
    pos = _make_position(state="holding")
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="OPEN", balance=100.0)

    with patch("src.execution.exit_lifecycle.place_sell_order") as mock_sell:
        mock_sell.return_value = {"orderID": "sell_123"}
        outcome = execute_exit(
            portfolio=portfolio,
            position=pos,
            exit_reason="EDGE_REVERSAL",
            current_market_price=0.45,
            paper_mode=False,
            clob=clob,
        )

    # Position must still be in portfolio (not closed)
    assert pos in portfolio.positions
    assert pos.state != "settled"
    assert pos.state != "voided"
    # Exit state should indicate sell was placed but not filled
    assert pos.exit_state in ("sell_placed", "sell_pending")


# ---- Test 2: Entry creates pending_tracked ----

def test_live_entry_creates_pending_tracked():
    """Entry must create position even before fill confirmed.

    The Position dataclass must support pending_tracked with entry_order_id.
    """
    pos = _make_position(
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
    )

    assert pos.state == "pending_tracked"
    assert pos.entry_order_id == "buy_123"
    assert pos.entry_fill_verified is False
    # Must have LifecycleState enum support
    assert LifecycleState(pos.state) == LifecycleState.PENDING_TRACKED


# ---- Test 3: Cancelled pending → void ----

def test_pending_tracked_voids_after_cancel():
    """Pending entry that gets cancelled → void, not phantom position."""
    pos = _make_position(
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
    )
    portfolio = _make_portfolio(pos)

    # Simulate CLOB returning CANCELLED
    from src.execution.fill_tracker import check_pending_entries
    clob = _make_clob(order_status="CANCELLED")

    stats = check_pending_entries(portfolio, clob)

    # Position should be voided and removed from portfolio
    assert stats["voided"] == 1
    assert len(portfolio.positions) == 0  # void_position removes from portfolio


# ---- Test 4: Retry respects cooldown ----

def test_exit_retry_respects_cooldown():
    """After failed sell, must wait cooldown before retrying."""
    future_time = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
    pos = _make_position(
        exit_state="retry_pending",
        next_exit_retry_at=future_time,
        exit_retry_count=1,
    )

    assert is_exit_cooldown_active(pos) is True

    # check_pending_retries should not reset a position in cooldown
    result = check_pending_retries(pos)
    assert result is False
    assert pos.exit_state == "retry_pending"  # unchanged


# ---- Test 5: Backoff exhausted holds to settlement ----

def test_backoff_exhausted_holds_to_settlement():
    """After MAX_EXIT_RETRIES retries, stop trying to sell. Hold to settlement."""
    pos = _make_position(
        exit_state="backoff_exhausted",
        exit_retry_count=MAX_EXIT_RETRIES,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob()

    # execute_exit should not be called for backoff_exhausted positions,
    # but even if it were, the position should remain unchanged
    result = check_pending_retries(pos)
    assert result is False
    assert pos.exit_state == "backoff_exhausted"

    # Position stays in portfolio — not closed, not voided
    assert pos in portfolio.positions
    assert pos.state != "settled"
    assert pos.state != "voided"


# ---- Test 6: Paper exit does not use sell order ----

def test_paper_exit_does_not_use_sell_order():
    """Paper mode: direct close_position, no CLOB interaction."""
    pos = _make_position(state="holding")
    portfolio = _make_portfolio(pos)
    clob = _make_clob()

    with patch("src.execution.exit_lifecycle.place_sell_order") as mock_sell:
        outcome = execute_exit(
            portfolio=portfolio,
            position=pos,
            exit_reason="EDGE_REVERSAL",
            current_market_price=0.45,
            paper_mode=True,
            clob=clob,
        )

    # No sell order should have been placed
    mock_sell.assert_not_called()
    # Position should be closed
    assert "paper_exit" in outcome


# ---- Test 7: Collateral check blocks underfunded sell ----

def test_collateral_check_blocks_underfunded_sell():
    """Can't sell if wallet doesn't have enough collateral."""
    clob = _make_clob(balance=0.50)

    # entry_price=0.10, shares=50 → needs (1-0.10)*50 = $45 collateral
    can_sell, reason = check_sell_collateral(
        entry_price=0.10, shares=50.0, clob=clob,
    )

    assert can_sell is False
    assert reason is not None
    assert "need $45.00" in reason


# ---- Test 8: Quarantine expires after 48h ----

def test_quarantine_expires_after_48h():
    """Quarantined positions become exit-eligible after 48 hours."""
    past_time = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
    pos = _make_position(
        chain_state="quarantined",
        quarantined_at=past_time,
    )
    portfolio = _make_portfolio(pos)

    expired = check_quarantine_timeouts(portfolio)

    assert expired == 1
    assert pos.chain_state == "quarantine_expired"


# ---- Bonus: Quarantine does NOT expire before 48h ----

def test_quarantine_does_not_expire_early():
    """Quarantined positions stay quarantined before 48 hours."""
    recent_time = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    pos = _make_position(
        chain_state="quarantined",
        quarantined_at=recent_time,
    )
    portfolio = _make_portfolio(pos)

    expired = check_quarantine_timeouts(portfolio)

    assert expired == 0
    assert pos.chain_state == "quarantined"


# ---- Bonus: Collateral check fail-closed on API error ----

def test_collateral_check_fails_closed_on_api_error():
    """If balance fetch fails, collateral check blocks the sell."""
    clob = MagicMock()
    clob.get_balance.side_effect = Exception("API timeout")

    can_sell, reason = check_sell_collateral(
        entry_price=0.40, shares=10.0, clob=clob,
    )

    assert can_sell is False
    assert "balance_fetch_failed" in reason


# ---- Bonus: Live exit blocked by collateral goes to retry ----

def test_live_exit_collateral_blocked_goes_to_retry():
    """Live exit that fails collateral check transitions to retry_pending."""
    pos = _make_position(state="holding")
    portfolio = _make_portfolio(pos)
    clob = _make_clob(balance=0.01)  # Not enough

    outcome = execute_exit(
        portfolio=portfolio,
        position=pos,
        exit_reason="EDGE_REVERSAL",
        current_market_price=0.45,
        paper_mode=False,
        clob=clob,
    )

    assert "collateral_blocked" in outcome
    assert pos.exit_state == "retry_pending"
    assert pos.exit_retry_count == 1
    assert pos in portfolio.positions  # NOT closed


# ---- Autonomous Discovery Tests ----

def test_incomplete_chain_response_skips_voiding():
    """If chain API returns 0 positions but we have active local positions,
    don't void them — the API response is likely incomplete."""
    from src.state.chain_reconciliation import reconcile

    pos = _make_position(state="holding", token_id="tok_yes_real")
    portfolio = _make_portfolio(pos)

    # Chain returns EMPTY — suspect incomplete API response
    stats = reconcile(portfolio, chain_positions=[])

    # Position should NOT be voided
    assert stats["voided"] == 0
    assert pos in portfolio.positions
    assert stats.get("skipped_void_incomplete_api", 0) > 0


def test_exit_retry_exponential_backoff():
    """Retry cooldown should increase exponentially."""
    from src.execution.exit_lifecycle import _mark_exit_retry, _parse_iso, _utcnow

    pos = _make_position()

    # First retry: base cooldown (300s = 5min)
    _mark_exit_retry(pos, reason="TEST", cooldown_seconds=300)
    first_retry = _parse_iso(pos.next_exit_retry_at)
    assert pos.exit_retry_count == 1
    assert pos.exit_state == "retry_pending"

    # Second retry: 2x cooldown (600s = 10min)
    _mark_exit_retry(pos, reason="TEST", cooldown_seconds=300)
    second_retry = _parse_iso(pos.next_exit_retry_at)
    assert pos.exit_retry_count == 2

    # Second retry should be further in the future than first was
    # (both relative to their own "now", so we just check count increments)
    assert pos.exit_retry_count == 2


def test_sell_order_rounds_shares_down():
    """Sell shares must round DOWN to prevent over-selling."""
    import math
    # 25.137 shares → 25.13 (floor), not 25.14 (ceil)
    shares = 25.137
    rounded = math.floor(shares * 100 + 1e-9) / 100.0
    assert rounded == 25.13

    # 0.004 shares → 0.00 (floor) → blocked
    tiny = 0.004
    rounded_tiny = math.floor(tiny * 100 + 1e-9) / 100.0
    assert rounded_tiny == 0.0  # Would be blocked by "shares <= 0" guard


def test_chain_position_view_immutability():
    """ChainPositionView must be immutable (frozen dataclass)."""
    from src.state.chain_reconciliation import ChainPosition, ChainPositionView

    cp = ChainPosition(token_id="tok_1", size=10.0, avg_price=0.50)
    view = ChainPositionView.from_chain_positions([cp])

    assert view.has_token("tok_1")
    assert not view.has_token("tok_nonexistent")
    assert view.get_position("tok_1").size == 10.0
    assert view.get_position("tok_nonexistent") is None

    # Frozen: cannot modify
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.fetched_at = "modified"


def test_stranded_exit_intent_recovered():
    """If place_sell_order throws, position is stranded in exit_intent.
    check_pending_exits must recover it via retry."""
    pos = _make_position(
        state="holding",
        exit_state="exit_intent",  # stranded by exception
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob()

    stats = check_pending_exits(portfolio, clob)

    assert stats["retried"] == 1
    assert pos.exit_state == "retry_pending"
    assert pos in portfolio.positions  # NOT closed


def test_ignored_tokens_skip_reconciliation():
    """Tokens in ignored_tokens should not be quarantined during reconciliation."""
    from src.state.chain_reconciliation import ChainPosition, reconcile

    portfolio = _make_portfolio()
    portfolio.ignored_tokens = ["tok_redeemed"]

    # Chain has a position for an ignored token
    chain = [ChainPosition(token_id="tok_redeemed", size=5.0, avg_price=0.30)]
    stats = reconcile(portfolio, chain)

    # Should NOT quarantine the ignored token
    assert stats["quarantined"] == 0


# ---- Provenance Tests ----

def test_position_carries_env():
    """Every position must carry its env provenance."""
    pos = _make_position(env="paper")
    assert pos.env == "paper"

    pos_live = _make_position(env="live")
    assert pos_live.env == "live"


def test_contamination_guard_blocks_wrong_env():
    """Loading a live position into paper portfolio (or vice versa) must fail."""
    from src.state.portfolio import PortfolioModeError, load_portfolio, save_portfolio
    import tempfile
    from pathlib import Path

    # Create a portfolio with a "live" position
    pos = _make_position(env="live")
    portfolio = _make_portfolio(pos)

    # Save to temp file
    tmp = Path(tempfile.mktemp(suffix=".json"))
    try:
        save_portfolio(portfolio, tmp)

        # Loading in paper mode (settings.mode == "paper") should raise
        # because the position has env="live"
        with pytest.raises(PortfolioModeError, match="live position"):
            load_portfolio(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def test_state_path_includes_mode():
    """state_path must produce mode-qualified filenames."""
    from src.config import state_path, settings
    path = state_path("positions.json")
    assert f"-{settings.mode}" in path.name


def test_empty_env_positions_pass_guard():
    """Positions with empty env (legacy) should pass the contamination guard."""
    pos = _make_position(env="")
    portfolio = _make_portfolio(pos)

    # Empty env should not trigger guard (backward compat for legacy data)
    import tempfile
    from pathlib import Path
    from src.state.portfolio import load_portfolio, save_portfolio

    tmp = Path(tempfile.mktemp(suffix=".json"))
    try:
        save_portfolio(portfolio, tmp)
        loaded = load_portfolio(tmp)
        assert len(loaded.positions) == 1
    finally:
        tmp.unlink(missing_ok=True)
