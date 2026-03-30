"""Update Reaction: Mode B discovery cycle. Spec §6.2.

4×/day aligned with ENS model update times (07:00, 09:00, 19:00, 21:00 UTC):
1. Check exit triggers on all held positions
2. Scan non-held markets for new edges (same pipeline as Opening Hunt)
"""

import logging
from datetime import date, datetime, timezone

import numpy as np

from src.calibration.manager import get_calibrator
from src.calibration.platt import calibrate_and_normalize
from src.config import settings
from src.data.ensemble_client import fetch_ensemble, validate_ensemble
from src.data.market_scanner import find_weather_markets
from src.data.polymarket_client import PolymarketClient
from src.execution.executor import execute_order
from src.execution.exit_triggers import evaluate_exit_triggers, clear_reversal_state
from src.riskguard.riskguard import get_current_level
from src.riskguard.risk_level import RiskLevel
from src.signal.ensemble_signal import EnsembleSignal
from src.signal.model_agreement import model_agreement
from src.state.chronicler import log_event
from src.state.db import get_connection
from src.state.portfolio import (
    PortfolioState, load_portfolio, save_portfolio,
    remove_position, portfolio_heat,
)
from src.strategy.market_fusion import compute_alpha, vwmp
from src.types import Bin

logger = logging.getLogger(__name__)


def run_update_reaction() -> dict:
    """Run one Update Reaction cycle.

    Returns: {"exits": int, "entries": int}
    """
    level = get_current_level()
    if level in (RiskLevel.ORANGE, RiskLevel.RED):
        logger.info("Update Reaction skipped: RiskGuard=%s", level.value)
        return {"exits": 0, "entries": 0}

    conn = get_connection()
    portfolio = load_portfolio()

    # Step 1: Check exit triggers on held positions
    exits = _check_exits(conn, portfolio)

    # Step 2: Scan for new entries (unless YELLOW — YELLOW allows exits only)
    entries = 0
    if level == RiskLevel.GREEN:
        # Reuse Opening Hunt pipeline for entry scanning
        from src.execution.opening_hunt import run_opening_hunt
        # Opening Hunt handles its own RiskGuard check
        entries = run_opening_hunt()

    if exits > 0:
        save_portfolio(portfolio)
    conn.close()

    logger.info("Update Reaction: %d exits, %d entries", exits, entries)
    return {"exits": exits, "entries": entries}


def _check_exits(conn, portfolio: PortfolioState) -> int:
    """Evaluate exit triggers on all held positions."""
    clob = PolymarketClient(paper_mode=(settings.mode == "paper"))
    exits = 0

    # Iterate over a copy since we may remove positions
    for pos in list(portfolio.positions):
        try:
            # Refresh current market price via VWMP using stored token_id
            current_p_market = pos.entry_price  # Default to entry price
            tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
            if tid:
                try:
                    bid, ask, bid_sz, ask_sz = clob.get_best_bid_ask(tid)
                    from src.strategy.market_fusion import vwmp
                    current_p_market = vwmp(bid, ask, bid_sz, ask_sz)
                except Exception as e:
                    logger.warning("VWMP refresh failed for %s: %s", pos.trade_id, e)

            # TODO: Recompute p_posterior from fresh ENS + calibration
            # For now, use stored posterior (conservative — edge direction preserved)
            signal = evaluate_exit_triggers(
                position=pos,
                current_p_posterior=pos.p_posterior,
                current_p_market=current_p_market,
            )

            if signal is not None:
                logger.info("EXIT %s: %s — %s", pos.trade_id, signal.trigger, signal.reason)
                remove_position(portfolio, pos.trade_id)
                clear_reversal_state(pos.trade_id)
                log_event(conn, "EXIT", pos.trade_id, {
                    "trigger": signal.trigger,
                    "reason": signal.reason,
                })
                conn.commit()
                exits += 1

        except Exception as e:
            logger.error("Exit check failed for %s: %s", pos.trade_id, e)

    return exits
