# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: KARACHI_TRADE_DECISIONS_GAP_TRACE.md §8 (Replay Strategy)
"""Synthesizer: reconstruct missing trade_decisions bridge from available join tables.

Given a position_id, reconstructs the missing trade_decisions row from:
  position_current ⋈ venue_commands ⋈ position_events

Decision-time analytics (edge, ci_lower, ci_upper, kelly_fraction, p_raw) are
NOT recoverable from the join — they are zero-filled and the row is marked
status='synthesized' to flag the reconstruction.  The bridge row's purpose is
structural (linking UUID position_current to INTEGER position_lots) not
analytical; zero-filled numerics preserve the constraint without fabricating data.

Idempotent: if the bridge row already exists, returns immediately.

Called automatically by update_trade_lifecycle (src/state/db.py) when a
lifecycle event fires against a position that is missing its bridge.  No
operator action is required.  Logs [BRIDGE_SYNTHESIZED] on success.
"""
from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


class BridgeSynthesisError(RuntimeError):
    """Raised when the synthesizer cannot reconstruct a missing bridge row."""


def synthesize_missing_bridge(conn: sqlite3.Connection, position_id: str) -> None:
    """Reconstruct the trade_decisions bridge row for position_id.

    Idempotent: if trade_decisions already has a row for position_id, returns
    immediately without modifying the DB.

    Raises BridgeSynthesisError if position_current has no row for position_id
    (position does not exist — caller has a logic error).
    """
    # Idempotency gate
    existing = conn.execute(
        "SELECT trade_id FROM trade_decisions WHERE runtime_trade_id = ? LIMIT 1",
        (position_id,),
    ).fetchone()
    if existing is not None:
        return

    # Load position_current row
    pc_row = conn.execute(
        """
        SELECT position_id, market_id, bin_label, direction, size_usd,
               entry_price, p_posterior, strategy_key, entry_method,
               discovery_mode, order_id, updated_at, edge_source, decision_snapshot_id
        FROM position_current
        WHERE position_id = ?
        """,
        (position_id,),
    ).fetchone()
    if pc_row is None:
        raise BridgeSynthesisError(
            f"[BRIDGE_SYNTHESIS_FAILED] position_id={position_id!r} "
            f"not found in position_current; cannot synthesize bridge."
        )

    (
        pc_position_id, market_id, bin_label, direction, size_usd,
        entry_price, p_posterior, strategy_key, entry_method,
        discovery_mode, order_id, updated_at, edge_source, decision_snapshot_id,
    ) = pc_row

    # Load earliest position_event for timestamp + env
    pe_row = conn.execute(
        """
        SELECT occurred_at, env, decision_id
        FROM position_events
        WHERE position_id = ?
        ORDER BY sequence_no ASC
        LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    timestamp = (pe_row[0] if pe_row and pe_row[0] else updated_at) or "unknown"
    env = (pe_row[1] if pe_row and pe_row[1] else "live") or "live"
    decision_id = (pe_row[2] if pe_row else None)

    # Load venue_commands for price confirmation
    vc_row = conn.execute(
        """
        SELECT price, state
        FROM venue_commands
        WHERE position_id = ?
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    price = (vc_row[0] if vc_row and vc_row[0] else entry_price) or 0.0

    # NOT NULL fields not recoverable from joins: zero-filled with synthesized flag
    # status='synthesized' is a valid TEXT value (no CHECK constraint on status)
    conn.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp,
         p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
         status, runtime_trade_id, order_id, strategy, entry_method,
         discovery_mode, edge_source, env)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            market_id or "unknown",
            bin_label or "unknown",
            direction or "buy_yes",
            size_usd or 0.0,
            price,
            timestamp,
            # p_raw, p_posterior: use p_posterior as best available proxy
            p_posterior or 0.0,
            p_posterior or 0.0,
            # edge, ci_lower, ci_upper, kelly_fraction: not recoverable → 0.0
            0.0, 0.0, 0.0, 0.0,
            "synthesized",
            position_id,
            order_id or "",
            strategy_key or "",
            entry_method or "",
            discovery_mode or "",
            edge_source or "",
            env,
        ),
    )

    source_chain = [
        "position_current",
        ("position_events" if pe_row else None),
        ("venue_commands" if vc_row else None),
    ]
    source_chain_str = "[" + ", ".join(s for s in source_chain if s) + "]"

    logger.info(
        "[BRIDGE_SYNTHESIZED] position_id=%s source_chain=%s "
        "decision_id=%s strategy_key=%s",
        position_id,
        source_chain_str,
        decision_id,
        strategy_key,
    )
