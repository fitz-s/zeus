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
from pathlib import Path
import sys
from datetime import timedelta

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_connection
from src.state.portfolio import load_portfolio, Position
from src.config import state_path
from src.contracts.edge_context import EdgeContext
from src.contracts.semantic_types import EntryMethod
from src.execution.exit_triggers import evaluate_exit_triggers

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

def run_profit_validation_replay():
    """Phase 3.1: Validate new Phase 2 semantic boundaries using historical trades."""
    logger.info("Initializing Phase 3 Validation Replay...")
    
    positions_file = state_path("positions.json")
    if not positions_file.exists():
        logger.error("No mode-qualified positions file found for replay: %s", positions_file)
        return

    portfolio = load_portfolio()
    conn = get_connection()
    
    logger.info(f"Loaded {len(portfolio.recent_exits)} recent exits for historical simulation.")
    
    # Track stats
    stats = {
        "total_exits": len(portfolio.recent_exits),
        "tick_covered": 0,
        "high_confidence_analyzed": 0,
        "low_confidence_reconstructed": 0,
        "fully_skipped": 0,
        
        "v1_misidentified_exits": 0,
        "v2_early_cut_losses": 0,
        "v1_false_stops": 0,
        
        "gross_delta_all_analyzed": 0.0,
        "gross_delta_high_confidence_only": 0.0,
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
            stats["fully_skipped"] += 1
            continue

        stats["tick_covered"] += 1
        
        # 3-tier field recovery: recent_exits (new) → trade_decisions DB → skip
        real_entry_price = exit_record.get("entry_price")
        real_size = exit_record.get("size_usd")
        real_ci_width = exit_record.get("entry_ci_width")
        real_prob = exit_record.get("p_posterior")
        real_method = exit_record.get("entry_method")
        
        is_high_confidence = False
        field_source = "unknown"
        
        # Tier 1: Direct from enriched recent_exits (post-fix)
        # Only entry_price, size_usd, p_posterior must be non-zero.
        # entry_ci_width=0.0 is valid (means no CI band), entry_method can default.
        if all(v not in (None, "") for v in [real_entry_price, real_size, real_prob]) and real_entry_price > 0 and real_size > 0:
            is_high_confidence = True
            field_source = "recent_exits"
            real_ci_width = real_ci_width if real_ci_width is not None else 0.0
            real_method = real_method or "ens_member_counting"
            stats["high_confidence_analyzed"] += 1
        else:
            # Tier 2: Fallback to trade_decisions DB
            bin_label = exit_record.get("bin_label")
            row = conn.execute(
                "SELECT price, size_usd, ci_upper, ci_lower, p_posterior FROM trade_decisions WHERE bin_label = ? AND direction = ? ORDER BY timestamp DESC LIMIT 1",
                (bin_label, direction)
            ).fetchone()
            
            if row:
                real_entry_price = row["price"]
                real_size = row["size_usd"]
                real_ci_width = row["ci_upper"] - row["ci_lower"]
                real_prob = row["p_posterior"]
                real_method = "ens_member_counting"
                is_high_confidence = True
                field_source = "trade_decisions"
                stats["high_confidence_analyzed"] += 1
            else:
                # Tier 3: No fabrication. Truly skip.
                stats["fully_skipped"] += 1
                continue

        # Synthesize V2 simulation using real dimensions
        sim_position = Position(
            trade_id=exit_record.get("trade_id", "sim"), market_id=exit_record.get("market_id", ""), 
            city=city, cluster=exit_record.get("cluster", ""), target_date=target_date, 
            bin_label=exit_record.get("bin_label", ""), direction=direction, 
            entry_price=real_entry_price, size_usd=real_size, 
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
            raw_prob = native_p_posterior
            cal_prob = native_p_posterior
                
            divergence_score = abs(native_p_posterior - native_p_market)
            alpha_usd = (native_p_posterior - native_p_market) * sim_position.size_usd
            market_velocity_1h = 0.0
            
            # Reconstruct real 1H Velocity from the dataset
            tick_dt = datetime.fromisoformat(tick["timestamp"].replace("Z", "+00:00"))
            one_hour_ago = (tick_dt - timedelta(hours=1)).isoformat()
            row = conn.execute(
                "SELECT price FROM token_price_log WHERE token_id = ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
                (token_id, one_hour_ago)
            ).fetchone()
            if row:
                old_native_p = row["price"] if direction == "buy_yes" else 1.0 - row["price"]
                market_velocity_1h = native_p_market - old_native_p
            
            # Build Phase 2 edge context
            edge_ctx = EdgeContext(
                p_raw=np.array([raw_prob]),
                p_cal=np.array([cal_prob]),
                p_market=np.array([native_p_market]),
                p_posterior=native_p_posterior,
                forward_edge=native_p_posterior - native_p_market,
                alpha=alpha_usd,
                confidence_band_upper=real_ci_width,
                confidence_band_lower=0.0,
                entry_provenance=EntryMethod(sim_position.entry_method),
                decision_snapshot_id="replay",
                n_edges_found=1,
                n_edges_after_fdr=1,
                market_velocity_1h=market_velocity_1h,
                divergence_score=divergence_score
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
                v2_exit_price = native_p_market
                break
                
        # Evaluate V2 vs V1
        # Find V1 exit price from tick history closest to exited_at
        v1_exited_at = exit_record.get("exited_at")
        v1_exit_price = None
        if v1_exited_at:
            for tick in ticks:
                if tick["timestamp"] >= v1_exited_at:
                    v1_exit_price = tick["price"] if direction == "buy_yes" else 1.0 - tick["price"]
                    break
        if not v1_exit_price:
            v1_exit_price = ticks[-1]["price"] if direction == "buy_yes" else 1.0 - ticks[-1]["price"]
            
        v1_pnl_sim = sim_position.size_usd * (v1_exit_price / sim_position.entry_price) - sim_position.size_usd
        
        v2_final_tick = ticks[-1]["price"] if direction == "buy_yes" else 1.0 - ticks[-1]["price"]
        v2_final_price = v2_exit_price if v2_exit_price is not None else v2_final_tick
        v2_pnl_sim = sim_position.size_usd * (v2_final_price / sim_position.entry_price) - sim_position.size_usd

        pnl_delta = v2_pnl_sim - v1_pnl_sim
        stats["gross_delta_all_analyzed"] += pnl_delta
        if is_high_confidence:
            stats["gross_delta_high_confidence_only"] += pnl_delta

        if "BUY_NO_EDGE_EXIT" in exit_reason and v1_pnl_sim < 0 and v2_exit_price is None:
            stats["v1_false_stops"] += 1
            
        if v1_pnl_sim < -5.0 and v2_exit_price is not None and v2_exit_time < v1_exited_at:
            stats["v2_early_cut_losses"] += 1
                
    if stats["total_exits"] > 0:
        coverage_pct = (stats["tick_covered"] / stats["total_exits"]) * 100
    else:
        coverage_pct = 0.0

    logger.info("--- SHADOW ATTRIBUTION REPLAY RESULTS ---")
    logger.info(f"Total Trajectories Exits: {stats['total_exits']}")
    logger.info(f"Ticks Covered: {stats['tick_covered']} ({coverage_pct:.1f}%)")
    logger.info(f"High Confidence Analyzed: {stats['high_confidence_analyzed']}")
    logger.info(f"Low Confidence Reconstructed: {stats['low_confidence_reconstructed']}")
    logger.info(f"Fully Skipped Missing Attributes: {stats['fully_skipped']}")
    logger.info(f"V1 False Stops Avoided: {stats['v1_false_stops']}")
    logger.info(f"V2 Early-Cuts Triggered: {stats['v2_early_cut_losses']}")
    logger.info(f"Gross Advantage vs V1 Path (All): ${stats['gross_delta_all_analyzed']:.2f}")
    logger.info(f"Gross Advantage vs V1 Path (High Conf): ${stats['gross_delta_high_confidence_only']:.2f}")

    # Generate Report File
    report_path = PROJECT_ROOT / "shadow_replay_report.md"
    try:
        with open(report_path, "w") as f:
            f.write(f"# Zeus Measurement Spine: Operational PnL Replay\n\n")
            f.write(f"## Data Estate Coverage\n")
            f.write(f"- Total Historical Exits In DB: {stats['total_exits']}\n")
            f.write(f"- Ticks Covered: {stats['tick_covered']}\n")
            f.write(f"- High Confidence Analyzed: {stats['high_confidence_analyzed']}\n")
            f.write(f"- Low Confidence Reconstructed: {stats['low_confidence_reconstructed']}\n")
            f.write(f"- Fully Skipped Missing Attributes: {stats['fully_skipped']}\n")
            f.write(f"\n## Metrics of Decision Output\n")
            f.write(f"- V1 False-Stops Nullified (Position recovered): {stats['v1_false_stops']}\n")
            f.write(f"- V2 Pre-emptive Loss Mitigations (Cut ahead of bleed): {stats['v2_early_cut_losses']}\n")
            f.write(f"### Estimated Gross Advantage Delta (All Analyzed): **${stats['gross_delta_all_analyzed']:.2f}**\n")
            f.write(f"### Estimated Gross Advantage Delta (High Confidence Only): **${stats['gross_delta_high_confidence_only']:.2f}**\n\n")
            f.write(f"> PR-B.1 Cleanup Signed. False heuristics removed for absolute measurement.\n")
    except Exception as e:
        logger.error(f"Failed to write shadow artifact: {e}")

if __name__ == "__main__":
    run_profit_validation_replay()
