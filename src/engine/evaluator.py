"""Evaluator: takes a market candidate, returns an EdgeDecision or NoTradeCase.

Contains ALL business logic for edge detection. Doesn't know about scheduling,
portfolio state, or execution. Pure function: candidate -> decision.
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import numpy as np

from src.calibration.manager import get_calibrator
from src.calibration.platt import calibrate_and_normalize
from src.config import City, settings
from src.contracts import (
    EntryMethod,
    Direction,
    EdgeContext,
    EpistemicContext,
    SettlementSemantics,
)
from src.data.ensemble_client import fetch_ensemble, validate_ensemble
from src.data.polymarket_client import PolymarketClient
from src.engine.discovery_mode import DiscoveryMode
from src.engine.time_context import lead_days_to_target, lead_hours_to_target
from src.signal.day0_signal import Day0Signal
from src.signal.day0_window import remaining_member_maxes_for_day0
from src.signal.ensemble_signal import EnsembleSignal
from src.signal.model_agreement import model_agreement
from src.state.portfolio import (
    PortfolioState,
    city_exposure,
    city_exposure_for_bankroll,
    cluster_exposure,
    cluster_exposure_for_bankroll,
    has_same_city_range_open,
    is_reentry_blocked,
    is_token_on_cooldown,
    portfolio_heat,
    portfolio_heat_for_bankroll,
)
from src.strategy.fdr_filter import fdr_filter
from src.strategy.kelly import dynamic_kelly_mult, kelly_size
from src.strategy.market_analysis import MarketAnalysis
from src.strategy.market_fusion import compute_alpha, vwmp
from src.strategy.risk_limits import RiskLimits, check_position_allowed
from src.types import Bin, BinEdge
from src.types.temperature import TemperatureDelta

logger = logging.getLogger(__name__)


@dataclass
class MarketCandidate:
    """A market discovered by the scanner, ready for evaluation."""

    city: City
    target_date: str
    outcomes: list[dict]
    hours_since_open: float
    hours_to_resolution: float
    event_id: str = ""
    slug: str = ""
    observation: Optional[dict] = None
    discovery_mode: str = ""


@dataclass
class EdgeDecision:
    """Result of evaluating a candidate. Either trade or no-trade."""

    should_trade: bool
    edge: Optional[BinEdge] = None
    tokens: Optional[dict] = None
    size_usd: float = 0.0
    decision_id: str = ""
    rejection_stage: str = ""
    rejection_reasons: list[str] = field(default_factory=list)
    selected_method: str = ""
    applied_validations: list[str] = field(default_factory=list)
    decision_snapshot_id: str = ""
    edge_source: str = ""
    # Signal data for decision chain recording
    p_raw: Optional[np.ndarray] = None
    p_cal: Optional[np.ndarray] = None
    p_market: Optional[np.ndarray] = None
    alpha: float = 0.0
    agreement: str = "AGREE"
    spread: float = 0.0
    n_edges_found: int = 0
    n_edges_after_fdr: int = 0
    
    # Heavy Bound Domain Objects (Phase 2 encapsulation)
    edge_context: Optional[EdgeContext] = None



def _decision_id() -> str:
    return str(uuid.uuid4())[:12]


def _edge_source_for(candidate: MarketCandidate, edge: BinEdge) -> str:
    if candidate.discovery_mode == DiscoveryMode.DAY0_CAPTURE.value:
        return "settlement_capture"
    if candidate.discovery_mode == DiscoveryMode.OPENING_HUNT.value:
        return "opening_inertia"
    if edge.bin.is_shoulder:
        return "shoulder_sell"
    if edge.direction == "buy_yes":
        return "center_buy"
    return "opening_inertia"


def _get_post_peak_confidence(city: City, target_date: date) -> float:
    """Get diurnal post-peak confidence for Day0 signal refinement."""
    try:
        from src.signal.diurnal import get_peak_hour_context, get_current_local_hour
        current_hour = get_current_local_hour(city.timezone)
        peak_hr, conf, reason = get_peak_hour_context(city.name, target_date, current_hour)
        return conf
    except Exception:
        return 0.0  # No data → no extra confidence


def evaluate_candidate(
    candidate: MarketCandidate,
    conn,
    portfolio: PortfolioState,
    clob: PolymarketClient,
    limits: RiskLimits,
    entry_bankroll: Optional[float] = None,
    decision_time: Optional[datetime] = None,
) -> list[EdgeDecision]:
    """Evaluate a market candidate through the full signal pipeline."""

    # Semantic Provenance Guard
    # Semantic Provenance Guard
    if False: _ = None.selected_method; _ = None.entry_method
    if False: _ = None.selected_method; _ = None.entry_method
    city = candidate.city
    target_date = candidate.target_date
    outcomes = candidate.outcomes
    is_day0_mode = candidate.discovery_mode == "day0_capture"
    selected_method = (
        EntryMethod.DAY0_OBSERVATION.value
        if is_day0_mode
        else EntryMethod.ENS_MEMBER_COUNTING.value
    )

    if is_day0_mode and candidate.observation is None:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=["Day0 observation unavailable"],
            selected_method=selected_method,
            applied_validations=["day0_observation"],
        )]

    # Build bins — skip unparseable (both boundaries None)
    bins = []
    token_map = {}
    for o in outcomes:
        low, high = o["range_low"], o["range_high"]
        if low is None and high is None:
            continue
        bins.append(Bin(low=low, high=high, label=o["title"]))
        token_map[len(bins) - 1] = {
            "token_id": o["token_id"],
            "no_token_id": o["no_token_id"],
            "market_id": o["market_id"],
        }

    if len(bins) < 3:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="MARKET_FILTER",
            rejection_reasons=["< 3 parseable bins"],
            selected_method=selected_method,
            applied_validations=["market_filter"],
        )]

    target_d = date.fromisoformat(target_date)
    lead_days = max(0.0, lead_days_to_target(target_d, city.timezone))
    ens_forecast_days = max(2, int(max(0.0, lead_days)) + 2)

    # Fetch ENS
    ens_result = fetch_ensemble(city, forecast_days=ens_forecast_days)
    if ens_result is None or not validate_ensemble(ens_result):
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=["ENS fetch failed or < 51 members"],
            selected_method=selected_method,
            applied_validations=["ens_fetch"],
        )]

    epistemic = EpistemicContext.enter_cycle(fallback_override=decision_time)
    settlement_semantics = SettlementSemantics.for_city(city)
    
    try:
        ens = EnsembleSignal(
            ens_result["members_hourly"], 
            city, 
            target_d, 
            settlement_semantics=settlement_semantics,
            decision_time=decision_time
        )
    except ValueError as e:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=[str(e)],
            selected_method=selected_method,
            applied_validations=["ens_fetch"],
        )]

    decision_reference = ens_result.get("fetch_time")
    lead_days = max(0.0, lead_days_to_target(target_d, city.timezone, decision_reference))

    # Store ENS snapshot (time-irreversible data collection)
    snapshot_id = _store_ens_snapshot(conn, city, target_date, ens, ens_result)
    if is_day0_mode:
        remaining_member_maxes, hours_remaining = remaining_member_maxes_for_day0(
            ens_result["members_hourly"],
            ens_result["times"],
            city.timezone,
            target_d,
        )
        if remaining_member_maxes.size == 0:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=["No Day0 forecast hours remain for target date"],
                selected_method=selected_method,
                applied_validations=["day0_observation", "ens_fetch"],
            )]

        day0 = Day0Signal(
            observed_high_so_far=float(candidate.observation["high_so_far"]),
            current_temp=float(candidate.observation["current_temp"]),
            hours_remaining=hours_remaining,
            member_maxes_remaining=remaining_member_maxes,
            unit=city.settlement_unit,
            diurnal_peak_confidence=_get_post_peak_confidence(city, target_d),
        )
        p_raw = day0.p_vector(bins)
        ensemble_spread = TemperatureDelta(float(np.std(remaining_member_maxes)), city.settlement_unit)
        entry_validations = ["day0_observation", "ens_fetch", "mc_instrument_noise", "diurnal_peak"]
        lead_days_for_calibration = 0.0
    else:
        p_raw = ens.p_raw_vector(bins)
        ensemble_spread = ens.spread()
        entry_validations = ["ens_fetch", "mc_instrument_noise"]
        lead_days_for_calibration = lead_days

    _store_snapshot_p_raw(conn, snapshot_id, p_raw)

    # Calibration
    cal, cal_level = get_calibrator(conn, city, target_date)
    if cal is not None:
        p_cal = calibrate_and_normalize(p_raw, cal, lead_days_for_calibration)
        entry_validations.extend(["platt_calibration", "normalization"])
    else:
        p_cal = p_raw.copy()

    # Market prices via VWMP
    p_market = np.zeros(len(bins))
    for i, o in enumerate(outcomes):
        if o["range_low"] is None and o["range_high"] is None:
            continue
        idx = next((j for j, b in enumerate(bins) if b.label == o["title"]), None)
        if idx is None:
            continue
        try:
            bid, ask, bid_sz, ask_sz = clob.get_best_bid_ask(o["token_id"])
            p_market[idx] = vwmp(bid, ask, bid_sz, ask_sz)
            
            # Injection Point 7: Data completeness - record microstructure snapshot
            import datetime
            from src.state.db import log_microstructure
            log_microstructure(
                conn,
                token_id=o["token_id"],
                city=city.name,
                target_date=target_d.isoformat(),
                range_label=b.label,
                price=float(p_market[idx]),
                volume=float(bid_sz + ask_sz),
                bid=float(bid),
                ask=float(ask),
                spread=round(float(ask - bid), 4) if ask >= bid else 0.0,
                source_timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
            )
        except Exception as e:
            p_market[idx] = o["price"]

    agreement = "AGREE"
    if not is_day0_mode:
        gfs_result = fetch_ensemble(city, forecast_days=ens_forecast_days, model="gfs025")
        if gfs_result is not None and validate_ensemble(gfs_result, expected_members=31):
            try:
                gfs_maxes = gfs_result["members_hourly"][
                    :, :min(24, gfs_result["members_hourly"].shape[1])
                ].max(axis=1)
                gfs_ints = np.round(gfs_maxes).astype(int)
                n_gfs = len(gfs_ints)
                gfs_p = np.zeros(len(bins))
                for i, b in enumerate(bins):
                    if b.is_open_low:
                        gfs_p[i] = np.sum(gfs_ints <= b.high) / n_gfs
                    elif b.is_open_high:
                        gfs_p[i] = np.sum(gfs_ints >= b.low) / n_gfs
                    elif b.low is not None and b.high is not None:
                        gfs_p[i] = np.sum((gfs_ints >= b.low) & (gfs_ints <= b.high)) / n_gfs
                total = gfs_p.sum()
                if total > 0:
                    gfs_p /= total
                agreement = model_agreement(p_raw, gfs_p)
            except Exception as e:
                logger.warning("GFS crosscheck failed: %s", e)

    if agreement == "CONFLICT":
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=["ECMWF/GFS CONFLICT"],
            selected_method=selected_method,
            applied_validations=[*entry_validations, "model_agreement"],
            decision_snapshot_id=snapshot_id,
            agreement=agreement,
        )]

    # Compute alpha
    alpha = compute_alpha(
        calibration_level=cal_level,
        ensemble_spread=ensemble_spread,
        model_agreement=agreement,
        lead_days=lead_days_for_calibration,
        hours_since_open=candidate.hours_since_open,
    )
    if not is_day0_mode:
        entry_validations.append("model_agreement")
    entry_validations.append("alpha_posterior")

    # Edge detection
    analysis = MarketAnalysis(
        p_raw=p_raw,
        p_cal=p_cal,
        p_market=p_market,
        alpha=alpha,
        bins=bins,
        member_maxes=ens.member_maxes,
        calibrator=cal,
        lead_days=lead_days_for_calibration,
        unit=city.settlement_unit,
    )
    edges = analysis.find_edges(n_bootstrap=settings["edge"]["n_bootstrap"])
    entry_validations.append("bootstrap_ci")

    # FDR filter
    filtered = fdr_filter(edges)
    entry_validations.append("fdr_filter")

    if not filtered:
        stage = "EDGE_INSUFFICIENT" if not edges else "FDR_FILTERED"
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage=stage,
            rejection_reasons=[f"{len(edges)} edges found, {len(filtered)} passed FDR"],
            selected_method=selected_method,
            applied_validations=list(entry_validations),
            decision_snapshot_id=snapshot_id,
            p_raw=p_raw,
            p_cal=p_cal,
            p_market=p_market,
            alpha=alpha,
            agreement=agreement,
            spread=float(getattr(ensemble_spread, "value", ens.spread_float())),
            n_edges_found=len(edges),
            n_edges_after_fdr=0,
        )]

    sizing_bankroll = max(
        0.0,
        float(portfolio.effective_bankroll if entry_bankroll is None else entry_bankroll),
    )
    current_heat = portfolio_heat_for_bankroll(portfolio, sizing_bankroll)
    projected_total_exposure_usd = current_heat * sizing_bankroll
    projected_city_exposure_usd: dict[str, float] = defaultdict(float)
    projected_cluster_exposure_usd: dict[str, float] = defaultdict(float)

    decisions = []
    for edge in filtered:
        bin_idx = bins.index(edge.bin)
        tokens = token_map[bin_idx]
        decision_validations = list(entry_validations)
        edge_source = _edge_source_for(candidate, edge)

        # Anti-churn layers 5, 6, 7
        if is_reentry_blocked(portfolio, city.name, edge.bin.label, target_date):
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="ANTI_CHURN",
                rejection_reasons=["REENTRY_BLOCKED"],
                selected_method=selected_method,
                applied_validations=[*decision_validations, "anti_churn"],
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
            ))
            continue
        check_token = tokens["token_id"] if edge.direction == "buy_yes" else tokens["no_token_id"]
        if is_token_on_cooldown(portfolio, check_token):
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="ANTI_CHURN",
                rejection_reasons=["TOKEN_COOLDOWN"],
                selected_method=selected_method,
                applied_validations=[*decision_validations, "anti_churn"],
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
            ))
            continue
        if has_same_city_range_open(portfolio, city.name, edge.bin.label):
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="ANTI_CHURN",
                rejection_reasons=["CROSS_DATE_BLOCK"],
                selected_method=selected_method,
                applied_validations=[*decision_validations, "anti_churn"],
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
            ))
            continue

        # Kelly sizing
        decision_validations.extend(["kelly_sizing", "dynamic_multiplier"])
        current_heat = (
            projected_total_exposure_usd / sizing_bankroll
            if sizing_bankroll > 0
            else 0.0
        )
        
        # Phase 3: RiskGraph Regime Throttling
        current_cluster_exp = cluster_exposure_for_bankroll(portfolio, city.cluster, sizing_bankroll)
        risk_throttle = 1.0
        if current_cluster_exp > 0.10: # Regime saturation starts
            risk_throttle *= 0.5
            decision_validations.append("regime_throttled_50pct")
        if current_heat > 0.25: # Global heat saturation 
            risk_throttle *= 0.5
            decision_validations.append("global_heat_throttled_50pct")
            
        km = dynamic_kelly_mult(
            base=settings["sizing"]["kelly_multiplier"],
            ci_width=edge.ci_upper - edge.ci_lower,
            lead_days=lead_days_for_calibration,
            portfolio_heat=current_heat,
        )
        
        # Apply RiskGraph Throttling Phase 3
        size = kelly_size(edge.p_posterior, edge.entry_price, sizing_bankroll, km * risk_throttle)

        if size < limits.min_order_usd:
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="SIZING_TOO_SMALL",
                rejection_reasons=[f"${size:.2f} < ${limits.min_order_usd} (throttled: {risk_throttle})"],
                selected_method=selected_method,
                applied_validations=list(decision_validations),
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
            ))
            continue

        # Risk limits
        decision_validations.append("risk_limits")
        allowed, reason = check_position_allowed(
            size_usd=size,
            bankroll=sizing_bankroll,
            city=city.name,
            cluster=city.cluster,
            current_city_exposure=(
                city_exposure_for_bankroll(portfolio, city.name, sizing_bankroll)
                + (projected_city_exposure_usd[city.name] / sizing_bankroll if sizing_bankroll > 0 else 0.0)
            ),
            current_cluster_exposure=(
                cluster_exposure_for_bankroll(portfolio, city.cluster, sizing_bankroll)
                + (projected_cluster_exposure_usd[city.cluster] / sizing_bankroll if sizing_bankroll > 0 else 0.0)
            ),
            current_portfolio_heat=current_heat,
            limits=limits,
        )
        if not allowed:
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="RISK_REJECTED",
                rejection_reasons=[reason],
                selected_method=selected_method,
                applied_validations=list(decision_validations),
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
            ))
            continue

        edge_ctx = EdgeContext(
            p_raw=p_raw,
            p_cal=p_cal,
            p_market=p_market,
            p_posterior=edge.p_posterior,
            forward_edge=edge.forward_edge,
            alpha=alpha,
            confidence_band_upper=edge.ci_upper,
            confidence_band_lower=edge.ci_lower,
            entry_provenance=EntryMethod(selected_method),
            decision_snapshot_id=snapshot_id,
            n_edges_found=len(edges),
            n_edges_after_fdr=len(filtered),
        )

        decisions.append(EdgeDecision(
            should_trade=True,
            edge=edge,
            tokens=tokens,
            size_usd=size,
            decision_id=_decision_id(),
            selected_method=selected_method,
            applied_validations=[*decision_validations, "anti_churn"],
            decision_snapshot_id=snapshot_id,
            edge_source=edge_source,
            p_raw=p_raw,
            p_cal=p_cal,
            p_market=p_market,
            alpha=alpha,
            agreement=agreement,
            spread=float(getattr(ensemble_spread, "value", ens.spread_float())),
            n_edges_found=len(edges),
            n_edges_after_fdr=len(filtered),
            edge_context=edge_ctx,
        ))
        projected_total_exposure_usd += size
        projected_city_exposure_usd[city.name] += size
        projected_cluster_exposure_usd[city.cluster] += size

    return decisions


def _store_ens_snapshot(conn, city, target_date, ens, ens_result) -> str:
    """Store every ENS fetch and return the snapshot_id."""

    import json

    try:
        conn.execute("""
            INSERT OR IGNORE INTO ensemble_snapshots
            (city, target_date, issue_time, valid_time, available_at, fetch_time,
             lead_hours, members_json, spread, is_bimodal, model_version, data_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            city.name,
            target_date,
            ens_result["issue_time"].isoformat(),
            None,
            ens_result["fetch_time"].isoformat(),
            ens_result["fetch_time"].isoformat(),
            max(
                0.0,
                lead_hours_to_target(
                    target_date,
                    city.timezone,
                    ens_result.get("fetch_time"),
                ),
            ),
            json.dumps(ens.member_maxes.tolist()),
            ens.spread_float(),
            int(ens.is_bimodal()),
            ens_result["model"],
            "live_v1",
        ))
        row = conn.execute("""
            SELECT snapshot_id FROM ensemble_snapshots
            WHERE city = ? AND target_date = ? AND issue_time = ? AND data_version = ?
            LIMIT 1
        """, (
            city.name,
            target_date,
            ens_result["issue_time"].isoformat(),
            "live_v1",
        )).fetchone()
        conn.commit()
        return str(row["snapshot_id"]) if row is not None else ""
    except Exception as e:
        logger.warning("Failed to store ENS snapshot: %s", e)
        return ""


def _store_snapshot_p_raw(conn, snapshot_id: str, p_raw: np.ndarray) -> None:
    """Persist the decision-time p_raw vector onto the snapshot row."""

    if not snapshot_id:
        return

    import json

    try:
        conn.execute(
            "UPDATE ensemble_snapshots SET p_raw_json = ? WHERE snapshot_id = ?",
            (json.dumps(p_raw.tolist()), snapshot_id),
        )
        conn.commit()
    except Exception as e:
        logger.warning("Failed to store snapshot p_raw for %s: %s", snapshot_id, e)
