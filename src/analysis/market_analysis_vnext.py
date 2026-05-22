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

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
    from src.strategy.candidates import FamilyOrderBookSnapshot

# T4_MERGE_DATE: placeholder. Production pass overwrites with the merge commit
# ISO timestamp extracted from:
#   git log --format=%cI -1 origin/main
# after PR-T4 is merged. Antibody INV-anchor-source-real-value queries
# settlement_commands WHERE requested_at >= T4_MERGE_DATE.
T4_MERGE_DATE: str = "2026-05-21T07:46:25+00:00"


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


@dataclass(frozen=True)
class PassiveMakerExecutionEstimate:
    """Fill-probability estimate for a live passive-maker entry."""

    expected_fill_probability: Decimal
    queue_depth_ahead: Decimal | None
    adverse_selection_score: Decimal
    evidence_order_count: int
    evidence_fill_count: int
    evidence_source: str = "venue_command_trade_history"


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
        family_book_snapshot: "Optional[FamilyOrderBookSnapshot]" = None,
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
            family_book_snapshot: Optional caller-injected neg-risk family order-book
                     snapshot. When present, neg_risk_basket.evaluate() reads per-leg
                     depth for exact sweep-cost / profit calculation (§11.5-11.8).
        """
        self._snapshot = snapshot
        self._history = history
        self._polymarket_end_anchor_source = polymarket_end_anchor_source
        self._bin_grid_id = bin_grid_id
        self._bin_schema_version = bin_schema_version
        self._family_book_snapshot = family_book_snapshot

    @property
    def family_book_snapshot(self) -> "Optional[FamilyOrderBookSnapshot]":
        """Caller-injected neg-risk family order-book snapshot (may be None)."""
        return self._family_book_snapshot

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


def _table_exists(conn: Any, table_name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _queue_depth_ahead(snapshot: "ExecutableMarketSnapshotV2", quote_price: Decimal) -> Decimal | None:
    try:
        orderbook = json.loads(snapshot.orderbook_depth_jsonb)
    except Exception:
        return None
    bids = orderbook.get("bids") if isinstance(orderbook, dict) else None
    if not isinstance(bids, list) or not bids:
        return None
    depth = Decimal("0")
    for row in bids:
        if not isinstance(row, dict):
            continue
        price = _decimal_or_none(row.get("price"))
        size = _decimal_or_none(row.get("size"))
        if price is None or size is None:
            continue
        if price >= quote_price:
            depth += size
    return depth


def estimate_passive_maker_execution(
    conn: Any,
    snapshot: "ExecutableMarketSnapshotV2",
    *,
    quote_price: Decimal,
) -> PassiveMakerExecutionEstimate | None:
    """Estimate passive-maker fill context from local command/trade facts.

    No history means no model. The live path must then reject passive submit
    rather than treating a resting quote as executable edge.
    """

    if conn is None or not _table_exists(conn, "venue_commands"):
        return None
    token_id = str(snapshot.selected_outcome_token_id or "")
    if not token_id:
        return None
    has_trades = _table_exists(conn, "venue_trade_facts")
    price_window = max(Decimal(str(snapshot.min_tick_size)) * Decimal("2"), Decimal("0.01"))
    query = """
        SELECT
            vc.command_id,
            vc.price,
            vc.state,
            EXISTS (
                SELECT 1 FROM venue_trade_facts vtf
                WHERE vtf.command_id = vc.command_id
                  AND UPPER(COALESCE(vtf.state, '')) IN ('MATCHED','MINED','CONFIRMED')
            ) AS has_fill,
            (
                SELECT vtf.fill_price FROM venue_trade_facts vtf
                WHERE vtf.command_id = vc.command_id
                  AND UPPER(COALESCE(vtf.state, '')) IN ('MATCHED','MINED','CONFIRMED')
                ORDER BY vtf.observed_at DESC
                LIMIT 1
            ) AS fill_price
        FROM venue_commands vc
        WHERE vc.token_id = ?
          AND UPPER(COALESCE(vc.intent_kind, '')) = 'ENTRY'
          AND UPPER(COALESCE(vc.side, '')) = 'BUY'
          AND ABS(CAST(vc.price AS REAL) - CAST(? AS REAL)) <= CAST(? AS REAL)
        ORDER BY vc.created_at DESC
        LIMIT 50
    """ if has_trades else """
        SELECT
            vc.command_id,
            vc.price,
            vc.state,
            CASE WHEN UPPER(COALESCE(vc.state, '')) IN ('MATCHED','FILLED','PARTIAL','PARTIALLY_MATCHED')
                 THEN 1 ELSE 0 END AS has_fill,
            vc.price AS fill_price
        FROM venue_commands vc
        WHERE vc.token_id = ?
          AND UPPER(COALESCE(vc.intent_kind, '')) = 'ENTRY'
          AND UPPER(COALESCE(vc.side, '')) = 'BUY'
          AND ABS(CAST(vc.price AS REAL) - CAST(? AS REAL)) <= CAST(? AS REAL)
        ORDER BY vc.created_at DESC
        LIMIT 50
    """
    try:
        rows = conn.execute(query, (token_id, str(quote_price), str(price_window))).fetchall()
    except Exception:
        return None
    if not rows:
        return None
    evidence_order_count = len(rows)
    evidence_fill_count = sum(1 for row in rows if int(row[3] or 0) == 1)
    expected_fill_probability = Decimal(evidence_fill_count + 1) / Decimal(evidence_order_count + 2)

    adverse_scores: list[Decimal] = []
    for row in rows:
        if int(row[3] or 0) != 1:
            continue
        fill_price = _decimal_or_none(row[4])
        if fill_price is None or quote_price <= 0:
            continue
        adverse_scores.append(max(Decimal("0"), (fill_price - quote_price) / quote_price))
    adverse_selection_score = (
        sum(adverse_scores, Decimal("0")) / Decimal(len(adverse_scores))
        if adverse_scores
        else Decimal("0")
    )
    return PassiveMakerExecutionEstimate(
        expected_fill_probability=expected_fill_probability,
        queue_depth_ahead=_queue_depth_ahead(snapshot, quote_price),
        adverse_selection_score=min(adverse_selection_score, Decimal("1")),
        evidence_order_count=evidence_order_count,
        evidence_fill_count=evidence_fill_count,
    )
