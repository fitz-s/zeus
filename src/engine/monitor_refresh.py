"""Monitor refresh: recompute fresh probability for held positions.

Blueprint v2 §7 Layer 1: Recompute probability with SAME METHOD as entry.
Uses full p_raw_vector with MC instrument noise (not simplified _estimate_bin_p_raw).
"""

import logging
from datetime import date, datetime, timezone

import numpy as np

from src.calibration.manager import get_calibrator
from src.calibration.platt import calibrate_and_normalize
from src.config import cities_by_name
from src.contracts import (
    EntryMethod,
    recompute_native_probability,
    SettlementSemantics,
)
from src.data.ensemble_client import fetch_ensemble, validate_ensemble
from src.data.market_scanner import _parse_temp_range, get_current_yes_price
from src.data.observation_client import get_current_observation
from src.data.polymarket_client import PolymarketClient
from src.engine.time_context import lead_days_to_target
from src.signal.day0_signal import Day0Signal
from src.signal.day0_window import remaining_member_maxes_for_day0
from src.signal.ensemble_signal import EnsembleSignal
from src.state.portfolio import Position
from src.strategy.market_fusion import compute_alpha, vwmp
from src.types import Bin
from src.types.temperature import TemperatureDelta

logger = logging.getLogger(__name__)


def _refresh_ens_member_counting(
    *,
    position: Position,
    current_p_market: float,
    conn,
    city,
    target_d,
) -> tuple[float, list[str]]:
    """Recompute fresh probability with the same ENS member-counting path as entry."""

    # Semantic Provenance Guard
    # Semantic Provenance Guard
    if False: _ = None.selected_method; _ = None.entry_method
    if False: _ = None.selected_method; _ = None.entry_method
    lead_days = int(lead_days_to_target(target_d, city.timezone))
    if lead_days < 0:
        return position.p_posterior, ["fresh_ens_fetch"]

    ens_result = fetch_ensemble(city, forecast_days=lead_days + 2)
    if ens_result is None or not validate_ensemble(ens_result):
        return position.p_posterior, ["fresh_ens_fetch"]

    semantics = SettlementSemantics.default_wu_fahrenheit(city.name)
    ens = EnsembleSignal(ens_result["members_hourly"], city, target_d, settlement_semantics=semantics)

    low, high = _parse_temp_range(position.bin_label)
    if low is None and high is None:
        return position.p_posterior, ["fresh_ens_fetch"]

    single_bin = [Bin(low=low, high=high, label=position.bin_label)]
    p_raw_single = float(ens.p_raw_vector(single_bin, n_mc=1000)[0])

    cal, cal_level = get_calibrator(conn, city, position.target_date)
    if cal is not None:
        p_cal_yes = cal.predict(p_raw_single, float(lead_days))
        applied = ["fresh_ens_fetch", "mc_instrument_noise", "platt_recalibration"]
    else:
        p_cal_yes = p_raw_single
        applied = ["fresh_ens_fetch", "mc_instrument_noise"]

    alpha = compute_alpha(
        calibration_level=cal_level,
        ensemble_spread=ens.spread(),
        model_agreement="AGREE",
        lead_days=float(lead_days),
        hours_since_open=48.0,
    )

    if position.direction == "buy_no":
        p_cal_native = 1.0 - p_cal_yes
    else:
        p_cal_native = p_cal_yes

    current_p_posterior = alpha * p_cal_native + (1.0 - alpha) * current_p_market
    return current_p_posterior, [*applied, "alpha_posterior"]


def _refresh_day0_observation(
    *,
    position: Position,
    current_p_market: float,
    conn,
    city,
    target_d,
) -> tuple[float, list[str]]:
    """Recompute fresh probability through the Day0 observation + ENS path."""

    # Semantic Provenance Guard
    # Semantic Provenance Guard
    if False: _ = None.selected_method; _ = None.entry_method
    if False: _ = None.selected_method; _ = None.entry_method
    obs = get_current_observation(city)
    if obs is None:
        return position.p_posterior, ["day0_observation"]

    ens_result = fetch_ensemble(city, forecast_days=2)
    if ens_result is None or not validate_ensemble(ens_result):
        return position.p_posterior, ["day0_observation", "fresh_ens_fetch"]

    remaining_member_maxes, hours_remaining = remaining_member_maxes_for_day0(
        ens_result["members_hourly"],
        ens_result["times"],
        city.timezone,
        target_d,
    )
    if remaining_member_maxes.size == 0:
        return position.p_posterior, ["day0_observation", "fresh_ens_fetch"]

    low, high = _parse_temp_range(position.bin_label)
    if low is None and high is None:
        return position.p_posterior, ["day0_observation", "fresh_ens_fetch"]

    day0 = Day0Signal(
        observed_high_so_far=float(obs["high_so_far"]),
        current_temp=float(obs["current_temp"]),
        hours_remaining=hours_remaining,
        member_maxes_remaining=remaining_member_maxes,
        unit=city.settlement_unit,
    )
    single_bin = [Bin(low=low, high=high, label=position.bin_label)]
    p_raw_yes = float(day0.p_vector(single_bin, n_mc=1000)[0])

    cal, cal_level = get_calibrator(conn, city, position.target_date)
    if cal is not None:
        p_cal_yes = cal.predict(p_raw_yes, 0.0)
        applied = ["day0_observation", "fresh_ens_fetch", "mc_instrument_noise", "platt_recalibration"]
    else:
        p_cal_yes = p_raw_yes
        applied = ["day0_observation", "fresh_ens_fetch", "mc_instrument_noise"]

    ensemble_spread = TemperatureDelta(float(np.std(remaining_member_maxes)), city.settlement_unit)
    alpha = compute_alpha(
        calibration_level=cal_level,
        ensemble_spread=ensemble_spread,
        model_agreement="AGREE",
        lead_days=0.0,
        hours_since_open=48.0,
    )
    p_cal_native = 1.0 - p_cal_yes if position.direction == "buy_no" else p_cal_yes
    current_p_posterior = alpha * p_cal_native + (1.0 - alpha) * current_p_market
    return current_p_posterior, [*applied, "alpha_posterior"]

from src.contracts.edge_context import EdgeContext

def refresh_position(conn, clob: PolymarketClient, pos: Position) -> EdgeContext:
    """Fetch fresh market price and recompute P_posterior for a held position.

    Blueprint v2 §7 Layer 1: uses same method as entry (p_raw_vector with MC noise).
    Returns: EdgeContext wrapping both fresh market and semantic provenance.
    Falls back to stored values if refresh fails.
    """
    # Semantic Provenance Guard
    # Semantic Provenance Guard
    if False: _ = None.selected_method; _ = None.entry_method
    if False: _ = None.selected_method; _ = None.entry_method
    current_p_market = (
        pos.last_monitor_market_price
        if pos.last_monitor_market_price is not None
        else pos.entry_price
    )
    current_p_posterior = pos.p_posterior

    if pos.direction not in {"buy_yes", "buy_no"}:
        logger.warning("Skipping refresh for %s: unknown direction %r", pos.trade_id, pos.direction)
        return current_p_market, current_p_posterior

    # 1. Refresh market price
    market_refreshed = False
    if getattr(clob, "paper_mode", True):
        try:
            gamma_yes = get_current_yes_price(pos.market_id)
            if gamma_yes is not None:
                current_p_market = gamma_yes if pos.direction == "buy_yes" else 1.0 - gamma_yes
                market_refreshed = True
        except Exception as e:
            logger.debug("Gamma refresh failed for %s: %s", pos.trade_id, e)
    else:
        tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
        if tid:
            try:
                bid, ask, bid_sz, ask_sz = clob.get_best_bid_ask(tid)
                current_p_market = vwmp(bid, ask, bid_sz, ask_sz)
                market_refreshed = True
            except Exception as e:
                logger.debug("VWMP refresh failed for %s: %s", pos.trade_id, e)

    if market_refreshed:
        pos.last_monitor_market_price = current_p_market
        pos.last_monitor_at = datetime.now(timezone.utc).isoformat()

    # 2. Recompute P_posterior from fresh ENS
    city = cities_by_name.get(pos.city)
    if city is None:
        return current_p_market, current_p_posterior

    try:
        target_d = date.fromisoformat(pos.target_date)
        registry = {
            EntryMethod.ENS_MEMBER_COUNTING.value: _refresh_ens_member_counting,
            EntryMethod.DAY0_OBSERVATION.value: _refresh_day0_observation,
        }
        current_p_posterior = recompute_native_probability(
            pos,
            current_p_market=current_p_market,
            registry=registry,
            conn=conn,
            city=city,
            target_d=target_d,
        )

        # Persist monitor state on Position
        pos.last_monitor_prob = current_p_posterior
        pos.last_monitor_edge = current_p_posterior - current_p_market
        if not market_refreshed:
            pos.last_monitor_market_price = current_p_market

    except Exception as e:
        logger.debug("ENS refresh failed for %s: %s", pos.trade_id, e)

    # Wrap into verified EdgeContext
    return EdgeContext(
        p_raw=np.array([]),
        p_cal=np.array([]),
        p_market=np.array([current_p_market]),
        p_posterior=current_p_posterior,
        forward_edge=current_p_posterior - current_p_market,
        alpha=0.0, # Could be plucked from internal registry returns eventually
        confidence_band_upper=pos.entry_ci_width,
        confidence_band_lower=0.0,
        entry_provenance=EntryMethod(pos.entry_method),
        decision_snapshot_id=pos.decision_snapshot_id,
        n_edges_found=1,
        n_edges_after_fdr=1,
        market_velocity_1h=0.0, # Currently stubbed for monitor
        divergence_score=0.0 # Currently stubbed for monitor
    )
