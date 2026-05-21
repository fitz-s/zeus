# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §7.3 (sha 00c2399742)
"""Antibody test: INV-vnext-substitution-consistency

Invariant: when wide_spread_display_substitution=True, both legacy market_scanner
and MarketAnalysisVNext apply substitution identically.

Cross-module relationship test:
  src/data/market_scanner.py (legacy scanner)
  vs src/analysis/market_analysis_vnext.py (MarketAnalysisVNext)

The legacy scanner computes:
  wide_spread_display_substitution = bool(
      _spread_usd is not None and _spread_usd >= WIDE_SPREAD_THRESHOLD_USD
  )
MarketAnalysisVNext.compute() must replicate this logic exactly.

SCAFFOLD status: xfail because MarketAnalysisVNext.compute() raises NotImplementedError.
Production pass wires production body; antibody transitions from XFAIL → PASS.
"""

from __future__ import annotations

import pytest

from src.analysis.market_analysis_vnext import MarketAnalysisVNext


@pytest.mark.xfail(
    strict=True,
    reason="T4 production pending; MarketAnalysisVNext.compute() raises NotImplementedError (SCAFFOLD)",
)
def test_inv_vnext_substitution_consistency() -> None:
    """INV-vnext-substitution-consistency: MarketAnalysisVNext.compute() must
    produce the same wide_spread_display_substitution value as the legacy
    market_scanner logic for an equivalent snapshot.

    SCAFFOLD: fires xfail via NotImplementedError from compute().

    Production assertion (activated in T4 production pass):
      For a snapshot with orderbook_top_bid=0.44, orderbook_top_ask=0.56
      (spread=0.12 >= WIDE_SPREAD_THRESHOLD_USD=0.10):
        - legacy: wide_spread_display_substitution=True
        - vnext.compute().wide_spread_display_substitution must also be True.
      For spread=0.03 (< threshold):
        - both must be False.
    """
    from unittest.mock import MagicMock

    # Minimal mock snapshot with wide spread (0.12 USD > 0.10 threshold)
    snapshot = MagicMock()
    snapshot.wide_spread_display_substitution = True
    snapshot.depth_at_best_ask = 50
    snapshot.snapshot_id = "test-snap-001"
    snapshot.event_slug = "will-it-rain-chicago-2026-05-20"
    snapshot.condition_id = "0xdeadbeef"
    snapshot.captured_at.isoformat.return_value = "2026-05-20T12:00:00+00:00"

    history: list[dict] = []

    vnext = MarketAnalysisVNext(snapshot=snapshot, history=history)
    # This raises NotImplementedError → xfail RED
    metrics = vnext.compute()

    # Production assertion (unreachable in SCAFFOLD):
    assert metrics.wide_spread_display_substitution is True, (
        "INV-vnext-substitution-consistency: MarketAnalysisVNext.compute() returned "
        f"wide_spread_display_substitution={metrics.wide_spread_display_substitution!r} "
        "but legacy scanner would return True for spread >= WIDE_SPREAD_THRESHOLD_USD"
    )
