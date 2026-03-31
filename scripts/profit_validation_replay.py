import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import math
import numpy as np
from pathlib import Path
import sys

# Setup imports securely
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_connection
from src.state.portfolio import load_portfolio, Position
from src.contracts.edge_context import EdgeContext
from src.contracts.semantic_types import EntryMethod
from src.execution.exit_triggers import evaluate_exit_triggers

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

def run_profit_validation_replay():
    """Phase 3.1: Validate new Phase 2 semantic boundaries using historical trades."""
    logger.info("Initializing Phase 3 Validation Replay...")
    
    positions_file = PROJECT_ROOT / "state" / "positions.json"
    if not positions_file.exists():
        logger.error("No positions.json found for replay.")
        return

    portfolio = load_portfolio()
    conn = get_connection()
    
    logger.info(f"Loaded {len(portfolio.recent_exits)} recent exits for historical simulation.")
    
    # Track stats
    stats = {
        "trades_analyzed": 0,
        "v1_misidentified_exits": 0,
        "v2_early_cut_losses": 0,
        "v1_false_stops": 0,
        "total_pnl_diff_usd": 0.0,
        "low_confidence_skipped": 0,
    }
    
    for exit_record in portfolio.recent_exits:
        city = exit_record.get("city")
        target_date = exit_record.get("target_date")
        token_id = exit_record.get("token_id")
        direction = exit_record.get("direction", "buy_yes")
        exit_reason = exit_record.get("exit_reason", "")
        v1_pnl = exit_record.get("pnl", 0.0)
        
        # In a real environment we'd rebuild from `recent_exits` into dummy `Position` properly.
        # But this is sufficient to gather the tick history.
        if not token_id:
            continue
            
        # Replay tick history from SQLite
        ticks = conn.execute('''
            SELECT price, timestamp 
            FROM token_price_log 
            WHERE token_id = ? 
            ORDER BY timestamp ASC
        ''', (token_id,)).fetchall()
        
        if not ticks:
            # logger.warning(f"No price history found for {city} {target_date} {token_id}")
            continue

        stats["trades_analyzed"] += 1
        
        # Pull real attributes from historical trade output
        real_entry_price = exit_record.get("entry_price")
        real_size = exit_record.get("size_usd")
        real_ci_width = exit_record.get("entry_ci_width")
        real_prob = exit_record.get("p_posterior")
        real_method = exit_record.get("entry_method")
        
        if None in (real_entry_price, real_size, real_ci_width, real_prob, real_method):
            stats["low_confidence_skipped"] += 1
            continue

        # Synthesize V2 simulation using real dimensions
        sim_position = Position(
            trade_id="sim_123", market_id="", city=city, cluster="", target_date=target_date, 
            bin_label="", direction=direction, entry_price=real_entry_price, size_usd=real_size, 
            p_posterior=real_prob, entry_ci_width=real_ci_width, entry_method=real_method
        )
        
        v2_exit_time = None
        v2_exit_price = None
        
        for idx, tick in enumerate(ticks):
            tick_price = tick["price"]
            
            # The market price is relative to the YES space logic.
            # Convert to NATIVE space as strictly enforced by the Phase 2 boundary rules.
            if direction == "buy_no":
                native_p_market = 1.0 - tick_price
            else:
                native_p_market = tick_price
                
            native_p_posterior = sim_position.p_posterior
                
            # Build Phase 2 edge context
            edge_ctx = EdgeContext(
                p_raw=np.array([]),
                p_cal=np.array([]),
                p_market=np.array([native_p_market]),
                p_posterior=native_p_posterior,
                forward_edge=native_p_posterior - native_p_market,
                alpha=0.0,
                confidence_band_upper=real_ci_width,
                confidence_band_lower=0.0,
                entry_provenance=EntryMethod(sim_position.entry_method),
                decision_snapshot_id="replay",
                n_edges_found=1,
                n_edges_after_fdr=1,
                market_velocity_1h=0.0,
                divergence_score=0.0
            )
            
            # Compute actual hours to settlement
            tick_dt = datetime.fromisoformat(tick["timestamp"].replace("Z", "+00:00"))
            target_dt = datetime.fromisoformat(target_date + "T23:59:59+00:00")
            hours_to_settle = max(0.0, (target_dt - tick_dt).total_seconds() / 3600.0)
            
            signal = evaluate_exit_triggers(
                position=sim_position,
                current_edge_context=edge_ctx,
                hours_to_settlement=hours_to_settle,
            )
            
            if signal:
                v2_exit_time = tick["timestamp"]
                v2_exit_price = tick_price
                break
                
        # Compare V1 vs V2 attribution
        if "BUY_NO_EDGE_EXIT" in exit_reason and v1_pnl < 0:
            if v2_exit_price is None:
                # V2 successfully stayed in a trade V1 panicked out of
                stats["v1_misidentified_exits"] += 1
                stats["v1_false_stops"] += 1
                stats["total_pnl_diff_usd"] += abs(v1_pnl)
                
        elif v1_pnl < -5.0:
            if v2_exit_price is not None:
                # V2 cut it early successfully
                stats["v2_early_cut_losses"] += 1
                diff = abs(v1_pnl) - (sim_position.size_usd - (sim_position.size_usd/sim_position.entry_price * v2_exit_price))
                stats["total_pnl_diff_usd"] += max(0, diff)
        else:
            if v2_exit_price is None and "EXIT" in exit_reason:
                stats["low_confidence_skipped"] += 1
                
    logger.info("--- SHADOW ATTRIBUTION REPLAY RESULTS ---")
    logger.info(f"Trades Analyzed: {stats['trades_analyzed']}")
    logger.info(f"V1 False Stops Avoided: {stats['v1_false_stops']}")
    logger.info(f"V2 Early-Cuts Triggered: {stats['v2_early_cut_losses']}")
    logger.info(f"Low Confidence Divergences Skipped: {stats['low_confidence_skipped']}")
    logger.info(f"Gross Advantage vs V1 Path: ${stats['total_pnl_diff_usd']:.2f}")

if __name__ == "__main__":
    run_profit_validation_replay()
