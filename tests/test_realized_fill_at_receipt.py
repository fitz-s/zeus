# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: PR #19 phase 4 closeout completion + phase 3 P3.3
#                  commit-message promise to thread RealizedFill at
#                  fill receipt
"""Slice P5-1 relationship + function tests.

P3.3 (commit a1d539d) commit message promised "+ thread RealizedFill at
fill receipt"; the diff only delivered planning-side typing of
ExecutionIntent.max_slippage. P3.3b/P4-3 (commit 25effbf) closed the
anticipated-slippage half at executor.py:249. P5-1 closes the receipt
half: at exit_lifecycle.py:453, :611, and :600 area, construct typed
RealizedFill from actual vs expected fill prices.

Tests pin:
1. Typed RealizedFill is constructed when valid prices present
   (DEBUG log surfaces typed slippage).
2. Defensive skip when prices are insufficient (e.g., expected_price=0,
   shares=0, empty trade_id) — no exception, no log.
3. Construction failure (malformed input that bypasses the skip guard)
   surfaces a WARNING — exit flow not crashed.
4. Side semantics: sell with actual<expected → adverse direction.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.execution.exit_lifecycle import _emit_typed_realized_fill


def test_emit_realized_fill_with_valid_prices_logs_typed_slippage(caplog):
    """Happy path: valid actual + expected → DEBUG log carries typed slippage."""
    with caplog.at_level(logging.DEBUG, logger="src.execution.exit_lifecycle"):
        _emit_typed_realized_fill(
            actual_price=0.45,
            expected_price=0.48,
            side="sell",
            shares=10.0,
            trade_id="entry-2026-04-15-NYC-001",
        )
    realized_logs = [
        r for r in caplog.records
        if "realized_fill:" in r.message
    ]
    assert realized_logs, "RealizedFill construction must emit DEBUG log"
    msg = realized_logs[0].message
    assert "trade=entry-2026-04-15-NYC-001" in msg
    assert "side=sell" in msg
    # Sell with actual (0.45) < expected (0.48) → adverse (received less)
    assert "direction=adverse" in msg
    # Magnitude: |0.45 - 0.48| / 0.48 * 10000 ≈ 625 bps
    assert "625" in msg or "624" in msg or "626" in msg  # allow floating-point


def test_emit_realized_fill_skips_on_zero_expected_price(caplog):
    """Defensive guard: expected_price <= 0 → silent skip, no log."""
    with caplog.at_level(logging.DEBUG, logger="src.execution.exit_lifecycle"):
        _emit_typed_realized_fill(
            actual_price=0.45,
            expected_price=0.0,
            side="sell",
            shares=10.0,
            trade_id="trade-x",
        )
    assert not any("realized_fill:" in r.message for r in caplog.records)
    assert not any("RealizedFill construction failed" in r.message for r in caplog.records)


def test_emit_realized_fill_skips_on_zero_shares(caplog):
    with caplog.at_level(logging.DEBUG, logger="src.execution.exit_lifecycle"):
        _emit_typed_realized_fill(
            actual_price=0.45,
            expected_price=0.48,
            side="sell",
            shares=0.0,
            trade_id="trade-x",
        )
    assert not any("realized_fill:" in r.message for r in caplog.records)


def test_emit_realized_fill_skips_on_empty_trade_id(caplog):
    with caplog.at_level(logging.DEBUG, logger="src.execution.exit_lifecycle"):
        _emit_typed_realized_fill(
            actual_price=0.45,
            expected_price=0.48,
            side="sell",
            shares=10.0,
            trade_id="",
        )
    assert not any("realized_fill:" in r.message for r in caplog.records)


def test_emit_realized_fill_warns_on_construction_failure(caplog):
    """Pathological input that passes the skip guard but fails construction
    (e.g., side="invalid") → WARNING surfaced, no crash."""
    with caplog.at_level(logging.WARNING, logger="src.execution.exit_lifecycle"):
        _emit_typed_realized_fill(
            actual_price=0.45,
            expected_price=0.48,
            side="invalid",  # not "buy" / "sell" → SlippageBps raises
            shares=10.0,
            trade_id="trade-x",
        )
    assert any(
        "RealizedFill construction failed" in r.message
        for r in caplog.records
    ), "construction failure must surface as WARNING for ops review"


def test_buy_side_actual_above_expected_is_adverse(caplog):
    """BUY semantics: actual > expected → adverse (paid more)."""
    with caplog.at_level(logging.DEBUG, logger="src.execution.exit_lifecycle"):
        _emit_typed_realized_fill(
            actual_price=0.52,
            expected_price=0.50,
            side="buy",
            shares=10.0,
            trade_id="trade-buy-1",
        )
    realized_logs = [
        r for r in caplog.records
        if "realized_fill:" in r.message
    ]
    assert realized_logs
    assert "direction=adverse" in realized_logs[0].message


def test_buy_side_actual_below_expected_is_favorable(caplog):
    """BUY semantics: actual < expected → favorable (paid less)."""
    with caplog.at_level(logging.DEBUG, logger="src.execution.exit_lifecycle"):
        _emit_typed_realized_fill(
            actual_price=0.48,
            expected_price=0.50,
            side="buy",
            shares=10.0,
            trade_id="trade-buy-2",
        )
    realized_logs = [
        r for r in caplog.records
        if "realized_fill:" in r.message
    ]
    assert realized_logs
    assert "direction=favorable" in realized_logs[0].message
