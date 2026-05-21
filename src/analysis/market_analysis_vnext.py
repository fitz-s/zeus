# Created: 2026-05-20
# Last reused or audited: 2026-05-21
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §7 (sha 00c2399742)
"""MarketAnalysisVNext — production.

Phase 2 T4 production surface:
  - MarketAnalysisVNext(snapshot, history) -> MicrostructureMetrics
  - T4_MERGE_DATE constant (updated at merge time: git log --format=%cI -1 origin/main)
  - spread_observed_window_ms field plumbed from ExecutableMarketSnapshotV2
    (deferred from PR 2 path-a; verify defer comment at snapshot_repo.py:78)

Storage: market_microstructure_snapshots table on forecasts DB
Schema: SCHEMA_FORECASTS_VERSION 5 (production pass — 2026-05-21)
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2

# T4_MERGE_DATE: placeholder. Production pass overwrites with the merge commit
# ISO timestamp extracted from:
#   git log --format=%cI -1 origin/main
# after PR-T4 is merged. Antibody INV-anchor-source-real-value queries
# settlement_commands WHERE requested_at >= T4_MERGE_DATE.
T4_MERGE_DATE: str = "2026-05-XX"


@dataclass(frozen=True)
class MicrostructureMetrics:
    """Computed microstructure observables for a single market snapshot.

    All fields correspond to market_microstructure_snapshots columns
    (forecasts DB, SCHEMA_FORECASTS_VERSION 5).
    """

    # Snapshot identity
    snapshot_id: str
    event_slug: str
    condition_id: str
    captured_at_iso: str  # ISO-8601 UTC

    # Spread / substitution — independently recomputed from orderbook inputs
    wide_spread_display_substitution: bool
    spread_observed_window_ms: Optional[int]  # None until windowed observer ships

    # Orderbook depth
    depth_at_best_ask: int

    # Market end anchor source (INV-anchor-source-real-value target)
    # Derived by market_end_anchor_source(market) at caller site; passed in.
    polymarket_end_anchor_source: str

    # bin_grid_id propagated from ensemble_snapshots_v2 (F4 retrofit)
    bin_grid_id: Optional[str] = None
    bin_schema_version: Optional[str] = None


class MarketAnalysisVNext:
    """Microstructure analytics for a single captured market snapshot.

    Substitution consistency invariant (INV-vnext-substitution-consistency):
      wide_spread_display_substitution must match legacy market_scanner logic:
        bool(_spread_usd is not None and _spread_usd >= WIDE_SPREAD_THRESHOLD_USD)
      Antibody: tests/test_inv_vnext_substitution_consistency.py
    """

    def __init__(
        self,
        snapshot: "ExecutableMarketSnapshotV2",
        history: list[dict],
        *,
        polymarket_end_anchor_source: str = "",
        bin_grid_id: Optional[str] = None,
        bin_schema_version: Optional[str] = None,
    ) -> None:
        """
        Args:
            snapshot: ExecutableMarketSnapshotV2 captured before order submission.
            history: Recent market_price_history rows (world DB) for this market.
                     Used for windowed spread observation (spread_observed_window_ms).
            polymarket_end_anchor_source: Derived by caller via market_end_anchor_source(market).
                     'gamma_explicit' | 'f1_12z_fallback'.
            bin_grid_id: From ensemble_snapshots_v2.bin_grid_id for this snapshot's
                     triggering cycle (F4 retrofit).
            bin_schema_version: Companion to bin_grid_id.
        """
        self._snapshot = snapshot
        self._history = history
        self._polymarket_end_anchor_source = polymarket_end_anchor_source
        self._bin_grid_id = bin_grid_id
        self._bin_schema_version = bin_schema_version

    def compute(self) -> MicrostructureMetrics:
        """Compute MicrostructureMetrics for this snapshot.

        wide_spread_display_substitution is independently recomputed from
        orderbook_top_bid / orderbook_top_ask (NOT re-read from snapshot field)
        to verify the legacy market_scanner logic is consistent.

        Spread threshold: WIDE_SPREAD_THRESHOLD_USD = 0.10 USD.
        """
        from src.contracts.executable_market_snapshot_v2 import WIDE_SPREAD_THRESHOLD_USD

        snapshot = self._snapshot
        bid = snapshot.orderbook_top_bid
        ask = snapshot.orderbook_top_ask

        # Independent recompute — must match legacy market_scanner formula exactly:
        #   bool(_spread_usd is not None and _spread_usd >= WIDE_SPREAD_THRESHOLD_USD)
        if bid is not None and ask is not None:
            spread_usd: Optional[Decimal] = ask - bid
        else:
            spread_usd = None
        wide_spread = bool(spread_usd is not None and spread_usd >= WIDE_SPREAD_THRESHOLD_USD)

        # spread_observed_window_ms: deferred until windowed observer is implemented.
        spread_observed_window_ms: Optional[int] = None

        return MicrostructureMetrics(
            snapshot_id=snapshot.snapshot_id,
            event_slug=snapshot.event_slug,
            condition_id=snapshot.condition_id,
            captured_at_iso=snapshot.captured_at.isoformat(),
            wide_spread_display_substitution=wide_spread,
            spread_observed_window_ms=spread_observed_window_ms,
            depth_at_best_ask=snapshot.depth_at_best_ask,
            polymarket_end_anchor_source=self._polymarket_end_anchor_source,
            bin_grid_id=self._bin_grid_id,
            bin_schema_version=self._bin_schema_version,
        )
