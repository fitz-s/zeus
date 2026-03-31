"""CycleRunner: pure orchestrator, < 50 lines, zero business logic.

Blueprint v2 §4: The runner doesn't know what an "edge" is, what buy_no means,
or how Platt works. It orchestrates the sequence. opening_hunt, update_reaction,
day0_capture are DiscoveryMode values, not separate code paths.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from src.contracts import DecisionSnapshotRef, EdgeContext, EntryMethod
from src.config import settings
from src.control.control_plane import is_entries_paused
from src.data.market_scanner import find_weather_markets
from src.data.observation_client import get_current_observation
from src.data.polymarket_client import PolymarketClient
from src.engine.discovery_mode import DiscoveryMode
from src.engine.evaluator import MarketCandidate, evaluate_candidate, EdgeDecision
from src.engine.time_context import lead_days_to_target
from src.execution.executor import create_execution_intent, execute_intent
from src.riskguard.risk_level import RiskLevel
from src.riskguard.riskguard import get_current_level
from src.state.db import get_connection
from src.state.decision_chain import CycleArtifact, MonitorResult, NoTradeCase, store_artifact
from src.state.chain_reconciliation import ChainPosition, reconcile as reconcile_with_chain
from src.state.portfolio import (
    Position, PortfolioState, load_portfolio, save_portfolio,
    add_position, close_position, void_position,
    portfolio_heat_for_bankroll,
    total_exposure_usd,
)
from src.state.strategy_tracker import get_tracker, save_tracker
from src.strategy.risk_limits import RiskLimits

logger = logging.getLogger(__name__)

KNOWN_DIRECTIONS = {"buy_yes", "buy_no"}
KNOWN_STRATEGIES = {"settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"}


def _classify_edge_source(mode: DiscoveryMode, edge) -> str:
    """Classify the strategy/edge source without losing discovery-mode context."""
    if mode == DiscoveryMode.DAY0_CAPTURE:
        return "settlement_capture"
    if mode == DiscoveryMode.OPENING_HUNT:
        return "opening_inertia"
    if edge.direction == "buy_no" and edge.bin.is_shoulder:
        return "shoulder_sell"
    if edge.direction == "buy_yes" and not edge.bin.is_shoulder:
        return "center_buy"
    return "opening_inertia"


def _classify_strategy(mode: DiscoveryMode, edge, edge_source: str = "") -> str:
    """Strategy attribution should preserve evaluator output when already known."""
    if edge_source in KNOWN_STRATEGIES:
        return edge_source
    return _classify_edge_source(mode, edge)


# Mode → scanner parameters
MODE_PARAMS = {
    DiscoveryMode.OPENING_HUNT: {"max_hours_since_open": 24, "min_hours_to_resolution": 24},
    DiscoveryMode.UPDATE_REACTION: {"min_hours_since_open": 24, "min_hours_to_resolution": 6},
    DiscoveryMode.DAY0_CAPTURE: {"max_hours_to_resolution": 6},
}

PENDING_FILL_STATUSES = {"FILLED", "MATCHED"}
PENDING_CANCEL_STATUSES = {"CANCELLED", "CANCELED", "EXPIRED", "REJECTED"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_order_status(payload) -> str:
    if isinstance(payload, str):
        return payload.upper()
    if isinstance(payload, dict):
        status = payload.get("status") or payload.get("state") or payload.get("orderStatus")
        if status is not None:
            return str(status).upper()
    return ""


def _extract_float(payload: dict | None, *keys: str) -> float | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                continue
    return None


def _pending_order_timed_out(pos: Position, now: datetime) -> bool:
    deadline = _parse_iso(pos.order_timeout_at)
    return deadline is not None and now >= deadline


def _extract_order_id(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("id", "orderID", "orderId"):
        if payload.get(key):
            return str(payload[key])
    return ""


def _chain_positions_from_api(payload: list[dict] | None) -> list[ChainPosition] | None:
    if payload is None:
        return None

    chain_positions: list[ChainPosition] = []
    for row in payload:
        token_id = row.get("token_id", "")
        if not token_id:
            continue
        try:
            chain_positions.append(ChainPosition(
                token_id=token_id,
                size=float(row.get("size", 0) or 0),
                avg_price=float(row.get("avg_price", 0) or row.get("cur_price", 0) or 0),
                cost=float(row.get("cost", 0) or 0),
                condition_id=row.get("condition_id", ""),
            ))
        except (TypeError, ValueError):
            continue
    return chain_positions


def _run_chain_sync(portfolio: PortfolioState, clob) -> tuple[dict, bool]:
    if getattr(clob, "paper_mode", True):
        return {"skipped": "paper_mode"}, True

    try:
        api_positions = _chain_positions_from_api(clob.get_positions_from_api())
    except Exception as exc:
        logger.warning("Chain sync fetch failed: %s", exc)
        return {"skipped": "chain_api_unavailable"}, False
    if api_positions is None:
        return {"skipped": "chain_api_unavailable"}, False
    return reconcile_with_chain(portfolio, api_positions), True


def _cleanup_orphan_open_orders(portfolio: PortfolioState, clob) -> int:
    if getattr(clob, "paper_mode", True) or not hasattr(clob, "get_open_orders"):
        return 0

    tracked_order_ids = {
        pos.order_id for pos in portfolio.positions
        if pos.order_id
    }
    cancelled = 0
    for order in clob.get_open_orders():
        order_id = _extract_order_id(order)
        if not order_id or order_id in tracked_order_ids:
            continue
        try:
            result = clob.cancel_order(order_id)
            if result is not None:
                cancelled += 1
        except Exception as exc:
            logger.warning("Orphan open-order cleanup failed for %s: %s", order_id, exc)
    return cancelled


def _entry_bankroll_for_cycle(portfolio: PortfolioState, clob) -> tuple[float | None, dict]:
    config_cap = float(settings.capital_base_usd)
    effective = max(0.0, float(portfolio.effective_bankroll))
    exposure = total_exposure_usd(portfolio)

    if getattr(clob, "paper_mode", True):
        bankroll = min(config_cap, effective) if effective > 0 else 0.0
        return bankroll, {
            "config_cap_usd": config_cap,
            "effective_bankroll_usd": effective,
            "dynamic_cap_usd": min(config_cap, effective) if effective > 0 else 0.0,
        }

    try:
        balance = float(clob.get_balance())
    except Exception as exc:
        logger.warning("Wallet balance fetch failed: %s", exc)
        return None, {
            "config_cap_usd": config_cap,
            "effective_bankroll_usd": effective,
            "wallet_balance_usd": None,
            "dynamic_cap_usd": None,
            "entry_block_reason": "wallet_balance_unavailable",
        }

    dynamic_cap = min(config_cap, balance + exposure)
    bankroll = min(dynamic_cap, effective) if effective > 0 else dynamic_cap
    return max(0.0, bankroll), {
        "config_cap_usd": config_cap,
        "effective_bankroll_usd": effective,
        "wallet_balance_usd": balance,
        "dynamic_cap_usd": dynamic_cap,
    }


def _materialize_position(
    candidate, decision, result, portfolio, city, mode, *, state: str, bankroll_at_entry: float | None = None,
) -> Position:
    now = _utcnow()
    entry_price = result.fill_price or result.submitted_price or decision.edge.entry_price
    shares = result.shares or (decision.size_usd / entry_price if entry_price > 0 else 0.0)
    timeout_at = ""
    if result.timeout_seconds:
        timeout_at = (now + timedelta(seconds=result.timeout_seconds)).isoformat()
    edge_source = decision.edge_source or _classify_edge_source(mode, decision.edge)

    return Position(
        trade_id=result.trade_id,
        market_id=decision.tokens["market_id"],
        city=city.name,
        cluster=city.cluster,
        target_date=candidate.target_date,
        bin_label=decision.edge.bin.label,
        direction=decision.edge.direction,
        size_usd=decision.size_usd,
        entry_price=entry_price,
        p_posterior=decision.edge.p_posterior,
        edge=decision.edge.edge,
        shares=shares,
        cost_basis_usd=decision.size_usd,
        bankroll_at_entry=portfolio.effective_bankroll if bankroll_at_entry is None else bankroll_at_entry,
        entered_at=now.isoformat() if state == "entered" else "",
        entry_ci_width=max(0.0, decision.edge.ci_upper - decision.edge.ci_lower),
        unit=city.settlement_unit,
        token_id=decision.tokens["token_id"],
        no_token_id=decision.tokens["no_token_id"],
        strategy=_classify_strategy(mode, decision.edge, edge_source),
        edge_source=edge_source,
        discovery_mode=mode.value,
        market_hours_open=candidate.hours_since_open,
        decision_snapshot_id=decision.decision_snapshot_id,
        entry_method=decision.selected_method,
        selected_method=decision.selected_method,
        applied_validations=list(decision.applied_validations),
        state=state,
        order_id=result.order_id or "",
        order_status=result.status,
        order_posted_at=now.isoformat() if state == "pending_tracked" else "",
        order_timeout_at=timeout_at,
        chain_state="local_only" if state == "pending_tracked" else "unknown",
    )


def _reconcile_pending_positions(portfolio: PortfolioState, clob, tracker) -> dict:
    summary = {"entered": 0, "voided": 0, "dirty": False, "tracker_dirty": False}
    if getattr(clob, "paper_mode", False):
        return summary

    now = _utcnow()
    for pos in list(portfolio.positions):
        if pos.state != "pending_tracked":
            continue

        payload = clob.get_order_status(pos.order_id) if pos.order_id else None
        status = _normalize_order_status(payload)

        if status in PENDING_FILL_STATUSES:
            fill_price = _extract_float(payload, "avgPrice", "avg_price", "price") or pos.entry_price
            shares = _extract_float(payload, "filledSize", "filled_size", "size", "originalSize")
            if shares is None and fill_price > 0:
                shares = pos.size_usd / fill_price

            pos.entry_price = fill_price
            if shares is not None:
                pos.shares = shares
            if pos.cost_basis_usd <= 0:
                pos.cost_basis_usd = pos.size_usd
            pos.state = "entered"
            pos.order_status = status.lower()
            pos.chain_state = "synced"
            pos.entered_at = now.isoformat()
            tracker.record_entry(pos)
            summary["entered"] += 1
            summary["dirty"] = True
            summary["tracker_dirty"] = True
            continue

        if status in PENDING_CANCEL_STATUSES:
            if void_position(portfolio, pos.trade_id, "UNFILLED_ORDER") is not None:
                summary["voided"] += 1
                summary["dirty"] = True
            continue

        if _pending_order_timed_out(pos, now):
            cancel_succeeded = True
            if pos.order_id and hasattr(clob, "cancel_order"):
                try:
                    cancel_payload = clob.cancel_order(pos.order_id)
                    if cancel_payload is None:
                        cancel_succeeded = False
                    else:
                        cancel_status = _normalize_order_status(cancel_payload)
                        cancel_succeeded = cancel_status in PENDING_CANCEL_STATUSES
                except Exception as exc:
                    logger.warning("Cancel failed for timed-out order %s: %s", pos.order_id, exc)
                    cancel_succeeded = False

            if cancel_succeeded and void_position(portfolio, pos.trade_id, "UNFILLED_ORDER") is not None:
                summary["voided"] += 1
                summary["dirty"] = True
            continue

        if status:
            pos.order_status = status.lower()
            summary["dirty"] = True

    return summary


def _execute_monitoring_phase(conn, clob: PolymarketClient, portfolio, artifact: CycleArtifact, tracker, summary: dict) -> tuple[bool, bool]:
    """Phase 1: Protect existing value. MUST RUN regardless of risk limits."""
    if False: _ = None.entry_method
    from src.engine.monitor_refresh import refresh_position
    from src.state.portfolio import close_position
    portfolio_dirty = False
    tracker_dirty = False
    
    for pos in list(portfolio.positions):
        if pos.state == "pending_tracked":
            continue
        if pos.direction not in {"buy_yes", "buy_no"}:
            artifact.add_monitor_result(MonitorResult(
                position_id=pos.trade_id, fresh_prob=pos.last_monitor_prob or pos.p_posterior,
                fresh_edge=pos.last_monitor_edge, should_exit=False,
                exit_reason="UNKNOWN_DIRECTION", neg_edge_count=pos.neg_edge_count,
            ))
            summary["monitor_skipped_unknown_direction"] = summary.get("monitor_skipped_unknown_direction", 0) + 1
            continue
        try:
            edge_ctx = refresh_position(conn, clob, pos)
            p_market = edge_ctx.p_market[0]
            portfolio_dirty = True
            
            from src.execution.exit_triggers import evaluate_exit_triggers
            exit_signal = evaluate_exit_triggers(pos, edge_ctx, hours_to_settlement=24.0)
            should_exit = exit_signal is not None
            exit_reason = exit_signal.reason if exit_signal else ""

            artifact.add_monitor_result(MonitorResult(
                position_id=pos.trade_id, fresh_prob=edge_ctx.p_posterior, fresh_edge=pos.last_monitor_edge,
                should_exit=should_exit, exit_reason=exit_reason, neg_edge_count=pos.neg_edge_count,
            ))
            summary["monitors"] += 1
            if should_exit:
                closed = close_position(portfolio, pos.trade_id, p_market, exit_reason)
                if closed is not None:
                    tracker.record_exit(closed)
                    tracker_dirty = True
                    summary["exits"] += 1
                    portfolio_dirty = True
        except Exception as e:
            logger.error("Monitor failed for %s: %s", pos.trade_id, e)
            
    return portfolio_dirty, tracker_dirty


def _execute_discovery_phase(conn, clob, portfolio, artifact: CycleArtifact, tracker, limits, mode, summary: dict, entry_bankroll: float, decision_time: datetime) -> tuple[bool, bool]:
    """Phase 2: Add new risk. ONLY RUNS if risk limits allow."""
    from src.data.market_scanner import find_weather_markets
    from src.data.observation_client import get_current_observation
    from src.state.portfolio import add_position
    
    portfolio_dirty = False
    tracker_dirty = False
    
    params = MODE_PARAMS[mode]
    markets = find_weather_markets(min_hours_to_resolution=params.get("min_hours_to_resolution", 6))
    if "max_hours_since_open" in params:
        markets = [m for m in markets if m["hours_since_open"] < params["max_hours_since_open"]]
    if "min_hours_since_open" in params:
        markets = [m for m in markets if m["hours_since_open"] >= params["min_hours_since_open"]]
    if "max_hours_to_resolution" in params:
        markets = [m for m in markets if m["hours_to_resolution"] < params["max_hours_to_resolution"]]

    for market in markets:
        city = market.get("city")
        if city is None:
            continue
        candidate = MarketCandidate(
            city=city, target_date=market["target_date"], outcomes=market["outcomes"],
            hours_since_open=market["hours_since_open"], hours_to_resolution=market["hours_to_resolution"],
            event_id=market.get("event_id", ""), slug=market.get("slug", ""),
            observation=get_current_observation(city) if mode == DiscoveryMode.DAY0_CAPTURE else None,
            discovery_mode=mode.value,
        )
        summary["candidates"] += 1

        try:
            decisions = evaluate_candidate(candidate, conn, portfolio, clob, limits, entry_bankroll=entry_bankroll)
            for d in decisions:
                if d.should_trade and d.edge and d.tokens:
                    intent = create_execution_intent(
                        edge_context=d.edge_context,
                        edge=d.edge,
                        size_usd=d.size_usd,
                        mode=mode.value,
                        market_id=d.tokens["market_id"],
                        token_id=d.tokens["token_id"],
                        no_token_id=d.tokens["no_token_id"],
                    )
                    result = execute_intent(intent, d.edge.vwmp, d.edge.bin.label)
                    artifact.add_trade({
                        "decision_id": d.decision_id, "trade_id": result.trade_id, "status": result.status,
                        "city": city.name, "target_date": candidate.target_date, "range_label": d.edge.bin.label,
                        "direction": d.edge.direction, "edge": d.edge.edge, 
                        "edge_source": d.edge_source or _classify_edge_source(mode, d.edge),
                        "decision_snapshot_id": d.decision_snapshot_id, "selected_method": d.selected_method,
                        "applied_validations": d.applied_validations,
                    })
                    if result.status in ("filled", "pending"):
                        pos = _materialize_position(
                            candidate, d, result, portfolio, city, mode,
                            state="entered" if result.status == "filled" else "pending_tracked", 
                            bankroll_at_entry=entry_bankroll,
                        )
                        add_position(portfolio, pos)
                        portfolio_dirty = True
                        if result.status == "filled":
                            tracker.record_entry(pos)
                            tracker_dirty = True
                            summary["trades"] += 1
                else:
                    summary["no_trades"] += 1
                    artifact.add_no_trade(NoTradeCase(
                        decision_id=d.decision_id, city=city.name, target_date=candidate.target_date,
                        range_label=d.edge.bin.label if d.edge else "", direction=d.edge.direction if d.edge else "",
                        rejection_stage=d.rejection_stage, rejection_reasons=list(d.rejection_reasons),
                        best_edge=d.edge.edge if d.edge else 0.0, model_prob=d.edge.p_posterior if d.edge else 0.0,
                        market_price=d.edge.entry_price if d.edge else 0.0, timestamp=decision_time.isoformat(),
                    ))
        except Exception as e:
            logger.error("Evaluation failed for %s %s: %s", city.name, candidate.target_date, e)
            
    return portfolio_dirty, tracker_dirty


def run_cycle(mode: DiscoveryMode) -> dict:
    """Run one discovery cycle. Pure orchestration `< 50` lines."""
    decision_time = _utcnow()
    summary = {"mode": mode.value, "started_at": decision_time.isoformat(),
                "monitors": 0, "exits": 0, "candidates": 0, "trades": 0, "no_trades": 0}
    artifact = CycleArtifact(mode=mode.value, started_at=summary["started_at"], summary=summary)

    try:
        from src.control.control_plane import process_commands
        process_commands()
    except Exception as e:
        logger.warning("Control plane precheck failed: %s", e)

    # 1. Pipeline Initialization & Reconciliation
    risk_level = get_current_level()
    summary["risk_level"] = risk_level.value

    conn = get_connection()
    portfolio = load_portfolio()
    clob = PolymarketClient(paper_mode=(settings.mode == "paper"))
    tracker = get_tracker()
    limits = RiskLimits(
        max_single_position_pct=settings["sizing"]["max_single_position_pct"],
        max_portfolio_heat_pct=settings["sizing"]["max_portfolio_heat_pct"],
        max_correlated_pct=settings["sizing"]["max_correlated_pct"],
        max_city_pct=settings["sizing"]["max_city_pct"],
        max_region_pct=settings["sizing"]["max_region_pct"],
        min_order_usd=settings["sizing"]["min_order_usd"],
    )
    portfolio_dirty, tracker_dirty = False, False

    pending_updates = _reconcile_pending_positions(portfolio, clob, tracker)
    portfolio_dirty = portfolio_dirty or pending_updates["dirty"]
    tracker_dirty = tracker_dirty or pending_updates["tracker_dirty"]
    summary["trades"] += pending_updates["entered"]
    summary["pending_voids"] = pending_updates["voided"]

    chain_stats, chain_ready = _run_chain_sync(portfolio, clob)
    if chain_stats:
        summary["chain_sync"] = chain_stats
        if chain_stats.get("synced") or chain_stats.get("voided") or chain_stats.get("quarantined") or chain_stats.get("updated"):
            portfolio_dirty = True

    stale_cancelled = _cleanup_orphan_open_orders(portfolio, clob)
    if stale_cancelled:
        summary["stale_orders_cancelled"] = stale_cancelled

    entry_bankroll, cap_summary = _entry_bankroll_for_cycle(portfolio, clob)
    summary.update({k: v for k, v in cap_summary.items() if v is not None})

    # 2. MONITOR FIRST — unskippable existing position protection
    p_dirty, t_dirty = _execute_monitoring_phase(conn, clob, portfolio, artifact, tracker, summary)
    portfolio_dirty, tracker_dirty = portfolio_dirty or p_dirty, tracker_dirty or t_dirty

    # 3. Discovery Pipeline Execution Gates
    current_heat = portfolio_heat_for_bankroll(portfolio, entry_bankroll or 0.0)
    summary["portfolio_heat_pct"] = round(current_heat * 100.0, 2) if entry_bankroll else 0.0
    exposure_gate_hit = (entry_bankroll is not None and entry_bankroll > 0 and current_heat >= limits.max_portfolio_heat_pct * 0.95)
    
    entries_blocked_reason = None
    if not chain_ready: entries_blocked_reason = "chain_sync_unavailable"
    elif risk_level in (RiskLevel.ORANGE, RiskLevel.RED): entries_blocked_reason = f"risk_level={risk_level.value}"
    elif entry_bankroll is None: entries_blocked_reason = cap_summary.get("entry_block_reason", "entry_bankroll_unavailable")
    elif entry_bankroll <= 0: entries_blocked_reason = "entry_bankroll_non_positive"
    elif exposure_gate_hit: entries_blocked_reason = "near_max_exposure"

    # 4. DISCOVERY — Add new risk only if strictly permitted
    entries_paused = is_entries_paused()
    if risk_level == RiskLevel.GREEN and not entries_paused and entries_blocked_reason is None:
        p_dirty, t_dirty = _execute_discovery_phase(conn, clob, portfolio, artifact, tracker, limits, mode, summary, entry_bankroll, decision_time)
        portfolio_dirty, tracker_dirty = portfolio_dirty or p_dirty, tracker_dirty or t_dirty
    else:
        if entries_paused: summary["entries_paused"] = True
        if entries_blocked_reason is not None:
            summary["entries_blocked_reason"] = entries_blocked_reason
            if entries_blocked_reason == "near_max_exposure": summary["near_max_exposure"] = True

    # 5. Serialization and Chain Finalization
    if portfolio_dirty or summary["trades"] > 0 or summary["exits"] > 0:
        save_portfolio(portfolio)
    if tracker_dirty:
        save_tracker(tracker)

    artifact.completed_at = _utcnow().isoformat()
    try:
        store_artifact(conn, artifact)
    except Exception as e:
        logger.warning("Decision chain recording failed: %s", e)

    conn.close()
    summary["completed_at"] = _utcnow().isoformat()

    try:
        from src.observability.status_summary import write_status
        write_status(summary)
    except Exception as e:
        logger.warning("Status summary write failed: %s", e)
        
    logger.info("Cycle %s: %d monitors, %d exits, %d candidates, %d trades",
                mode.value, summary["monitors"], summary["exits"],
                summary["candidates"], summary["trades"])
    return summary
