"""Tests for K4 Slice J — Time & Quarantine fixes (Bugs #18, #57)."""

from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import datetime, timedelta, timezone

import pytest

from src.state.chain_reconciliation import check_quarantine_timeouts, QUARANTINE_TIMEOUT_HOURS
from src.state.portfolio import Position, PortfolioState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_quarantined_position(
    trade_id: str = "test-pos-1",
    quarantined_at: str = "",
) -> Position:
    """Create a minimal quarantined Position for testing."""
    return Position(
        trade_id=trade_id,
        market_id="test-market",
        city="Chicago",
        cluster="test-cluster",
        target_date="2026-04-15",
        bin_label="60-65",
        direction="buy_yes",
        chain_state="quarantined",
        quarantined_at=quarantined_at,
    )


# ---------------------------------------------------------------------------
# Bug #18: obs_settled dead variable removed from day0_signal.py
# ---------------------------------------------------------------------------


def test_obs_settled_not_used():
    """Verify obs_settled dead variable is gone from Day0Signal.p_vector."""
    from src.signal import day0_signal

    source = textwrap.dedent(inspect.getsource(day0_signal.Day0Signal.p_vector))
    tree = ast.parse(source)

    # Check no assignment target named 'obs_settled' exists
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "obs_settled":
                    pytest.fail("obs_settled dead variable still exists in p_vector")


# ---------------------------------------------------------------------------
# Bug #57: Bad/missing quarantined_at → zombie positions
# ---------------------------------------------------------------------------


def test_quarantine_bad_timestamp_forces_expiry():
    """Position with unparseable quarantined_at gets expired immediately."""
    pos = _make_quarantined_position(quarantined_at="not-a-date")
    portfolio = PortfolioState(positions=[pos])

    expired = check_quarantine_timeouts(portfolio)

    assert expired == 1
    assert pos.chain_state == "quarantine_expired"


def test_quarantine_missing_timestamp_forces_expiry():
    """Position with quarantined_at='' (empty) gets expired immediately."""
    pos = _make_quarantined_position(quarantined_at="")
    portfolio = PortfolioState(positions=[pos])

    expired = check_quarantine_timeouts(portfolio)

    assert expired == 1
    assert pos.chain_state == "quarantine_expired"


def test_quarantine_valid_timestamp_still_works():
    """Position quarantined > 48h ago expires normally."""
    old_time = datetime.now(timezone.utc) - timedelta(hours=QUARANTINE_TIMEOUT_HOURS + 1)
    pos = _make_quarantined_position(quarantined_at=old_time.isoformat())
    portfolio = PortfolioState(positions=[pos])

    expired = check_quarantine_timeouts(portfolio)

    assert expired == 1
    assert pos.chain_state == "quarantine_expired"


def test_quarantine_valid_timestamp_not_yet_expired():
    """Position quarantined < 48h ago stays quarantined."""
    recent_time = datetime.now(timezone.utc) - timedelta(hours=1)
    pos = _make_quarantined_position(quarantined_at=recent_time.isoformat())
    portfolio = PortfolioState(positions=[pos])

    expired = check_quarantine_timeouts(portfolio)

    assert expired == 0
    assert pos.chain_state == "quarantined"
