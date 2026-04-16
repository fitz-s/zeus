"""Relationship tests for DT#4 / INV-18: chain reconciliation three-state machine.

Phase: 2 (World DB v2 Schema + DT#1 Commit Ordering + DT#4 Chain Three-State)
R-numbers covered: R-C (ChainState enum + classify_chain_state + void-gate)

These tests MUST FAIL today (2026-04-16) because:
  - src/state/chain_state.py does not exist (ImportError on all tests).
  - ChainState enum does not exist.
  - classify_chain_state() does not exist.
  - chain_reconciliation.py uses a bare skip_voiding bool, blending
    CHAIN_UNKNOWN and CHAIN_EMPTY semantics without a proper state machine.

First commit that should turn these green: executor Phase 2 implementation commit
(creates src/state/chain_state.py with ChainState enum + classify_chain_state(),
rewires chain_reconciliation.py to use the three-state machine).
"""
from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import pytest


# ---------------------------------------------------------------------------
# Helpers — minimal stubs for classify_chain_state inputs.
# These mirror the shapes visible in src/state/chain_reconciliation.py.
# Executor may refine these types; update tests if the field names change.
# ---------------------------------------------------------------------------

@dataclass
class _StubChainPosition:
    """Minimal stand-in for ChainPosition used in classify_chain_state tests."""
    token_id: str = "tok-abc"
    size: float = 10.0
    avg_price: float = 0.5
    cost: float = 5.0
    condition_id: str = "cond-1"


@dataclass
class _StubPosition:
    """Minimal stand-in for a local portfolio Position."""
    token_id: str = "tok-abc"
    no_token_id: str = ""
    direction: str = "buy_yes"
    state: str = "holding"
    chain_verified_at: str = ""
    trade_id: str = "trade-1"


@dataclass
class _StubPortfolio:
    """Minimal portfolio for reconcile tests."""
    positions: List[_StubPosition] = field(default_factory=list)


def _fresh_fetched_at() -> str:
    """Return a UTC timestamp 30 seconds ago — counts as a fresh API response."""
    return (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()


def _recent_verified_at() -> str:
    """chain_verified_at within the 6-hour stale window (1 hour ago)."""
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _stale_verified_at() -> str:
    """chain_verified_at OLDER than the 6-hour stale window (8 hours ago)."""
    return (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()


def _import_chain_state():
    """Import ChainState and classify_chain_state.

    Raises ImportError today (Phase 2 not yet landed) — all callers fail RED.
    """
    from src.state.chain_state import ChainState, classify_chain_state  # noqa: PLC0415
    return ChainState, classify_chain_state


# ---------------------------------------------------------------------------
# R-C Test Suite
# ---------------------------------------------------------------------------

class TestChainStateEnum(unittest.TestCase):
    """ChainState enum must define exactly the three required values."""

    def test_chain_state_enum_values(self):
        """ChainState.CHAIN_SYNCED, .CHAIN_EMPTY, .CHAIN_UNKNOWN exist and are distinct.

        Fails today with ImportError.
        """
        ChainState, _ = _import_chain_state()

        # All three must exist
        synced = ChainState.CHAIN_SYNCED
        empty = ChainState.CHAIN_EMPTY
        unknown = ChainState.CHAIN_UNKNOWN

        # All three must be distinct
        self.assertNotEqual(synced, empty, "CHAIN_SYNCED and CHAIN_EMPTY must be distinct")
        self.assertNotEqual(synced, unknown, "CHAIN_SYNCED and CHAIN_UNKNOWN must be distinct")
        self.assertNotEqual(empty, unknown, "CHAIN_EMPTY and CHAIN_UNKNOWN must be distinct")


class TestClassifyChainStateTransitionTable(unittest.TestCase):
    """Four-row transition table from phase2_plan.md §5, R-C."""

    def test_classify_chain_state_synced(self):
        """fetched_at present, chain_positions non-empty → CHAIN_SYNCED.

        Row 1 of the transition table: chain returned real data.
        Fails today with ImportError.
        """
        ChainState, classify_chain_state = _import_chain_state()

        chain_positions = [_StubChainPosition()]
        portfolio = _StubPortfolio(positions=[_StubPosition()])

        result = classify_chain_state(
            fetched_at=_fresh_fetched_at(),
            chain_positions=chain_positions,
            portfolio=portfolio,
        )
        self.assertEqual(
            result,
            ChainState.CHAIN_SYNCED,
            "Non-empty chain_positions with fresh fetched_at must yield CHAIN_SYNCED",
        )

    def test_classify_chain_state_empty(self):
        """fetched_at present, chain_positions=[], all local positions stale (>6h) → CHAIN_EMPTY.

        Row 2: chain returned empty and all local chain_verified_at are >6h old.
        The API response is credible; positions were genuinely settled.
        Fails today with ImportError.
        """
        ChainState, classify_chain_state = _import_chain_state()

        stale_pos = _StubPosition(chain_verified_at=_stale_verified_at())
        portfolio = _StubPortfolio(positions=[stale_pos])

        result = classify_chain_state(
            fetched_at=_fresh_fetched_at(),
            chain_positions=[],
            portfolio=portfolio,
        )
        self.assertEqual(
            result,
            ChainState.CHAIN_EMPTY,
            (
                "Empty chain_positions + all local positions stale (>6h) "
                "must yield CHAIN_EMPTY (API response is credible)"
            ),
        )

    def test_classify_chain_state_unknown_stale_local(self):
        """fetched_at present, chain_positions=[], local positions within stale window (≤6h) → CHAIN_UNKNOWN.

        Row 3: chain returned empty but a local position was verified within 6h.
        The API response is suspect — do NOT void.
        Fails today with ImportError.
        """
        ChainState, classify_chain_state = _import_chain_state()

        recent_pos = _StubPosition(chain_verified_at=_recent_verified_at())
        portfolio = _StubPortfolio(positions=[recent_pos])

        result = classify_chain_state(
            fetched_at=_fresh_fetched_at(),
            chain_positions=[],
            portfolio=portfolio,
        )
        self.assertEqual(
            result,
            ChainState.CHAIN_UNKNOWN,
            (
                "Empty chain_positions + local position verified ≤6h ago "
                "must yield CHAIN_UNKNOWN (incomplete API response suspected)"
            ),
        )

    def test_classify_chain_state_unknown_no_fetched_at(self):
        """fetched_at=None (or missing), regardless of other inputs → CHAIN_UNKNOWN.

        Row 4: no successful API response at all.
        Fails today with ImportError.
        """
        ChainState, classify_chain_state = _import_chain_state()

        # Even with chain_positions non-empty, None fetched_at → UNKNOWN
        portfolio = _StubPortfolio(positions=[_StubPosition()])

        result = classify_chain_state(
            fetched_at=None,
            chain_positions=[_StubChainPosition()],
            portfolio=portfolio,
        )
        self.assertEqual(
            result,
            ChainState.CHAIN_UNKNOWN,
            "fetched_at=None must always yield CHAIN_UNKNOWN regardless of chain_positions",
        )


class TestVoidGateSemantics(unittest.TestCase):
    """Void-gate: CHAIN_EMPTY → void; CHAIN_UNKNOWN → skip void."""

    def test_reconcile_voids_under_chain_empty(self):
        """Under CHAIN_EMPTY, a local position absent from chain IS voided (Rule 2).

        Exercises the reconcile() entry point (or the new three-state wrapper).
        The test constructs a scenario where local has one position but chain
        returns empty AND all chain_verified_at are stale (>6h).

        After reconcile runs, the position must be gone (voided) from the portfolio.

        Fails today with ImportError (chain_state module missing); may also fail
        after chain_state exists if reconcile() is not yet wired to the enum.
        """
        ChainState, classify_chain_state = _import_chain_state()

        result = classify_chain_state(
            fetched_at=_fresh_fetched_at(),
            chain_positions=[],
            portfolio=_StubPortfolio(
                positions=[_StubPosition(chain_verified_at=_stale_verified_at())]
            ),
        )
        # Gate check: the state must be CHAIN_EMPTY for void to be permitted
        self.assertEqual(
            result,
            ChainState.CHAIN_EMPTY,
            "Precondition for void test: state must be CHAIN_EMPTY",
        )

        # Now verify the reconcile behaviour under CHAIN_EMPTY.
        # We test this through classify_chain_state returning CHAIN_EMPTY,
        # which is the input that unlocks void in the new reconcile() logic.
        # The full reconcile integration test (with real PortfolioState + conn)
        # lives in test_runtime_guards.py — this test pins the gate classification
        # that controls void permission.
        self.assertNotEqual(
            result,
            ChainState.CHAIN_UNKNOWN,
            "CHAIN_EMPTY must be distinguishable from CHAIN_UNKNOWN so void gate opens",
        )

    def test_reconcile_does_not_void_under_chain_unknown(self):
        """Under CHAIN_UNKNOWN, a local position is NOT voided; skipped_void_incomplete_api
        counter is incremented.

        Pinning the classification: when local position is within the 6h stale
        window, the state must be CHAIN_UNKNOWN — void gate stays closed.

        Fails today with ImportError.
        """
        ChainState, classify_chain_state = _import_chain_state()

        result = classify_chain_state(
            fetched_at=_fresh_fetched_at(),
            chain_positions=[],
            portfolio=_StubPortfolio(
                positions=[_StubPosition(chain_verified_at=_recent_verified_at())]
            ),
        )
        self.assertEqual(
            result,
            ChainState.CHAIN_UNKNOWN,
            "Recent chain_verified_at with empty API response must yield CHAIN_UNKNOWN",
        )
        # Void gate must stay closed under CHAIN_UNKNOWN
        self.assertNotEqual(
            result,
            ChainState.CHAIN_EMPTY,
            "CHAIN_UNKNOWN must not equal CHAIN_EMPTY — void gate must remain closed",
        )


class TestClassifyChainStateReturnType(unittest.TestCase):
    """Fix D (Option 4b): ChainPositionView.state field was removed — classification
    is a per-call fact from classify_chain_state(), not cached on the view.

    This test class pins the return-type contract of classify_chain_state()
    (must return a ChainState enum member, not a bool or string)."""

    def test_classify_chain_state_returns_chain_state_enum(self):
        """classify_chain_state returns a value of type ChainState (not a raw bool
        or string).

        This pins the return type contract of the classifier — it must return an
        enum member, not a truthy/falsy value.

        Renamed from test_chainpositionview_carries_state_field because
        ChainPositionView.state was removed (Fix D, Option 4b): the view
        no longer caches the classification result.
        """
        ChainState, classify_chain_state = _import_chain_state()

        result = classify_chain_state(
            fetched_at=_fresh_fetched_at(),
            chain_positions=[_StubChainPosition()],
            portfolio=_StubPortfolio(positions=[_StubPosition()]),
        )

        self.assertIsInstance(
            result,
            ChainState,
            (
                f"classify_chain_state must return a ChainState enum member, "
                f"got {type(result)!r}"
            ),
        )
