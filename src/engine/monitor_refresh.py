"""Monitor refresh: recompute fresh probability for held positions.

Blueprint v2 §7 Layer 1: Recompute probability with SAME METHOD as entry.
Uses full p_raw_vector with MC instrument noise (not simplified _estimate_bin_p_raw).
"""

import logging
from datetime import date

import numpy as np

from src.calibration.manager import get_calibrator
from src.calibration.platt import calibrate_and_normalize
from src.config import cities_by_name
from src.data.ensemble_client import fetch_ensemble, validate_ensemble
from src.data.polymarket_client import PolymarketClient
from src.signal.ensemble_signal import EnsembleSignal
from src.state.portfolio import Position
from src.strategy.market_fusion import compute_alpha, vwmp
from src.types import Bin
from src.data.market_scanner import _parse_temp_range

logger = logging.getLogger(__name__)


def refresh_position(conn, clob: PolymarketClient, pos: Position) -> tuple[float, float]:
    """Fetch fresh market price and recompute P_posterior for a held position.

    Blueprint v2 §7 Layer 1: uses same method as entry (p_raw_vector with MC noise).
    Returns: (current_p_market, current_p_posterior) in native space.
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

        # Build single-bin Bin for the position's range
        low, high = _parse_temp_range(pos.bin_label)
        if low is None and high is None:
            return current_p_market, current_p_posterior

        single_bin = [Bin(low=low, high=high, label=pos.bin_label)]

        # Use FULL p_raw_vector with MC noise (same method as entry)
        # Blueprint v2 §7 Layer 1: method consistency
        p_raw_single = float(ens.p_raw_vector(single_bin, n_mc=1000)[0])

        cal, cal_level = get_calibrator(conn, city, pos.target_date)
        if cal is not None:
            p_cal_yes = cal.predict(p_raw_single, float(lead_days))
        else:
            p_cal_yes = p_raw_single

        alpha = compute_alpha(
            calibration_level=cal_level,
            ensemble_spread=ens.spread(),
            model_agreement="AGREE",
            lead_days=float(lead_days),
            hours_since_open=48.0,
        )

        # Layer 2: Flip to native space EXACTLY ONCE for buy_no
        if pos.direction == "buy_no":
            p_cal_native = 1.0 - p_cal_yes
        else:
            p_cal_native = p_cal_yes

        current_p_posterior = alpha * p_cal_native + (1.0 - alpha) * current_p_market

        # Persist monitor state on Position
        pos.last_monitor_prob = current_p_posterior
        pos.last_monitor_edge = current_p_posterior - current_p_market

    except Exception as e:
        logger.debug("ENS refresh failed for %s: %s", pos.trade_id, e)

    return current_p_market, current_p_posterior
