# Created: 2026-06-12
# Last reused/audited: 2026-06-12
# Authority basis: external deep code review 2026-06-12 FINDING-A (operator direct-fix
#   order). Twin-authority: the final TAKER size was swept from the elected DB
#   snapshot's depth while the limit price was the FRESH submit-time witness price; the
#   witness carries no depth, so stale size paired with fresh price could oversize a
#   FOK/FAK against liquidity no longer on the book.
"""FINDING-A relationship invariant: the swept depth must belong to the same book the
fresh witness witnessed, or the candidate fails CLOSED with the typed TRANSIENT reason
``LIVE_DEPTH_AUTHORITY_MISSING`` (requeue + refresh), never sizing from stale depth.

The canonical case from the review: DB snapshot depth = 300 shares at an acceptable
ask, the fresh witness shows a DIFFERENT (thin) touch, and the candidate size > the
live top-of-book. The fix forbids sizing from the stale 300.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.event_reactor_adapter import _assert_taker_depth_authority_fresh
from src.events.reactor import (
    TRANSIENT_MONEY_PATH_REASONS,
    _is_transient_money_path_reason,
)


def _snapshot_with_top(
    *,
    top_ask: str | None,
    top_bid: str | None,
    depth_at_best_ask: int,
):
    """An elected DB snapshot stand-in carrying a (possibly stale) top-of-book + depth.

    The invariant under test reads ONLY ``orderbook_top_ask`` / ``orderbook_top_bid``
    via getattr (exactly as it does on the real ExecutableMarketSnapshot object); a
    lightweight namespace is a faithful, dependency-free substitute that keeps the test
    pinned to the relationship (touch agreement) rather than the snapshot's full schema.
    """
    return SimpleNamespace(
        orderbook_top_ask=Decimal(top_ask) if top_ask is not None else None,
        orderbook_top_bid=Decimal(top_bid) if top_bid is not None else None,
        depth_at_best_ask=depth_at_best_ask,
    )


def test_reason_registered_transient():
    """The new reason base must be a registered TRANSIENT money-path reason."""
    assert "LIVE_DEPTH_AUTHORITY_MISSING" in TRANSIENT_MONEY_PATH_REASONS
    wrapped = (
        "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:"
        "LIVE_DEPTH_AUTHORITY_MISSING:side=buy_no:witness_touch=0.73:"
        "snapshot_touch=0.50:tick=0.01"
    )
    assert _is_transient_money_path_reason(wrapped) is True


def test_stale_depth_book_divergence_fails_closed_buy_no():
    """DB snapshot top-ask 0.50 (300-share depth), fresh witness crosses at 0.73 — the
    books diverge by far more than a tick, so the 300-share depth is UNWITNESSED. The
    candidate must NOT size from the stale 300; it fails CLOSED with the typed reason.
    """
    snap = _snapshot_with_top(top_ask="0.50", top_bid="0.49", depth_at_best_ask=300)
    with pytest.raises(ValueError) as exc:
        _assert_taker_depth_authority_fresh(
            snapshot=snap,
            direction="buy_no",
            witness_touch=Decimal("0.73"),
            tick_size=Decimal("0.01"),
        )
    msg = str(exc.value)
    assert msg.startswith("LIVE_DEPTH_AUTHORITY_MISSING:")
    # And the surfaced reason is classified TRANSIENT (requeue, never terminal-burn).
    assert _is_transient_money_path_reason(msg) is True


def test_missing_snapshot_touch_fails_closed():
    """A snapshot with no top-of-book on the relevant side carries no witnessed depth."""
    snap = _snapshot_with_top(top_ask=None, top_bid="0.49", depth_at_best_ask=300)
    with pytest.raises(ValueError, match=r"^LIVE_DEPTH_AUTHORITY_MISSING:"):
        _assert_taker_depth_authority_fresh(
            snapshot=snap,
            direction="buy_no",
            witness_touch=Decimal("0.73"),
            tick_size=Decimal("0.01"),
        )


def test_agreeing_book_within_one_tick_passes():
    """When the snapshot top-of-book agrees with the witness touch (same book, ≤ 1 tick),
    the swept depth IS the witnessed depth — the sweep proceeds (no raise)."""
    snap = _snapshot_with_top(top_ask="0.73", top_bid="0.72", depth_at_best_ask=300)
    # exact agreement
    _assert_taker_depth_authority_fresh(
        snapshot=snap,
        direction="buy_no",
        witness_touch=Decimal("0.73"),
        tick_size=Decimal("0.01"),
    )
    # one tick of drift is still the same depth authority
    _assert_taker_depth_authority_fresh(
        snapshot=snap,
        direction="buy_no",
        witness_touch=Decimal("0.74"),
        tick_size=Decimal("0.01"),
    )


def test_sell_side_uses_top_bid():
    """A SELL taker's depth authority is the snapshot top-BID vs the witnessed bid."""
    snap = _snapshot_with_top(top_ask="0.55", top_bid="0.30", depth_at_best_ask=0)
    # bid diverges -> fail closed
    with pytest.raises(ValueError, match=r"^LIVE_DEPTH_AUTHORITY_MISSING:"):
        _assert_taker_depth_authority_fresh(
            snapshot=snap,
            direction="sell_yes",
            witness_touch=Decimal("0.50"),
            tick_size=Decimal("0.01"),
        )
    # bid agrees -> pass
    _assert_taker_depth_authority_fresh(
        snapshot=snap,
        direction="sell_yes",
        witness_touch=Decimal("0.30"),
        tick_size=Decimal("0.01"),
    )
