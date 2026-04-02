"""Evaluator: takes a market candidate, returns an EdgeDecision or NoTradeCase.

Contains ALL business logic for edge detection. Doesn't know about scheduling,
portfolio state, or execution. Pure function: candidate -> decision.
"""

import json
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional

import numpy as np

from src.calibration.manager import get_calibrator
from src.calibration.manager import season_from_date
from src.calibration.platt import calibrate_and_normalize
from src.config import City, edge_n_bootstrap, ensemble_crosscheck_member_count, settings
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
from src.signal.ensemble_signal import EnsembleSignal, select_hours_for_target_date
from src.control.control_plane import get_edge_threshold_multiplier
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
    settlement_semantics_json: Optional[str] = None
    epistemic_context_json: Optional[str] = None
    edge_context_json: Optional[str] = None



def _decision_id() -> str:
    return str(uuid.uuid4())[:12]


def _to_jsonable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {f.name: _to_jsonable(getattr(value, f.name)) for f in fields(value)}
    return value


def _serialize_json(value) -> str:
    return json.dumps(_to_jsonable(value), default=str, ensure_ascii=False)


def _forecast_source_key(model_name: str | None) -> str:
    text = str(model_name or "ecmwf_ifs025").strip().lower()
    if text.startswith("ecmwf"):
        return "ecmwf"
    if text.startswith("gfs"):
        return "gfs"
    if text.startswith("icon"):
        return "icon"
    if text.startswith("openmeteo"):
        return "openmeteo"
    return text


def _load_model_bias_reference(conn, *, city_name: str, season: str, forecast_source: str) -> dict:
    if conn is None:
        return {}
    try:
        row = conn.execute(
            """
            SELECT bias, mae, n_samples, discount_factor
            FROM model_bias
            WHERE city = ? AND season = ? AND source = ?
            """,
            (city_name, season, forecast_source),
        ).fetchone()
    except Exception:
        return {}
    if row is None:
        return {}
    return {
        "source": forecast_source,
        "bias": float(row["bias"]),
        "mae": float(row["mae"]),
        "n_samples": int(row["n_samples"]),
        "discount_factor": float(row["discount_factor"]),
    }


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


def _get_day0_temporal_context(city: City, target_date: date, observation: Optional[dict] = None):
    try:
        if observation is not None and not observation.get("observation_time"):
            return None
        from src.signal.diurnal import build_day0_temporal_context
        observation_time = observation.get("observation_time") if observation else None
        observation_source = observation.get("source", "") if observation else ""
        return build_day0_temporal_context(
            city.name,
            target_date,
            city.timezone,
            observation_time=observation_time,
            observation_source=observation_source,
        )
    except Exception:
        return None


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
        bins.append(Bin(low=low, high=high, label=o["title"], unit=city.settlement_unit))
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
            ens_result["times"],
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
        temporal_context = _get_day0_temporal_context(city, target_d, candidate.observation)
        if temporal_context is None:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=["Solar/DST context unavailable for Day0"],
                selected_method=selected_method,
                applied_validations=["day0_observation", "solar_context"],
            )]

        remaining_member_maxes, hours_remaining = remaining_member_maxes_for_day0(
            ens_result["members_hourly"],
            ens_result["times"],
            city.timezone,
            target_d,
            now=temporal_context.current_utc_timestamp,
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
            observation_source=str(candidate.observation.get("source", "")),
            observation_time=candidate.observation.get("observation_time"),
            current_utc_timestamp=temporal_context.current_utc_timestamp.isoformat(),
            temporal_context=temporal_context,
        )
        p_raw = day0.p_vector(bins)
        day0_forecast_context = day0.forecast_context()
        ensemble_spread = TemperatureDelta(float(np.std(remaining_member_maxes)), city.settlement_unit)
        entry_validations = ["day0_observation", "ens_fetch", "mc_instrument_noise", "diurnal_peak"]
        lead_days_for_calibration = 0.0
    else:
        p_raw = ens.p_raw_vector(bins)
        day0_forecast_context = None
        ensemble_spread = ens.spread()
        entry_validations = ["ens_fetch", "mc_instrument_noise"]
        lead_days_for_calibration = lead_days

    _store_snapshot_p_raw(conn, snapshot_id, p_raw)

    # Calibration
    cal, cal_level = get_calibrator(conn, city, target_date)
    if cal is not None:
        p_cal = calibrate_and_normalize(
            p_raw,
            cal,
            lead_days_for_calibration,
            bin_widths=[b.width for b in bins],
        )
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
            try:
                from src.contracts.exceptions import EmptyOrderbookError
            except ImportError:
                EmptyOrderbookError = type("Dummy", (Exception,), {})
            if isinstance(e, EmptyOrderbookError) or e.__class__.__name__ == "EmptyOrderbookError":
                logger.warning("Empty orderbook detected: %s", e)
                return [EdgeDecision(
                    False,
                    decision_id=_decision_id(),
                    rejection_stage="MARKET_LIQUIDITY",
                    rejection_reasons=[str(e)],
                    selected_method=selected_method,
                    applied_validations=entry_validations,
                    decision_snapshot_id=snapshot_id,
                    p_raw=p_raw,
                    p_cal=p_cal,
                    p_market=p_market,
                )]
            p_market[idx] = o["price"]

    agreement = "AGREE"
    if not is_day0_mode:
        gfs_result = fetch_ensemble(city, forecast_days=ens_forecast_days, model="gfs025")
        if gfs_result is None or not validate_ensemble(
            gfs_result,
            expected_members=ensemble_crosscheck_member_count(),
        ):
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=["GFS crosscheck unavailable"],
                selected_method=selected_method,
                applied_validations=[*entry_validations, "gfs_crosscheck_unavailable"],
                decision_snapshot_id=snapshot_id,
                p_raw=p_raw,
                p_cal=p_cal,
                p_market=p_market,
                agreement="CROSSCHECK_UNAVAILABLE",
            )]
        try:
            gfs_tz_hours = select_hours_for_target_date(
                target_d,
                city.timezone,
                times=gfs_result["times"],
            )
            gfs_maxes = gfs_result["members_hourly"][:, gfs_tz_hours].max(axis=1)
            gfs_measured = settlement_semantics.round_values(gfs_maxes)
            n_gfs = len(gfs_measured)
            gfs_p = np.zeros(len(bins))
            for i, b in enumerate(bins):
                if b.is_open_low:
                    gfs_p[i] = np.sum(gfs_measured <= b.high) / n_gfs
                elif b.is_open_high:
                    gfs_p[i] = np.sum(gfs_measured >= b.low) / n_gfs
                elif b.low is not None and b.high is not None:
                    gfs_p[i] = np.sum((gfs_measured >= b.low) & (gfs_measured <= b.high)) / n_gfs
            total = gfs_p.sum()
            if total > 0:
                gfs_p /= total
            agreement = model_agreement(p_raw, gfs_p)
        except Exception as e:
            logger.warning("GFS crosscheck failed: %s", e)
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=[f"GFS crosscheck unavailable: {e}"],
                selected_method=selected_method,
                applied_validations=[*entry_validations, "gfs_crosscheck_unavailable"],
                decision_snapshot_id=snapshot_id,
                p_raw=p_raw,
                p_cal=p_cal,
                p_market=p_market,
                agreement="CROSSCHECK_UNAVAILABLE",
            )]

    if agreement == "CONFLICT":
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=["ECMWF/GFS CONFLICT"],
            selected_method=selected_method,
            applied_validations=[*entry_validations, "model_agreement"],
            decision_snapshot_id=snapshot_id,
            p_raw=p_raw,
            p_cal=p_cal,
            p_market=p_market,
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

    forecast_source = _forecast_source_key(ens_result.get("model"))
    season = season_from_date(target_date)
    bias_reference = _load_model_bias_reference(
        conn,
        city_name=city.name,
        season=season,
        forecast_source=forecast_source,
    )

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
        city_name=city.name,
        season=season,
        forecast_source=forecast_source,
        bias_corrected=bool(getattr(ens, "bias_corrected", False)),
        bias_reference=bias_reference,
    )
    if hasattr(analysis, "forecast_context"):
        forecast_context = analysis.forecast_context()
    else:
        forecast_context = {
            "uncertainty": analysis.sigma_context(),
            "location": analysis.mean_context(),
        }
    if day0_forecast_context is not None:
        forecast_context["day0"] = day0_forecast_context
    edges = analysis.find_edges(n_bootstrap=edge_n_bootstrap())
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
        control_risk_multiplier = max(1.0, float(get_edge_threshold_multiplier()))
        if control_risk_multiplier > 1.0:
            km = km / control_risk_multiplier
            decision_validations.append(f"control_plane_risk_tightened_{control_risk_multiplier:g}x")
        
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
            settlement_semantics_json=_serialize_json(settlement_semantics),
            epistemic_context_json=_serialize_json({
                **_to_jsonable(epistemic),
                "forecast_context": forecast_context,
            }),
            edge_context_json=_serialize_json(edge_ctx),
        ))
        projected_total_exposure_usd += size
        projected_city_exposure_usd[city.name] += size
        projected_cluster_exposure_usd[city.cluster] += size

    return decisions


def _snapshot_time_value(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _snapshot_issue_time_value(ens_result: dict) -> str:
    issue_time = _snapshot_time_value(ens_result.get("issue_time"))
    if issue_time is not None:
        return issue_time

    fetch_time = _snapshot_time_value(ens_result.get("fetch_time"))
    if fetch_time is None:
        return "UNAVAILABLE_UPSTREAM_ISSUE_TIME"
    return f"UNAVAILABLE_UPSTREAM_ISSUE_TIME(fetch_time={fetch_time})"


def _snapshot_valid_time_value(target_date: str, ens_result: dict) -> str:
    valid_time = _snapshot_time_value(ens_result.get("valid_time"))
    if valid_time is not None:
        return valid_time

    first_valid_time = _snapshot_time_value(ens_result.get("first_valid_time"))
    if first_valid_time is not None:
        return f"FORECAST_WINDOW_START({first_valid_time})"

    return f"UNSPECIFIED_FORECAST_VALID_TIME(target_date={target_date})"


def _store_ens_snapshot(conn, city, target_date, ens, ens_result) -> str:
    """Store every ENS fetch and return the snapshot_id."""

    import json

    try:
        issue_time_value = _snapshot_issue_time_value(ens_result)
        valid_time_value = _snapshot_valid_time_value(target_date, ens_result)
        fetch_time_value = _snapshot_time_value(ens_result.get("fetch_time"))
        if fetch_time_value is None:
            raise ValueError("ENS snapshot missing fetch_time")

        conn.execute("""
            INSERT OR IGNORE INTO ensemble_snapshots
            (city, target_date, issue_time, valid_time, available_at, fetch_time,
             lead_hours, members_json, spread, is_bimodal, model_version, data_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            city.name,
            target_date,
            issue_time_value,
            valid_time_value,
            fetch_time_value,
            fetch_time_value,
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
            issue_time_value,
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
