"""Opening Hunt: Mode A discovery cycle. Spec §6.2.

Every 30 minutes:
1. Scan for markets opened < 24h ago
2. For each: fetch ENS → EnsembleSignal → calibrate → MarketAnalysis → FDR → Kelly → execute
3. All trades carry mandatory attribution fields

Opening Hunt targets the 6-24h window after market opening where:
- Opening prices are sticky (inertia)
- Bot competition is lowest
- Edge from ENS vs stale market prices is largest
"""

import logging
from datetime import date, datetime, timezone

import numpy as np

from src.calibration.manager import get_calibrator, route_to_bucket, season_from_date
from src.calibration.platt import calibrate_and_normalize
from src.config import settings
from src.data.ensemble_client import fetch_ensemble, validate_ensemble
from src.data.market_scanner import find_weather_markets
from src.data.polymarket_client import PolymarketClient
from src.execution.executor import execute_order
from src.riskguard.riskguard import get_current_level
from src.riskguard.risk_level import RiskLevel
from src.signal.ensemble_signal import EnsembleSignal
from src.signal.model_agreement import model_agreement
from src.state.chronicler import log_event
from src.state.db import get_connection
from src.state.portfolio import (
    Position, PortfolioState, load_portfolio, save_portfolio,
    add_position, portfolio_heat, city_exposure, cluster_exposure,
)
from src.strategy.fdr_filter import fdr_filter
from src.strategy.kelly import kelly_size, dynamic_kelly_mult
from src.strategy.market_analysis import MarketAnalysis
from src.strategy.market_fusion import compute_alpha, vwmp
from src.strategy.risk_limits import RiskLimits, check_position_allowed
from src.types import Bin

logger = logging.getLogger(__name__)


def run_opening_hunt() -> int:
    """Run one Opening Hunt cycle. Returns number of trades placed."""
    # RiskGuard gate
    level = get_current_level()
    if level in (RiskLevel.YELLOW, RiskLevel.ORANGE, RiskLevel.RED):
        logger.info("Opening Hunt skipped: RiskGuard=%s", level.value)
        return 0

    # Discover markets
    markets = find_weather_markets(
        min_hours_to_resolution=settings["discovery"]["min_hours_to_resolution"],
    )

    # Filter: only markets opened < 24h ago
    fresh = [m for m in markets if m["hours_since_open"] < 24]
    logger.info("Opening Hunt: %d fresh markets (of %d total)", len(fresh), len(markets))

    if not fresh:
        return 0

    conn = get_connection()
    portfolio = load_portfolio()
    clob = PolymarketClient(paper_mode=(settings.mode == "paper"))
    limits = RiskLimits(
        max_single_position_pct=settings["sizing"]["max_single_position_pct"],
        max_portfolio_heat_pct=settings["sizing"]["max_portfolio_heat_pct"],
        max_correlated_pct=settings["sizing"]["max_correlated_pct"],
        max_city_pct=settings["sizing"]["max_city_pct"],
        max_region_pct=settings["sizing"]["max_region_pct"],
        min_order_usd=settings["sizing"]["min_order_usd"],
    )

    trades_placed = 0

    for market in fresh:
        try:
            n = _process_market(conn, market, portfolio, clob, limits)
            trades_placed += n
        except Exception as e:
            logger.error("Opening Hunt error for %s %s: %s",
                         market["city"].name, market["target_date"], e)

    if trades_placed > 0:
        save_portfolio(portfolio)

    conn.close()
    logger.info("Opening Hunt complete: %d trades placed", trades_placed)
    return trades_placed


def _process_market(
    conn, market: dict, portfolio: PortfolioState,
    clob: PolymarketClient, limits: RiskLimits,
) -> int:
    """Process one market through the full edge detection pipeline."""
    city = market["city"]
    target_date = market["target_date"]
    outcomes = market["outcomes"]

    # Build bins from outcomes
    bins = []
    token_map = {}  # bin_index → {token_id, no_token_id, market_id}
    for i, o in enumerate(outcomes):
        low = o["range_low"]
        high = o["range_high"]
        bins.append(Bin(low=low, high=high, label=o["title"]))
        token_map[i] = {
            "token_id": o["token_id"],
            "no_token_id": o["no_token_id"],
            "market_id": o["market_id"],
        }

    if len(bins) < 3:
        return 0

    # Fetch ENS
    ens_result = fetch_ensemble(city, forecast_days=8)
    if ens_result is None or not validate_ensemble(ens_result):
        return 0

    target_d = date.fromisoformat(target_date)
    try:
        ens = EnsembleSignal(ens_result["members_hourly"], city, target_d)
    except ValueError as e:
        logger.warning("EnsembleSignal failed for %s %s: %s", city.name, target_date, e)
        return 0

    # Compute P_raw
    np.random.seed(None)  # Fresh seed for live trading
    p_raw = ens.p_raw_vector(bins)

    # Calibration
    cal, cal_level = get_calibrator(conn, city, target_date)
    if cal is not None:
        lead_days = (target_d - date.today()).days
        p_cal = calibrate_and_normalize(p_raw, cal, float(lead_days))
    else:
        p_cal = p_raw.copy()

    # Market prices via VWMP
    p_market = np.zeros(len(bins))
    for i, o in enumerate(outcomes):
        try:
            bid, ask, bid_sz, ask_sz = clob.get_best_bid_ask(o["token_id"])
            p_market[i] = vwmp(bid, ask, bid_sz, ask_sz)
        except Exception as e:
            # CLOB orderbook unavailable — use Gamma API snapshot price.
            # Logged because VWMP is the required price source (CLAUDE.md).
            logger.warning("VWMP fetch failed for %s bin %d: %s. Using Gamma price %.3f",
                           city.name, i, e, o["price"])
            p_market[i] = o["price"]

    # Model agreement (GFS crosscheck)
    gfs_result = fetch_ensemble(city, forecast_days=8, model="gfs025")
    agreement = "AGREE"
    if gfs_result is not None and validate_ensemble(gfs_result, expected_members=31):
        try:
            gfs_ens = EnsembleSignal(gfs_result["members_hourly"], city, target_d)
            gfs_p = gfs_ens.p_raw_vector(bins)
            agreement = model_agreement(p_raw, gfs_p)
        except ValueError:
            pass

    if agreement == "CONFLICT":
        logger.info("Skipping %s %s: ECMWF/GFS CONFLICT", city.name, target_date)
        return 0

    # Compute alpha
    alpha = compute_alpha(
        calibration_level=cal_level,
        ensemble_spread=ens.spread(),
        model_agreement=agreement,
        lead_days=float((target_d - date.today()).days),
        hours_since_open=market["hours_since_open"],
    )

    # Edge detection
    analysis = MarketAnalysis(
        p_raw=p_raw, p_cal=p_cal, p_market=p_market,
        alpha=alpha, bins=bins, member_maxes=ens.member_maxes,
        calibrator=cal, lead_days=float((target_d - date.today()).days),
    )
    edges = analysis.find_edges(n_bootstrap=settings["edge"]["n_bootstrap"])

    # FDR filter
    filtered = fdr_filter(edges)
    if not filtered:
        return 0

    # Size and execute
    trades = 0
    for edge in filtered:
        bin_idx = bins.index(edge.bin)
        tokens = token_map[bin_idx]

        # Dynamic Kelly
        km = dynamic_kelly_mult(
            base=settings["sizing"]["kelly_multiplier"],
            ci_width=edge.ci_upper - edge.ci_lower,
            lead_days=float((target_d - date.today()).days),
            portfolio_heat=portfolio_heat(portfolio),
        )
        size = kelly_size(edge.p_posterior, edge.entry_price, portfolio.bankroll, km)

        if size < limits.min_order_usd:
            continue

        # Risk limits check
        allowed, reason = check_position_allowed(
            size_usd=size, bankroll=portfolio.bankroll,
            city=city.name, cluster=city.cluster,
            current_city_exposure=city_exposure(portfolio, city.name),
            current_cluster_exposure=cluster_exposure(portfolio, city.cluster),
            current_portfolio_heat=portfolio_heat(portfolio),
            limits=limits,
        )
        if not allowed:
            logger.info("Position blocked: %s", reason)
            continue

        # Execute
        result = execute_order(edge, size, mode="opening_hunt", market_id=tokens["market_id"])

        if result.status == "filled":
            pos = Position(
                trade_id=result.trade_id,
                market_id=tokens["market_id"],
                city=city.name, cluster=city.cluster,
                target_date=target_date, bin_label=edge.bin.label,
                direction=edge.direction, size_usd=size,
                entry_price=result.fill_price or edge.entry_price,
                p_posterior=edge.p_posterior, edge=edge.edge,
                entered_at=datetime.now(timezone.utc).isoformat(),
                edge_source="opening_inertia",
                discovery_mode="opening_hunt",
                market_hours_open=market["hours_since_open"],
            )
            add_position(portfolio, pos)
            log_event(conn, "ENTRY", result.trade_id, {
                "city": city.name, "bin": edge.bin.label,
                "direction": edge.direction, "size": size,
                "edge": edge.edge, "alpha": alpha,
            })
            conn.commit()
            trades += 1

    return trades
