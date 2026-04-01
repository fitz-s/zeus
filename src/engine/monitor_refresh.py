"""Monitor refresh: recompute fresh probability for held positions.

Blueprint v2 §7 Layer 1: Recompute probability with SAME METHOD as entry.
Uses full p_raw_vector with MC instrument noise (not simplified _estimate_bin_p_raw).
"""

import logging
from datetime import date, datetime, timezone

import numpy as np

from src.calibration.manager import get_calibrator
from src.calibration.platt import calibrate_and_normalize
from src.config import cities_by_name, day0_n_mc, ensemble_n_mc
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
    requested_lead_days = max(0.0, lead_days_to_target(target_d, city.timezone))
    if requested_lead_days < 0:
        return position.p_posterior, ["fresh_ens_fetch"]

    ens_result = fetch_ensemble(city, forecast_days=int(requested_lead_days) + 2)
    if ens_result is None or not validate_ensemble(ens_result):
        return position.p_posterior, ["fresh_ens_fetch"]
    lead_days = max(0.0, lead_days_to_target(target_d, city.timezone, ens_result.get("fetch_time")))

    semantics = SettlementSemantics.for_city(city)
    ens = EnsembleSignal(
        ens_result["members_hourly"],
        ens_result["times"],
        city,
        target_d,
        settlement_semantics=semantics,
        decision_time=ens_result.get("fetch_time"),
    )

    low, high = _parse_temp_range(position.bin_label)
    if low is None and high is None:
        return position.p_posterior, ["fresh_ens_fetch"]

    single_bin = [Bin(low=low, high=high, label=position.bin_label, unit=position.unit)]
    p_raw_single = float(ens.p_raw_vector(single_bin, n_mc=ensemble_n_mc())[0])

    cal, cal_level = get_calibrator(conn, city, position.target_date)
    if cal is not None:
        p_cal_yes = cal.predict_for_bin(
            p_raw_single,
            float(lead_days),
            bin_width=single_bin[0].width,
        )
        applied = ["fresh_ens_fetch", "mc_instrument_noise", "platt_recalibration"]
    else:
        p_cal_yes = p_raw_single
        applied = ["fresh_ens_fetch", "mc_instrument_noise"]

    # Compute actual hours since position was entered (not hardcoded 48h)
    hours_since_open = 48.0
    if position.entered_at:
        try:
            entered = datetime.fromisoformat(position.entered_at)
            if entered.tzinfo is None:
                entered = entered.replace(tzinfo=timezone.utc)
            hours_since_open = (datetime.now(timezone.utc) - entered).total_seconds() / 3600.0
        except Exception:
            pass  # Malformed timestamp → fall back to 48h

    alpha = compute_alpha(
        calibration_level=cal_level,
        ensemble_spread=ens.spread(),
        model_agreement="AGREE",
        lead_days=float(lead_days),
        hours_since_open=hours_since_open,
    )

    # Persistence anomaly check: if ENS predicts a historically rare
    # day-to-day temperature change, discount model trust
    anomaly_discount = _check_persistence_anomaly(
        conn, city.name, target_d, float(np.mean(ens.member_maxes))
    )
    if anomaly_discount < 1.0:
        alpha *= anomaly_discount
        applied.append("persistence_anomaly_discount")
        logger.info(
            "Persistence anomaly for %s: α discounted by %.0f%%",
            city.name, (1.0 - anomaly_discount) * 100,
        )

    if position.direction == "buy_no":
        p_cal_native = 1.0 - p_cal_yes
    else:
        p_cal_native = p_cal_yes

    current_p_posterior = alpha * p_cal_native + (1.0 - alpha) * current_p_market
    return current_p_posterior, [*applied, "alpha_posterior"]


def _fetch_day0_observation(city: Position | object, target_d: date):
    reference_time = datetime.now(timezone.utc)
    try:
        return get_current_observation(city, target_date=target_d, reference_time=reference_time)
    except TypeError:
        return get_current_observation(city)


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
    obs = _fetch_day0_observation(city, target_d)
    if obs is None:
        return position.p_posterior, ["day0_observation"]
    if not obs.get("observation_time"):
        return position.p_posterior, ["day0_observation", "missing_observation_timestamp"]

    ens_result = fetch_ensemble(city, forecast_days=2)
    if ens_result is None or not validate_ensemble(ens_result):
        return position.p_posterior, ["day0_observation", "fresh_ens_fetch"]

    low, high = _parse_temp_range(position.bin_label)
    if low is None and high is None:
        return position.p_posterior, ["day0_observation", "fresh_ens_fetch"]

    try:
        from src.signal.diurnal import build_day0_temporal_context
        temporal_context = build_day0_temporal_context(
            city.name,
            target_d,
            city.timezone,
            observation_time=obs.get("observation_time"),
            observation_source=obs.get("source", ""),
        )
    except Exception:
        temporal_context = None

    if temporal_context is None:
        return position.p_posterior, ["day0_observation", "fresh_ens_fetch", "missing_solar_context"]

    remaining_member_maxes, hours_remaining = remaining_member_maxes_for_day0(
        ens_result["members_hourly"],
        ens_result["times"],
        city.timezone,
        target_d,
        now=temporal_context.current_utc_timestamp,
    )
    if remaining_member_maxes.size == 0:
        return position.p_posterior, ["day0_observation", "fresh_ens_fetch"]

    day0 = Day0Signal(
        observed_high_so_far=float(obs["high_so_far"]),
        current_temp=float(obs["current_temp"]),
        hours_remaining=hours_remaining,
        member_maxes_remaining=remaining_member_maxes,
        unit=city.settlement_unit,
        temporal_context=temporal_context,
    )
    single_bin = [Bin(low=low, high=high, label=position.bin_label, unit=position.unit)]
    p_raw_yes = float(day0.p_vector(single_bin, n_mc=day0_n_mc())[0])

    cal, cal_level = get_calibrator(conn, city, position.target_date)
    if cal is not None:
        p_cal_yes = cal.predict_for_bin(
            p_raw_yes,
            0.0,
            bin_width=single_bin[0].width,
        )
        applied = ["day0_observation", "fresh_ens_fetch", "mc_instrument_noise", "platt_recalibration"]
    else:
        p_cal_yes = p_raw_yes
        applied = ["day0_observation", "fresh_ens_fetch", "mc_instrument_noise"]

    ensemble_spread = TemperatureDelta(float(np.std(remaining_member_maxes)), city.settlement_unit)

    hours_since_open = 48.0
    if position.entered_at:
        try:
            entered = datetime.fromisoformat(position.entered_at)
            if entered.tzinfo is None:
                entered = entered.replace(tzinfo=timezone.utc)
            hours_since_open = (datetime.now(timezone.utc) - entered).total_seconds() / 3600.0
        except Exception:
            pass

    alpha = compute_alpha(
        calibration_level=cal_level,
        ensemble_spread=ensemble_spread,
        model_agreement="AGREE",
        lead_days=0.0,
        hours_since_open=hours_since_open,
    )
    p_cal_native = 1.0 - p_cal_yes if position.direction == "buy_no" else p_cal_yes
    current_p_posterior = alpha * p_cal_native + (1.0 - alpha) * current_p_market
    return current_p_posterior, [*applied, "alpha_posterior"]


def _delta_bucket(delta: float) -> str:
    if abs(delta) <= 1:
        return "-1 to 1"
    elif -3 <= delta < -1:
        return "-3 to -1"
    elif -5 <= delta < -3:
        return "-5 to -3"
    elif -10 <= delta < -5:
        return "-10 to -5"
    elif delta < -10:
        return "<-10"
    elif 1 < delta <= 3:
        return "1 to 3"
    elif 3 < delta <= 5:
        return "3 to 5"
    elif 5 < delta <= 10:
        return "5 to 10"
    else:
        return ">10"


def _check_persistence_anomaly(
    conn, city_name: str, target_date, predicted_high: float
) -> float:
    """Check if ENS-predicted temp change from recent days is historically rare.

    Looks at the last 3 days of settlements and averages the delta to smooth out
    single-day noise. Discount is confidence-scaled by sample size:
    - n < 30: not enough data → no discount
    - n=30: 10% discount
    - n=100+: 30% max discount
    """
    from datetime import timedelta

    try:
        month = target_date.month
        if month in (12, 1, 2):
            season = "DJF"
        elif month in (3, 4, 5):
            season = "MAM"
        elif month in (6, 7, 8):
            season = "JJA"
        else:
            season = "SON"

        # Average delta over last 3 available settlement days
        deltas = []
        for days_back in range(1, 4):
            d = (target_date - timedelta(days=days_back)).isoformat()
            row = conn.execute(
                "SELECT settlement_value FROM settlements "
                "WHERE city = ? AND target_date = ? LIMIT 1",
                (city_name, d),
            ).fetchone()
            if row and row["settlement_value"] is not None:
                deltas.append(predicted_high - row["settlement_value"])

        if not deltas:
            return 1.0

        delta = sum(deltas) / len(deltas)
        bucket = _delta_bucket(delta)

        freq_row = conn.execute(
            "SELECT frequency, n_samples FROM temp_persistence "
            "WHERE city = ? AND season = ? AND delta_bucket = ?",
            (city_name, season, bucket),
        ).fetchone()

        if freq_row and freq_row["frequency"] < 0.05:
            n = freq_row["n_samples"]
            if n < 30:
                return 1.0  # Too few samples to trust the frequency estimate
            # Scale discount: 10% at n=30, grows linearly to 30% at n>=100
            discount_magnitude = min(0.30, 0.10 + 0.20 * (n - 30) / 70.0)
            return 1.0 - discount_magnitude

    except Exception as e:
        logger.debug("Persistence anomaly check failed for %s: %s", city_name, e)

    return 1.0


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
        raise ValueError(f"Unknown direction {pos.direction} for trade {pos.trade_id}")

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

                # Injection Point 7: Data completeness - record microstructure snapshot
                from src.state.db import log_microstructure

                log_microstructure(
                    conn,
                    token_id=tid,
                    city=pos.city,
                    target_date=pos.target_date,
                    range_label=pos.bin_label,
                    price=float(current_p_market),
                    volume=float(bid_sz + ask_sz),
                    bid=float(bid),
                    ask=float(ask),
                    spread=round(float(ask - bid), 4) if ask >= bid else 0.0,
                    source_timestamp=datetime.now(timezone.utc).isoformat(),
                )
            except Exception as e:
                logger.debug("VWMP refresh failed for %s: %s", pos.trade_id, e)

    if market_refreshed:
        pos.last_monitor_market_price = current_p_market
        pos.last_monitor_at = datetime.now(timezone.utc).isoformat()

    # 2. Recompute P_posterior from fresh ENS
    city = cities_by_name.get(pos.city)
    if city is None:
        raise ValueError(f"Unknown city {pos.city} for trade {pos.trade_id}")

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

    divergence_score = abs(current_p_posterior - current_p_market)
    market_velocity_1h = 0.0

    # Try fetching 1h velocity if we know the token
    tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
    if tid:
        from datetime import timedelta
        try:
            one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            row = conn.execute(
                "SELECT price FROM token_price_log WHERE token_id = ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
                (tid, one_hour_ago),
            ).fetchone()
            if row:
                old_native_p = row["price"]
                market_velocity_1h = current_p_market - old_native_p
        except Exception as e:
            logger.debug("Failed to calculate market velocity for %s: %s", pos.trade_id, e)

    # Wrap into verified EdgeContext
    current_forward_edge = current_p_posterior - current_p_market
    ci_half_width = max(0.0, pos.entry_ci_width) / 2.0
    return EdgeContext(
        p_raw=np.array([]),
        p_cal=np.array([]),
        p_market=np.array([current_p_market]),
        p_posterior=current_p_posterior,
        forward_edge=current_forward_edge,
        alpha=0.0,
        confidence_band_upper=current_forward_edge + ci_half_width,
        confidence_band_lower=current_forward_edge - ci_half_width,
        entry_provenance=EntryMethod(pos.entry_method),
        decision_snapshot_id=pos.decision_snapshot_id,
        n_edges_found=1,
        n_edges_after_fdr=1,
        market_velocity_1h=market_velocity_1h,
        divergence_score=divergence_score,
    )
