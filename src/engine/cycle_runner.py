"""CycleRunner: pure orchestrator, < 50 lines, zero business logic.

Blueprint v2 §4: The runner doesn't know what an "edge" is, what buy_no means,
or how Platt works. It orchestrates the sequence. opening_hunt, update_reaction,
day0_capture are DiscoveryMode values, not separate code paths.
"""

import logging
from datetime import datetime, timezone

from src.config import settings
from src.data.market_scanner import find_weather_markets
from src.data.polymarket_client import PolymarketClient
from src.engine.discovery_mode import DiscoveryMode
from src.engine.evaluator import MarketCandidate, evaluate_candidate
from src.execution.executor import execute_order
from src.riskguard.risk_level import RiskLevel
from src.riskguard.riskguard import get_current_level
from src.state.db import get_connection
from src.state.portfolio import (
    Position, PortfolioState, load_portfolio, save_portfolio,
    add_position, close_position,
)
from src.strategy.risk_limits import RiskLimits

logger = logging.getLogger(__name__)

# Mode → scanner parameters
MODE_PARAMS = {
    DiscoveryMode.OPENING_HUNT: {"max_hours_since_open": 24, "min_hours_to_resolution": 24},
    DiscoveryMode.UPDATE_REACTION: {"min_hours_since_open": 24, "min_hours_to_resolution": 6},
    DiscoveryMode.DAY0_CAPTURE: {"max_hours_to_resolution": 6},
}


def run_cycle(mode: DiscoveryMode) -> dict:
    """Run one discovery cycle. Pure orchestration.

    Returns summary dict for logging/status.
    """
    summary = {"mode": mode.value, "started_at": datetime.now(timezone.utc).isoformat(),
                "monitors": 0, "exits": 0, "candidates": 0, "trades": 0, "no_trades": 0}

    # 1. Risk precheck
    risk_level = get_current_level()
    if risk_level in (RiskLevel.ORANGE, RiskLevel.RED):
        summary["skipped"] = f"risk_level={risk_level.value}"
        return summary

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

    # 2. MONITOR FIRST — protect existing value
    from src.execution.monitor import _refresh_position
    for pos in list(portfolio.positions):
        try:
            p_market, p_posterior = _refresh_position(conn, clob, pos)
            decision = pos.evaluate_exit(current_p_posterior=p_posterior,
                                          current_p_market=p_market)
            summary["monitors"] += 1
            if decision.should_exit:
                close_position(portfolio, pos.trade_id, p_market, decision.reason)
                summary["exits"] += 1
        except Exception as e:
            logger.error("Monitor failed for %s: %s", pos.trade_id, e)

    # 3. Scan for new (only if GREEN)
    if risk_level == RiskLevel.GREEN:
        params = MODE_PARAMS[mode]
        markets = find_weather_markets(
            min_hours_to_resolution=params.get("min_hours_to_resolution", 6),
        )
        # Apply mode-specific filtering
        if "max_hours_since_open" in params:
            markets = [m for m in markets if m["hours_since_open"] < params["max_hours_since_open"]]
        if "min_hours_since_open" in params:
            markets = [m for m in markets if m["hours_since_open"] >= params["min_hours_since_open"]]
        if "max_hours_to_resolution" in params:
            markets = [m for m in markets if m["hours_to_resolution"] < params["max_hours_to_resolution"]]

        from src.config import cities_by_name, cities_by_alias
        for market in markets:
            city = market.get("city")
            if city is None:
                continue
            candidate = MarketCandidate(
                city=city, target_date=market["target_date"],
                outcomes=market["outcomes"],
                hours_since_open=market["hours_since_open"],
                hours_to_resolution=market["hours_to_resolution"],
                event_id=market.get("event_id", ""),
            )
            summary["candidates"] += 1

            try:
                decisions = evaluate_candidate(candidate, conn, portfolio, clob, limits)
                for d in decisions:
                    if d.should_trade and d.edge and d.tokens:
                        result = execute_order(
                            d.edge, d.size_usd, mode=mode.value,
                            market_id=d.tokens["market_id"],
                            token_id=d.tokens["token_id"],
                            no_token_id=d.tokens["no_token_id"],
                        )
                        if result.status == "filled":
                            pos = Position(
                                trade_id=result.trade_id,
                                market_id=d.tokens["market_id"],
                                city=city.name, cluster=city.cluster,
                                target_date=candidate.target_date,
                                bin_label=d.edge.bin.label,
                                direction=d.edge.direction,
                                size_usd=d.size_usd,
                                entry_price=result.fill_price or d.edge.entry_price,
                                p_posterior=d.edge.p_posterior,
                                edge=d.edge.edge,
                                entered_at=datetime.now(timezone.utc).isoformat(),
                                token_id=d.tokens["token_id"],
                                no_token_id=d.tokens["no_token_id"],
                                strategy=d.edge.ev_per_dollar > 0 and "shoulder_sell" or "center_buy",
                                edge_source=getattr(d.edge, 'ev_per_dollar', '') and "favorite_longshot",
                                discovery_mode=mode.value,
                                market_hours_open=candidate.hours_since_open,
                            )
                            add_position(portfolio, pos)
                            summary["trades"] += 1
                    else:
                        summary["no_trades"] += 1
            except Exception as e:
                logger.error("Evaluation failed for %s %s: %s",
                             city.name, candidate.target_date, e)

    # 4. Save and cleanup
    if summary["trades"] > 0 or summary["exits"] > 0:
        save_portfolio(portfolio)
    conn.close()

    summary["completed_at"] = datetime.now(timezone.utc).isoformat()
    logger.info("Cycle %s: %d monitors, %d exits, %d candidates, %d trades",
                mode.value, summary["monitors"], summary["exits"],
                summary["candidates"], summary["trades"])
    return summary
