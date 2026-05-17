# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: OPS_FORENSICS.md §F8 — F8 part 1 antibody: pending_fill_rescue stamps ISO timestamp not sentinel
"""Antibody test: pending_fill_rescue occurred_at must be ISO timestamp, not 'unknown_entered_at'.

Relationship invariant:
    When chain_reconciliation rescues a position whose entered_at is falsy,
    the `now` ISO timestamp MUST be stamped — never the literal sentinel string
    "unknown_entered_at".

F8 bug: line 658 set rescued.entered_at = "unknown_entered_at" instead of now.
Fix: rescued.entered_at = now (now already in scope).
"""
from __future__ import annotations

import re
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from src.state.chain_reconciliation import reconcile
from src.state.portfolio import Position, PortfolioState

# ISO 8601 basic pattern: starts with 4-digit year
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")

_TRADE_ID = "test-rescue-f8"
_TOKEN_ID = "tok-rescue-001"
_ORDER_ID = "ord-test-1"
_CONDITION_ID = "cond-test-1"


def _make_stub_conn() -> sqlite3.Connection:
    """In-memory DB with tables needed by reconcile's nested guards.

    Provides:
    - position_current with phase='pending_entry' → baseline guard passes
    - Empty venue_commands → durable-command guard returns False (skips fill-fact guard)
    - position_events with payload/env columns for _emit_rescue_event
    - rescue_events_v2 for log_rescue_event
    - position_history (empty) for _has_canonical_position_history
    - trade_lifecycle for update_trade_lifecycle
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        f"""
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY, phase TEXT
        );
        INSERT INTO position_current VALUES ('{_TRADE_ID}', 'pending_entry');

        CREATE TABLE venue_commands (
            venue_order_id TEXT, intent_kind TEXT
        );

        CREATE TABLE position_history (
            position_id TEXT, sequence_no INTEGER
        );

        CREATE TABLE position_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id TEXT,
            sequence_no INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            payload TEXT,
            source_module TEXT,
            env TEXT
        );

        CREATE TABLE rescue_events_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT,
            position_id TEXT,
            decision_snapshot_id TEXT,
            temperature_metric TEXT,
            causality_status TEXT,
            authority TEXT,
            authority_source TEXT,
            chain_state TEXT,
            reason TEXT,
            occurred_at TEXT,
            UNIQUE(trade_id, occurred_at)
        );

        CREATE TABLE trade_lifecycle (
            trade_id TEXT PRIMARY KEY,
            state TEXT,
            entered_at TEXT,
            updated_at TEXT
        );
        """
    )
    return conn


def _make_pending_position() -> Position:
    """Position in pending_tracked state with no entered_at — triggers rescue path."""
    return Position(
        trade_id=_TRADE_ID,
        market_id="mkt-test",
        city="Karachi",
        cluster="South-Asia",
        target_date="2026-05-17",
        bin_label="90-95F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=10.0,
        entry_price=0.5,
        cost_basis_usd=10.0,
        shares=20.0,
        state="pending_tracked",
        chain_state="unknown",
        entered_at="",  # falsy — triggers the F8 rescue sentinel path
        token_id=_TOKEN_ID,
        order_id=_ORDER_ID,
        entry_order_id=_ORDER_ID,
        condition_id=_CONDITION_ID,
    )


def _make_chain_position() -> MagicMock:
    """Filled chain position matching the pending position."""
    cp = MagicMock()
    cp.condition_id = _CONDITION_ID
    cp.size = 20.0
    cp.avg_price = 0.5
    cp.cost = 10.0
    cp.chain_state = "synced"
    cp.chain_verified_at = "2026-05-17T10:00:00+00:00"
    cp.entry_price = 0.5
    cp.cost_basis_usd = 10.0
    cp.is_quarantined = False
    cp.token_id = _TOKEN_ID
    return cp


def test_pending_fill_rescue_stamps_iso_not_sentinel():
    """After rescue, pos.entered_at must be an ISO timestamp, not 'unknown_entered_at'.

    Patches build_reconciliation_rescue_canonical_write + append_many_and_project
    to avoid requiring full DB schema while still exercising the rescue code path.
    """
    pos = _make_pending_position()
    portfolio = PortfolioState(positions=[pos])
    conn = _make_stub_conn()
    chain_cp = _make_chain_position()

    fake_projection = MagicMock()
    fake_projection.state = "entered"
    fake_projection.entered_at = "2026-05-17T10:00:00+00:00"

    with (
        patch(
            "src.engine.lifecycle_events.build_reconciliation_rescue_canonical_write",
            return_value=([], fake_projection),
        ),
        patch("src.state.db.append_many_and_project"),
    ):
        reconcile(portfolio, [chain_cp], conn=conn)

    entered_at = pos.entered_at
    assert entered_at != "unknown_entered_at", (
        f"entered_at must not be the sentinel string; got {entered_at!r}"
    )
    assert entered_at and _ISO_RE.match(str(entered_at)), (
        f"entered_at must be an ISO timestamp; got {entered_at!r}"
    )
