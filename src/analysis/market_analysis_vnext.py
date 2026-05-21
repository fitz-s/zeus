# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §7 (sha 00c2399742)
"""MarketAnalysisVNext — SCAFFOLD only.

Phase 2 T4 production surface:
  - MarketAnalysisVNext(snapshot, history) -> MicrostructureMetrics
  - T4_MERGE_DATE constant (updated at merge time: git log --format=%cI -1 origin/main)
  - spread_observed_window_ms field plumbed from ExecutableMarketSnapshotV2
    (deferred from PR 2 path-a; verify defer comment at snapshot_repo.py:78)

Storage: market_microstructure_snapshots table on forecasts DB
Schema: SCHEMA_FORECASTS_VERSION 4 → 5 (production pass only; not in SCAFFOLD)

SCAFFOLD STATUS: NO production bodies. All compute() raises NotImplementedError.
Production bodies land in T4 production pass after critic approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2

# T4_MERGE_DATE: placeholder. Production pass overwrites with the merge commit
# ISO timestamp extracted from:
#   git log --format=%cI -1 origin/main
# after PR-T4 is merged. Antibody INV-anchor-source-real-value queries
# decision_events WHERE decision_time >= T4_MERGE_DATE.
T4_MERGE_DATE: str = "2026-05-XX"


@dataclass(frozen=True)
class MicrostructureMetrics:
    """Computed microstructure observables for a single market snapshot.

    All fields correspond to market_microstructure_snapshots columns
    (forecasts DB, SCHEMA_FORECASTS_VERSION 5 — production pass DDL).

    SCAFFOLD: fields declared; storage DDL deferred to production pass.
    """

    # Snapshot identity
    snapshot_id: str
    event_slug: str
    condition_id: str
    captured_at_iso: str  # ISO-8601 UTC

    # Spread / substitution (plumbed from ExecutableMarketSnapshotV2)
    wide_spread_display_substitution: bool
    spread_observed_window_ms: Optional[int]  # None until production pass populates field

    # Orderbook depth
    depth_at_best_ask: int

    # Market end anchor source (INV-anchor-source-real-value target)
    # Derived by market_end_anchor_source(market) — production wiring in T4 pass.
    polymarket_end_anchor_source: str

    # bin_grid_id propagated from ensemble_snapshots_v2 (F4 retrofit — production pass)
    bin_grid_id: Optional[str] = None
    bin_schema_version: Optional[str] = None


class MarketAnalysisVNext:
    """Microstructure analytics for a single captured market snapshot.

    SCAFFOLD: constructor and compute() declared; production bodies raise
    NotImplementedError until T4 production pass.

    Wiring plan (production pass):
      1. Receive ExecutableMarketSnapshotV2 + market dict from caller.
      2. Call market_end_anchor_source(market) → polymarket_end_anchor_source.
      3. Propagate bin_grid_id from ensemble_snapshots_v2 row at caller site
         (NOT from cycle_runtime.bins — no propagation path per Phase 1 T2 finding).
      4. Write MicrostructureMetrics row to market_microstructure_snapshots
         (forecasts DB) via caller-provided conn (INV-37: caller-provided conn only).
      5. Replace static _context_text(context.get("polymarket_end_anchor_source"))
         in execution_intent.py:673 with market_end_anchor_source(market) call.

    Substitution consistency invariant (INV-vnext-substitution-consistency):
      wide_spread_display_substitution must match legacy market_scanner logic:
        bool(_spread_usd is not None and _spread_usd >= WIDE_SPREAD_THRESHOLD_USD)
      Antibody: tests/test_inv_vnext_substitution_consistency.py
    """

    def __init__(
        self,
        snapshot: "ExecutableMarketSnapshotV2",
        history: list[dict],
    ) -> None:
        """
        Args:
            snapshot: ExecutableMarketSnapshotV2 captured before order submission.
            history: Recent market_price_history rows (world DB) for this market.
                     Used for windowed spread observation (spread_observed_window_ms).
        """
        # SCAFFOLD: store inputs; production pass adds validation + derived fields.
        self._snapshot = snapshot
        self._history = history

    def compute(self) -> MicrostructureMetrics:
        """Compute MicrostructureMetrics for this snapshot.

        SCAFFOLD: raises NotImplementedError.
        Production body: derive spread_observed_window_ms from history,
        call market_end_anchor_source, propagate bin_grid_id, return metrics.
        """
        raise NotImplementedError(
            "MarketAnalysisVNext.compute() is a SCAFFOLD stub. "
            "Production bodies land in T4 production pass."
        )
