"""Heavy runtime helpers extracted from cycle_runner.

The goal is to keep `cycle_runner.py` focused on orchestration while preserving
monkeypatch-based tests that patch symbols on the cycle_runner module. Every
function here receives a `deps` object, typically the cycle_runner module.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from src.engine.time_context import lead_hours_to_target


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_order_status(payload) -> str:
    if isinstance(payload, str):
        return payload.upper()
    if isinstance(payload, dict):
        status = payload.get("status") or payload.get("state") or payload.get("orderStatus")
        if status is not None:
            return str(status).upper()
    return ""


def extract_float(payload: dict | None, *keys: str) -> float | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                continue
    return None


def pending_order_timed_out(pos, now: datetime) -> bool:
    deadline = parse_iso(pos.order_timeout_at)
    return deadline is not None and now >= deadline


def extract_order_id(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("id", "orderID", "orderId"):
        if payload.get(key):
            return str(payload[key])
    return ""


def chain_positions_from_api(payload, *, ChainPosition):
    if payload is None:
        return None

    chain_positions = []
    for row in payload:
        token_id = row.get("token_id", "")
        if not token_id:
            continue
        try:
            chain_positions.append(
                ChainPosition(
                    token_id=token_id,
                    size=float(row.get("size", 0) or 0),
                    avg_price=float(row.get("avg_price", 0) or row.get("cur_price", 0) or 0),
                    cost=float(row.get("cost", 0) or 0),
                    condition_id=row.get("condition_id", ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return chain_positions


def run_chain_sync(portfolio, clob, conn=None, *, deps):
    if getattr(clob, "paper_mode", True):
        return {"skipped": "paper_mode"}, True

    try:
        api_positions = chain_positions_from_api(clob.get_positions_from_api(), ChainPosition=deps.ChainPosition)
    except Exception as exc:
        deps.logger.warning("Chain sync fetch failed: %s", exc)
        return {"skipped": "chain_api_unavailable"}, False
    if api_positions is None:
        return {"skipped": "chain_api_unavailable"}, False
    return deps.reconcile_with_chain(portfolio, api_positions, conn=conn), True


def cleanup_orphan_open_orders(portfolio, clob, *, deps) -> int:
    if getattr(clob, "paper_mode", True) or not hasattr(clob, "get_open_orders"):
        return 0

    tracked_order_ids = set()
    for pos in portfolio.positions:
        if pos.order_id:
            tracked_order_ids.add(pos.order_id)
        if pos.last_exit_order_id:
            tracked_order_ids.add(pos.last_exit_order_id)
    cancelled = 0
    for order in clob.get_open_orders():
        order_id = extract_order_id(order)
        if not order_id or order_id in tracked_order_ids:
            continue
        try:
            result = clob.cancel_order(order_id)
            if result is not None:
                cancelled += 1
        except Exception as exc:
            deps.logger.warning("Orphan open-order cleanup failed for %s: %s", order_id, exc)
    return cancelled


def entry_bankroll_for_cycle(portfolio, clob, *, deps):
    config_cap = float(deps.settings.capital_base_usd)
    effective = max(0.0, float(portfolio.effective_bankroll))
    exposure = deps.total_exposure_usd(portfolio)

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
        deps.logger.warning("Wallet balance fetch failed: %s", exc)
        return None, {
            "config_cap_usd": config_cap,
            "effective_bankroll_usd": effective,
            "wallet_balance_usd": None,
            "dynamic_cap_usd": None,
            "entry_block_reason": "wallet_balance_unavailable",
        }

    if balance <= 0.0 and exposure > 0:
        deps.logger.warning(
            "SUSPICIOUS: wallet balance $%.2f but exposure $%.2f — possible API error. Blocking new entries.",
            balance,
            exposure,
        )
        return None, {
            "config_cap_usd": config_cap,
            "effective_bankroll_usd": effective,
            "wallet_balance_usd": balance,
            "dynamic_cap_usd": None,
            "entry_block_reason": "wallet_balance_zero_with_exposure",
        }

    dynamic_cap = min(config_cap, balance + exposure)
    bankroll = min(dynamic_cap, effective) if effective > 0 else dynamic_cap
    return max(0.0, bankroll), {
        "config_cap_usd": config_cap,
        "effective_bankroll_usd": effective,
        "wallet_balance_usd": balance,
        "dynamic_cap_usd": dynamic_cap,
    }


def materialize_position(candidate, decision, result, portfolio, city, mode, *, state: str, bankroll_at_entry=None, deps):
    now = deps._utcnow()
    entry_price = result.fill_price or result.submitted_price or decision.edge.entry_price
    shares = result.shares or (decision.size_usd / entry_price if entry_price > 0 else 0.0)
    timeout_at = ""
    if result.timeout_seconds:
        timeout_at = (now + timedelta(seconds=result.timeout_seconds)).isoformat()
    edge_source = decision.edge_source or deps._classify_edge_source(mode, decision.edge)

    return deps.Position(
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
        strategy=deps._classify_strategy(mode, decision.edge, edge_source),
        edge_source=edge_source,
        discovery_mode=mode.value,
        market_hours_open=candidate.hours_since_open,
        decision_snapshot_id=decision.decision_snapshot_id,
        entry_method=decision.selected_method,
        selected_method=decision.selected_method,
        applied_validations=list(decision.applied_validations),
        settlement_semantics_json=decision.settlement_semantics_json,
        epistemic_context_json=decision.epistemic_context_json,
        edge_context_json=decision.edge_context_json,
        state=state,
        order_id=result.order_id or "",
        entry_order_id=result.order_id or "",
        order_status=result.status,
        order_posted_at=now.isoformat() if state == "pending_tracked" else "",
        order_timeout_at=timeout_at,
        chain_state="local_only" if state == "pending_tracked" else "unknown",
        env=deps.settings.mode,
    )


def reconcile_pending_positions(portfolio, clob, tracker, *, deps):
    summary = {"entered": 0, "voided": 0, "dirty": False, "tracker_dirty": False}
    if getattr(clob, "paper_mode", False):
        return summary

    from src.execution.fill_tracker import check_pending_entries

    stats = check_pending_entries(
        portfolio,
        clob,
        tracker,
        deps=deps,
    )
    summary["entered"] = int(stats.get("entered", 0) or 0)
    summary["voided"] = int(stats.get("voided", 0) or 0)
    summary["dirty"] = bool(stats.get("dirty", False) or summary["entered"] or summary["voided"])
    summary["tracker_dirty"] = bool(stats.get("tracker_dirty", False) or summary["entered"])
    return summary


def _apply_acknowledged_quarantine_clears(portfolio, summary: dict, *, deps) -> bool:
    portfolio_dirty = False
    for pos in list(portfolio.positions):
        if pos.chain_state not in {"quarantined", "quarantine_expired"}:
            continue
        token_id = pos.token_id if pos.direction != "buy_no" else pos.no_token_id
        if not token_id:
            continue
        if token_id in getattr(portfolio, "ignored_tokens", []):
            continue
        if not deps.has_acknowledged_quarantine_clear(token_id):
            continue
        portfolio.ignored_tokens.append(token_id)
        summary["operator_clears_applied"] = summary.get("operator_clears_applied", 0) + 1
        portfolio_dirty = True
    return portfolio_dirty


def _position_state_value(pos) -> str:
    state = getattr(pos, "state", "")
    return getattr(state, "value", state) or ""


def _build_exit_context(pos, edge_ctx, *, hours_to_settlement, paper_mode, ExitContext):
    if False:
        _ = pos.entry_method
        _ = pos.selected_method
    p_market = None
    if getattr(edge_ctx, "p_market", None) is not None and len(edge_ctx.p_market) > 0:
        p_market = float(edge_ctx.p_market[0])
    elif getattr(pos, "last_monitor_market_price", None) is not None:
        p_market = float(pos.last_monitor_market_price)

    best_bid = getattr(pos, "last_monitor_best_bid", None)
    if paper_mode and best_bid is None and p_market is not None:
        best_bid = p_market

    position_state = _position_state_value(pos)
    return ExitContext(
        fresh_prob=float(edge_ctx.p_posterior) if getattr(edge_ctx, "p_posterior", None) is not None else None,
        fresh_prob_is_fresh=bool(getattr(pos, "last_monitor_prob_is_fresh", False)),
        current_market_price=p_market,
        current_market_price_is_fresh=bool(getattr(pos, "last_monitor_market_price_is_fresh", False)),
        best_bid=best_bid,
        best_ask=getattr(pos, "last_monitor_best_ask", None),
        market_vig=getattr(pos, "last_monitor_market_vig", None),
        hours_to_settlement=hours_to_settlement,
        position_state=position_state,
        day0_active=position_state == "day0_window",
        whale_toxicity=getattr(pos, "last_monitor_whale_toxicity", None),
        chain_is_fresh=None if paper_mode else pos.chain_state == "synced",
        divergence_score=float(getattr(edge_ctx, "divergence_score", 0.0) or 0.0),
        market_velocity_1h=float(getattr(edge_ctx, "market_velocity_1h", 0.0) or 0.0),
    )


def _execution_stub(candidate, decision, result, city, mode, *, deps):
    edge_source = decision.edge_source or deps._classify_edge_source(mode, decision.edge)
    return SimpleNamespace(
        trade_id=result.trade_id,
        market_id=decision.tokens["market_id"],
        city=city.name,
        target_date=candidate.target_date,
        bin_label=decision.edge.bin.label,
        direction=decision.edge.direction,
        strategy=deps._classify_strategy(mode, decision.edge, edge_source),
        edge_source=edge_source,
        decision_snapshot_id=decision.decision_snapshot_id,
        order_id=result.order_id or "",
        order_status=result.status,
        order_posted_at="",
        entered_at="",
        chain_state="",
        fill_quality=None,
    )



def execute_monitoring_phase(conn, clob, portfolio, artifact, tracker, summary: dict, *, deps):
    from src.engine.monitor_refresh import refresh_position
    from src.execution.exit_lifecycle import ExitContext, check_pending_exits, check_pending_retries, execute_exit, is_exit_cooldown_active
    from src.state.chain_reconciliation import quarantine_resolution_reason
    exit_lifecycle_owned_states = {"exit_intent", "sell_placed", "sell_pending", "retry_pending"}
    exit_lifecycle_recovery_states = {"exit_intent", "retry_pending", "backoff_exhausted"}

    paper_mode = getattr(clob, "paper_mode", True)
    portfolio_dirty = _apply_acknowledged_quarantine_clears(portfolio, summary, deps=deps)
    tracker_dirty = False

    if not paper_mode:
        exit_stats = check_pending_exits(portfolio, clob, conn=conn)
        if exit_stats["filled"] or exit_stats["retried"]:
            portfolio_dirty = True

        for filled_pos in exit_stats.get("filled_positions", []):
            artifact.add_exit(
                filled_pos.trade_id,
                filled_pos.exit_reason or "DEFERRED_SELL_FILL",
                filled_pos.exit_price or 0.0,
                "sell_filled",
            )
            tracker.record_exit(filled_pos)
            tracker_dirty = True

        summary["pending_exits_filled"] = exit_stats["filled"]
        summary["pending_exits_retried"] = exit_stats["retried"]

    for pos in list(portfolio.positions):
        if pos.state == "pending_tracked":
            continue
        if False:
            _ = pos.entry_method
            _ = pos.selected_method
        if pos.chain_state == "exit_pending_missing" and pos.exit_state in exit_lifecycle_recovery_states:
            closed = deps.void_position(portfolio, pos.trade_id, "EXIT_CHAIN_MISSING_REVIEW_REQUIRED")
            if closed is not None:
                tracker.record_exit(closed)
                tracker_dirty = True
                portfolio_dirty = True
                summary["exit_chain_missing_closed"] = summary.get("exit_chain_missing_closed", 0) + 1
            continue
        if pos.chain_state == "exit_pending_missing" and pos.exit_state in exit_lifecycle_owned_states:
            summary["monitor_skipped_exit_pending_missing"] = summary.get("monitor_skipped_exit_pending_missing", 0) + 1
            continue
        if pos.exit_state in ("sell_placed", "sell_pending"):
            continue
        if pos.exit_state == "backoff_exhausted":
            continue
        if is_exit_cooldown_active(pos):
            continue

        check_pending_retries(pos, conn=conn)

        if pos.chain_state in {"quarantined", "quarantine_expired"}:
            if not pos.admin_exit_reason:
                pos.admin_exit_reason = quarantine_resolution_reason(pos.chain_state)
                pos.exit_reason = pos.admin_exit_reason
                pos.last_exit_at = deps._utcnow().isoformat() if hasattr(deps, "_utcnow") else datetime.now(timezone.utc).isoformat()
                portfolio_dirty = True
                summary["quarantine_resolution_marked"] = summary.get("quarantine_resolution_marked", 0) + 1
            artifact.add_monitor_result(
                deps.MonitorResult(
                    position_id=pos.trade_id,
                    fresh_prob=pos.last_monitor_prob or pos.p_posterior,
                    fresh_edge=pos.last_monitor_edge,
                    should_exit=False,
                    exit_reason=pos.admin_exit_reason,
                    neg_edge_count=pos.neg_edge_count,
                )
            )
            summary["monitor_skipped_quarantine_resolution"] = summary.get("monitor_skipped_quarantine_resolution", 0) + 1
            continue

        if pos.direction not in {"buy_yes", "buy_no"}:
            artifact.add_monitor_result(
                deps.MonitorResult(
                    position_id=pos.trade_id,
                    fresh_prob=pos.last_monitor_prob or pos.p_posterior,
                    fresh_edge=pos.last_monitor_edge,
                    should_exit=False,
                    exit_reason="UNKNOWN_DIRECTION",
                    neg_edge_count=pos.neg_edge_count,
                )
            )
            summary["monitor_skipped_unknown_direction"] = summary.get("monitor_skipped_unknown_direction", 0) + 1
            continue

        try:
            city = deps.cities_by_name.get(pos.city)
            hours_to_settlement = None
            if city is not None:
                hours_to_settlement = lead_hours_to_target(
                    pos.target_date,
                    city.timezone,
                    deps._utcnow(),
                )
                if hours_to_settlement <= 6.0 and pos.state in {"entered", "holding"}:
                    pos.state = "day0_window"
                    if not pos.day0_entered_at:
                        pos.day0_entered_at = deps._utcnow().isoformat()
                    portfolio_dirty = True
                    if conn is not None:
                        try:
                            from src.state.db import update_trade_lifecycle

                            update_trade_lifecycle(conn=conn, pos=pos)
                        except Exception as exc:
                            deps.logger.warning(
                                "Failed to persist day0_window lifecycle for %s: %s",
                                pos.trade_id,
                                exc,
                            )

            edge_ctx = refresh_position(conn, clob, pos)
            exit_context = _build_exit_context(
                pos,
                edge_ctx,
                hours_to_settlement=hours_to_settlement,
                paper_mode=paper_mode,
                ExitContext=ExitContext,
            )
            p_market = exit_context.current_market_price
            portfolio_dirty = True
            exit_decision = pos.evaluate_exit(exit_context)
            should_exit = exit_decision.should_exit
            exit_reason = exit_decision.reason
            if exit_reason.startswith("INCOMPLETE_EXIT_CONTEXT"):
                summary["monitor_incomplete_exit_context"] = summary.get("monitor_incomplete_exit_context", 0) + 1
                deps.logger.warning(
                    "Exit authority incomplete for %s: %s",
                    pos.trade_id,
                    exit_reason,
                )

            artifact.add_monitor_result(
                deps.MonitorResult(
                    position_id=pos.trade_id,
                    fresh_prob=edge_ctx.p_posterior,
                    fresh_edge=pos.last_monitor_edge,
                    should_exit=should_exit,
                    exit_reason=exit_reason,
                    neg_edge_count=pos.neg_edge_count,
                )
            )
            summary["monitors"] += 1

            if should_exit:
                pos.exit_trigger = exit_decision.trigger or exit_reason
                pos.exit_reason = exit_reason
                pos.exit_divergence_score = edge_ctx.divergence_score
                pos.exit_market_velocity_1h = edge_ctx.market_velocity_1h
                pos.exit_forward_edge = edge_ctx.forward_edge
                outcome = execute_exit(
                    portfolio=portfolio,
                    position=pos,
                    exit_context=replace(exit_context, exit_reason=exit_reason),
                    paper_mode=paper_mode,
                    clob=clob,
                    conn=conn,
                )
                if "paper_exit" in outcome or "exit_filled" in outcome:
                    tracker.record_exit(pos)
                    tracker_dirty = True
                summary["exits"] += 1
                portfolio_dirty = True
        except Exception as e:
            deps.logger.error("Monitor failed for %s: %s", pos.trade_id, e)

    return portfolio_dirty, tracker_dirty


def fetch_day0_observation(city, target_date: str, decision_time, *, deps):
    getter = deps.get_current_observation
    try:
        return getter(city, target_date=target_date, reference_time=decision_time)
    except TypeError:
        return getter(city)


def execute_discovery_phase(conn, clob, portfolio, artifact, tracker, limits, mode, summary: dict, entry_bankroll: float, decision_time, *, deps):
    portfolio_dirty = False
    tracker_dirty = False
    market_candidate_ctor = getattr(deps, "MarketCandidate", None)
    if market_candidate_ctor is None:
        from src.engine.evaluator import MarketCandidate as market_candidate_ctor

    params = deps.MODE_PARAMS[mode]
    markets = deps.find_weather_markets(min_hours_to_resolution=params.get("min_hours_to_resolution", 6))
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
        parseable_labels = [
            outcome["title"]
            for outcome in market.get("outcomes", [])
            if not (outcome.get("range_low") is None and outcome.get("range_high") is None)
        ]
        try:
            obs = (
                fetch_day0_observation(city, market["target_date"], decision_time, deps=deps)
                if mode == deps.DiscoveryMode.DAY0_CAPTURE
                else None
            )
        except Exception as e:
            from src.contracts.exceptions import MissingCalibrationError, ObservationUnavailableError

            if isinstance(e, (ObservationUnavailableError, MissingCalibrationError)):
                deps.logger.warning("Skipping candidate for %s: %s", city.name, e)
                continue
            raise

        candidate = market_candidate_ctor(
            city=city,
            target_date=market["target_date"],
            outcomes=market["outcomes"],
            hours_since_open=market["hours_since_open"],
            hours_to_resolution=market["hours_to_resolution"],
            event_id=market.get("event_id", ""),
            slug=market.get("slug", ""),
            observation=obs,
            discovery_mode=mode.value,
        )
        summary["candidates"] += 1

        try:
            decisions = deps.evaluate_candidate(candidate, conn, portfolio, clob, limits, entry_bankroll=entry_bankroll)
            if decisions:
                try:
                    from src.engine.time_context import lead_hours_to_target
                    from src.state.db import log_shadow_signal
                    first = decisions[0]
                    edges_payload = [
                        {
                            "decision_id": d.decision_id,
                            "should_trade": d.should_trade,
                            "direction": d.edge.direction if d.edge else "",
                            "bin_label": d.edge.bin.label if d.edge else "",
                            "edge": d.edge.edge if d.edge else 0.0,
                            "rejection_stage": d.rejection_stage,
                            "decision_snapshot_id": d.decision_snapshot_id,
                            "selected_method": d.selected_method,
                        }
                        for d in decisions
                    ]
                    log_shadow_signal(
                        conn,
                        city=city.name,
                        target_date=candidate.target_date,
                        timestamp=decision_time.isoformat(),
                        decision_snapshot_id=first.decision_snapshot_id,
                        p_raw_json=json.dumps(first.p_raw.tolist() if getattr(first, "p_raw", None) is not None else []),
                        p_cal_json=json.dumps(first.p_cal.tolist() if getattr(first, "p_cal", None) is not None else []),
                        edges_json=json.dumps(edges_payload),
                        lead_hours=float(lead_hours_to_target(date.fromisoformat(candidate.target_date), city.timezone, decision_time)),
                    )
                except Exception:
                    pass
            for d in decisions:
                if False:
                    _ = d.calibration
                if d.should_trade and d.edge and d.tokens:
                    strategy_name = deps._classify_strategy(mode, d.edge, d.edge_source or deps._classify_edge_source(mode, d.edge))
                    if not deps.is_strategy_enabled(strategy_name):
                        edge_source = d.edge_source or deps._classify_edge_source(mode, d.edge)
                        summary["no_trades"] += 1
                        summary["strategy_gate_rejections"] = summary.get("strategy_gate_rejections", 0) + 1
                        artifact.add_no_trade(
                            deps.NoTradeCase(
                                decision_id=d.decision_id,
                                city=city.name,
                                target_date=candidate.target_date,
                                range_label=d.edge.bin.label if d.edge else "",
                                direction=d.edge.direction if d.edge else "",
                                rejection_stage="RISK_REJECTED",
                                strategy=strategy_name,
                                edge_source=edge_source,
                                rejection_reasons=[f"strategy_gate_disabled:{strategy_name}"],
                                best_edge=d.edge.edge if d.edge else 0.0,
                                model_prob=d.edge.p_posterior if d.edge else 0.0,
                                market_price=d.edge.entry_price if d.edge else 0.0,
                                decision_snapshot_id=d.decision_snapshot_id,
                                selected_method=d.selected_method,
                                settlement_semantics_json=d.settlement_semantics_json,
                                epistemic_context_json=d.epistemic_context_json,
                                edge_context_json=d.edge_context_json,
                                applied_validations=list(d.applied_validations),
                                bin_labels=parseable_labels,
                                p_raw_vector=d.p_raw.tolist() if getattr(d, "p_raw", None) is not None else [],
                                p_cal_vector=d.p_cal.tolist() if getattr(d, "p_cal", None) is not None else [],
                                p_market_vector=d.p_market.tolist() if getattr(d, "p_market", None) is not None else [],
                                alpha=getattr(d, "alpha", 0.0),
                                agreement=getattr(d, "agreement", ""),
                                timestamp=decision_time.isoformat(),
                            )
                        )
                        continue
                    intent = deps.create_execution_intent(
                        edge_context=d.edge_context,
                        edge=d.edge,
                        size_usd=d.size_usd,
                        mode=mode.value,
                        market_id=d.tokens["market_id"],
                        token_id=d.tokens["token_id"],
                        no_token_id=d.tokens["no_token_id"],
                    )
                    result = deps.execute_intent(intent, d.edge.vwmp, d.edge.bin.label)
                    artifact.add_trade(
                        {
                            "decision_id": d.decision_id,
                            "trade_id": result.trade_id,
                            "status": result.status,
                            "timestamp": decision_time.isoformat(),
                            "city": city.name,
                            "target_date": candidate.target_date,
                            "range_label": d.edge.bin.label,
                            "direction": d.edge.direction,
                            "market_id": d.tokens["market_id"],
                            "token_id": d.tokens["token_id"],
                            "no_token_id": d.tokens["no_token_id"],
                            "size_usd": d.size_usd,
                            "entry_price": d.edge.entry_price,
                            "p_posterior": d.edge.p_posterior,
                            "edge": d.edge.edge,
                            "strategy": strategy_name,
                            "edge_source": d.edge_source or deps._classify_edge_source(mode, d.edge),
                            "market_hours_open": candidate.hours_since_open,
                            "decision_snapshot_id": d.decision_snapshot_id,
                            "selected_method": d.selected_method,
                            "applied_validations": d.applied_validations,
                            "settlement_semantics_json": d.settlement_semantics_json,
                            "epistemic_context_json": d.epistemic_context_json,
                            "edge_context_json": d.edge_context_json,
                            "bin_labels": parseable_labels,
                            "p_raw_vector": d.p_raw.tolist() if getattr(d, "p_raw", None) is not None else [],
                            "p_cal_vector": d.p_cal.tolist() if getattr(d, "p_cal", None) is not None else [],
                            "p_market_vector": d.p_market.tolist() if getattr(d, "p_market", None) is not None else [],
                            "alpha": getattr(d, "alpha", 0.0),
                            "agreement": getattr(d, "agreement", ""),
                        }
                    )
                    if result.status in ("filled", "pending"):
                        pos = materialize_position(
                            candidate,
                            d,
                            result,
                            portfolio,
                            city,
                            mode,
                            state="entered" if result.status == "filled" else "pending_tracked",
                            bankroll_at_entry=entry_bankroll,
                            deps=deps,
                        )
                        deps.add_position(portfolio, pos)
                        from src.state.db import log_execution_report, log_trade_entry

                        log_trade_entry(conn, pos)
                        log_execution_report(conn, pos, result)
                        portfolio_dirty = True
                        if result.status == "filled":
                            tracker.record_entry(pos)
                            tracker_dirty = True
                            summary["trades"] += 1
                    else:
                        from src.state.db import log_execution_report

                        log_execution_report(
                            conn,
                            _execution_stub(candidate, d, result, city, mode, deps=deps),
                            result,
                        )
                else:
                    edge_source = ""
                    strategy_name = ""
                    if d.edge:
                        edge_source = d.edge_source or deps._classify_edge_source(mode, d.edge)
                        strategy_name = deps._classify_strategy(mode, d.edge, edge_source)
                    summary["no_trades"] += 1
                    artifact.add_no_trade(
                        deps.NoTradeCase(
                            decision_id=d.decision_id,
                            city=city.name,
                            target_date=candidate.target_date,
                            range_label=d.edge.bin.label if d.edge else "",
                            direction=d.edge.direction if d.edge else "",
                            rejection_stage=d.rejection_stage,
                            strategy=strategy_name,
                            edge_source=edge_source,
                            rejection_reasons=list(d.rejection_reasons),
                            best_edge=d.edge.edge if d.edge else 0.0,
                            model_prob=d.edge.p_posterior if d.edge else 0.0,
                            market_price=d.edge.entry_price if d.edge else 0.0,
                            decision_snapshot_id=d.decision_snapshot_id,
                            selected_method=d.selected_method,
                            settlement_semantics_json=d.settlement_semantics_json,
                            epistemic_context_json=d.epistemic_context_json,
                            edge_context_json=d.edge_context_json,
                            applied_validations=list(d.applied_validations),
                            bin_labels=parseable_labels,
                            p_raw_vector=d.p_raw.tolist() if getattr(d, "p_raw", None) is not None else [],
                            p_cal_vector=d.p_cal.tolist() if getattr(d, "p_cal", None) is not None else [],
                            p_market_vector=d.p_market.tolist() if getattr(d, "p_market", None) is not None else [],
                            alpha=getattr(d, "alpha", 0.0),
                            agreement=getattr(d, "agreement", ""),
                            timestamp=decision_time.isoformat(),
                        )
                    )
        except Exception as e:
            deps.logger.error("Evaluation failed for %s %s: %s", city.name, candidate.target_date, e)

    return portfolio_dirty, tracker_dirty
