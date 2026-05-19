# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: PR 2 antibody tests — wide_spread_display_substitution and depth_at_best_ask fields
# Reuse: Run when executable_market_snapshot_v2.py, snapshot_repo.py, or market_scanner.py change.
"""R-EE.5 and R-EE.6 tests for PR 2 microstructure transparency fields.

R-EE.5: wide_spread_display_substitution and depth_at_best_ask roundtrip through snapshot_repo
R-EE.6: one-sided book (orderbook_top_ask=None) yields depth_at_best_ask=0
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.contracts.executable_market_snapshot_v2 import (
    ExecutableMarketSnapshotV2,
    WIDE_SPREAD_THRESHOLD_USD,
)
from src.state.db import init_schema
from src.state.snapshot_repo import (
    init_snapshot_schema,
    insert_snapshot,
    get_snapshot,
)
from src.data.market_scanner import _compute_spread, _depth_at_best_ask


# ── Constants ────────────────────────────────────────────────────────────────

NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


# ── Fixture ──────────────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    """In-memory world DB with snapshot schema (incl. PR2 columns)."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    init_snapshot_schema(c)
    yield c
    c.close()


def _make_snapshot(
    snapshot_id: str = "snap-pr2-test",
    wide_spread: bool = True,
    depth: int = 50,
    orderbook_top_ask: Decimal | None = Decimal("0.51"),
    orderbook_depth_jsonb: str = '{"asks":[{"price":"0.51","size":"50"}],"bids":[{"price":"0.49","size":"100"}]}',
    **overrides,
) -> ExecutableMarketSnapshotV2:
    """Minimal snapshot factory with PR2 fields."""
    kwargs = dict(
        snapshot_id=snapshot_id,
        gamma_market_id="gamma-1",
        event_id="event-1",
        event_slug="weather-nyc-high",
        condition_id="condition-1",
        question_id="question-1",
        yes_token_id="yes-token",
        no_token_id="no-token",
        selected_outcome_token_id="yes-token",
        outcome_label="YES",
        enable_orderbook=True,
        active=True,
        closed=False,
        accepting_orders=True,
        market_start_at=NOW + timedelta(hours=1),
        market_end_at=NOW + timedelta(days=1),
        market_close_at=NOW + timedelta(days=1, hours=1),
        sports_start_at=None,
        min_tick_size=Decimal("0.01"),
        min_order_size=Decimal("0.01"),
        fee_details={"bps": 0, "source": "test"},
        token_map_raw={"YES": "yes-token", "NO": "no-token"},
        rfqe=None,
        neg_risk=False,
        orderbook_top_bid=Decimal("0.49"),
        orderbook_top_ask=orderbook_top_ask,
        orderbook_depth_jsonb=orderbook_depth_jsonb,
        raw_gamma_payload_hash=HASH_A,
        raw_clob_market_info_hash=HASH_B,
        raw_orderbook_hash=HASH_C,
        authority_tier="CLOB",
        captured_at=NOW,
        freshness_deadline=NOW + timedelta(seconds=30),
        # PR 2 fields
        wide_spread_display_substitution=wide_spread,
        depth_at_best_ask=depth,
    )
    kwargs.update(overrides)
    return ExecutableMarketSnapshotV2(**kwargs)


# ── R-EE.5: roundtrip test ───────────────────────────────────────────────────

def test_wide_spread_fields_roundtrip_through_snapshot_repo(conn):
    """R-EE.5: Insert snapshot with wide_spread=True, depth=50;
    reload from DB; assert fields equal."""
    snap = _make_snapshot(
        snapshot_id="snap-r-ee5",
        wide_spread=True,
        depth=50,
    )
    insert_snapshot(conn, snap)
    loaded = get_snapshot(conn, "snap-r-ee5")
    assert loaded is not None, "Snapshot not found after insert"
    assert loaded.wide_spread_display_substitution is True, (
        f"wide_spread_display_substitution should be True, got {loaded.wide_spread_display_substitution}"
    )
    assert loaded.depth_at_best_ask == 50, (
        f"depth_at_best_ask should be 50, got {loaded.depth_at_best_ask}"
    )


def test_narrow_spread_false_roundtrip(conn):
    """Narrow spread snapshot stores wide_spread=False correctly."""
    snap = _make_snapshot(
        snapshot_id="snap-narrow",
        wide_spread=False,
        depth=200,
    )
    insert_snapshot(conn, snap)
    loaded = get_snapshot(conn, "snap-narrow")
    assert loaded is not None
    assert loaded.wide_spread_display_substitution is False
    assert loaded.depth_at_best_ask == 200


def test_zero_depth_roundtrip(conn):
    """depth_at_best_ask=0 (one-sided book or unavailable) stores and reloads correctly."""
    snap = _make_snapshot(
        snapshot_id="snap-zero-depth",
        wide_spread=False,
        depth=0,
    )
    insert_snapshot(conn, snap)
    loaded = get_snapshot(conn, "snap-zero-depth")
    assert loaded is not None
    assert loaded.depth_at_best_ask == 0


def test_pr2_schema_idempotent(conn):
    """init_snapshot_schema can be called twice without raising 'duplicate column' errors."""
    # Second call should silently skip already-existing columns
    init_snapshot_schema(conn)  # no error = pass


# ── R-EE.6: one-sided book → depth=0 ─────────────────────────────────────────

def test_one_sided_book_yields_zero_depth():
    """R-EE.6: _depth_at_best_ask returns 0 when orderbook has no asks."""
    no_ask_orderbook = {
        "bids": [{"price": "0.49", "size": "100"}],
        # no "asks" key
    }
    assert _depth_at_best_ask(no_ask_orderbook) == 0


def test_empty_asks_yields_zero_depth():
    """Empty asks list returns 0 depth."""
    empty_asks_orderbook = {
        "bids": [{"price": "0.49", "size": "100"}],
        "asks": [],
    }
    assert _depth_at_best_ask(empty_asks_orderbook) == 0


def test_asks_present_yields_nonzero_depth():
    """When asks are present, depth_at_best_ask > 0."""
    orderbook = {
        "bids": [{"price": "0.49", "size": "100"}],
        "asks": [{"price": "0.51", "size": "75"}],
    }
    depth = _depth_at_best_ask(orderbook)
    assert depth == 75, f"Expected 75, got {depth}"


def test_depth_truncated_to_int():
    """Fractional ask sizes are truncated to int (floor)."""
    orderbook = {
        "bids": [{"price": "0.49", "size": "100"}],
        "asks": [{"price": "0.51", "size": "99.9"}],
    }
    depth = _depth_at_best_ask(orderbook)
    assert isinstance(depth, int)
    assert depth == 99


# ── WIDE_SPREAD_THRESHOLD constant ───────────────────────────────────────────

def test_wide_spread_threshold_value():
    """WIDE_SPREAD_THRESHOLD_USD should be Decimal('0.10')."""
    assert WIDE_SPREAD_THRESHOLD_USD == Decimal("0.10")


def test_compute_spread_none_when_no_ask():
    """_compute_spread returns None when top_ask is None (one-sided book)."""
    result = _compute_spread({}, top_bid=Decimal("0.49"), top_ask=None)
    assert result is None


def test_compute_spread_correct_value():
    """_compute_spread returns ask - bid."""
    result = _compute_spread({}, top_bid=Decimal("0.49"), top_ask=Decimal("0.51"))
    assert result == Decimal("0.02")


# ── Dataclass validators ──────────────────────────────────────────────────────

def test_negative_depth_at_best_ask_raises():
    """Negative depth_at_best_ask must raise ValueError in __post_init__."""
    with pytest.raises(ValueError, match="depth_at_best_ask"):
        _make_snapshot(depth=-1)


def test_snapshot_default_fields_are_safe():
    """Default PR2 fields (wide_spread=False, depth=0) construct without error."""
    snap = _make_snapshot(
        snapshot_id="snap-defaults",
        wide_spread=False,
        depth=0,
    )
    assert snap.wide_spread_display_substitution is False
    assert snap.depth_at_best_ask == 0
