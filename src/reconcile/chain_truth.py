# Created: 2026-07-08
# Last reused or audited: 2026-07-08
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R2-a
#                  docs/rebuild/whole_system_first_principles_2026-07-07.md §2.4
"""ONE canonical chain/venue-truth snapshot: what the outside world says
happened, independent of what Zeus's own bookkeeping (local_truth) claims.

Three components, one snapshot:
    1. On-chain positions      -- ChainPositionFact, reused from
       src.state.chain_mirror_reconciler (that module is already the
       target-form kernel for this concept -- see its docstring's Public
       surface list -- so this contract imports it rather than minting a
       fourth copy of the same dataclass).
    2. Settlement resolutions  -- SettlementFact, same reuse rationale.
    3. Venue order/trade fact  -- venue_order_facts (latest point-in-time
       stream                     order state per command) + venue_trade_facts
                                   (deduped fill evidence per command), using
                                   src.state.fill_dedup.canonical_trade_fact_cte
                                   as the ONLY dedup primitive -- never copied
                                   (that CTE already has three independent
                                   copies pre-R2: src.execution.command_recovery.
                                   _canonical_trade_fact_cte,
                                   src.execution.exchange_reconcile.
                                   _canonical_trade_fact_cte, and an inlined
                                   copy in src.state.venue_command_repo's
                                   _reconstruct_trade -- see
                                   src/reconcile/local_truth.py's module
                                   docstring for the full file:line survey).

Local truth vs chain truth: local_truth is Zeus's own derived belief
(venue_commands state machine + position_current projection + reservation
lifecycle); chain_truth is independent ground evidence Zeus observed FROM the
venue/chain (order facts, trade facts, on-chain balances, market
resolutions). The diff engine (src/reconcile/diff_engine.py) exists
precisely to find where the two diverge.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from src.reconcile._order_fact_queries import (
    load_latest_order_facts_by_command,
    load_latest_rest_order_facts_by_command,
)
from src.state.chain_mirror_reconciler import (
    ChainPositionFact,
    SettlementFact,
    load_chain_positions_by_asset,
    load_settlement_lookup,
)
from src.state.fill_dedup import canonical_trade_fact_cte

__all__ = [
    "ChainPositionFact",
    "SettlementFact",
    "ChainCommandFacts",
    "ChainTruthSnapshot",
    "load_chain_truth_snapshot",
    # Re-exported for caller convenience (see load_chain_truth_snapshot's
    # docstring: chain_by_asset must already be fetched by the caller).
    "load_chain_positions_by_asset",
]

# HEARTBEAT_CANCEL_SUSPECTED (registered venue_order_facts.state, see
# src/state/db.py CREATE TABLE venue_order_facts) exists precisely because
# the venue's WebSocket order stream can go quiet without an explicit
# cancel/fill terminal event -- see diff_engine's ws_unreliable predicate.
_HEARTBEAT_CANCEL_SUSPECTED = "HEARTBEAT_CANCEL_SUSPECTED"
_REST_POINT_TRUTH_SOURCES = ("REST", "DATA_API")


@dataclass(frozen=True)
class ChainCommandFacts:
    """Deduped venue order/trade fact view for a single command_id.

    ``canonical_filled_size``/``canonical_fill_price`` are derived EXCLUSIVELY
    via ``canonical_trade_fact_cte`` -- never a bare SUM(filled_size), which
    over-counts 1x-4x on the same real fill re-observed across lifecycle
    revisions (see src/state/fill_dedup.py docstring).
    """

    command_id: str
    canonical_filled_size: float = 0.0
    canonical_fill_price: Optional[float] = None
    fill_states: tuple[str, ...] = ()
    trade_ids: tuple[str, ...] = ()
    latest_fill_observed_at: Optional[str] = None
    latest_order_state: Optional[str] = None
    latest_order_source: Optional[str] = None
    latest_order_observed_at: Optional[str] = None
    latest_order_local_sequence: Optional[int] = None
    latest_rest_order_state: Optional[str] = None
    latest_rest_observed_at: Optional[str] = None

    @property
    def has_fills(self) -> bool:
        return self.canonical_filled_size > 0.0

    @property
    def heartbeat_cancel_suspected(self) -> bool:
        return self.latest_order_state == _HEARTBEAT_CANCEL_SUSPECTED

    @property
    def ws_state_stale_vs_rest(self) -> bool:
        """True iff the most recent WS-sourced order state disagrees with a
        STRICTLY LATER REST/DATA_API point-in-time observation -- the venue's
        WS stream is not authoritative once a fresher REST read exists.
        """
        if self.latest_rest_order_state is None or self.latest_rest_observed_at is None:
            return False
        if self.latest_order_source in _REST_POINT_TRUTH_SOURCES:
            return False
        if self.latest_order_observed_at is None:
            return False
        return self.latest_rest_observed_at > self.latest_order_observed_at


@dataclass
class ChainTruthSnapshot:
    generated_at: str
    positions_by_asset: dict[str, ChainPositionFact] = field(default_factory=dict)
    settlement_by_key: dict[tuple, SettlementFact] = field(default_factory=dict)
    commands: dict[str, ChainCommandFacts] = field(default_factory=dict)

    def command_facts(self, command_id: str) -> ChainCommandFacts:
        existing = self.commands.get(command_id)
        if existing is not None:
            return existing
        return ChainCommandFacts(command_id=command_id)

    def position_fact_for_token(self, token_id: str) -> Optional[ChainPositionFact]:
        if not token_id:
            return None
        return self.positions_by_asset.get(token_id)


def _load_canonical_fill_aggregate_sql() -> str:
    return (
        "WITH " + canonical_trade_fact_cte() + """
        SELECT command_id,
               SUM(CAST(filled_size AS REAL)) AS canonical_filled_size,
               SUM(CAST(filled_size AS REAL) * CAST(fill_price AS REAL))
                   / NULLIF(SUM(CAST(filled_size AS REAL)), 0) AS canonical_fill_price,
               GROUP_CONCAT(DISTINCT state) AS fill_states,
               GROUP_CONCAT(DISTINCT trade_id) AS trade_ids,
               MAX(observed_at) AS latest_fill_observed_at
          FROM canonical_trade_fact
         GROUP BY command_id
        """
    )


def load_chain_truth_snapshot(
    conn_trades: sqlite3.Connection,
    conn_forecasts: Optional[sqlite3.Connection],
    chain_by_asset: dict[str, ChainPositionFact],
    *,
    now: Optional[datetime] = None,
) -> ChainTruthSnapshot:
    """Load the full chain-truth snapshot.

    ``chain_by_asset`` is already-fetched on-chain position data (see
    src.state.chain_mirror_reconciler.load_chain_positions_by_asset) -- this
    function performs no network I/O itself, mirroring chain_mirror_reconciler's
    design (the CLI/caller owns adapter construction).
    """

    now = now or datetime.now(timezone.utc)
    settlement_by_key = load_settlement_lookup(conn_forecasts) if conn_forecasts is not None else {}

    commands: dict[str, ChainCommandFacts] = {}

    latest_order = load_latest_order_facts_by_command(conn_trades)
    latest_rest_order = load_latest_rest_order_facts_by_command(conn_trades)
    fill_rows = conn_trades.execute(_load_canonical_fill_aggregate_sql()).fetchall()
    fill_by_command: dict[str, sqlite3.Row] = {str(row["command_id"]): row for row in fill_rows}

    for command_id in set(latest_order) | set(fill_by_command):
        order_row = latest_order.get(command_id)
        rest_row = latest_rest_order.get(command_id)
        fill_row = fill_by_command.get(command_id)
        commands[command_id] = ChainCommandFacts(
            command_id=command_id,
            canonical_filled_size=(
                float(fill_row["canonical_filled_size"]) if fill_row is not None and fill_row["canonical_filled_size"] is not None else 0.0
            ),
            canonical_fill_price=(
                float(fill_row["canonical_fill_price"])
                if fill_row is not None and fill_row["canonical_fill_price"] is not None
                else None
            ),
            fill_states=(
                tuple(str(fill_row["fill_states"]).split(",")) if fill_row is not None and fill_row["fill_states"] else ()
            ),
            trade_ids=(
                tuple(str(fill_row["trade_ids"]).split(",")) if fill_row is not None and fill_row["trade_ids"] else ()
            ),
            latest_fill_observed_at=(
                str(fill_row["latest_fill_observed_at"]) if fill_row is not None and fill_row["latest_fill_observed_at"] else None
            ),
            latest_order_state=(str(order_row["state"]) if order_row is not None else None),
            latest_order_source=(str(order_row["source"]) if order_row is not None else None),
            latest_order_observed_at=(str(order_row["observed_at"]) if order_row is not None else None),
            latest_order_local_sequence=(
                int(order_row["local_sequence"]) if order_row is not None else None
            ),
            latest_rest_order_state=(str(rest_row["state"]) if rest_row is not None else None),
            latest_rest_observed_at=(str(rest_row["observed_at"]) if rest_row is not None else None),
        )

    return ChainTruthSnapshot(
        generated_at=now.isoformat(),
        positions_by_asset=dict(chain_by_asset),
        settlement_by_key=settlement_by_key,
        commands=commands,
    )
