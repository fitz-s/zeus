# Created: 2026-05-20
# Last reused or audited: 2026-05-21
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §7.3 (sha 00c2399742)
"""Antibody test: INV-vnext-substitution-consistency

Invariant: MarketAnalysisVNext.compute() derives wide_spread_display_substitution
independently from orderbook_top_bid / orderbook_top_ask, matching the legacy
market_scanner formula exactly:

  bool(_spread_usd is not None and _spread_usd >= WIDE_SPREAD_THRESHOLD_USD)

Cross-module relationship test:
  src/data/market_scanner.py (legacy scanner)
  vs src/analysis/market_analysis_vnext.py (MarketAnalysisVNext)

Production: both wide-spread (0.12 >= 0.10 → True) and narrow-spread
(0.03 < 0.10 → False) cases tested independently from orderbook inputs.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.analysis.market_analysis_vnext import MarketAnalysisVNext


def _make_snapshot(bid: Decimal | None, ask: Decimal | None) -> MagicMock:
    """Build a minimal mock ExecutableMarketSnapshotV2 with given orderbook prices."""
    snapshot = MagicMock()
    snapshot.orderbook_top_bid = bid
    snapshot.orderbook_top_ask = ask
    snapshot.depth_at_best_ask = 50
    snapshot.snapshot_id = "test-snap-001"
    snapshot.event_slug = "will-it-rain-chicago-2026-05-21"
    snapshot.condition_id = "0xdeadbeef"
    snapshot.captured_at.isoformat.return_value = "2026-05-21T12:00:00+00:00"
    return snapshot


def test_inv_vnext_substitution_consistency_wide_spread() -> None:
    """INV-vnext-substitution-consistency (wide spread):

    bid=0.44, ask=0.56, spread=0.12 >= WIDE_SPREAD_THRESHOLD_USD=0.10.
    MarketAnalysisVNext.compute() must derive wide_spread=True independently
    from orderbook inputs — NOT from snapshot.wide_spread_display_substitution.

    Proof of independence: snapshot.wide_spread_display_substitution is intentionally
    NOT set (MagicMock returns a truthy MagicMock for unset attributes, but compute()
    must derive from bid/ask arithmetic, not read this field).
    """
    snapshot = _make_snapshot(Decimal("0.44"), Decimal("0.56"))
    history: list[dict] = []
    vnext = MarketAnalysisVNext(snapshot=snapshot, history=history)
    metrics = vnext.compute()

    assert metrics.wide_spread_display_substitution is True, (
        "INV-vnext-substitution-consistency: spread=0.12 >= threshold=0.10 must yield True; "
        f"got {metrics.wide_spread_display_substitution!r}"
    )


def test_inv_vnext_substitution_consistency_narrow_spread() -> None:
    """INV-vnext-substitution-consistency (narrow spread):

    bid=0.49, ask=0.52, spread=0.03 < WIDE_SPREAD_THRESHOLD_USD=0.10.
    MarketAnalysisVNext.compute() must derive wide_spread=False independently.
    """
    snapshot = _make_snapshot(Decimal("0.49"), Decimal("0.52"))
    history: list[dict] = []
    vnext = MarketAnalysisVNext(snapshot=snapshot, history=history)
    metrics = vnext.compute()

    assert metrics.wide_spread_display_substitution is False, (
        "INV-vnext-substitution-consistency: spread=0.03 < threshold=0.10 must yield False; "
        f"got {metrics.wide_spread_display_substitution!r}"
    )


def test_inv_vnext_substitution_consistency_one_sided_book() -> None:
    """INV-vnext-substitution-consistency (one-sided book / None bid):

    When bid or ask is None, spread is None → wide_spread=False.
    Matches legacy formula: bool(None is not None and ...) == False.
    """
    snapshot = _make_snapshot(None, Decimal("0.56"))
    history: list[dict] = []
    vnext = MarketAnalysisVNext(snapshot=snapshot, history=history)
    metrics = vnext.compute()

    assert metrics.wide_spread_display_substitution is False, (
        "INV-vnext-substitution-consistency: one-sided book (bid=None) must yield False; "
        f"got {metrics.wide_spread_display_substitution!r}"
    )


def test_inv_vnext_substitution_consistency_at_threshold() -> None:
    """INV-vnext-substitution-consistency (exactly at threshold):

    bid=0.40, ask=0.50, spread=0.10 == WIDE_SPREAD_THRESHOLD_USD.
    Legacy formula: spread >= threshold → True (boundary inclusive).
    """
    snapshot = _make_snapshot(Decimal("0.40"), Decimal("0.50"))
    history: list[dict] = []
    vnext = MarketAnalysisVNext(snapshot=snapshot, history=history)
    metrics = vnext.compute()

    assert metrics.wide_spread_display_substitution is True, (
        "INV-vnext-substitution-consistency: spread=0.10 == threshold=0.10 must yield True (inclusive); "
        f"got {metrics.wide_spread_display_substitution!r}"
    )
