"""Position monitor: variable-frequency exit trigger checking. Spec §6.3.

Frequency scales with time to settlement:
  >48h:  every 6h
  24-48h: every 2h
  12-24h: every 1h
  4-12h:  every 30min
  <4h:    every 15min

ONLY checks exit triggers. Does NOT re-evaluate entry.
For each position: fetch fresh VWMP + recompute P_posterior from ENS.
"""

import logging
from datetime import date, datetime, timezone

import numpy as np

from src.calibration.manager import get_calibrator
from src.calibration.platt import calibrate_and_normalize
from src.config import settings
from src.data.ensemble_client import fetch_ensemble, validate_ensemble
from src.data.polymarket_client import PolymarketClient
from src.execution.exit_triggers import evaluate_exit_triggers, clear_reversal_state
from src.signal.ensemble_signal import EnsembleSignal
from src.state.chronicler import log_event
from src.state.db import get_connection
from src.state.portfolio import (
    Position, PortfolioState,
    load_portfolio, save_portfolio, remove_position,
)
from src.strategy.market_fusion import compute_alpha, vwmp
from src.types import Bin

logger = logging.getLogger(__name__)


def get_check_interval_minutes(hours_to_settlement: float) -> int:
    """Determine check interval based on time to settlement."""
    if hours_to_settlement < 4:
        return 15
    elif hours_to_settlement < 12:
        return 30
    elif hours_to_settlement < 24:
        return 60
    elif hours_to_settlement < 48:
        return 120
    else:
        return 360


def run_monitor() -> int:
    """Check exit triggers on all held positions with fresh data.

    For each position:
    1. Fetch current VWMP via CLOB orderbook (using stored token_id)
    2. Recompute P_posterior from fresh ENS + calibration
    3. Evaluate exit triggers with current values
    """
    portfolio = load_portfolio()
    if not portfolio.positions:
        return 0

    conn = get_connection()
    clob = PolymarketClient(paper_mode=(settings.mode == "paper"))
    exits = 0

    for pos in list(portfolio.positions):
        try:
            current_p_market, current_p_posterior = _refresh_position(
                conn, clob, pos
            )

            signal = evaluate_exit_triggers(
                position=pos,
                current_p_posterior=current_p_posterior,
                current_p_market=current_p_market,
            )

            if signal is not None:
                logger.info("MONITOR EXIT %s: %s — %s",
                            pos.trade_id, signal.trigger, signal.reason)
                remove_position(portfolio, pos.trade_id)
                clear_reversal_state(pos.trade_id)
                log_event(conn, "EXIT", pos.trade_id, {
                    "trigger": signal.trigger,
                    "reason": signal.reason,
                    "current_edge": current_p_posterior - current_p_market,
                    "entry_edge": pos.edge,
                    "source": "monitor",
                })
                conn.commit()
                exits += 1

        except Exception as e:
            logger.error("Monitor check failed for %s: %s", pos.trade_id, e)

    if exits > 0:
        save_portfolio(portfolio)

    conn.close()
    return exits


def _refresh_position(
    conn, clob: PolymarketClient, pos: Position
) -> tuple[float, float]:
    """Fetch fresh market price and recompute P_posterior for a held position.

    Returns: (current_p_market, current_p_posterior)
    Falls back to stored values if refresh fails.
    """
    current_p_market = pos.entry_price
    current_p_posterior = pos.p_posterior

    # 1. Refresh market price via VWMP
    tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
    if tid:
        try:
            bid, ask, bid_sz, ask_sz = clob.get_best_bid_ask(tid)
            current_p_market = vwmp(bid, ask, bid_sz, ask_sz)
        except Exception as e:
            logger.debug("VWMP refresh failed for %s: %s", pos.trade_id, e)

    # 2. Recompute P_posterior from fresh ENS
    from src.config import cities_by_name
    city = cities_by_name.get(pos.city)
    if city is None:
        return current_p_market, current_p_posterior

    try:
        target_d = date.fromisoformat(pos.target_date)
        lead_days = (target_d - date.today()).days
        if lead_days < 0:
            return current_p_market, current_p_posterior

        ens_result = fetch_ensemble(city, forecast_days=lead_days + 2)
        if ens_result is None or not validate_ensemble(ens_result):
            return current_p_market, current_p_posterior

        ens = EnsembleSignal(ens_result["members_hourly"], city, target_d)

        # We need the bin structure — reconstruct from the position's bin_label
        # For a single-bin check, we compute P_posterior for that specific bin
        # using the market-wide P_raw renormalized
        bin_obj = Bin(low=None, high=None, label=pos.bin_label)
        # Simple single-bin posterior: calibrated P_raw vs market VWMP
        p_raw_single = _estimate_bin_p_raw(ens, pos.bin_label)

        cal, cal_level = get_calibrator(conn, city, pos.target_date)
        if cal is not None:
            p_cal_single = cal.predict(p_raw_single, float(lead_days))
        else:
            p_cal_single = p_raw_single

        alpha = compute_alpha(
            calibration_level=cal_level,
            ensemble_spread=ens.spread(),
            model_agreement="AGREE",  # Skip GFS crosscheck for monitor (too expensive)
            lead_days=float(lead_days),
            hours_since_open=48.0,  # Conservative: treat as established market
        )

        current_p_posterior = alpha * p_cal_single + (1.0 - alpha) * current_p_market

    except Exception as e:
        logger.debug("ENS refresh failed for %s: %s", pos.trade_id, e)

    return current_p_market, current_p_posterior


def _estimate_bin_p_raw(ens: EnsembleSignal, bin_label: str) -> float:
    """Estimate P_raw for a single bin from ensemble member maxes.

    Parses bin label to get boundaries, then counts members in range.
    Falls back to a rough estimate if parsing fails.
    """
    from src.data.market_scanner import _parse_temp_range

    low, high = _parse_temp_range(bin_label)
    measured = np.round(ens.member_maxes).astype(int)

    if low is None and high is not None:
        p = float(np.mean(measured <= high))
    elif high is None and low is not None:
        p = float(np.mean(measured >= low))
    elif low is not None and high is not None:
        p = float(np.mean((measured >= low) & (measured <= high)))
    else:
        p = 0.1  # Unparseable — conservative default

    return max(0.01, min(0.99, p))
