"""Evaluator: takes a market candidate, returns an EdgeDecision or NoTradeCase.

Contains ALL business logic for edge detection. Doesn't know about scheduling,
portfolio state, or execution. Pure function: candidate → decision.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np

from src.calibration.manager import get_calibrator
from src.calibration.platt import calibrate_and_normalize
from src.config import settings, City
from src.data.ensemble_client import fetch_ensemble, validate_ensemble
from src.data.polymarket_client import PolymarketClient
from src.signal.ensemble_signal import EnsembleSignal, sigma_instrument
from src.signal.model_agreement import model_agreement
from src.strategy.fdr_filter import fdr_filter
from src.strategy.kelly import kelly_size, dynamic_kelly_mult
from src.strategy.market_analysis import MarketAnalysis
from src.strategy.market_fusion import compute_alpha, vwmp
from src.strategy.risk_limits import RiskLimits, check_position_allowed
from src.state.portfolio import (
    PortfolioState, portfolio_heat, city_exposure, cluster_exposure,
    is_reentry_blocked, is_token_on_cooldown, has_same_city_range_open,
)
from src.types import Bin, BinEdge

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


@dataclass
class EdgeDecision:
    """Result of evaluating a candidate. Either trade or no-trade."""
    should_trade: bool
    edge: Optional[BinEdge] = None
    tokens: Optional[dict] = None
    size_usd: float = 0.0
    rejection_stage: str = ""
    rejection_reasons: list[str] = field(default_factory=list)
    # Signal data for decision chain recording
    p_raw: Optional[np.ndarray] = None
    p_cal: Optional[np.ndarray] = None
    p_market: Optional[np.ndarray] = None
    alpha: float = 0.0
    agreement: str = "AGREE"
    spread: float = 0.0
    n_edges_found: int = 0
    n_edges_after_fdr: int = 0


def evaluate_candidate(
    candidate: MarketCandidate,
    conn,
    portfolio: PortfolioState,
    clob: PolymarketClient,
    limits: RiskLimits,
) -> list[EdgeDecision]:
    """Evaluate a market candidate through the full signal pipeline.

    Returns list of EdgeDecisions (one per tradeable edge found, plus NoTradeCases).
    The CycleRunner iterates these and executes the ones with should_trade=True.
    """
    city = candidate.city
    target_date = candidate.target_date
    outcomes = candidate.outcomes

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
        return [EdgeDecision(False, rejection_stage="MARKET_FILTER",
                              rejection_reasons=["< 3 parseable bins"])]

    # Fetch ENS
    ens_result = fetch_ensemble(city, forecast_days=8)
    if ens_result is None or not validate_ensemble(ens_result):
        return [EdgeDecision(False, rejection_stage="SIGNAL_QUALITY",
                              rejection_reasons=["ENS fetch failed or < 51 members"])]

    target_d = date.fromisoformat(target_date)
    try:
        ens = EnsembleSignal(ens_result["members_hourly"], city, target_d)
    except ValueError as e:
        return [EdgeDecision(False, rejection_stage="SIGNAL_QUALITY",
                              rejection_reasons=[str(e)])]

    # Store ENS snapshot (time-irreversible data collection)
    _store_ens_snapshot(conn, city, target_date, ens, ens_result)

    # Compute P_raw
    p_raw = ens.p_raw_vector(bins)

    # Calibration
    cal, cal_level = get_calibrator(conn, city, target_date)
    lead_days = float((target_d - date.today()).days)
    if cal is not None:
        p_cal = calibrate_and_normalize(p_raw, cal, lead_days)
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
        except Exception:
            p_market[idx] = o["price"]

    # GFS crosscheck (P0-3 fix: direct member counting, no EnsembleSignal)
    gfs_result = fetch_ensemble(city, forecast_days=8, model="gfs025")
    agreement = "AGREE"
    if gfs_result is not None and validate_ensemble(gfs_result, expected_members=31):
        try:
            gfs_maxes = gfs_result["members_hourly"][:, :min(24, gfs_result["members_hourly"].shape[1])].max(axis=1)
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
        return [EdgeDecision(False, rejection_stage="SIGNAL_QUALITY",
                              rejection_reasons=["ECMWF/GFS CONFLICT"],
                              agreement=agreement)]

    # Compute alpha
    alpha = compute_alpha(
        calibration_level=cal_level,
        ensemble_spread=ens.spread(),
        model_agreement=agreement,
        lead_days=lead_days,
        hours_since_open=candidate.hours_since_open,
    )

    # Edge detection
    analysis = MarketAnalysis(
        p_raw=p_raw, p_cal=p_cal, p_market=p_market,
        alpha=alpha, bins=bins, member_maxes=ens.member_maxes,
        calibrator=cal, lead_days=lead_days, unit=city.settlement_unit,
    )
    edges = analysis.find_edges(n_bootstrap=settings["edge"]["n_bootstrap"])

    # FDR filter
    filtered = fdr_filter(edges)

    if not filtered:
        stage = "EDGE_INSUFFICIENT" if not edges else "FDR_FILTERED"
        return [EdgeDecision(
            False, rejection_stage=stage,
            rejection_reasons=[f"{len(edges)} edges found, {len(filtered)} passed FDR"],
            p_raw=p_raw, p_cal=p_cal, p_market=p_market,
            alpha=alpha, agreement=agreement,
            spread=ens.spread_float(),
            n_edges_found=len(edges), n_edges_after_fdr=0,
        )]

    # Size and check risk for each edge
    decisions = []
    for edge in filtered:
        bin_idx = bins.index(edge.bin)
        tokens = token_map[bin_idx]

        # Anti-churn layers 5, 6, 7
        if is_reentry_blocked(portfolio, city.name, edge.bin.label, target_date):
            decisions.append(EdgeDecision(False, edge=edge, rejection_stage="ANTI_CHURN",
                                           rejection_reasons=["REENTRY_BLOCKED"]))
            continue
        check_token = tokens["token_id"] if edge.direction == "buy_yes" else tokens["no_token_id"]
        if is_token_on_cooldown(portfolio, check_token):
            decisions.append(EdgeDecision(False, edge=edge, rejection_stage="ANTI_CHURN",
                                           rejection_reasons=["TOKEN_COOLDOWN"]))
            continue
        if has_same_city_range_open(portfolio, city.name, edge.bin.label):
            decisions.append(EdgeDecision(False, edge=edge, rejection_stage="ANTI_CHURN",
                                           rejection_reasons=["CROSS_DATE_BLOCK"]))
            continue

        # Kelly sizing
        km = dynamic_kelly_mult(
            base=settings["sizing"]["kelly_multiplier"],
            ci_width=edge.ci_upper - edge.ci_lower,
            lead_days=lead_days,
            portfolio_heat=portfolio_heat(portfolio),
        )
        size = kelly_size(edge.p_posterior, edge.entry_price, portfolio.bankroll, km)

        if size < limits.min_order_usd:
            decisions.append(EdgeDecision(False, edge=edge, rejection_stage="SIZING_TOO_SMALL",
                                           rejection_reasons=[f"${size:.2f} < ${limits.min_order_usd}"]))
            continue

        # Risk limits
        allowed, reason = check_position_allowed(
            size_usd=size, bankroll=portfolio.bankroll,
            city=city.name, cluster=city.cluster,
            current_city_exposure=city_exposure(portfolio, city.name),
            current_cluster_exposure=cluster_exposure(portfolio, city.cluster),
            current_portfolio_heat=portfolio_heat(portfolio),
            limits=limits,
        )
        if not allowed:
            decisions.append(EdgeDecision(False, edge=edge, rejection_stage="RISK_REJECTED",
                                           rejection_reasons=[reason]))
            continue

        # All gates passed — trade!
        decisions.append(EdgeDecision(
            should_trade=True, edge=edge, tokens=tokens, size_usd=size,
            p_raw=p_raw, p_cal=p_cal, p_market=p_market,
            alpha=alpha, agreement=agreement,
            spread=ens.spread_float(),
            n_edges_found=len(edges), n_edges_after_fdr=len(filtered),
        ))

    return decisions


def _store_ens_snapshot(conn, city, target_date, ens, ens_result):
    """Store every ENS fetch — irreversible time window."""
    import json
    try:
        conn.execute("""
            INSERT OR IGNORE INTO ensemble_snapshots
            (city, target_date, issue_time, valid_time, available_at, fetch_time,
             lead_hours, members_json, spread, is_bimodal, model_version, data_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            city.name, target_date,
            ens_result["issue_time"].isoformat(),
            target_date + "T12:00:00Z",
            ens_result["fetch_time"].isoformat(),
            ens_result["fetch_time"].isoformat(),
            float((date.fromisoformat(target_date) - date.today()).days * 24),
            json.dumps(ens.member_maxes.tolist()),
            ens.spread_float(),
            int(ens.is_bimodal()),
            ens_result["model"], "live_v1",
        ))
        conn.commit()
    except Exception as e:
        logger.warning("Failed to store ENS snapshot: %s", e)
